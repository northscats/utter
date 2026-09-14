"""Regression checks for restore-before-bootstrap startup ordering."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix='osv_durable_bootstrap_')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'startup.db')
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.pop('INITIAL_ADMIN_PASSWORD', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db, init_db
from database import User


class DurableBootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        init_db(perform_durable=False)

    def _empty_schema(self):
        with app.app_context():
            db.drop_all()
            db.create_all()

    @staticmethod
    def _restore_original():
        restored = User(
            username='restored-original',
            password_hash='restored-hash',
            role='admin',
        )
        db.session.add(restored)
        db.session.commit()
        return True

    def test_restore_wins_without_initial_secret(self):
        self._empty_schema()
        os.environ.pop('INITIAL_ADMIN_PASSWORD', None)
        with patch('app._durable_restore', self._restore_original):
            init_db(perform_durable=True)
        with app.app_context():
            self.assertIsNotNone(
                User.query.filter_by(username='restored-original').one())
            self.assertIsNone(User.query.filter_by(username='admin').first())

    def test_restore_wins_with_initial_secret(self):
        self._empty_schema()
        os.environ['INITIAL_ADMIN_PASSWORD'] = 'bootstrap-secret-phrase-2026'
        with patch('app._durable_restore', self._restore_original):
            init_db(perform_durable=True)
        with app.app_context():
            self.assertIsNotNone(
                User.query.filter_by(username='restored-original').one())
            self.assertIsNone(User.query.filter_by(username='admin').first())

    def test_clean_start_without_snapshot_or_secret_fails_closed(self):
        self._empty_schema()
        os.environ.pop('INITIAL_ADMIN_PASSWORD', None)
        with self.assertRaisesRegex(RuntimeError, 'INITIAL_ADMIN_PASSWORD'):
            init_db(perform_durable=True)
        with app.app_context():
            self.assertEqual(User.query.count(), 0)


if __name__ == '__main__':
    unittest.main()