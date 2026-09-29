"""Regression checks for removing MAX without deleting financial source data."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import inspect, text

from database import (Company, DailyReconciliationAlert, LimitChange,
                      LimitComparison, LimitComparisonResolution, LimitDataset,
                      LimitObject, LimitRowSnapshot, LimitVersion,
                      LimitVersionTombstone, Setting, User, db)
from durable_data import (COUNT_TABLES, LATEST_KEY, _active_models, _manifest_counts,
                          _restore_json, backup, _LocalStore)
from limits_schema import RETIRED_TABLES, migrate_limits_schema


class RetireMaxSchemaTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        self.assertEqual(db.session.execute(text('PRAGMA foreign_keys')).scalar(), 1)
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_migration_preserves_financial_records_and_is_repeatable(self):
        db.session.add(User(id=1, username='operator', password_hash='test'))
        db.session.flush()
        db.session.add(Company(id=1, name='Company', created_by=1))
        db.session.flush()
        db.session.add(LimitDataset(id=1, name='Limits', owner_id=1))
        db.session.flush()
        for version_id, source_date in ((1, date(2025, 1, 1)),
                                        (2, date(2025, 2, 1))):
            db.session.add(LimitVersion(
                id=version_id, dataset_id=1, uploaded_by=1,
                source_date=source_date, source_name=f'{version_id}.xlsx',
                source_path=f'limits/1/{version_id}.xlsx',
                sha256=str(version_id) * 64, status='accepted'))
        db.session.flush()
        db.session.get(LimitDataset, 1).current_version_id = 2
        db.session.add(LimitObject(
            id=1, dataset_id=1, uin='UIN-1', title='Financial object',
            last_seen_version_id=2))
        db.session.flush()
        db.session.add(LimitRowSnapshot(
            version_id=2, object_id=1, source_row=7, cells_json='{}'))
        db.session.add(LimitComparison(
            id=1, old_version_id=1, new_version_id=2, status='ready'))
        db.session.flush()
        db.session.add(LimitComparisonResolution(
            comparison_id=1, issue_key='verified', resolved_by=1))
        db.session.add(LimitChange(
            comparison_id=1, object_id=1, uin='UIN-1',
            category='limit_cost', field_key='amount'))
        db.session.add(LimitVersionTombstone(
            version_id=999, dataset_id=1, source_path='deleted.xlsx'))
        db.session.add(DailyReconciliationAlert(
            company_id=1, account='51', side='debit',
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            difference_cents=500))
        Setting.set('limits_max_token', 'retired')
        Setting.set('limits_bot_dataset_id', '1')
        Setting.set('limits_rotation_needs_backup', '1')
        db.session.commit()

        # The legacy graph is populated, including a queued delivery and
        # alert transition; FK enforcement catches any wrong drop ordering.
        legacy = {
            'limit_recipients': 'id INTEGER PRIMARY KEY',
            'limit_moderation_requests': (
                'id INTEGER PRIMARY KEY, recipient_id INTEGER NOT NULL '
                'REFERENCES limit_recipients(id), dataset_id INTEGER '
                'REFERENCES limit_datasets(id)'),
            'limit_bot_outbound': (
                'id INTEGER PRIMARY KEY, moderation_request_id INTEGER '
                'REFERENCES limit_moderation_requests(id), '
                'status TEXT NOT NULL'),
            'limit_bot_dialogs': (
                'id INTEGER PRIMARY KEY, recipient_id INTEGER '
                'REFERENCES limit_recipients(id), dataset_id INTEGER '
                'REFERENCES limit_datasets(id)'),
            'limit_subscriptions': (
                'id INTEGER PRIMARY KEY, recipient_id INTEGER '
                'REFERENCES limit_recipients(id), dataset_id INTEGER '
                'REFERENCES limit_datasets(id)'),
            'limit_max_events': 'id INTEGER PRIMARY KEY',
            'limit_deliveries': (
                'id INTEGER PRIMARY KEY, recipient_id INTEGER '
                'REFERENCES limit_recipients(id), version_id INTEGER '
                'REFERENCES limit_versions(id), object_id INTEGER '
                'REFERENCES limit_objects(id), status TEXT NOT NULL'),
            'limit_delivery_attempts': (
                'id INTEGER PRIMARY KEY, delivery_id INTEGER '
                'REFERENCES limit_deliveries(id)'),
            'limit_delivery_parts': (
                'id INTEGER PRIMARY KEY, delivery_id INTEGER '
                'REFERENCES limit_deliveries(id)'),
            'daily_reconciliation_alert_deliveries': (
                'id INTEGER PRIMARY KEY, recipient_id INTEGER '
                'REFERENCES limit_recipients(id), outbound_id INTEGER '
                'REFERENCES limit_bot_outbound(id), alert_id INTEGER '
                'REFERENCES daily_reconciliation_alerts(id)'),
        }
        with db.engine.begin() as conn:
            for table, columns in legacy.items():
                conn.execute(text(f'CREATE TABLE {table} ({columns})'))
            for table, columns, values in (
                ('limit_recipients', 'id', '1'),
                ('limit_moderation_requests', 'id, recipient_id, dataset_id', '1, 1, 1'),
                ('limit_bot_outbound', 'id, moderation_request_id, status',
                 "1, 1, 'queued'"),
                ('limit_bot_dialogs', 'id, recipient_id, dataset_id', '1, 1, 1'),
                ('limit_subscriptions', 'id, recipient_id, dataset_id', '1, 1, 1'),
                ('limit_max_events', 'id', '1'),
                ('limit_deliveries',
                 'id, recipient_id, version_id, object_id, status',
                 "1, 1, 2, 1, 'queued'"),
                ('limit_delivery_attempts', 'id, delivery_id', '1, 1'),
                ('limit_delivery_parts', 'id, delivery_id', '1, 1'),
                ('daily_reconciliation_alert_deliveries',
                 'id, recipient_id, outbound_id, alert_id', '1, 1, 1, 1'),
            ):
                conn.execute(text(f'INSERT INTO {table} ({columns}) VALUES ({values})'))
        for _ in range(2):
            migrate_limits_schema()
        tables = set(inspect(db.engine).get_table_names())
        self.assertTrue(set(RETIRED_TABLES).isdisjoint(tables))
        self.assertIn('daily_reconciliation_alerts', tables)
        self.assertEqual(DailyReconciliationAlert.query.one().difference_cents, 500)
        self.assertEqual(LimitDataset.query.one().name, 'Limits')
        self.assertEqual(db.session.get(LimitDataset, 1).current_version_id, 2)
        self.assertEqual(LimitVersion.query.count(), 2)
        self.assertEqual(LimitObject.query.one().title, 'Financial object')
        self.assertEqual(LimitRowSnapshot.query.one().source_row, 7)
        self.assertEqual(LimitComparison.query.one().status, 'ready')
        self.assertEqual(LimitComparisonResolution.query.one().issue_key, 'verified')
        self.assertEqual(LimitChange.query.one().field_key, 'amount')
        self.assertEqual(LimitVersionTombstone.query.one().version_id, 999)
        self.assertIsNone(db.session.get(Setting, 'limits_max_token'))
        self.assertIsNone(db.session.get(Setting, 'limits_bot_dataset_id'))
        self.assertEqual(Setting.get('limits_rotation_needs_backup'), '1')

    def test_old_manifest_bot_counts_not_protected(self):
        class OldRecipient:
            __tablename__ = 'limit_recipients'

        models = _active_models((LimitDataset, OldRecipient, LimitVersion))
        self.assertEqual(models, (LimitDataset, LimitVersion, Setting))
        self.assertNotIn('limit_recipients', COUNT_TABLES)
        old = {'counts': {'limit_recipients': 42, 'limit_datasets': 1}}
        self.assertEqual(_manifest_counts(old, models)['limit_datasets'], 0)
        self.assertNotIn('limit_recipients', {
            model.__tablename__ for model in models})

        db.session.add(User(id=1, username='operator', password_hash='test'))
        db.session.commit()
        restored, _, conflicts = _restore_json(
            db, {'tables': {
                'LimitDataset': [{'id': 7, 'name': 'Recovered',
                                  'scope': '', 'owner_id': 1}],
                'OldRecipient': [{'id': 88, 'max_user_id': 'old'}],
                'Setting': [{'key': 'limits_max_token', 'value': 'old-secret'}],
            }}, (LimitDataset, OldRecipient))
        self.assertEqual((restored, conflicts), (1, 0))
        self.assertEqual(db.session.get(LimitDataset, 7).name, 'Recovered')
        self.assertNotIn('limit_recipients', inspect(db.engine).get_table_names())
        self.assertIsNone(db.session.get(Setting, 'limits_max_token'))

    def test_new_backup_excludes_bot_and_keeps_financial_alert(self):
        class OldRecipient:
            __tablename__ = 'limit_recipients'

        db.session.add(User(id=1, username='operator', password_hash='test'))
        db.session.flush()
        db.session.add(Company(id=1, name='Company', created_by=1))
        db.session.add(DailyReconciliationAlert(
            company_id=1, account='51', side='debit',
            period_start=date(2025, 1, 1), period_end=date(2025, 1, 31),
            difference_cents=500))
        db.session.commit()
        with TemporaryDirectory() as directory:
            store = _LocalStore(directory)
            with patch('durable_data._store', return_value=store):
                result = backup(db, Path(directory) / 'uploads',
                                Path(directory) / 'output',
                                (User, Company, DailyReconciliationAlert,
                                 OldRecipient), 'limits_retirement')
            self.assertTrue(result['promoted'])
            manifest = store.get_json(store.get_json(LATEST_KEY)['manifest'])
            self.assertEqual(
                manifest['table_row_counts']['DailyReconciliationAlert'], 1)
            self.assertNotIn('OldRecipient', manifest['table_chunks'])


if __name__ == '__main__':
    unittest.main()