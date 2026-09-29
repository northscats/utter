"""Routes for financial limits and the separate MAX bot surface."""
import inspect
import io
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='limits_routes_')
os.environ['DATABASE_URL'] = 'sqlite:///' + _TEMP.name + '/routes.db'
os.environ['INITIAL_ADMIN_PASSWORD'] = 'copper meadow river lantern 2026'
os.environ.setdefault('ENCRYPTION_KEY', 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.pop('DURABLE_BACKUP_DIR', None)

from app import (app, init_db, _ensure_limits_recovery,
                 _limits_history_ready, _sync_daily_reconciliation_alerts)
from database import (db, User, UserModuleAccess, Setting, LimitDataset,
                      LimitObject, LimitVersion, LimitComparison, LimitChange)
from limits_views import objects_context, object_context
from database import (MaxLimitsProfile, MaxLimitsUin, MaxLimitsApplication,
                      MaxLimitsOutbox, MaxLimitsSubscription)
import max_bot_config
from pathlib import Path


class LimitsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls._tombstone_transport = patch('durable_data.record_limit_tombstone',
                                         return_value=[])
        cls._tombstone_transport.start()
        cls.addClassCleanup(cls._tombstone_transport.stop)
        init_db()

    def setUp(self):
        self.bot_temp = tempfile.TemporaryDirectory(prefix='max-bot-route-')
        self.addCleanup(self.bot_temp.cleanup)
        config_patch = patch.object(
            max_bot_config, 'CONFIG', Path(self.bot_temp.name) / 'private' / 'max-bot.json')
        config_patch.start()
        self.addCleanup(config_patch.stop)
        with app.app_context():
            for table in reversed(db.metadata.sorted_tables):
                db.session.execute(table.delete())
            db.session.add_all([
                User(id=1, username='operator', password_hash='x', role='admin'),
                User(id=2, username='viewer', password_hash='x', role='user'),
                User(id=3, username='denied', password_hash='x', role='user'),
            ])
            db.session.add(UserModuleAccess(user_id=2, module_key='limit_notifications'))
            db.session.add(LimitDataset(id=1, owner_id=1, name='Набор', scope='Полный перечень'))
            db.session.flush()
            db.session.add(LimitObject(id=1, dataset_id=1, uin='100.200',
                                       title='Объект', is_present=True))
            db.session.add(LimitVersion(
                id=1, dataset_id=1, uploaded_by=1, source_date=date(2026, 9, 21),
                source_name='current.xlsx', source_path='/nonexistent.xlsx',
                sha256='b' * 64, status='accepted',
                snapshot_json=json.dumps({'rows': [{
                    'uin': '100.200', 'row': 5,
                    'cells': {'object_name': {'value': 'Объект', 'kind': 'text'}}}]})))
            db.session.get(LimitDataset, 1).current_version_id = 1
            Setting.set('limits_history_ready', 'ready')
            db.session.commit()
        _limits_history_ready.set()
        self.client_admin = self.client(1)

    @staticmethod
    def client(user_id):
        client = app.test_client()
        with app.app_context():
            version = db.session.get(User, user_id).password_version or 1
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['password_version'] = version
        return client

    def test_financial_routes_remain_available(self):
        for path in ('/limit-notifications',
                     '/limit-notifications/dashboard',
                     '/limit-notifications/datasets/1/objects',
                     '/limit-notifications/objects/1',
                     '/limit-notifications/versions/1/review',
                     '/limit-notifications/versions/1/delete'):
            response = self.client_admin.get(path)
            self.assertEqual(200, response.status_code, path)
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertNotIn('Telegram', response.get_data(as_text=True))

    def test_max_version_selection_is_saved_admin_only_and_get_is_readonly(self):
        with patch('app._schedule_durable_safety_backup'):
            result = self.client_admin.post('/limit-notifications/max', data={'version_id': '1'})
        self.assertEqual(result.status_code, 302)
        with app.app_context():
            self.assertEqual(Setting.get('max_limits_version_id'), '1')
        self.assertEqual(self.client(2).post(
            '/limit-notifications/max', data={'version_id': '1'}).status_code, 403)
        self.assertEqual(self.client_admin.post(
            '/limit-notifications/max', data={'version_id': '99999'}).status_code, 404)
        self.client_admin.get('/limit-notifications/max?dataset=99999')
        with app.app_context():
            self.assertEqual(Setting.get('max_limits_version_id'), '1')

    def test_global_table_has_all_sequences_and_no_manual_actions(self):
        with app.app_context():
            db.session.add(LimitDataset(id=2, owner_id=1, name='Legacy'))
            db.session.add(LimitVersion(
                id=2, dataset_id=2, uploaded_by=1, source_date=date(2026, 9, 22),
                source_name='legacy.xlsx', source_path='/legacy.xlsx',
                sha256='d' * 64, status='review', snapshot_json='{"rows": []}'))
            db.session.commit()
        html = self.client_admin.get('/limit-notifications').get_data(as_text=True)
        for text in ('current.xlsx', 'legacy.xlsx', 'Возобновить обработку', '/limit-notifications/upload'):
            self.assertIn(text, html)
        for text in ('Новый набор', 'Проверить и принять', 'Построить отчёт', '/accept'):
            self.assertNotIn(text, html)

    def test_global_upload_empty_database_stages_once_and_retries(self):
        with app.app_context():
            db.session.get(LimitDataset, 1).current_version_id = None
            db.session.flush()
            LimitObject.query.delete()
            LimitVersion.query.delete()
            LimitDataset.query.delete()
            db.session.commit()
        with tempfile.TemporaryDirectory() as uploads, \
                patch('app.UPLOADS', uploads), \
                patch('app._limit_parse_in_background') as background:
            def upload():
                return self.client_admin.post('/limit-notifications/upload', data={
                    'source_date': '2026-09-23',
                    'file': (io.BytesIO(b'file for mocked background parser'), 'limits.xlsx'),
                }, content_type='multipart/form-data')
            self.assertEqual(302, upload().status_code)
            with app.app_context():
                version = LimitVersion.query.one()
                version_id = version.id
                self.assertEqual(1, LimitDataset.query.count())
                self.assertEqual(1, version.uploaded_by)
                self.assertEqual('queued', version.status)
            background.assert_called_once_with(version_id)
            self.assertEqual(302, upload().status_code)
            self.assertEqual(1, background.call_count)
            self.assertEqual(302, self.client_admin.post(
                f'/limit-notifications/versions/{version_id}/retry').status_code)
            self.assertEqual(2, background.call_count)
            with app.app_context():
                self.assertEqual(1, LimitVersion.query.count())
                from limits_service import process_version
                with patch('limits_parser.parse_xlsx', return_value={
                        'columns': [], 'warnings': [], 'rows': [{
                            'uin': '100.200', 'row': 5, 'cells': {}}]}):
                    first = process_version(version_id)
                self.assertEqual('accepted', first.status)
                self.assertIsNone(first.previous_version_id)
                self.assertEqual(0, LimitComparison.query.count())
                self.assertEqual(first.id, first.dataset.current_version_id)

    def test_upload_validation_rights_and_csrf(self):
        self.assertEqual(403, self.client(2).post('/limit-notifications/upload').status_code)
        response = self.client_admin.post('/limit-notifications/upload',
            data={'source_date': '2026-09-23'}, follow_redirects=True)
        self.assertIn('Выберите исходный XLSX', response.get_data(as_text=True))
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            for path in ('/limit-notifications/upload',
                         '/limit-notifications/versions/1/retry',
                         '/limit-notifications/max'):
                self.assertEqual(403, self.client_admin.post(path).status_code)
        finally:
            app.config['WTF_CSRF_ENABLED'] = False

    def test_upload_processes_successor_and_bad_file_without_manual_accept(self):
        from limits_service import process_version
        snapshot = {
            'columns': [{'key': 'object_name', 'category': 'requisite', 'label': 'Название'}],
            'warnings': [], 'rows': [{
                'uin': '100.200', 'row': 5,
                'cells': {'object_name': {'value': 'Новое название', 'kind': 'text'}}}],
        }
        def synchronous_background(version_id):
            try:
                process_version(version_id)
            except Exception:
                pass  # Real worker reports durable status while returning the upload redirect.
        with tempfile.TemporaryDirectory() as uploads, \
                patch('app.UPLOADS', uploads), \
                patch('app._limit_parse_in_background', side_effect=synchronous_background), \
                patch('limits_parser.parse_xlsx', return_value=snapshot) as parser:
            def upload(payload):
                return self.client_admin.post('/limit-notifications/upload', data={
                    'source_date': '2026-09-23',
                    'file': (io.BytesIO(payload), 'successor.xlsx'),
                }, content_type='multipart/form-data')
            self.assertEqual(302, upload(b'successor workbook').status_code)
            with app.app_context():
                successor = LimitVersion.query.order_by(LimitVersion.id.desc()).first()
                self.assertEqual('accepted', successor.status, successor.error_text)
                successor_id = successor.id
                report = LimitComparison.query.filter_by(new_version_id=successor_id).one()
                self.assertEqual('ready', report.status)
                report_id = report.id
            html = self.client_admin.get('/limit-notifications').get_data(as_text=True)
            self.assertIn(f'/comparisons/{report_id}/export.xlsx', html)
            self.assertNotIn('Проверить и принять', html)
            report_html = self.client_admin.get(
                f'/limit-notifications/comparisons/{report_id}').get_data(as_text=True)
            # No ministry/completion metadata and a non-cost change must still
            # appear by default; explicit restrictive filters remain effective.
            self.assertIn('>100.200</a>', report_html)
            filtered_html = self.client_admin.get(
                f'/limit-notifications/comparisons/{report_id}?year=2026_plus'
            ).get_data(as_text=True)
            self.assertNotIn('>100.200</a>', filtered_html)
            parser.side_effect = ValueError('Неверный формат книги')
            self.assertEqual(302, upload(b'broken workbook').status_code)
            with app.app_context():
                failed = LimitVersion.query.order_by(LimitVersion.id.desc()).first()
                self.assertEqual('error', failed.status)
                self.assertIn('Проверьте файл', failed.error_text)
                self.assertNotIn('Неверный формат книги', failed.error_text)
                self.assertEqual(successor_id, db.session.get(LimitDataset, 1).current_version_id)

    def test_measured_progress_survives_reload_and_only_accepted_is_100(self):
        import time
        now = time.time()
        with app.app_context():
            version = db.session.get(LimitVersion, 1)
            version.status = 'parsing'
            version.progress_json = json.dumps(dict(stage='rows', percent=32, done=500,
                                                    total=1000, started=now-10,
                                                    updated=now, eta=10))
            db.session.commit()
        for _ in range(2):
            html = self.client_admin.get('/limit-notifications').get_data(as_text=True)
            self.assertIn('32%', html)
            self.assertNotIn('приблизительно', html)
            self.assertNotIn('500 / 1000', html)
            self.assertNotIn('Чтение строк XLSX', html)
            self.assertNotIn('Осталось на текущем этапе:', html)
            self.assertNotIn('Прошло:', html)
            self.assertNotIn('<p>100%', html)

    def test_incomplete_version_retry_is_admin_only(self):
        self.assertEqual(403, self.client(2).post(
            '/limit-notifications/versions/1/retry').status_code)
        self.assertEqual(409, self.client_admin.post(
            '/limit-notifications/versions/1/retry').status_code)
        with app.app_context():
            db.session.get(LimitVersion, 1).status = 'error'
            db.session.get(LimitVersion, 1).error_text = 'Повреждён XLSX'
            db.session.commit()
        html = self.client_admin.get('/limit-notifications').get_data(as_text=True)
        self.assertIn('Проверьте XLSX', html)
        self.assertNotIn('Повреждён XLSX', html)
        with patch('app._limit_parse_in_background') as background:
            self.assertEqual(302, self.client_admin.post(
                '/limit-notifications/versions/1/retry').status_code)
            background.assert_called_once_with(1)

    def test_max_funding_year_selection_is_admin_only_and_persistent(self):
        page = self.client_admin.get('/limit-notifications/max').get_data(as_text=True)
        self.assertIn('value="2026" selected', page)
        self.assertIn('Все года', page)
        path = '/limit-notifications/max/funding-year'
        self.assertEqual(self.client(2).post(path, data={'funding_year': 'all'}).status_code, 403)
        self.assertEqual(self.client_admin.post(
            path, data={'funding_year': 'invalid'}).status_code, 400)
        with patch('app._schedule_durable_safety_backup'):
            self.assertEqual(self.client_admin.post(
                path, data={'funding_year': '2025'}).status_code, 302)
        with app.app_context():
            self.assertEqual(Setting.get('max_limits_min_funding_year'), '2025')
        page = self.client_admin.get('/limit-notifications/max').get_data(as_text=True)
        self.assertIn('value="2025" selected', page)
        with patch('app._schedule_durable_safety_backup'):
            self.assertEqual(self.client_admin.post(
                path, data={'funding_year': 'all'}).status_code, 302)
        with app.app_context():
            self.assertEqual(Setting.get('max_limits_min_funding_year'), 'all')

    def test_max_feedback_is_shown_on_bot_page_not_overview(self):
        path = '/limit-notifications/max/funding-year'
        with patch('app._schedule_durable_safety_backup'):
            response = self.client_admin.post(
                path, data={'funding_year': 'all'}, follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn('Годы финансирования, доступные пользователям бота, сохранены.', page)
        self.assertNotIn('Годы финансирования, доступные пользователям бота, сохранены.',
                         self.client_admin.get('/limit-notifications').get_data(as_text=True))
        with app.app_context():
            Setting.set('max_limits_dataset_id', '1')
            db.session.commit()
        max_bot_config.save('A' * 40, '-345')
        max_bot_config.set_enabled(True)
        with patch('max_limits.status', return_value='connected'), \
             patch('max_digest.prepare_digest', return_value=(0, 0)):
            response = self.client_admin.post(
                '/limit-notifications/max/send-changes', data={'confirm': 'yes'},
                follow_redirects=True)
        self.assertIn('Рассылка не создана',
                      response.get_data(as_text=True))
        self.assertNotIn('Рассылка не создана',
                         self.client_admin.get('/limit-notifications').get_data(as_text=True))

    def test_max_registry_and_delete_revoke_only_selected_user(self):
        with app.app_context():
            for uid in (101, 102):
                db.session.add(MaxLimitsProfile(
                    user_id=uid, full_name=f'Тестовый Пользователь {uid}',
                    phone=f'+70000000{uid}', step='idle', notice='notice'))
                db.session.add(MaxLimitsUin(user_id=uid, uin='100.200', status='approved'))
                db.session.add(MaxLimitsSubscription(user_id=uid, uin='100.200', active=True))
                db.session.add(MaxLimitsApplication(
                    user_id=uid, full_name='Тест', phone='123', uins_json='["100.200"]',
                    status='pending'))
                db.session.add(MaxLimitsOutbox(
                    user_id=uid, uin='100.200', version_id=1, text='test'))
            db.session.commit()
        page = self.client_admin.get('/limit-notifications/max').get_data(as_text=True)
        self.assertIn('Тестовый Пользователь 101', page)
        self.assertIn('Да, удалить', page)
        self.assertNotIn('<h2>Регистрация и подписка</h2>', page)
        self.assertNotIn('<h2>Управление кнопками</h2>', page)
        searched = self.client_admin.get('/limit-notifications/max?q=101').get_data(as_text=True)
        self.assertIn('Тестовый Пользователь 101', searched)
        self.assertNotIn('Тестовый Пользователь 102', searched)
        viewer = self.client(2)
        self.assertNotIn('Тестовый Пользователь', viewer.get(
            '/limit-notifications/max').get_data(as_text=True))
        url = '/limit-notifications/max/users/101/delete'
        self.assertEqual(viewer.post(url, data={'confirm': 'yes'}).status_code, 403)
        self.assertEqual(self.client_admin.get(url).status_code, 405)
        self.assertEqual(self.client_admin.post(url).status_code, 400)
        with patch('app._schedule_durable_safety_backup'):
            self.assertEqual(self.client_admin.post(url, data={'confirm': 'yes'}).status_code, 302)
            self.assertEqual(self.client_admin.post(url, data={'confirm': 'yes'}).status_code, 302)
        with app.app_context():
            profile = db.session.get(MaxLimitsProfile, 101)
            self.assertEqual((profile.step, profile.full_name, profile.phone, profile.notice),
                             ('deleted', '', '', ''))
            self.assertEqual(MaxLimitsUin.query.filter_by(user_id=101).first().status, 'inactive')
            self.assertEqual(MaxLimitsApplication.query.filter_by(user_id=101).first().status, 'cancelled')
            self.assertIsNotNone(MaxLimitsOutbox.query.filter_by(user_id=101).first().cancelled_at)
            self.assertFalse(MaxLimitsSubscription.query.filter_by(user_id=101).first().active)
            self.assertEqual(MaxLimitsUin.query.filter_by(user_id=102).first().status, 'approved')
            self.assertEqual(db.session.get(LimitDataset, 1).current_version_id, 1)
            self.assertEqual(LimitVersion.query.count(), 1)
        after = self.client_admin.get('/limit-notifications/max').get_data(as_text=True)
        self.assertNotIn('/users/101/delete', after)
        self.assertIn('/users/102/delete', after)
        self.assertEqual(self.client_admin.post(
            '/limit-notifications/max/users/999/delete', data={'confirm': 'yes'}).status_code, 404)

    def test_max_user_delete_requires_csrf(self):
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            response = self.client_admin.post(
                '/limit-notifications/max/users/101/delete', data={'confirm': 'yes'})
            self.assertEqual(response.status_code, 403)
        finally:
            app.config['WTF_CSRF_ENABLED'] = False

    def test_max_registry_paginates_and_includes_legacy_users(self):
        with app.app_context():
            db.session.add_all([MaxLimitsProfile(user_id=i, full_name=f'User {i}')
                                for i in range(100, 126)])
            db.session.add(MaxLimitsSubscription(user_id=999, uin='legacy', active=True))
            db.session.commit()
        from max_user_registry import registry
        with app.app_context():
            first = registry()
            self.assertEqual(first['total'], 27)
            self.assertEqual(len(first['rows']), 25)
            second = registry(2)
            self.assertEqual([row['user_id'] for row in second['rows']], [125, 999])

    def test_dashboard_uses_stable_upload_source_without_dataset_selector(self):
        with app.app_context():
            db.session.add(LimitDataset(
                id=2, owner_id=1, name='Новый набор', scope='2026',
                created_at=date(2099, 9, 22)))
            Setting.set('limits_upload_dataset_id', '1')
            db.session.commit()
        default = self.client_admin.get('/limit-notifications/dashboard')
        self.assertEqual(200, default.status_code)
        self.assertNotIn('Новый набор', default.get_data(as_text=True))
        self.assertNotIn('id="la-dataset"', default.get_data(as_text=True))
        self.assertIn('/datasets/1/objects', default.get_data(as_text=True))
        selected = self.client_admin.get('/limit-notifications/dashboard?dataset=2')
        self.assertEqual(200, selected.status_code)
        self.assertNotIn('id="la-dataset"', selected.get_data(as_text=True))
        self.assertIn('/datasets/2/objects', selected.get_data(as_text=True))
        for value in ('unknown', '0', '-1', '999'):
            self.assertEqual(
                404, self.client_admin.get(
                    '/limit-notifications/dashboard?dataset=' + value).status_code)

    def test_dashboard_respects_module_access_and_restore_gate(self):
        self.assertEqual(200, self.client(2).get(
            '/limit-notifications/dashboard').status_code)
        self.assertNotEqual(200, self.client(3).get(
            '/limit-notifications/dashboard').status_code)
        with app.app_context():
            Setting.set('limits_history_ready', 'restoring')
            db.session.commit()
        self.assertEqual(503, self.client_admin.get(
            '/limit-notifications/dashboard').status_code)

    def test_retired_messaging_routes_not_exposed(self):
        for path in ('/limit-notifications/telegram',
                     '/limit-notifications/max-journal',
                     '/limit-notifications/settings',
                     '/limit-notifications/datasets/1/subscribers',
                     '/limit-notifications/datasets/1/deliveries',
                     '/limit-notifications/objects/1/message'):
            self.assertEqual(404, self.client_admin.get(path).status_code, path)
        for path in ('/limit-notifications/max-settings',
                     '/limit-notifications/max-retry',
                     '/limit-notifications/deliveries/1/retry',
                     '/limit-notifications/recipients/1/revoke-access'):
            self.assertEqual(404, self.client_admin.post(path).status_code, path)

    def test_max_page_and_webhook_security(self):
        from max_limits import webhook_secret
        self.assertEqual(200, self.client_admin.get(
            '/limit-notifications/max').status_code)
        self.assertNotEqual(200, self.client(3).get(
            '/limit-notifications/max').status_code)
        self.assertEqual(404, self.client_admin.get(
            '/limit-notifications/telegram').status_code)
        url = 'https://example.org/limit-notifications/max/webhook'
        max_bot_config.save('A' * 40, '-345')
        max_bot_config.set_enabled(True)
        with patch.dict(os.environ, {
            'MAX_LIMITS_WEBHOOK_URL': url,
        }):
            endpoint = '/limit-notifications/max/webhook'
            self.assertEqual(403, app.test_client().post(
                endpoint, json={'update_type': 'bot_started'}).status_code)
            headers = {'X-Max-Bot-Api-Secret': webhook_secret(
                'A' * 40, app.secret_key)}
            self.assertEqual(200, app.test_client().post(
                endpoint, json={'update_type': 'message_created',
                                'message': {'sender': {'user_id': 2, 'is_bot': True}}},
                headers=headers).status_code)
            max_bot_config.set_enabled(False)
            self.assertEqual(404, app.test_client().post(
                endpoint, json={'update_type': 'bot_started'}, headers=headers).status_code)

    def test_max_credentials_are_admin_only_masked_and_do_not_start(self):
        token = 'NEW' * 20
        url = '/limit-notifications/max/credentials'
        self.assertEqual(403, self.client(2).post(
            url, data={'token': token, 'group_id': '-345'}).status_code)
        self.assertEqual(302, self.client_admin.post(
            url, data={'token': token, 'group_id': '-345'}).status_code)
        config = max_bot_config.read()
        self.assertEqual(config['token'], token)
        self.assertFalse(config['enabled'])
        page = self.client_admin.get('/limit-notifications/max').get_data(as_text=True)
        self.assertNotIn(token, page)
        self.assertIn('Токен сохранён', page)
        self.assertNotIn('Токен бота MAX', self.client(2).get(
            '/limit-notifications/max').get_data(as_text=True))
        self.assertEqual(403, self.client(2).post(
            '/limit-notifications/max/start').status_code)
        self.assertEqual(403, self.client(2).post(
            '/limit-notifications/max/stop').status_code)
        with patch('max_limits.start_max_limits_worker', return_value=True):
            self.client_admin.post('/limit-notifications/max/start')
        self.assertTrue(max_bot_config.read()['enabled'])
        self.client_admin.post('/limit-notifications/max/stop')
        self.assertFalse(max_bot_config.read()['enabled'])
        self.assertEqual(302, self.client_admin.post(
            url, data={'token': '', 'group_id': 'bad'}).status_code)
        self.assertEqual(token, max_bot_config.read()['token'])

    def test_max_status_updates_without_reloading_page(self):
        max_page = self.client_admin.get('/limit-notifications/max')
        self.assertEqual(max_page.status_code, 200)
        html = max_page.get_data(as_text=True)
        self.assertIn('id="max-connection-status"', html)
        self.assertIn('id="max-send-changes"', html)
        self.assertIn('id="max-digest-dialog"', html)
        self.assertNotIn('window.confirm', html)
        self.assertIn('/limit-notifications/max/status', html)
        with patch('max_limits.status', return_value='connected'):
            response = self.client_admin.get('/limit-notifications/max/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'connected'})
        self.assertEqual(
            self.client(2).get('/limit-notifications/max/status').status_code, 403)

    def test_max_start_does_not_claim_success_when_worker_is_not_started(self):
        max_bot_config.save('A' * 40, '-345')
        with patch('max_limits.start_max_limits_worker', return_value=False):
            response = self.client_admin.post(
                '/limit-notifications/max/start', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Бот пока не запущен', html)
        self.assertNotIn('Подключение к MAX проверяется.', html)

    def test_connected_bot_can_queue_manual_digest_without_sending_in_test(self):
        with patch('max_limits.status', return_value='connected'), \
             patch('max_digest.prepare_digest', return_value=(2, 2)) as prepare, \
             patch('app._schedule_durable_safety_backup') as backup:
            response = self.client_admin.post(
                '/limit-notifications/max/send-changes',
                data={'confirm': 'yes', 'run_id': 'a' * 32}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('2 получателей, 2 сообщений',
                      response.get_data(as_text=True))
        prepare.assert_called_once_with(run_id='a' * 32)
        backup.assert_called_once()

    def test_max_new_operations_require_csrf(self):
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            for path in ('credentials', 'start', 'stop'):
                self.assertEqual(403, self.client_admin.post(
                    '/limit-notifications/max/' + path,
                    data={'token': 'A' * 40, 'group_id': '-345'}).status_code)
        finally:
            app.config['WTF_CSRF_ENABLED'] = False

    def test_access_and_review_confirmation(self):
        viewer = self.client(2)
        denied = self.client(3)
        self.assertEqual(200, viewer.get('/limit-notifications/datasets/1/objects').status_code)
        self.assertEqual(404, viewer.get('/limit-notifications/settings').status_code)
        self.assertEqual(403, denied.get('/limit-notifications').status_code)
        with app.app_context():
            db.session.get(LimitDataset, 1).current_version_id = None
            db.session.get(LimitVersion, 1).status = 'review'
            db.session.commit()
        response = self.client_admin.post('/limit-notifications/versions/1/accept')
        self.assertEqual(302, response.status_code)
        self.assertIn('/versions/1/review', response.headers['Location'])

    def test_delete_current_version_keeps_registry_usable(self):
        with patch('app._schedule_limits_rotation_backup'):
            response = self.client_admin.post(
                '/limit-notifications/versions/1/delete', data={'confirm': 'delete'})
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertIsNone(db.session.get(LimitVersion, 1))
            self.assertIsNone(db.session.get(LimitDataset, 1).current_version_id)
        self.assertEqual(200, self.client_admin.get('/limit-notifications').status_code)

    def test_filters_keep_repeated_uin_rows_and_match_same_financial_cell(self):
        with app.app_context():
            version = db.session.get(LimitVersion, 1)
            version.snapshot_json = json.dumps({
                'columns': [
                    {'key': 'om_code', 'label': 'Код ОМ', 'category': 'programme'},
                    {'key': 'work_type', 'label': 'Вид работ', 'category': 'requisite'},
                    {'key': 'f26', 'label': '2026', 'category': 'annual_funding',
                     'year': 2026, 'source': 'federal_budget'},
                    {'key': 'f27', 'label': '2027', 'category': 'annual_funding',
                     'year': 2027, 'source': 'moscow_oblast_budget'},
                ],
                'rows': [
                    {'uin': '100.200', 'row': 5, 'cells': {
                        'om_code': {'value': '01'}, 'work_type': {'value': 'Стройка'},
                        'f26': {'kind': 'money', 'value': 10}}},
                    {'uin': '100.200', 'row': 6, 'cells': {
                        'om_code': {'value': 'И3'}, 'work_type': {'value': 'Ремонт'},
                        'f27': {'kind': 'money', 'value': 20}}},
                ]})
            dataset = db.session.get(LimitDataset, 1)
            self.assertEqual(1, objects_context(dataset, {
                'om_code': 'И3', 'year': '2027',
                'source': 'moscow_oblast_budget'})['total'])
            self.assertEqual(0, objects_context(dataset, {
                'om_code': 'И3', 'year': '2026',
                'source': 'federal_budget'})['total'])
            self.assertEqual(2, len(object_context(db.session.get(LimitObject, 1))['current_rows']))
            db.session.commit()
        page = self.client_admin.get(
            '/limit-notifications/datasets/1/objects?om_code=%D0%983&year=2027')
        self.assertEqual(200, page.status_code)
        self.assertIn('100.200', page.get_data(as_text=True))

    def test_none_funding_hidden_but_zero_remains_visible(self):
        with app.app_context():
            db.session.get(LimitVersion, 1).snapshot_json = json.dumps({
                'columns': [
                    {'key': 'f26', 'label': 'Пустой лимит', 'category': 'annual_funding'},
                    {'key': 'f27', 'label': 'Нулевой лимит', 'category': 'annual_funding'},
                ],
                'rows': [{'uin': '100.200', 'row': 5, 'cells': {
                    'f26': {'kind': 'money', 'value': None},
                    'f27': {'kind': 'money', 'value': 0},
                }}]})
            db.session.commit()
            funding = object_context(db.session.get(LimitObject, 1))['current_rows'][0]['funding']
            self.assertEqual([0], [item['value'] for item in funding])
        page = self.client_admin.get('/limit-notifications/objects/1')
        self.assertEqual(200, page.status_code)
        self.assertIn('>0</td>', page.get_data(as_text=True))

    def test_change_filter_and_comparison_are_still_available(self):
        with app.app_context():
            db.session.add(LimitVersion(
                id=2, dataset_id=1, uploaded_by=1, source_date=date(2026, 8, 1),
                source_name='previous.xlsx', source_path='/previous.xlsx',
                sha256='c' * 64, status='accepted', snapshot_json='{"rows": []}'))
            version = db.session.get(LimitVersion, 1)
            version.previous_version_id = 2
            db.session.flush()
            comparison = LimitComparison(id=1, old_version_id=2, new_version_id=1,
                                         status='ready', summary_json='{}')
            db.session.add(comparison)
            db.session.add(LimitChange(comparison_id=1, object_id=1, uin='100.200',
                                       category='annual_funding', field_key='f26',
                                       field_label='Финансирование',
                                       old_json='{"value": 0}', new_json='{"value": 10}'))
            db.session.commit()
        filtered = self.client_admin.get('/limit-notifications/datasets/1/objects?changed=1')
        self.assertEqual(200, filtered.status_code)
        self.assertIn('100.200', filtered.get_data(as_text=True))
        compared = self.client_admin.get('/limit-notifications/comparisons/1')
        self.assertEqual(200, compared.status_code)
        self.assertIn('Сравнение лимитов', compared.get_data(as_text=True))

    def test_processing_version_cannot_be_deleted(self):
        with app.app_context():
            db.session.get(LimitVersion, 1).status = 'parsing'
            db.session.commit()
        page = self.client_admin.get('/limit-notifications/versions/1/delete')
        self.assertEqual(200, page.status_code)
        self.assertIn('Загрузка ещё обрабатывается', page.get_data(as_text=True))
        with patch('app._schedule_limits_rotation_backup') as backup:
            response = self.client_admin.post(
                '/limit-notifications/versions/1/delete', data={'confirm': 'delete'})
            backup.assert_not_called()
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertIsNotNone(db.session.get(LimitVersion, 1))

    def test_restore_gate_hides_financial_data_until_ready(self):
        _limits_history_ready.clear()
        try:
            self.assertEqual(503, self.client_admin.get('/limit-notifications/objects/1').status_code)
        finally:
            _limits_history_ready.set()
        self.assertEqual(200, self.client_admin.get('/limit-notifications/objects/1').status_code)

    def test_startup_and_reconciliation_have_no_bot_or_network_transport(self):
        startup = inspect.getsource(_ensure_limits_recovery)
        reconciliation = inspect.getsource(_sync_daily_reconciliation_alerts)
        for retired in ('limits_bot', 'limits_connection', 'limits_max',
                        'start_worker', 'start_reconciler', 'reconcile_connection',
                        '_limits_adapter'):
            self.assertNotIn(retired, startup)
        self.assertNotIn('max_notifications', reconciliation)
        self.assertIn('daily_reconciliation_state', reconciliation)
        self.assertTrue(any(
            rule.rule == '/limit-notifications/max'
            for rule in app.url_map.iter_rules()))
        self.assertFalse(any(
            rule.rule.startswith('/limit-notifications/telegram')
            for rule in app.url_map.iter_rules()))


if __name__ == '__main__':
    unittest.main()