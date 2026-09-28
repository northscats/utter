# -*- coding: utf-8 -*-
"""Isolated transaction tests for limits lifecycle."""
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix='limits_service_test_')
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP}/limits.db'
os.environ['INITIAL_ADMIN_PASSWORD'] = 'Cedar!river7Orbit#lantern'
os.environ.setdefault('ENCRYPTION_KEY',
                      'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from database import LimitChange, LimitDataset, LimitObject, LimitVersion, User, db
from limits_service import accept_version, create_comparison


def snapshot(value):
    columns = [
        {'key': 'uin', 'label': 'УИН', 'category': 'identifier',
         'year': None, 'source': None},
        {'key': 'list_type', 'label': 'Вид перечня', 'category': 'programme',
         'year': None, 'source': None},
        {'key': 'ap_status', 'label': 'Статус', 'category': 'status',
         'year': None, 'source': None},
        {'key': 'gp_code', 'label': 'ГП', 'category': 'programme', 'year': None, 'source': None},
        {'key': 'pp_code', 'label': 'ПП', 'category': 'programme', 'year': None, 'source': None},
        {'key': 'om_code', 'label': 'ОМ', 'category': 'programme', 'year': None, 'source': None},
        {'key': 'dm_code', 'label': 'ДМ', 'category': 'programme', 'year': None, 'source': None},
        {'key': 'limit_cost', 'label': 'Стоимость', 'category': 'limit_cost',
         'year': None, 'source': None},
    ]
    values = {
        'uin': ('100.200', 'text'), 'list_type': ('x', 'text'), 'ap_status': ('1', 'text'),
        'gp_code': ('1', 'text'), 'pp_code': ('2', 'text'), 'om_code': ('3', 'text'),
        'dm_code': ('4', 'text'), 'limit_cost': (value, 'money'),
    }
    return {'columns': columns, 'warnings': [], 'metadata': {},
            'rows': [{'uin': '100.200', 'row': 5, 'cells': {
                key: {'raw': value, 'value': value, 'kind': kind, 'cell': 'A5'}
                for key, (value, kind) in values.items()}}]}


class LimitsLifecycleTests(unittest.TestCase):
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
            admin = User(username='limits-admin', password_hash='x', role='admin')
            db.session.add(admin)
            db.session.flush()
            self.admin_id = admin.id
            dataset = LimitDataset(name='Тест', owner_id=admin.id)
            db.session.add(dataset)
            db.session.flush()
            self.dataset_id = dataset.id
            db.session.commit()

    def _version(self, data, previous=None):
        with app.app_context():
            row = LimitVersion(dataset_id=self.dataset_id, uploaded_by=self.admin_id,
                               source_date=date(2026, 1, 1), source_name='a.xlsx',
                               source_path='/tmp/a.xlsx', sha256=os.urandom(32).hex(),
                               snapshot_json=__import__('json').dumps(data),
                               status='review', previous_version_id=previous)
            db.session.add(row)
            db.session.flush()
            obj = LimitObject.query.filter_by(dataset_id=self.dataset_id, uin='100.200').first()
            if not obj:
                obj = LimitObject(dataset_id=self.dataset_id, uin='100.200', title='Объект')
                db.session.add(obj)
            obj.is_present, obj.last_seen_version_id = True, row.id
            db.session.commit()
            return row.id

    def test_acceptance_preserves_financial_comparison(self):
        old_id = self._version(snapshot('10'))
        with app.app_context():
            accept_version(old_id, self.admin_id)
        new_id = self._version(snapshot('15'), old_id)
        with app.app_context():
            comparison = create_comparison(old_id, new_id)
            self.assertEqual('ready', comparison.status)
            accept_version(new_id, self.admin_id)
            self.assertEqual(new_id, db.session.get(LimitDataset, self.dataset_id).current_version_id)
            self.assertTrue(LimitChange.query.filter_by(comparison_id=comparison.id).count())


if __name__ == '__main__':
    unittest.main()