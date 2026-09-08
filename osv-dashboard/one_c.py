# -*- coding: utf-8 -*-
"""Входящий пилотный JSON-канал 1С и его административное API."""
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

from flask import Blueprint, Response, g, jsonify, request
from flask_login import current_user
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from database import (Company, OneCAttempt, OneCChannel, OneCIdempotency, db,
                      utcnow)
from one_c_transport import (channel_url, is_production, is_secure_transport,
                             transport_info)

MAX_BODY = 8 * 1024 * 1024
MAX_DEPTH = 32
PROCESSING_SECONDS = 10
JOURNAL_LIMIT = 1000
IDEMPOTENCY_DAYS = 7

bp = Blueprint('one_c', __name__)
_heavy_slots = threading.BoundedSemaphore(2)
_preauth_lock = threading.Lock()
_preauth = defaultdict(lambda: deque(maxlen=360))
_preauth_global = deque(maxlen=600)
_overload_last_log = 0.0
_overload_suppressed = 0


@bp.before_request
def _admin_api_auth_shape():
    """Не допускает HTML redirect со служебных административных API."""
    # These independent pilot models are outside the OSV safety snapshot.
    g.skip_durable_backup = True
    if (request.path.startswith('/settings/1c/') and
            not current_user.is_authenticated):
        return _admin_error('Требуется вход администратора', 401)


@bp.after_request
def _never_cache_integration_data(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.errorhandler(SQLAlchemyError)
def _database_unavailable(_error):
    # Includes authentication lookups and lock acquisition before body parsing.
    # Never let Flask log the original SQL exception with packet parameters.
    db.session.rollback()
    if request.path.startswith('/api/1c/push/'):
        return _machine_error('Временная ошибка базы данных', 503)
    return _admin_error('Временная ошибка базы данных', 503)


class NumberLexeme:
    def __init__(self, text):
        self.text = text


class StrictJSONError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError('JSON содержит повторяющийся ключ')
        result[key] = value
    return result


def parse_strict(raw):
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise StrictJSONError('Тело запроса должно быть JSON в UTF-8') from exc
    # Проверяем вложенность до рекурсивного декодера CPython.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_DEPTH:
                raise StrictJSONError('Превышена максимальная глубина JSON')
        elif char in ']}':
            depth -= 1
    try:
        decoder = json.JSONDecoder(
            object_pairs_hook=_pairs,
            parse_int=NumberLexeme,
            parse_float=NumberLexeme,
            parse_constant=lambda value: (_ for _ in ()).throw(
                StrictJSONError('Недопустимое числовое значение')))
        start = len(text) - len(text.lstrip())
        value, end = decoder.raw_decode(text, start)
        if text[end:].strip():
            raise StrictJSONError('После JSON обнаружены лишние данные')
        _validate_unicode(value)
        return value, text
    except (json.JSONDecodeError, StrictJSONError, RecursionError) as exc:
        if isinstance(exc, StrictJSONError):
            raise
        raise StrictJSONError('Некорректный JSON') from exc


def _validate_unicode(value):
    if isinstance(value, str):
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeEncodeError as exc:
            raise StrictJSONError(
                'JSON содержит недопустимый одиночный суррогат') from exc
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode(key)
            _validate_unicode(item)
    elif isinstance(value, list):
        for item in value:
            _validate_unicode(item)


def _depth(value, level=1):
    if level > MAX_DEPTH:
        raise StrictJSONError('Превышена максимальная глубина JSON')
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def _json_text(value):
    if isinstance(value, NumberLexeme):
        return value.text
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if isinstance(value, list):
        return '[' + ','.join(_json_text(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(
            json.dumps(key, ensure_ascii=False) + ':' + _json_text(item)
            for key, item in value.items()) + '}'
    raise StrictJSONError('Неподдерживаемое значение JSON')


def _cell(value):
    if value is None:
        return {'kind': 'null', 'text': 'null'}
    if isinstance(value, NumberLexeme):
        return {'kind': 'number', 'text': value.text}
    if isinstance(value, bool):
        return {'kind': 'boolean', 'text': 'true' if value else 'false'}
    if isinstance(value, str):
        return {'kind': 'string', 'text': value}
    return {'kind': 'json', 'text': _json_text(value)}


def _columns(rows):
    seen = set()
    ordered = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    analytics = [key for key in ordered if re.fullmatch(r'Аналитика(\d+)', key)]
    # Числовой порядок без int(): Python ограничивает преобразование строк с
    # тысячами цифр. Нули не влияют на значение.
    analytics.sort(key=lambda key: (
        len((re.fullmatch(r'Аналитика(\d+)', key).group(1).lstrip('0') or '0')),
        re.fullmatch(r'Аналитика(\d+)', key).group(1).lstrip('0') or '0',
        key))
    if not analytics:
        return ordered
    analytics_set = set(analytics)
    first = next(index for index, key in enumerate(ordered)
                 if key in analytics_set)
    plain = [key for key in ordered if key not in analytics_set]
    before = sum(1 for key in ordered[:first] if key not in analytics_set)
    return plain[:before] + analytics + plain[before:]


def _machine(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response


def _machine_error(description, status):
    response = _machine({
        'ErrorCode': status,
        'ErrorDescription': description,
        'Result': None,
    }, status)
    if status in (429, 503):
        response.headers['Retry-After'] = '5'
    if status == 401:
        response.headers['WWW-Authenticate'] = 'Basic realm="1C push", charset="UTF-8"'
    return response


def _admin_error(text, status=400):
    return jsonify(error=text), status


def _basic_credentials():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Basic '):
        return None, None
    try:
        decoded = base64.b64decode(
            header[6:].strip(), validate=True).decode('utf-8')
        return decoded.split(':', 1) if ':' in decoded else (None, None)
    except (ValueError, UnicodeDecodeError):
        return None, None


def _preauth_allowed():
    key = request.environ.get('one_c.raw_peer', '') or 'unknown'
    now = time.monotonic()
    with _preauth_lock:
        while _preauth_global and _preauth_global[0] < now - 60:
            _preauth_global.popleft()
        if len(_preauth_global) >= _preauth_global.maxlen:
            return False
        _preauth_global.append(now)
        values = _preauth[key]
        while values and values[0] < now - 60:
            values.popleft()
        if len(values) >= 300:
            return False
        values.append(now)
        if len(_preauth) > 5000:
            for old_key in list(_preauth)[:1000]:
                if not _preauth[old_key] or _preauth[old_key][-1] < now - 60:
                    _preauth.pop(old_key, None)
        return True


def _transport_name():
    return 'https' if is_secure_transport() else 'http'


def _journal(channel_id, result, byte_size=0, row_count=None,
             receipt_id=None, error=''):
    receipt_id = receipt_id or str(uuid.uuid4())
    cutoff = utcnow() - timedelta(days=30)
    OneCAttempt.query.filter(OneCAttempt.received_at < cutoff).delete(
        synchronize_session=False)
    db.session.add(OneCAttempt(
        channel_id=channel_id, result=result, byte_size=byte_size,
        row_count=row_count, receipt_id=receipt_id,
        transport=_transport_name(), error=(error or '')[:300]))
    db.session.flush()
    excess = (OneCAttempt.query.filter_by(channel_id=channel_id)
              .order_by(OneCAttempt.received_at.desc(), OneCAttempt.id.desc())
              .offset(JOURNAL_LIMIT).all())
    for item in excess:
        db.session.delete(item)
    if channel_id is None:
        unknown_excess = (OneCAttempt.query.filter(
            OneCAttempt.channel_id.is_(None))
            .order_by(OneCAttempt.received_at.desc(), OneCAttempt.id.desc())
            .offset(JOURNAL_LIMIT).all())
        for item in unknown_excess:
            db.session.delete(item)
    return receipt_id


def _safe_early_attempt(channel_id, result, byte_size=0, error=''):
    """Best-effort audit which never exposes a database exception."""
    try:
        _set_db_timeouts()
        receipt = _journal(channel_id, result, byte_size, error=error)
        db.session.commit()
        return receipt
    except SQLAlchemyError:
        db.session.rollback()
        return None


def _sample_global_overload(byte_size):
    """At most one synchronous DB audit per minute during global overload."""
    global _overload_last_log, _overload_suppressed
    now = time.monotonic()
    with _preauth_lock:
        _overload_suppressed += 1
        if now - _overload_last_log < 60:
            return
        suppressed = _overload_suppressed
        _overload_suppressed = 0
        _overload_last_log = now
    _safe_early_attempt(
        None, 'rate_limited', byte_size,
        f'Глобальное ограничение частоты; подавлено попыток: {suppressed}')


def _set_db_timeouts():
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(sql_text("SET LOCAL lock_timeout = '3000ms'"))
        db.session.execute(sql_text("SET LOCAL statement_timeout = '9000ms'"))


def _channel_dict(channel):
    company = channel.company
    return {
        'id': channel.id,
        'company_id': channel.company_id,
        'company_name': company.name,
        'inn': company.inn or '',
        'enabled': channel.enabled,
        'allow_http': channel.allow_http,
        'last_received_at': (channel.last_received_at.isoformat()
                             if channel.last_received_at else None),
        'row_count': channel.last_row_count if channel.last_received_at else 0,
        'login': channel.login,
        'url': channel_url(channel.id),
    }


def _locked_channel(channel_id):
    _set_db_timeouts()
    company_id = db.session.query(OneCChannel.company_id).filter_by(
        id=channel_id).scalar()
    if company_id is None:
        return None
    Company.query.filter_by(id=company_id).with_for_update().populate_existing().first()
    return (OneCChannel.query.filter_by(id=channel_id).with_for_update()
            .populate_existing().first())


def _cleanup_retention():
    _set_db_timeouts()
    OneCAttempt.query.filter(
        OneCAttempt.received_at < utcnow() - timedelta(days=30)).delete(
            synchronize_session=False)
    OneCIdempotency.query.filter(
        OneCIdempotency.created_at <
        utcnow() - timedelta(days=IDEMPOTENCY_DAYS)).delete(
            synchronize_session=False)


def _render_snapshot(channel, offset, limit, column_offset, column_limit):
    if not _heavy_slots.acquire(blocking=False):
        return _admin_error('Сервер занят, повторите запрос позже', 503)
    started = time.monotonic()
    try:
        raw = channel.last_raw_json
        if raw is None or len(raw.encode('utf-8')) > MAX_BODY:
            return _admin_error('Сохранённый пакет превышает допустимый размер',
                                413)
        document, _ = parse_strict(raw.encode('utf-8'))
        all_rows = document['Result']
        columns = _columns(all_rows)
        if time.monotonic() - started > PROCESSING_SECONDS:
            return _admin_error('Превышен бюджет обработки снимка', 503)
        window = columns[column_offset:column_offset + column_limit]
        rendered = []
        for row in all_rows[offset:offset + limit]:
            rendered.append([
                _cell(row[key]) if key in row
                else {'kind': 'missing', 'text': ''}
                for key in window])
            if time.monotonic() - started > PROCESSING_SECONDS:
                return _admin_error('Превышен бюджет обработки снимка', 503)
        return Response(json.dumps(dict(
            received=True, receipt_id=channel.last_receipt_id,
            received_at=channel.last_received_at.isoformat(),
            row_count=len(all_rows), column_count=len(columns),
            offset=offset, limit=limit, column_offset=column_offset,
            column_limit=column_limit, columns=window, rows=rendered),
            ensure_ascii=False), content_type='application/json; charset=utf-8')
    finally:
        _heavy_slots.release()


@bp.post('/api/1c/push/<int:channel_id>')
def push(channel_id):
    g.skip_durable_backup = True
    if not _preauth_allowed():
        _sample_global_overload(request.content_length or 0)
        return _machine_error('Слишком много запросов', 429)
    started = time.monotonic()
    byte_size = request.content_length or 0
    login, password = _basic_credentials()
    _set_db_timeouts()
    channel = db.session.get(OneCChannel, channel_id)
    if (channel is None or login is None or not secrets.compare_digest(
            login.encode('utf-8'), channel.login.encode('utf-8'))):
        known_id = channel.id if channel else None
        db.session.rollback()
        _safe_early_attempt(known_id, 'unauthorized', byte_size,
                            'Неверные учётные данные')
        return _machine_error('Неверные учётные данные', 401)
    company_id = channel.company_id
    authenticated_hash = channel.password_hash
    db.session.rollback()
    if not _heavy_slots.acquire(blocking=False):
        _safe_early_attempt(channel_id, 'busy', byte_size,
                            'Ограничение параллельной обработки')
        return _machine_error('Сервер занят, повторите запрос позже', 503)
    try:
        if not check_password_hash(authenticated_hash, password or ''):
            _safe_early_attempt(channel_id, 'unauthorized', byte_size,
                                'Неверные учётные данные')
            return _machine_error('Неверные учётные данные', 401)
        if request.mimetype != 'application/json':
            _safe_early_attempt(
                channel_id, 'rejected', byte_size,
                'Требуется Content-Type application/json')
            return _machine_error('Требуется Content-Type application/json', 415)
        content_length = request.content_length
        if content_length is not None and content_length > MAX_BODY:
            _safe_early_attempt(channel_id, 'rejected', content_length,
                                'Размер запроса превышает 8 МиБ')
            return _machine_error('Размер запроса превышает 8 МиБ', 413)
        try:
            request.max_content_length = MAX_BODY
        except AttributeError:
            pass
        raw = request.stream.read(MAX_BODY + 1)
        byte_size = len(raw)
        if byte_size > MAX_BODY:
            raise StrictJSONError('Размер запроса превышает 8 МиБ')
        document, raw_text = parse_strict(raw)
        _depth(document)
        if time.monotonic() - started > PROCESSING_SECONDS:
            raise TimeoutError
        if not isinstance(document, dict) or set(document) != {
                'ErrorCode', 'ErrorDescription', 'Result'}:
            raise StrictJSONError(
                'Ожидается конверт ErrorCode, ErrorDescription, Result')
        code = document['ErrorCode']
        if (not isinstance(code, NumberLexeme) or
                not re.fullmatch(r'-?0+(?:\.0+)?(?:[eE][+-]?\d+)?',
                                 code.text)):
            _safe_early_attempt(
                channel_id, 'source_error', byte_size,
                'Источник вернул ненулевой ErrorCode')
            return _machine_error('Источник вернул ошибку', 422)
        rows = document['Result']
        if not isinstance(document['ErrorDescription'], str):
            raise StrictJSONError('ErrorDescription должен быть строкой')
        if not isinstance(rows, list) or any(not isinstance(row, dict)
                                             for row in rows):
            raise StrictJSONError('Result должен быть массивом объектов')
        inns = set()
        for row in rows:
            if time.monotonic() - started > PROCESSING_SECONDS:
                raise TimeoutError
            value = row.get('ОрганизацияИНН')
            inns.add(value if isinstance(value, str) else '')
        _columns(rows)
        if time.monotonic() - started > PROCESSING_SECONDS:
            raise TimeoutError
        body_hash = hashlib.sha256(raw).hexdigest()
        idem_key = request.headers.get('Idempotency-Key', '').strip()
        if len(idem_key) > 200:
            raise StrictJSONError('Слишком длинный Idempotency-Key')

        # Только теперь берём блокировки, в едином порядке company -> channel.
        _set_db_timeouts()
        company = (Company.query.filter_by(id=company_id).with_for_update()
                   .populate_existing().first())
        locked = (OneCChannel.query.filter_by(id=channel_id).with_for_update()
                  .populate_existing().first())
        if time.monotonic() - started > PROCESSING_SECONDS:
            raise TimeoutError
        if (locked is None or locked.company_id != company_id or
                not secrets.compare_digest(locked.login.encode('utf-8'),
                                           login.encode('utf-8')) or
                not secrets.compare_digest(locked.password_hash.encode('utf-8'),
                                           authenticated_hash.encode('utf-8'))):
            db.session.rollback()
            _safe_early_attempt(channel_id, 'unauthorized', byte_size,
                                'Реквизиты были заменены')
            return _machine_error('Неверные учётные данные', 401)
        if not locked.enabled:
            _journal(channel_id, 'rejected', byte_size, error='Канал отключён')
            db.session.commit()
            return _machine_error('Канал отключён', 403)
        if company is None or company.status != 'active':
            _journal(channel_id, 'rejected', byte_size,
                     error='Предприятие не активно')
            db.session.commit()
            return _machine_error('Предприятие не активно', 409)
        expected_inn = (company.inn or '').strip()
        if (not expected_inn or
                (inns and (len(inns) != 1 or expected_inn not in inns))):
            _journal(
                channel_id, 'rejected', byte_size,
                error='Пакет должен содержать только ИНН предприятия канала')
            db.session.commit()
            return _machine_error(
                'Пакет должен содержать только ИНН предприятия канала', 400)
        if (not is_secure_transport() and
                (is_production() or not locked.allow_http)):
            _journal(channel_id, 'rejected', byte_size, error='Требуется HTTPS')
            db.session.commit()
            return _machine_error('Требуется HTTPS', 403)
        minute_ago = utcnow() - timedelta(minutes=1)
        quota_count = OneCAttempt.query.filter(
            OneCAttempt.channel_id == channel_id,
            OneCAttempt.received_at >= minute_ago,
            OneCAttempt.result.notin_(
                ('unauthorized', 'rate_limited', 'busy'))).count()
        if quota_count >= 60:
            _journal(channel_id, 'rate_limited', byte_size,
                     error='Ограничение частоты канала')
            db.session.commit()
            return _machine_error('Слишком много запросов к каналу', 429)
        cutoff = utcnow() - timedelta(days=IDEMPOTENCY_DAYS)
        OneCIdempotency.query.filter(
            OneCIdempotency.channel_id == channel_id,
            OneCIdempotency.created_at < cutoff).delete(
                synchronize_session=False)
        if idem_key:
            old = OneCIdempotency.query.filter_by(
                channel_id=channel_id, key=idem_key).first()
            if old:
                if old.body_hash != body_hash:
                    _journal(channel_id, 'conflict', byte_size,
                             error='Конфликт Idempotency-Key')
                    db.session.commit()
                    return _machine_error('Idempotency-Key уже использован', 409)
                if time.monotonic() - started > PROCESSING_SECONDS:
                    raise TimeoutError
                _journal(channel_id, 'duplicate', byte_size, len(rows))
                db.session.commit()
                return _machine({
                    'ErrorCode': 0, 'ErrorDescription': '',
                    'Result': {'receipt_id': old.receipt_id,
                               'duplicate': True}})
        receipt_id = str(uuid.uuid4())
        if time.monotonic() - started > PROCESSING_SECONDS:
            raise TimeoutError
        locked.last_received_at = utcnow()
        locked.last_receipt_id = receipt_id
        locked.last_raw_json = raw_text
        locked.last_row_count = len(rows)
        if idem_key:
            db.session.add(OneCIdempotency(
                channel_id=channel_id, key=idem_key, body_hash=body_hash,
                receipt_id=receipt_id))
        _journal(channel_id, 'accepted', byte_size, len(rows), receipt_id)
        if time.monotonic() - started > PROCESSING_SECONDS:
            raise TimeoutError
        db.session.commit()
        return _machine({
            'ErrorCode': 0, 'ErrorDescription': '',
            'Result': {'receipt_id': receipt_id, 'duplicate': False}})
    except TimeoutError:
        db.session.rollback()
        _safe_early_attempt(channel_id, 'rejected', byte_size,
                            'Превышен бюджет обработки 10 секунд')
        return _machine_error('Превышен бюджет обработки', 503)
    except StrictJSONError as exc:
        db.session.rollback()
        _safe_early_attempt(channel_id, 'rejected', byte_size, str(exc))
        return _machine_error(str(exc),
                              413 if '8 МиБ' in str(exc) else 400)
    except RequestEntityTooLarge:
        db.session.rollback()
        _safe_early_attempt(channel_id, 'rejected', byte_size,
                            'Размер запроса превышает 8 МиБ')
        return _machine_error('Размер запроса превышает 8 МиБ', 413)
    except IntegrityError:
        db.session.rollback()
        return _machine_error('Конфликт параллельного запроса', 409)
    except SQLAlchemyError:
        db.session.rollback()
        return _machine_error('Временная ошибка базы данных', 503)
    finally:
        _heavy_slots.release()


def init_one_c(app, csrf, admin_required):
    """Регистрирует API после объявления административного декоратора."""
    csrf.exempt(push)

    @bp.get('/settings/1c/channels')
    @admin_required
    def channels_get():
        _cleanup_retention()
        db.session.commit()
        channels = OneCChannel.query.order_by(OneCChannel.id).all()
        companies = Company.query.filter_by(status='active').order_by(
            Company.name).all()
        return jsonify(
            channels=[_channel_dict(item) for item in channels],
            companies=[{'id': item.id, 'name': item.name, 'inn': item.inn or ''}
                       for item in companies],
            transport=transport_info())

    @bp.post('/settings/1c/channels')
    @admin_required
    def channels_create():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'company_id'}:
            return _admin_error('Укажите предприятие')
        company_id = payload.get('company_id')
        if isinstance(company_id, bool) or not isinstance(company_id, int):
            return _admin_error('Укажите предприятие')
        _set_db_timeouts()
        company = (Company.query.filter_by(id=company_id).with_for_update()
                   .populate_existing().first())
        if not company or company.status != 'active':
            return _admin_error('Активное предприятие не найдено', 404)
        if not (company.inn or '').strip():
            return _admin_error('У предприятия не указан ИНН', 409)
        if OneCChannel.query.filter_by(company_id=company.id).first():
            return _admin_error('Для предприятия канал уже создан', 409)
        password = secrets.token_urlsafe(32)
        channel = OneCChannel(
            company_id=company.id,
            login='onec_' + secrets.token_urlsafe(12),
            password_hash=generate_password_hash(password))
        db.session.add(channel)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return _admin_error('Для предприятия канал уже создан', 409)
        return jsonify(channel=_channel_dict(channel), password=password), 201

    @bp.post('/settings/1c/channels/<int:channel_id>')
    @admin_required
    def channels_update(channel_id):
        channel = _locked_channel(channel_id)
        if not channel:
            return _admin_error('Канал не найден', 404)
        payload = request.get_json(silent=True)
        allowed = {'enabled', 'allow_http'}
        if (not isinstance(payload, dict) or not payload or
                not set(payload).issubset(allowed) or
                any(not isinstance(value, bool) for value in payload.values())):
            return _admin_error('Некорректные параметры канала')
        if payload.get('allow_http') and is_production():
            return _admin_error('HTTP запрещён в производственной среде', 409)
        for key, value in payload.items():
            setattr(channel, key, value)
        db.session.commit()
        return jsonify(channel=_channel_dict(channel))

    @bp.post('/settings/1c/channels/<int:channel_id>/rotate')
    @admin_required
    def channels_rotate(channel_id):
        channel = _locked_channel(channel_id)
        if not channel:
            return _admin_error('Канал не найден', 404)
        password = secrets.token_urlsafe(32)
        channel.password_hash = generate_password_hash(password)
        db.session.commit()
        return jsonify(channel=_channel_dict(channel), password=password)

    @bp.get('/settings/1c/channels/<int:channel_id>/data')
    @admin_required
    def channels_data(channel_id):
        _cleanup_retention()
        db.session.commit()
        channel = db.session.get(OneCChannel, channel_id)
        if not channel:
            return _admin_error('Канал не найден', 404)
        if not channel.last_received_at:
            return jsonify(received=False)
        requested_receipt = request.args.get('receipt_id')
        if requested_receipt and requested_receipt != channel.last_receipt_id:
            return _admin_error('Снимок данных изменился', 409)
        try:
            offset = max(0, int(request.args.get('offset', 0)))
            limit = min(200, max(1, int(request.args.get('limit', 50))))
            column_offset = max(0, int(request.args.get('column_offset', 0)))
            column_limit = min(
                100, max(1, int(request.args.get('column_limit', 30))))
        except ValueError:
            return _admin_error('Некорректные параметры пагинации')
        return _render_snapshot(
            channel, offset, limit, column_offset, column_limit)

    @bp.get('/settings/1c/attempts')
    @admin_required
    def attempts_get():
        _cleanup_retention()
        db.session.commit()
        try:
            page = max(1, int(request.args.get('page', 1)))
            channel_id = request.args.get('channel_id')
            channel_id = int(channel_id) if channel_id else None
        except ValueError:
            return _admin_error('Некорректные параметры журнала')
        query = OneCAttempt.query
        if channel_id is not None:
            query = query.filter_by(channel_id=channel_id)
        items = query.order_by(
            OneCAttempt.received_at.desc(), OneCAttempt.id.desc()).offset(
                (page - 1) * 50).limit(51).all()
        return jsonify(items=[{
            'channel_id': item.channel_id,
            'received_at': item.received_at.isoformat(),
            'result': item.result,
            'receipt_id': item.receipt_id,
            'byte_size': item.byte_size,
            'row_count': item.row_count,
            'transport': item.transport,
            'error': item.error or '',
        } for item in items[:50]], page=page, has_more=len(items) > 50)

    app.register_blueprint(bp)