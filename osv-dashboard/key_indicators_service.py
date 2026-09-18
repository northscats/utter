"""Read-only source assembly for the live CFO screen.

This screen deliberately does not rewrite archived dashboards or rating
snapshots. Every source revision invalidates the bounded in-process cache.
"""
import copy
import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func

from database import DailyRow, Upload, db
from consolidated_osv import parse_consolidated_osv
from key_indicators import _period_bounds, build_indicator_source
from osvparser import parse_osv
from source_manifest import relative_upload_path, sha256_file
from source_passport import (
    build_period_passport,
    build_source_passport,
    enrich_source_passport,
)

_CONTROL_SNAPSHOT_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'control_snapshots')
_UPLOADED_CONTROL_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'uploads')


def _is_trusted_control_path(path):
    candidate = os.path.realpath(path)
    legacy_root = os.path.realpath(_CONTROL_SNAPSHOT_ROOT)
    try:
        if os.path.commonpath((legacy_root, candidate)) == legacy_root:
            return True
        relative = os.path.relpath(
            candidate, os.path.realpath(_UPLOADED_CONTROL_ROOT))
        parts = relative.split(os.sep)
        return (len(parts) == 3 and parts[0].isdigit()
                and parts[1] == 'control_snapshots')
    except ValueError:
        return False


def _control_period_key(path):
    try:
        parsed = parse_consolidated_osv(path)
        if not parsed:
            return None
        start, end = _period_bounds(next(iter(parsed.values())).get('period', ''))
        return (
            start.isoformat() if start else None,
            end.isoformat() if end else None,
        )
    except Exception:
        return None


def _control_source_paths(company_id):
    """Uploaded controls supersede bundled legacy controls for the same period."""
    legacy_dir = os.path.join(_CONTROL_SNAPSHOT_ROOT, str(int(company_id)))
    uploaded_dir = os.path.join(
        _UPLOADED_CONTROL_ROOT, str(int(company_id)), 'control_snapshots')
    uploaded = [
        entry.path for entry in os.scandir(uploaded_dir)
        if entry.is_file() and entry.name.lower().endswith(('.xls', '.xlsx'))
    ] if os.path.isdir(uploaded_dir) else []
    uploaded_periods = {
        key for key in (_control_period_key(path) for path in uploaded) if key
    }
    legacy = []
    if os.path.isdir(legacy_dir):
        for entry in os.scandir(legacy_dir):
            if (not entry.is_file()
                    or not entry.name.lower().endswith(('.xls', '.xlsx'))):
                continue
            period = _control_period_key(entry.path)
            if period is None or period not in uploaded_periods:
                legacy.append(entry.path)
    return legacy + uploaded


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


def _money_cents(value):
    """Convert a parsed monetary value to integer kopecks."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(
            (Decimal(str(value)) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _control_account_turnovers(parsed_items):
    """Return control totals for root accounts and explicit subaccount rows."""
    result = {}
    for parsed in parsed_items:
        account = str(parsed.get("acc") or "")
        top = parsed.get("top") or {}
        if account:
            result[account] = {"obd": top.get("obd"), "obk": top.get("obk")}
        for row in parsed.get("rows") or []:
            label = str(row.get("label") or "").strip()
            if (
                label and label != account
                and re.fullmatch(r"\d{2}(?:\.\d+)+", label)
            ):
                result[label] = {
                    "obd": row.get("obd"),
                    "obk": row.get("obk"),
                }
    return result


def _reconciliation_daily_rows(accounts, account):
    """Select accepted daily rows belonging to one control account."""
    raw = accounts.get(account)
    root = account.split(".", 1)[0]
    exact_bucket = raw is not None
    if raw is None:
        raw = accounts.get(root) or {}
    observations = raw.get("balance_observations") or []
    daily = observations or raw.get("daily") or []
    if exact_bucket or account == root:
        return daily
    return [
        row for row in daily
        if account in {
            str(row.get(key) or "").strip()
            for key in ("account", "path1", "path2", "path3")
        }
    ]


def _daily_control_reconciliations(source, complete_snapshots):
    """Compare accepted daily turnovers with exact-period control totals."""
    reconciliations = []
    accounts = source.get("accounts") or {}
    for snapshot in complete_snapshots:
        start = snapshot.get("start")
        end = snapshot.get("end")
        controls = snapshot.get("account_turnovers") or {}
        if not start or not end:
            continue
        for account, control in sorted(controls.items()):
            # Persisted DailyRow records remain as balance observations when a
            # control snapshot covers the same dates. They are the accepted
            # day-level source that must be reconciled. File-derived daily
            # rows are the fallback when no persisted observations exist.
            daily = _reconciliation_daily_rows(accounts, str(account))
            selected = [
                row for row in daily
                if start <= str(row.get("date") or "") <= end
            ]
            if not selected:
                continue
            for side, key in (("debit", "obd"), ("credit", "obk")):
                control_cents = _money_cents(control.get(key))
                daily_values = [_money_cents(row.get(key)) for row in selected]
                known_values = [value for value in daily_values if value is not None]
                if control_cents is None or not known_values:
                    continue
                daily_cents = sum(known_values)
                difference_cents = control_cents - daily_cents
                reconciliations.append({
                    "account": str(account),
                    "side": side,
                    "start": start,
                    "end": end,
                    "source_file": snapshot.get("source_file"),
                    "control_amount": control_cents / 100,
                    "daily_amount": daily_cents / 100,
                    "difference": difference_cents / 100,
                    "matches": difference_cents == 0,
                    "daily_rows": len(selected),
                })
    return reconciliations


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
    raw_manifest_json = (
        getattr(row, 'source_manifest_json', None)
        if not isinstance(row, dict) else row.get('source_manifest_json')
    )
    supplied_manifest = (
        getattr(row, 'source_manifest', None)
        if not isinstance(row, dict) else row.get('source_manifest')
    )
    manifest = supplied_manifest
    manifest_status = 'absent'
    if raw_manifest_json not in (None, ''):
        try:
            decoded_manifest = json.loads(raw_manifest_json)
        except (TypeError, ValueError):
            decoded_manifest = None
        if isinstance(decoded_manifest, list) and all(
                isinstance(item, dict) for item in decoded_manifest):
            manifest = decoded_manifest
            manifest_status = 'valid'
        else:
            manifest = None
            manifest_status = 'invalid'
    elif isinstance(manifest, list):
        manifest_status = (
            'valid' if all(isinstance(item, dict) for item in manifest)
            else 'invalid')
    elif supplied_manifest is not None:
        manifest_status = 'invalid'
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
        # ``None`` is meaningful here: it distinguishes a legacy Upload from
        # a persisted empty/invalid manifest.  The passport must never infer a
        # file-to-upload relationship from the upload timestamp.
        'source_manifest': manifest,
        'source_manifest_status': manifest_status,
    }
    result['_accounts_json'] = accounts_json
    result['_source_manifest_json'] = (
        raw_manifest_json if raw_manifest_json is not None
        else json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        if manifest is not None else None
    )
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
            item.get('_source_manifest_json'),
        )
        for item in metadata
    )


def _source_identity(relative_path, digest):
    return hashlib.sha256(
        f'{relative_path}\0{digest}'.encode('utf-8')).hexdigest()


def _file_identity(path, company_upload_root):
    """Return verifiable identity fields for an accepted source file."""
    result = {}
    try:
        result['sha256'] = sha256_file(path)
    except Exception as exc:
        result['identity_error'] = str(exc)
        return result
    try:
        relative = relative_upload_path(path, company_upload_root)
    except Exception as exc:
        result['identity_error'] = str(exc)
        return result
    result['relative_path'] = relative
    result['source_id'] = _source_identity(relative, result['sha256'])
    return result


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


def monthly_files(root, company_id, accepted_before=None, excluded_batches=()):
    """Use legacy bare-timestamp monthly directories, not staging.

    Persisted manifests are resolved separately by ``load_company_source``.
    This discovery path is intentionally retained only for legacy uploads.
    """
    return _legacy_monthly_files(
        root, company_id, accepted_before=accepted_before,
        excluded_batches=excluded_batches)


def _legacy_monthly_files(
        root, company_id, accepted_before=None, excluded_batches=()):
    """Discover files for uploads that predate persisted manifests."""
    directory = os.path.join(root, str(int(company_id)))
    if not os.path.isdir(directory):
        return []
    excluded_batches = set(excluded_batches or ())
    paths = []
    for folder in sorted(os.listdir(directory)):
        if folder.startswith(('zip_', 'daily_', 'tmp_', 'stage_', 'failed_', '.')) or folder in (
                'historical', 'tmp'):
            continue
        if folder in excluded_batches:
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


def _valid_manifest_relative_path(value):
    """Validate a portable manifest path before joining it to company root."""
    relative = str(value or "")
    if not relative or relative.startswith("/") or "\\" in relative:
        return False
    if ":" in relative.split("/", 1)[0]:
        return False
    parts = [part for part in relative.split("/") if part not in ("", ".")]
    return bool(parts) and ".." not in parts


def _manifest_source_paths(uploads, company_upload_root):
    """Resolve current files named by valid persisted manifest records.

    A manifest is the acceptance-side allowlist.  We deliberately do not
    parse a file until its current bytes match the persisted digest.  A
    tampered file is returned as passport-only evidence instead of feeding
    untrusted bytes into the KPI calculation.
    """
    paths = []
    owned_batches = set()
    tampered = []
    candidates = []
    root = os.path.realpath(company_upload_root)
    for upload in uploads:
        manifest = upload.get("source_manifest")
        if not isinstance(manifest, list):
            continue
        manifest_status = upload.get("source_manifest_status")
        manifest_valid = manifest_status in (None, "valid")
        for record in manifest:
            if not isinstance(record, dict):
                continue
            relative = record.get("relative_path")
            if not _valid_manifest_relative_path(relative):
                continue
            relative = str(relative)
            parts = [part for part in relative.split("/") if part not in ("", ".")]
            if parts:
                owned_batches.add(parts[0])
            if not manifest_valid:
                continue
            digest = str(record.get("sha256") or "").lower()
            source_id = str(record.get("source_id") or "")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or source_id != _source_identity(relative, digest)
                or not str(record.get("account") or "")
            ):
                continue
            path = os.path.realpath(os.path.join(root, *parts))
            if not (path == root or path.startswith(root + os.sep)):
                continue
            if path not in candidates:
                candidates.append(path)
            if not os.path.isfile(path) or not path.lower().endswith(
                    (".xls", ".xlsx")):
                continue
            try:
                actual_digest = sha256_file(path).lower()
            except Exception:
                continue
            if actual_digest != digest:
                try:
                    actual_relative = relative_upload_path(
                        path, company_upload_root)
                except Exception:
                    actual_relative = relative
                try:
                    stat = os.stat(path)
                    source_updated_at = datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc).isoformat()
                    source_size = stat.st_size
                except OSError:
                    source_updated_at = None
                    source_size = None
                tampered.append({
                    "source_file": os.path.basename(path),
                    "path": path,
                    "batch": parts[0] if parts else None,
                    "kind": "osv",
                    "status": "tampered",
                    "relative_path": actual_relative,
                    "sha256": actual_digest,
                    "source_id": _source_identity(
                        actual_relative, actual_digest),
                    "source_updated_at": source_updated_at,
                    "source_size": source_size,
                    "check_ok": None,
                    "warnings": [
                        "Содержимое файла не совпадает с сохранённым хешем."
                    ],
                })
                continue
            if path not in paths:
                paths.append(path)
    return paths, owned_batches, tampered, candidates


def _path_signature(path):
    """Return a cache revision for existing or currently missing files."""
    try:
        stat = os.stat(path)
    except OSError:
        return path, None, None, None
    try:
        digest = sha256_file(path)
    except Exception:
        digest = None
    return path, stat.st_mtime_ns, stat.st_size, digest


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


def assemble_source(paths, daily_rows, company_upload_root=None):
    """Latest report wins overlapping dates, including zero-transaction dates.

    Historical non-overlapping daily observations and report-boundary balance
    snapshots remain available. No account-level DailyRow is added over a
    detailed monthly report's covered interval.
    """
    reports = {}
    failures = []
    excluded_annual = set()
    source_manifest = []
    complete_snapshots = []
    daily_uploaded_at = []
    for path in sorted(paths, key=lambda p: (os.stat(p).st_mtime_ns, p)):
        name = os.path.basename(path)
        batch, _batch_at = _manifest_batch(path)
        identity = _file_identity(path, company_upload_root) \
            if company_upload_root else {}
        if 'план' in name.lower() and 'счет' in name.lower():
            source_manifest.append({
                'source_file': name,
                'path': path,
                'batch': batch,
                'kind': 'chart_of_accounts',
                'status': 'excluded',
                **identity,
            })
            continue
        try:
            trusted_control = _is_trusted_control_path(path)
            consolidated = parse_consolidated_osv(path) if trusted_control else None
            if trusted_control and not consolidated:
                raise ValueError('Некорректный полный контрольный снимок ОСВ')
            parsed_items = list(consolidated.values()) if consolidated else [
                parse_osv(path)]
        except ValueError:
            if _is_annual_analytics(path):
                excluded_annual.add(name)
                source_manifest.append({
                    'source_file': name,
                    'path': path,
                    'batch': batch,
                    'kind': 'annual_analytics',
                    'status': 'excluded',
                    **identity,
                })
            else:
                failures.append(name)
                source_manifest.append({
                    'source_file': name,
                    'path': path,
                    'batch': batch,
                    'kind': 'osv',
                    'status': 'failure',
                    **identity,
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
                **identity,
            })
            continue
        updated_at = datetime.fromtimestamp(
            os.stat(path).st_mtime, timezone.utc).isoformat()
        if consolidated:
            first = parsed_items[0]
            period_start, period_end = _period_bounds(first.get('period', ''))
            complete_snapshots.append({
                'start': period_start.isoformat() if period_start else None,
                'end': period_end.isoformat() if period_end else None,
                'source_file': name,
                'source_updated_at': updated_at,
                'accounts': sorted(
                    str(parsed.get('acc') or '') for parsed in parsed_items
                    if parsed.get('acc')),
                'account_turnovers': _control_account_turnovers(parsed_items),
            })
            for parsed in parsed_items:
                # A consolidated control ОСВ proves period totals and boundary
                # balances, but normally has no day-level hierarchy.  It must
                # not erase detailed daily movements accepted from account
                # reports covering the same interval.
                parsed['_consolidated_control'] = True
        source_manifest.append({
            'source_file': name,
            'path': path,
            'batch': batch,
            'kind': 'consolidated_control' if consolidated else 'osv',
            'status': 'parsed',
            **identity,
            'account': 'ALL' if consolidated else str(parsed_items[0].get('acc') or ''),
            'period': parsed_items[0].get('period', ''),
            'report_start': (
                _period_bounds(parsed_items[0].get('period', ''))[0].isoformat()
                if _period_bounds(parsed_items[0].get('period', ''))[0] else None),
            'report_end': (
                _period_bounds(parsed_items[0].get('period', ''))[1].isoformat()
                if _period_bounds(parsed_items[0].get('period', ''))[1] else None),
            'daily_dates': [
                row.get('date') for parsed in parsed_items
                for row in parsed.get('daily', [])
                if row.get('date')
            ],
            'source_updated_at': updated_at,
            'source_size': os.stat(path).st_size,
            'check_ok': all(parsed.get('check_ok') is not False
                            for parsed in parsed_items),
            'warnings': list(dict.fromkeys(
                warning for parsed in parsed_items
                for warning in parsed.get('warnings') or [])),
        })
        for parsed in parsed_items:
            parsed['source_file'] = name
            parsed['source_updated_at'] = updated_at
            reports.setdefault(str(parsed['acc']), []).append(parsed)
    data = {}
    for acc, entries in reports.items():
        latest = copy.deepcopy(entries[-1])
        merged = []
        for report in entries:
            report_daily = report.get('daily', [])
            if report.get('_consolidated_control') and not report_daily:
                continue
            merged = [row for row in merged if not _covered(row['date'], [report])]
            merged.extend(report_daily)
        latest['daily'] = merged
        latest['balance_reports'] = [
            {key: copy.deepcopy(report.get(key)) for key in (
                'period', 'top', 'rows', 'source_file', 'source_updated_at',
                'complete_snapshot')}
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
            'source_type': get('source_type') or 'unknown',
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
    source['_complete_snapshots'] = complete_snapshots
    source['_daily_control_reconciliations'] = (
        _daily_control_reconciliations(source, complete_snapshots))
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
    # Acceptance stores manifest paths relative to the company directory
    # (uploads/<company_id>), not relative to the shared uploads root.
    company_upload_root = os.path.join(
        uploads_root, str(int(company_id)))
    (
        manifest_paths,
        owned_batches,
        tampered_manifest_files,
        manifest_candidates,
    ) = _manifest_source_paths(uploads, company_upload_root)
    has_legacy_uploads = any(
        not isinstance(upload.get("source_manifest"), list)
        and upload.get("source_manifest_status") in (None, "absent")
        for upload in uploads
    )
    fallback_paths = (
        monthly_files(
            uploads_root,
            company_id,
            accepted_before=accepted_before,
            excluded_batches=owned_batches,
        )
        if uploads and has_legacy_uploads else []
    )
    paths = list(manifest_paths)
    for path in fallback_paths:
        if path not in paths:
            paths.append(path)
    control_paths = _control_source_paths(company_id)
    for path in control_paths:
        if path not in paths:
            paths.append(path)
    signature_paths = list(manifest_candidates)
    for path in paths:
        if path not in signature_paths:
            signature_paths.append(path)
    file_signature = tuple(
        _path_signature(path)
        for path in signature_paths
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
        source = assemble_source(paths, rows, company_upload_root)
        source['_source_manifest'].extend(tampered_manifest_files)
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
            timestamps = [
                stat[1] / 1e9 for stat in file_signature
                if stat[1] is not None
            ]
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