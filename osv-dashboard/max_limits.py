"""MAX bot for current accepted limits data, independent of Telegram state."""
import json
import hashlib
import hmac
import logging
import os
import re
import ssl
import threading
import time
import contextlib
import fcntl
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from sqlalchemy import inspect, text
from sqlalchemy.orm import aliased

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitObject, LimitVersion,
    MaxLimitsCursor, MaxLimitsOutbox, MaxLimitsSubscription, MaxLimitsUin,
    Setting, db, utcnow,
)
from max_dataset_scope import (selected_dataset_id, selected_version, selected_version_id,
                               snapshot_objects, selected_history_ids, serialized)
import max_bot_config as bot_config
from limits_version_display import display_number, version_numbers

API_BASE = 'https://platform-api2.max.ru'
TRUSTED_CA = Path(__file__).parent / 'certs' / 'russian-trusted-root-ca.pem'
MAX_TEXT = 3800
_logger = logging.getLogger(__name__)
_worker_lock = threading.Lock()
_worker_thread = None
_worker_token = None
_worker_generation = None
_connected_token = None
_connected_generation = None
_last_error = False


def status():
    """A saved username is not proof of a live connection."""
    config = bot_config.read()
    if not config['token'] or not config['group_id']:
        return 'unconfigured'
    if not config.get('enabled'):
        return 'stopped'
    if (_connected_token == config['token']
            and _connected_generation == config.get('generation')
            and _worker_thread and _worker_thread.is_alive()):
        return 'connected'
    if _last_error:
        return 'error'
    return 'connecting'


def valid_webhook_url(url):
    try:
        parts = urlsplit(url)
        prefix = os.environ.get('SCRIPT_NAME', '').rstrip('/')
        if prefix and (not prefix.startswith('/') or '//' in prefix
                       or any(segment in ('.', '..') for segment in prefix.split('/'))):
            return False
        return bool(parts.scheme == 'https' and parts.hostname
                    and parts.port in (None, 443) and not parts.username
                    and not parts.password and not parts.query and not parts.fragment
                    and parts.path == prefix + '/limit-notifications/max/webhook')
    except ValueError:
        return False


def requires_webhook():
    return any(os.environ.get(name, '').strip().lower() in ('1', 'true')
               for name in ('REPLIT_DEPLOYMENT', 'MAX_LIMITS_REQUIRE_WEBHOOK'))


def _assert_active(token, generation=None):
    config = bot_config._read()
    if (not config.get('enabled') or config.get('token') != token
            or (generation is not None and config.get('generation') != generation)):
        raise RuntimeError('MAX bot is stopped')


@contextlib.contextmanager
def active_delivery(token, generation=None):
    with bot_config.locked():
        _assert_active(token, generation)
        yield


def _verify_group(token, chat_id):
    """Confirm the configured group exists and this bot can moderate it."""
    identity = max_api(token, 'GET', '/me', timeout=15)
    username = identity.get('username') or ''
    bot_id = identity.get('user_id')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,100}', username) or type(bot_id) is not int:
        raise RuntimeError('MAX identity unavailable')
    chat = max_api(token, 'GET', f'/chats/{chat_id}', timeout=15)
    if str(chat.get('chat_id')) != str(chat_id):
        raise RuntimeError('MAX group unavailable')
    admins = max_api(token, 'GET', f'/chats/{chat_id}/members/admins', timeout=15)
    if not any(isinstance(member, dict) and member.get('user_id') == bot_id
               for member in (admins.get('members') or [])):
        raise RuntimeError('MAX bot is not a group administrator')
    return username


def migrate_max_limits_schema():
    """Retire Telegram-only state without affecting financial tables."""
    for table in ('telegram_limits_outbox', 'telegram_limits_cursors',
                  'telegram_limits_subscriptions'):
        if table in inspect(db.engine).get_table_names():
            with db.engine.begin() as connection:
                connection.execute(text(f'DROP TABLE "{table}"'))
    for key in ('telegram_limits_offset', 'telegram_limits_username'):
        row = db.session.get(Setting, key)
        if row is not None:
            db.session.delete(row)
    db.session.commit()


def _clean(value, limit=500):
    value = str(value if value is not None else '').replace('\x00', '')
    return ' '.join(value.split())[:limit]


def _value(encoded):
    try:
        value = json.loads(encoded or '{}')
    except (TypeError, ValueError):
        return '—'
    if isinstance(value, dict):
        return _clean(value.get('value', value.get('raw', value.get('kind', '—'))), 180) or '—'
    return _clean(value, 180) or '—'


def _accepted_objects(uin):
    version = selected_version()
    obj = snapshot_objects(version).get(uin)
    return [(obj, version.dataset, version)] if obj else []


def _cancel_outbox(user_id):
    MaxLimitsOutbox.query.filter_by(
        user_id=user_id, delivered_at=None, cancelled_at=None).update(
            {'cancelled_at': utcnow()}, synchronize_session=False)


def current_information(uin):
    from max_funding_year import visible_column
    records = _accepted_objects(uin)
    if not records:
        return 'Для этого УИН нет объекта в текущих принятых срезах.'
    lines = [f'УИН {uin}: текущая принятая информация']
    numbers = version_numbers()
    truncated = False
    for obj, dataset, version in records:
        block = [
            f'Файл #{display_number(version, numbers)}: {_clean(version.source_name, 160)}',
            f'Срез: {_clean(version.source_date, 40)}',
            f'Объект: {_clean(obj.title, 500) or "—"}',
            f'Адрес: {_clean(obj.address, 500) or "—"}',
        ]
        try:
            snapshot = json.loads(version.snapshot_json or '{}')
        except (TypeError, ValueError):
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        columns = {
            str(column.get('key')): column
            for column in snapshot.get('columns', [])
            if isinstance(column, dict) and column.get('category') in (
                'annual_funding', 'funding_snapshot', 'limit_cost')
            and visible_column(column)
        }
        row_number = 0
        for row in snapshot.get('rows', []):
            if not isinstance(row, dict) or str(row.get('uin', '')).strip() != uin:
                continue
            row_number += 1
            cells = row.get('cells', {})
            if not isinstance(cells, dict):
                continue
            for key, column in columns.items():
                cell = cells.get(key)
                if not isinstance(cell, dict) or cell.get('kind') != 'money':
                    continue
                value = cell.get('value')
                if value is None:
                    continue
                label = _clean(column.get('label') or key, 180)
                unit_match = re.search(
                    r'(тыс\.?\s*руб(?:\.|лей)?|руб(?:\.|лей)?)',
                    label, flags=re.IGNORECASE)
                unit = _clean(unit_match.group(1), 40) if unit_match else \
                    'единица не указана в заголовке'
                line = (
                    f'Строка {_clean(row.get("row") or row_number, 20)}: '
                    f'{label} ({unit}) — {_clean(value, 100)}')
                if sum(map(len, lines)) + sum(map(len, block)) + len(line) + 1 > MAX_TEXT - 20:
                    truncated = True
                    break
                block.append(line)
            if truncated:
                break
        lines.extend(block)
        if truncated:
            break
    if truncated and sum(map(len, lines)) + 5 < MAX_TEXT:
        lines.append('…')
    return '\n'.join(lines)


def accepted_changes(uin):
    from max_funding_year import visible_change
    old_version = aliased(LimitVersion)
    new_version = aliased(LimitVersion)
    rows = (db.session.query(LimitChange, LimitComparison, new_version, LimitDataset)
            .join(LimitComparison, LimitComparison.id == LimitChange.comparison_id)
            .join(old_version, old_version.id == LimitComparison.old_version_id)
            .join(new_version, new_version.id == LimitComparison.new_version_id)
            .join(LimitDataset, LimitDataset.id == new_version.dataset_id)
            .filter(LimitChange.uin == uin,
                    old_version.status == 'accepted',
                    new_version.status == 'accepted',
                    new_version.id.in_(selected_history_ids()),
                    old_version.id.in_(selected_history_ids()),
                    LimitComparison.status == 'ready',
                    old_version.dataset_id == new_version.dataset_id,
                    LimitDataset.id == selected_dataset_id())
            .order_by(new_version.accepted_at.desc(), new_version.id.desc(),
                      LimitChange.id.asc()).limit(100).all())
    rows = [row for row in rows if visible_change(row[0])]
    if not rows:
        return f'Для УИН {uin} нет сохранённых изменений принятых версий.'
    lines = [f'Изменения по УИН {uin}:']
    numbers = version_numbers()
    for change, _comparison, version, dataset in rows:
        lines.append(
            f'Файл #{display_number(version, numbers)} · {_clean(version.source_date, 40)}: '
            f'{_clean(change.field_label, 160) or _clean(change.field_key, 100)} — '
            f'{_value(change.old_json)} → {_value(change.new_json)}')
        if sum(map(len, lines)) > MAX_TEXT - 100:
            lines.append('…')
            break
    return '\n'.join(lines)[:MAX_TEXT]


@serialized
def queue_accepted_changes(version, comparison):
    """Queue matching recipients inside the caller's acceptance transaction."""
    from max_readable_changes import describe, date_label
    from max_funding_year import visible_change
    if (comparison is None or version.id != selected_version_id()
            or comparison.new_version_id != version.id or comparison.status != 'ready'):
        return 0
    changes = [change for change in LimitChange.query.filter_by(
        comparison_id=comparison.id).order_by(LimitChange.uin, LimitChange.id).all()
        if visible_change(change)]
    changed_uins = {change.uin for change in changes}
    if not changed_uins:
        return 0
    subscriptions = MaxLimitsUin.query.filter(
        MaxLimitsUin.status == 'approved',
        MaxLimitsUin.uin.in_(changed_uins)).all()
    grouped = {}
    for change in changes:
        grouped.setdefault(change.uin, []).append(change)
    for subscription in subscriptions:
        obj = snapshot_objects(version).get(subscription.uin)
        lines = [f'Что изменилось по объекту · {date_label(version.source_date)}',
                 _clean(obj.title, 250) if obj else f'УИН {subscription.uin}',
                 f'УИН {subscription.uin}']
        for change in grouped[subscription.uin]:
            description = describe(change)
            if len('\n\n'.join(lines + [description])) > MAX_TEXT - 100:
                lines.append('Все изменения доступны кнопкой «Изменения» в меню.')
                break
            lines.append(description)
        db.session.add(MaxLimitsOutbox(
            user_id=subscription.user_id, uin=subscription.uin,
            version_id=version.id, text='\n\n'.join(lines)[:MAX_TEXT]))
    return len(subscriptions)


def process_update(update, is_group_admin=None):
    """Return (destination, identifier, response) for authorized MAX events."""
    from max_registration import decide, group_id, handle_callback, handle_text, response
    if not isinstance(update, dict):
        return None
    kind = update.get('update_type')
    if kind in ('bot_stopped', 'dialog_removed'):
        user = update.get('user') or {}
        try:
            user_id = int(user.get('user_id'))
        except (TypeError, ValueError):
            return None
        _cancel_outbox(user_id)
        db.session.commit()
        return None
    if kind == 'bot_started':
        user = update.get('user') or {}
        try:
            user_id = int(user.get('user_id'))
        except (TypeError, ValueError):
            return None
        return 'user', user_id, handle_text(user_id, '/start')
    if kind == 'message_callback':
        callback = update.get('callback') or {}
        recipient = (update.get('message') or {}).get('recipient') or {}
        user = callback.get('user') or {}
        if user.get('is_bot') or not isinstance(callback.get('callback_id'), str):
            return None
        try:
            user_id = int(user.get('user_id'))
        except (TypeError, ValueError):
            return None
        payload = callback.get('payload')
        if not isinstance(payload, str) or len(payload) > 100:
            return None
        if recipient.get('chat_type') == 'chat':
            try:
                chat_id = int(recipient.get('chat_id'))
            except (TypeError, ValueError):
                return None
            if chat_id != group_id():
                return None
            if is_group_admin is None or not is_group_admin(chat_id, user_id):
                answer = response('Решение может принять только администратор группы.')
            else:
                answer = decide(chat_id, user_id, payload)
            answer.pop('buttons', None)
        elif recipient.get('chat_type') == 'dialog':
            try:
                if int(recipient.get('user_id') or user_id) != user_id:
                    return None
            except (TypeError, ValueError):
                return None
            answer = handle_callback(user_id, payload)
        else:
            return None
        return 'callback', callback['callback_id'], answer
    if kind != 'message_created':
        return None
    message = update.get('message') or {}
    recipient = message.get('recipient') or {}
    sender = message.get('sender') or {}
    if sender.get('is_bot'):
        return None
    raw_text = (message.get('body') or {}).get('text')
    if not isinstance(raw_text, str):
        return None
    if recipient.get('chat_type') == 'chat':
        if raw_text.strip().split('@')[0] == '/chatid':
            return 'chat', recipient.get('chat_id'), response(
                f'ID этой группы: {recipient.get("chat_id")}. '
                'Администратор может указать его на странице «Бот MAX» в Лимитах.')
        return None
    if recipient.get('chat_type') != 'dialog':
        return None
    try:
        user_id = int(sender.get('user_id'))
    except (TypeError, ValueError):
        return None
    if len(raw_text) > 1000:
        return 'user', user_id, response('Сообщение слишком длинное.')
    raw_text = raw_text.strip()
    command = raw_text.split(maxsplit=1)[0].lower() if raw_text else ''
    content = command if command.startswith('/') else raw_text
    return 'user', user_id, handle_text(user_id, content)


def max_api(token, method, path, params=None, payload=None, timeout=30):
    """MAX API transport; never log URLs, headers, tokens or response bodies."""
    url = API_BASE + path
    if params:
        url += '?' + urlencode(params)
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    request = Request(url, data=data, method=method, headers={
        'Authorization': token,
        'Content-Type': 'application/json',
    })
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(TRUSTED_CA))
    with urlopen(request, timeout=timeout, context=context) as response:
        result = json.loads(response.read(1024 * 1024).decode('utf-8'))
    if not isinstance(result, dict):
        raise RuntimeError('Invalid MAX API response')
    return result


def webhook_secret(token, session_secret):
    """Derive a separate MAX webhook proof without exposing either credential."""
    if not token or not session_secret:
        raise RuntimeError('MAX webhook requires a configured session secret')
    return hmac.new(session_secret.encode('utf-8'),
                    b'max-limits-webhook-v1:' + token.encode('utf-8'),
                    hashlib.sha256).hexdigest()


def _message_body(text, buttons=None, file_token=None):
    body = {'text': text[:MAX_TEXT]}
    attachments = []
    if file_token:
        attachments.append({'type': 'file', 'payload': {'token': file_token}})
    if buttons:
        attachments.append({'type': 'inline_keyboard', 'payload': {
            'buttons': [[{'type': 'callback', 'text': title, 'payload': payload}
                         for title, payload in row] for row in buttons]}})
    if attachments:
        body['attachments'] = attachments
    return body


def send_message(token, user_id, text, api_call=max_api, buttons=None,
                 chat=False, file_token=None):
    return api_call(token, 'POST', '/messages',
                    {'chat_id' if chat else 'user_id': user_id},
                    _message_body(text, buttons, file_token), timeout=15)


def group_admin_lookup(token, chat_id, user_id, api_call=max_api):
    """Authorize moderation against the current MAX admin roster, never callback metadata."""
    marker = None
    seen = set()
    for _ in range(50):
        params = {'marker': marker} if marker is not None else None
        page = api_call(token, 'GET', f'/chats/{chat_id}/members/admins',
                        params, timeout=15)
        members = page.get('members')
        if not isinstance(members, list):
            return False
        for member in members:
            if not isinstance(member, dict) or member.get('is_bot') is True:
                continue
            if (member.get('user_id') == user_id
                    and (member.get('is_admin') is True
                         or member.get('is_owner') is True)):
                return True
        marker = page.get('marker')
        if marker is None:
            return False
        if type(marker) is not int or marker in seen:
            return False
        seen.add(marker)
    return False


def dispatch_update(token, update, api_call=None, generation=None):
    with active_delivery(token, generation):
        return _dispatch_update_active(token, update, api_call)


def _dispatch_update_active(token, update, api_call=None):
    from max_registration import upload_catalog
    api_call = api_call or max_api
    _assert_active(token)
    result = process_update(
        update, is_group_admin=lambda chat_id, user_id: group_admin_lookup(
            token, chat_id, user_id, api_call=api_call))
    if not result:
        return
    destination, identifier, answer = result
    attachment = answer.get('file')
    if attachment == 'changes':
        from max_digest import changes_excel_bytes
        try:
            data = changes_excel_bytes(answer['file_user_id'])
        except ValueError as exc:
            # Approval or selected dataset may change between callback and upload.
            answer['text'] = str(exc)
            file_token = None
        else:
            file_token = upload_catalog(
                token, api_call, data=data, filename='limits_changes.xlsx')
    else:
        file_token = upload_catalog(token, api_call) if attachment else None
    if destination == 'callback':
        with active_delivery(token):
            result = api_call(token, 'POST', '/answers', {'callback_id': identifier},
                          {'message': _message_body(
                              answer['text'], answer.get('buttons'), file_token)},
                          timeout=15)
        if result.get('success') is not True:
            raise RuntimeError('MAX callback response failed')
    else:
        with active_delivery(token):
            send_message(token, identifier, answer['text'], api_call=api_call,
                         buttons=answer.get('buttons'), file_token=file_token,
                         chat=destination == 'chat')


@serialized
def drain_outbox(api_call=None, limit=20, on_state_change=None, generation=None):
    api_call = api_call or max_api
    config = bot_config.read()
    if (not config.get('enabled') or not config.get('token')
            or (generation is not None and config.get('generation') != generation)):
        return 0
    version_id = selected_version_id()
    pending = MaxLimitsOutbox.query.filter(
        MaxLimitsOutbox.delivered_at.is_(None),
        MaxLimitsOutbox.cancelled_at.is_(None))
    selected_versions = db.session.query(LimitVersion.id).filter(
        LimitVersion.id == version_id)
    cancelled = pending.filter(
        ~MaxLimitsOutbox.version_id.in_(selected_versions)).update(
            {'cancelled_at': utcnow()}, synchronize_session=False)
    if cancelled:
        db.session.commit()
        if on_state_change:
            on_state_change()
    items = (MaxLimitsOutbox.query.filter(
        MaxLimitsOutbox.delivered_at.is_(None),
        MaxLimitsOutbox.cancelled_at.is_(None),
        MaxLimitsOutbox.next_attempt_at <= utcnow())
        .order_by(MaxLimitsOutbox.id).limit(max(1, min(int(limit), 100))).all())
    sent = 0
    for item in items:
        version = db.session.get(LimitVersion, item.version_id)
        if (version is None or version.id != selected_version_id()):
            item.cancelled_at = utcnow()
            db.session.commit()
            if on_state_change:
                on_state_change()
            continue
        token = config['token']
        if not bot_config.active(token) or (generation is not None
                and bot_config.read().get('generation') != generation):
            break
        if item.uin.startswith('__manual_digest__:'):
            from max_digest import message_for_outbox
            message = message_for_outbox(item)
        else:
            subscription = MaxLimitsUin.query.filter_by(
                user_id=item.user_id, uin=item.uin, status='approved').first()
            # Pending messages may predate a stricter administrator setting.
            # Rebuild from verified changes rather than trusting queued text.
            comparison = LimitComparison.query.filter_by(
                new_version_id=version.id, old_version_id=version.previous_version_id,
                status='ready').first()
            visible = []
            if subscription is not None and comparison is not None:
                from max_funding_year import visible_change
                visible = [change for change in LimitChange.query.filter_by(
                    comparison_id=comparison.id, uin=item.uin).order_by(
                        LimitChange.id).all() if visible_change(change)]
            if visible:
                from max_readable_changes import describe, date_label
                obj = snapshot_objects(version).get(item.uin)
                lines = [f'Что изменилось по объекту · {date_label(version.source_date)}',
                         _clean(obj.title, 250) if obj else f'УИН {item.uin}',
                         f'УИН {item.uin}']
                for change in visible:
                    description = describe(change)
                    if len('\n\n'.join(lines + [description])) > MAX_TEXT - 100:
                        lines.append('Все изменения доступны кнопкой «Изменения» в меню.')
                        break
                    lines.append(description)
                message = '\n\n'.join(lines)[:MAX_TEXT]
            else:
                message = None
        if message is None:
            item.cancelled_at = utcnow()
            db.session.commit()
            if on_state_change:
                on_state_change()
            continue
        try:
            from max_registration import MENU
            with active_delivery(token, generation):
                send_message(token, item.user_id, message, api_call=api_call, buttons=MENU)
        except Exception:
            _logger.warning('MAX limits outbox send failed; retry scheduled')
            db.session.rollback()
            current = db.session.get(MaxLimitsOutbox, item.id)
            if current:
                current.attempts += 1
                delay = min(21600, 30 * (2 ** min(current.attempts - 1, 10)))
                current.next_attempt_at = utcnow() + timedelta(seconds=delay)
                db.session.commit()
                if on_state_change:
                    on_state_change()
            continue
        current = db.session.get(MaxLimitsOutbox, item.id)
        if current:
            current.delivered_at = utcnow()
            db.session.commit()
            sent += 1
            if on_state_change:
                on_state_change()
    return sent


def _worker_loop(application, token, generation):
    global _connected_token, _connected_generation, _last_error
    backoff = 2
    identified = False
    while True:
        if not _generation_active(token, generation):
            break
        try:
            with application.app_context():
                if not identified:
                    username = _verify_group(token, bot_config.read()['group_id'])
                changed = [False]
                try:
                    cursor = db.session.get(MaxLimitsCursor, 1)
                    params = {'timeout': 20, 'types': (
                        'bot_started,bot_stopped,dialog_removed,message_created,message_callback')}
                    if cursor and cursor.marker is not None:
                        params['marker'] = cursor.marker
                    batch = max_api(token, 'GET', '/updates', params, timeout=30)
                    updates = batch.get('updates', [])
                    marker = batch.get('marker')
                    if not isinstance(updates, list) or not isinstance(marker, (int, type(None))):
                        raise RuntimeError('Invalid MAX update batch')
                    if not identified:
                        _assert_active(token, generation)
                        _connected_token = token
                        _connected_generation = generation
                        _last_error = False
                        identified = True
                        _logger.warning('MAX limits bot polling connected as @%s', username)
                    for update in updates:
                        if not _generation_active(token, generation):
                            break
                        dispatch_update(token, update, generation=generation)
                        changed[0] = True
                    _assert_active(token, generation)
                    if marker is not None and (cursor is None or cursor.marker != marker):
                        if cursor is None:
                            cursor = MaxLimitsCursor(id=1, marker=marker)
                            db.session.add(cursor)
                        else:
                            cursor.marker = marker
                        db.session.commit()
                        changed[0] = True
                    drain_outbox(on_state_change=lambda: changed.__setitem__(0, True),
                                 generation=generation)
                    from max_registration import pending_moderation
                    if _generation_active(token, generation):
                        with active_delivery(token, generation):
                            pending_moderation(max_api, token)
                    backoff = 2
                finally:
                    if changed[0]:
                        from app import _schedule_durable_safety_backup
                        _schedule_durable_safety_backup('MAX limits polling batch')
        except Exception:
            _logger.warning('MAX limits polling failed; retrying with backoff')
            with application.app_context():
                db.session.rollback()
            identified = False
            if _connected_token == token and _connected_generation == generation:
                _connected_token = None
                _connected_generation = None
            if _generation_active(token, generation):
                _last_error = True
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
    if _connected_token == token and _connected_generation == generation:
        _connected_token = None
        _connected_generation = None


def _webhook_outbox_loop(application, token, url, generation):
    """Register production webhook and deliver queued changes independently."""
    if not valid_webhook_url(url):
        _logger.error('MAX webhook URL must be an HTTPS /limit-notifications/max/webhook endpoint')
        return
    global _connected_token, _connected_generation, _last_error
    backoff = 2
    connected = False
    while True:
        if not _generation_active(token, generation):
            break
        try:
            with application.app_context():
                if not connected:
                    secret = webhook_secret(token, application.secret_key)
                    username = _verify_group(token, bot_config.read()['group_id'])
                    _assert_active(token, generation)
                    result = max_api(token, 'POST', '/subscriptions', payload={
                        'url': url,
                        'update_types': [
                            'bot_started', 'bot_stopped', 'dialog_removed',
                            'message_created', 'message_callback',
                        ],
                        'secret': secret,
                    }, timeout=15)
                    if result.get('success') is not True:
                        raise RuntimeError('MAX webhook registration failed')
                    try:
                        _assert_active(token, generation)
                    except RuntimeError:
                        _unregister_webhook(token, url)
                        raise
                    connected = True
                    _connected_token = token
                    _connected_generation = generation
                    _last_error = False
                    _logger.warning('MAX limits webhook connected as @%s', username)
                drain_outbox(
                    on_state_change=lambda: _schedule_max_backup('MAX limits delivery'),
                    generation=generation)
                from max_registration import pending_moderation
                if _generation_active(token, generation):
                    with active_delivery(token, generation):
                        pending_moderation(max_api, token)
                backoff = 2
        except Exception:
            _logger.warning('MAX limits webhook or delivery failed; retrying with backoff')
            with application.app_context():
                db.session.rollback()
            if _connected_token == token and _connected_generation == generation:
                _connected_token = None
                _connected_generation = None
            if _generation_active(token, generation):
                _last_error = True
            connected = False
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
            continue
        time.sleep(5)
    if _connected_token == token and _connected_generation == generation:
        _connected_token = None
        _connected_generation = None


def _schedule_max_backup(reason):
    from app import _schedule_durable_safety_backup
    _schedule_durable_safety_backup(reason)


def _unregister_webhook(token, url):
    try:
        subscriptions = max_api(token, 'GET', '/subscriptions', timeout=5)
        for row in subscriptions.get('subscriptions', []):
            if row.get('url') == url and row.get('subscription_id'):
                max_api(token, 'DELETE',
                        f"/subscriptions/{row['subscription_id']}", timeout=5)
    except Exception:
        _logger.warning('MAX webhook unregister failed; local bot remains stopped')


def _generation_active(token, generation):
    config = bot_config.read()
    return bool(config.get('enabled') and config.get('token') == token
                and config.get('generation') == generation)


def _run_single_worker(application, token, webhook_url, generation):
    """One active consumer even if two server processes start at once."""
    bot_config._directory()
    path = bot_config.CONFIG.parent / 'max-bot-worker.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        while _generation_active(token, generation):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.25)
        else:
            return
        if not _generation_active(token, generation):
            return
        if webhook_url:
            _webhook_outbox_loop(application, token, webhook_url, generation)
        else:
            _worker_loop(application, token, generation)
    finally:
        os.close(fd)


def start_max_limits_worker(application):
    global _worker_thread, _worker_token, _worker_generation, _last_error
    if application.config.get('TESTING') or not application.config.get('DURABLE_STARTUP_READY'):
        return False
    with _worker_lock:
        config = bot_config.read()
        token = config['token']
        if not config.get('enabled') or not token:
            return False
        if (_worker_thread and _worker_thread.is_alive()
                and _worker_token == token
                and _worker_generation == config['generation']):
            return True
        webhook_url = os.environ.get('MAX_LIMITS_WEBHOOK_URL', '').strip()
        if ((requires_webhook() and not webhook_url)
                or (webhook_url and not valid_webhook_url(webhook_url))):
            _last_error = True
            _logger.error('MAX limits bot needs a valid HTTPS webhook in production')
            return False
        _last_error = False
        _worker_thread = threading.Thread(
            target=_run_single_worker,
            args=(application, token, webhook_url, config['generation']),
            name='max-limits-bot', daemon=True)
        _worker_thread.start()
        _worker_token = token
        _worker_generation = config['generation']
        return True


def stop_max_limits_worker(old_token=None):
    global _connected_token, _connected_generation, _last_error
    with bot_config.locked():
        config = bot_config._read()
        bot_config.set_enabled(False)
        _connected_token = None
        _connected_generation = None
        _last_error = False
        # Serialize remote unsubscribe with a concurrent start.
        unregister_token = old_token or config.get('token')
        if unregister_token and os.environ.get('MAX_LIMITS_WEBHOOK_URL'):
            _unregister_webhook(unregister_token, os.environ['MAX_LIMITS_WEBHOOK_URL'])