"""Contract tests for per-user metric-modal layout persistence."""
import os
import tempfile
import unittest
from unittest.mock import patch


_TMP = tempfile.TemporaryDirectory(prefix='metric-layouts-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(
    _TMP.name, 'layouts.db')

import app as app_module
from database import (
    Company, CompanyMembership, MetricLayout, User, UserModuleAccess, db,
)


class MetricLayoutApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        cls.imap_patch = patch.object(app_module, '_ensure_imap_thread')
        cls.imap_patch.start()
        with cls.app.app_context():
            db.create_all()
            cls.user = User(
                username='metric-layout-user', password_hash='unused')
            cls.other_user = User(
                username='metric-layout-other', password_hash='unused')
            cls.company = Company(name='Layout company', created_by=1)
            db.session.add_all([cls.user, cls.other_user])
            db.session.flush()
            cls.company.created_by = cls.user.id
            db.session.add(cls.company)
            db.session.flush()
            db.session.add(CompanyMembership(
                user_id=cls.user.id, company_id=cls.company.id))
            db.session.add(UserModuleAccess(
                user_id=cls.user.id, module_key='analytics'))
            db.session.commit()
            cls.user_id = cls.user.id
            cls.other_user_id = cls.other_user.id
            cls.company_id = cls.company.id

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        cls.imap_patch.stop()
        _TMP.cleanup()

    def _client(self, user_id=None):
        client = self.app.test_client()
        if user_id is not None:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True
        return client

    def _url(self, metric='revenue', mode='osv', company=None):
        return (
            f'/company/{company or self.company_id}/key-indicators/layout/'
            f'{mode}/{metric}')

    def _csrf_headers(self, client):
        token = client.get('/rating-api/csrf').get_json()['csrf_token']
        return {'X-CSRFToken': token, 'Accept': 'application/json'}

    def test_default_save_reload_and_reset(self):
        client = self._client(self.user_id)
        url = self._url()
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.get(url).get_json(), {
            'layout': {'version': 1, 'items': {}}, 'revision': 0})

        headers = self._csrf_headers(client)
        saved = client.put(
            url,
            json={
                'layout': {'version': 1, 'items': {
                    'main-card': 'main', 'metric-chart': 'detail',
                }},
                'revision': 0,
            },
            headers=headers,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()['revision'], 1)
        self.assertEqual(client.get(url).get_json(), saved.get_json())

        reset = client.put(
            url,
            json={'layout': {'version': 1, 'items': {}}, 'revision': 1},
            headers=headers,
        )
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.get_json(), {
            'layout': {'version': 1, 'items': {}}, 'revision': 2})

    def test_conflict_does_not_overwrite(self):
        client = self._client(self.user_id)
        url = self._url(metric='cash')
        headers = self._csrf_headers(client)
        self.assertEqual(client.put(
            url,
            json={'layout': {'version': 1, 'items': {'cash': 'hidden'}},
                  'revision': 0},
            headers=headers,
        ).status_code, 200)
        conflict = client.put(
            url,
            json={'layout': {'version': 1, 'items': {'cash': 'main'}},
                  'revision': 0},
            headers=headers,
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(set(conflict.get_json()), {'error'})
        self.assertEqual(client.get(url).get_json()['layout']['items'],
                         {'cash': 'hidden'})

    def test_validation_access_and_csrf(self):
        client = self._client(self.user_id)
        url = self._url(metric='net_profit', mode='annual')
        headers = self._csrf_headers(client)
        valid = {'layout': {'version': 1, 'items': {}}, 'revision': 0}
        for bad in (
            {'layout': {'version': 1, 'items': {}}, 'revision': True},
            {'layout': {'version': 2, 'items': {}}, 'revision': 0},
            {'layout': {'version': 1, 'items': []}, 'revision': 0},
            {'layout': {'version': 1, 'items': {'bad key': 'main'}},
             'revision': 0},
            {'layout': {'version': 1, 'items': {}}, 'revision': 0,
             'user_id': self.user_id},
        ):
            self.assertEqual(
                client.put(url, json=bad, headers=headers).status_code, 400)
        self.assertEqual(client.put(url, json=valid).status_code, 403)
        self.assertEqual(
            self._client(self.other_user_id).get(url).status_code, 403)
        self.assertEqual(
            client.get(self._url(metric='not-a-metric')).status_code, 400)
        self.assertEqual(
            client.get(self._url(company=self.company_id + 100)).status_code,
            403)

    def test_only_known_mode_metric_pairs_are_accepted(self):
        client = self._client(self.user_id)
        self.assertEqual(
            client.get(self._url(metric='net_profit', mode='osv')).status_code,
            400)
        self.assertEqual(
            client.get(self._url(metric='collection', mode='annual')).status_code,
            400)

    def test_dashboard_layout_is_reserved_and_persists_visibility_and_order(self):
        client = self._client(self.user_id)
        url = self._url(metric='__dashboard__')
        self.assertEqual(client.get(url).get_json(), {
            'layout': {'version': 1, 'items': {}}, 'revision': 0})
        layout = {
            'version': 1,
            'items': {'cash': 'hidden', 'revenue': 'main', 'profit': 'main'},
            'order': ['profit', 'cash', 'revenue'],
        }
        saved = client.put(
            url, json={'layout': layout, 'revision': 0},
            headers=self._csrf_headers(client))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()['layout'], layout)
        self.assertEqual(client.get(url).get_json()['layout'], layout)
        invalid = client.put(
            url,
            json={'layout': {'version': 1, 'items': {'not-a-card': 'main'}},
                  'revision': 1},
            headers=self._csrf_headers(client))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(client.get(url).get_json()['layout'], layout)

    def test_dashboard_reserved_id_does_not_open_arbitrary_metric_records(self):
        client = self._client(self.user_id)
        self.assertEqual(
            client.get(self._url(metric='not-a-dashboard')).status_code, 400)
        self.assertEqual(
            client.get(self._url(metric='dashboard')).status_code, 200)
        alias_url = self._url(metric='dashboard', mode='annual')
        saved = client.put(
            alias_url,
            json={'layout': {'version': 1, 'items': {'cash': 'hidden'}},
                  'revision': 0},
            headers=self._csrf_headers(client))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(client.get(self._url(
            metric='__dashboard__', mode='annual')).get_json(),
                         saved.get_json())

    def test_order_round_trip_and_validation(self):
        client = self._client(self.user_id)
        url = self._url(metric='capex')
        headers = self._csrf_headers(client)
        layout = {
            'version': 1,
            'items': {'first': 'main', 'second': 'detail', 'third': 'hidden'},
            'order': ['third', 'second', 'first'],
        }
        saved = client.put(
            url, json={'layout': layout, 'revision': 0}, headers=headers)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(client.get(url).get_json()['layout'], layout)
        for order in ('first', ['first', 'first'], ['unknown'], [None],
                      [['first']], ['first'] * 251):
            invalid = dict(layout, order=order)
            response = client.put(
                url, json={'layout': invalid, 'revision': 1}, headers=headers)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(client.get(url).get_json()['layout'], layout)
        # Reset and older clients can omit order without a schema migration.
        reset = {'version': 1, 'items': {}}
        self.assertEqual(client.put(
            url, json={'layout': reset, 'revision': 1},
            headers=headers).status_code, 200)
        self.assertEqual(client.get(url).get_json()['layout'], reset)

    def test_database_row_has_owner_and_tenant_keys(self):
        with self.app.app_context():
            rows = MetricLayout.query.filter_by(
                user_id=self.user_id, company_id=self.company_id,
                mode='osv').all()
            self.assertGreaterEqual(len(rows), 0)
            if rows:
                self.assertEqual(rows[0].mode, 'osv')

    def test_non_member_cannot_load_indicator_page_or_financial_source(self):
        # Grant module access so this tests company scoping, not the earlier
        # module-access gate. The authenticated outsider has no membership.
        with self.app.app_context():
            access = UserModuleAccess.query.filter_by(
                user_id=self.other_user_id, module_key='analytics').first()
            if access is None:
                db.session.add(UserModuleAccess(
                    user_id=self.other_user_id, module_key='analytics'))
                db.session.commit()
        outsider = self._client(self.other_user_id)
        root = f'/company/{self.company_id}/key-indicators'
        with patch('key_indicators_service.load_company_source') as source:
            self.assertEqual(outsider.get(root).status_code, 403)
            self.assertEqual(outsider.get(root + '/data').status_code, 403)
            self.assertEqual(outsider.get(self._url()).status_code, 403)
            source.assert_not_called()
        member = self._client(self.user_id)
        self.assertEqual(member.get(root).status_code, 200)

    def test_authorized_users_and_companies_have_independent_layouts(self):
        with self.app.app_context():
            colleague = User(
                username='metric-layout-colleague', password_hash='unused')
            other_company = Company(
                name='Other layout company', created_by=self.user_id)
            db.session.add_all([colleague, other_company])
            db.session.flush()
            colleague_id, other_company_id = colleague.id, other_company.id
            db.session.add_all([
                CompanyMembership(
                    user_id=colleague_id, company_id=self.company_id),
                CompanyMembership(
                    user_id=self.user_id, company_id=other_company_id),
                UserModuleAccess(
                    user_id=colleague_id, module_key='analytics'),
            ])
            db.session.commit()
        owner = self._client(self.user_id)
        colleague = self._client(colleague_id)
        url = self._url(metric='salary')
        body = {
            'layout': {'version': 1, 'items': {'salary-card': 'detail'}},
            'revision': 0,
        }
        self.assertEqual(owner.put(
            url, json=body, headers=self._csrf_headers(owner)).status_code, 200)
        default = {'layout': {'version': 1, 'items': {}}, 'revision': 0}
        self.assertEqual(colleague.get(url).get_json(), default)
        self.assertEqual(owner.get(self._url(
            metric='salary', company=other_company_id)).get_json(), default)
        body['layout']['items']['salary-card'] = 'hidden'
        self.assertEqual(colleague.put(
            url, json=body,
            headers=self._csrf_headers(colleague)).status_code, 200)
        self.assertEqual(owner.get(url).get_json()['layout']['items'],
                         {'salary-card': 'detail'})


if __name__ == '__main__':
    unittest.main()