"""Startup uses the authoritative database without App Storage restoration."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='osv_application_startup_')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TEMP.name, 'startup.db')
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (app, db, init_db, _ensure_initial_admin,
                 _finish_application_startup, _limits_startup_lock)
from database import Setting, User


class ApplicationStartupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.create_all()

    def test_empty_database_requires_bootstrap_password(self):
        with app.app_context():
            db.session.query(User).delete()
            db.session.commit()
            with patch.dict(os.environ, {'INITIAL_ADMIN_PASSWORD': ''}):
                with self.assertRaisesRegex(RuntimeError, 'INITIAL_ADMIN_PASSWORD'):
                    _ensure_initial_admin()
            self.assertEqual(User.query.count(), 0)

    def test_bootstrap_admin_is_created_from_configured_password(self):
        with app.app_context():
            db.session.query(User).delete()
            db.session.commit()
            with patch.dict(
                    os.environ,
                    {'INITIAL_ADMIN_PASSWORD': 'bootstrap-secret-phrase-2026'}):
                self.assertTrue(_ensure_initial_admin())
            user = User.query.filter_by(username='admin').one()
            self.assertEqual(user.role, 'admin')
            self.assertTrue(user.password_change_required)

    def test_startup_reconciles_authoritative_database_without_restore(self):
        app.config['APPLICATION_STARTUP_READY'] = False
        with app.app_context(), \
                patch('app._ensure_initial_admin'), \
                patch('app._import_bundled_mup_kgh_account55'), \
                patch('app._reconcile_limits_on_startup') as reconcile, \
                patch('app._retry_pending_osv_source_cleanup',
                      return_value={'completed': 0}), \
                patch('app._ensure_limits_recovery'):
            def mark_history_ready():
                Setting.set('limits_history_ready', 'ready')
                db.session.commit()
            reconcile.side_effect = mark_history_ready
            self.assertTrue(_finish_application_startup())
        self.assertTrue(app.config['APPLICATION_STARTUP_READY'])
        self.assertFalse(app.config['APPLICATION_STARTUP_FAILED'])
        reconcile.assert_called_once_with()

    def test_startup_gate_keeps_liveness_routes_responsive(self):
        app.config['APPLICATION_STARTUP_READY'] = False
        app.config['APPLICATION_STARTUP_FAILED'] = False
        try:
            response = app.test_client().get('/limit-notifications')
        finally:
            app.config['APPLICATION_STARTUP_READY'] = True
        self.assertEqual(200, response.status_code)
        self.assertIn('Приложение запускается', response.get_data(as_text=True))
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_parallel_startup_is_rejected(self):
        _limits_startup_lock.acquire()
        try:
            self.assertFalse(_finish_application_startup())
        finally:
            _limits_startup_lock.release()

    def test_schema_only_initialization_leaves_runtime_gate_for_gunicorn(self):
        init_db(complete_startup=False)
        self.assertTrue(app.config['APPLICATION_STARTUP_READY'])


if __name__ == '__main__':
    unittest.main()
