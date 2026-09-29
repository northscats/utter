# -*- coding: utf-8 -*-
"""Transactional application service for the GИС РЭБ limits module."""
import hashlib
import json
import os
import shutil
import uuid
import threading
import tempfile
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import Workbook

from database import (
    LimitChange, LimitComparison, LimitComparisonResolution, LimitDataset,
    LimitObject, LimitRowSnapshot, LimitVersion, LimitVersionTombstone, Setting,
    AuditLog, db, utcnow,
)

MAX_HISTORY = 10
_service_lock = threading.RLock()
_lock_state = threading.local()


def _processing_error_message(exc):
    """Show validation failures, but keep unexpected server details private."""
    from limits_parser import LimitsParserError
    if isinstance(exc, (LimitsParserError, LimitsError)):
        return 'Ошибка проверки файла: ' + str(exc)[:1000]
    return ('Обработка не завершена. Проверьте файл и повторите попытку; '
            'при повторной ошибке обратитесь к администратору.')


@contextmanager
def _process_lock():
    """Hold a database-wide lock across the pipeline's durable checkpoints."""
    if getattr(_lock_state, 'held', False):
        yield
        return
    _lock_state.held = True
    try:
        if db.engine.dialect.name == 'postgresql':
            from sqlalchemy import text
            with db.engine.connect() as connection:
                connection.execute(text('SELECT pg_advisory_lock(301, 1)'))
                try:
                    yield
                finally:
                    connection.execute(text('SELECT pg_advisory_unlock(301, 1)'))
        else:
            import fcntl
            identity = hashlib.sha256(str(db.engine.url).encode()).hexdigest()
            with open(os.path.join(tempfile.gettempdir(), f'limits-{identity}.lock'), 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
    finally:
        _lock_state.held = False


def _serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        # MAX selection and deletion must share the entire check/commit critical
        # section. Always take MAX first: publication queues also take this
        # reentrant lock, and callers may already own it.
        from max_bot_config import locked
        with locked(), _service_lock, _process_lock():
            try:
                return function(*args, **kwargs)
            except Exception:
                db.session.rollback()
                raise
    return wrapped


class LimitsError(RuntimeError):
    pass


def _json(value, default):
    try:
        parsed = json.loads(value or '')
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':'))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def _cell_value(cell):
    return cell.get('value') if isinstance(cell, dict) else cell


def _row_text(row, fragments):
    cells = row.get('cells', {}) if isinstance(row, dict) else {}
    for key, cell in cells.items():
        key_text = str(key).lower()
        if any(fragment in key_text for fragment in fragments):
            value = _cell_value(cell)
            if value not in (None, ''):
                return str(value)
    return ''


def stable_key(row):
    """Conservative per-row key: never treat a changed program code as new UIN."""
    cells = row.get('cells', {}) or {}
    parts = [str(row.get('uin', '')).strip()]
    for key in sorted(cells):
        normalized = str(key).lower()
        if any(marker in normalized for marker in (
                'переч', 'status', 'статус', 'гп', 'пп', 'ом', 'дм',
                'program', 'programme')):
            value = _cell_value(cells[key])
            parts.append('' if value is None else str(value).strip())
    return '|'.join(parts)


def make_dataset(name, scope, owner_id):
    name = (name or '').strip()
    if not name:
        raise LimitsError('Укажите название набора отчётности.')
    dataset = LimitDataset(name=name, scope=(scope or '').strip(), owner_id=owner_id)
    db.session.add(dataset)
    db.session.commit()
    return dataset


@_serialized
def resolve_upload_dataset(user_id):
    """Persist one technical upload sequence without merging historical sources."""
    db.session.expire_all()
    saved = Setting.get('limits_upload_dataset_id', '')
    if saved:
        dataset = db.session.get(LimitDataset, int(saved))
        if dataset is None:
            raise LimitsError('Сохранённый источник загрузок отсутствует. Требуется восстановление.')
        return dataset
    legacy = Setting.get('max_limits_dataset_id', '')
    dataset = db.session.get(LimitDataset, int(legacy)) if str(legacy).isdigit() else None
    if dataset is None:
        candidates = LimitDataset.query.limit(2).all()
        dataset = candidates[0] if len(candidates) == 1 else None
    if dataset is None:
        dataset = LimitDataset(name='Лимиты', scope='Техническая последовательность файлов', owner_id=user_id)
        db.session.add(dataset)
        db.session.flush()
    Setting.set('limits_upload_dataset_id', str(dataset.id))
    db.session.commit()
    return dataset


def _audit_version(version, action, **details):
    db.session.add(AuditLog(user_id=version.uploaded_by, action=action,
                           details=_dump(dict(version_id=version.id, **details))))


@_serialized
def process_version(version_id):
    """Idempotently parse, compare against the latest base, and publish."""
    db.session.expire_all()
    version = db.session.get(LimitVersion, version_id)
    if not version:
        raise LimitsError('Версия не найдена.')
    if version.status == 'accepted':
        return version
    if version.status not in ('queued', 'parsing', 'review', 'error'):
        raise LimitsError('Версия недоступна для обработки.')
    from limits_progress import Progress
    progress = Progress(version)
    try:
        if version.status != 'review':
            # A previous process may have stopped after the parsing checkpoint.
            version.status = 'queued'
            version.progress_json = '{}'
            dataset = db.session.get(LimitDataset, version.dataset_id)
            version.previous_version_id = dataset.current_version_id
            db.session.commit()
            version = parse_version(version.id, progress=progress)
        dataset = db.session.get(LimitDataset, version.dataset_id)
        db.session.refresh(dataset)
        version.previous_version_id = dataset.current_version_id
        db.session.commit()
        if dataset.current_version_id:
            comparison = create_comparison(dataset.current_version_id, version.id, progress=progress)
            if _unresolved(comparison):
                version.error_text = 'Требуется исправить неоднозначности сопоставления.'
                db.session.commit()
                return version
        version.error_text = ''
        progress('applying')
        return accept_version(version.id, version.uploaded_by)
    except Exception as exc:
        db.session.rollback()
        version = db.session.get(LimitVersion, version_id)
        if version and version.status != 'accepted':
            version.status = 'error'
            version.error_text = _processing_error_message(exc)
            _audit_version(version, 'limits_processing_error', error=str(exc))
            db.session.commit()
        raise


def resume_pending_versions():
    """Run after restore; errors are reported per file, never silently discarded."""
    ids = [row.id for row in LimitVersion.query.filter(
        LimitVersion.status.in_(('queued', 'parsing', 'review'))
    ).order_by(LimitVersion.id).all()]
    results = []
    for version_id in ids:
        try:
            version = process_version(version_id)
            results.append({'version_id': version_id, 'status': version.status})
        except Exception as exc:
            results.append({'version_id': version_id, 'status': 'error', 'error': str(exc)})
    return results


def rename_legacy_limits_dataset():
    """Apply the one-time rename of the shipped registry with stable identity."""
    marker = 'limits_legacy_dataset_renamed_v2'
    if Setting.get(marker, '') == '1':
        return False

    dataset = None
    candidates = LimitDataset.query.filter_by(
        name='1', current_version_id=22).limit(2).all()
    if len(candidates) == 1:
        dataset = candidates[0]

    if (dataset is not None and dataset.name == '1'
            and dataset.current_version_id == 22):
        dataset.name = 'Лимиты'
        renamed = True
    else:
        renamed = False

    # The migration runs only after additive restore has completed. Recording
    # even a no-op prevents a future user-created registry named "1" from
    # becoming migration input on an installation that never had the legacy row.
    Setting.set(marker, '1')
    return renamed


@_serialized
def stage_upload(dataset, stream, filename, source_date, uploaded_by, storage_root):
    """Copy an upload once into private immutable module storage."""
    if not filename or not filename.lower().endswith('.xlsx'):
        raise LimitsError('Нужен файл XLSX.')
    if not source_date:
        raise LimitsError('Дата среза обязательна и не берётся из имени файла.')
    root = Path(storage_root) / 'limits' / str(dataset.id)
    root.mkdir(parents=True, exist_ok=True)
    clean_name = os.path.basename(filename)
    target = root / f'{uuid.uuid4().hex}.xlsx'
    stream.save(str(target))
    digest = _sha256(str(target))
    duplicate = LimitVersion.query.filter_by(dataset_id=dataset.id, sha256=digest).first()
    if duplicate:
        target.unlink(missing_ok=True)
        return duplicate, True
    version = LimitVersion(
        dataset_id=dataset.id, uploaded_by=uploaded_by, source_date=source_date,
        source_name=clean_name, source_path=str(target), sha256=digest, status='queued',
        previous_version_id=dataset.current_version_id)
    db.session.add(version)
    db.session.flush()
    _audit_version(version, 'limits_upload')
    db.session.commit()
    return version, False


@_serialized
def parse_version(version_id, progress=None):
    """Parse staged file and materialise rows. Intended for a background worker."""
    version = db.session.get(LimitVersion, version_id)
    if not version:
        raise LimitsError('Версия не найдена.')
    if version.status not in ('queued', 'error'):
        return version
    version.status = 'parsing'
    version.error_text = ''
    db.session.commit()
    try:
        from limits_parser import parse_xlsx
        snapshot = parse_xlsx(version.source_path, progress=progress) if progress else parse_xlsx(version.source_path)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('rows'), list):
            raise LimitsError('Парсер вернул недопустимый снимок.')
        if progress:
            progress('saving')
        version.snapshot_json = _dump(snapshot)
        version.warnings_json = _dump(snapshot.get('warnings', []))
        LimitRowSnapshot.query.filter_by(version_id=version.id).delete()
        for row in snapshot['rows']:
            uin = str(row.get('uin', '')).strip()
            if not uin:
                continue
            db.session.add(LimitRowSnapshot(
                version_id=version.id, object_id=None,
                source_row=int(row.get('row') or 0), stable_key=stable_key(row),
                cells_json=_dump(row.get('cells', {}))))
        version.parser_version = str(snapshot.get('parser_version', 'limits_parser'))
        version.status = 'review'
        db.session.commit()
        # A staged successor gets a durable preview immediately. Publication
        # still requires an operator and checks that this base is current.
        if version.previous_version_id and not progress:
            create_comparison(version.previous_version_id, version.id)
        return version
    except Exception as exc:
        db.session.rollback()
        version = db.session.get(LimitVersion, version_id)
        version.status = 'error'
        version.error_text = _processing_error_message(exc)
        db.session.commit()
        raise


def _decimal(value):
    if isinstance(value, dict):
        value = value.get('value')
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@_serialized
def create_comparison(old_version_id, new_version_id, resolutions=None, progress=None):
    old = db.session.get(LimitVersion, old_version_id)
    new = db.session.get(LimitVersion, new_version_id)
    if not old or not new or old.id == new.id or old.dataset_id != new.dataset_id:
        raise LimitsError('Для сравнения выберите две версии одного набора.')
    if old.status not in ('review', 'accepted') or new.status not in ('review', 'accepted'):
        raise LimitsError('Сравнивать можно только успешно разобранные версии.')
    comparison = LimitComparison.query.filter_by(
        old_version_id=old.id, new_version_id=new.id).first()
    parsed_resolutions = resolutions or {'transfers': []}
    if comparison and resolutions is None:
        for item in LimitComparisonResolution.query.filter_by(comparison_id=comparison.id).all():
            resolution = _json(item.resolution_json, {})
            parsed_resolutions['transfers'].append(resolution)
    from limits_parser import compare_snapshots, LimitsParserError
    try:
        result = compare_snapshots(_json(old.snapshot_json, {}), _json(new.snapshot_json, {}),
                                   parsed_resolutions, progress=progress)
    except LimitsParserError as exc:
        raise LimitsError(str(exc)) from exc
    if progress:
        progress('changes')
    for index, issue in enumerate(result.get('issues', [])):
        if isinstance(issue, dict) and not issue.get('key'):
            issue['key'] = f"{issue.get('code', 'issue')}:{issue.get('uin', '')}"
    if not comparison:
        comparison = LimitComparison(old_version_id=old.id, new_version_id=new.id)
        db.session.add(comparison)
        db.session.flush()
    else:
        LimitChange.query.filter_by(comparison_id=comparison.id).delete()
    comparison.status = 'needs_resolution' if result.get('issues') else 'ready'
    comparison.summary_json = _dump(result.get('summary', {}))
    comparison.issues_json = _dump(result.get('issues', []))
    dataset = db.session.get(LimitDataset, new.dataset_id)
    if new.status == 'review' and old.id == dataset.current_version_id:
        new.previous_version_id = old.id
    for item in result.get('changes', []):
        uin = str(item.get('uin') or item.get('object_uin') or '').strip()
        if not uin:
            continue
        obj = LimitObject.query.filter_by(dataset_id=new.dataset_id, uin=uin).first()
        old_value, new_value = item.get('old'), item.get('new')
        old_num, new_num = _decimal(old_value), _decimal(new_value)
        absolute = _decimal(item.get('delta'))
        if absolute is None and old_num is not None and new_num is not None:
            absolute = new_num - old_num
        relative = _decimal(item.get('percent_change'))
        # The parser returns percent units, not a 0..1 ratio.
        source = {key: item.get(key) for key in (
            'kind', 'old_row', 'new_row', 'year', 'source', 'programme',
            'old_programme', 'new_programme')}
        source.update(old_cell=(old_value or {}).get('cell') if isinstance(old_value, dict) else None,
                      new_cell=(new_value or {}).get('cell') if isinstance(new_value, dict) else None)
        db.session.add(LimitChange(
            comparison_id=comparison.id, object_id=obj.id if obj else None, uin=uin,
            category=str(item.get('category', item.get('kind', 'other'))),
            field_key=str(item.get('field') or item.get('kind') or ''),
            field_label=str(item.get('label') or {
                'object_added': 'Новый объект', 'object_absent': 'Объект отсутствует в выгрузке',
                'row_added': 'Добавлена строка мероприятия', 'row_absent': 'Строка отсутствует',
                'row_transferred': 'Перенос программных кодов',
            }.get(item.get('kind'), item.get('kind', ''))),
            old_json=_dump(old_value), new_json=_dump(new_value),
            absolute_delta=absolute, relative_delta=relative,
            source_json=_dump(source)))
    db.session.commit()
    return comparison


@_serialized
def resolve_comparison_issue(comparison_id, issue_key, resolution, user_id):
    comparison = db.session.get(LimitComparison, comparison_id)
    if not comparison or comparison.status not in ('needs_resolution', 'ready'):
        raise LimitsError('Сравнение недоступно для разрешения.')
    if not any(str(issue.get('key', issue.get('id', ''))) == str(issue_key)
               for issue in _json(comparison.issues_json, [])):
        raise LimitsError('Проблема сопоставления не найдена.')
    issue_key = str(issue_key) + '|' + str(resolution.get('old_row') or resolution.get('new_row'))
    item = LimitComparisonResolution.query.filter_by(
        comparison_id=comparison.id, issue_key=str(issue_key)).first()
    if item:
        item.resolution_json = _dump(resolution)
        item.resolved_by = user_id
        item.resolved_at = utcnow()
    else:
        db.session.add(LimitComparisonResolution(
            comparison_id=comparison.id, issue_key=str(issue_key),
            resolution_json=_dump(resolution), resolved_by=user_id))
    db.session.flush()
    # Recompute from immutable snapshots with all explicit row mappings. This
    # makes publication auditable and prevents an operator note from silently
    # selecting a row.
    db.session.add(AuditLog(user_id=user_id, action='limits_issue_resolved',
                           details=_dump({'comparison_id': comparison.id,
                                          'issue_key': issue_key, 'resolution': resolution})))
    create_comparison(comparison.old_version_id, comparison.new_version_id)
    version = db.session.get(LimitVersion, comparison.new_version_id)
    if version and version.status == 'review':
        process_version(version.id)


def _unresolved(comparison):
    return _json(comparison.issues_json, [])


@_serialized
def accept_version(version_id, user_id):
    """Publish the reviewed financial snapshot atomically."""
    version = db.session.get(LimitVersion, version_id)
    if not version or version.status != 'review':
        raise LimitsError('К принятию доступна только проверенная версия.')
    dataset = db.session.query(LimitDataset).filter_by(id=version.dataset_id)\
        .populate_existing().with_for_update().one()
    db.session.refresh(version)
    if version.status != 'review':
        raise LimitsError('Эта версия уже обработана другим оператором.')
    if version.previous_version_id != dataset.current_version_id:
        raise LimitsError('Актуальная база изменилась; повторите сравнение с текущей версией.')
    comparison = None
    if dataset.current_version_id:
        comparison = LimitComparison.query.filter_by(
            old_version_id=dataset.current_version_id, new_version_id=version.id).first()
        if not comparison:
            raise LimitsError('Перед принятием создайте сравнение с актуальной версией.')
        if _unresolved(comparison):
            raise LimitsError('Есть неразрешённые неоднозначности сопоставления.')
    accepted = LimitVersion.query.filter_by(dataset_id=dataset.id, status='accepted')\
        .order_by(LimitVersion.accepted_at.asc(), LimitVersion.id.asc()).all()
    version.previous_version_id = dataset.current_version_id
    version.status = 'accepted'
    version.accepted_by = user_id
    version.accepted_at = utcnow()
    dataset.current_version_id = version.id
    from collections import defaultdict
    grouped = defaultdict(list)
    for row in _json(version.snapshot_json, {}).get('rows', []):
        grouped[row['uin']].append(row)
    objects = {obj.uin: obj for obj in LimitObject.query.filter_by(dataset_id=dataset.id).all()}
    for obj in objects.values():
        obj.is_present = obj.uin in grouped
    for uin, rows in grouped.items():
        obj = objects.get(uin)
        if obj is None:
            obj = LimitObject(dataset_id=dataset.id, uin=uin)
            db.session.add(obj)
            db.session.flush()
            objects[uin] = obj
        titles = sorted({str(_cell_value(row.get('cells', {}).get('object_name')) or '') for row in rows} - {''})
        addresses = sorted({str(_cell_value(row.get('cells', {}).get('address')) or '') for row in rows} - {''})
        obj.title = ' / '.join(titles) or obj.title
        obj.address = ' / '.join(addresses) or obj.address
        obj.last_seen_version_id, obj.is_present = version.id, True
        for row in rows:
            LimitRowSnapshot.query.filter_by(version_id=version.id, source_row=row['row']).update(
                {'object_id': obj.id}, synchronize_session=False)
    if comparison:
        for change in LimitChange.query.filter_by(comparison_id=comparison.id).all():
            change.object_id = objects[change.uin].id
        db.session.flush()
    protected = protected_version_ids()
    removable = [row for row in accepted if row.id not in protected]
    for row in removable[:max(0, len(accepted) + 1 - MAX_HISTORY)]:
        _purge_version_data(row, manual=False)
    _audit_version(version, 'limits_auto_accepted', accepted_by=user_id,
                   previous_version_id=version.previous_version_id)
    from max_limits import queue_accepted_changes
    queue_accepted_changes(version, comparison)
    version.accepted_at = utcnow()
    db.session.commit()
    cleanup_purged_files()
    return version


@_serialized
def purge_version(version_id, manual=True):
    """Delete an upload, restoring the remaining current snapshot atomically."""
    version = db.session.get(LimitVersion, version_id)
    if not version:
        return False
    if version.status not in ('accepted', 'review', 'error', 'rejected'):
        raise LimitsError('Загрузка ещё обрабатывается. Дождитесь завершения проверки.')
    dataset = db.session.query(LimitDataset).filter_by(
        id=version.dataset_id).populate_existing().with_for_update().one()
    was_current = dataset.current_version_id == version.id
    fallback = None
    if was_current:
        fallback_query = LimitVersion.query.filter(
            LimitVersion.dataset_id == dataset.id,
            LimitVersion.status == 'accepted',
            LimitVersion.id != version.id).order_by(
                LimitVersion.accepted_at.desc(), LimitVersion.id.desc())
        previously_deleted = {
            row.version_id for row in LimitVersionTombstone.query.filter_by(
                dataset_id=dataset.id).all()
        }
        if previously_deleted:
            fallback_query = fallback_query.filter(
                ~LimitVersion.id.in_(previously_deleted))
        fallback = fallback_query.first()
        dataset.current_version_id = fallback.id if fallback else None
    tombstone = _purge_version_data(version, manual=manual)
    if was_current:
        from collections import defaultdict
        grouped = defaultdict(list)
        if fallback:
            snapshot = _json(fallback.snapshot_json, {})
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('rows'), list):
                raise LimitsError('Не удалось восстановить предыдущий срез: повреждён снимок. Удаление отменено.')
            for row in snapshot['rows']:
                grouped[row['uin']].append(row)
        for obj in LimitObject.query.filter_by(dataset_id=dataset.id).all():
            rows = grouped.get(obj.uin, [])
            obj.is_present = bool(rows)
            if rows:
                obj.last_seen_version_id = fallback.id
                titles = sorted({str(_cell_value(row.get('cells', {}).get('object_name')) or '')
                                 for row in rows} - {''})
                addresses = sorted({str(_cell_value(row.get('cells', {}).get('address')) or '')
                                    for row in rows} - {''})
                obj.title = ' / '.join(titles)
                obj.address = ' / '.join(addresses)
            elif not fallback or obj.last_seen_version_id == version_id:
                obj.last_seen_version_id = None
    db.session.commit()
    if tombstone is not None:
        _publish_manual_tombstone(tombstone)
    cleanup_purged_files()
    return True


def _publish_manual_tombstone(tombstone):
    """Publish only the tiny full-loss safeguard, never a full backup."""
    try:
        from durable_data import record_limit_tombstone
        record_limit_tombstone(
            tombstone.version_id, tombstone.dataset_id,
            tombstone.source_path, tombstone.source_sha256)
        from database import Setting
        Setting.set('limits_tombstones_external_pending', '0')
        Setting.set('limits_tombstone_backup_error', '')
        db.session.commit()
    except Exception as exc:
        # The committed DB marker still protects additive restore.  The
        # rotation worker retries this sidecar separately before its snapshot.
        from database import Setting
        db.session.rollback()
        Setting.set('limits_tombstones_external_pending', '1')
        Setting.set('limits_tombstone_backup_error', str(exc))
        db.session.commit()


def _purge_version_data(version, manual=False):
    """Delete materialisations and record filesystem intent in caller transaction."""
    if version.id in protected_version_ids():
        raise LimitsError('Версия выбрана в MAX или нужна для её отчёта. Сначала смените источник MAX.')
    source_path = version.source_path
    comparisons = LimitComparison.query.filter(
        (LimitComparison.old_version_id == version.id) |
        (LimitComparison.new_version_id == version.id)).all()
    for comparison in comparisons:
        LimitComparisonResolution.query.filter_by(comparison_id=comparison.id).delete()
        LimitChange.query.filter_by(comparison_id=comparison.id).delete()
        db.session.delete(comparison)
    LimitRowSnapshot.query.filter_by(version_id=version.id).delete()
    LimitObject.query.filter_by(last_seen_version_id=version.id).update(
        {LimitObject.last_seen_version_id: None}, synchronize_session=False)
    LimitVersion.query.filter_by(previous_version_id=version.id).update(
        {LimitVersion.previous_version_id: None}, synchronize_session=False)
    from database import Setting
    pending_paths = _json(Setting.get('limits_file_cleanup_pending', '[]'), [])
    if source_path and source_path not in pending_paths:
        pending_paths.append(source_path)
    Setting.set('limits_file_cleanup_pending', _dump(pending_paths))
    Setting.set('limits_rotation_needs_backup', '1')
    tombstone = None
    if manual:
        tombstone = LimitVersionTombstone.query.filter_by(
            version_id=version.id).first()
        if tombstone is None:
            tombstone = LimitVersionTombstone(
                version_id=version.id,
                dataset_id=version.dataset_id,
                source_path=source_path or '',
                source_sha256=version.sha256 or '',
            )
            db.session.add(tombstone)
        else:
            tombstone.dataset_id = version.dataset_id
            tombstone.source_path = source_path or ''
            tombstone.source_sha256 = version.sha256 or ''
        Setting.set('limits_tombstones_external_pending', '1')
    db.session.delete(version)
    db.session.flush()
    return tombstone


@_serialized
def cleanup_purged_files():
    """Repeat post-commit source removal safely after restart."""
    from database import Setting
    pending_paths = _json(Setting.get('limits_file_cleanup_pending', '[]'), [])
    remaining = []
    for path in pending_paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError:
            remaining.append(path)
    Setting.set('limits_file_cleanup_pending', _dump(remaining))
    db.session.commit()
    return len(remaining)


@_serialized
def cleanup_temporary_versions(hours=72):
    """Remove old rejected/error staging data; accepted history is untouched."""
    cutoff = utcnow() - timedelta(hours=max(1, int(hours)))
    candidates = LimitVersion.query.filter(
        LimitVersion.status.in_(('error', 'rejected')),
        LimitVersion.uploaded_at < cutoff).all()
    for row in candidates:
        _purge_version_data(row, manual=False)
    if candidates:
        db.session.commit()
        cleanup_purged_files()
    return len(candidates)


@_serialized
def reconcile_history_window():
    """Enforce the retained financial-version history window on recovery."""
    blocked = []
    for dataset in LimitDataset.query.all():
        accepted = LimitVersion.query.filter_by(dataset_id=dataset.id, status='accepted')\
            .order_by(LimitVersion.accepted_at.asc(), LimitVersion.id.asc()).all()
        while len(accepted) > MAX_HISTORY:
            protected = protected_version_ids()
            candidate = next((row for row in accepted if row.id not in protected
                              and row.id != dataset.current_version_id), None)
            if candidate is None:
                blocked.extend({'dataset_id': dataset.id, 'version_id': row.id}
                               for row in accepted)
                break
            try:
                purge_version(candidate.id, manual=False)
            except LimitsError:
                blocked.append({'dataset_id': dataset.id, 'version_id': candidate.id})
                break
            accepted.remove(candidate)
    return blocked


def protected_version_ids():
    """Pin the selected MAX snapshot and both inputs of its persisted report."""
    setting = Setting.query.filter_by(key='max_limits_version_id').populate_existing().first()
    value = setting.value if setting else ''
    if not str(value).isdigit():
        return set()
    selected = db.session.get(LimitVersion, int(value))
    if selected is None:
        return set()
    protected = {selected.id}
    if selected.previous_version_id:
        protected.add(selected.previous_version_id)
    for comparison in LimitComparison.query.filter_by(new_version_id=selected.id).all():
        protected.add(comparison.old_version_id)
    return protected


def export_comparison(comparison_id):
    """Create a safe XLSX report. Formula-looking user data is always escaped."""
    comparison = db.session.get(LimitComparison, comparison_id)
    if not comparison:
        raise LimitsError('Сравнение не найдено.')
    from limits_parser import compare_snapshots, export_comparison as export_xlsx
    old, new = comparison.old_version, comparison.new_version
    if old.status not in ('accepted', 'review') or new.status not in ('accepted', 'review'):
        raise LimitsError('Версии недоступны.')
    old_snapshot, new_snapshot = _json(old.snapshot_json, {}), _json(new.snapshot_json, {})
    resolutions = {'transfers': [_json(row.resolution_json, {}) for row in
                   LimitComparisonResolution.query.filter_by(comparison_id=comparison.id).all()]}
    result = compare_snapshots(old_snapshot, new_snapshot, resolutions)
    result['metadata'].update(old_version=f'{old.id} / {old.source_date}',
                              new_version=f'{new.id} / {new.source_date}')
    return export_xlsx(result, old_snapshot, new_snapshot)