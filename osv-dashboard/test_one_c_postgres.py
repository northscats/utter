# -*- coding: utf-8 -*-
"""PostgreSQL-only concurrency regressions in a disposable private schema."""
import base64
import atexit
import json
import os
import re
import sys
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_ORIGINAL_DATABASE_URL = os.environ.get('DATABASE_URL', '')
_ENV = os.environ.get('ONE_C_ENV', '').strip().lower()
_DEPLOYED = os.environ.get('REPLIT_DEPLOYMENT', '').strip().lower()
_SCHEMA = 'one_c_test_' + uuid.uuid4().hex
_SAFE_SCHEMA = bool(re.fullmatch(r'one_c_test_[0-9a-f]{32}', _SCHEMA))
_BOOTSTRAP = None
_ISOLATED_URL = ''
_SKIP_REASON = ''
_SCHEMA_CREATED = False
_SCHEMA_DROPPED = False

try:
    _URL = make_url(_ORIGINAL_DATABASE_URL) if _ORIGINAL_DATABASE_URL else None
    if _DEPLOYED in ('1', 'true'):
        _SKIP_REASON = 'production deployment is never eligible'
    elif _ENV not in ('development', 'test'):
        _SKIP_REASON = 'ONE_C_ENV must explicitly designate development/test'
    elif _URL is None or not _URL.drivername.startswith('postgresql'):
        _SKIP_REASON = 'an explicit development PostgreSQL DATABASE_URL is required'
    elif not _SAFE_SCHEMA:
        _SKIP_REASON = 'generated schema failed safety validation'
    else:
        _BOOTSTRAP = create_engine(_URL, hide_parameters=True)
        with _BOOTSTRAP.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{_SCHEMA}"'))
        _SCHEMA_CREATED = True
        _SCOPED_URL = _URL.update_query_dict({
            'options': f'-csearch_path={_SCHEMA}',
        })
        # Kept in memory only. Never log or include this value in failures.
        _ISOLATED_URL = _SCOPED_URL.render_as_string(hide_password=False)
        os.environ['DATABASE_URL'] = _ISOLATED_URL
except Exception:
    _SKIP_REASON = 'private PostgreSQL schema could not be created'
    if _BOOTSTRAP is not None:
        if _SCHEMA_CREATED and _SAFE_SCHEMA:
            try:
                with _BOOTSTRAP.begin() as connection:
                    connection.execute(text(
                        f'DROP SCHEMA "{_SCHEMA}" CASCADE'))
                _SCHEMA_DROPPED = True
            except Exception:
                pass
        _BOOTSTRAP.dispose()
        _BOOTSTRAP = None


def _cleanup_private_schema(raise_on_error=False):
    global _SCHEMA_DROPPED
    if not _SCHEMA_CREATED or _SCHEMA_DROPPED:
        return
    try:
        if not _SAFE_SCHEMA:
            raise RuntimeError('unsafe generated schema')
        cleanup = create_engine(_ORIGINAL_DATABASE_URL, hide_parameters=True)
        try:
            with cleanup.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{_SCHEMA}" CASCADE'))
            _SCHEMA_DROPPED = True
        finally:
            cleanup.dispose()
    except Exception:
        if raise_on_error:
            raise AssertionError('private PostgreSQL schema cleanup failed')


atexit.register(_cleanup_private_schema)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if not _SKIP_REASON:
    import one_c
    from app import app
    from database import (Company, OneCAttempt, OneCChannel, OneCIdempotency,
                          User, db)
    from werkzeug.security import generate_password_hash

    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        ONE_C_ENV='test',
        ONE_C_PUBLIC_BASE_URL='https://onec.test',
    )


def _envelope(rows):
    return json.dumps({
        'ErrorCode': 0, 'ErrorDescription': '', 'Result': rows,
    }, ensure_ascii=False, separators=(',', ':')).encode()


def _basic(login, password):
    encoded = base64.b64encode(f'{login}:{password}'.encode()).decode()
    return {'Authorization': 'Basic ' + encoded}


@unittest.skipIf(bool(_SKIP_REASON), _SKIP_REASON or 'unavailable')
class OneCPostgresConcurrencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imap_patch = patch('app._ensure_imap_thread')
        cls.imap_patch.start()
        with app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cls.imap_patch.stop()

    def setUp(self):
        one_c._preauth.clear()
        one_c._preauth_global.clear()
        with app.app_context():
            db.session.remove()
            for model in (OneCIdempotency, OneCAttempt, OneCChannel, Company,
                          User):
                model.query.delete(synchronize_session=False)
            db.session.commit()
            admin = User(username='pg-admin',
                         password_hash=generate_password_hash('pass'),
                         role='admin')
            db.session.add(admin)
            db.session.flush()
            company = Company(name='PG concurrent company',
                              inn='5007091951', status='active',
                              created_by=admin.id)
            db.session.add(company)
            db.session.commit()
            self.admin_id = admin.id
            self.company_id = company.id

    def login(self, client):
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
            session['password_version'] = 1

    def create_channel(self):
        client = app.test_client()
        self.login(client)
        response = client.post(
            '/settings/1c/channels', json={'company_id': self.company_id},
            base_url='https://localhost')
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        return payload['channel'], payload['password']

    def push(self, channel, password, body, idem=None):
        headers = _basic(channel['login'], password)
        if idem:
            headers['Idempotency-Key'] = idem
        return app.test_client().post(
            f"/api/1c/push/{channel['id']}", data=body, headers=headers,
            content_type='application/json', base_url='https://onec.test')

    def test_two_simultaneous_channel_creates_make_exactly_one(self):
        gate = threading.Barrier(2)

        def create():
            client = app.test_client()
            self.login(client)
            gate.wait(timeout=5)
            return client.post(
                '/settings/1c/channels', json={'company_id': self.company_id},
                base_url='https://localhost').status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            statuses = sorted(future.result(timeout=8) for future in futures)
        self.assertEqual(statuses, [201, 409])
        with app.app_context():
            self.assertEqual(OneCChannel.query.filter_by(
                company_id=self.company_id).count(), 1)

    def test_parallel_identical_idempotency_has_one_receipt(self):
        channel, password = self.create_channel()
        body = _envelope([{'ОрганизацияИНН': '5007091951', 'value': 1}])
        gate = threading.Barrier(2)

        def send():
            gate.wait(timeout=5)
            return self.push(channel, password, body, 'parallel-same')

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: send(), range(2)))
        for index, response in enumerate(responses):
            if response.status_code == 503:
                responses[index] = self.push(
                    channel, password, body, 'parallel-same')
        self.assertEqual([item.status_code for item in responses], [200, 200])
        payloads = [item.get_json()['Result'] for item in responses]
        self.assertEqual(len({item['receipt_id'] for item in payloads}), 1)
        self.assertEqual(sorted(item['duplicate'] for item in payloads),
                         [False, True])
        with app.app_context():
            self.assertEqual(OneCIdempotency.query.filter_by(
                channel_id=channel['id'], key='parallel-same').count(), 1)

    def test_parallel_different_packages_store_one_exact_whole_snapshot(self):
        channel, password = self.create_channel()
        bodies = [
            _envelope([{'ОрганизацияИНН': '5007091951', 'packet': 'A'}] * 7),
            _envelope([{'ОрганизацияИНН': '5007091951', 'packet': 'B'}] * 11),
        ]
        gate = threading.Barrier(2)

        def send(body):
            gate.wait(timeout=5)
            return body, self.push(channel, password, body)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, bodies))
        self.assertEqual([response.status_code for _, response in results],
                         [200, 200])
        receipt_to_body = {
            response.get_json()['Result']['receipt_id']: body.decode()
            for body, response in results
        }
        with app.app_context():
            db.session.remove()
            saved = db.session.get(OneCChannel, channel['id'])
            self.assertIn(saved.last_receipt_id, receipt_to_body)
            self.assertEqual(saved.last_raw_json,
                             receipt_to_body[saved.last_receipt_id])
            parsed = json.loads(saved.last_raw_json)
            markers = {row['packet'] for row in parsed['Result']}
            self.assertEqual(len(markers), 1)

    def test_rotation_and_disable_win_before_waiting_old_push(self):
        channel, old_password = self.create_channel()
        body = _envelope([{'ОрганизацияИНН': '5007091951'}])
        entered = threading.Event()
        release = threading.Event()
        original_hash = one_c.generate_password_hash

        def blocked_hash(value):
            entered.set()
            self.assertTrue(release.wait(5))
            return original_hash(value)

        admin = app.test_client()
        self.login(admin)
        with patch('one_c.generate_password_hash', side_effect=blocked_hash):
            with ThreadPoolExecutor(max_workers=2) as pool:
                rotate_future = pool.submit(
                    admin.post,
                    f"/settings/1c/channels/{channel['id']}/rotate",
                    base_url='https://localhost')
                self.assertTrue(entered.wait(5))
                push_future = pool.submit(
                    self.push, channel, old_password, body)
                release.set()
                self.assertEqual(rotate_future.result(timeout=8).status_code, 200)
                old_push = push_future.result(timeout=8)
        self.assertEqual(old_push.status_code, 401)

        entered.clear()
        release.clear()

        def blocked_environment():
            entered.set()
            self.assertTrue(release.wait(5))
            return False

        with patch('one_c.is_production', side_effect=blocked_environment):
            with ThreadPoolExecutor(max_workers=2) as pool:
                disable_future = pool.submit(
                    admin.post,
                    f"/settings/1c/channels/{channel['id']}",
                    json={'enabled': False, 'allow_http': True},
                    base_url='https://localhost')
                self.assertTrue(entered.wait(5))
                new_password = rotate_future.result().get_json()['password']
                push_future = pool.submit(
                    self.push, channel, new_password, body)
                release.set()
                self.assertEqual(disable_future.result(timeout=8).status_code, 200)
                disabled_push = push_future.result(timeout=8)
        self.assertEqual(disabled_push.status_code, 403)

    def test_busy_is_immediate_and_fresh_engine_reads_committed_snapshot(self):
        channel, password = self.create_channel()
        self.assertTrue(one_c._heavy_slots.acquire(blocking=False))
        self.assertTrue(one_c._heavy_slots.acquire(blocking=False))
        started = time.monotonic()
        try:
            response = self.push(channel, password, _envelope([]))
        finally:
            one_c._heavy_slots.release()
            one_c._heavy_slots.release()
        self.assertEqual(response.status_code, 503)
        self.assertLess(time.monotonic() - started, 2)

        accepted = self.push(
            channel, password,
            _envelope([{'ОрганизацияИНН': '5007091951', 'fresh': True}]))
        self.assertEqual(accepted.status_code, 200)
        receipt = accepted.get_json()['Result']['receipt_id']
        fresh_engine = create_engine(_ISOLATED_URL, hide_parameters=True)
        try:
            with fresh_engine.connect() as connection:
                row = connection.execute(text(
                    'SELECT last_receipt_id, last_raw_json '
                    'FROM one_c_channels WHERE id=:channel_id'),
                    {'channel_id': channel['id']}).one()
            self.assertEqual(row.last_receipt_id, receipt)
            self.assertTrue(json.loads(row.last_raw_json)['Result'][0]['fresh'])
        finally:
            fresh_engine.dispose()


def tearDownModule():
    if _BOOTSTRAP is not None:
        _BOOTSTRAP.dispose()
    _cleanup_private_schema(raise_on_error=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)