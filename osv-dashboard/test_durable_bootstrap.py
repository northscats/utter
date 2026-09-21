"""Regression checks for restore-before-bootstrap startup ordering."""
import os
import importlib.util
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_TMP = tempfile.mkdtemp(prefix='osv_durable_bootstrap_')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'startup.db')
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.pop('INITIAL_ADMIN_PASSWORD', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (app, db, init_db, _finish_durable_startup,
                 _limits_history_ready, _limits_startup_lock)
from database import Setting, User


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

    def test_failed_background_restore_becomes_retryable_error(self):
        self._empty_schema()
        with patch('app._durable_restore', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Durable restore failed'):
                _finish_durable_startup(restore=True)
        with app.app_context():
            self.assertEqual('error', Setting.get('limits_history_ready'))
            self.assertEqual('restore', Setting.get('limits_startup_error'))
            self.assertFalse(_limits_history_ready.is_set())

    def test_successful_retry_opens_history_gate(self):
        self._empty_schema()
        with patch('app._durable_restore', return_value=False):
            with self.assertRaises(RuntimeError):
                _finish_durable_startup(restore=True)
        with patch('app._durable_restore', return_value=True), \
                patch('app._ensure_initial_admin_after_restore'), \
                patch('app._import_bundled_mup_kgh_account55'), \
                patch('app._reconcile_limits_after_restore') as reconcile:
            def ready():
                Setting.set('limits_history_ready', 'ready')
                db.session.commit()
                _limits_history_ready.set()
            reconcile.side_effect = ready
            self.assertTrue(_finish_durable_startup(restore=True))
        with app.app_context():
            self.assertEqual('ready', Setting.get('limits_history_ready'))
            self.assertTrue(_limits_history_ready.is_set())

    def test_parallel_startup_retry_is_rejected(self):
        _limits_startup_lock.acquire()
        try:
            self.assertFalse(_finish_durable_startup(restore=True))
        finally:
            _limits_startup_lock.release()

    def test_gunicorn_worker_runs_durable_startup_before_imap(self):
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'gunicorn.conf.py')
        spec = importlib.util.spec_from_file_location(
            'test_gunicorn_config', config_path)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        calls = []

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        server = SimpleNamespace(log=MagicMock())
        worker = SimpleNamespace(pid=123)
        with patch('app.init_db', side_effect=lambda **_kw: calls.append('schema')), \
                patch('app._finish_durable_startup',
                      side_effect=lambda **_kw: calls.append('durable')), \
                patch('app._ensure_imap_thread',
                      side_effect=lambda: calls.append('imap')), \
                patch.object(config.threading, 'Thread', ImmediateThread):
            config.post_fork(server, worker)
        self.assertEqual(['schema', 'durable', 'imap'], calls)


if __name__ == '__main__':
    unittest.main()