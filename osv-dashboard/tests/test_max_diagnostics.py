"""Isolated MAX diagnostics: fake credentials, SQLite and fake transport only."""
import json
import ast
import os
import socket
import ssl
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from flask import Flask
from flask import abort, jsonify, g, request
from flask_login import LoginManager, UserMixin, current_user, login_required
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text

import max_bot_config
import max_limits as m
from database import db, MaxLimitsCursor


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(max_bot_config, 'CONFIG', Path(self.temp.name) / 'private' / 'max-bot.json'),
            patch.object(m, '_diagnostic_stages', {}),
            patch.object(m, '_diagnostic_generation', None),
            patch.object(m, '_worker_thread', None),
            patch.object(m, '_worker_generation', None),
            patch.object(m, '_connected_token', None),
            patch.object(m, '_connected_generation', None),
            patch.object(m, '_last_error', False),
            patch.dict(os.environ, {'MAX_LIMITS_WEBHOOK_URL': '',
                                   'MAX_LIMITS_REQUIRE_WEBHOOK': '', 'REPLIT_DEPLOYMENT': ''}),
        ]
        for p in self.patches:
            p.start()
        max_bot_config.save('Z' * 40, '-123')
        max_bot_config.set_enabled(True)
        self.config = max_bot_config.read()
        self.generation = self.config['generation']
        m._diagnostic_local.generation = self.generation
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False,
                               DURABLE_STARTUP_READY=True)
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        for p in reversed(self.patches):
            p.stop()
        m._diagnostic_local.generation = None
        self.temp.cleanup()

    @staticmethod
    def transport(token, method, path, *args, **kwargs):
        if method != 'GET':
            raise AssertionError('Mutation forbidden')
        return {'/me': {'username': 'fake_bot', 'user_id': 7},
                '/chats/-123': {'chat_id': -123},
                '/chats/-123/members/admins': {'members': [{'user_id': 7}]},
                '/updates': {'updates': [{'fake': True}], 'marker': 9}}[path]

    def test_errors_are_allowlisted_and_recovery_replaces_error(self):
        cases = [(HTTPError('SECRET', 401, 'PII', {}, None), 'authorization', 401),
                 (HTTPError('SECRET', 403, 'PII', {}, None), 'forbidden', 403),
                 (HTTPError('SECRET', 429, 'PII', {}, None), 'rate_limit', 429),
                 (URLError(ssl.SSLCertVerificationError('SECRET')), 'tls', None),
                 (URLError(socket.gaierror('SECRET')), 'dns', None),
                 (TimeoutError('SECRET'), 'timeout', None),
                 (RuntimeError('<script>SECRET</script>'), 'operation', None)]
        for exc, category, code in cases:
            with self.assertLogs(m._logger, level='WARNING') as logs:
                with self.assertRaises(type(exc)), m.diagnostic_stage('polling', self.generation):
                    raise exc
            row = m.diagnostics()['stages'][0]
            self.assertEqual((row['error'], row['http_code']), (category, code))
            self.assertNotIn('SECRET', json.dumps(row) + str(logs.output))
        with m.diagnostic_stage('polling', self.generation):
            pass
        self.assertEqual(m.diagnostics()['stages'][0]['state'], 'ok')

    def test_probe_is_read_only_and_not_connection_confirmation(self):
        with patch.object(m, 'max_api', side_effect=self.transport) as api, \
                patch.object(m, 'start_max_limits_worker') as start, \
                patch.object(m, 'drain_outbox') as drain:
            checks = m.diagnostic_check(self.app)
        self.assertEqual([c.args[2] for c in api.call_args_list],
                         ['/me', '/chats/-123', '/chats/-123/members/admins'])
        self.assertEqual([r['state'] for r in checks[-3:]], ['ok'] * 3)
        start.assert_not_called()
        drain.assert_not_called()
        self.assertEqual(m.status(), 'connecting')
        self.assertEqual(m.diagnostics()['stages'], [])
        self.assertIsNone(db.session.get(MaxLimitsCursor, 1))

    def test_probe_failure_distinguishes_identity_group_admins(self):
        for failing in ['/me', '/chats/-123', '/chats/-123/members/admins']:
            def transport(token, method, path, *args, **kwargs):
                if path == failing:
                    raise HTTPError('SECRET', 403, 'SECRET', {}, None)
                return self.transport(token, method, path, *args, **kwargs)
            with patch.object(m, 'max_api', side_effect=transport):
                rows = m.diagnostic_check(self.app)
            self.assertEqual(rows[-1]['stage'], {
                '/me': 'identity', '/chats/-123': 'group',
                '/chats/-123/members/admins': 'admins'}[failing])
            self.assertEqual(rows[-1]['http_code'], 403)
            self.assertNotIn('SECRET', json.dumps(rows))

    def test_stale_generation_and_stop_hide_errors(self):
        with self.assertRaises(RuntimeError), m.diagnostic_stage('dispatch', self.generation):
            raise RuntimeError('SECRET')
        max_bot_config.set_enabled(False)
        self.assertEqual(m.diagnostics()['stages'], [])
        max_bot_config.set_enabled(True)
        with self.assertRaises(RuntimeError), m.diagnostic_stage('dispatch', self.generation):
            raise RuntimeError('stale')
        self.assertEqual(m.diagnostics()['stages'], [])
        self.assertEqual(m.status(), 'connecting')

    def test_polling_retries_event_without_cursor_then_recovers(self):
        active = [True]
        calls = [0]
        def dispatch(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('SECRET dispatch')
            active[0] = False
        def sleep(delay):
            self.assertIsNone(db.session.get(MaxLimitsCursor, 1))
        backup = Mock()
        with patch.object(m, '_generation_active', side_effect=lambda *a: active[0]), \
                patch.object(m, 'max_api', side_effect=self.transport), \
                patch.object(m, 'dispatch_update', side_effect=dispatch), \
                patch.object(m, 'drain_outbox'), \
                patch.object(m.time, 'sleep', side_effect=sleep), \
                patch.dict(sys.modules, {'app': types.SimpleNamespace(_schedule_durable_safety_backup=backup)}):
            m._worker_loop(self.app, self.config['token'], self.generation)
        self.assertEqual(calls[0], 2)
        self.assertEqual(db.session.get(MaxLimitsCursor, 1).marker, 9)
        rows = {r['stage']: r for r in m.diagnostics()['stages']}
        self.assertEqual(rows['dispatch']['state'], 'ok')
        self.assertEqual(rows['cursor_save']['state'], 'ok')

    def test_backup_does_not_mask_dispatch_error(self):
        active = [True]
        def transport(token, method, path, *args, **kwargs):
            result = self.transport(token, method, path, *args, **kwargs)
            if path == '/updates':
                result['updates'] *= 2
            return result
        dispatch = Mock(side_effect=[None, TimeoutError('SECRET dispatch')])
        def sleep(delay):
            active[0] = False
        backup = Mock(side_effect=RuntimeError('SECRET backup'))
        with patch.object(m, '_generation_active', side_effect=lambda *a: active[0]), \
                patch.object(m, 'max_api', side_effect=transport), \
                patch.object(m, 'dispatch_update', dispatch), \
                patch.object(m.time, 'sleep', side_effect=sleep), \
                patch.dict(sys.modules, {'app': types.SimpleNamespace(_schedule_durable_safety_backup=backup)}):
            m._worker_loop(self.app, self.config['token'], self.generation)
        rows = {r['stage']: r for r in m.diagnostics()['stages']}
        self.assertEqual(rows['dispatch']['error'], 'timeout')
        self.assertEqual(rows['backup']['error'], 'operation')
        self.assertIsNone(db.session.get(MaxLimitsCursor, 1))

    def test_webhook_registration_retry_is_not_inbound_confirmation(self):
        self.app.secret_key = 'isolated-test-key'
        active = [True]
        registrations = [0]
        def transport(token, method, path, *args, **kwargs):
            if path == '/subscriptions':
                registrations[0] += 1
                if registrations[0] == 1:
                    raise HTTPError('SECRET', 503, 'SECRET', {}, None)
                return {'success': True}
            return self.transport(token, method, path, *args, **kwargs)
        def sleep(delay):
            if registrations[0] == 2:
                active[0] = False
        with patch.dict(os.environ, {'SCRIPT_NAME': ''}), \
                patch.object(m, '_generation_active', side_effect=lambda *a: active[0]), \
                patch.object(m, 'max_api', side_effect=transport), \
                patch.object(m, 'drain_outbox'), \
                patch('max_registration.pending_moderation'), \
                patch.object(m.time, 'sleep', side_effect=sleep):
            m._webhook_outbox_loop(self.app, self.config['token'],
                                  'https://example.org/limit-notifications/max/webhook', self.generation)
        self.assertEqual(registrations[0], 2)
        rows = {r['stage']: r for r in m.diagnostics()['stages']}
        self.assertEqual(rows['webhook_registration']['state'], 'ok')
        self.assertFalse(m.diagnostics()['inbound_confirmed'])

    def test_invalid_config_and_ca_fail_closed_before_network(self):
        with patch.dict(os.environ, {'MAX_LIMITS_WEBHOOK_URL': 'http://example.org/invalid'}), \
                patch.object(m, 'max_api') as api:
            rows = m.diagnostic_check(self.app)
        api.assert_not_called()
        self.assertEqual(rows[0]['error'], 'configuration')
        with patch.object(m, 'TRUSTED_CA', Path(self.temp.name) / 'missing.pem'), \
                patch.object(m, 'max_api') as api:
            rows = m.diagnostic_check(self.app)
        api.assert_not_called()
        self.assertEqual(next(r for r in rows if r['stage'] == 'ca')['state'], 'error')

    def test_missing_admin_rights_and_readiness_are_explicit(self):
        def transport(token, method, path, *args, **kwargs):
            if path.endswith('/admins'):
                return {'members': []}
            return self.transport(token, method, path, *args, **kwargs)
        self.app.config['DURABLE_STARTUP_READY'] = False
        with patch.object(m, 'max_api', side_effect=transport):
            rows = m.diagnostic_check(self.app)
        self.assertEqual(rows[-1]['error'], 'admin_required')
        self.assertEqual(next(r for r in rows if r['stage'] == 'readiness')['error'], 'not_ready')

    def install_routes(self):
        """Compile only production routes, never import/start the production app."""
        source = (Path(__file__).resolve().parents[1] / 'app.py').read_text()
        tree = ast.parse(source)
        routes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name in {'max_limits_status', 'max_limits_diagnostics', 'max_limits_webhook'}]
        self.app.secret_key = 'isolated-test-key'
        manager = LoginManager(self.app)
        class User(UserMixin):
            id = 'test'
            is_admin = True
        user = User()
        manager.user_loader(lambda _: user)
        csrf = CSRFProtect(self.app)
        backup = Mock()
        namespace = dict(__name__='isolated_max_routes', app=self.app, csrf=csrf, login_required=login_required,
                         current_user=current_user, abort=abort, jsonify=jsonify,
                         g=g, request=request, os=os, db=db,
                         _schedule_durable_safety_backup=backup)
        exec(compile(ast.Module(body=routes, type_ignores=[]), '<isolated-max-routes>', 'exec'), namespace)
        @self.app.get('/test-csrf')
        def test_csrf():
            return generate_csrf()
        return user, backup

    def test_routes_require_admin_csrf_and_status_does_not_probe(self):
        user, backup = self.install_routes()
        client = self.app.test_client()
        with patch.object(m, 'max_api', side_effect=self.transport) as api:
            self.assertEqual(client.get('/limit-notifications/max/status').status_code, 401)
            with client.session_transaction() as session:
                session['_user_id'] = 'test'
                session['_fresh'] = True
            g.pop('_login_user', None)
            csrf = client.get('/test-csrf').text
            user.is_admin = False
            self.assertEqual(client.get('/limit-notifications/max/status').status_code, 403)
            self.assertEqual(client.post('/limit-notifications/max/diagnostics',
                                        data={'csrf_token': csrf}).status_code, 403)
            user.is_admin = True
            self.assertEqual(client.post('/limit-notifications/max/diagnostics').status_code, 400)
            response = client.get('/limit-notifications/max/status')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertIn('diagnostics', response.json)
            api.assert_not_called()
            response = client.post('/limit-notifications/max/diagnostics', data={'csrf_token': csrf})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual([c.args[2] for c in api.call_args_list],
                             ['/me', '/chats/-123', '/chats/-123/members/admins'])
        backup.assert_not_called()

    def test_webhook_route_distinguishes_dispatch_and_backup_failures(self):
        _, backup = self.install_routes()
        client = self.app.test_client()
        headers = {'X-Max-Bot-Api-Secret': m.webhook_secret(self.config['token'], self.app.secret_key)}
        with patch.dict(os.environ, {'MAX_LIMITS_WEBHOOK_URL': 'https://example.org/limit-notifications/max/webhook'}), \
                patch.object(m, 'dispatch_update', side_effect=TimeoutError('SECRET')):
            self.assertEqual(client.post('/limit-notifications/max/webhook',
                                        json={}, headers=headers).status_code, 503)
        backup.assert_not_called()
        rows = {r['stage']: r for r in m.diagnostics()['stages']}
        self.assertEqual(rows['webhook_dispatch']['error'], 'timeout')
        self.assertTrue(m.diagnostics()['inbound_confirmed'])
        backup.side_effect = RuntimeError('SECRET backup')
        with patch.dict(os.environ, {'MAX_LIMITS_WEBHOOK_URL': 'https://example.org/limit-notifications/max/webhook'}), \
                patch.object(m, 'dispatch_update'):
            self.assertEqual(client.post('/limit-notifications/max/webhook',
                                        json={}, headers=headers).status_code, 503)
        rows = {r['stage']: r for r in m.diagnostics()['stages']}
        self.assertEqual(rows['webhook_dispatch']['state'], 'ok')
        self.assertEqual(rows['webhook_backup']['error'], 'operation')
        backup.side_effect = None
        with patch.dict(os.environ, {'MAX_LIMITS_WEBHOOK_URL': 'https://example.org/limit-notifications/max/webhook'}), \
                patch.object(m, 'dispatch_update'):
            self.assertEqual(client.post('/limit-notifications/max/webhook',
                                        json={}, headers=headers).status_code, 200)
        rows = {r['stage']: r for r in m.diagnostics()['stages']}
        self.assertEqual(rows['webhook_backup']['state'], 'ok')

    def test_ui_escapes_diagnostic_values(self):
        root = Path(__file__).resolve().parents[1] / 'templates'
        source = (root / 'limits_max_bot.html').read_text()
        fragment = source[source.index('<pre id="max-diagnostic-stages"'):source.index('</pre>', source.index('<pre id="max-diagnostic-stages"')) + 6]
        rendered = Environment(autoescape=True, loader=FileSystemLoader(root)).from_string(fragment).render(
            bot_diagnostics={'stages': [dict(stage='<script>PII</script>', state='error', error='<img>', http_code=401)]})
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('.innerHTML', source)

    def test_timestamp_russian_labels_hints_and_retry_retains_primary_error(self):
        for category in ('configuration', 'tls', 'database', 'forbidden', 'rate_limit', 'operation'):
            row = m._diagnostic_entry('polling', 'error', category, 403 if category == 'forbidden' else None)
            self.assertRegex(row['timestamp'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
            self.assertEqual(row['stage_label'], 'Получение событий')
            self.assertEqual(row['state_label'], 'Ошибка')
            self.assertTrue(row['error_label'])
            self.assertTrue(row['hint'])
        with self.assertRaises(TimeoutError), m.diagnostic_stage('dispatch', self.generation):
            raise TimeoutError('SECRET')
        original = m.diagnostics()['stages'][0]
        with m.diagnostic_stage('dispatch', self.generation):
            retry = m.diagnostics()['stages'][0]
            self.assertEqual(retry['state'], 'error')
            self.assertEqual(retry['error'], 'timeout')
            self.assertEqual(retry['timestamp'], original['timestamp'])
            self.assertTrue(retry['retrying'])
            with self.assertRaises(RuntimeError), m.diagnostic_stage('backup', self.generation):
                raise RuntimeError('SECRET backup')
            self.assertEqual(m.diagnostics()['stages'][0]['error'], 'timeout')
        recovered = m.diagnostics()['stages'][0]
        self.assertEqual(recovered['state'], 'ok')
        self.assertFalse(recovered['retrying'])
        self.assertIsNone(recovered['error'])

    def test_readiness_detects_missing_max_table_and_column_and_rolls_back(self):
        table = db.metadata.tables['max_limits_profiles']
        for broken_schema in (
                'ALTER TABLE max_limits_profiles DROP COLUMN phone',
                'DROP TABLE max_limits_profiles'):
            db.session.execute(text(broken_schema))
            db.session.commit()
            with patch.object(m, 'max_api', side_effect=self.transport), \
                    patch.object(db.session, 'rollback', wraps=db.session.rollback) as rollback:
                checks = m.diagnostic_check(self.app)
                rollback.assert_called_once()
            readiness = next(row for row in checks if row['stage'] == 'readiness')
            self.assertEqual(readiness['error'], 'database')
            self.assertEqual(db.session.execute(text('SELECT 1')).scalar(), 1)
            db.session.rollback()
            table.drop(bind=db.engine, checkfirst=True)
            table.create(bind=db.engine)
            with patch.object(m, 'max_api', side_effect=self.transport):
                checks = m.diagnostic_check(self.app)
            self.assertEqual(next(row for row in checks if row['stage'] == 'readiness')['state'], 'ok')

    def test_russian_diagnostic_template_shape_and_escaping(self):
        source = (Path(__file__).resolve().parents[1] / 'templates' / 'limits_max_bot.html').read_text()
        start = source.index('<pre id="max-diagnostic-stages"')
        fragment = source[start:source.index('</pre>', start) + 6]
        row = m._diagnostic_entry('admins', 'error', 'forbidden', 403)
        row['hint'] += '<script>SECRET</script>'
        row['retrying'] = True
        rendered = Environment(autoescape=True).from_string(fragment).render(bot_diagnostics={'stages': [row]})
        for phrase in ('Права администратора', 'Ошибка', 'Что делать:', 'повторная попытка',
                       'UTC', 'admins: error', 'forbidden', 'HTTP 403'):
            self.assertIn(phrase, rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)


if __name__ == '__main__':
    unittest.main()