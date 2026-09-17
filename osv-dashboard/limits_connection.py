"""Durable, always-on MAX webhook connection management."""
import fcntl
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from database import Setting, db
from limits_max import MaxAPIError
from sqlalchemy import text

WEBHOOK_PATH = '/limit-notifications/max/webhook'
_connection_lock = threading.Lock()
_PROCESS_LOCK_PATH = os.environ.get(
    'LIMITS_MAX_CONNECTION_LOCK', '/tmp/osv-limits-max-connection.lock')
_ADVISORY_LOCK_ID = 166_2026_09
RETRY_DELAYS = (15, 60, 300, 900)


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat(timespec='seconds')


def public_webhook_url(*, configured=''):
    """Return only a deployment-controlled public receiver URL."""
    return str(configured or '').strip()


def configured_webhook_url(app=None):
    """Read the canonical URL identically in requests and background workers."""
    configured = app.config.get('LIMITS_MAX_WEBHOOK_URL', '') if app else ''
    return public_webhook_url(
        configured=configured or os.environ.get('LIMITS_MAX_WEBHOOK_URL', ''))


def _validate_webhook_url(url):
    target = urlsplit(str(url or ''))
    if (target.scheme != 'https' or not target.netloc or target.username
            or target.password or target.query or target.fragment):
        raise ValueError(
            'Адрес webhook не передан в запущенную публикацию. Задайте production-'
            'переменную LIMITS_MAX_WEBHOOK_URL полным каноническим HTTPS-адресом '
            'и заново опубликуйте приложение.')


def configuration_complete():
    return bool(Setting.get('limits_max_token', '')
                and Setting.get('limits_max_group_chat_id', '')
                and Setting.get('limits_bot_dataset_id', ''))


def registration_active():
    # Background threads own long-lived scoped sessions. Never let their
    # identity maps retain a pre-reconciliation Setting value.
    db.session.expire_all()
    return configuration_complete() and Setting.get(
        'limits_max_webhook_registered', '') == '1'


def connection_status():
    complete = configuration_complete()
    active = registration_active()
    retry_at = Setting.get('limits_max_retry_at', '')
    return {
        'state': 'working' if active else (
            'reconnecting' if complete and retry_at else 'incomplete'),
        'complete': complete,
        'active': active,
        'error': Setting.get('limits_max_connection_error', ''),
        'last_attempt_at': Setting.get('limits_max_last_attempt_at', ''),
        'last_success_at': Setting.get('limits_max_last_success_at', ''),
        'retry_at': retry_at,
    }


@contextmanager
def _serialized_connection(blocking=True):
    """Serialize registration/dispatch across threads, processes and replicas."""
    acquired = _connection_lock.acquire(blocking=blocking)
    if not acquired:
        yield False
        return
    handle = None
    advisory_connection = None
    try:
        handle = open(_PROCESS_LOCK_PATH, 'a+')
        mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(handle, mode)
        except BlockingIOError:
            yield False
            return
        try:
            if db.engine.dialect.name == 'postgresql':
                advisory_connection = db.engine.connect()
                function = 'pg_advisory_lock' if blocking else 'pg_try_advisory_lock'
                locked = advisory_connection.execute(
                    text(f'SELECT {function}(:key)'),
                    {'key': _ADVISORY_LOCK_ID}).scalar()
                if not blocking and not locked:
                    yield False
                    return
        except RuntimeError:
            # Allows lock-only unit tests outside a Flask application context.
            advisory_connection = None
        yield True
    finally:
        if advisory_connection is not None:
            try:
                advisory_connection.execute(
                    text('SELECT pg_advisory_unlock(:key)'),
                    {'key': _ADVISORY_LOCK_ID})
            finally:
                advisory_connection.close()
        if handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()
        _connection_lock.release()


def _safe_error(exc):
    code = getattr(exc, 'code', '')
    status = getattr(exc, 'status', None)
    if code == 'receiver_outdated':
        return ('На опубликованном сайте работает старая версия: проверочный адрес MAX '
                'вернул 404. Обновите публикацию; повтор будет выполнен автоматически.')
    if code == 'receiver_secret_mismatch':
        return ('Опубликованный обработчик не подтвердил служебный секрет. Проверьте, '
                'что настройки открыты в текущей публикации.')
    if code.startswith('receiver_'):
        return ('Опубликованный обработчик MAX сейчас недоступен или ответил неверно. '
                'Приложение повторит подключение автоматически.')
    if code == 'tls_verification':
        return 'Не удалось проверить TLS-сертификат MAX.'
    if status == 401:
        return 'MAX отклонил токен: проверьте токен выбранного бота.'
    if status in (403, 404):
        return ('MAX не подтвердил доступ бота к указанной админ-группе. '
                'Проверьте ID группы и права бота.')
    if code == 'rejected':
        return 'MAX отклонил регистрацию webhook. Проверьте права бота и адрес приёмника.'
    if isinstance(exc, ValueError):
        return str(exc)
    return ('Webhook не зарегистрирован в MAX. Проверьте настройки; '
            'приложение повторит подключение автоматически.')


def _schedule_retry(attempt):
    delay = RETRY_DELAYS[min(max(attempt - 1, 0), len(RETRY_DELAYS) - 1)]
    Setting.set('limits_max_retry_count', str(attempt))
    Setting.set('limits_max_retry_at', _iso(_now() + timedelta(seconds=delay)))


def reconcile_connection(*, encrypt, decrypt, adapter_factory, webhook_url,
                         raise_on_error=False, blocking=True):
    """Verify receiver, credentials and subscription; preserve queues on failure."""
    with _serialized_connection(blocking=blocking) as acquired:
        if not acquired:
            return False
        if not configuration_complete():
            Setting.set('limits_max_webhook_registered', '0')
            Setting.set('limits_max_enabled', '0')
            Setting.set('limits_max_retry_at', '')
            Setting.set('limits_max_connection_error',
                        'Укажите токен MAX, ID админ-группы и набор данных.')
            db.session.commit()
            return False
        attempt_at = _now()
        Setting.set('limits_max_last_attempt_at', _iso(attempt_at))
        Setting.set('limits_max_webhook_registered', '0')
        Setting.set('limits_max_enabled', '0')
        db.session.commit()
        try:
            _validate_webhook_url(webhook_url)
            secret = decrypt(Setting.get('limits_max_webhook_secret', ''))
            if not secret:
                # Legacy complete-but-disabled configurations predate automatic
                # secret creation. Persist it before the receiver can observe it.
                secret = secrets.token_urlsafe(36)
                Setting.set('limits_max_webhook_secret', encrypt(secret))
                db.session.commit()
            adapter = adapter_factory()
            db.session.commit()
            adapter.check_webhook_receiver(webhook_url, secret)
            adapter.get_chat_admins(Setting.get('limits_max_group_chat_id'))
            result = adapter.subscribe_webhook(webhook_url, secret)
            if not isinstance(result, dict) or result.get('success') is False:
                raise MaxAPIError('MAX не подтвердил регистрацию webhook.')
        except Exception as exc:
            db.session.rollback()
            attempt = int(Setting.get('limits_max_retry_count', '0') or 0) + 1
            Setting.set('limits_max_webhook_registered', '0')
            Setting.set('limits_max_enabled', '0')
            Setting.set('limits_max_connection_error', _safe_error(exc))
            _schedule_retry(attempt)
            db.session.commit()
            if raise_on_error:
                raise MaxAPIError('Не удалось подключить MAX.') from None
            return False
        Setting.set('limits_max_webhook_registered', '1')
        Setting.set('limits_max_enabled', '1')
        Setting.set('limits_max_connection_error', '')
        Setting.set('limits_max_last_success_at', _iso(_now()))
        Setting.set('limits_max_registered_url', webhook_url)
        Setting.set('limits_max_retry_at', '')
        Setting.set('limits_max_retry_count', '0')
        db.session.commit()
        return True


def configure_connection(*, token, group_id, dataset_id, encrypt, decrypt,
                         adapter_factory, webhook_url):
    """Save required settings and immediately enforce the always-on target."""
    with _serialized_connection():
        if token:
            Setting.set('limits_max_token', encrypt(token))
        Setting.set('limits_max_group_chat_id', group_id)
        Setting.set('limits_bot_dataset_id', str(dataset_id or ''))
        # New credentials/domain are untrusted until the complete preflight and
        # subscription succeed. Close the dispatch window in this same commit.
        Setting.set('limits_max_webhook_registered', '0')
        Setting.set('limits_max_enabled', '0')
        if configuration_complete() and not decrypt(
                Setting.get('limits_max_webhook_secret', '')):
            Setting.set('limits_max_webhook_secret',
                        encrypt(secrets.token_urlsafe(36)))
        db.session.commit()
    return reconcile_connection(
        encrypt=encrypt, decrypt=decrypt, adapter_factory=adapter_factory,
        webhook_url=webhook_url, raise_on_error=True)


def start_reconciler(app, encrypt, decrypt, adapter_factory, webhook_url_factory,
                     interval_seconds=5):
    """Continuously restore required registration after startup and failures."""
    state = app.extensions.get('limits_max_reconciler')
    if state and state['thread'].is_alive():
        return state
    stop = threading.Event()

    def work():
        with app.app_context():
            while not stop.is_set():
                try:
                    status = connection_status()
                    now = _now()
                    retry = status['retry_at']
                    due = not retry
                    if retry:
                        due = datetime.fromisoformat(retry) <= now
                    target = webhook_url_factory()
                    target_changed = (
                        Setting.get('limits_max_registered_url', '') != target)
                    if status['complete'] and (not status['active'] or target_changed) and due:
                        reconcile_connection(
                            encrypt=encrypt, decrypt=decrypt,
                            adapter_factory=adapter_factory,
                            webhook_url=target, blocking=False)
                except Exception:
                    db.session.rollback()
                    app.logger.exception('limits MAX reconciliation failed')
                stop.wait(max(1, int(interval_seconds)))

    thread = threading.Thread(target=work, name='limits-max-reconciler', daemon=True)
    state = {'thread': thread, 'stop': stop}
    app.extensions['limits_max_reconciler'] = state
    thread.start()
    return state