"""Plain Russian descriptions of verified changes; never infer missing amounts."""
import json
import re
from decimal import Decimal, InvalidOperation


def clean(value):
    return ' '.join(str(value or '').replace('\x00', '').split())


def amount(value):
    try:
        number = Decimal(str(value).replace(' ', '').replace('\u00a0', '').replace(',', '.'))
        if not number.is_finite():
            return str(value)
        return format(number, ',f').replace(',', ' ').replace('.', ',')
    except (InvalidOperation, ValueError):
        return str(value)


def label(value):
    text = clean(value)
    text = re.sub(r'^Строка\s+\d+\s*:\s*', '', text, flags=re.I)
    return text.replace('Объем финансирования', 'Финансирование').replace(
        'Объём финансирования', 'Финансирование')


def value(encoded):
    try:
        cell = json.loads(encoded)
    except (TypeError, ValueError):
        return 'не указано'
    if isinstance(cell, dict):
        if cell.get('kind') in ('blank', 'dash') or cell.get('value') is None:
            return 'не указано'
        return amount(cell['value']) if cell.get('kind') == 'money' else clean(cell['value'])
    if cell is None:
        return 'не указано'
    return amount(cell) if isinstance(cell, (int, float)) else clean(cell)


def transfer_fields(change):
    """Return recorded programme-code changes as separate before/after values."""
    try:
        source = json.loads(getattr(change, 'source_json', '') or '{}')
        before, after = source.get('old_programme'), source.get('new_programme')
    except (ValueError, TypeError, AttributeError):
        return []
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    codes = [('list_type', 'Вид перечня'), ('ap_status', 'Статус АП'),
             ('gp_code', 'Код ГП'), ('pp_code', 'Код ПП'),
             ('om_code', 'Код ОМ'), ('dm_code', 'Код ДМ')]
    return [(name, clean(before.get(key)) or 'не указано',
             clean(after.get(key)) or 'не указано')
            for key, name in codes if before.get(key) != after.get(key)]


def describe(change):
    title = label(change.field_label) or 'Сведения об объекте'
    kind = change.field_key
    if kind == 'object_added':
        return 'Объект добавлен в перечень.'
    if kind == 'object_absent':
        return 'Объекта больше нет в новом перечне. Это не означает, что объект закрыт.'
    if kind == 'row_added':
        return 'По объекту добавлено мероприятие.'
    if kind == 'row_absent':
        return 'В новом перечне отсутствует ранее указанное мероприятие.'
    if kind == 'row_transferred':
        fields = transfer_fields(change)
        if fields:
            return 'Изменились реквизиты мероприятия:\n' + '\n'.join(
                f'{name}: {before} → {after}' for name, before, after in fields)
        return ('Мероприятие сопоставлено с другой строкой перечня. '
                'Конкретное изменение программных кодов не указано.')
    old, new = value(change.old_json), value(change.new_json)
    if old == 'не указано':
        return f'{title}\nРаньше: не указано.\nТеперь: {new}.'
    if new == 'не указано':
        return f'{title}\nРаньше: {old}.\nВ новом документе значение не указано.'
    return f'{title}\nИзменилось с {old} на {new}.'


def date_label(value):
    return value.strftime('%d.%m.%Y') if hasattr(value, 'strftime') else clean(value)