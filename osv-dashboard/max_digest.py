"""Administrator-triggered summaries of the current accepted limits comparison."""
import json
import io
import re
import uuid
from collections import defaultdict

from openpyxl import Workbook

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitObject, LimitVersion,
    MaxLimitsOutbox, MaxLimitsUin, db, utcnow,
)
from max_dataset_scope import selected_dataset_id
from max_funding_year import selection, visible_change
from max_readable_changes import label, transfer_fields, value

PREFIX = '__manual_digest__:'
MAX_TEXT = 3800


def current_comparison():
    dataset = db.session.get(LimitDataset, selected_dataset_id())
    if not dataset or not dataset.current_version_id:
        return None
    version = db.session.get(LimitVersion, dataset.current_version_id)
    if not version or version.status != 'accepted' or not version.previous_version_id:
        return None
    previous = db.session.get(LimitVersion, version.previous_version_id)
    if not previous or previous.status != 'accepted':
        return None
    comparison = LimitComparison.query.filter_by(
        old_version_id=previous.id, new_version_id=version.id, status='ready').first()
    return (version, comparison) if comparison else None


def _entries(comparison, uins, funding_year=None):
    if not uins:
        return []
    changes = (LimitChange.query.filter(
        LimitChange.comparison_id == comparison.id, LimitChange.uin.in_(uins))
        .order_by(LimitChange.uin, LimitChange.id).all())
    grouped = defaultdict(list)
    for change in changes:
        if visible_change(change, funding_year):
            grouped[change.uin].append(change)
    objects = {obj.uin: obj for obj in LimitObject.query.filter(
        LimitObject.dataset_id == comparison.new_version.dataset_id,
        LimitObject.uin.in_(grouped)).all()}
    entries = []
    for uin in sorted(grouped):
        title = (objects[uin].title if uin in objects else '') or 'Объект'
        title = ' '.join(title.split())[:300]
        entries.append((uin, f'{title}\nУИН {uin}'))
    return entries


def _pages(entries):
    pages = []
    current = []
    for uin, entry in entries:
        # Leave room for the totals and numbering; never cut an object in half.
        if current and sum(len(text) + 16 for _, text in current) + len(entry) + 16 > MAX_TEXT - 190:
            pages.append(current)
            current = []
        current.append((uin, entry))
    if current:
        pages.append(current)
    return pages


def _message(page, total, changed, positions):
    heading = (f'Всего ваших объектов — {total}\n'
               f'Объектов с изменившимися показателями — {changed}\n\n'
               'Перечень изменившихся объектов:')
    return heading + '\n\n' + '\n\n'.join(
        f'{positions[uin]}. {entry}' for uin, entry in page)


def _approved_objects(user_id):
    dataset_id = selected_dataset_id()
    if dataset_id is None:
        return set()
    return {uin for (uin,) in db.session.query(MaxLimitsUin.uin).join(
        LimitObject, (LimitObject.uin == MaxLimitsUin.uin) &
        (LimitObject.dataset_id == dataset_id)).filter(
        MaxLimitsUin.user_id == user_id, MaxLimitsUin.status == 'approved').all()}


def changed_uins_for_user(user_id):
    current = current_comparison()
    if not current:
        return set()
    approved = _approved_objects(user_id)
    if not approved:
        return set()
    return {change.uin for change in LimitChange.query.filter(
        LimitChange.comparison_id == current[1].id,
        LimitChange.uin.in_(approved)).all() if visible_change(change)}


def changes_excel_bytes(user_id):
    """Only this user's currently approved UINs and their latest accepted changes."""
    current = current_comparison()
    uins = changed_uins_for_user(user_id)
    if not current or not uins:
        raise ValueError('Нет изменений по вашим одобренным объектам.')
    changes = [change for change in LimitChange.query.filter(
        LimitChange.comparison_id == current[1].id,
        LimitChange.uin.in_(uins)).order_by(LimitChange.uin, LimitChange.id).all()
        if visible_change(change)]
    objects = {obj.uin: obj for obj in LimitObject.query.filter(
        LimitObject.dataset_id == current[0].dataset_id,
        LimitObject.uin.in_(uins)).all()}
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Изменения')
    sheet.append(['УИН', 'Объект', 'Адрес', 'Изменившийся показатель', 'Было', 'Стало'])
    for change in changes:
        obj = objects.get(change.uin)
        if change.field_key == 'row_transferred':
            from max_readable_changes import describe
            fields = transfer_fields(change) or [(describe(change), '—', '—')]
        else:
            field = label(change.field_label) or change.field_key
            if change.field_key in ('object_added', 'object_absent', 'row_added',
                                    'row_absent'):
                from max_readable_changes import describe
                field = describe(change)
            fields = [(field, value(change.old_json), value(change.new_json))]
        for field, before, after in fields:
            cells = [change.uin, obj.title if obj else '', obj.address if obj else '',
                     field, before, after]
            # Excel formula injection is possible in every imported cell.
            sheet.append(["'" + str(cell) if str(cell).startswith(('=', '+', '-', '@'))
                          else str(cell) for cell in cells])
    output = io.BytesIO()
    book.save(output)
    if output.tell() > 10 * 1024 * 1024:
        raise ValueError('Файл изменений слишком большой для отправки через бота.')
    return output.getvalue()


def prepare_digest(run_id=None):
    """Queue a manual digest once per recipient and confirmed admin action."""
    if run_id is None:
        run_id = uuid.uuid4().hex
    elif not isinstance(run_id, str) or re.fullmatch(r'[0-9a-fA-F]{32}', run_id) is None:
        raise ValueError('Идентификатор запуска должен содержать 32 шестнадцатеричных символа.')
    run_id = run_id.lower()
    current = current_comparison()
    if not current:
        raise ValueError('Нет принятого сравнения текущей версии с предыдущей.')
    version, comparison = current
    funding_year = selection()
    changed = {change.uin for change in LimitChange.query.filter_by(
        comparison_id=comparison.id).all() if visible_change(change, funding_year)}
    if not changed:
        return 0, 0
    subscriptions = MaxLimitsUin.query.join(
        LimitObject, (LimitObject.uin == MaxLimitsUin.uin) &
        (LimitObject.dataset_id == version.dataset_id)).filter(
        MaxLimitsUin.status == 'approved', MaxLimitsUin.uin.in_(changed)
    ).order_by(MaxLimitsUin.user_id, MaxLimitsUin.uin).all()
    recipients = defaultdict(set)
    for subscription in subscriptions:
        recipients[subscription.user_id].add(subscription.uin)
    queued_users = 0
    queued_messages = 0
    for user_id, uins in recipients.items():
        pages = _pages(_entries(comparison, uins, funding_year))
        queued = False
        for index, page in enumerate(pages):
            marker = f'{PREFIX}{funding_year}:{run_id}:{index}'
            existing = MaxLimitsOutbox.query.filter_by(
                user_id=user_id, version_id=version.id, uin=marker).first()
            if existing:
                if existing.delivered_at is not None or existing.cancelled_at is None:
                    continue
                # A filter change cancelled this page before delivery; it may
                # be queued again if the administrator selects this filter later.
                existing.cancelled_at = None
                existing.attempts = 0
                existing.next_attempt_at = utcnow()
                existing.text = json.dumps([uin for uin, _ in page], ensure_ascii=False)
            else:
                db.session.add(MaxLimitsOutbox(
                    user_id=user_id, version_id=version.id, uin=marker,
                    text=json.dumps([uin for uin, _ in page], ensure_ascii=False)))
            queued = True
            queued_messages += 1
        queued_users += int(queued)
    db.session.commit()
    return queued_users, queued_messages


def message_for_outbox(item):
    """Revalidate membership and comparison just before delivering a queued page."""
    # Old-format pages were created without a funding-year identity. Only
    # allow them under the original default; changing the filter cancels them.
    suffix = item.uin[len(PREFIX):] if item.uin.startswith(PREFIX) else ''
    funding_year = selection()
    if not (suffix.startswith(f'{funding_year}:') or
            (funding_year == '2026' and suffix.isdecimal())):
        return None
    current = current_comparison()
    if not current or current[0].id != item.version_id:
        return None
    try:
        uins = json.loads(item.text)
    except (TypeError, ValueError):
        return None
    if not isinstance(uins, list) or not uins or not all(isinstance(uin, str) for uin in uins):
        return None
    approved = _approved_objects(item.user_id)
    all_entries = _entries(current[1], approved)
    positions = {uin: i for i, (uin, _) in enumerate(all_entries, 1)}
    entries = [entry for entry in all_entries if entry[0] in uins]
    return _message(entries, len(approved), len(all_entries), positions) if entries else None