# -*- coding: utf-8 -*-
"""Изолированные регрессии транспорта и безопасной диагностики DaData."""
import json
import socket
import ssl
from io import BytesIO
from unittest.mock import patch
from urllib import error
from urllib import request

from dadata_client import DaDataError, _default_opener, find_party


PARTY = {
    'value': 'Организация',
    'data': {'inn': '7707083893', 'name': {'full_with_opf': 'Организация'}},
}


class Response:
    status = 200

    def __init__(self, payload=None):
        self.payload = payload or {'suggestions': [PARTY]}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def require(label, condition):
    if not condition:
        raise AssertionError(label)
    print('PASS', label)


events = []
with patch('dadata_client.request.urlopen', return_value=Response()) as urlopen:
    raw, normalized = find_party(
        'top-secret-token', inn='7707083893', diagnostic=events.append,
        close_connection=True, opener=request)
request_obj = urlopen.call_args.args[0]
require('успешный ответ нормализован', raw == PARTY and normalized['inn'] == '7707083893')
require('режим закрытого контура закрывает соединение',
        request_obj.get_header('Connection') == 'close')
require('журнал содержит жизненный цикл и длительность',
        [item['stage'] for item in events] == ['sending', 'response', 'completed']
        and all('duration_ms' in item for item in events))
require('секрет и реквизиты не попадают в события',
        'top-secret-token' not in json.dumps(events)
        and '7707083893' not in json.dumps(events))

for code, expected in ((401, 'authorization'), (403, 'authorization'),
                       (407, 'proxy'),
                       (429, 'rate_limit'), (503, 'provider')):
    http_error = error.HTTPError(
        'https://example.invalid', code, 'failed', {}, BytesIO())
    captured = []
    with patch('dadata_client.request.urlopen', side_effect=http_error), \
            patch('dadata_client.time.sleep'):
        try:
            find_party('token', inn='7707083893', retries=0,
                       diagnostic=captured.append)
        except DaDataError as exc:
            require(f'HTTP {code} классифицирован', exc.category == expected)
    require(f'HTTP {code} записан безопасно',
            captured[-1]['http_status'] == code
            and captured[-1]['category'] == expected)

network_cases = [
    (error.URLError(socket.gaierror(-2, 'Name or service not known')), 'dns'),
    (error.URLError(ConnectionRefusedError('connection refused')), 'connection'),
    (error.URLError(socket.timeout('timed out')), 'timeout'),
    (error.URLError(ssl.SSLCertVerificationError('certificate verify failed')), 'tls'),
    (error.URLError(OSError('Tunnel connection failed: 407 Proxy Authentication Required')),
     'proxy'),
]
for network_error, expected in network_cases:
    captured = []
    with patch('dadata_client.request.urlopen', side_effect=network_error):
        try:
            find_party('token', inn='7707083893', retries=0,
                       diagnostic=captured.append)
        except DaDataError as exc:
            require(f'сетевая категория {expected}', exc.category == expected)

for network_error in (
        error.URLError(ssl.SSLError('certificate verify failed')),
        error.URLError(OSError('Tunnel connection failed: 407 Proxy Authentication Required'))):
    with patch('dadata_client.request.urlopen', side_effect=network_error) as urlopen, \
            patch('dadata_client.time.sleep') as sleep:
        try:
            find_party('token', inn='7707083893', retries=3)
        except DaDataError:
            pass
    require('постоянная TLS/proxy ошибка не повторяется',
            urlopen.call_count == 1 and not sleep.called)

sequence = [
    error.HTTPError('https://example.invalid', 429, 'limited', {}, BytesIO()),
    error.URLError(socket.timeout('timed out')),
    Response(),
]
with patch('dadata_client.request.urlopen', side_effect=sequence) as urlopen, \
        patch('dadata_client.time.sleep') as sleep:
    find_party('token', inn='7707083893', retries=2, backoff=0.25)
require('временные ошибки повторяются', urlopen.call_count == 3)
require('задержка повторов контролируема',
        [call.args[0] for call in sleep.call_args_list] == [0.25, 0.5])

with patch('dadata_client.request.urlopen',
           return_value=Response(payload=['not', 'an', 'object'])):
    try:
        find_party('token', inn='7707083893', retries=0)
    except DaDataError as exc:
        require('необъектный JSON классифицирован безопасно',
                exc.category == 'invalid_response')

for malformed in ({'suggestions': [None]},
                  {'suggestions': 'not-a-list'},
                  {'suggestions': [{'data': 'not-an-object'}]},
                  {'suggestions': {}},
                  {'suggestions': ''},
                  {'suggestions': None},
                  {},
                  {'suggestions': [{'data': []}]},
                  {'suggestions': [{'data': None}]}):
    with patch('dadata_client.request.urlopen',
               return_value=Response(payload=malformed)):
        try:
            find_party('token', inn='7707083893', retries=0)
        except DaDataError as exc:
            require('неверная схема suggestions классифицирована безопасно',
                    exc.category == 'invalid_response')

with patch('dadata_client.request.urlopen', return_value=Response()):
    raw, _ = find_party(
        'token', inn='7707083893',
        diagnostic=lambda _event: (_ for _ in ()).throw(RuntimeError('log failed')))
require('сбой журналирования не ломает основной запрос', raw == PARTY)

with patch.dict('os.environ', {'REQUESTS_CA_BUNDLE': '/missing/ca.pem'}, clear=False), \
        patch('dadata_client.ssl.create_default_context',
              side_effect=FileNotFoundError('missing')):
    try:
        find_party('token', inn='7707083893', retries=2)
    except DaDataError as exc:
        require('ошибка загрузки CA классифицирована как TLS', exc.category == 'tls')

with patch.dict('os.environ', {'REQUESTS_CA_BUNDLE': '/company/ca.pem'}, clear=False), \
        patch('dadata_client.ssl.create_default_context') as context, \
        patch('dadata_client.request.build_opener') as build:
    _default_opener()
require('корпоративный CA bundle применяется', context.call_args.kwargs['cafile']
        == '/company/ca.pem')
require('для CA и стандартного proxy собран opener', build.called)

print('ВСЕ ТЕСТЫ ТРАНСПОРТА DADATA ПРОЙДЕНЫ')