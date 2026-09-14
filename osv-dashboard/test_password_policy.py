"""Isolated authentication/password-policy regression tests.

This file deliberately owns a temporary SQLite database and never touches the
development database.  It can be run alone with ``python test_password_policy.py``.
"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest


_TMP = tempfile.mkdtemp(prefix='osv_password_policy_')
_DB = os.path.join(_TMP, 'auth.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_DB}'
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ['INITIAL_ADMIN_PASSWORD'] = 'initial-admin-passphrase-2026'
os.environ['REPLIT_DOMAINS'] = 'preview.example'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Exercise migration from the pre-policy users table, before SQLAlchemy creates
# the rest of the application schema.
with sqlite3.connect(_DB) as connection:
    connection.execute("""
        CREATE TABLE users (
          id INTEGER PRIMARY KEY,
          username VARCHAR(80) NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          role VARCHAR(20) NOT NULL DEFAULT 'user',
          created_at DATETIME
        )
    """)
    connection.execute(
        "INSERT INTO users (id, username, password_hash, role) "
        "VALUES (1, 'legacy', 'not-used', 'user')")

from app import (app, _check_password, _hash_password, init_db,
                 validate_password)
from database import AuditLog, User, db


class PasswordPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        init_db(perform_durable=False)

    def setUp(self):
        with app.app_context():
            admin = User.query.filter_by(username='policy-admin').first()
            if admin is None:
                admin = User(
                    username='policy-admin',
                    password_hash=_hash_password(
                        'cloud-lantern-passphrase-2026'),
                    role='admin')
                db.session.add(admin)
                db.session.commit()
            self.admin_id = admin.id
            self.admin_version = int(admin.password_version or 1)
        self.client = app.test_client()

    def _session(self, user_id, version=1, include_version=True):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            if include_version:
                session['password_version'] = version

    def test_policy_length_spaces_unicode_and_denylist(self):
        self.assertIsNotNone(validate_password('x' * 14, 'user'))
        self.assertIsNone(validate_password(' leading and trailing ', 'user'))
        self.assertIsNone(validate_password('😀' * 15, 'user'))
        self.assertIsNotNone(validate_password('пароль123456789', 'user'))
        self.assertIsNotNone(validate_password('user123', 'user'))
        self.assertIsNotNone(validate_password('x' * 4097, 'user'))
        self.assertIsNone(validate_password('x' * 73, 'user'))

    def test_full_utf8_hash_is_versioned_and_exact(self):
        value = '  ' + ('😀' * 20) + '  '
        hashed = _hash_password(value)
        self.assertTrue(hashed.startswith('bcrypt-sha256$1$'))
        self.assertTrue(_check_password(value, hashed))
        self.assertFalse(_check_password(value.strip(), hashed))
        # Existing raw bcrypt hashes remain accepted by the compatibility path.
        import bcrypt
        raw = bcrypt.hashpw(b'legacy-password', bcrypt.gensalt()).decode()
        self.assertTrue(_check_password('legacy-password', raw))

    def test_migration_is_idempotent_and_preserves_existing_user(self):
        with app.app_context():
            legacy = User.query.filter_by(username='legacy').one()
            self.assertFalse(legacy.password_change_required)
            self.assertEqual(legacy.password_version, 1)
        init_db(perform_durable=False)
        with app.app_context():
            self.assertEqual(User.query.filter_by(username='legacy').count(), 1)

    def test_missing_session_version_is_rejected(self):
        with app.app_context():
            user = User(username='session-user', password_hash=_hash_password(
                'session-user-password-2026'), role='user')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        self._session(user_id, include_version=False)
        response = self.client.get('/modules')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_reset_forced_change_audit_and_invalidation(self):
        with app.app_context():
            admin = db.session.get(User, self.admin_id)
            target = User(username='policy-target',
                          password_hash=_hash_password(
                              'policy-target-password-2026'), role='user')
            db.session.add_all([admin, target])
            db.session.commit()
            admin_id = admin.id
            before = target.password_version
            target_id = target.id
        self._session(admin_id, version=self.admin_version)
        response = self.client.post(
            f'/users/{target_id}/set-password',
            data={'password': '  target-reset-😀-2026  ',
                  'password_repeat': '  target-reset-😀-2026  '})
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            target = db.session.get(User, target_id)
            self.assertTrue(target.password_change_required)
            self.assertGreater(target.password_version, before)
            self.assertTrue(_check_password(
                '  target-reset-😀-2026  ', target.password_hash))
            self.assertTrue(AuditLog.query.filter_by(
                action='user_password_changed').count() >= 1)

    def test_admin_create_and_forced_current_password_change(self):
        with app.app_context():
            admin_id = self.admin_id
        self._session(admin_id, version=self.admin_version)
        response = self.client.post('/users/create', data={
            'username': 'created-policy-user',
            'password': ' created-policy-passphrase-2026 ',
            'password_repeat': ' created-policy-passphrase-2026 ',
            'role': 'user',
        })
        self.assertEqual(response.status_code, 303)
        with app.app_context():
            user = User.query.filter_by(
                username='created-policy-user').one()
            self.assertTrue(user.password_change_required)
            user_id = user.id
            user_version = int(user.password_version or 1)
        self._session(user_id, version=user_version)
        forced = self.client.get('/modules')
        self.assertEqual(forced.status_code, 302)
        self.assertIn('/password/change-required', forced.headers['Location'])
        changed = self.client.post('/password/change-required', data={
            'current_password': ' created-policy-passphrase-2026 ',
            'new_password': ' changed-policy-passphrase-2026 ',
            'new_password_repeat': ' changed-policy-passphrase-2026 ',
        })
        self.assertEqual(changed.status_code, 302)
        with app.app_context():
            user = db.session.get(User, user_id)
            self.assertFalse(user.password_change_required)
            self.assertTrue(AuditLog.query.filter_by(
                action='password_changed').count() >= 1)

    def test_origin_does_not_trust_forwarded_host_and_logout_is_post_only(self):
        page = self.client.get('/login', base_url='http://localhost')
        token = re.search(
            r'name="login_csrf_token" value="([^"]+)"',
            page.get_data(as_text=True)).group(1)
        attack = self.client.post(
            '/login', base_url='http://localhost',
            data={'username': 'legacy', 'password': 'anything',
                  'login_csrf_token': token},
            headers={'Origin': 'https://evil.example',
                     'X-Forwarded-Host': 'evil.example'})
        self.assertEqual(attack.status_code, 403)

        with app.app_context():
            user = User.query.filter_by(username='session-user').one()
            user_id = user.id
            user_version = int(user.password_version or 1)
        self._session(user_id, version=user_version)
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            self.assertEqual(self.client.get('/logout').status_code, 405)
            self.assertIn(self.client.post('/logout').status_code, (400, 403))
        finally:
            app.config['WTF_CSRF_ENABLED'] = False


if __name__ == '__main__':
    unittest.main()