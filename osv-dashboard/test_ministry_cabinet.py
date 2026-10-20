# -*- coding: utf-8 -*-
"""Regression coverage for Ministry access and immutable XLSX report imports."""
import io
import gzip
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook


_TMP = tempfile.mkdtemp(prefix='ministry_cabinet_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')
os.environ.setdefault('REPLIT_DOMAINS', 'preview.example')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module
from app import app, init_db
from database import (
    Company, CompanyMembership, User, UserModuleAccess, UzterReportImport,
    UzterReportRow, CompanyDadataSnapshot, Setting, db,
)
from ministry_geo import MUNICIPALITIES


def _workbook(*rows, supplier='Поставщик Тест', debt_resource='Электроэнергия',
              report_month='02'):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Поставщик ТЭР_Предприятия'
    sheet.append([None, 'Наименование поставщика', 'ИНН поставщика',
                  'КПП поставщика', 'Тип ТЭР/Услуги', 'Тип потребителя',
                  'Отчетный период: Месяц', 'Отчетный период: Год',
                  'Единица измерения:'])
    sheet.append([None, supplier, '1111111111', '111101001',
                  debt_resource, 'Предприятие ЖКХ', report_month, 2026, 'руб.'])
    sheet.append([])
    sheet.append([
        '№ строки', 'Наименование потребителя', 'ИНН потребителя',
        'КПП потребителя', 'Состояние', 'Детализация потребителя',
        'Административная принадлежность потребителя',
        'Поставлено с начала года, (кВт)',
        'Поставлено в отчетном периоде, (кВт)',
        'Начислено с начала года, (руб)',
        'Начислено в отчетном периоде, (руб)',
        'Поступления с начала года, (руб)',
        'Поступления в отчетном периоде, (руб)',
        'Просроченная задолженность, всего, (руб)',
    ])
    sheet.append(list(range(1, 15)))
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _enterprise(name, inn, debt, *, territory='Балашиха', detail='РСО'):
    return [
        1, name, inn, '111101001', None, detail, territory,
        None, None, None, 0, None, None, debt,
    ]


def _login(client, user):
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
        session['password_version'] = int(user.password_version or 1)


class MinistryCabinetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, DADATA_DISABLED=True)
        app.config['UPLOADS'] = os.path.join(_TMP, 'uploads')
        init_db(perform_durable=False)
        with app.app_context():
            admin = User.query.filter_by(role='admin').first()
            if admin is None:
                admin = User(username='sysadmin', password_hash='unused', role='admin')
                db.session.add(admin)
                db.session.flush()
            admin.password_change_required = False
            ministry = User(username='ministry-test', password_hash='unused', role='user')
            supplier = User(username='supplier-test', password_hash='unused', role='user')
            directory_user = User(
                username='directory-test', password_hash='unused', role='user')
            db.session.add_all([ministry, supplier, directory_user])
            db.session.flush()
            company = Company(
                name='Поставщик Тест', inn='1111111111',
                created_by=admin.id, status='active')
            db.session.add(company)
            db.session.flush()
            db.session.add_all([
                UserModuleAccess(user_id=ministry.id, module_key='ministry_cabinet'),
                UserModuleAccess(user_id=ministry.id, module_key='directories'),
                UserModuleAccess(user_id=directory_user.id, module_key='directories'),
                CompanyMembership(user_id=ministry.id, company_id=company.id),
                CompanyMembership(user_id=directory_user.id, company_id=company.id),
            ])
            db.session.commit()
            cls.ids = {
                'admin': admin.id, 'ministry': ministry.id,
                'supplier': supplier.id, 'directory': directory_user.id,
                'company': company.id,
            }

    def setUp(self):
        self.client = app.test_client()

    def test_forced_password_change_lands_in_ministry_cabinet(self):
        initial_password = 'J3@uE6_hK9wN1pQ5'
        updated_password = 'R6!Xn4_vT9qP2kM8'
        with app.app_context():
            ministry = db.session.get(User, self.ids['ministry'])
            original_hash = ministry.password_hash
            ministry.password_hash = app_module._hash_password(initial_password)
            ministry.password_change_required = True
            db.session.commit()

        client = app.test_client()
        login_page = client.get('/login', base_url='https://preview.example')
        token = re.search(
            r'name="login_csrf_token" value="([^"]+)"',
            login_page.get_data(as_text=True)).group(1)
        login_response = client.post(
            '/login',
            base_url='https://preview.example',
            data={
                'username': 'ministry-test',
                'password': initial_password,
                'login_csrf_token': token,
            },
            headers={'Referer': 'https://preview.example/login'},
        )
        self.assertEqual(
            login_response.status_code, 302,
            login_response.get_data(as_text=True)[:1200])
        self.assertTrue(login_response.headers['Location'].endswith(
            '/password/change-required'))
        changed = client.post(
            '/password/change-required',
            base_url='https://preview.example',
            data={
                'current_password': initial_password,
                'new_password': updated_password,
                'new_password_repeat': updated_password,
            },
            headers={'Referer': 'https://preview.example/password/change-required'},
        )
        self.assertTrue(changed.headers['Location'].endswith('/ministry'))
        with app.app_context():
            ministry = db.session.get(User, self.ids['ministry'])
            ministry.password_hash = original_hash
            ministry.password_change_required = False
            db.session.commit()

    def _preview_apply(self, raw, filename):
        response = self.client.post(
            '/ministry/import/preview',
            data={
                'provider_id': str(self.ids['company']),
                'file': (io.BytesIO(raw), filename),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        preview = response.get_json()
        apply_response = self.client.post(
            '/ministry/import/apply',
            data={'preview_token': preview['previewToken']},
        )
        return preview, apply_response

    def test_access_and_source_preserving_versioned_import_flow(self):
        with app.app_context():
            ministry = db.session.get(User, self.ids['ministry'])
            supplier = db.session.get(User, self.ids['supplier'])
            directory_user = db.session.get(User, self.ids['directory'])
            admin = db.session.get(User, self.ids['admin'])

        _login(self.client, supplier)
        self.assertEqual(self.client.get('/ministry').status_code, 403)
        self.assertEqual(self.client.get('/ministry/api/data').status_code, 403)
        self.assertEqual(self.client.get('/modules').status_code, 403)

        _login(self.client, directory_user)
        self.assertEqual(self.client.get('/directories/companies').status_code, 200)
        self.assertEqual(self.client.get('/home').status_code, 403)

        _login(self.client, ministry)
        self.assertEqual(self.client.get('/ministry').status_code, 200)
        self.assertEqual(self.client.get('/modules').status_code, 403)
        self.assertEqual(self.client.get('/settings').status_code, 403)
        self.assertEqual(self.client.get('/directories/companies').status_code, 200)

        february = _workbook(
            _enterprise('ООО Нулевой долг', '1234567890', 0),
            _enterprise('Прочие потребители', None, None, detail='Прочие'),
        )
        preview, response = self._preview_apply(february, 'Февраль.xlsx')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(preview['period'], '2026-02')
        self.assertEqual(preview['dataRows'], 2)
        self.assertEqual(response.get_json()['ok'], True)

        march_duplicate = _workbook(
            _enterprise('ООО Повтор', '1234567890', 10),
            _enterprise('ООО Повтор', '1234567890', 11),
            report_month='03',
        )
        _, response = self._preview_apply(march_duplicate, 'Март-повтор.xlsx')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        # Same report period and owner, new file becomes a new version, not an overwrite.
        march_corrected = _workbook(
            _enterprise('ООО Нулевой долг', '1234567890', 12),
            report_month='03',
        )
        _, response = self._preview_apply(march_corrected, 'Март-исправление.xlsx')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        with app.app_context():
            imports = UzterReportImport.query.order_by(
                UzterReportImport.id).all()
            march_versions = [
                item for item in imports
                if item.report_year == 2026 and item.report_month == 3
                and item.resource_type == 'Электроэнергия'
                and item.data_row_count <= 2
            ]
            self.assertEqual(len(march_versions), 2)
            self.assertEqual(sorted(item.version for item in march_versions), [1, 2])
            self.assertTrue(any(item.source_filename == 'Февраль.xlsx' for item in imports))
            self.assertEqual(UzterReportRow.query.filter(
                UzterReportRow.import_id == imports[0].id).count(), 2)

        february_rows = self.client.get(
            '/ministry/api/data?month=2026-02&resource=all'
        ).get_json()['rows']
        self.assertTrue(any(row['debt'] == 0 for row in february_rows))
        self.assertTrue(any(row['debt'] is None and not row['inn']
                            for row in february_rows))
        self.assertTrue(any(row['isGroupRow'] for row in february_rows))
        february_summary = self.client.get(
            '/ministry/api/data?month=2026-02&resource=all'
        ).get_json()['summary']
        self.assertEqual(february_summary['missingInn'], 0)
        self.assertEqual(february_summary['groupRows'], 1)

        march = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all&q=Нулевой'
        ).get_json()
        self.assertEqual(march['summary']['dataRows'], 1)
        self.assertEqual(march['rows'][0]['debt'], 12)
        self.assertTrue(march['rows'][0]['comparison']['available'])
        self.assertEqual(march['rows'][0]['comparison']['delta'], 12)
        self.assertEqual(march['summary']['verifiedRegionalTotal'], False)
        self.assertTrue(march['map']['available'])
        self.assertEqual(march['map']['geometryUrl'],
                         '/ministry/api/map-geometry')
        self.assertEqual(len(march['map']['municipalities']), 56)
        dashboard_response = self.client.get('/ministry')
        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_html = dashboard_response.get_data(as_text=True)
        self.assertNotIn('class="page-head"', dashboard_html)
        self.assertNotIn('Долговые отчёты поставщиков', dashboard_html)
        self.assertIn('class="page-actions"', dashboard_html)
        self.assertIn('id="widgets-toggle"', dashboard_html)
        self.assertIn('id="open-import"', dashboard_html)
        self.assertIn('id="map-mode-toggle"', dashboard_html)
        self.assertIn('class="left-panel-header"', dashboard_html)
        self.assertIn('id="map-mode-icon"', dashboard_html)
        self.assertIn('id="overview-mode-icon"', dashboard_html)
        self.assertNotIn('id="map-mode-label"', dashboard_html)
        self.assertNotIn('К показателям', dashboard_html)
        self.assertIn('id="map-panel"', dashboard_html)
        self.assertNotIn('id="map-title"', dashboard_html)
        self.assertNotIn('id="map-reset"', dashboard_html)
        self.assertIn('id="filter-district"', dashboard_html)
        self.assertNotIn('id="map-note"', dashboard_html)
        self.assertNotIn('сведения об источнике и лицензии не указаны',
                         dashboard_html)
        self.assertNotIn('class="notice"', dashboard_html)
        self.assertNotIn('Только подтверждённые данные', dashboard_html)
        geometry_response = self.client.get(march['map']['geometryUrl'])
        self.assertEqual(geometry_response.status_code, 200)
        self.assertEqual(geometry_response.headers.get('Content-Encoding'),
                         'gzip')
        geometry = json.loads(gzip.decompress(geometry_response.data))
        geometry_response.close()
        self.assertEqual(len(geometry['features']), 56)
        self.assertEqual(
            {feature['properties']['territory']
             for feature in geometry['features']},
            {name for name, _kind in MUNICIPALITIES},
        )
        district_rows = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all&district=Балашиха'
        ).get_json()['rows']
        self.assertEqual(len(district_rows), 1)
        self.assertEqual(district_rows[0]['district'], 'Балашиха')
        self.assertEqual(self.client.get(
            '/ministry/api/data?month=2026-03&resource=all&district=unassigned'
        ).get_json()['rows'], [])

        history = self.client.get(
            '/ministry/api/debtors/1234567890'
        ).get_json()
        self.assertGreaterEqual(len(history['history']), 2)
        self.assertEqual(history['rows'][0]['debt'], 12)

        download_id = march['rows'][0]['importId']
        download = self.client.get(f'/ministry/import/{download_id}/file')
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b'PK'))
        download.close()

        duplicate = self.client.post(
            '/ministry/import/preview',
            data={
                'provider_id': str(self.ids['company']),
                'file': (io.BytesIO(march_corrected), 'Март-исправление-копия.xlsx'),
            },
            content_type='multipart/form-data',
        ).get_json()
        duplicate_response = self.client.post(
            '/ministry/import/apply',
            data={'preview_token': duplicate['previewToken']},
        )
        self.assertEqual(duplicate_response.status_code, 409)
        with app.app_context():
            self.assertEqual(
                UzterReportImport.query.filter_by(
                    file_sha256=imports[-1].file_sha256).count(), 1)

        encrypted = self.client.post(
            '/ministry/import/preview',
            data={
                'provider_id': str(self.ids['company']),
                'file': (io.BytesIO(bytes.fromhex('d0cf11e0a1b11ae1') + b'locked'),
                         'Защищённая-книга.xlsx'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(encrypted.status_code, 422)
        self.assertIn('не обходится', encrypted.get_json()['error'])

        # DaData is stored as a dated snapshot; its result never overwrites the directory.
        with app.app_context():
            company = db.session.get(Company, self.ids['company'])
            original_name = company.name
            original_address = company.address
            Setting.set('dadata_enabled', '1')
            db.session.commit()
        app.config['DADATA_DISABLED'] = False
        party = {'data': {'inn': '1111111111', 'name': {'short_with_opf': 'Новое имя'}}}
        normalized = {
            'inn': '1111111111', 'name': 'Новое имя',
            'actuality_date': '2026-01-01',
        }
        with patch.object(app_module, '_dadata_token', return_value='test-token'), \
                patch.object(app_module, '_find_dadata_party',
                             return_value=(party, normalized)):
            dadata = self.client.post(
                '/ministry/api/dadata/lookup',
                json={'inn': '1111111111'}).get_json()
        self.assertTrue(dadata['ok'])
        self.assertEqual(dadata['snapshot']['source'], 'DaData')
        with app.app_context():
            company = db.session.get(Company, self.ids['company'])
            self.assertEqual(company.name, original_name)
            self.assertEqual(company.address, original_address)
            self.assertEqual(
                CompanyDadataSnapshot.query.filter_by(
                    company_id=company.id, status='success').count(), 1)
            Setting.set('dadata_enabled', '0')
            db.session.commit()
        app.config['DADATA_DISABLED'] = True

        _login(self.client, admin)
        self.assertEqual(self.client.get('/modules').status_code, 200)
        self.assertEqual(self.client.get('/ministry').status_code, 200)


if __name__ == '__main__':
    unittest.main()
