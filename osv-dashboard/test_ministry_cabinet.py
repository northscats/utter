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
        march_by_inn = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all&q=1234567890'
        ).get_json()
        self.assertEqual(march_by_inn['pagination']['total'], 1)
        self.assertEqual(march_by_inn['rows'][0]['inn'], '1234567890')
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
        self.assertNotIn('class="page-actions"', dashboard_html)
        self.assertNotIn('id="widgets-toggle"', dashboard_html)
        self.assertNotIn('Настроить вид', dashboard_html)
        self.assertNotIn('id="widget-menu"', dashboard_html)
        self.assertNotIn('data-widget-toggle', dashboard_html)
        self.assertIn('id="summary-grid"', dashboard_html)
        self.assertIn('id="monthly-chart"', dashboard_html)
        self.assertIn('id="map-mode-toggle"', dashboard_html)
        self.assertIn('class="left-panel-header"', dashboard_html)
        self.assertIn('id="map-mode-icon"', dashboard_html)
        self.assertIn('id="overview-mode-icon"', dashboard_html)
        self.assertNotIn('id="map-mode-label"', dashboard_html)
        self.assertNotIn('К показателям', dashboard_html)
        self.assertIn('id="map-panel"', dashboard_html)
        self.assertNotIn('data-widget=', dashboard_html)
        self.assertNotIn('id="map-title"', dashboard_html)
        self.assertNotIn('id="map-reset"', dashboard_html)
        self.assertNotIn('<section class="filters"', dashboard_html)
        self.assertNotIn('id="filter-month"', dashboard_html)
        self.assertNotIn('id="filter-resource"', dashboard_html)
        self.assertNotIn('id="filter-query"', dashboard_html)
        table_start = dashboard_html.index(
            '<section class="panel table-panel"')
        rows_status = dashboard_html.index(
            '<div id="rows-status"', table_start)
        table_header = dashboard_html[table_start:rows_status]
        table_panel = dashboard_html[
            table_start:dashboard_html.index('</section>', table_start)]
        self.assertIn('id="open-import"', table_header)
        self.assertIn('id="reset-filters"', table_header)
        self.assertIn('aria-label="Загрузить отчёт"', table_header)
        self.assertNotIn('>Загрузить отчёт<', table_header)
        self.assertIn('id="org-filter-toggle"', table_panel)
        self.assertIn('id="organization-filter-query"', table_panel)
        self.assertIn('id="omcu-filter-toggle"', table_panel)
        self.assertIn('id="omcu-options"', table_panel)
        self.assertIn('aria-label="Выбрать все ОМСУ"', table_panel)
        self.assertIn('aria-label="Очистить выбор ОМСУ"', table_panel)
        self.assertNotIn('id="omcu-select-all">Выбрать все', table_panel)
        self.assertNotIn('id="omcu-clear">Очистить', table_panel)
        self.assertIn('omcu-popover{width:min(340px', dashboard_html)
        self.assertIn(
            '.header-filter-popover label.omcu-option{display:flex;align-items:center;gap:8px;margin:0',
            dashboard_html,
        )
        self.assertIn('font-weight:400;line-height:16px;text-transform:none;letter-spacing:0;white-space:nowrap', dashboard_html)
        self.assertIn(
            '.header-filter-popover.is-portaled{position:fixed;z-index:1000',
            dashboard_html,
        )
        self.assertIn('.header-filter-popover .popover-action{', dashboard_html)
        with open(
            os.path.join(os.path.dirname(__file__), "static", "ministry_dashboard.js"),
            encoding="utf-8",
        ) as script_file:
            dashboard_js = script_file.read()
        self.assertIn(
            'if (popover.parentElement !== document.body) document.body.append(popover);',
            dashboard_js,
        )
        self.assertNotIn("Источник: ", dashboard_js)
        self.assertIn("els.rowsStatus.hidden = hasRows;", dashboard_js)
        self.assertIn('id="period-filter-toggle"', table_panel)
        self.assertIn('id="period-filter"', table_panel)
        self.assertIn('id="debt-filter-toggle"', table_panel)
        self.assertIn('id="debt-min"', table_panel)
        self.assertIn('id="debt-max"', table_panel)
        self.assertNotIn('<th>Сопоставление</th>', table_panel)
        table_head_start = dashboard_html.index('<thead><tr>', table_start)
        table_head_end = dashboard_html.index('</tr></thead>', table_head_start)
        self.assertEqual(len(re.findall(
            r'<th(?:\s|>)',
            dashboard_html[table_head_start:table_head_end],
        )), 5)
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
        multiple_district_rows = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all'
            '&district=Балашиха&district=Домодедово'
        ).get_json()
        self.assertEqual(multiple_district_rows['pagination']['total'], 1)
        self.assertEqual(
            multiple_district_rows['filters']['selected']['districts'],
            ['Балашиха', 'Домодедово'],
        )
        self.assertEqual(len(multiple_district_rows['filters']['districts']), 56)
        debt_range = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all'
            '&debt_min=11&debt_max=13'
        ).get_json()
        self.assertEqual(debt_range['pagination']['total'], 1)
        self.assertEqual(debt_range['rows'][0]['debt'], 12)
        debt_range_empty = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all&debt_min=13'
        ).get_json()
        self.assertEqual(debt_range_empty['pagination']['total'], 0)
        invalid_debt_range = self.client.get(
            '/ministry/api/data?month=2026-03&resource=all'
            '&debt_min=20&debt_max=10'
        )
        self.assertEqual(invalid_debt_range.status_code, 400)
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
