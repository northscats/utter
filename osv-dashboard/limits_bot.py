# -*- coding: utf-8 -*-
"""Durable MAX bot worker for the limits module.

The HTTP webhook must only call :func:`ingest_event`.  All state changes and
all user-visible replies happen in this worker, so a provider retry or a
process restart cannot turn an already handled update into a second
subscription or moderation decision.

This module intentionally does not use the similarly named legacy helpers in
``limits_service``: those helpers commit intermediate state and therefore
cannot put an incoming-event transition and an outbox record in one
transaction.
"""
import json
import re
import threading
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError

from database import (
    AuditLog, LimitBotDialog, LimitBotOutbound, LimitDataset, LimitDelivery,
    LimitDeliveryAttempt, LimitDeliveryPart, LimitMaxEvent, LimitModerationRequest, LimitObject,
    LimitRecipient, LimitSubscription, Setting, db, utcnow,
)
from limits_service import valid_inn
from limits_max import MaxAPIError, MaxUnknownOutcome, event_key


MAX_ATTEMPTS = 5
MAX_EVENT_ATTEMPTS = 5
DEFAULT_BATCH_SIZE = 20
_STOP = object()
# Serializes the final eligibility check/send transition with administrative
# revoke/cancel actions inside this Python process.  Database row locks still
# protect competing web workers; once a provider call has begun no system can
# unsend it, hence the check and call must share this process lock.
_dispatch_lock = threading.RLock()


class LimitsBotError(ValueError):
    """An explicit operational failure which is safe to show in monitoring."""


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def _load(value, fallback):
    try:
        decoded = json.loads(value or '')
        return decoded if isinstance(decoded, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def _event_error(event, message, retry=False):
    """Record a bounded retry or a visible terminal event failure."""
    if retry and event.attempts < MAX_EVENT_ATTEMPTS:
        event.status = 'retry'
        event.next_attempt_at = utcnow() + _retry_delay(event.attempts, _retry_after(message))
        event.processed_at = None
    else:
        event.status = 'error'
        event.processed_at = utcnow()
    event.last_error = str(message)[:2000]


def _message_id(response):
    if response is None:
        return ''
    if isinstance(response, (str, int)):
        return str(response)
    if isinstance(response, dict):
        for key in ('message_id', 'id', 'messageId', 'mid'):
            if response.get(key) not in (None, ''):
                return str(response[key])
        body = response.get('body')
        if isinstance(body, dict) and body.get('mid') not in (None, ''):
            return str(body['mid'])
        nested = response.get('message')
        if isinstance(nested, dict):
            return _message_id(nested)
    return ''


def _retry_delay(attempts, retry_after=None):
    try:
        if retry_after is not None:
            return timedelta(seconds=max(1, min(int(retry_after), 3600)))
    except (TypeError, ValueError):
        pass
    return timedelta(minutes=min(60, 2 ** min(max(1, attempts), 10)))


def _is_unknown_exception(exc):
    # A response may have reached MAX when the connection closed after write.
    return isinstance(exc, (MaxUnknownOutcome, TimeoutError, ConnectionError))


def _retry_after(exc):
    return exc.retry_after if isinstance(exc, MaxAPIError) else None


def _is_rate_limited(exc):
    return isinstance(exc, MaxAPIError) and (exc.status == 429 or exc.retry_after is not None)


def _is_retryable_exception(exc):
    return not isinstance(exc, MaxAPIError) or bool(exc.retryable)


def _keyboard_attachments(keyboard):
    if not keyboard:
        return None
    return [{'type': 'inline_keyboard', 'payload': {'buttons': keyboard}}]


def _recipient(max_user_id, create=True):
    max_user_id = str(max_user_id or '').strip()
    if not max_user_id:
        raise LimitsBotError('В событии MAX отсутствует идентификатор пользователя.')
    row = LimitRecipient.query.filter_by(max_user_id=max_user_id).first()
    if row is None and create:
        row = LimitRecipient(max_user_id=max_user_id)
        db.session.add(row)
        db.session.flush()
    return row


def _dialog(recipient, dataset_id):
    row = LimitBotDialog.query.filter_by(recipient_id=recipient.id).first()
    if row is None:
        row = LimitBotDialog(recipient_id=recipient.id, dataset_id=dataset_id, step='idle')
        db.session.add(row)
    return row


def _queue_message(target_id, content, keyboard=None, moderation_request=None):
    if len(str(content)) > 4000 and moderation_request is None:
        rows = [_queue_message(target_id, part, keyboard if index == 0 else None)
                for index, part in enumerate(_split_alert_content(str(content)))]
        return rows[0]
    row = LimitBotOutbound(
        target_id=str(target_id), content=str(content),
        keyboard_json=_dump(keyboard or []), status='queued', next_attempt_at=utcnow(),
    )
    if moderation_request is not None:
        row.moderation_request_id = moderation_request.id
    db.session.add(row)
    return row


def _cancel_pending_for_recipient(recipient, reason, revoke_subscriptions=True,
                                  cancel_moderation=True):
    if revoke_subscriptions:
        LimitSubscription.query.filter_by(recipient_id=recipient.id, is_active=True).update(
            {'is_active': False, 'revoked_at': utcnow()}, synchronize_session=False)
    LimitDelivery.query.filter(
        LimitDelivery.recipient_id == recipient.id,
        LimitDelivery.status.in_(('queued', 'retry')),
    ).update({'status': 'cancelled', 'last_error': reason}, synchronize_session=False)
    delivery_ids = [row.id for row in LimitDelivery.query.filter_by(
        recipient_id=recipient.id).filter(
            LimitDelivery.status.in_(('queued', 'retry', 'unknown', 'error', 'cancelled'))).all()]
    if delivery_ids:
        LimitDeliveryPart.query.filter(LimitDeliveryPart.delivery_id.in_(delivery_ids),
                              LimitDeliveryPart.status.in_(('queued', 'retry'))).update(
                                  {'status': 'cancelled', 'last_error': reason},
                                  synchronize_session=False)
    LimitBotOutbound.query.filter(
        LimitBotOutbound.target_id == str(recipient.max_user_id),
        LimitBotOutbound.status.in_(('queued', 'retry')),
    ).update({'status': 'cancelled', 'last_error': reason}, synchronize_session=False)
    if cancel_moderation:
        LimitModerationRequest.query.filter_by(recipient_id=recipient.id, status='pending').update(
            {'status': 'cancelled', 'decision_reason': reason}, synchronize_session=False)


def ingest_event(event_id, payload=None):
    """Persist a provider update exactly once before it is acknowledged.

    The caller should commit no surrounding work after this call.  The unique
    event id is the final deduplication authority under concurrent webhooks.
    """
    # The webhook boundary normally passes the original payload.  Supporting
    # the two-argument form is useful to the long-poll bridge and keeps the
    # stable provider id explicit in tests.
    if payload is None and isinstance(event_id, dict):
        payload = event_id
        event_id = payload.get('update_id') or payload.get('event_id') or payload.get('id')
    if not isinstance(payload, dict):
        raise LimitsBotError('MAX event id обязателен для дедупликации.')
    try:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':'), default=str)
    except (TypeError, ValueError) as exc:
        raise LimitsBotError('MAX event не сериализуется для дедупликации.') from exc
    if len(canonical.encode('utf-8')) > 262144:
        raise LimitsBotError('MAX event превышает допустимый размер 256 КиБ.')
    try:
        canonical_key = event_key(payload)
    except ValueError as exc:
        raise LimitsBotError('MAX event имеет неподдерживаемый update_type.') from exc
    # MAX Update does not promise an update_id.  An exact canonical payload
    # digest is the durable fallback for its repeated webhook delivery.
    event_id = event_id or ('sha256:' + canonical_key)
    try:
        with db.session.begin_nested():
            row = LimitMaxEvent(event_id=str(event_id), payload_json=_dump(payload), status='received')
            db.session.add(row)
            db.session.flush()
        db.session.commit()
        return row, True
    except IntegrityError:
        db.session.rollback()
        return LimitMaxEvent.query.filter_by(event_id=str(event_id)).one(), False


def _event_parts(payload):
    kind = str(payload.get('update_type') or '').lower()
    # MAX's conventional callback Update is top-level ``callback`` *and*
    # top-level ``message``.  Documentation also names ``message_callback``;
    # both spellings are accepted, but all group/message bindings come from
    # the attached message rather than callback-provided claims.
    callback = (payload.get('message_callback') or payload.get('callback')) \
        if kind == 'message_callback' else {}
    callback = callback if isinstance(callback, dict) else {}
    message = payload.get('message')
    message = message if isinstance(message, dict) else {}
    body = callback if kind == 'message_callback' else message
    sender = body.get('user') or payload.get('user') or body.get('sender') or payload.get('sender') or {}
    user_id = sender.get('user_id') or sender.get('id') or body.get('user_id') or payload.get('user_id')
    recipient = message.get('recipient') if isinstance(message.get('recipient'), dict) else {}
    chat = body.get('chat') or payload.get('chat') or {}
    claimed_chat_id = chat.get('chat_id') or chat.get('id') or body.get('chat_id') or payload.get('chat_id')
    recipient_chat_id = recipient.get('chat_id')
    if claimed_chat_id and recipient_chat_id and str(claimed_chat_id) != str(recipient_chat_id):
        raise LimitsBotError('MAX callback содержит конфликтующие идентификаторы группы.')
    chat_id = recipient_chat_id or claimed_chat_id or ''
    message_body = message.get('body') if isinstance(message.get('body'), dict) else {}
    callback_message = body.get('message') if isinstance(body.get('message'), dict) else {}
    callback_body = callback_message.get('body') if isinstance(callback_message.get('body'), dict) else {}
    message_id = (message_body.get('mid') or message.get('message_id') or
                  callback_body.get('mid') or callback_message.get('message_id') or
                  body.get('message_id') or body.get('message_mid') or '')
    data = body.get('payload') or body.get('callback_data') or ''
    callback_id = body.get('callback_id') or ''
    text = message.get('text') or message_body.get('text') or ''
    chat_type = recipient.get('chat_type') or recipient.get('type') or ''
    return (kind, str(user_id or ''), str(chat_id or ''), str(message_id or ''),
            str(data), str(callback_id or ''), str(text or ''), str(chat_type or ''))


def _current_dataset():
    configured = str(Setting.get('limits_bot_dataset_id', '') or '').strip()
    if not configured.isdigit() or int(configured) <= 0:
        raise LimitsBotError('Не настроен набор лимитов для MAX-бота.')
    dataset = db.session.get(LimitDataset, int(configured))
    if dataset is None:
        raise LimitsBotError('Настроенный набор лимитов для MAX-бота не найден.')
    return dataset


def _initials_valid(text):
    return bool(re.match(r'^[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]+\s+[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]\.$', text))


def _full_uin(dataset_id, text):
    text = (text or '').strip()
    return LimitObject.query.filter_by(dataset_id=dataset_id, uin=text).first()


def _process_text(user_id, text, chat_type=''):
    """Mutate registration/subscriptions and queue exactly one text response."""
    if str(chat_type).lower() in ('chat', 'group', 'channel'):
        if not str(user_id or '').strip():
            raise LimitsBotError('В групповом сообщении MAX отсутствует пользователь.')
        _queue_message(user_id, 'Регистрация и ввод контактных данных доступны только в личном диалоге с ботом.')
        return
    dataset = _current_dataset()
    if dataset is None:
        raise LimitsBotError('Нет доступного набора лимитов для регистрации.')
    recipient = _recipient(user_id)
    dialog = _dialog(recipient, dataset.id)
    values = _load(dialog.values_json, {})
    text = (text or '').strip()
    lower = text.lower()

    if text in ('/start', 'Начать'):
        dialog.dataset_id, dialog.step, dialog.values_json = dataset.id, 'consent', '{}'
        _queue_message(user_id, 'Для обработки ИНН, фамилии с инициалами и телефона '
                       'подтвердите согласие: отправьте «Согласен».')
        return
    if dialog.step == 'consent':
        if lower not in ('согласен', '/consent', 'да'):
            _queue_message(user_id, 'Для продолжения требуется явное согласие. Отправьте «Согласен».')
            return
        recipient.consent_granted_at = utcnow()
        dialog.step, dialog.values_json = 'inn', '{}'
        _queue_message(user_id, 'Согласие сохранено. Введите ИНН организации (10 или 12 цифр).')
        return

    if recipient.access_status == 'approved' and not recipient.stopped_at:
        if text in ('/mine', '/my', 'Мои объекты'):
            rows = LimitSubscription.query.filter_by(
                recipient_id=recipient.id, dataset_id=dataset.id, is_active=True).all()
            answer = ('Ваши объекты: ' + ', '.join(row.uin for row in rows)
                      if rows else 'У вас нет активных подписок.')
            _queue_message(user_id, answer)
            return
        if lower.startswith('/unsubscribe ') or text.startswith('Отписаться '):
            uin = text.split(' ', 1)[1].strip()
            sub = LimitSubscription.query.filter_by(recipient_id=recipient.id, dataset_id=dataset.id,
                                                    uin=uin, is_active=True).first()
            if sub is None:
                _queue_message(user_id, 'Активная подписка на этот полный УИН не найдена.')
            else:
                sub.is_active, sub.revoked_at = False, utcnow()
                _queue_message(user_id, f'Подписка на {uin} отключена.')
            return
        if lower.startswith('/subscribe ') or text.startswith('Подписаться '):
            uin = text.split(' ', 1)[1].strip()
            if not _full_uin(dataset.id, uin):
                _queue_message(user_id, 'УИН не найден. Введите полный УИН из доступного набора.')
                return
            sub = LimitSubscription.query.filter_by(
                recipient_id=recipient.id, dataset_id=dataset.id, uin=uin).first()
            if sub is None:
                db.session.add(LimitSubscription(recipient_id=recipient.id, dataset_id=dataset.id, uin=uin))
            else:
                sub.is_active, sub.revoked_at = True, None
            _queue_message(user_id, f'Подписка на {uin} активна.')
            return
        _queue_message(user_id, 'Используйте /subscribe <полный УИН>, /mine или /unsubscribe <полный УИН>.')
        return

    if dialog.step == 'inn':
        if not valid_inn(text):
            _queue_message(user_id, 'ИНН не прошёл проверку формата и контрольных цифр. Повторите ввод.')
            return
        values['inn'], dialog.step, dialog.values_json = re.sub(r'\D', '', text), 'initials', _dump(values)
        _queue_message(user_id, 'Введите фамилию и инициалы, например: Иванов И.И.')
        return
    if dialog.step == 'initials':
        if not _initials_valid(text):
            _queue_message(user_id, 'Нужны фамилия и два инициала, например: Иванов И.И.')
            return
        values['initials'], dialog.step, dialog.values_json = text, 'phone', _dump(values)
        _queue_message(user_id, 'Введите телефон. Ручной номер не считается подтверждённым владением.')
        return
    if dialog.step == 'phone':
        if not 10 <= len(re.sub(r'\D', '', text)) <= 15:
            _queue_message(user_id, 'Введите телефон в международном или российском формате.')
            return
        values['phone'], dialog.step, dialog.values_json = text, 'uin', _dump(values)
        _queue_message(user_id, 'Введите полный УИН объекта.')
        return
    if dialog.step == 'uin':
        if not _full_uin(dataset.id, text):
            _queue_message(user_id, 'УИН не найден. Проверьте полный идентификатор и повторите ввод.')
            return
        recipient.organization_inn = values.get('inn', '')
        recipient.surname_initials = values.get('initials', '')
        recipient.phone = values.get('phone', '')
        request = LimitModerationRequest.query.filter_by(
            recipient_id=recipient.id, dataset_id=dataset.id, status='pending').first()
        if request is None:
            request = LimitModerationRequest(
                recipient_id=recipient.id, dataset_id=dataset.id, initial_uin=text,
                group_chat_id='',
            )
            db.session.add(request)
            db.session.flush()
        dialog.step = 'pending'
        _queue_message(user_id, 'Заявка передана на согласование администратору.')
        return request
    _queue_message(user_id, 'Заявка ожидает решения. Для новой регистрации отправьте /start.')


def _moderation_keyboard(request_id):
    return [[
        {'type': 'callback', 'text': 'Одобрить', 'payload': f'limits:approve:{request_id}'},
        {'type': 'callback', 'text': 'Отказать', 'payload': f'limits:reject:{request_id}'},
    ]]


def _request_from_outbound(outbound):
    request_id = outbound.moderation_request_id
    if request_id:
        return db.session.get(LimitModerationRequest, request_id)
    for row in _load(outbound.keyboard_json, []):
        for item in row if isinstance(row, list) else []:
            token = str(item.get('payload', '') if isinstance(item, dict) else '')
            match = re.fullmatch(r'limits:(?:approve|reject):(\d+)', token)
            if match:
                return db.session.get(LimitModerationRequest, int(match.group(1)))
    return None


def _queue_moderation_card(request, group_chat_id):
    if not group_chat_id:
        raise LimitsBotError('Не настроена группа MAX для модерации заявок.')
    request.group_chat_id = str(group_chat_id)
    recipient = db.session.get(LimitRecipient, request.recipient_id)
    return _queue_message(
        group_chat_id,
        f'Заявка #{request.id}: ИНН {recipient.organization_inn}; '
        f'{recipient.surname_initials}; УИН {request.initial_uin}.',
        _moderation_keyboard(request.id), moderation_request=request,
    )


def _process_callback(user_id, chat_id, message_id, data, callback_id, configured_group, is_human_admin):
    match = re.fullmatch(r'limits:(approve|reject):(\d+)', data or '')
    if not match:
        raise LimitsBotError('Неизвестная callback-команда.')
    if not configured_group or str(chat_id) != str(configured_group):
        raise LimitsBotError('Callback получен не из настроенной группы модерации.')
    # This result comes from MaxClient.is_human_admin(), which refreshes the
    # documented /chats/{chatId}/members/admins endpoint for this exact chat.
    if not is_human_admin:
        raise LimitsBotError('Пользователь не является текущим администратором этой группы MAX.')
    request = db.session.query(LimitModerationRequest).filter_by(id=int(match.group(2))).with_for_update().first()
    if request is None or request.status != 'pending':
        raise LimitsBotError('Заявка уже обработана или не существует.')
    if str(request.group_chat_id) != str(chat_id) or str(request.moderation_message_id) != str(message_id):
        raise LimitsBotError('Кнопка не связана с актуальным сообщением заявки.')
    _apply_moderation_decision(request, match.group(1), str(user_id), '')
    return callback_id


def _apply_moderation_decision(request, decision, actor, reason):
    """One DB-only decision transition shared by MAX and website moderation."""
    decision = {'approved': 'approve', 'rejected': 'reject'}.get(decision, decision)
    if decision not in ('approve', 'reject'):
        raise LimitsBotError('Некорректное решение модерации.')
    recipient = db.session.get(LimitRecipient, request.recipient_id)
    request.status = 'approved' if decision == 'approve' else 'rejected'
    request.decided_by_max_user_id, request.decided_at = str(actor), utcnow()
    request.decision_reason = (reason or '').strip()
    if decision == 'approve':
        recipient.access_status, recipient.approved_at, recipient.stopped_at = 'approved', utcnow(), None
        sub = LimitSubscription.query.filter_by(
            recipient_id=recipient.id, dataset_id=request.dataset_id, uin=request.initial_uin).first()
        if sub is None:
            db.session.add(LimitSubscription(recipient_id=recipient.id, dataset_id=request.dataset_id,
                                              uin=request.initial_uin))
        else:
            sub.is_active, sub.revoked_at = True, None
        _queue_message(recipient.max_user_id, 'Заявка одобрена. Первоначальная подписка активна.')
    else:
        recipient.access_status = 'rejected'
        _queue_message(recipient.max_user_id, 'Заявка отклонена администратором.')
    return recipient


def decide_web_moderation(request_id, decision, actor_id, reason=''):
    """Website-admin decision; it never treats a web account as MAX group admin."""
    try:
        request = (db.session.query(LimitModerationRequest).filter_by(id=int(request_id))
                   .with_for_update().first())
        if request is None or request.status != 'pending':
            raise LimitsBotError('Заявка уже обработана или не существует.')
        recipient = _apply_moderation_decision(request, decision, f'web:{actor_id}', reason)
        db.session.commit()
        return recipient
    except Exception:
        db.session.rollback()
        raise


def _claim_event():
    row = (LimitMaxEvent.query.filter(
        LimitMaxEvent.status.in_(('received', 'retry')),
        LimitMaxEvent.next_attempt_at <= utcnow())
           .order_by(LimitMaxEvent.id).with_for_update().first())
    if row:
        row.status, row.attempts = 'processing', row.attempts + 1
        row.processing_started_at = utcnow()
    return row


def _process_event_once(adapter, group_chat_id=None):
    """Claim and process one event.  DB updates plus outbox are one commit."""
    event = _claim_event()
    if event is None:
        db.session.commit()
        return False
    event_id, payload = event.event_id, _load(event.payload_json, {})
    db.session.commit()  # short claim transaction; no external work under DB lock
    callback_id = ''
    try:
        kind, user_id, chat_id, message_id, data, callback_id, text, chat_type = _event_parts(payload)
        configured_group = group_chat_id
        if configured_group is None:
            configured_group = Setting.get('limits_max_group_chat_id', '')
        # Reading the setting uses SQLAlchemy autobegin; close that read
        # transaction before the external admin check and explicit write unit.
        db.session.commit()
        # Never keep a database transaction open across a MAX API call.  The
        # result is still current at the point immediately preceding the
        # serialized, one-time decision below.
        is_human_admin = (adapter.is_human_admin(str(configured_group), str(user_id))
                          if kind == 'message_callback' else False)
        with db.session.begin():
            event = db.session.query(LimitMaxEvent).filter_by(event_id=event_id).with_for_update().one()
            if kind == 'bot_stopped':
                recipient = _recipient(user_id)
                recipient.stopped_at = utcnow()
                _cancel_pending_for_recipient(
                    recipient, 'Пользователь остановил бота в MAX.',
                    revoke_subscriptions=False, cancel_moderation=False)
            elif kind == 'bot_started':
                recipient = _recipient(user_id)
                recipient.stopped_at = None
                _queue_message(user_id, 'Бот снова активен. Для новой подписки отправьте /start.')
            elif kind == 'message_created':
                created = _process_text(user_id, text, chat_type)
                if isinstance(created, LimitModerationRequest):
                    _queue_moderation_card(created, configured_group)
            elif kind == 'message_callback':
                callback_id = _process_callback(user_id, chat_id, message_id, data, callback_id,
                                                configured_group, is_human_admin)
            event.status, event.processed_at = 'processed', utcnow()
        if callback_id and adapter.is_configured:
            try:
                # ``message`` is the documented callback-answer object.  Do
                # not rely on an unconfirmed provider notification field.
                adapter.answer_callback(callback_id, message={'text': 'Решение принято.'})
            except Exception:
                # The decision is already durable.  MAX will show no promise
                # of a second decision; its callback acknowledgement is UI-only.
                pass
        return True
    except Exception as exc:
        db.session.rollback()
        with db.session.begin():
            event = db.session.query(LimitMaxEvent).filter_by(event_id=event_id).with_for_update().one()
            _event_error(event, exc, retry=isinstance(exc, MaxAPIError) and exc.retryable)
        return True


def _recover_sending():
    """Never resend a request whose provider outcome was lost on restart."""
    changed = 0
    # No event transition/outbox write happens before an event is committed as
    # processed, so a process dying after the claim can safely retry it.
    for event in LimitMaxEvent.query.filter_by(status='processing').with_for_update().all():
        _event_error(event, 'Процесс перезапущен во время обработки события MAX.', retry=True)
        changed += 1
    for model in (LimitBotOutbound, LimitDelivery):
        changed += model.query.filter_by(status='sending').update(
            {'status': 'unknown', 'last_error': 'Процесс перезапущен во время отправки.'},
            synchronize_session=False)
    changed += LimitDeliveryPart.query.filter_by(status='sending').update(
            {'status': 'unknown', 'last_error': 'Процесс перезапущен во время отправки части.'},
            synchronize_session=False)
    affected = {row.delivery_id for row in LimitDeliveryPart.query.filter_by(status='unknown').all()}
    for delivery_id in affected:
        delivery = db.session.get(LimitDelivery, delivery_id)
        if delivery:
            _sync_delivery_from_parts(
                delivery, LimitDeliveryPart.query.filter_by(delivery_id=delivery_id)
                .order_by(LimitDeliveryPart.part_no).all())
    db.session.commit()
    return changed


def recover():
    """Recovery entry point to call after database restore and before workers."""
    return _recover_sending()


def retry_event(event_id, by_pk=False):
    """Explicitly requeue an errored provider update after operator review."""
    try:
        query = db.session.query(LimitMaxEvent)
        query = query.filter_by(id=int(event_id)) if by_pk else query.filter_by(event_id=str(event_id))
        event = query.with_for_update().first()
        if event is None:
            raise LimitsBotError('Событие MAX не найдено.')
        if event.status not in ('error', 'retry'):
            raise LimitsBotError('Повтор доступен только для ошибочного события MAX.')
        event.status, event.attempts, event.next_attempt_at, event.last_error = (
            'received', 0, utcnow(), '')
        event.processed_at, event.processing_started_at = None, None
        db.session.commit()
        return event
    except Exception:
        db.session.rollback()
        raise


def _cancel_delivery(delivery_id, reason='Отменено администратором.'):
    """Explicit operator cancellation; unknown outcomes are never retried here."""
    try:
        row = db.session.query(LimitDelivery).filter_by(id=int(delivery_id)).with_for_update().first()
        if row is None:
            raise LimitsBotError('Задание рассылки не найдено.')
        if row.status in ('accepted', 'cancelled'):
            raise LimitsBotError('Завершённое задание нельзя отменить повторно.')
        row.status, row.last_error = 'cancelled', str(reason or 'Отменено администратором.')
        LimitDeliveryPart.query.filter_by(delivery_id=row.id).filter(
                LimitDeliveryPart.status.in_(('queued', 'retry', 'error', 'unknown'))).update(
                    {'status': 'cancelled', 'last_error': row.last_error},
                    synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return row


def cancel_delivery(delivery_id, reason='Отменено администратором.'):
    with _dispatch_lock:
        return _cancel_delivery(delivery_id, reason)


def _cancel_bot_outbound(outbound_id, reason='Отменено администратором.'):
    """Explicit cancellation counterpart for durable bot replies/cards."""
    try:
        row = db.session.query(LimitBotOutbound).filter_by(id=int(outbound_id)).with_for_update().first()
        if row is None:
            raise LimitsBotError('Исходящее сообщение бота не найдено.')
        if row.status in ('accepted', 'cancelled'):
            raise LimitsBotError('Завершённое сообщение нельзя отменить повторно.')
        row.status, row.last_error = 'cancelled', str(reason or 'Отменено администратором.')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return row


def cancel_bot_outbound(outbound_id, reason='Отменено администратором.'):
    with _dispatch_lock:
        return _cancel_bot_outbound(outbound_id, reason)


def _retry_bot_outbound(outbound_id, confirm_unknown=False):
    """Explicitly retry a bot text/card, invalidating its old card binding."""
    try:
        row = (db.session.query(LimitBotOutbound).filter_by(id=int(outbound_id))
               .with_for_update().first())
        if row is None:
            raise LimitsBotError('Исходящее сообщение бота не найдено.')
        if row.status == 'unknown' and not confirm_unknown:
            raise LimitsBotError(
                'Исход предыдущей отправки неизвестен; нужен явный подтверждённый повтор.')
        if row.status not in ('error', 'retry', 'unknown'):
            raise LimitsBotError('Повтор доступен только для ошибочного сообщения бота.')
        if row.moderation_request_id:
            request = db.session.query(LimitModerationRequest).filter_by(
                id=row.moderation_request_id).with_for_update().first()
            if request and request.status == 'pending':
                # A callback on an older accepted/unknown card must fail exact
                # message binding after the operator has chosen re-send.
                request.moderation_message_id = ''
        row.status, row.attempts, row.next_attempt_at, row.last_error = 'queued', 0, utcnow(), ''
        row.provider_message_id, row.accepted_at = '', None
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def retry_bot_outbound(outbound_id, confirm_unknown=False):
    with _dispatch_lock:
        return _retry_bot_outbound(outbound_id, confirm_unknown)


def _retry_delivery(delivery_id, confirm_unknown=False):
    """Explicitly requeue a known failed alert.

    ``unknown`` is intentionally excluded unless an operator supplies an
    affirmative confirmation: the previous request may already have reached
    MAX, so an ordinary retry must never silently duplicate it.
    """
    try:
        row = db.session.query(LimitDelivery).filter_by(id=int(delivery_id)).with_for_update().first()
        if row is None:
            raise LimitsBotError('Задание рассылки не найдено.')
        if row.status == 'unknown' and not confirm_unknown:
            raise LimitsBotError(
                'Исход предыдущей отправки неизвестен. Нужна отдельная явная '
                'подтверждённая операция, иначе повтор запрещён.')
        if row.status not in ('error', 'retry', 'unknown'):
            raise LimitsBotError('Повтор доступен только для ошибочного задания.')
        parts = LimitDeliveryPart.query.filter_by(delivery_id=row.id).order_by(
            LimitDeliveryPart.part_no).all()
        if not parts:
            # A legacy parent has never been sent: first worker claim will
            # materialise its durable chunks.
            _ensure_delivery_parts(row)
            parts = LimitDeliveryPart.query.filter_by(delivery_id=row.id).order_by(
                LimitDeliveryPart.part_no).all()
        if any(part.status == 'unknown' for part in parts) and not confirm_unknown:
            raise LimitsBotError('Есть часть с неизвестным исходом; повтор требует явного подтверждения.')
        for part in parts:
            if part.status in ('error', 'retry') or (part.status == 'unknown' and confirm_unknown):
                part.status, part.next_attempt_at, part.last_error = 'queued', utcnow(), ''
        row.status, row.next_attempt_at, row.last_error = 'queued', utcnow(), ''
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def retry_delivery(delivery_id, confirm_unknown=False):
    with _dispatch_lock:
        return _retry_delivery(delivery_id, confirm_unknown)


def _revoke_access(recipient_id, actor_id, reason='Отозвано администратором.'):
    """Atomically stop a recipient and audit the web-admin access revocation.

    The audit deliberately contains no phone/INN.  Existing queued bot texts,
    subscriptions and alerts are cancelled in the same transaction; neither a
    restored queue nor a delayed worker can send after this transition.
    """
    try:
        recipient = (db.session.query(LimitRecipient).filter_by(id=int(recipient_id))
                     .with_for_update().first())
        if recipient is None:
            raise LimitsBotError('Получатель MAX не найден.')
        recipient.access_status, recipient.stopped_at = 'revoked', utcnow()
        _cancel_pending_for_recipient(recipient, 'Доступ отозван администратором.')
        db.session.add(AuditLog(
            user_id=int(actor_id) if actor_id is not None else None,
            action='limits_recipient_access_revoked',
            details=_dump({'recipient_id': recipient.id, 'reason': str(reason or '')[:500]}),
        ))
        db.session.commit()
        return recipient
    except Exception:
        db.session.rollback()
        raise


def revoke_access(recipient_id, actor_id, reason='Отозвано администратором.'):
    with _dispatch_lock:
        return _revoke_access(recipient_id, actor_id, reason)


def _claim_outbound():
    row = (LimitBotOutbound.query.filter(
        LimitBotOutbound.status.in_(('queued', 'retry')),
        LimitBotOutbound.next_attempt_at <= utcnow(),
    ).order_by(LimitBotOutbound.id).with_for_update().first())
    if row:
        row.status, row.attempts = 'sending', row.attempts + 1
    return row


def _process_bot_outbound_once(adapter):
    if adapter is None or not adapter.is_configured:
        return 0
    row = _claim_outbound()
    if row is None:
        db.session.commit()
        return 0
    recipient = _recipient(row.target_id, create=False)
    if recipient is not None and recipient.stopped_at:
        row.status, row.last_error = 'cancelled', 'Пользователь остановил бота в MAX.'
        db.session.commit()
        return 0
    row_id, target, content, keyboard = row.id, row.target_id, row.content, _load(row.keyboard_json, [])
    moderation_request = _request_from_outbound(row)
    db.session.commit()
    try:
        # MaxClient has explicit recipient fields.  Moderation cards are the
        # only group-targeted outbox entries; all replies target one MAX user.
        if moderation_request is not None:
            response = adapter.send_message(chat_id=target, text=content,
                                            attachments=_keyboard_attachments(keyboard))
        else:
            response = adapter.send_message(user_id=target, text=content,
                                            attachments=_keyboard_attachments(keyboard))
    except Exception as exc:
        with db.session.begin():
            row = db.session.query(LimitBotOutbound).filter_by(id=row_id).with_for_update().one()
            if _is_unknown_exception(exc):
                row.status, row.last_error = 'unknown', f'Неопределённый исход MAX: {exc}'
            else:
                row.last_error = str(exc)
                row.status = ('error' if row.attempts >= MAX_ATTEMPTS
                              or not _is_retryable_exception(exc) else 'retry')
                row.next_attempt_at = utcnow() + _retry_delay(row.attempts, _retry_after(exc))
        return 0
    with db.session.begin():
        row = db.session.query(LimitBotOutbound).filter_by(id=row_id).with_for_update().one()
        row.status = 'accepted'
        row.accepted_at = utcnow()
        row.provider_message_id = _message_id(response)
        request = _request_from_outbound(row)
        if request is not None:
            provider_id = _message_id(response)
            if provider_id:
                request.moderation_message_id = provider_id
            else:
                row.last_error = 'MAX принял карточку, но не вернул message_id; callback будет отклонён.'
    return 1


def process_event_once(adapter, group_chat_id=None):
    from limits_connection import _serialized_connection, registration_active
    with _dispatch_lock, _serialized_connection() as acquired:
        if not acquired or not registration_active():
            return False
        return _process_event_once(adapter, group_chat_id)


def process_bot_outbound_once(adapter):
    from limits_connection import _serialized_connection, registration_active
    with _dispatch_lock, _serialized_connection() as acquired:
        if not acquired or not registration_active():
            return False
        return _process_bot_outbound_once(adapter)


def _recipient_can_receive(recipient, delivery):
    if (recipient is None or recipient.access_status != 'approved' or recipient.stopped_at
            or not recipient.consent_granted_at):
        return False
    return LimitSubscription.query.filter_by(
        recipient_id=recipient.id, dataset_id=delivery.object.dataset_id,
        uin=delivery.object.uin, is_active=True).first() is not None


def _split_alert_content(content, maximum=4000):
    """Lossless split, preferring line/word boundaries and never truncating."""
    content = str(content or '')
    if not content:
        raise LimitsBotError('Текст уведомления пуст.')
    parts = []
    remaining = content
    while len(remaining) > maximum:
        cut = max(remaining.rfind('\n', 0, maximum + 1),
                  remaining.rfind(' ', 0, maximum + 1))
        # Include a natural separator in the preceding part, preserving the
        # exact source text.  A single overlong word uses a hard boundary.
        if cut > 0:
            cut += 1
        else:
            cut = maximum
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        parts.append(remaining)
    return parts


def _ensure_delivery_parts(delivery):
    """Materialise durable parts inside the delivery claim transaction."""
    rows = LimitDeliveryPart.query.filter_by(delivery_id=delivery.id).order_by(
        LimitDeliveryPart.part_no).all()
    if rows:
        return rows
    for number, content in enumerate(_split_alert_content(delivery.content), 1):
        db.session.add(LimitDeliveryPart(delivery_id=delivery.id, part_no=number, content=content,
                                         status='queued', attempts=0, next_attempt_at=utcnow()))
    db.session.flush()
    return LimitDeliveryPart.query.filter_by(delivery_id=delivery.id).order_by(
        LimitDeliveryPart.part_no).all()


def _sync_delivery_from_parts(delivery, parts):
    """Derive the parent journal state; only all accepted means accepted."""
    if not parts:
        return
    unknown = next((part for part in parts if part.status == 'unknown'), None)
    failed = next((part for part in parts if part.status == 'error'), None)
    if unknown:
        delivery.status, delivery.last_error = 'unknown', (
            f'Часть {unknown.part_no}: {unknown.last_error}')
    elif failed:
        delivery.status, delivery.last_error = 'error', f'Часть {failed.part_no}: {failed.last_error}'
    elif all(part.status == 'accepted' for part in parts):
        delivery.status, delivery.accepted_at, delivery.last_error = 'accepted', utcnow(), ''
    elif all(part.status == 'cancelled' for part in parts):
        delivery.status, delivery.last_error = 'cancelled', 'Все части уведомления отменены.'
    else:
        delivery.status = 'queued'
        due = [part.next_attempt_at.replace(tzinfo=timezone.utc)
               if part.next_attempt_at.tzinfo is None else part.next_attempt_at
               for part in parts if part.status in ('queued', 'retry')]
        if due:
            delivery.next_attempt_at = min(due)


def _claim_delivery():
    row = (LimitDelivery.query.filter(
        LimitDelivery.status.in_(('queued', 'retry')),
        LimitDelivery.next_attempt_at <= utcnow(),
    ).order_by(LimitDelivery.id).with_for_update().first())
    if row:
        recipient = db.session.get(LimitRecipient, row.recipient_id)
        if not _recipient_can_receive(recipient, row):
            row.status, row.last_error = 'cancelled', 'Согласие, доступ или подписка отозваны до отправки.'
            return _STOP
        try:
            parts = _ensure_delivery_parts(row)
        except LimitsBotError as exc:
            row.status, row.last_error = 'error', str(exc)
            return _STOP
        _sync_delivery_from_parts(row, parts)
        if row.status in ('unknown', 'error', 'accepted', 'cancelled'):
            return _STOP
        part = next((item for item in parts
                     if item.status in ('queued', 'retry') and
                     item.next_attempt_at.replace(tzinfo=timezone.utc).timestamp()
                     <= utcnow().replace(tzinfo=timezone.utc).timestamp()), None)
        if part is None:
            return _STOP
        part.status, part.attempts = 'sending', part.attempts + 1
        row.attempts += 1
        return row, part
    return None


def _process_alert_once(adapter):
    if adapter is None or not adapter.is_configured:
        return 0
    claimed = _claim_delivery()
    if claimed is _STOP:
        db.session.commit()
        return 0
    if claimed is None:
        db.session.commit()
        return 0
    row, part = claimed
    row_id, part_id, user_id, content = row.id, part.id, row.recipient.max_user_id, part.content
    db.session.commit()
    try:
        response = adapter.send_message(user_id=user_id, text=content)
    except Exception as exc:
        with db.session.begin():
            row = db.session.query(LimitDelivery).filter_by(id=row_id).with_for_update().one()
            part = db.session.query(LimitDeliveryPart).filter_by(id=part_id).with_for_update().one()
            if _is_unknown_exception(exc):
                status, error = 'unknown', f'Неопределённый исход MAX: {exc}'
            else:
                status, error = (
                    'error' if part.attempts >= MAX_ATTEMPTS or not _is_retryable_exception(exc)
                    else 'retry'), str(exc)
                part.next_attempt_at = utcnow() + _retry_delay(part.attempts, _retry_after(exc))
            part.status, part.last_error = status, error
            _sync_delivery_from_parts(
                row, LimitDeliveryPart.query.filter_by(delivery_id=row.id)
                .order_by(LimitDeliveryPart.part_no).all())
            db.session.add(LimitDeliveryAttempt(
                delivery_id=row.id, attempt_no=row.attempts, status=status,
                error_text=f'Часть {part.part_no}: {error}'))
        return 0
    with db.session.begin():
        row = db.session.query(LimitDelivery).filter_by(id=row_id).with_for_update().one()
        part = db.session.query(LimitDeliveryPart).filter_by(id=part_id).with_for_update().one()
        part.status, part.accepted_at, part.last_error = 'accepted', utcnow(), ''
        part.provider_message_id = _message_id(response)
        _sync_delivery_from_parts(
            row, LimitDeliveryPart.query.filter_by(delivery_id=row.id)
            .order_by(LimitDeliveryPart.part_no).all())
        db.session.add(LimitDeliveryAttempt(
            delivery_id=row.id, attempt_no=row.attempts, status='accepted',
            response_ref=f'part:{part.part_no}:{_message_id(response)}'))
    return 1


def process_alert_once(adapter):
    from limits_connection import _serialized_connection, registration_active
    with _dispatch_lock, _serialized_connection() as acquired:
        if not acquired or not registration_active():
            return False
        return _process_alert_once(adapter)


def run_worker_once(adapter, group_chat_id=None, limit=DEFAULT_BATCH_SIZE):
    """Run a bounded, transport-safe worker pass; it never creates an adapter."""
    from limits_connection import registration_active
    if not registration_active() or adapter is None or not adapter.is_configured:
        return {'events': 0, 'bot_messages': 0, 'alerts': 0, 'configured': False}
    result = {'events': 0, 'bot_messages': 0, 'alerts': 0, 'configured': True}
    for _ in range(max(1, int(limit))):
        if not process_event_once(adapter, group_chat_id):
            break
        result['events'] += 1
    for _ in range(max(1, int(limit))):
        if not process_bot_outbound_once(adapter):
            break
        result['bot_messages'] += 1
    for _ in range(max(1, int(limit))):
        if not process_alert_once(adapter):
            break
        result['alerts'] += 1
    return result


def init_worker(app, adapter_factory, ready_check=None, interval_seconds=2):
    """Register explicit worker configuration; does not contact MAX or start it."""
    state = {
        'adapter_factory': adapter_factory, 'ready_check': ready_check,
        'interval_seconds': max(1, int(interval_seconds)), 'thread': None,
        'stop': threading.Event(),
    }
    app.extensions['limits_bot_worker'] = state
    return state


def start_worker(app, adapter_factory=None, ready_check=None, interval_seconds=2):
    """Start the opt-in worker only after restore readiness has been confirmed."""
    state = app.extensions.get('limits_bot_worker')
    if state is None:
        if adapter_factory is None:
            raise LimitsBotError('Нужна явная фабрика адаптера MAX.')
        state = init_worker(app, adapter_factory, ready_check, interval_seconds)
    if state['thread'] and state['thread'].is_alive():
        return state

    def work():
        with app.app_context():
            while not state['stop'].wait(state['interval_seconds']):
                # Recovery readiness can change while a restore/reconciliation
                # is in progress.  Do not dispatch before *every* pass.
                if state['ready_check'] is not None and not state['ready_check']():
                    continue
                try:
                    from limits_connection import registration_active
                    if not registration_active():
                        continue
                    # The first ready pass also converts abandoned ``sending``
                    # rows to visible ``unknown`` before any possible send.
                    if not state.get('recovered'):
                        recover()
                        state['recovered'] = True
                    adapter = state['adapter_factory']()
                    run_worker_once(adapter)
                except Exception:
                    db.session.rollback()
                    app.logger.exception('limits MAX worker pass failed')

    state['thread'] = threading.Thread(target=work, name='limits-max-worker', daemon=True)
    state['thread'].start()
    return state