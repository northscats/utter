"""Explicit operator-driven MAX connection; never runs on process startup."""
import secrets
import threading
from urllib.parse import urlsplit

from database import Setting, db
from limits_max import MaxAPIError

WEBHOOK_PATH = '/limit-notifications/max/webhook'
_connection_lock = threading.Lock()


def public_webhook_url(*, configured=''):
    """Return only an operator/deployment-controlled public receiver URL."""
    return str(configured or '').strip()


def _validate_webhook_url(url):
    target = urlsplit(str(url or ''))
    if (target.scheme != 'https' or not target.netloc or target.username
            or target.password or target.query or target.fragment):
        raise ValueError(
            'Не настроен доверенный публичный HTTPS-адрес webhook для этой публикации.')


def configure_connection(*, token, group_id, dataset_id, enabled,
                         encrypt, decrypt, adapter_factory, webhook_url):
    from limits_bot import _dispatch_lock
    with _connection_lock, _dispatch_lock:
        if token:
            Setting.set('limits_max_token', encrypt(token))
        Setting.set('limits_max_group_chat_id', group_id)
        Setting.set('limits_bot_dataset_id', str(dataset_id or ''))
        # Pause sends before registration/replacement. Failures stay disabled.
        Setting.set('limits_max_enabled', '0')
        Setting.set('limits_max_connection_error', '')
        if not enabled:
            db.session.commit()
            return
        secret = decrypt(Setting.get('limits_max_webhook_secret', ''))
        if not secret:
            secret = secrets.token_urlsafe(36)
            Setting.set('limits_max_webhook_secret', encrypt(secret))
        # Receiver must see the secret before MAX can begin sending events.
        db.session.commit()
        try:
            # Validate before constructing the adapter or sending the secret.
            # In particular, request Host headers never control this sink.
            _validate_webhook_url(webhook_url)
            adapter = adapter_factory()
            db.session.commit()  # close settings-read transaction before I/O
            adapter.check_webhook_receiver(webhook_url, secret)
            # This validates both the token and the configured moderation chat
            # before MAX is allowed to start delivering updates.
            adapter.get_chat_admins(group_id)
            result = adapter.subscribe_webhook(webhook_url, secret)
            # MAX returns the created subscription object.  Some deployments
            # include ``success: true`` and some do not.  _request has already
            # rejected HTTP errors and an explicit ``success: false``.
            if not isinstance(result, dict) or result.get('success') is False:
                raise MaxAPIError('MAX не подтвердил регистрацию webhook.')
        except Exception as exc:
            db.session.rollback()
            Setting.set('limits_max_enabled', '0')
            code = getattr(exc, 'code', '')
            status = getattr(exc, 'status', None)
            if code == 'receiver_outdated':
                message = (
                    'На опубликованном сайте работает старая версия: проверочный адрес MAX '
                    'вернул 404. Сначала обновите публикацию приложения, затем снова включите бота.')
            elif code == 'receiver_secret_mismatch':
                message = (
                    'Настройки открыты не в той установке приложения: опубликованный обработчик '
                    'не подтвердил служебный секрет. Откройте настройки на опубликованном сайте.')
            elif code.startswith('receiver_'):
                message = (
                    'Опубликованный обработчик MAX сейчас недоступен или ответил неверно. '
                    'Проверьте публикацию и повторите включение из опубликованного приложения.')
            elif code == 'tls_verification':
                message = 'Не удалось проверить TLS-сертификат MAX. Подключение не включено.'
            elif status == 401:
                message = 'MAX отклонил токен: проверьте, что это действующий токен выбранного бота.'
            elif status in (403, 404):
                message = (
                    'MAX не подтвердил доступ бота к указанной админ-группе. '
                    'Проверьте ID группы и права бота.')
            elif code == 'rejected':
                message = (
                    'MAX отклонил регистрацию webhook. Проверьте права бота, '
                    'адрес приёмника и повторите включение.')
            elif isinstance(exc, ValueError):
                message = str(exc)
            else:
                message = (
                    'Webhook не зарегистрирован в MAX. Проверьте токен бота и повторите включение. '
                    'Отправки остаются выключенными.')
            Setting.set('limits_max_connection_error', message)
            db.session.commit()
            raise MaxAPIError('Не удалось подключить MAX. Отправки остаются выключенными.') from None
        Setting.set('limits_max_webhook_registered', '1')
        Setting.set('limits_max_enabled', '1')
        db.session.commit()