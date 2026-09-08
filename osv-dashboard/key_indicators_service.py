"""Read-only source assembly for the live CFO screen.

This screen deliberately does not rewrite archived dashboards or rating
snapshots. Every source revision invalidates the bounded in-process cache.
"""
import copy
import os
import threading
from collections import OrderedDict
from datetime import datetime, timezone

from sqlalchemy import func

from database import DailyRow, Upload, db
from key_indicators import _period_bounds, build_indicator_source
from osvparser import parse_osv


class SourceBusyError(Exception):
    pass


class SourceReadError(Exception):
    pass


_cache = OrderedDict()
_cache_lock = threading.Lock()
_build_slot = threading.BoundedSemaphore(1)
_CACHE_LIMIT = 12


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


def assemble_source(paths, daily_rows):
    """Latest report wins overlapping dates, including zero-transaction dates.

    Historical non-overlapping daily observations and report-boundary balance
    snapshots remain available. No account-level DailyRow is added over a
    detailed monthly report's covered interval.
    """
    reports = {}
    failures = []
    for path in sorted(paths, key=lambda p: (os.stat(p).st_mtime_ns, p)):
        name = os.path.basename(path)
        if 'план' in name.lower() and 'счет' in name.lower():
            continue
        try:
            parsed = parse_osv(path)
        except Exception:
            failures.append(name)
            continue
        parsed['source_file'] = name
        parsed['source_updated_at'] = datetime.fromtimestamp(
            os.stat(path).st_mtime, timezone.utc).isoformat()
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
        latest['warnings'] = [
            warning for report in entries for warning in report.get('warnings', [])
        ]
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
            observation['source_updated_at'] = uploaded_at.isoformat()
        raw.setdefault('balance_observations', []).append(observation)
        if not _covered(day, reports.get(acc, [])):
            raw['daily'].append(observation)
    if failures:
        # Do not silently serve the previous or incomplete version as current.
        raise SourceReadError(
            f'Не удалось прочитать файлов ОСВ: {len(failures)}. '
            'Проверьте исходные загрузки и повторите запрос.')
    return build_indicator_source(data)


def load_company_source(company_id, uploads_root):
    upload_revision = db.session.query(
        func.max(Upload.id), func.max(Upload.uploaded_at)
    ).filter(Upload.company_id == company_id).one()
    paths = monthly_files(
        uploads_root, company_id, accepted_before=upload_revision[1]
    ) if upload_revision[0] is not None else []
    file_signature = tuple(
        (path, os.stat(path).st_mtime_ns, os.stat(path).st_size) for path in paths
    )
    daily_revision = db.session.query(
        func.count(DailyRow.id), func.max(DailyRow.id), func.max(DailyRow.uploaded_at)
    ).filter(DailyRow.company_id == company_id).one()
    revision = (file_signature, tuple(daily_revision), tuple(upload_revision))
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
        dates = [
            value for value in (daily_revision[2], upload_revision[1])
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