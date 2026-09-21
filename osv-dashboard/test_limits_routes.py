"""Isolated access, filter, and presentation contracts; no MAX network calls."""
import json
import hashlib
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import ANY, MagicMock, patch
from datetime import date, datetime, timezone, timedelta

_TEMP = tempfile.TemporaryDirectory(prefix='limits_routes_')
os.environ['DATABASE_URL'] = 'sqlite:///' + _TEMP.name + '/routes.db'
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')
os.environ.setdefault('ENCRYPTION_KEY', 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')

from app import app, init_db, _limits_history_ready
from database import (db, User, UserModuleAccess, Setting, LimitDataset, LimitObject,
                      LimitVersion, LimitVersionTombstone, LimitSubscription,
                      LimitRecipient, LimitDelivery, LimitComparison, LimitChange,
                      LimitMaxEvent)
from limits_views import objects_context, object_context
from werkzeug.datastructures import MultiDict


class LimitsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.fake_tombstone_sidecar = {}

        def fake_record(version_id, dataset_id, source_path='', source_sha256=''):
            cls.fake_tombstone_sidecar[int(version_id)] = {
                'version_id': int(version_id),
                'dataset_id': dataset_id,
                'source_path': source_path,
                'source_sha256': source_sha256,
            }
            return list(cls.fake_tombstone_sidecar.values())

        cls._tombstone_transport = patch(
            'durable_data.record_limit_tombstone',
            side_effect=fake_record)
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
            dataset = LimitDataset(id=1, owner_id=1, name='Набор', scope='Полный перечень')
            db.session.add(dataset)
            db.session.flush()
            self.snapshot = {
                'columns': [
                    {'key': 'uin', 'label': 'УИН', 'category': 'identifier'},
                    {'key': 'object_name', 'label': 'Наименование', 'category': 'requisite'},
                    {'key': 'om_code', 'label': 'ОМ', 'category': 'programme'},
                    {'key': 'work_type', 'label': 'Вид работ', 'category': 'requisite'},
                    {'key': 'f26', 'label': 'Финансирование 2026', 'category': 'annual_funding',
                     'year': 2026, 'source': 'federal_budget'},
                ],
                'rows': [
                    self.row('100.200', 5, '01', 'Первая строка', '10'),
                    self.row('100.200', 6, 'И3', 'Другая строка', '20'),
                ],
                'warnings': [], 'metadata': {},
            }
            db.session.add(LimitVersion(
                id=1, dataset_id=1, uploaded_by=1, source_date=date(2026, 9, 1),
                source_name='form.xlsx', source_path='/not-public.xlsx', sha256='a'*64,
                status='accepted', snapshot_json=json.dumps(self.snapshot)))
            dataset.current_version_id = 1
            db.session.add(LimitObject(id=1, dataset_id=1, uin='100.200',
                                       title='Объект', address='Адрес', is_present=True))
            Setting.set('limits_history_ready', 'ready')
            db.session.commit()
        _limits_history_ready.set()

    @staticmethod
    def row(uin, row_number, code, name, amount):
        values = {'uin': uin, 'om_code': code, 'object_name': name,
                  'work_type': 'Строительство', 'f26': amount}
        return {'uin': uin, 'row': row_number, 'cells': {
            key: {'raw': value, 'value': value, 'kind': 'money' if key == 'f26' else 'text',
                  'cell': f'A{row_number}'} for key, value in values.items()}}

    def client(self, user_id):
        client = app.test_client()
        with app.app_context():
            password_version = int(db.session.get(User, user_id).password_version or 1)
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['password_version'] = password_version
        return client

    def test_module_permission_protects_all_read_surfaces(self):
        client = self.client(3)
        for path in ('/limit-notifications', '/limit-notifications/datasets/1/objects',
                     '/limit-notifications/objects/1',
                     '/limit-notifications/versions/1/source.xlsx',
                     '/limit-notifications/comparisons/1/export.xlsx'):
            self.assertEqual(403, client.get(path).status_code, path)

    def test_legacy_dataset_rename_preserves_dashboard_and_bot_binding(self):
        from app import _reconcile_limits_after_restore

        with app.app_context():
            dataset = db.session.get(LimitDataset, 1)
            dataset.name = '1'
            db.session.add(LimitVersion(
                id=22, dataset_id=1, uploaded_by=1,
                source_date=date(2026, 9, 21),
                source_name='current.xlsx', source_path='/current.xlsx',
                sha256='b' * 64, status='accepted',
                snapshot_json=json.dumps(self.snapshot)))
            dataset.current_version_id = 22
            unrelated = LimitDataset(
                id=2, owner_id=1, name='1', scope='Другой реестр')
            db.session.add(unrelated)
            Setting.set('limits_bot_dataset_id', str(dataset.id))
            recipient = LimitRecipient(max_user_id='rename-regression')
            db.session.add(recipient)
            db.session.flush()
            subscription = LimitSubscription(
                dataset_id=dataset.id, uin='100.200',
                recipient_id=recipient.id, is_active=True)
            db.session.add(subscription)
            original_version_id = dataset.current_version_id
            original_object_ids = [
                row.id for row in LimitObject.query.filter_by(
                    dataset_id=dataset.id).order_by(LimitObject.id).all()
            ]
            db.session.commit()
            original_subscription_id = subscription.id

            _reconcile_limits_after_restore()
            _reconcile_limits_after_restore()
            db.session.expire_all()

            renamed = db.session.get(LimitDataset, 1)
            self.assertEqual('Лимиты', renamed.name)
            self.assertEqual(original_version_id, renamed.current_version_id)
            self.assertEqual(
                original_object_ids,
                [row.id for row in LimitObject.query.filter_by(
                    dataset_id=renamed.id).order_by(LimitObject.id).all()])
            self.assertEqual(str(renamed.id),
                             Setting.get('limits_bot_dataset_id'))
            self.assertEqual('1', db.session.get(LimitDataset, 2).name)
            preserved_subscription = db.session.get(
                LimitSubscription, original_subscription_id)
            self.assertEqual(renamed.id, preserved_subscription.dataset_id)
            self.assertTrue(preserved_subscription.is_active)

            db.session.add(LimitDataset(
                id=3, owner_id=1, name='1', scope='Создан после миграции'))
            db.session.commit()
            _reconcile_limits_after_restore()
            self.assertEqual('1', db.session.get(LimitDataset, 3).name)

        client = self.client(1)
        dashboard = client.get('/limit-notifications?dataset=1')
        settings = client.get('/limit-notifications/settings?dataset=1')
        self.assertEqual(200, dashboard.status_code)
        self.assertIn('Лимиты', dashboard.get_data(as_text=True))
        self.assertEqual(200, settings.status_code)
        settings_html = settings.get_data(as_text=True)
        self.assertIn('>Лимиты</option>', settings_html)
        self.assertIn('value="1" selected', settings_html)

    def test_viewer_cannot_access_admin_or_mutate(self):
        client = self.client(2)
        self.assertEqual(403, client.get('/limit-notifications/datasets/1/subscribers').status_code)
        self.assertEqual(403, client.get('/limit-notifications/datasets/1/deliveries').status_code)
        self.assertEqual(403, client.get('/limit-notifications/max-journal').status_code)
        self.assertEqual(403, client.post('/limit-notifications/versions/1/accept').status_code)
        self.assertEqual(403, client.post('/limit-notifications/max-settings').status_code)

    def test_restore_gate_covers_direct_source_and_card(self):
        client = self.client(1)
        _limits_history_ready.clear()
        for path in ('/limit-notifications/objects/1',
                     '/limit-notifications/versions/1/source.xlsx',
                     '/limit-notifications/comparisons/1/export.xlsx'):
            self.assertEqual(503, client.get(path).status_code)
        _limits_history_ready.set()

    def test_filters_keep_all_repeated_uin_rows(self):
        with app.app_context():
            dataset = db.session.get(LimitDataset, 1)
            for code in ('01', 'И3'):
                ctx = objects_context(dataset, MultiDict(
                    {'om_code': code, 'year': '2026', 'source': 'federal_budget'}))
                self.assertEqual(1, ctx['total'])
            self.assertEqual(0, objects_context(dataset, MultiDict(
                {'year': '2027', 'source': 'federal_budget'}))['total'])
            ctx = object_context(db.session.get(LimitObject, 1))
            self.assertEqual(2, len(ctx['current_rows']))
            self.assertEqual(1, len(ctx['conflicts']))
            self.assertEqual('20', ctx['current_rows'][1]['funding'][0]['value'])

    def test_object_card_hides_none_values_including_annual_funding(self):
        with app.app_context():
            version = db.session.get(LimitVersion, 1)
            snapshot = json.loads(version.snapshot_json)
            snapshot['columns'].extend([
                {'key':'empty_limit', 'label':'Пустой годовой лимит',
                 'category':'annual_funding', 'year':2027, 'source':'federal_budget'},
                {'key':'none_text', 'label':'Текст None', 'category':'requisite'},
                {'key':'dash_limit', 'label':'Прочерк', 'category':'annual_funding',
                 'year':2028, 'source':'federal_budget'}])
            snapshot['rows'][0]['cells'].update({
                'empty_limit': {'raw':'', 'value':None, 'kind':'blank', 'cell':'AZ5'},
                'none_text': {'raw':'None', 'value':'None', 'kind':'text', 'cell':'BA5'},
                'dash_limit': {'raw':'-', 'value':None, 'kind':'dash', 'cell':'BB5'}})
            version.snapshot_json = json.dumps(snapshot)
            db.session.commit()
            ctx = object_context(db.session.get(LimitObject, 1))
            first = ctx['current_rows'][0]
            self.assertNotIn('empty_limit', [item['key'] for item in first['funding']])
            self.assertNotIn('none_text', [item['key'] for item in first['requisites']])
            self.assertNotIn('dash_limit', [item['key'] for item in first['funding']])
        page = self.client(1).get('/limit-notifications/objects/1').get_data(as_text=True)
        self.assertNotIn('Пустой годовой лимит', page)
        self.assertNotIn('Текст None', page)
        self.assertNotIn('Прочерк', page)
        self.assertIn('Годовые лимиты', page)

    def test_comparison_defaults_to_cost_2026_plus_and_energy_ministry(self):
        energy = 'МИНИСТЕРСТВО ЭНЕРГЕТИКИ МОСКОВСКОЙ ОБЛАСТИ'
        housing = 'МИНИСТЕРСТВО ЖИЛИЩНО-КОММУНАЛЬНОГО ХОЗЯЙСТВА МОСКОВСКОЙ ОБЛАСТИ'
        with app.app_context():
            current = db.session.get(LimitVersion, 1)
            snapshot = json.loads(current.snapshot_json)
            snapshot['rows'][0]['cells'].update({
                'cio_grbs': {'value': energy},
                'work_completion_date': {'value': '2026-10-14'},
            })
            snapshot['rows'][1]['uin'] = '200.300'
            snapshot['rows'][1]['cells']['uin']['value'] = '200.300'
            snapshot['rows'][1]['cells'].update({
                'cio_grbs': {'value': housing},
                'work_completion_date': {'value': '2027-10-14'},
            })
            current.snapshot_json = json.dumps(snapshot)
            db.session.add(LimitVersion(
                id=2, dataset_id=1, uploaded_by=1, source_date=date(2026, 8, 1),
                source_name='old.xlsx', source_path='/old.xlsx', sha256='b'*64,
                status='accepted', snapshot_json=json.dumps(snapshot)))
            db.session.add(LimitObject(
                id=2, dataset_id=1, uin='200.300', title='Объект ЖКХ',
                is_present=True))
            comparison = LimitComparison(
                id=1, old_version_id=2, new_version_id=1)
            db.session.add(comparison)
            db.session.add_all([
                LimitChange(comparison_id=1, object_id=1, uin='100.200',
                            category='limit_cost', field_key='limit_cost',
                            field_label='Стоимость'),
                LimitChange(comparison_id=1, object_id=2, uin='200.300',
                            category='limit_cost', field_key='limit_cost',
                            field_label='Стоимость'),
                LimitChange(comparison_id=1, object_id=1, uin='100.200',
                            category='schedule', field_key='work_completion_date',
                            field_label='Завершение'),
            ])
            db.session.commit()

        page = self.client(1).get(
            '/limit-notifications/comparisons/1').get_data(as_text=True)
        self.assertIn('value="limit_cost" selected', page)
        self.assertIn('value="2026_plus" selected', page)
        self.assertIn(f'value="{energy}" selected', page)
        self.assertIn('Минэнерго МО', page)
        self.assertIn('МинЖКХ МО', page)
        self.assertIn('100.200', page)
        self.assertNotIn('200.300', page)
        self.assertNotIn('Завершение', page)

        all_page = self.client(1).get(
            '/limit-notifications/comparisons/1?category=&year=&ministry='
        ).get_data(as_text=True)
        self.assertIn('200.300', all_page)
        self.assertNotIn('<th>Показатель</th>', all_page)

    def test_filter_before_pagination_does_not_hide_tail(self):
        with app.app_context():
            for i in range(1501):
                db.session.add(LimitObject(dataset_id=1, uin=f'000.{i:06}',
                                           title='Другой', is_present=True))
            db.session.commit()
            ctx = objects_context(db.session.get(LimitDataset, 1),
                                  MultiDict({'om_code': 'И3'}))
            self.assertEqual(['100.200'], [obj.uin for obj in ctx['objects']])
            ctx = objects_context(db.session.get(LimitDataset, 1), MultiDict())
            self.assertEqual(1502, ctx['total'])
            self.assertEqual(100, len(ctx['objects']))
            self.assertEqual(16, ctx['pages'])
            recipient = LimitRecipient(max_user_id='pagination-recipient')
            db.session.add(recipient)
            db.session.flush()
            db.session.add_all([LimitDelivery(
                version_id=1, object_id=1, recipient_id=recipient.id,
                content='Pagination test', campaign_key=f'test-{i}')
                for i in range(201)])
            db.session.commit()
        client = self.client(1)
        for path in ('objects', 'deliveries'):
            response = client.get(f'/limit-notifications/datasets/1/{path}?page=2')
            self.assertEqual(200, response.status_code)
            self.assertIn('page=1', response.get_data(as_text=True))

    def test_views_render_with_true_disconnected_status(self):
        client = self.client(1)
        for path in ('/limit-notifications', '/limit-notifications/datasets/1/objects',
                     '/limit-notifications/objects/1', '/limit-notifications/datasets/1/deliveries',
                     '/limit-notifications/datasets/1/subscribers', '/limit-notifications/max-journal',
                     '/limit-notifications/versions/1/review'):
            response = client.get(path)
            self.assertEqual(200, response.status_code, path)
            self.assertIn('no-store', response.headers['Cache-Control'])
        response = client.get('/limit-notifications')
        self.assertIn('MAX не подключён', response.get_data(as_text=True))
        today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        self.assertIn(f'name="source_date" type="date" value="{today}" required', response.get_data(as_text=True))

    def test_accept_requires_explicit_preview_confirmation(self):
        with app.app_context():
            db.session.get(LimitDataset, 1).current_version_id = None
            db.session.get(LimitVersion, 1).status = 'review'
            db.session.commit()
        client = self.client(1)
        response = client.post('/limit-notifications/versions/1/accept')
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers['Location'].endswith('/versions/1/review'))
        preview = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('Строк мероприятий', preview)
        self.assertIn('Уникальных УИН', preview)
        self.assertIn('name="confirm"', preview)
        with app.app_context():
            self.assertEqual('review', db.session.get(LimitVersion, 1).status)

    def test_settings_separated_and_tabs_preserve_dataset(self):
        client = self.client(1)
        overview = client.get('/limit-notifications?dataset=1').get_data(as_text=True)
        self.assertNotIn('name="token"', overview)
        for path in ('/limit-notifications?dataset=1',
                     '/limit-notifications/datasets/1/objects',
                     '/limit-notifications/datasets/1/subscribers',
                     '/limit-notifications/settings?dataset=1'):
            response = client.get(path)
            self.assertEqual(200, response.status_code)
            html = response.get_data(as_text=True)
            self.assertIn('aria-current="page"', html)
            self.assertIn('/limit-notifications/datasets/1/objects', html)
            self.assertIn('/limit-notifications/datasets/1/subscribers', html)
            self.assertIn('/limit-notifications/settings?dataset=1', html)
        settings = client.get('/limit-notifications/settings?dataset=1')
        settings_html = settings.get_data(as_text=True)
        self.assertIn('name="token"', settings_html)
        self.assertNotIn('name="webhook_secret"', settings_html)
        self.assertNotIn('name="enabled"', settings_html)
        self.assertIn('no-store', settings.headers['Cache-Control'])
        self.assertEqual(403, self.client(2).get('/limit-notifications/settings').status_code)
        response = client.post('/limit-notifications/max-settings', data={'context_dataset':'1'})
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers['Location'].endswith('/settings?dataset=1'))

    def test_saved_credentials_are_indicated_and_preserved_without_exposure(self):
        from app import decrypt_value
        client = self.client(1)
        client.post('/limit-notifications/max-settings', data={
            'token': 'synthetic-test-token', 'webhook_secret': 'synthetic-test-secret'})
        with app.app_context():
            saved = Setting.get('limits_max_token')
            self.assertEqual('synthetic-test-token', decrypt_value(saved))
        page = client.get('/limit-notifications/settings').get_data(as_text=True)
        self.assertIn('Токен сохранён', page)
        self.assertNotIn('name="webhook_secret"', page)
        self.assertNotIn('synthetic-test-token', page)
        self.assertNotIn('synthetic-test-secret', page)
        client.post('/limit-notifications/max-settings', data={'token':'', 'webhook_secret':''})
        with app.app_context():
            self.assertEqual(saved, Setting.get('limits_max_token'))

    @patch.dict(app.config, {
        'LIMITS_MAX_WEBHOOK_URL':
        'https://current-installation.example/limit-notifications/max/webhook',
    })
    def test_complete_settings_generate_secret_and_keep_bot_always_on(self):
        from app import decrypt_value
        client = self.client(1)
        with patch('app._limits_adapter') as factory:
            factory.return_value.subscribe_webhook.return_value = {
                'url': 'https://localhost/limit-notifications/max/webhook',
                'update_types': ['message_created'],
            }
            response = client.post('/limit-notifications/max-settings', data={
                'token':'synthetic-auto-token', 'group_chat_id':'-123',
                'bot_dataset_id':'1', 'context_dataset':'1'})
            self.assertEqual(302, response.status_code)
            with app.app_context():
                stored = Setting.get('limits_max_webhook_secret')
                secret = decrypt_value(stored)
                self.assertGreaterEqual(len(secret),32)
                self.assertNotEqual(secret,stored)
                self.assertEqual('1',Setting.get('limits_max_enabled'))
            factory.return_value.check_webhook_receiver.assert_called_once()
            factory.return_value.get_chat_admins.assert_called_once_with('-123')
            factory.return_value.subscribe_webhook.assert_called_once()
            self.assertEqual(secret,factory.return_value.subscribe_webhook.call_args.args[1])
        page=client.get('/limit-notifications/settings').get_data(as_text=True)
        self.assertNotIn(secret,page)
        payload={'update_type':'bot_started','timestamp':42,'user':{'user_id':123}}
        endpoint='/limit-notifications/max/webhook'
        self.assertEqual(403,client.post(endpoint,json=payload).status_code)
        self.assertEqual(403,client.get(endpoint).status_code)
        headers={'X-Max-Bot-Api-Secret':secret}
        self.assertEqual({'ready':True},client.get(endpoint,headers=headers).get_json())
        with patch('limits_connection._serialized_connection') as guard:
            guard.return_value.__enter__.return_value = False
            guard.return_value.__exit__.return_value = False
            self.assertEqual(
                503, client.post(endpoint, json=payload, headers=headers).status_code)
        self.assertEqual(200,client.post(endpoint,json=payload,headers=headers).status_code)
        self.assertTrue(client.post(endpoint,json=payload,headers=headers).get_json()['duplicate'])
        with patch('app._limits_adapter') as factory:
            factory.return_value.subscribe_webhook.return_value = {'success':True}
            client.post('/limit-notifications/max-settings',data={
                'group_chat_id':'-123','bot_dataset_id':'1'})
            self.assertEqual(secret,factory.return_value.subscribe_webhook.call_args.args[1])

    @patch.dict(app.config, {
        'LIMITS_MAX_WEBHOOK_URL':
        'https://current-installation.example/limit-notifications/max/webhook',
    })
    def test_connection_failure_never_enables_or_exposes_secret(self):
        client=self.client(1)
        for result in ({'success':False},TimeoutError('synthetic-private-error')):
            with patch('app._limits_adapter') as factory:
                if isinstance(result,Exception):
                    factory.return_value.subscribe_webhook.side_effect=result
                else:
                    factory.return_value.subscribe_webhook.return_value=result
                response=client.post('/limit-notifications/max-settings',data={
                    'token':'synthetic-token','group_chat_id':'-123','bot_dataset_id':'1'})
                self.assertEqual(302,response.status_code)
                with app.app_context():
                    self.assertEqual('0',Setting.get('limits_max_enabled'))
                    self.assertTrue(Setting.get('limits_max_connection_error'))
                    self.assertTrue(Setting.get('limits_max_last_attempt_at'))
                    self.assertTrue(Setting.get('limits_max_retry_at'))
                self.assertNotIn('synthetic-private-error',client.get(response.headers['Location']).get_data(as_text=True))
        with patch('app._limits_adapter') as factory:
            factory.return_value.check_webhook_receiver.side_effect=RuntimeError('wrong installation')
            client.post('/limit-notifications/max-settings',data={
                'group_chat_id':'-123','bot_dataset_id':'1'})
            factory.return_value.subscribe_webhook.assert_not_called()
        with patch('app._limits_adapter') as factory:
            client.post('/limit-notifications/max-settings',data={'group_chat_id':'-123'})
            factory.assert_not_called()

    @patch.dict(app.config, {
        'LIMITS_MAX_WEBHOOK_URL':
        'https://current-installation.example/limit-notifications/max/webhook',
    })
    def test_admin_can_retry_saved_max_connection_without_reentering_credentials(self):
        from app import encrypt_value
        client = self.client(1)
        with app.app_context():
            Setting.set('limits_max_token', encrypt_value('saved-token'))
            Setting.set('limits_max_group_chat_id', '-123')
            Setting.set('limits_bot_dataset_id', '1')
            Setting.set('limits_max_webhook_registered', '0')
            db.session.commit()

        page = client.get('/limit-notifications/settings?dataset=1').get_data(as_text=True)
        self.assertIn('Повторить подключение сейчас', page)
        with patch('app._limits_adapter') as factory:
            factory.return_value.subscribe_webhook.return_value = {'success': True}
            response = client.post(
                '/limit-notifications/max-retry',
                data={'context_dataset': '1'})
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers['Location'].endswith('/settings?dataset=1'))
        factory.return_value.check_webhook_receiver.assert_called_once()
        factory.return_value.get_chat_admins.assert_called_once_with('-123')
        factory.return_value.subscribe_webhook.assert_called_once()
        with app.app_context():
            self.assertEqual('1', Setting.get('limits_max_webhook_registered'))
            self.assertEqual('saved-token', __import__('app').decrypt_value(
                Setting.get('limits_max_token')))
        page = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('MAX подтвердил регистрацию webhook', page)
        self.assertNotIn('Повторить подключение сейчас', page)

    def test_legacy_complete_settings_create_secret_and_retry_automatically(self):
        from app import decrypt_value, encrypt_value
        from limits_bot import ingest_event
        from limits_connection import reconcile_connection, start_reconciler

        adapter = MagicMock()
        adapter.subscribe_webhook.side_effect = [
            TimeoutError('temporary private detail'),
            {'success': True},
        ]
        with app.app_context(), patch('limits_connection.RETRY_DELAYS', (0,)):
            Setting.set('limits_max_token', encrypt_value('legacy-token'))
            Setting.set('limits_max_group_chat_id', '-123')
            Setting.set('limits_bot_dataset_id', '1')
            # Legacy complete-but-disabled settings intentionally have no secret.
            ingest_event({'update_type': 'bot_started', 'timestamp': 77,
                          'user': {'user_id': 7}})
            db.session.commit()
            self.assertFalse(reconcile_connection(
                encrypt=encrypt_value, decrypt=decrypt_value,
                adapter_factory=lambda: adapter,
                webhook_url='https://current.example/limit-notifications/max/webhook'))
            stored_secret = Setting.get('limits_max_webhook_secret')
            self.assertTrue(decrypt_value(stored_secret))
            self.assertEqual('received', LimitMaxEvent.query.one().status)

            state = start_reconciler(
                app, encrypt_value, decrypt_value, lambda: adapter,
                lambda: 'https://current.example/limit-notifications/max/webhook',
                interval_seconds=1)
        try:
            deadline = time.time() + 3
            while time.time() < deadline:
                with app.app_context():
                    db.session.expire_all()
                    if Setting.get('limits_max_webhook_registered') == '1':
                        break
                time.sleep(.05)
            with app.app_context():
                self.assertEqual('1', Setting.get('limits_max_webhook_registered'))
                self.assertEqual(stored_secret, Setting.get('limits_max_webhook_secret'))
                self.assertEqual('received', LimitMaxEvent.query.one().status)
                self.assertEqual('', Setting.get('limits_max_retry_at'))
        finally:
            state['stop'].set()
            state['thread'].join(2)
            app.extensions.pop('limits_max_reconciler', None)

    def test_long_max_failure_creates_one_safe_admin_alert_until_success(self):
        from datetime import datetime, timedelta, timezone
        from app import decrypt_value, encrypt_value
        from limits_connection import connection_alert, reconcile_connection

        adapter = MagicMock()
        adapter.check_webhook_receiver.side_effect = RuntimeError(
            'synthetic-private-token-and-secret')
        now = datetime.now(timezone.utc)
        with app.app_context():
            Setting.set('limits_max_token', encrypt_value('saved-token'))
            Setting.set('limits_max_group_chat_id', '-123')
            Setting.set('limits_bot_dataset_id', '1')
            db.session.commit()
            self.assertFalse(reconcile_connection(
                encrypt=encrypt_value, decrypt=decrypt_value,
                adapter_factory=lambda: adapter,
                webhook_url='https://current.example/limit-notifications/max/webhook'))
            self.assertIsNone(connection_alert(
                now=now + timedelta(minutes=30),
                alert_after_seconds=3600))
            alert = connection_alert(
                now=now + timedelta(hours=2),
                alert_after_seconds=3600)
            self.assertIn('Webhook не зарегистрирован', alert['reason'])
            self.assertTrue(alert['last_attempt_at'])
            self.assertNotIn('synthetic-private', str(alert))

            # Further failures keep the same episode instead of creating alerts.
            started = alert['failure_started_at']
            reconcile_connection(
                encrypt=encrypt_value, decrypt=decrypt_value,
                adapter_factory=lambda: adapter,
                webhook_url='https://current.example/limit-notifications/max/webhook')
            self.assertEqual(
                started,
                connection_alert(
                    now=now + timedelta(hours=2),
                    alert_after_seconds=3600)['failure_started_at'])

            adapter.check_webhook_receiver.side_effect = None
            adapter.subscribe_webhook.return_value = {'success': True}
            self.assertTrue(reconcile_connection(
                encrypt=encrypt_value, decrypt=decrypt_value,
                adapter_factory=lambda: adapter,
                webhook_url='https://current.example/limit-notifications/max/webhook'))
            self.assertIsNone(connection_alert(
                now=now + timedelta(hours=3),
                alert_after_seconds=0))

    def test_long_max_failure_banner_is_not_shown_in_workspace(self):
        with app.app_context():
            Setting.set('limits_max_token', 'encrypted')
            Setting.set('limits_max_group_chat_id', '-123')
            Setting.set('limits_bot_dataset_id', '1')
            Setting.set('limits_max_webhook_registered', '0')
            Setting.set('limits_max_connection_error', 'Безопасная причина.')
            Setting.set('limits_max_last_attempt_at', '2026-09-17T10:00:00+00:00')
            Setting.set('limits_max_failure_started_at', '2026-09-17T09:00:00+00:00')
            db.session.commit()
        app.config['LIMITS_MAX_ALERT_AFTER_SECONDS'] = 0
        try:
            admin_page = self.client(1).get('/modules').get_data(as_text=True)
            self.assertNotIn('MAX долго не подключён', admin_page)
            self.assertNotIn('2026-09-17T10:00:00+00:00', admin_page)
            self.assertNotIn('limits_max_token', admin_page)
            viewer_page = self.client(2).get('/modules').get_data(as_text=True)
            self.assertNotIn('MAX долго не подключён', viewer_page)
        finally:
            app.config.pop('LIMITS_MAX_ALERT_AFTER_SECONDS', None)

    def test_changed_settings_pause_worker_before_reconciliation(self):
        from app import decrypt_value, encrypt_value
        from limits_bot import run_worker_once
        from limits_connection import configure_connection, registration_active

        adapter = MagicMock()
        adapter.is_configured = True
        with app.app_context():
            Setting.set('limits_max_token', encrypt_value('old-token'))
            Setting.set('limits_max_group_chat_id', '-100')
            Setting.set('limits_bot_dataset_id', '1')
            Setting.set('limits_max_webhook_registered', '1')
            Setting.set('limits_max_enabled', '1')
            db.session.commit()

            def delayed_reconciliation(**_kwargs):
                self.assertFalse(registration_active())
                result = run_worker_once(adapter)
                self.assertFalse(result['configured'])
                adapter.send_message.assert_not_called()
                return False

            with patch('limits_connection.reconcile_connection',
                       side_effect=delayed_reconciliation):
                configure_connection(
                    token='new-token', group_id='-200', dataset_id=1,
                    encrypt=encrypt_value, decrypt=decrypt_value,
                    adapter_factory=lambda: adapter,
                    webhook_url='https://current.example/limit-notifications/max/webhook')
            self.assertEqual('0', Setting.get('limits_max_webhook_registered'))

    def test_startup_reconciliation_waits_for_inflight_dispatch(self):
        from app import decrypt_value, encrypt_value
        from limits_connection import _serialized_connection, reconcile_connection

        adapter = MagicMock()
        adapter.subscribe_webhook.return_value = {'success': True}
        with app.app_context():
            Setting.set('limits_max_token', encrypt_value('saved-token'))
            Setting.set('limits_max_group_chat_id', '-100')
            Setting.set('limits_bot_dataset_id', '1')
            Setting.set('limits_max_webhook_secret', encrypt_value('saved-secret'))
            Setting.set('limits_max_webhook_registered', '1')
            Setting.set('limits_max_enabled', '1')
            db.session.commit()

        dispatch_inside = threading.Event()
        release_dispatch = threading.Event()
        reconciliation_done = threading.Event()

        def inflight_dispatch():
            with app.app_context(), _serialized_connection():
                dispatch_inside.set()
                release_dispatch.wait(2)

        def startup_reconciliation():
            with app.app_context():
                reconcile_connection(
                    encrypt=encrypt_value, decrypt=decrypt_value,
                    adapter_factory=lambda: adapter,
                    webhook_url='https://current.example/limit-notifications/max/webhook')
                reconciliation_done.set()

        dispatch = threading.Thread(target=inflight_dispatch)
        startup = threading.Thread(target=startup_reconciliation)
        dispatch.start()
        self.assertTrue(dispatch_inside.wait(1))
        startup.start()
        time.sleep(.05)
        self.assertFalse(reconciliation_done.is_set())
        with app.app_context():
            self.assertEqual('1', Setting.get('limits_max_webhook_registered'))
        release_dispatch.set()
        dispatch.join(2)
        startup.join(2)
        self.assertTrue(reconciliation_done.is_set())
        with app.app_context():
            self.assertEqual('1', Setting.get('limits_max_webhook_registered'))

    def test_connection_uses_current_installation_and_reports_group_rejection(self):
        from limits_max import MaxAPIError
        client = self.client(1)
        with patch.dict(os.environ, {
                'LIMITS_MAX_WEBHOOK_URL':
                'https://current-installation.example/limit-notifications/max/webhook',
        }), patch('app._limits_adapter') as factory:
            factory.return_value.get_chat_admins.side_effect = MaxAPIError(
                'synthetic', status=404, code='http_error')
            response = client.post(
                '/limit-notifications/max-settings',
                data={'token':'synthetic-token',
                      'group_chat_id':'-999', 'bot_dataset_id':'1'})
            factory.return_value.check_webhook_receiver.assert_called_once_with(
                'https://current-installation.example/limit-notifications/max/webhook',
                ANY)
            factory.return_value.subscribe_webhook.assert_not_called()
        page = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('не подтвердил доступ бота', page)

    def test_spoofed_host_never_reaches_max_adapter(self):
        client = self.client(1)
        session_cookie = client.get_cookie(app.config.get('SESSION_COOKIE_NAME', 'session'))
        client.set_cookie(
            app.config.get('SESSION_COOKIE_NAME', 'session'),
            session_cookie.value, domain='attacker.example')
        with patch.dict(os.environ, {}, clear=False), \
                patch('app._limits_adapter') as factory:
            os.environ.pop('LIMITS_MAX_WEBHOOK_URL', None)
            response = client.post(
                '/limit-notifications/max-settings',
                headers={'Host': 'attacker.example', 'X-Forwarded-Proto': 'https'},
                data={'token':'synthetic-token',
                      'group_chat_id':'-999', 'bot_dataset_id':'1'})
            factory.assert_not_called()
        page = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('Адрес webhook не передан в запущенную публикацию', page)
        self.assertIn('LIMITS_MAX_WEBHOOK_URL', page)
        self.assertIn('заново опубликуйте приложение', page)

    @patch.dict(app.config, {
        'LIMITS_MAX_WEBHOOK_URL':
        'https://current-installation.example/limit-notifications/max/webhook',
    })
    def test_connection_explains_outdated_publication_and_dataset(self):
        from limits_max import MaxAPIError
        client = self.client(1)
        with patch('app._limits_adapter') as factory:
            factory.return_value.check_webhook_receiver.side_effect = MaxAPIError(
                'synthetic', status=404, code='receiver_outdated')
            response = client.post('/limit-notifications/max-settings', data={
                'token':'synthetic-token', 'group_chat_id':'-123',
                'bot_dataset_id':'1', 'context_dataset':'1'})
        page = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('проверочный адрес MAX', page)
        self.assertIn('вернул 404', page)
        self.assertIn('Реестр объектов для подписок бота', page)
        self.assertIn('искать введённый пользователем УИН именно в этом реестре', page)

    def _old_upload(self, status='accepted'):
        path = os.path.join(_TEMP.name, 'deletable.xlsx')
        with open(path, 'wb') as file:
            file.write(b'isolated-test-source')
        with app.app_context():
            db.session.add(LimitVersion(id=2, dataset_id=1, uploaded_by=1,
                source_date=date(2026,8,1), source_name='deletable.xlsx',
                source_path=path, sha256='b'*64, status=status))
            db.session.commit()
        return path

    def test_delete_inactive_uploads_requires_confirmation_and_preserves_current(self):
        client = self.client(1)
        for status in ('accepted', 'review', 'error', 'rejected'):
            path = self._old_upload(status)
            response = client.get('/limit-notifications/versions/2/delete')
            self.assertEqual(200, response.status_code)
            self.assertIn('name="confirm"', response.get_data(as_text=True))
            client.post('/limit-notifications/versions/2/delete')
            self.assertTrue(os.path.exists(path))
            with app.app_context():
                db.session.add(LimitComparison(old_version_id=2,new_version_id=1))
                db.session.commit()
            with patch('app._schedule_limits_rotation_backup') as backup:
                response = client.post('/limit-notifications/versions/2/delete',data={'confirm':'delete'})
                self.assertEqual(302,response.status_code)
                backup.assert_called_once()
            self.assertFalse(os.path.exists(path))
            with app.app_context():
                self.assertIsNone(db.session.get(LimitVersion,2))
                self.assertEqual(1,db.session.get(LimitDataset,1).current_version_id)
                self.assertIsNotNone(db.session.get(LimitObject,1))
                self.assertEqual(0,LimitComparison.query.count())

    def test_delete_protects_processing_and_pending_deliveries(self):
        client = self.client(1)
        path = self._old_upload('parsing')
        client.post('/limit-notifications/versions/2/delete',data={'confirm':'delete'})
        with app.app_context():
            self.assertIsNotNone(db.session.get(LimitVersion,1))
            db.session.get(LimitVersion,2).status='accepted'
            recipient=LimitRecipient(max_user_id='deletion-test')
            db.session.add(recipient)
            db.session.flush()
            db.session.add(LimitDelivery(version_id=2,object_id=1,recipient_id=recipient.id,
                                         content='test',status='unknown'))
            db.session.commit()
        client.post('/limit-notifications/versions/2/delete',data={'confirm':'delete'})
        self.assertTrue(os.path.exists(path))
        with app.app_context():
            self.assertIsNotNone(db.session.get(LimitVersion,2))
            self.assertEqual('unknown',LimitDelivery.query.one().status)
        for method in ('get','post'):
            self.assertEqual(403,getattr(self.client(2),method)('/limit-notifications/versions/2/delete').status_code)
        app.config['WTF_CSRF_ENABLED']=True
        try:
            self.assertIn(client.post('/limit-notifications/versions/2/delete',data={'confirm':'delete'}).status_code,(400,403))
        finally:
            app.config['WTF_CSRF_ENABLED']=False

    def test_delete_current_restores_previous_snapshot_without_notifications(self):
        client = self.client(1)
        self.assertIn('/limit-notifications/versions/1/delete',
                      client.get('/limit-notifications?dataset=1').get_data(as_text=True))
        self._old_upload()
        with app.app_context():
            old = db.session.get(LimitVersion, 2)
            old.snapshot_json = json.dumps({'rows': [
                self.row('300.400', 5, '01', 'Название из предыдущего среза', '12')]})
            db.session.add(LimitObject(id=2, dataset_id=1, uin='300.400',
                                       title='Старое название', is_present=False))
            recipient = LimitRecipient(max_user_id='preserved-subscriber')
            db.session.add(recipient)
            db.session.flush()
            db.session.add(LimitSubscription(dataset_id=1, uin='100.200',
                                             recipient_id=recipient.id, is_active=True))
            db.session.commit()
        page = client.get('/limit-notifications/versions/1/delete').get_data(as_text=True)
        self.assertIn('Актуальным станет', page)
        self.assertIn('name="confirm"', page)
        with patch('app._schedule_limits_rotation_backup'):
            response = client.post('/limit-notifications/versions/1/delete', data={'confirm':'delete'})
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertIsNone(db.session.get(LimitVersion, 1))
            self.assertEqual(2, db.session.get(LimitDataset, 1).current_version_id)
            self.assertFalse(db.session.get(LimitObject, 1).is_present)
            restored = db.session.get(LimitObject, 2)
            self.assertTrue(restored.is_present)
            self.assertEqual(2, restored.last_seen_version_id)
            self.assertEqual('Название из предыдущего среза', restored.title)
            self.assertTrue(LimitSubscription.query.one().is_active)
            self.assertEqual(0, LimitDelivery.query.count())
        page = client.get('/limit-notifications/versions/2/delete').get_data(as_text=True)
        self.assertIn('Реестр останется без текущего среза', page)
        with patch('app._schedule_limits_rotation_backup'):
            client.post('/limit-notifications/versions/2/delete', data={'confirm':'delete'})
        with app.app_context():
            self.assertIsNone(db.session.get(LimitDataset, 1).current_version_id)
            self.assertEqual(0, LimitObject.query.filter_by(is_present=True).count())
            self.assertEqual(0, LimitObject.query.filter(LimitObject.last_seen_version_id.isnot(None)).count())
            self.assertTrue(LimitSubscription.query.one().is_active)
        self.assertEqual(200, client.get('/limit-notifications?dataset=1').status_code)
        register = client.get('/limit-notifications/datasets/1/objects')
        self.assertEqual(200, register.status_code)
        self.assertIn('нет актуального принятого среза', register.get_data(as_text=True))
        card = client.get('/limit-notifications/objects/1')
        self.assertEqual(200, card.status_code)
        card_text = card.get_data(as_text=True)
        self.assertIn('Данные актуального среза отсутствуют', card_text)
        self.assertNotIn('Создать ручное сообщение', card_text)

    def test_current_pending_deliveries_prevent_deletion_and_pointer_change(self):
        client = self.client(1)
        self._old_upload()
        with app.app_context():
            recipient = LimitRecipient(max_user_id='pending-current')
            db.session.add(recipient)
            db.session.flush()
            db.session.add(LimitDelivery(version_id=1, object_id=1, recipient_id=recipient.id,
                                        content='pending', status='unknown'))
            db.session.commit()
        with patch('app._schedule_limits_rotation_backup') as backup:
            client.post('/limit-notifications/versions/1/delete', data={'confirm':'delete'})
            backup.assert_not_called()
        with app.app_context():
            self.assertIsNotNone(db.session.get(LimitVersion, 1))
            self.assertEqual(1, db.session.get(LimitDataset, 1).current_version_id)
            from limits_service import purge_version, LimitsError
            with self.assertRaises(LimitsError):
                purge_version(1)
            self.assertEqual(1, db.session.get(LimitDataset, 1).current_version_id)

    def test_delete_refreshes_current_pointer_before_switching(self):
        self._old_upload()
        with app.app_context():
            from limits_service import purge_version
            cached = db.session.get(LimitDataset, 1)
            self.assertEqual(1, cached.current_version_id)
            # A request can load the dataset before another publication commits.
            db.session.connection().execute(
                LimitDataset.__table__.update().where(LimitDataset.id == 1).values(current_version_id=2))
            self.assertEqual(1, cached.current_version_id)
            purge_version(1)
            self.assertEqual(2, db.session.get(LimitDataset, 1).current_version_id)

    def test_durable_restore_handles_current_version_cycle_and_exact_decimal(self):
        from decimal import Decimal
        from durable_data import _encoded, _restore_json
        with app.app_context():
            self.assertEqual('123456789.012345', _encoded(Decimal('123456789.012345')))
            models = (LimitDataset, LimitVersion, LimitObject)
            tables = {}
            for model in models:
                tables[model.__name__] = [
                    {column.name: _encoded(getattr(row, column.name))
                     for column in model.__table__.columns}
                    for row in model.query.all()]
            dataset = db.session.get(LimitDataset, 1)
            dataset.current_version_id = None
            db.session.flush()
            for model in reversed(models):
                model.query.delete()
            db.session.commit()
            result = _restore_json(db, {'tables': tables}, models)
            self.assertEqual(1, db.session.get(LimitDataset, 1).current_version_id)
            self.assertEqual('100.200', db.session.get(LimitObject, 1).uin)
            self.assertEqual(3, result[0])

    def test_manual_delete_tombstone_blocks_old_manifest_and_file_resurrection(self):
        """An old additive snapshot cannot recreate a manually deleted head."""
        from pathlib import Path
        from durable_data import _restore_files, _restore_json, _snapshot_rows
        from limits_service import purge_version
        from app import UPLOADS

        with app.app_context():
            dataset = db.session.get(LimitDataset, 1)
            old_path = Path(UPLOADS) / 'limits' / '1' / 'old-manifest.xlsx'
            deleted_path = Path(UPLOADS) / 'limits' / '1' / 'deleted-head.xlsx'
            old_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.write_bytes(b'old-manifest-version')
            deleted_path.write_bytes(b'deleted-head-version')
            first = db.session.get(LimitVersion, 1)
            first.source_path = str(old_path)
            first.accepted_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
            head = LimitVersion(
                id=2, dataset_id=1, uploaded_by=1, source_date=date(2026, 9, 2),
                accepted_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
                source_name='deleted-head.xlsx', source_path=str(deleted_path),
                sha256='b' * 64, status='accepted',
                snapshot_json=json.dumps(self.snapshot))
            db.session.add(head)
            db.session.flush()
            dataset.current_version_id = head.id
            db.session.get(LimitObject, 1).last_seen_version_id = head.id
            db.session.commit()

            models = (User, LimitDataset, LimitVersion, LimitObject,
                      LimitVersionTombstone)
            old_manifest = {
                'tables': _snapshot_rows(models),
                'files': [
                    {'area': 'uploads', 'path': 'limits/1/old-manifest.xlsx',
                     'sha256': 'a' * 64},
                    {'area': 'uploads', 'path': 'limits/1/deleted-head.xlsx',
                     'sha256': 'b' * 64},
                ],
            }
            with open(old_path, 'rb') as stream:
                old_bytes = stream.read()
            with open(deleted_path, 'rb') as stream:
                deleted_bytes = stream.read()
            old_sha = hashlib.sha256(old_bytes).hexdigest()
            deleted_sha = hashlib.sha256(deleted_bytes).hexdigest()
            old_manifest['files'][0]['sha256'] = old_sha
            old_manifest['files'][1]['sha256'] = deleted_sha

            purge_version(head.id)
            self.assertEqual(1, dataset.current_version_id)
            self.assertEqual(1, db.session.get(LimitDataset, 1).current_version_id)
            self.assertIsNotNone(LimitVersionTombstone.query.filter_by(
                version_id=2).one())
            self.assertIn(2, self.fake_tombstone_sidecar)

            # Simulate a partial database loss before the new removal-aware
            # snapshot is promoted.  The deletion marker itself survives.
            LimitObject.query.delete()
            LimitVersion.query.delete()
            LimitDataset.query.delete()
            db.session.commit()
            old_path.unlink(missing_ok=True)

            result = _restore_json(db, old_manifest, models)

            class Files:
                def get_file(self, key, destination):
                    payload = {old_sha: old_bytes, deleted_sha: deleted_bytes}[
                        key.rsplit('/', 1)[-1]]
                    Path(destination).parent.mkdir(parents=True, exist_ok=True)
                    Path(destination).write_bytes(payload)

            files = _restore_files(Files(), old_manifest, UPLOADS, '/tmp')
            self.assertEqual(3, result[0])
            self.assertEqual(1, db.session.get(LimitDataset, 1).current_version_id)
            self.assertIsNotNone(db.session.get(LimitVersion, 1))
            self.assertIsNone(db.session.get(LimitVersion, 2))
            self.assertEqual(1, db.session.get(LimitObject, 1).last_seen_version_id)
            self.assertTrue(db.session.get(LimitObject, 1).is_present)
            self.assertTrue(old_path.is_file())
            self.assertFalse(deleted_path.is_file())
            self.assertEqual(1, files)


if __name__ == '__main__':
    unittest.main()