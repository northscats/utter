"""Explicit operator-driven MAX connection; never runs on process startup."""
import secrets
import threading

from database import Setting, db
from limits_max import MaxAPIError

# Verified through deployment metadata; override for a different publication.
DEFAULT_WEBHOOK_URL = 'https://gisuzter.replit.app/limit-notifications/max/webhook'
_connection_lock = threading.Lock()


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
            adapter = adapter_factory()
            db.session.commit()  # close settings-read transaction before I/O
            adapter.check_webhook_receiver(webhook_url, secret)
            result = adapter.subscribe_webhook(webhook_url, secret)
            if not isinstance(result, dict) or result.get('success') is not True:
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
            elif status == 401:
                message = 'MAX отклонил токен: проверьте, что это действующий токен выбранного бота.'
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