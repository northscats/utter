"""SQLite-only regression tests for the durable metric snapshot boundary."""
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory(prefix='metric-snapshots-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_tmp.name, 'test.db')

import app as app_module
from database import ActiveMetricSnapshot, Company, MetricSnapshot, User, db
from metric_snapshots import _periods, build_metric_snapshot, read_metric_values


class MetricSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = app_module.app.app_context()
        cls.ctx.push()
        db.drop_all()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.ctx.pop()
        _tmp.cleanup()

    def setUp(self):
        db.session.remove()
        db.drop_all()
        db.create_all()
        user = User(username='snapshot-user', password_hash='x', role='admin')
        db.session.add(user)
        db.session.flush()
        self.company = Company(name='Snapshot Co', created_by=user.id)
        db.session.add(self.company)
        db.session.commit()

    def test_publishes_dynamic_cards_without_full_payload(self):
        source = {'version': 1, 'accounts': {}}
        cards = [{'id': 'new-card', 'value': 12.5},
                 {'id': 'missing', 'value': None},
                 {'id': 'chart', 'value': {'points': [1, 2]}}]
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      return_value={'cards': cards}):
            snapshot = build_metric_snapshot(self.company.id, '/unused',
                                             endpoint_date=date(2026, 1, 31))
        self.assertEqual(snapshot.version, 1)
        self.assertEqual(
            {(row['metric_id'], row['value']) for row in
             read_metric_values(self.company.id)},
            {('missing', None), ('new-card', 12.5)})
        self.assertFalse(hasattr(snapshot, 'payload_json'))

    def test_sparse_source_uses_only_real_period_endpoints(self):
        source = {'dates': ['2025-01-31', '2025-03-31', '2026-02-28']}
        periods = _periods(source, date(2026, 2, 28))
        self.assertEqual(
            {(kind, end.isoformat()) for kind, end in periods},
            {('month', '2025-01-31'), ('month', '2025-03-31'),
             ('month', '2026-02-28'), ('quarter', '2025-03-31'),
             ('quarter', '2026-02-28'), ('year', '2025-03-31'),
             ('year', '2026-02-28')})

    def test_calculation_failure_does_not_replace_active_snapshot(self):
        source = {'version': 1, 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      return_value={'cards': [{'id': 'stable', 'value': 1}]}):
            build_metric_snapshot(self.company.id, '/unused',
                                  endpoint_date=date(2026, 1, 31))
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      side_effect=ValueError('bad source')):
            with self.assertRaises(ValueError):
                build_metric_snapshot(self.company.id, '/unused',
                                      endpoint_date=date(2026, 1, 31))
        self.assertEqual(read_metric_values(self.company.id)[0]['value'], 1)

    def test_summary_requires_access_and_reports_not_built(self):
        app_module.app.config.update(TESTING=True)
        company_id = self.company.id
        user_id = self.company.created_by
        url = (f'/analytics/summary?company_ids={company_id}'
               '&metric_ids=revenue&granularity=quarter'
               '&from=2026-01-01&to=2026-03-31')
        db.session.remove()
        self.ctx.pop()  # Request clients need independent Flask-Login contexts.
        try:
            anonymous = app_module.app.test_client()
            self.assertEqual(anonymous.get(url).status_code, 302)
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(user_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.location)
                self.assertEqual(response.json['companies'][0]['status'], 'not_built')
                self.assertEqual(response.json['values'], [])
                self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
                self.assertEqual(client.get('/analytics/summary?company_ids=bogus').status_code, 400)
        finally:
            self.ctx.push()

    def test_company_deletion_removes_snapshot_children(self):
        source = {'version': 1, 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      return_value={'cards': [{'id': 'new-card', 'value': 1}]}):
            build_metric_snapshot(self.company.id, '/unused',
                                  endpoint_date=date(2026, 1, 31))
        company_id = self.company.id
        app_module._delete_company_records(self.company)
        db.session.commit()
        self.assertIsNone(db.session.get(Company, company_id))
        self.assertEqual(MetricSnapshot.query.count(), 0)
        self.assertEqual(ActiveMetricSnapshot.query.count(), 0)


if __name__ == '__main__':
    unittest.main()