"""Single explicitly selected limit dataset shared by MAX features."""
from database import LimitDataset, Setting, db


SETTING_KEY = 'max_limits_dataset_id'


def selected_dataset_id():
    """Return the configured, existing dataset ID; never choose one implicitly."""
    value = Setting.get(SETTING_KEY, '')
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return None
    dataset_id = int(value)
    if dataset_id < 1:
        return None
    return dataset_id if db.session.get(LimitDataset, dataset_id) is not None else None