"""Administrator-controlled funding-year visibility for MAX recipients."""
import json
import re
from datetime import datetime, timezone

from database import Setting


SETTING_KEY = 'max_limits_min_funding_year'
DEFAULT = '2026'
FUNDING_CATEGORIES = frozenset(('annual_funding', 'funding_snapshot'))


def choices():
    return ['all', *[str(year) for year in range(2020, max(2027, datetime.now(timezone.utc).year + 11))]]


def selection():
    saved = Setting.get(SETTING_KEY, DEFAULT)
    # An invalid stored value must never broaden a recipient's access.
    return saved if saved in choices() else DEFAULT


def visible_column(column, selected=None):
    selected = selection() if selected is None else selected
    if selected == 'all' or column.get('category') not in FUNDING_CATEGORIES:
        return True
    year = column.get('year')
    if type(year) is int:
        return year >= int(selected)
    key = column.get('key', '')
    match = re.match(r'^(?:annual_funding|funding_as_of)_(20\d{2})_', str(key))
    return match is not None and int(match.group(1)) >= int(selected)


def visible_change(change, selected=None):
    selected = selection() if selected is None else selected
    if selected == 'all' or change.category not in FUNDING_CATEGORIES:
        return True
    try:
        source = json.loads(change.source_json or '{}')
    except (TypeError, ValueError):
        source = {}
    if not isinstance(source, dict):
        source = {}
    return visible_column({
        'category': change.category,
        'year': source.get('year'),
        'key': change.field_key,
    }, selected)