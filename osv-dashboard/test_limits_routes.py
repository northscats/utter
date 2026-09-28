"""Routes for financial limits and the separate MAX bot surface."""
import inspect
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
                    'cells': {'object_name': {'value': 'Объект'}}}]})))
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

    def test_max_dataset_selection_is_saved_admin_only_and_get_is_readonly(self):
        with patch('app._schedule_durable_safety_backup'):
            result = self.client_admin.post('/limit-notifications/max', data={'dataset': '1'})
        self.assertEqual(result.status_code, 302)
        with app.app_context():
            self.assertEqual(Setting.get('max_limits_dataset_id'), '1')
        self.assertEqual(self.client(2).post(
            '/limit-notifications/max', data={'dataset': '1'}).status_code, 403)
        self.assertEqual(self.client_admin.post(
            '/limit-notifications/max', data={'dataset': '99999'}).status_code, 404)
        self.client_admin.get('/limit-notifications/max?dataset=99999')
        with app.app_context():
            self.assertEqual(Setting.get('max_limits_dataset_id'), '1')

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
            Setting.set('max_limits_username', 'test')
            db.session.commit()
        with patch.dict(os.environ, {'MAX_LIMITS_BOT_TOKEN': 'test-only'}), \
             patch('max_digest.prepare_digest', return_value=(0, 0)):
            response = self.client_admin.post(
                '/limit-notifications/max/send-changes', data={'confirm': 'yes'},
                follow_redirects=True)
        self.assertIn('Нет новых сообщений для выбранного фильтра',
                      response.get_data(as_text=True))
        self.assertNotIn('Нет новых сообщений для выбранного фильтра',
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

    def test_dashboard_defaults_to_last_created_dataset_and_can_switch(self):
        with app.app_context():
            db.session.add(LimitDataset(
                id=2, owner_id=1, name='Новый набор', scope='2026',
                created_at=date(2099, 9, 22)))
            db.session.commit()
        default = self.client_admin.get('/limit-notifications/dashboard')
        self.assertEqual(200, default.status_code)
        self.assertIn('Новый набор', default.get_data(as_text=True))
        self.assertIn('<option value="2" selected>', default.get_data(as_text=True))
        selected = self.client_admin.get('/limit-notifications/dashboard?dataset=1')
        self.assertEqual(200, selected.status_code)
        self.assertIn('<option value="1" selected>', selected.get_data(as_text=True))
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
        with patch.dict(os.environ, {
            'MAX_LIMITS_BOT_TOKEN': 'test-only',
            'MAX_LIMITS_WEBHOOK_URL': url,
        }):
            endpoint = '/limit-notifications/max/webhook'
            self.assertEqual(403, app.test_client().post(
                endpoint, json={'update_type': 'bot_started'}).status_code)
            headers = {'X-Max-Bot-Api-Secret': webhook_secret(
                'test-only', app.secret_key)}
            self.assertEqual(200, app.test_client().post(
                endpoint, json={'update_type': 'message_created',
                                'message': {'sender': {'user_id': 2, 'is_bot': True}}},
                headers=headers).status_code)

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