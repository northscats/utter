"""Transport boundary for the machine receiver, independent of browser ProxyFix.

Only an explicitly trusted immediate peer may describe the outer TLS connection.
Gunicorn forwarded_allow_ips is disabled: capture the original WSGI scheme here.
"""
import ipaddress
import os
from urllib.parse import urlsplit

from flask import current_app, request


class OneCTransportMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        # Preserve these BEFORE any legacy ProxyFix mutates the WSGI environ.
        environ['one_c.raw_peer'] = environ.get('REMOTE_ADDR', '')
        environ['one_c.raw_scheme'] = environ.get('wsgi.url_scheme', 'http')
        return self.app(environ, start_response)


def _option(key, default=''):
    return current_app.config.get(key, os.environ.get(key, default))


def is_production():
    # Unknown standalone environments fail closed. Testing HTTP requires an
    # explicit test deployment designation, never just Flask debug mode.
    deployed = str(os.environ.get('REPLIT_DEPLOYMENT', '')).lower()
    return (deployed in ('1', 'true')
            or str(_option('ONE_C_ENV', 'production')).lower()
            not in ('development', 'test'))


def is_secure_transport():
    environ = request.environ
    original = environ.get('werkzeug.proxy_fix.orig', {})
    scheme = environ.get(
        'one_c.raw_scheme', original.get('wsgi.url_scheme',
                                        environ.get('wsgi.url_scheme', 'http')))
    if scheme == 'https':
        return True
    peer = environ.get('one_c.raw_peer', original.get(
        'REMOTE_ADDR', environ.get('REMOTE_ADDR', '')))
    try:
        address = ipaddress.ip_address(peer)
        networks = str(_option('ONE_C_TRUSTED_PROXY_CIDRS', '')).split(',')
        trusted = any(address in ipaddress.ip_network(item.strip())
                      for item in networks if item.strip())
    except ValueError:
        return False
    # Exactly one overwritten header; never trust client-appended header chains.
    return trusted and request.headers.get('X-Forwarded-Proto', '') == 'https'


def channel_url(channel_id):
    base = str(_option('ONE_C_PUBLIC_BASE_URL', '')).rstrip('/')
    if not base:
        # Development-only address is explicitly marked as such in settings.
        # Production needs the operator-configured canonical HTTPS origin.
        if is_production():
            return ''
        base = 'https://' + request.host + request.script_root
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        return ''
    return base + '/api/1c/push/' + str(channel_id)


def transport_info():
    production = is_production()
    return {
        'production': production,
        'http_note': (
            'HTTPS обязателен в production независимо от переключателя. '
            if production else
            'HTTP допустим только в изолированной тестовой сети/VPN с отдельными '
            'реквизитами и ограничением IP. Пароль и данные передаются открыто. '
        ) + (
            'Прямой HTTP из точки проверки 08.09.2026 недоступен '
            '(соединение с портом 80 не установлено); доступность из сети 1С '
            'не подтверждена. Переключатель не отключает HTTPS внешнего прокси. '
            'Не перенаправляйте запрос с Authorization в обход ограничения. '
        ) + (
            'Это адрес preview: он не предназначен для круглосуточного обмена. '
            'После публикации используйте реквизиты опубликованного приложения.'
            if not production else
            'Нужны постоянный хостинг, публичная маршрутизация и доступ из сети 1С.'
        ),
    }