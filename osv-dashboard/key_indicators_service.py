"""Read-only source assembly for the live CFO screen.

This screen deliberately does not rewrite archived dashboards or rating
snapshots. Every source revision invalidates the bounded in-process cache.
"""
import copy
import json
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone

from sqlalchemy import func

from database import DailyRow, Upload, db
from key_indicators import _period_bounds, build_indicator_source
from osvparser import parse_osv
from source_passport import (
    build_period_passport,
    build_source_passport,
    enrich_source_passport,
)


class SourceBusyError(Exception):
    pass


class SourceReadError(Exception):
    """A source file could not be read; never silently downgrade to old data."""

    def __init__(self, message, failures=()):
        super().__init__(message)
        self.failures = tuple(failures)


_cache = OrderedDict()
_cache_lock = threading.Lock()
_build_slot = threading.BoundedSemaphore(1)
_CACHE_LIMIT = 12


def _upload_metadata(row):
    """Serialize source-relevant Upload fields without trusting their values.

    ``accounts_loaded`` is retained as metadata only.  The passport compares
    it with accounts found in the accepted files; it never uses this field to
    decide whether an account exists.
    """
    raw_accounts = getattr(row, 'accounts_loaded', None)
    if isinstance(row, dict):
        raw_accounts = row.get('accounts_loaded')
    uploaded_at = getattr(row, 'uploaded_at', None)
    if isinstance(row, dict):
        uploaded_at = row.get('uploaded_at')
    if uploaded_at is not None:
        if hasattr(uploaded_at, 'isoformat'):
            uploaded_at = uploaded_at.isoformat()
        else:
            uploaded_at = str(uploaded_at)
    # Keep the raw JSON in the revision so an edit to metadata invalidates the
    # source cache even when id/uploaded_at remain unchanged.
    accounts_json = raw_accounts if isinstance(raw_accounts, str) else json.dumps(
        raw_accounts, ensure_ascii=False, sort_keys=True)
    result = {
        'id': getattr(row, 'id', None) if not isinstance(row, dict)
        else row.get('id'),
        'uploaded_at': uploaded_at,
        'period_label': getattr(row, 'period_label', '') if not isinstance(row, dict)
        else row.get('period_label', ''),
        'accounts_loaded': raw_accounts if raw_accounts is not None else '[]',
        'files_count': getattr(row, 'files_count', None) if not isinstance(row, dict)
        else row.get('files_count'),
        'source': getattr(row, 'source', '') if not isinstance(row, dict)
        else row.get('source', ''),
        'dashboard_file': getattr(row, 'dashboard_file', '') if not isinstance(row, dict)
        else row.get('dashboard_file', ''),
        'metrics_json': getattr(row, 'metrics_json', '') if not isinstance(row, dict)
        else row.get('metrics_json', ''),
    }
    result['_accounts_json'] = accounts_json
    return result


def _upload_revision(metadata):
    """Stable, JSON-safe DB revision including mutable metadata columns."""
    def accounts_json(item):
        value = item.get('_accounts_json')
        if value is not None:
            return value
        raw = item.get('accounts_loaded')
        return raw if isinstance(raw, str) else json.dumps(
            raw, ensure_ascii=False, sort_keys=True)

    return tuple(
        (
            item.get('id'),
            item.get('uploaded_at'),
            item.get('period_label'),
            accounts_json(item),
            item.get('files_count'),
            item.get('source'),
            item.get('dashboard_file'),
            item.get('metrics_json'),
        )
        for item in metadata
    )


def _manifest_batch(path):
    """Return accepted batch name/time for a source path."""
    parent = os.path.basename(os.path.dirname(path))
    match = re.match(r'^(\d{8}_\d{6})$', parent)
    if not match:
        return parent or None, None
    try:
        batch = datetime.strptime(match.group(1), '%Y%m%d_%H%M%S')
    except ValueError:
        return parent, None
    return parent, batch.replace(tzinfo=timezone.utc)


def monthly_files(root, company_id, accepted_before=None):
    """Use only this company's accepted monthly directories, not staging."""
    directory = os.path.join(root, str(int(company_id)))
    if not os.path.isdir(directory):
        return []
    paths = []
    for folder in sorted(os.listdir(directory)):
        if folder.startswith(('zip_', 'daily_', 'tmp_', 'stage_', 'failed_', '.')) or folder in (
                'historical', 'tmp'):
            continue
        if accepted_before is not None:
            # Current uploads are moved out of stage before their Upload record
            # is committed. Do not read a batch newer than the last completion.
            try:
                batch_time = datetime.strptime(folder[:15], '%Y%m%d_%H%M%S')
            except ValueError:
                continue
            if batch_time > accepted_before.replace(tzinfo=None):
                continue
        child = os.path.join(directory, folder)
        if not os.path.isdir(child):
            continue
        for filename in sorted(os.listdir(child)):
            if filename.lower().endswith(('.xls', '.xlsx')):
                paths.append(os.path.join(child, filename))
    return paths


def _covered(day, reports):
    for report in reports:
        start, end = _period_bounds(report.get('period', ''))
        if start and end and start.isoformat() <= day <= end.isoformat():
            return True
        if not start and day in {d.get('date') for d in report.get('daily', [])}:
            return True
    return False


def _is_annual_analytics(path):
    """Recognize the supported annual workbook, not arbitrary invalid ОСВ."""
    if not path.lower().endswith('.xlsx'):
        return False
    from historical_parser import parse_historical_xlsx
    try:
        parsed = parse_historical_xlsx(path)
    except Exception:
        return False
    known_sections = {
        section['key'] for section in parsed.get('sections', [])
        if section.get('is_known')
    }
    return len(known_sections) >= 2 and any(
        value.get('year') and value.get('section_key') in known_sections
        for value in parsed.get('values', [])
    )


def assemble_source(paths, daily_rows):
    """Latest report wins overlapping dates, including zero-transaction dates.

    Historical non-overlapping daily observations and report-boundary balance
    snapshots remain available. No account-level DailyRow is added over a
    detailed monthly report's covered interval.
    """
    reports = {}
    failures = []
    excluded_annual = set()
    source_manifest = []
    daily_uploaded_at = []
    for path in sorted(paths, key=lambda p: (os.stat(p).st_mtime_ns, p)):
        name = os.path.basename(path)
        batch, _batch_at = _manifest_batch(path)
        if 'план' in name.lower() and 'счет' in name.lower():
            source_manifest.append({
                'source_file': name,
                'path': path,
                'batch': batch,
                'kind': 'chart_of_accounts',
                'status': 'excluded',
            })
            continue
        try:
            parsed = parse_osv(path)
        except ValueError:
            if _is_annual_analytics(path):
                excluded_annual.add(name)
                source_manifest.append({
                    'source_file': name,
                    'path': path,
                    'batch': batch,
                    'kind': 'annual_analytics',
                    'status': 'excluded',
                })
            else:
                failures.append(name)
                source_manifest.append({
                    'source_file': name,
                    'path': path,
                    'batch': batch,
                    'kind': 'osv',
                    'status': 'failure',
                })
            continue
        except Exception:
            failures.append(name)
            source_manifest.append({
                'source_file': name,
                'path': path,
                'batch': batch,
                'kind': 'osv',
                'status': 'failure',
            })
            continue
        parsed['source_file'] = name
        parsed['source_updated_at'] = datetime.fromtimestamp(
            os.stat(path).st_mtime, timezone.utc).isoformat()
        source_manifest.append({
            'source_file': name,
            'path': path,
            'batch': batch,
            'kind': 'osv',
            'status': 'parsed',
            'account': str(parsed.get('acc') or ''),
            'period': parsed.get('period', ''),
            'report_start': (
                _period_bounds(parsed.get('period', ''))[0].isoformat()
                if _period_bounds(parsed.get('period', ''))[0] else None),
            'report_end': (
                _period_bounds(parsed.get('period', ''))[1].isoformat()
                if _period_bounds(parsed.get('period', ''))[1] else None),
            'daily_dates': [
                row.get('date') for row in parsed.get('daily', [])
                if row.get('date')
            ],
            'source_updated_at': parsed['source_updated_at'],
            'source_size': os.stat(path).st_size,
            'check_ok': parsed.get('check_ok'),
            'warnings': list(parsed.get('warnings') or []),
        })
        reports.setdefault(str(parsed['acc']), []).append(parsed)
    data = {}
    for acc, entries in reports.items():
        latest = copy.deepcopy(entries[-1])
        merged = []
        for report in entries:
            merged = [row for row in merged if not _covered(row['date'], [report])]
            merged.extend(report.get('daily', []))
        latest['daily'] = merged
        latest['balance_reports'] = [
            {key: copy.deepcopy(report.get(key)) for key in (
                'period', 'top', 'rows', 'source_file', 'source_updated_at')}
            for report in entries
        ]
        latest['coverage_reports'] = [report.get('period', '') for report in entries]
        latest['warnings'] = list(dict.fromkeys(
            warning for report in entries for warning in report.get('warnings', [])
        ))
        if any(report.get('check_ok') is False for report in entries):
            latest['warnings'].append(
                'Контрольное равенство в одной из ОСВ не сходится; '
                'показатели требуют проверки.')
        data[acc] = latest
    for row in daily_rows:
        # Accept dicts for pure tests, ORM records in runtime.
        get = row.get if isinstance(row, dict) else lambda key: getattr(row, key)
        acc = str(get('acc'))
        date = get('date')
        day = date.isoformat() if hasattr(date, 'isoformat') else str(date)
        raw = data.setdefault(acc, {
            'acc': acc, 'period': '', 'rows': [], 'daily': [],
            'warnings': [], 'top': None,
        })
        observation = {
            'date': day, 'month': day[:7], 'path1': '', 'path2': '', 'path3': '',
            **{key: get(key) for key in ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')},
        }
        uploaded_at = row.get('uploaded_at') if isinstance(row, dict) else row.uploaded_at
        if uploaded_at is not None:
            uploaded_text = (
                uploaded_at.isoformat()
                if hasattr(uploaded_at, 'isoformat') else str(uploaded_at))
            observation['source_updated_at'] = uploaded_text
            daily_uploaded_at.append(uploaded_text)
        raw.setdefault('balance_observations', []).append(observation)
        if not _covered(day, reports.get(acc, [])):
            raw['daily'].append(observation)
    if failures:
        # Do not silently serve the previous or incomplete version as current.
        raise SourceReadError(
            f'Не удалось прочитать файлов ОСВ: {len(failures)}. '
            f'Файлы: {", ".join(sorted(set(failures))[:3])}. '
            'Проверьте исходные загрузки и повторите запрос.',
            failures=sorted(set(failures)))
    source = build_indicator_source(data)
    if daily_uploaded_at:
        source['source_daily_uploaded_at'] = daily_uploaded_at
    # This is deliberately a separate read-only manifest.  The calculation
    # source keeps only the fields needed by key_indicators.py, while the
    # passport needs file identity, accepted batch and parser evidence.
    source['_source_manifest'] = source_manifest
    for name in sorted(excluded_annual):
        source['warnings'].append(
            f'Файл «{name}» содержит годовую финансовую аналитику, а не ОСВ. '
            'Он не включён в расчёт основных показателей по счетам.')
    source = enrich_source_passport(source, [])
    return source


def load_company_source(company_id, uploads_root):
    # Read all metadata in this DB context.  A max(id)/max(uploaded_at)
    # revision cannot notice an operator correcting period_label,
    # accounts_loaded or the saved metrics JSON.
    upload_rows = Upload.query.filter_by(company_id=company_id).order_by(
        Upload.uploaded_at.asc(), Upload.id.asc()).all()
    uploads = [_upload_metadata(row) for row in upload_rows]
    upload_revision = _upload_revision(uploads)
    upload_dates = [
        datetime.fromisoformat(item['uploaded_at'].replace('Z', '+00:00'))
        for item in uploads if item.get('uploaded_at')
    ]
    accepted_before = max(upload_dates) if upload_dates else None
    paths = monthly_files(
        uploads_root, company_id, accepted_before=accepted_before
    ) if uploads else []
    file_signature = tuple(
        (path, os.stat(path).st_mtime_ns, os.stat(path).st_size) for path in paths
    )
    daily_revision = db.session.query(
        func.count(DailyRow.id), func.max(DailyRow.id), func.max(DailyRow.uploaded_at)
    ).filter(DailyRow.company_id == company_id).one()
    # Include the complete mutable upload metadata, not only a row id/date.
    revision = (file_signature, tuple(daily_revision), upload_revision)
    cache_key = (os.path.abspath(uploads_root), int(company_id))
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and entry[0] == revision:
            _cache.move_to_end(cache_key)
            return entry[1], entry[2]
    if not _build_slot.acquire(timeout=1):
        raise SourceBusyError(
            'Подготавливаются показатели другого запроса. Повторите через несколько секунд.')
    try:
        with _cache_lock:
            entry = _cache.get(cache_key)
            if entry and entry[0] == revision:
                return entry[1], entry[2]
        rows = DailyRow.query.filter_by(company_id=company_id).order_by(
            DailyRow.date, DailyRow.acc).all()
        source = assemble_source(paths, rows)
        # assemble_source deliberately has a no-DB contract for pure tests;
        # runtime adds the immutable snapshot of Upload metadata here.
        source = enrich_source_passport(source, uploads)
        dates = [
            value for value in (
                daily_revision[2],
                *(datetime.fromisoformat(item['uploaded_at'].replace(
                    'Z', '+00:00')) for item in uploads
                  if item.get('uploaded_at')),
            )
            if value is not None
        ]
        timestamps = [
            value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
            for value in dates
        ]
        # File mtime is a fallback, not a fabricated refresh date.
        if not timestamps:
            timestamps = [stat[1] / 1e9 for stat in file_signature]
        updated_at = datetime.fromtimestamp(
            max(timestamps), timezone.utc).isoformat() if timestamps else None
        with _cache_lock:
            _cache[cache_key] = (revision, source, updated_at)
            _cache.move_to_end(cache_key)
            while len(_cache) > _CACHE_LIMIT:
                _cache.popitem(last=False)
        return source, updated_at
    finally:
        _build_slot.release()