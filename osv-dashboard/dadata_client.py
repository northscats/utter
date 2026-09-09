# -*- coding: utf-8 -*-
"""Серверный клиент DaData с безопасной сетевой диагностикой."""
import json
import os
import socket
import ssl
import time
import uuid
from urllib import error, request

from directory_service import clean_identifier, normalize_party


class DaDataError(RuntimeError):
    def __init__(self, message, status=None, category=None, attempt_id=None):
        super().__init__(message)
        self.status = status
        self.category = category
        self.attempt_id = attempt_id


def _default_opener(close_connection=False):
    """Build an opener only when explicit CA/proxy-compatible behavior needs it."""
    ca_bundle = os.environ.get('SSL_CERT_FILE') or os.environ.get(
        'REQUESTS_CA_BUNDLE')
    if not ca_bundle and not close_connection:
        return request
    handlers = [request.ProxyHandler()]
    if ca_bundle:
        handlers.append(request.HTTPSHandler(
            context=ssl.create_default_context(cafile=ca_bundle)))
    return request.build_opener(*handlers)


def _network_error(exc):
    reason = getattr(exc, 'reason', exc)
    text = str(reason).lower()
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return 'timeout', 'Истекло время ожидания ответа DaData'
    if isinstance(reason, ssl.SSLError) or any(
            marker in text for marker in ('certificate verify', 'ssl:', 'tls')):
        return 'tls', 'Не удалось проверить TLS-сертификат DaData'
    if isinstance(reason, socket.gaierror) or any(
            marker in text for marker in ('name or service not known',
                                           'temporary failure in name resolution',
                                           'nodename nor servname')):
        return 'dns', 'DNS не смог определить адрес DaData'
    if 'proxy' in text or 'tunnel connection failed' in text:
        return 'proxy', 'Прокси-сервер отклонил или не смог передать запрос'
    return 'connection', 'Не удалось установить соединение с DaData'


def _emit(callback, started, attempt_id, stage, retry_no, **extra):
    if callback is None:
        return
    event = {
        'attempt_id': attempt_id,
        'stage': stage,
        'retry_no': retry_no,
        'duration_ms': max(0, int((time.monotonic() - started) * 1000)),
    }
    event.update(extra)
    try:
        callback(event)
    except Exception:
        pass


def _http_message(code):
    if code == 407:
        return 'proxy', 'Прокси-сервер требует авторизацию или отклонил запрос'
    if code in (401, 403):
        return 'authorization', 'Токен DaData недействителен или не имеет доступа'
    if code == 429:
        return 'rate_limit', 'Превышен лимит запросов DaData, повторите позже'
    if code >= 500:
        return 'provider', 'DaData временно недоступна'
    return 'http', 'Провайдер отклонил запрос'


def find_party(token, *, inn='', ogrn='', timeout=12, retries=1,
               backoff=0.35, close_connection=False, diagnostic=None,
               opener=None):
    inn = clean_identifier(inn, (10, 12))
    ogrn = clean_identifier(ogrn, (13, 15))
    query = inn or ogrn
    if not token:
        raise DaDataError('Токен DaData не настроен')
    if not query:
        raise DaDataError('Укажите корректный ИНН (10/12 цифр) или ОГРН (13/15 цифр)')

    endpoint = 'https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party'
    body = json.dumps({'query': query}).encode('utf-8')
    attempt_id = uuid.uuid4().hex[:12]
    last_error = None
    for attempt in range(max(0, int(retries)) + 1):
        started = time.monotonic()
        headers = {
            'Authorization': 'Token ' + token,
            'Content-Type': 'application/json',
        }
        if close_connection:
            headers['Connection'] = 'close'
        req = request.Request(endpoint, data=body, method='POST', headers=headers)
        _emit(diagnostic, started, attempt_id, 'sending', attempt)
        try:
            try:
                transport = opener or _default_opener(close_connection)
            except (OSError, ssl.SSLError) as exc:
                last_error = DaDataError(
                    'Не удалось загрузить доверенные TLS-сертификаты',
                    category='tls', attempt_id=attempt_id)
                _emit(diagnostic, started, attempt_id, 'network_error', attempt,
                      category='tls', outcome='error')
                raise last_error from exc
            open_url = (transport.urlopen if hasattr(transport, 'urlopen')
                        else transport.open)
            with open_url(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('response root must be an object')
                status = getattr(response, 'status', 200)
            _emit(diagnostic, started, attempt_id, 'response', attempt,
                  http_status=status, outcome='success')
            suggestions = payload.get('suggestions')
            if (not isinstance(suggestions, list)
                    or any(not isinstance(item, dict) for item in suggestions)
                    or any(not isinstance(item.get('data'), dict)
                           for item in suggestions)):
                raise ValueError('invalid suggestions schema')
            if ogrn:
                suggestions = [
                    item for item in suggestions
                    if (item.get('data') or {}).get('ogrn') == ogrn
                ]
            if inn:
                suggestions = [
                    item for item in suggestions
                    if (item.get('data') or {}).get('inn') == inn
                ]
            if not suggestions:
                _emit(diagnostic, started, attempt_id, 'completed', attempt,
                      category='not_found', outcome='error')
                raise DaDataError('Организация по указанным реквизитам не найдена',
                                  category='not_found', attempt_id=attempt_id)
            party = suggestions[0]
            _emit(diagnostic, started, attempt_id, 'completed', attempt,
                  http_status=status, outcome='success')
            return party, normalize_party(party)
        except error.HTTPError as exc:
            category, detail = _http_message(exc.code)
            last_error = DaDataError(
                detail, exc.code, category, attempt_id)
            retrying = (
                (exc.code == 429 or 500 <= exc.code <= 599)
                and attempt < retries)
            _emit(diagnostic, started, attempt_id, 'http_error', attempt,
                  http_status=exc.code, category=category,
                  outcome='retry' if retrying else 'error')
            if not retrying:
                raise last_error
        except error.URLError as exc:
            category, detail = _network_error(exc)
            last_error = DaDataError(detail, category=category,
                                     attempt_id=attempt_id)
            retrying = category in ('dns', 'connection', 'timeout') and attempt < retries
            _emit(diagnostic, started, attempt_id, 'network_error', attempt,
                  category=category,
                  outcome='retry' if retrying else 'error')
            if not retrying:
                raise last_error from exc
        except (socket.timeout, TimeoutError) as exc:
            last_error = DaDataError(
                'Истекло время ожидания ответа DaData',
                category='timeout', attempt_id=attempt_id)
            retrying = attempt < retries
            _emit(diagnostic, started, attempt_id, 'network_error', attempt,
                  category='timeout',
                  outcome='retry' if retrying else 'error')
            if not retrying:
                raise last_error from exc
        except (AttributeError, IndexError, KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            last_error = DaDataError(
                'DaData вернула некорректный ответ',
                category='invalid_response', attempt_id=attempt_id)
            _emit(diagnostic, started, attempt_id, 'completed', attempt,
                  category='invalid_response', outcome='error')
            raise last_error from exc
        if last_error:
            time.sleep(max(0, float(backoff)) * (2 ** attempt))
    raise last_error or DaDataError('Не удалось получить ответ DaData')


def check_token(token, **options):
    """Проверяет авторизацию тем же точным API, не раскрывая ответ клиенту."""
    try:
        find_party(token, inn='7707083893', **options)
        return True, 'Подключение к DaData успешно'
    except DaDataError as exc:
        return False, str(exc)