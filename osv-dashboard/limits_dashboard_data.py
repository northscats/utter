"""Read-only chart data from accepted, immutable GIS REB limits snapshots.

Every monetary series represents one semantic XLSX column (one period and
funding source); values from different columns are never combined.
"""
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from database import LimitChange, LimitComparison, LimitObject, LimitVersion, db


_MONEY_CATEGORIES = {'limit_cost', 'funding_snapshot', 'annual_funding'}
_CATEGORY_LABELS = {
    'limit_cost': 'Предельная стоимость',
    'annual_funding': 'Годовое финансирование',
    'funding_snapshot': 'Финансирование на дату',
    'schedule': 'Сроки',
    'completion_year': 'Год завершения',
    'requisites': 'Реквизиты',
    'programme': 'Программа',
    'programme_transfer': 'Перенос программы',
    'object_added': 'Новые объекты',
    'object_absent': 'Отсутствующие объекты',
    'row_added': 'Новые строки',
    'row_absent': 'Отсутствующие строки',
    'object': 'Состав объектов',
}


def _snapshot(version):
    try:
        data = json.loads(version.snapshot_json or '{}')
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _money(cell):
    if not isinstance(cell, dict) or cell.get('kind') != 'money':
        return None
    value = cell.get('value')
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _version_label(version):
    return f'{version.source_date:%d.%m.%Y} · №{version.id}'


def _column_totals(snapshot):
    """Return sums only when every report row has a valid monetary cell.

    A partial total would silently understate a financial figure, so represent
    the whole point as missing instead. A genuine zero remains zero.
    """
    columns = snapshot.get('columns')
    rows = snapshot.get('rows')
    if not isinstance(columns, list) or not isinstance(rows, list) or not rows:
        return {}, {}
    labels, totals = {}, {}
    for column in columns:
        if not isinstance(column, dict) or column.get('category') not in _MONEY_CATEGORIES:
            continue
        key = column.get('key')
        if not isinstance(key, str) or not key:
            continue
        labels[key] = str(column.get('label') or key)
        total = Decimal(0)
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('cells'), dict):
                total = None
                break
            amount = _money(row['cells'].get(key))
            if amount is None:
                total = None
                break
            total += amount
        totals[key] = total
    return labels, totals


def _object_count(snapshot):
    rows = snapshot.get('rows')
    if not isinstance(rows, list):
        return None
    if any(not isinstance(row, dict) or not row.get('uin') for row in rows):
        return None
    return len({row['uin'] for row in rows})


def _comparison_context(dataset, current, version_by_id):
    empty = {
        'available': False, 'comparison_id': None, 'old_label': '',
        'new_label': _version_label(current) if current else '',
        'total': 0, 'objects': 0, 'categories': [], 'ranking': [],
        'ranking_label': 'Изменение предельной стоимости, тыс. руб.',
        'metric_label': 'Предельная стоимость',
    }
    if not current or current.previous_version_id not in version_by_id:
        return empty
    previous = version_by_id[current.previous_version_id]
    comparison = LimitComparison.query.filter_by(
        old_version_id=previous.id, new_version_id=current.id).first()
    if comparison is None:
        return empty

    changes = LimitChange.query.filter_by(comparison_id=comparison.id).all()
    categories = Counter()
    ranking = defaultdict(Decimal)
    affected_uins = set()
    for change in changes:
        affected_uins.add(change.uin)
        categories[change.category] += 1
        if change.field_key == 'limit_cost' and change.category == 'limit_cost':
            # An object may have multiple programme rows. Signed row deltas
            # are summed only within this one financial measure.
            if change.absolute_delta is not None:
                ranking[change.uin] += change.absolute_delta
    objects = {
        obj.uin: obj for obj in LimitObject.query.filter_by(
            dataset_id=dataset.id).filter(
            LimitObject.uin.in_(ranking.keys())).all()
    } if ranking else {}
    empty.update(
        available=True, comparison_id=comparison.id,
        old_label=_version_label(previous),
        total=len(changes), objects=len(affected_uins),
        categories=[
            {'label': _CATEGORY_LABELS.get(category, category), 'value': count}
            for category, count in categories.most_common()
        ],
        ranking=[
            {'uin': uin, 'title': objects[uin].title if uin in objects else '',
             'delta': float(amount)}
            for uin, amount in sorted(ranking.items(), key=lambda pair: (
                -abs(pair[1]), pair[0]))[:8]
        ],
    )
    return empty


def dashboard_context(dataset):
    """Return only accepted data belonging to the selected dataset."""
    versions = LimitVersion.query.filter_by(
        dataset_id=dataset.id, status='accepted').order_by(
        LimitVersion.source_date.asc(), LimitVersion.id.asc()).all()
    snapshots = [_snapshot(version) for version in versions]
    per_version = [_column_totals(snapshot) for snapshot in snapshots]
    object_counts = [_object_count(snapshot) for snapshot in snapshots]
    version_by_id = {version.id: version for version in versions}
    # Publication, not date sorting, determines the currently accepted base.
    current = version_by_id.get(dataset.current_version_id)
    current_index = versions.index(current) if current is not None else None

    keys = dict.fromkeys(key for labels, _ in per_version for key in labels)
    current_count = object_counts[current_index] if current_index is not None else None
    previous_index = (
        versions.index(version_by_id[current.previous_version_id])
        if current is not None and current.previous_version_id in version_by_id
        else None
    )
    previous_count = object_counts[previous_index] if previous_index is not None else None
    metrics = [{
        'key': 'objects_count', 'label': 'Количество объектов', 'unit': 'объектов',
        'points': object_counts, 'latest': current_count,
        'delta': (current_count - previous_count)
        if current_count is not None and previous_count is not None else None,
    }]
    for key in keys:
        points = [totals.get(key) for _, totals in per_version]
        latest = points[current_index] if current_index is not None else None
        previous = points[previous_index] if previous_index is not None else None
        label = next(
            labels[key] for labels, _ in reversed(per_version) if key in labels)
        metrics.append({
            'key': key, 'label': label, 'unit': 'тыс. руб.',
            'points': [float(point) if point is not None else None for point in points],
            'latest': float(latest) if latest is not None else None,
            'delta': float(latest - previous)
            if latest is not None and previous is not None else None,
        })
    metrics[1:] = sorted(metrics[1:], key=lambda item: (
        item['key'] != 'limit_cost', item['label'].casefold(), item['key']))

    return {
        'versions': [
            {'id': version.id, 'date': version.source_date.isoformat(),
             'label': _version_label(version),
             'objects': count}
            for version, count in zip(versions, object_counts)
        ],
        'metrics': metrics,
        'changes': _comparison_context(dataset, current, version_by_id),
        'current_label': _version_label(current) if current else '',
        'current_objects': current_count,
        'has_data': bool(versions),
    }