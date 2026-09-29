"""Explicit, persisted immutable file source shared by all MAX features."""
import json
from functools import wraps
from types import SimpleNamespace

from database import LimitDataset, LimitVersion, MaxLimitsOutbox, Setting, db, utcnow
from max_bot_config import locked
from limits_version_display import display_number

SETTING_KEY = 'max_limits_version_id'
LEGACY_SETTING_KEY = 'max_limits_dataset_id'


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with locked():
            return function(*args, **kwargs)
    return wrapped


def _id(value):
    value = str(value or '')
    return int(value) if value.isascii() and value.isdecimal() and int(value) > 0 else None


def selected_version():
    # Refresh the setting even in long-lived worker sessions.
    row = db.session.query(Setting).filter_by(key=SETTING_KEY).populate_existing().first()
    version_id = _id(row.value) if row else None
    version = db.session.get(LimitVersion, version_id) if version_id else None
    return version if version and version.status == 'accepted' else None


def selected_version_id():
    version = selected_version()
    return version.id if version else None


def selected_dataset_id():
    version = selected_version()
    return version.dataset_id if version else None


@serialized
def select_version(version_id):
    identifier = _id(version_id)
    # A caller may have cached this row before waiting for the shared lock.
    # Re-read after acquiring it so a concurrent completed purge cannot leave
    # a saved selection pointing at a deleted snapshot.
    version = LimitVersion.query.filter_by(id=identifier).populate_existing().first() if identifier else None
    if version_id not in (None, '') and (not version or version.status != 'accepted'):
        raise ValueError('Выберите успешно обработанную версию файла.')
    if version:
        snapshot_objects(version)  # Fail explicitly if the immutable source is damaged.
    Setting.set(SETTING_KEY, str(version.id) if version else '')
    pending = MaxLimitsOutbox.query.filter_by(delivered_at=None, cancelled_at=None)
    if version:
        pending = pending.filter(MaxLimitsOutbox.version_id != version.id)
    pending.update({'cancelled_at': utcnow()}, synchronize_session='fetch')
    db.session.commit()
    return version


@serialized
def migrate_selected_version():
    """Once only, including an explicitly empty result; never guess a dataset."""
    if db.session.get(Setting, SETTING_KEY) is not None:
        return selected_version()
    dataset_id = _id(Setting.get(LEGACY_SETTING_KEY, ''))
    dataset = db.session.get(LimitDataset, dataset_id) if dataset_id else None
    version = db.session.get(LimitVersion, dataset.current_version_id) if (
        dataset and dataset.current_version_id) else None
    return select_version(version.id if version and version.status == 'accepted'
                          and version.dataset_id == dataset.id else None)


def snapshot_objects(version=None):
    version = version or selected_version()
    if not version:
        return {}
    try:
        snapshot = json.loads(version.snapshot_json)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('rows'), list):
            raise ValueError()
        grouped = {}
        for row in snapshot['rows']:
            uin = str(row.get('uin', '')).strip()
            if uin:
                grouped.setdefault(uin, []).append(row)
        result = {}
        for uin, rows in grouped.items():
            def text(key):
                values = set()
                for row in rows:
                    cell = row.get('cells', {}).get(key, {})
                    value = cell.get('value', cell.get('raw', '')) if isinstance(cell, dict) else cell
                    if value is not None and str(value).strip():
                        values.add(str(value))
                return ' / '.join(sorted(values))
            result[uin] = SimpleNamespace(id=version.id, uin=uin, title=text('object_name'),
                                          address=text('address'), dataset_id=version.dataset_id)
        return result
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f'Повреждён снимок версии #{display_number(version)}.') from None


def selected_history_ids():
    """Only the selected file's accepted predecessor chain, never newer files."""
    version = selected_version()
    dataset_id = version.dataset_id if version else None
    ids = []
    while version and version.id not in ids and version.dataset_id == dataset_id:
        if version.status != 'accepted':
            break
        ids.append(version.id)
        version = db.session.get(LimitVersion, version.previous_version_id) if version.previous_version_id else None
    return ids