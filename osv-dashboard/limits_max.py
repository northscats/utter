"""Small MAX HTTP adapter. No implicit network calls or token discovery.

The injectable transport has urllib's ``urlopen(request, timeout, context)``
contract. Successful POST /messages means accepted by MAX, not delivered/read.
"""
import hashlib
import hmac
import json
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = 'https://platform-api2.max.ru'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_rate_lock = threading.Lock()
_last_request_at = 0.0


class MaxAPIError(RuntimeError):
    def __init__(self, message, *, status=None, code='', retryable=False,
                 retry_after=None, unknown_outcome=False):
        super().__init__(message)
        self.status = status
        self.status_code = status
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.unknown_outcome = unknown_outcome


class MaxUnknownOutcome(MaxAPIError, TimeoutError):
    def __init__(self, message='MAX: исход отправки неизвестен; автоматический повтор запрещён.'):
        super().__init__(message, unknown_outcome=True)


def verify_webhook_secret(expected, received):
    return bool(expected and received and hmac.compare_digest(
        str(expected).encode('utf-8'), str(received).encode('utf-8')))


def event_key(payload):
    if not isinstance(payload, dict) or payload.get('update_type') not in (
            'bot_started', 'bot_stopped', 'message_created', 'message_callback'):
        raise ValueError('Неподдерживаемое событие MAX.')
    # MAX Update does not promise a global update_id. Hash the complete
    # canonical event, including timestamp and message/callback identity.
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class MaxClient:
    def __init__(self, token=None, transport=None, timeout=15, ca_file=None):
        self._token = str(token or '').strip()
        self._transport = transport or urlopen
        self._timeout = min(max(float(timeout), 1), 60)
        # Verification is never disabled; optional CA adds a trusted root.
        self._context = ssl.create_default_context()
        if ca_file:
            self._context.load_verify_locations(cafile=ca_file)

    @property
    def is_configured(self):
        return bool(self._token)

    @property
    def configured(self):
        return self.is_configured

    def __repr__(self):
        return f'<MaxClient configured={self.is_configured}>'

    def _request(self, method, path, params=None, body=None):
        if not self.is_configured:
            raise MaxAPIError('MAX не подключён.', code='not_configured')
        query = urlencode({key: value for key, value in (params or {}).items()
                           if value is not None})
        url = API_BASE + path + ('?' + query if query else '')
        request = Request(url, data=json.dumps(body, ensure_ascii=False).encode('utf-8')
                          if body is not None else None, method=method,
                          headers={'Authorization': self._token,
                                   'Content-Type': 'application/json',
                                   'Accept': 'application/json'})
        # One shared limiter also covers freshly created clients and admin
        # checks, not just the notification batch. Keep below MAX's 30 RPS.
        global _last_request_at
        with _rate_lock:
            delay = 0.05 - (time.monotonic() - _last_request_at)
            if delay > 0:
                time.sleep(delay)
            _last_request_at = time.monotonic()
        try:
            with self._transport(request, timeout=self._timeout, context=self._context) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise MaxAPIError('MAX: ответ превышает допустимый размер.')
        except HTTPError as exc:
            try:
                retry_after = min(3600, max(1, int(exc.headers.get('Retry-After', '0'))))
            except (TypeError, ValueError):
                retry_after = None
            # Never include provider response, request headers, or URL in a log
            # message: provider errors may reflect personal data.
            raise MaxAPIError(
                f'MAX: HTTP {exc.code}.', status=exc.code,
                code='http_error', retryable=exc.code == 429 or exc.code >= 500,
                retry_after=retry_after) from None
        except (socket.timeout, TimeoutError):
            if method == 'POST':
                raise MaxUnknownOutcome() from None
            raise MaxAPIError('MAX: истекло время ожидания.', retryable=True) from None
        except URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                raise MaxAPIError('MAX: проверка TLS-сертификата не пройдена.',
                                  code='tls_verification') from None
            if method == 'POST':
                raise MaxUnknownOutcome() from None
            raise MaxAPIError('MAX: сервис временно недоступен.', retryable=True) from None
        except ssl.SSLError:
            raise MaxAPIError('MAX: проверка TLS-сертификата не пройдена.',
                              code='tls_verification') from None
        except (ConnectionError, OSError):
            if method == 'POST':
                raise MaxUnknownOutcome() from None
            raise MaxAPIError('MAX: ошибка соединения.', retryable=True) from None
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            if method == 'POST':
                raise MaxUnknownOutcome('MAX: ответ не распознан; исход запроса неизвестен.') from None
            raise MaxAPIError('MAX: некорректный ответ.') from None
        if not isinstance(data, dict):
            raise MaxAPIError('MAX: неожиданная структура ответа.')
        if data.get('success') is False:
            raise MaxAPIError('MAX отклонил запрос.', code='rejected')
        return data

    @staticmethod
    def _id(value, name):
        try:
            if isinstance(value, bool) or str(int(value)) != str(value):
                raise ValueError()
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{name}: нужен числовой ID MAX.') from None

    def send_message(self, user_id=None, text=None, *, chat_id=None, attachments=None):
        if (user_id is None) == (chat_id is None):
            raise ValueError('Нужен ровно один адресат: user_id или chat_id.')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Длина текста MAX должна быть от 1 до 4000 символов.')
        params = {'user_id': self._id(user_id, 'user_id')} if user_id is not None else {
            'chat_id': self._id(chat_id, 'chat_id')}
        body = {'text': text}
        if attachments:
            body['attachments'] = attachments
        result = self._request('POST', '/messages', params, body)
        if not ((result.get('message') or {}).get('body') or {}).get('mid'):
            raise MaxUnknownOutcome('MAX не вернул ID сообщения; исход отправки неизвестен.')
        return result

    def answer_callback(self, callback_id, text=None, *, message=None, notification=None):
        if not callback_id:
            raise ValueError('Нужен callback_id.')
        body = {}
        if message is not None:
            body['message'] = message
        elif notification or text:
            # Current /answers contract documents message, not a toast field.
            body['message'] = {'text': str(notification or text)[:4000],
                               'attachments': []}
        return self._request('POST', '/answers', {'callback_id': callback_id}, body)

    def get_chat_admins(self, chat_id):
        value = self._request('GET', f'/chats/{self._id(chat_id, "chat_id")}/members/admins')
        members = value.get('members')
        if not isinstance(members, list):
            raise MaxAPIError('MAX: список администраторов не подтверждён.')
        return members

    def is_human_admin(self, chat_id, user_id):
        target = str(self._id(user_id, 'user_id'))
        return any(str(member.get('user_id')) == target
                   and member.get('is_bot') is False for member in self.get_chat_admins(chat_id))

    def subscribe_webhook(self, url, secret):
        from urllib.parse import urlsplit
        target = urlsplit(url)
        if target.scheme != 'https' or not target.netloc or target.username or target.password:
            raise ValueError('Нужен публичный HTTPS-адрес webhook без учётных данных.')
        if not isinstance(secret, str) or not 5 <= len(secret) <= 256:
            raise ValueError('Секрет webhook должен содержать от 5 до 256 символов.')
        return self._request('POST', '/subscriptions', body={
            'url': url, 'secret': secret,
            'update_types': ['bot_started', 'bot_stopped', 'message_created', 'message_callback'],
        })

    def check_webhook_receiver(self, url, secret):
        """Confirm that the public receiver uses this installation's secret."""
        from urllib.parse import urlsplit
        from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler
        target = urlsplit(url)
        if target.scheme != 'https' or not target.netloc or target.username or target.password:
            raise ValueError('Нужен публичный HTTPS-адрес webhook.')
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        request = Request(url, headers={'X-Max-Bot-Api-Secret': secret}, method='GET')
        try:
            if self._transport is urlopen:
                response = build_opener(HTTPSHandler(context=self._context), NoRedirect()).open(
                    request, timeout=self._timeout)
            else:
                response = self._transport(request, timeout=self._timeout, context=self._context)
            with response:
                value = json.loads(response.read(4096))
            if not isinstance(value, dict) or value.get('ready') is not True:
                raise ValueError('Receiver not ready')
        except HTTPError as exc:
            if exc.code == 404:
                raise MaxAPIError(
                    'На опубликованном сайте отсутствует новый обработчик MAX.',
                    status=404, code='receiver_outdated') from None
            if exc.code == 403:
                raise MaxAPIError(
                    'Опубликованный обработчик MAX использует другое хранилище настроек.',
                    status=403, code='receiver_secret_mismatch') from None
            raise MaxAPIError(
                'Опубликованный обработчик MAX вернул ошибку.',
                status=exc.code, code='receiver_http_error') from None
        except (URLError, TimeoutError, socket.timeout):
            raise MaxAPIError(
                'Опубликованный обработчик MAX недоступен.',
                code='receiver_unavailable', retryable=True) from None
        except MaxAPIError:
            raise
        except Exception:
            raise MaxAPIError(
                'Опубликованный приёмник MAX не подтвердил готовность.',
                code='receiver_invalid_response') from None

    def get_updates(self, marker=None, timeout=25, limit=100):
        return self._request('GET', '/updates', {
            'marker': marker, 'timeout': min(max(int(timeout), 0), 90),
            'limit': min(max(int(limit), 1), 100),
            'types': 'bot_started,bot_stopped,message_created,message_callback',
        })