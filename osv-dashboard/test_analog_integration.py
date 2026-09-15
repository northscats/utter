# -*- coding: utf-8 -*-
"""Route-level regressions for the analogue module."""
import os
import pathlib
import tempfile
import unittest
import json
import zipfile
from io import BytesIO

_tmp = tempfile.mkdtemp(prefix='analog_route_test_')
os.environ['DATABASE_URL'] = f'sqlite:///{_tmp}/analog.db'
os.environ['INITIAL_ADMIN_PASSWORD'] = 'analog-admin-passphrase-2026'
os.environ.setdefault('ENCRYPTION_KEY',
                      'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.setdefault('REPLIT_DOMAINS', 'preview.example')

from app import app, init_db  # noqa: E402
from database import (AnalogDocument, AnalogImport, AnalogNetworkAttribute,
                      AnalogPassport, User, db)  # noqa: E402
from analog_migration import import_json_v4  # noqa: E402


ROOT = pathlib.Path(__file__).parent.parent / 'attached_assets'


class AnalogRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['INITIAL_ADMIN_PASSWORD'] = 'analog-admin-passphrase-2026'
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        init_db()
        cls.conclusion = next(ROOT.glob('*signature_*.xml')).read_bytes()
        cls.ssr = next(ROOT.glob('*.gge')).read_bytes()

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            self.admin = User.query.filter_by(role='admin').first()
            if not self.admin:
                self.admin = User(username='analog-admin', password_hash='unused',
                                  role='admin')
                db.session.add(self.admin)
            self.admin.password_change_required = False
            db.session.commit()
            self.admin_id = self.admin.id
            self.admin_version = int(self.admin.password_version or 1)
        self.login_values(self.admin_id, self.admin_version)

    def login_values(self, user_id, version=1):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['password_version'] = version

    def files(self, conclusion=None):
        return {'conclusion': (BytesIO(conclusion or self.conclusion), 'conclusion.xml'),
                'ssr': (BytesIO(self.ssr), 'summary.gge')}

    def test_preview_confirm_duplicate_and_raw_paths(self):
        conclusion = self.conclusion + b'\n'
        response = self.client.post('/analog-search/import/preview',
                                    data=self.files(conclusion),
                                    content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200)
        preview = response.get_json()
        self.assertEqual(preview['status'], 'preview')
        confirmed = self.client.post(
            f"/analog-search/import/{preview['import_id']}/confirm",
            json={})
        self.assertEqual(confirmed.status_code, 200)
        passport_id = confirmed.get_json()['passport_id']
        with app.app_context():
            row = db.session.get(AnalogImport, preview['import_id'])
            self.assertTrue(row.raw_conclusion_path.startswith('uploads/0/analog_sources/'))
            self.assertTrue(AnalogPassport.query.get(passport_id).current_version_id)
            documents = AnalogDocument.query.filter_by(
                passport_id=passport_id).all()
            self.assertTrue(any(item.parent_id is not None for item in documents))
            self.assertTrue(any(item.node_kind == 'section' for item in documents))
        duplicate = self.client.post('/analog-search/import/preview',
                                     data=self.files(conclusion),
                                     content_type='multipart/form-data')
        self.assertEqual(duplicate.get_json()['status'], 'duplicate')

    def test_global_abs_boundary_and_pagination(self):
        with app.app_context():
            for number, capacity in enumerate((90, 110, 89, 111), 1):
                db.session.add(AnalogPassport(
                    title=f'boundary-{number}', capacity_kw=capacity,
                    object_type='котельная', created_by=self.admin.id))
            db.session.commit()
        response = self.client.get('/analog-search/api/search?capacity_kw=100&per_page=2')
        payload = response.get_json()
        self.assertEqual(payload['good'], 2)
        self.assertEqual(payload['total'], 4)
        self.assertEqual(len(payload['items']), 2)
        self.assertTrue(all(item['good'] for item in payload['items']))

    def test_thousand_records_are_paginated_by_db(self):
        with app.app_context():
            before = AnalogPassport.query.filter_by(archived=False).count()
            db.session.bulk_save_objects([
                AnalogPassport(title=f'bulk-{index}', capacity_kw=100 + (index % 30),
                               object_type='котельная', created_by=self.admin_id)
                for index in range(1000)])
            db.session.commit()
        response = self.client.get(
            '/analog-search/api/search?capacity_kw=100&page=20&per_page=25')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['total'], before + 1000)
        self.assertEqual(len(payload['items']), 25)

    def test_json_v4_preserves_legacy_and_network_fields(self):
        archive = next(ROOT.glob('*Объекты_аналоги*.zip'))
        with zipfile.ZipFile(archive) as stream:
            name = next(item for item in stream.namelist()
                        if item.endswith('.json') and not item.startswith('__'))
            payload = json.loads(stream.read(name))
        with app.app_context():
            report = import_json_v4(payload, user_id=self.admin_id)
            self.assertEqual(report['transformed'], len(payload['records']))
            self.assertGreater(report['fields_preserved'], 0)
            self.assertGreater(AnalogPassport.query.filter(
                AnalogPassport.legacy_json != '{}').count(), 0)

    def test_reimport_target_and_same_identity_version(self):
        first = self.client.post('/analog-search/import/preview',
                                 data=self.files(self.conclusion + b'\t'),
                                 content_type='multipart/form-data').get_json()
        confirmed = self.client.post(
            f"/analog-search/import/{first['import_id']}/confirm", json={}).get_json()
        target = confirmed['passport_id']
        with app.app_context():
            baseline = len(AnalogPassport.query.get(target).versions)
        second = self.client.post(
            f'/analog-search/import/preview?passport_id={target}',
            data=self.files(self.conclusion + b' \n'),
            content_type='multipart/form-data').get_json()
        second_confirm = self.client.post(
            f"/analog-search/import/{second['import_id']}/confirm", json={}).get_json()
        self.assertEqual(second_confirm['passport_id'], target)
        with app.app_context():
            self.assertEqual(len(AnalogPassport.query.get(target).versions), baseline + 1)

    def test_network_create_api_and_html_persist_typed_attrs(self):
        api = self.client.post('/analog-search/passports', json={
            'title': 'Сеть API integration', 'object_type': 'тепловая сеть',
            'network_length_m': '125.5', 'network_diameter_mm': '273'})
        self.assertEqual(api.status_code, 201)
        api_id = api.get_json()['passport_id']
        html = self.client.post('/analog-search/passports', data={
            'title': 'Сеть HTML integration', 'object_type': 'тепловая сеть',
            'network_length_m': '80', 'network_diameter_mm': '159'},
            headers={'Accept': 'text/html'})
        self.assertEqual(html.status_code, 302)
        with app.app_context():
            attrs = AnalogNetworkAttribute.query.filter_by(passport_id=api_id).all()
            self.assertEqual({item.key for item in attrs},
                             {'length_m', 'diameter_mm'})
            self.assertEqual({item.unit for item in attrs}, {'м', 'мм'})
            self.assertIsNotNone(AnalogPassport.query.filter_by(
                title='Сеть HTML integration').first())

    def test_html_preview_and_confirm_redirect(self):
        response = self.client.post('/analog-search/import/preview',
                                    data=self.files(),
                                    content_type='multipart/form-data',
                                    headers={'Accept': 'text/html'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Подтвердить импорт', response.get_data(as_text=True))

    def test_confirm_is_owned_by_creator_or_admin(self):
        response = self.client.post('/analog-search/import/preview',
                                    data=self.files(self.conclusion + b'\n\n'),
                                    content_type='multipart/form-data')
        import_id = response.get_json()['import_id']
        with app.app_context():
            user = User(username='analog-regular', password_hash='unused', role='user')
            user.password_change_required = False
            db.session.add(user)
            db.session.commit()
            user_id, user_version = user.id, int(user.password_version or 1)
        self.login_values(user_id, user_version)
        denied = self.client.post(f'/analog-search/import/{import_id}/confirm',
                                  json={})
        self.assertEqual(denied.status_code, 403)


if __name__ == '__main__':
    unittest.main()