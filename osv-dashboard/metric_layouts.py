# -*- coding: utf-8 -*-
"""Validation helpers for per-user key-indicator modal layouts."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from annual_key_indicators import CARD_SPECS
from key_indicators import _CARD_ORDER


LAYOUT_VERSION = 1
LAYOUT_MODES = frozenset(('osv', 'annual'))
RESERVED_DASHBOARD_IDS = frozenset(('__dashboard__', 'dashboard'))

# These lists intentionally come from the two calculation modules rather than
# from request data.  A layout cannot be used as a way to address an
# arbitrary/nonexistent metric.
METRIC_IDS_BY_MODE = {
    'osv': frozenset(_CARD_ORDER),
    'annual': frozenset(spec['id'] for spec in CARD_SPECS),
}

MAX_ITEM_KEY_LENGTH = 128
MAX_ITEMS = 250
# Stable DOM-style identifiers may contain a metric prefix and separators,
# but must not contain whitespace, control characters, or path syntax.
ITEM_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
PLACEMENTS = frozenset(('main', 'detail', 'hidden'))

with (Path(__file__).parent / 'data' / 'default_metric_layouts.json').open(
        encoding='utf-8') as _default_layouts_file:
    DEFAULT_METRIC_LAYOUTS = json.load(_default_layouts_file)


class LayoutValidationError(ValueError):
    """A client supplied an invalid layout document."""


def default_layout(mode, metric_id):
    """Return the checked-in default without exposing mutable module state."""
    if metric_id in RESERVED_DASHBOARD_IDS:
        order = list(_CARD_ORDER if mode == 'osv'
                     else (spec['id'] for spec in CARD_SPECS))
        return {
            'version': LAYOUT_VERSION,
            'items': {key: 'main' for key in order},
            'order': order,
        }
    return copy.deepcopy(DEFAULT_METRIC_LAYOUTS.get(
        f'{mode}:{metric_id}',
        {'version': LAYOUT_VERSION, 'items': {}},
    ))


def validate_mode_metric(mode, metric_id):
    """Validate the endpoint's mode and metric against server-owned IDs."""
    if mode not in LAYOUT_MODES:
        raise LayoutValidationError('Неизвестный режим показателей')
    if metric_id in RESERVED_DASHBOARD_IDS:
        return
    if metric_id not in METRIC_IDS_BY_MODE[mode]:
        raise LayoutValidationError('Неизвестный показатель')


def _strict_int(value, field):
    # bool is an int subclass, but accepting it would violate the JSON
    # contract's strict integer requirement.
    if isinstance(value, bool) or not isinstance(value, int):
        raise LayoutValidationError(f'Поле {field} должно быть целым числом')
    return value


def validate_layout_payload(payload, mode=None, metric_id=None):
    """Return a canonical copy of a valid PUT body.

    The endpoint deliberately accepts no extension fields.  This keeps the
    persisted document bounded and prevents clients from smuggling identity or
    unrelated settings into a UI-preferences record.
    """
    if not isinstance(payload, dict):
        raise LayoutValidationError('Тело запроса должно быть JSON-объектом')
    if set(payload) != {'layout', 'revision'}:
        raise LayoutValidationError(
            'Тело запроса должно содержать только layout и revision')

    revision = _strict_int(payload['revision'], 'revision')
    if revision < 0:
        raise LayoutValidationError('revision не может быть отрицательным')

    layout = payload['layout']
    if not isinstance(layout, dict):
        raise LayoutValidationError('Поле layout должно быть JSON-объектом')
    if set(layout) not in ({'version', 'items'}, {'version', 'items', 'order'}):
        raise LayoutValidationError(
            'layout должен содержать version, items и необязательный order')

    version = _strict_int(layout['version'], 'layout.version')
    if version != LAYOUT_VERSION:
        raise LayoutValidationError('Неподдерживаемая версия layout')

    items = layout['items']
    if not isinstance(items, dict):
        raise LayoutValidationError('Поле layout.items должно быть объектом')
    if len(items) > MAX_ITEMS:
        raise LayoutValidationError(
            f'layout.items не может содержать более {MAX_ITEMS} элементов')

    is_dashboard = metric_id in RESERVED_DASHBOARD_IDS
    if is_dashboard and mode not in LAYOUT_MODES:
        raise LayoutValidationError('Неизвестный режим показателей')

    normalized_items = {}
    for key, placement in items.items():
        if (not isinstance(key, str)
                or len(key) > MAX_ITEM_KEY_LENGTH
                or not ITEM_KEY_RE.fullmatch(key)):
            raise LayoutValidationError(
                'Ключ элемента имеет недопустимый формат или длину')
        if is_dashboard and key not in METRIC_IDS_BY_MODE[mode]:
            raise LayoutValidationError('Неизвестная карточка показателей')
        if not isinstance(placement, str) or placement not in PLACEMENTS:
            raise LayoutValidationError(
                'Размещение элемента должно быть main, detail или hidden')
        normalized_items[key] = placement

    normalized_layout = {'version': LAYOUT_VERSION, 'items': normalized_items}
    if 'order' in layout:
        order = layout['order']
        if not isinstance(order, list) or len(order) > MAX_ITEMS:
            raise LayoutValidationError(
                f'layout.order должен быть массивом до {MAX_ITEMS} ключей')
        seen = set()
        for key in order:
            if not isinstance(key, str) or key not in normalized_items:
                raise LayoutValidationError(
                    'Каждый ключ layout.order должен присутствовать в items')
            if key in seen:
                raise LayoutValidationError('Ключи layout.order не должны повторяться')
            seen.add(key)
        normalized_layout['order'] = list(order)

    return {
        'layout': normalized_layout,
        'revision': revision,
    }


__all__ = [
    'DEFAULT_METRIC_LAYOUTS', 'ITEM_KEY_RE', 'LAYOUT_MODES', 'LAYOUT_VERSION',
    'MAX_ITEM_KEY_LENGTH', 'MAX_ITEMS', 'METRIC_IDS_BY_MODE',
    'PLACEMENTS', 'RESERVED_DASHBOARD_IDS', 'LayoutValidationError',
    'default_layout', 'validate_layout_payload',
    'validate_mode_metric',
]