"""Read-only presentation queries for the limits module.

Keep every repeated-UIN row when applying semantic filters. Pagination is
applied only after filtering; a cap must never silently hide matching objects.
"""
import json
from collections import defaultdict

from sqlalchemy import or_

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitDelivery, LimitObject,
    LimitRowSnapshot, LimitSubscription, LimitVersion, db,
)


def decode(value, default=None):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def display_value(encoded):
    value = decode(encoded, encoded) if isinstance(encoded, str) else encoded
    if not isinstance(value, dict):
        return '—' if value is None else str(value)
    if value.get('kind') == 'blank' or ('value' in value and value['value'] is None):
        return '(пусто)'
    if value.get('kind') == 'dash':
        return str(value.get('raw') or '—') + ' (прочерк)'
    if 'value' in value:
        return str(value['value'])
    if 'raw' in value:
        return str(value['raw'])
    if not value:
        return '—'
    return '; '.join(f'{key}: {display_value(item)}' for key, item in value.items()
                     if key not in ('cells',))


def _value(cells, key):
    return str((cells.get(key) or {}).get('value') or '')


def _visible_cell(cell):
    """Hide values represented as absent/None, while preserving zeroes."""
    if not isinstance(cell, dict) or cell.get('kind') == 'blank':
        return False
    value = cell.get('value')
    if value is None:
        return False
    return not (isinstance(value, str) and value.strip().casefold() == 'none')


def objects_context(dataset, args):
    query = LimitObject.query.filter_by(dataset_id=dataset.id)
    search = args.get('q', '').strip()
    if search:
        like = '%' + search + '%'
        query = query.filter(or_(LimitObject.uin.ilike(like),
                                 LimitObject.title.ilike(like),
                                 LimitObject.address.ilike(like)))
    if args.get('present') in ('0', '1'):
        query = query.filter_by(is_present=args['present'] == '1')
    subscription_uins = db.session.query(LimitSubscription.uin).filter_by(
        dataset_id=dataset.id, is_active=True)
    if args.get('subscribers') in ('0', '1'):
        condition = LimitObject.uin.in_(subscription_uins)
        query = query.filter(condition if args['subscribers'] == '1' else ~condition)
    version = db.session.get(LimitVersion, dataset.current_version_id) if dataset.current_version_id else None
    snapshot = decode(version.snapshot_json, {}) if version else {}
    columns = snapshot.get('columns', [])
    rows_by_uin = defaultdict(list)
    for row in snapshot.get('rows', []):
        rows_by_uin[row['uin']].append(row.get('cells', {}))
    if args.get('changed') == '1':
        comparison = (LimitComparison.query.filter_by(new_version_id=version.id,
                      old_version_id=version.previous_version_id).first()) if version else None
        changed_uins = db.session.query(LimitChange.uin).filter_by(
            comparison_id=comparison.id) if comparison else []
        query = query.filter(LimitObject.uin.in_(changed_uins))
    year, source = args.get('year', '').strip(), args.get('source', '').strip()
    work_type, program = args.get('work_type', '').strip(), args.get('program', '').strip()
    codes = {key: args.get(key, '').strip() for key in
             ('gp_code', 'pp_code', 'om_code', 'dm_code', 'list_type')}
    financial_columns = [c for c in columns if c.get('category') in
                         ('annual_funding', 'funding_snapshot')]

    def row_matches(cells):
        if work_type and work_type.casefold() not in _value(cells, 'work_type').casefold():
            return False
        if any(value and value.casefold() != _value(cells, key).casefold()
               for key, value in codes.items()):
            return False
        if program and not any(program.casefold() in _value(cells, key).casefold()
                               for key in codes):
            return False
        if year or source:
            # The source/year condition applies to one actual financial cell,
            # not arbitrary text elsewhere in this object.
            if not any(
                (not year or str(column.get('year') or '') == year)
                and (not source or column.get('source') == source)
                and (cells.get(column['key']) or {}).get('kind') == 'money'
                for column in financial_columns
            ):
                return False
        return True

    if any((year, source, work_type, program, *codes.values())):
        matching = [uin for uin, rows in rows_by_uin.items() if any(row_matches(row) for row in rows)]
        query = query.filter(LimitObject.uin.in_(matching))
    total = query.count()
    pages = max(1, (total + 99) // 100)
    try:
        page = max(1, min(int(args.get('page', 1)), pages))
    except (ValueError, TypeError):
        page = 1
    sources = {}
    for column in financial_columns:
        key = column.get('source')
        if key:
            sources[key] = source_label(key)
    return dict(
        dataset=dataset, objects=query.order_by(LimitObject.uin).offset((page-1)*100).limit(100).all(),
        search=search, filters=args, total=total, page=page, pages=pages,
        filter_options={
            'years': sorted({str(c['year']) for c in financial_columns if c.get('year')}),
            'sources': [{'value': key, 'label': value} for key, value in sorted(sources.items())],
            'work_types': sorted({_value(row, 'work_type') for rows in rows_by_uin.values()
                                  for row in rows if _value(row, 'work_type')}),
        },
    )


def source_label(value):
    return {
        'federal_budget': 'Федеральный бюджет',
        'moscow_oblast_budget': 'Бюджет Московской области',
        'moscow_city_budget': 'Бюджет Москвы',
        'municipal_budgets_moscow_oblast': 'Бюджеты муниципальных образований МО',
        'extrabudgetary': 'Внебюджетные средства',
        'territorial_development_fund': 'Фонд развития территорий',
    }.get(value, value or '')


def object_context(obj, version_id=None):
    dataset = db.session.get(LimitDataset, obj.dataset_id)
    versions = LimitVersion.query.filter_by(dataset_id=obj.dataset_id, status='accepted').order_by(
        LimitVersion.accepted_at.desc(), LimitVersion.id.desc()).all()
    selected = next((v for v in versions if v.id == (version_id or dataset.current_version_id)), None)
    snapshot = decode(selected.snapshot_json, {}) if selected else {}
    columns = {c['key']: c for c in snapshot.get('columns', [])}
    current_rows, values_by_field = [], defaultdict(set)
    for row in snapshot.get('rows', []):
        if row.get('uin') != obj.uin:
            continue
        display = {key: [] for key in ('programme', 'requisites', 'funding', 'dates',
                                      'limit_cost', 'funding_snapshot')}
        display['source_row'] = row['row']
        for key, cell in row.get('cells', {}).items():
            if not _visible_cell(cell):
                continue
            col = columns.get(key, {'label': key, 'category': 'requisite'})
            item = dict(col, **cell)
            item['source'] = source_label(col.get('source'))
            category = col.get('category')
            target = {'annual_funding': 'funding', 'date': 'dates',
                      'limit_cost': 'limit_cost', 'funding_snapshot': 'funding_snapshot',
                      'programme': 'programme', 'status': 'programme'}.get(category, 'requisites')
            display[target].append(item)
            if target == 'requisites' and key != 'uin':
                values_by_field[key].add(str(cell.get('value')))
        current_rows.append(display)
    conflicts = [{'label': columns[key]['label'], 'values_list': sorted(list(vals))}
                 for key, vals in values_by_field.items() if len(vals) > 1]
    version_ids = [v.id for v in versions]
    comparisons = LimitComparison.query.filter(
        LimitComparison.new_version_id.in_(version_ids),
        LimitComparison.old_version_id.in_(version_ids)).all()
    grouped = defaultdict(list)
    comparison_map = {c.id: c for c in comparisons}
    if comparisons:
        for change in LimitChange.query.filter(
                LimitChange.comparison_id.in_(comparison_map), LimitChange.uin == obj.uin).all():
            grouped[comparison_map[change.comparison_id].new_version_id].append(change)
    messages = LimitDelivery.query.filter(
        LimitDelivery.object_id == obj.id, LimitDelivery.version_id.in_(version_ids)
    ).order_by(LimitDelivery.created_at.desc()).all()
    return dict(
        object=obj, dataset=dataset, versions=versions, selected_version=selected,
        current_rows=current_rows, conflicts=conflicts,
        snapshots=LimitRowSnapshot.query.filter(
            LimitRowSnapshot.object_id == obj.id,
            LimitRowSnapshot.version_id.in_(version_ids)).all(),
        subscriptions=LimitSubscription.query.filter_by(
            dataset_id=obj.dataset_id, uin=obj.uin, is_active=True).all(),
        history=[{'version': v, 'changes': grouped[v.id]} for v in versions],
        messages=messages,
    )