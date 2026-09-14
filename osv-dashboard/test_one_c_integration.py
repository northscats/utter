# -*- coding: utf-8 -*-
"""Изолированные интеграционные регрессии пилотного входящего канала 1С."""
import base64
import json
import os
import re
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix='one_c_integration_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.pop('REPLIT_DEPLOYMENT', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import one_c
from app import app, init_db
from database import (Company, OneCAttempt, OneCChannel, OneCIdempotency, User,
                      db)
from werkzeug.security import check_password_hash, generate_password_hash

app.config.update(
    TESTING=True,
    WTF_CSRF_ENABLED=True,
    ONE_C_ENV='test',
    ONE_C_PUBLIC_BASE_URL='https://onec.test',
    ONE_C_TRUSTED_PROXY_CIDRS='127.0.0.0/8',
)
init_db(perform_durable=False)


def envelope(rows, description=''):
    return json.dumps({
        'ErrorCode': 0, 'ErrorDescription': description, 'Result': rows,
    }, ensure_ascii=False, separators=(',', ':')).encode()


def basic(login, password):
    value = base64.b64encode(f'{login}:{password}'.encode()).decode()
    return {'Authorization': 'Basic ' + value}


class OneCIntegrationTest(unittest.TestCase):
    def test_near_limit_single_cell_remains_fully_viewable(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        value = 'Я' * ((one_c.MAX_BODY - 2048) // 2)
        raw = envelope([{'ОрганизацияИНН': '5007091951', 'Большое поле': value}])
        self.assertLess(len(raw), one_c.MAX_BODY)
        response = self.push(channel, password, raw)
        self.assertEqual(response.status_code, 200)
        client = app.test_client()
        self.login_as(client, self.admin_id)
        response = client.get(
            f"/settings/1c/channels/{channel['id']}/data?column_offset=1&column_limit=1",
            base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['rows'][0][0]['text'] == value)

    @classmethod
    def setUpClass(cls):
        cls.imap_patch = patch('app._ensure_imap_thread')
        cls.imap_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.imap_patch.stop()

    def setUp(self):
        one_c._preauth.clear()
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()
            admin = User(username='onec-admin',
                         password_hash=generate_password_hash('admin-pass'),
                         role='admin')
            ordinary = User(username='onec-user',
                            password_hash=generate_password_hash('user-pass'),
                            role='user')
            db.session.add_all([admin, ordinary])
            db.session.flush()
            self.admin_id, self.user_id = admin.id, ordinary.id
            db.session.commit()

    def login_as(self, client, user_id):
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['password_version'] = 1

    def csrf(self, client):
        page = client.get('/settings', base_url='https://localhost')
        self.assertEqual(page.status_code, 200)
        match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def create_company(self, name='Канал Тест', inn='5007091951',
                       status='active'):
        with app.app_context():
            company = Company(name=name, inn=inn, status=status,
                              created_by=self.admin_id)
            db.session.add(company)
            db.session.commit()
            return company.id

    def create_channel(self, company_id, client=None):
        client = client or app.test_client()
        self.login_as(client, self.admin_id)
        token = self.csrf(client)
        response = client.post(
            '/settings/1c/channels',
            json={'company_id': company_id},
            headers={'X-CSRFToken': token, 'Referer': 'https://localhost/settings'},
            base_url='https://localhost')
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        payload = response.get_json()
        return payload['channel'], payload['password']

    def push(self, channel, password, body, **kwargs):
        headers = basic(channel['login'], password)
        headers.update(kwargs.pop('headers', {}))
        kwargs.setdefault('content_type', 'application/json')
        return app.test_client().post(
            f"/api/1c/push/{channel['id']}", data=body, headers=headers,
            base_url=kwargs.pop('base_url', 'https://onec.test'), **kwargs)

    def test_admin_auth_csrf_create_secret_hash_url_and_rotation(self):
        company_id = self.create_company()
        anonymous = app.test_client()
        self.assertEqual(
            anonymous.get('/settings/1c/channels',
                          base_url='https://localhost').status_code, 401)
        self.login_as(anonymous, self.user_id)
        self.assertEqual(
            anonymous.get('/settings/1c/channels',
                          base_url='https://localhost').status_code, 403)

        admin = app.test_client()
        self.login_as(admin, self.admin_id)
        self.assertEqual(admin.post(
            '/settings/1c/channels', json={'company_id': company_id},
            base_url='https://localhost').status_code, 403)
        token = self.csrf(admin)
        created = admin.post(
            '/settings/1c/channels', json={'company_id': company_id},
            headers={'X-CSRFToken': token, 'Referer': 'https://localhost/settings'}, base_url='https://localhost')
        self.assertEqual(created.status_code, 201)
        result = created.get_json()
        channel, password = result['channel'], result['password']
        self.assertEqual(channel['url'],
                         f"https://onec.test/api/1c/push/{channel['id']}")
        self.assertNotIn('password', channel)
        with app.app_context():
            model = db.session.get(OneCChannel, channel['id'])
            self.assertNotEqual(model.password_hash, password)
            self.assertTrue(check_password_hash(model.password_hash, password))

        listing = admin.get('/settings/1c/channels',
                            base_url='https://localhost').get_json()
        self.assertEqual(listing['channels'][0]['url'], channel['url'])
        self.assertNotIn('password', listing['channels'][0])
        rotated = admin.post(
            f"/settings/1c/channels/{channel['id']}/rotate",
            headers={'X-CSRFToken': token, 'Referer': 'https://localhost/settings'}, base_url='https://localhost')
        self.assertEqual(rotated.status_code, 200)
        new_password = rotated.get_json()['password']
        body = envelope([{'ОрганизацияИНН': '5007091951'}])
        self.assertEqual(self.push(channel, password, body).status_code, 401)
        self.assertEqual(self.push(channel, new_password, body).status_code, 200)

    def test_only_one_channel_can_be_created_under_parallel_requests(self):
        company_id = self.create_company('Параллельный канал')

        def create(_):
            client = app.test_client()
            self.login_as(client, self.admin_id)
            token = self.csrf(client)
            return client.post(
                '/settings/1c/channels', json={'company_id': company_id},
                headers={'X-CSRFToken': token, 'Referer': 'https://localhost/settings'},
                base_url='https://localhost').status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(create, range(2)))
        self.assertEqual(statuses, [201, 409])
        with app.app_context():
            self.assertEqual(
                OneCChannel.query.filter_by(company_id=company_id).count(), 1)

    def test_channel_state_company_state_and_wrong_credentials(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        body = envelope([{'ОрганизацияИНН': '5007091951'}])
        self.assertEqual(
            self.push(channel, 'wrong-password', body).status_code, 401)
        self.assertEqual(app.test_client().post(
            f"/api/1c/push/{channel['id']}", data=body,
            base_url='https://onec.test').status_code, 401)

        client = app.test_client()
        self.login_as(client, self.admin_id)
        token = self.csrf(client)
        disabled = client.post(
            f"/settings/1c/channels/{channel['id']}", json={'enabled': False},
            headers={'X-CSRFToken': token, 'Referer': 'https://localhost/settings'}, base_url='https://localhost')
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(self.push(channel, password, body).status_code, 403)
        with app.app_context():
            model = db.session.get(OneCChannel, channel['id'])
            model.enabled = True
            model.company.status = 'archived'
            db.session.commit()
        self.assertEqual(self.push(channel, password, body).status_code, 409)

    def test_sample_prefix_has_49_rows_six_inns_split_accepts_mixed_rejects(self):
        sample_path = (Path(__file__).parent.parent / 'attached_assets' /
                       'Pasted--ErrorCode-0-ErrorDescription-Result--1788866880332_1788866880334.txt')
        text = sample_path.read_text(encoding='utf-8')
        sample, end = json.JSONDecoder().raw_decode(text)
        self.assertTrue(text[end:].strip())
        self.assertEqual(len(sample['Result']), 49)
        packets = {}
        for row in sample['Result']:
            packets.setdefault(str(row['ОрганизацияИНН']), []).append(row)
        self.assertEqual(len(packets), 6)

        channels = []
        for index, (inn, rows) in enumerate(packets.items()):
            company_id = self.create_company(f'Предприятие {index}', inn)
            channel, password = self.create_channel(company_id)
            channels.append((channel, password))
            self.assertEqual(self.push(channel, password, envelope(rows)).status_code,
                             200)
        mixed = packets[next(iter(packets))][:1] + packets[list(packets)[1]][:1]
        self.assertEqual(
            self.push(*channels[0], envelope(mixed)).status_code, 400)
        self.assertEqual(
            self.push(*channels[0], text.encode('utf-8'),
                      content_type='text/plain').status_code, 415)

    def test_row_counts_empty_overwrite_persistence_and_failure_atomicity(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        for count in (0, 1, 49, 2000):
            rows = [{'ОрганизацияИНН': '5007091951', 'Номер': i}
                    for i in range(count)]
            response = self.push(channel, password, envelope(rows))
            self.assertEqual(response.status_code, 200)
            receipt = response.get_json()['Result']['receipt_id']
            with app.app_context():
                db.session.remove()
                persisted = db.session.get(OneCChannel, channel['id'])
                self.assertEqual(persisted.last_row_count, count)
                self.assertEqual(persisted.last_receipt_id, receipt)

        good = self.push(channel, password, envelope(
            [{'ОрганизацияИНН': '5007091951', 'stable': 'yes'}])).get_json()
        receipt = good['Result']['receipt_id']
        self.assertEqual(self.push(
            channel, password,
            envelope([{'ОрганизацияИНН': 'wrong'}])).status_code, 400)
        with app.app_context():
            db.session.remove()
            persisted = db.session.get(OneCChannel, channel['id'])
            self.assertEqual(persisted.last_receipt_id, receipt)
            self.assertIn('"stable":"yes"', persisted.last_raw_json)
        self.assertEqual(self.push(channel, password, envelope([])).status_code, 200)
        with app.app_context():
            db.session.remove()
            self.assertEqual(
                db.session.get(OneCChannel, channel['id']).last_row_count, 0)

    def test_data_pagination_columns_and_exact_safe_cells(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        rows = [
            {'ОрганизацияИНН': '5007091951', 'Аналитика10': 'ten',
             'big': 123456789012345678901234567890, 'null': None,
             'empty': '', 'nested': {'html': '<script>alert(1)</script>'}},
            {'ОрганизацияИНН': '5007091951', 'Аналитика2': 'two'},
            {'ОрганизацияИНН': '5007091951', 'Аналитика1': 'one'},
        ]
        rows.extend({'ОрганизацияИНН': '5007091951', f'Аналитика{i}': i}
                    for i in range(3, 105) if i != 4)
        rows.extend({'ОрганизацияИНН': '5007091951'} for _ in range(100))
        rows[-1]['ПоздняяКолонка'] = '<b>raw</b>'
        accepted = self.push(channel, password, envelope(rows)).get_json()
        receipt = accepted['Result']['receipt_id']

        admin = app.test_client()
        self.login_as(admin, self.admin_id)
        data = admin.get(
            f"/settings/1c/channels/{channel['id']}/data",
            query_string={'limit': 3, 'column_limit': 100,
                          'receipt_id': receipt},
            base_url='https://localhost').get_json()
        analytics = [c for c in data['columns'] if c.startswith('Аналитика')]
        self.assertEqual(analytics, sorted(analytics,
                                          key=lambda x: int(x[9:])))
        second_columns = admin.get(
            f"/settings/1c/channels/{channel['id']}/data",
            query_string={'limit': 3, 'column_offset': 100,
                          'column_limit': 100, 'receipt_id': receipt},
            base_url='https://localhost').get_json()
        by_column = dict(zip(data['columns'] + second_columns['columns'],
                             data['rows'][0] + second_columns['rows'][0]))
        self.assertEqual(by_column['big']['text'],
                         '123456789012345678901234567890')
        self.assertEqual(by_column['null'], {'kind': 'null', 'text': 'null'})
        self.assertEqual(by_column['empty'], {'kind': 'string', 'text': ''})
        self.assertEqual(by_column['nested']['kind'], 'json')
        self.assertIn('<script>', by_column['nested']['text'])
        second_row = dict(zip(
            data['columns'] + second_columns['columns'],
            data['rows'][1] + second_columns['rows'][1]))
        self.assertEqual(second_row['big'],
                         {'kind': 'missing', 'text': ''})
        page2 = admin.get(
            f"/settings/1c/channels/{channel['id']}/data",
            query_string={'offset': 200, 'limit': 10, 'column_offset': 100,
                          'column_limit': 100},
            base_url='https://localhost').get_json()
        self.assertIn('ПоздняяКолонка', page2['columns'])
        self.assertTrue(any(cell['text'] == '<b>raw</b>'
                            for row in page2['rows'] for cell in row))

    def test_analytics_cardinality_zero_three_and_over_one_hundred(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        admin = app.test_client()
        self.login_as(admin, self.admin_id)
        cases = (
            [],
            ['Аналитика10', 'Аналитика2', 'Аналитика1'],
            [f'Аналитика{i}' for i in range(1, 106) if i != 9],
        )
        for expected_names in cases:
            row = {'ОрганизацияИНН': '5007091951', 'Обычная': 'x'}
            row.update({name: name for name in reversed(expected_names)})
            accepted = self.push(channel, password, envelope([row]))
            self.assertEqual(accepted.status_code, 200)
            receipt = accepted.get_json()['Result']['receipt_id']
            columns = []
            for offset in (0, 100):
                page = admin.get(
                    f"/settings/1c/channels/{channel['id']}/data",
                    query_string={'column_offset': offset, 'column_limit': 100,
                                  'receipt_id': receipt},
                    base_url='https://localhost').get_json()
                columns.extend(page['columns'])
            actual = [name for name in columns if name.startswith('Аналитика')]
            self.assertEqual(
                actual,
                sorted(expected_names, key=lambda name: int(name[9:])))
            self.assertNotIn('Аналитика9', actual
                             if len(expected_names) > 100 else [])

    def test_strict_json_content_type_size_depth_and_busy_throttle(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        malformed = [
            b'\xef\xbb\xbf{"ErrorCode":0,"ErrorDescription":"","Result":[]}',
            b' {"ErrorCode":0,"ErrorDescription":"","Result":[]}',
            b'{"ErrorCode":0,"ErrorCode":0,"ErrorDescription":"","Result":[]}',
            b'{"ErrorCode":0,"ErrorDescription":"","Result":[]}\ntrailing',
            b'{"ErrorCode":0,"ErrorDescription":"","Result":["\xff"]}',
        ]
        expected = [400, 200, 400, 400, 400]
        for body, status in zip(malformed, expected):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.push(
                    channel, password, body,
                    content_type='application/json').status_code, status)
        self.assertEqual(self.push(
            channel, password, envelope([]),
            content_type='text/plain').status_code, 415)
        too_deep = '{"ОрганизацияИНН":"5007091951","x":' + (
            '[' * 40 + '0' + ']' * 40) + '}'
        deep_body = ('{"ErrorCode":0,"ErrorDescription":"","Result":[' +
                     too_deep + ']}').encode()
        self.assertEqual(self.push(channel, password, deep_body).status_code, 400)
        self.assertEqual(self.push(
            channel, password, b'x' * (one_c.MAX_BODY + 1)).status_code, 413)
        self.assertTrue(one_c._heavy_slots.acquire(blocking=False))
        self.assertTrue(one_c._heavy_slots.acquire(blocking=False))
        try:
            self.assertEqual(self.push(
                channel, password, envelope([])).status_code, 503)
        finally:
            one_c._heavy_slots.release()
            one_c._heavy_slots.release()

    def test_idempotency_conflict_duplicate_journal_is_safe(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        headers = {'Idempotency-Key': 'same-key'}
        body = envelope([{'ОрганизацияИНН': '5007091951', 'secret': 'RAW-SECRET'}])
        first = self.push(channel, password, body, headers=headers)
        duplicate = self.push(channel, password, body, headers=headers)
        conflict = self.push(channel, password, envelope(
            [{'ОрганизацияИНН': '5007091951', 'secret': 'OTHER'}]),
            headers=headers)
        self.assertEqual([first.status_code, duplicate.status_code,
                          conflict.status_code], [200, 200, 409])
        self.assertFalse(first.get_json()['Result']['duplicate'])
        self.assertTrue(duplicate.get_json()['Result']['duplicate'])
        self.assertEqual(first.get_json()['Result']['receipt_id'],
                         duplicate.get_json()['Result']['receipt_id'])
        with app.app_context():
            db.session.remove()
            attempts = OneCAttempt.query.filter_by(
                channel_id=channel['id']).order_by(OneCAttempt.id).all()
            self.assertEqual([a.result for a in attempts],
                             ['accepted', 'duplicate', 'conflict'])
            journal = ' '.join(a.error or '' for a in attempts)
            self.assertNotIn('RAW-SECRET', journal)
            self.assertNotIn(password, journal)
            self.assertEqual(OneCIdempotency.query.filter_by(
                channel_id=channel['id']).count(), 1)

    def test_timeout_and_both_request_throttles_are_machine_errors(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        body = envelope([{'ОрганизацияИНН': '5007091951'}])
        with patch('one_c.time.monotonic', side_effect=[100, 100, 111]):
            timed_out = self.push(channel, password, body)
        self.assertEqual(timed_out.status_code, 503)
        self.assertEqual(timed_out.get_json()['ErrorCode'], 503)

        with app.app_context():
            db.session.add_all([
                OneCAttempt(channel_id=channel['id'], result='accepted',
                            transport='https')
                for _ in range(60)
            ])
            db.session.commit()
        self.assertEqual(self.push(channel, password, body).status_code, 429)

        one_c._preauth['127.0.0.1'].extend([one_c.time.monotonic()] * 300)
        global_throttle = self.push(channel, password, body)
        self.assertEqual(global_throttle.status_code, 429)
        self.assertEqual(global_throttle.get_json()['ErrorCode'], 429)

    def test_journal_records_rejected_authenticated_and_credential_attempts(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        self.assertEqual(self.push(
            channel, 'incorrect-secret', envelope([])).status_code, 401)
        self.assertEqual(self.push(
            channel, password, b'not-json').status_code, 400)
        self.assertEqual(self.push(
            channel, password, envelope([])).status_code, 200)
        with app.app_context():
            attempts = OneCAttempt.query.filter_by(
                channel_id=channel['id']).order_by(OneCAttempt.id).all()
            self.assertEqual(len(attempts), 3)
            serialized = ' '.join(
                f'{item.result} {item.error}' for item in attempts)
            self.assertNotIn('incorrect-secret', serialized)
            self.assertNotIn(password, serialized)

    def test_wrong_credentials_do_not_exhaust_authenticated_channel_quota(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        body = envelope([])
        for _ in range(60):
            self.assertEqual(
                self.push(channel, 'wrong-password', body).status_code, 401)
        valid = self.push(channel, password, body)
        self.assertEqual(valid.status_code, 200)

    def test_transport_production_override_and_spoof_rejection(self):
        company_id = self.create_company()
        channel, password = self.create_channel(company_id)
        body = envelope([{'ОрганизацияИНН': '5007091951'}])
        self.assertEqual(self.push(
            channel, password, body, base_url='http://onec.test',
            environ_overrides={'REMOTE_ADDR': '203.0.113.10'},
            headers={'X-Forwarded-Proto': 'https'}).status_code, 403)
        self.assertEqual(self.push(
            channel, password, body, base_url='http://onec.test',
            environ_overrides={'REMOTE_ADDR': '127.0.0.1'},
            headers={'X-Forwarded-Proto': 'https'}).status_code, 200)

        client = app.test_client()
        self.login_as(client, self.admin_id)
        token = self.csrf(client)
        with patch.dict(os.environ, {'REPLIT_DEPLOYMENT': 'true'}):
            self.assertEqual(client.post(
                f"/settings/1c/channels/{channel['id']}",
                json={'allow_http': True}, headers={'X-CSRFToken': token, 'Referer': 'https://localhost/settings'},
                base_url='https://localhost').status_code, 409)
            self.assertEqual(self.push(
                channel, password, body, base_url='http://onec.test',
                environ_overrides={'REMOTE_ADDR': '127.0.0.1'},
                headers={'X-Forwarded-Proto': 'https'}).status_code, 200)
            self.assertEqual(self.push(
                channel, password, body, base_url='http://onec.test',
                environ_overrides={'REMOTE_ADDR': '127.0.0.1'}).status_code, 403)

    def test_full_company_deletion_cascades_channel_history_and_keys(self):
        company_id = self.create_company('Удаляемый канал')
        channel, password = self.create_channel(company_id)
        self.assertEqual(self.push(
            channel, password,
            envelope([{'ОрганизацияИНН': '5007091951'}]),
            headers={'Idempotency-Key': 'delete-me'}).status_code, 200)
        with app.app_context():
            db.session.get(Company, company_id).status = 'archived'
            db.session.commit()
        client = app.test_client()
        self.login_as(client, self.admin_id)
        token = self.csrf(client)
        response = client.post(
            f'/directories/companies/{company_id}/delete',
            data={'confirmation_name': 'Удаляемый канал', 'csrf_token': token},
            headers={'Referer': 'https://localhost/settings'},
            base_url='https://localhost')
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            db.session.remove()
            self.assertIsNone(db.session.get(Company, company_id))
            self.assertIsNone(db.session.get(OneCChannel, channel['id']))
            self.assertEqual(OneCAttempt.query.filter_by(
                channel_id=channel['id']).count(), 0)
            self.assertEqual(OneCIdempotency.query.filter_by(
                channel_id=channel['id']).count(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)