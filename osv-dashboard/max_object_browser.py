"""Read-only, paginated MAX browser for approved limit objects."""
import json
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import aliased
from max_readable_changes import describe, label as readable_label, date_label
from max_funding_year import visible_change, visible_column
from max_dataset_scope import selected_dataset_id

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitObject, LimitVersion,
    MaxLimitsUin, db,
)


PAGE_SIZE = 8
TEXT_LIMIT = 3800
DETAIL_BUDGET = 2900
FINANCIAL_CATEGORIES = frozenset(('annual_funding', 'funding_snapshot', 'limit_cost'))
_NUMBER = re.compile(r'^\d{1,9}$')


def _short(value, length=160):
    return ' '.join(str(value if value is not None else '').replace('\x00', '').split())[:length]


def _number(value):
    try:
        number = Decimal(str(value).replace('\u00a0', '').replace(' ', '').replace(',', '.'))
    except (InvalidOperation, ValueError):
        return str(value)
    if not number.is_finite():
        return str(value)
    integer, dot, fraction = format(number, 'f').partition('.')
    sign = '-' if integer.startswith('-') else ''
    integer = integer.lstrip('-')
    integer = ' '.join(reversed([integer[max(0, i - 3):i]
                                 for i in range(len(integer), 0, -3)]))
    return sign + integer + (',' + fraction if dot else '')


def _decoded(encoded):
    try:
        cell = json.loads(encoded)
    except (TypeError, ValueError):
        return 'не указано'
    if isinstance(cell, dict):
        if cell.get('kind') in ('blank', 'dash'):
            return 'не указано' if cell.get('kind') == 'blank' else '—'
        value = cell.get('value', cell.get('raw'))
        if value is None:
            return 'не указано'
        if cell.get('kind') == 'money':
            return _number(value)
        return str(value)
    if cell is None:
        return 'не указано'
    return str(cell)


def _reply(text, buttons):
    return {'text': text[:TEXT_LIMIT], 'buttons': buttons}


def _navigation(previous, following, back):
    buttons = []
    page = []
    if previous:
        page.append(('Назад', previous))
    if following:
        page.append(('Далее', following))
    if page:
        buttons.append(page)
    buttons.append([('К объектам', back), ('Главное меню', 'menu:home')])
    return buttons


def _subscription(user_id, subscription_id):
    return MaxLimitsUin.query.filter_by(
        id=subscription_id, user_id=user_id, status='approved').first()


def _current_objects(uin):
    return (db.session.query(LimitObject, LimitDataset, LimitVersion)
            .join(LimitDataset, LimitDataset.id == LimitObject.dataset_id)
            .join(LimitVersion, LimitVersion.id == LimitDataset.current_version_id)
            .filter(LimitObject.uin == uin, LimitObject.is_present.is_(True),
                    LimitDataset.id == selected_dataset_id(),
                    LimitObject.last_seen_version_id == LimitVersion.id,
                    LimitVersion.dataset_id == LimitDataset.id,
                    LimitVersion.status == 'accepted'))


def _listing(user_id, mode, page):
    query = MaxLimitsUin.query.filter_by(user_id=user_id, status='approved').order_by(
        MaxLimitsUin.uin, MaxLimitsUin.id)
    total = query.count()
    rows = query.offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    if not rows and page:
        return _reply('На этой странице подписок нет.',
                      [[('К списку', f'browse:list:{mode}:0'),
                        ('Главное меню', 'menu:home')]])
    buttons = []
    descriptions = []
    for index, row in enumerate(rows[:PAGE_SIZE], page * PAGE_SIZE + 1):
        obj = _current_objects(row.uin).order_by(LimitObject.id).first()
        title = _short(obj[0].title, 30) if obj else 'Нет объекта'
        descriptions.append(f'{index}. {_short(obj[0].title, 150) if obj else "Нет объекта"}\nУИН {row.uin}')
        buttons.append([(f'{index}. {title}',
                         f'browse:sources:{mode}:{row.id}:0')])
    buttons += _navigation(
        f'browse:list:{mode}:{page - 1}' if page else None,
        f'browse:list:{mode}:{page + 1}' if len(rows) > PAGE_SIZE else None,
        'menu:home')
    return _reply(
        f'Мои объекты: {total} · страница {page + 1}/{max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}\n'
        + ('\n\n'.join(descriptions) if descriptions else 'Одобренных объектов пока нет.')
        + '\n\nВыберите объект кнопкой ниже.', buttons)


def _sources(subscription, mode, page):
    query = _current_objects(subscription.uin).order_by(LimitDataset.id, LimitObject.id)
    record = query.first()
    if record:
        return _card(subscription, record[0].id, mode, 0)
    return _reply('Объекта нет в выбранном администратором наборе.',
                  [[('К объектам', f'browse:list:{mode}:0'),
                    ('Главное меню', 'menu:home')]])


def _financial_entries(version, uin):
    try:
        snapshot = json.loads(version.snapshot_json or '{}')
    except (TypeError, ValueError):
        return []
    if not isinstance(snapshot, dict):
        return []
    columns = [column for column in snapshot.get('columns', [])
               if isinstance(column, dict) and column.get('category') in FINANCIAL_CATEGORIES
               and column.get('key') is not None and visible_column(column)]
    entries = []
    for index, row in enumerate(snapshot.get('rows', []), 1):
        if not isinstance(row, dict) or str(row.get('uin', '')).strip() != uin:
            continue
        cells = row.get('cells')
        if not isinstance(cells, dict):
            continue
        for column in columns:
            cell = cells.get(str(column['key']))
            if not isinstance(cell, dict):
                continue
            label = readable_label(column.get('label')) or 'Показатель'
            # The source's header carries its unit; never infer or convert units.
            value = (_number(cell['value']) if cell.get('kind') == 'money'
                     and cell.get('value') is not None else
                     'не указано' if cell.get('kind') == 'dash' else 'не указано'
                     if cell.get('value') is None else str(cell['value']))
            entries.append(f'{label} — {value}')
    return entries


def _change_pages(dataset_id, uin):
    old = aliased(LimitVersion)
    new = aliased(LimitVersion)
    records = (db.session.query(LimitChange, new, old)
               .join(LimitComparison, LimitComparison.id == LimitChange.comparison_id)
               .join(old, old.id == LimitComparison.old_version_id)
               .join(new, new.id == LimitComparison.new_version_id)
               .filter(LimitChange.uin == uin, old.dataset_id == dataset_id,
                       new.dataset_id == dataset_id,
                       old.status == 'accepted', new.status == 'accepted')
               .order_by(new.source_date.desc(), new.id.desc(), LimitChange.id).all())
    groups = {}
    for change, new_version, old_version in records:
        if not visible_change(change):
            continue
        key = (new_version.id, old_version.id)
        if key not in groups:
            groups[key] = (
                f'Обновление {date_label(new_version.source_date)} '
                f'· Было на {date_label(old_version.source_date)}', [])
        groups[key][1].append(describe(change))
    # A page belongs to one comparison, and repeats its date only once.
    return [[heading, *page] for heading, entries in groups.values()
            for page in _pages(entries)] or [[]]


def _pages(entries):
    """Split long values rather than silently dropping their trailing characters."""
    segments = []
    for entry in entries:
        segments.extend(entry[i:i + 900] for i in range(0, len(entry), 900))
    pages = []
    current = []
    size = 0
    for segment in segments:
        if current and (size + len(segment) + 1 > DETAIL_BUDGET or len(current) >= 6):
            pages.append(current)
            current, size = [], 0
        current.append(segment)
        size += len(segment) + 1
    if current:
        pages.append(current)
    return pages or [[]]


def _card(subscription, obj_id, mode, page):
    record = _current_objects(subscription.uin).filter(LimitObject.id == obj_id).first()
    if not record:
        return _reply('Объект недоступен или текущий срез не принят.',
                      [[('К объектам', f'browse:list:{mode}:0'),
                        ('Главное меню', 'menu:home')]])
    obj, dataset, version = record
    pages = (_pages(_financial_entries(version, subscription.uin)) if mode == 'info'
             else _change_pages(dataset.id, subscription.uin))
    if page >= len(pages):
        return _reply('На этой странице данных нет.',
                      [[('К объектам', f'browse:list:{mode}:0'),
                        ('Главное меню', 'menu:home')]])
    heading = (f'УИН {_short(subscription.uin, 120)}\n'
               f'Объект: {_short(obj.title, 180) or "—"}\n'
               f'Адрес: {_short(obj.address, 180) or "—"}\n'
               + (f'Источник: {_short(dataset.name, 100)}\n'
                  f'Документ от {date_label(version.source_date)}\n' if mode == 'info' else '')
               +
               f'{"Текущие суммы (не изменения)" if mode == "info" else "Что изменилось по объекту"} · '
               f'страница {page + 1}/{len(pages)}')
    text = heading + '\n\n' + ('\n\n'.join(pages[page]) if pages[page] else
        ('Подтверждённых изменений в сохранённой истории пока нет. '
         'Это может быть первый документ по объекту. '
         'Текущие суммы можно посмотреть кнопкой ниже.'
         if mode == 'changes' else 'В документе нет сумм по этому объекту.'))
    base = f'browse:card:{mode}:{subscription.id}:{obj.id}:'
    switch_mode = 'changes' if mode == 'info' else 'info'
    buttons = [[('Изменения объекта' if mode == 'info' else 'Текущие показатели',
                 f'browse:card:{switch_mode}:{subscription.id}:{obj.id}:0')]]
    return _reply(text, buttons + _navigation(
        base + str(page - 1) if page else None,
        base + str(page + 1) if page + 1 < len(pages) else None,
        f'browse:list:{mode}:0'))


def browse(user_id, payload):
    """Handle browse callbacks only; recheck approved ownership on every card."""
    if not isinstance(payload, str) or not payload.startswith('browse:'):
        return None
    if selected_dataset_id() is None:
        return _reply('Администратор ещё не выбрал набор отчётности для бота.',
                      [[('Главное меню', 'menu:home')]])
    if len(payload) > 100:
        return _reply('Неверная кнопка.', [[('Главное меню', 'menu:home')]])
    parts = payload.split(':')
    if (len(parts) not in (4, 5, 6) or parts[1] not in ('list', 'sources', 'card')
            or parts[2] not in ('info', 'changes')
            or len(parts) != {'list': 4, 'sources': 5, 'card': 6}[parts[1]]
            or any(not _NUMBER.fullmatch(item) for item in parts[3:])):
        return _reply('Неверная кнопка.', [[('Главное меню', 'menu:home')]])
    mode = parts[2]
    if parts[1] == 'list':
        return _listing(user_id, mode, int(parts[3]))
    subscription = _subscription(user_id, int(parts[3]))
    if subscription is None:
        return _reply('Подписка недоступна.', [[('К списку', f'browse:list:{mode}:0'),
                                               ('Главное меню', 'menu:home')]])
    if parts[1] == 'sources':
        return _sources(subscription, mode, int(parts[4]))
    return _card(subscription, int(parts[4]), mode, int(parts[5]))