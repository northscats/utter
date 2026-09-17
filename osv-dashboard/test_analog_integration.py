# -*- coding: utf-8 -*-
"""Route-level regressions for the analogue module."""
import os
import pathlib
import tempfile
import unittest
import json
import zipfile
from io import BytesIO
from datetime import date
from decimal import Decimal

_tmp = tempfile.mkdtemp(prefix='analog_route_test_')
os.environ['DATABASE_URL'] = f'sqlite:///{_tmp}/analog.db'
os.environ['INITIAL_ADMIN_PASSWORD'] = 'analog-admin-passphrase-2026'
os.environ.setdefault('ENCRYPTION_KEY',
                      'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.setdefault('REPLIT_DOMAINS', 'preview.example')

from app import app, init_db  # noqa: E402
from database import (AnalogClimateReference, AnalogCostIndexReference,
                      AnalogDocument, AnalogImport, AnalogNetworkAttribute,
                      AnalogPassport, AnalogPassportVersion, User, db)  # noqa: E402
from analog_migration import import_json_v4  # noqa: E402


ROOT = pathlib.Path(__file__).parent.parent / 'attached_assets'


class AnalogRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['INITIAL_ADMIN_PASSWORD'] = 'analog-admin-passphrase-2026'
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        init_db()
        cls.conclusion = (ROOT / '50-1-1-2-018685-2026_26-04-29_11-32-17_signature_1789461292785.xml').read_bytes()
        cls.ssr = (ROOT / 'ССР_Кривцово_1789461305316.gge').read_bytes()
        cls.conclusion_101 = (ROOT / '56-1-1-2-044346-2024_24-08-06_09-12-42_signature_1789537746147.xml').read_bytes()
        cls.ssr_101 = (ROOT / 'Сводный_сметный_расчет_1789537733540.gge').read_bytes()

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

    def files(self, conclusion=None, ssr=None):
        return {'conclusion': (BytesIO(conclusion or self.conclusion), 'conclusion.xml'),
                'ssr': (BytesIO(ssr or self.ssr), 'summary.gge')}

    def test_summary_estimate_101_preview_and_confirm(self):
        response = self.client.post(
            '/analog-search/import/preview',
            data=self.files(self.conclusion_101, self.ssr_101),
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200)
        preview = response.get_json()
        self.assertTrue(preview['can_confirm'], preview['diagnostics'])
        pair = preview['preview']
        self.assertEqual(pair['ssr']['schema_version'], '1.01')
        self.assertEqual(pair['ssr']['price_quarter'], 4)
        self.assertEqual(pair['ssr']['summary']['total'], '31372.62')
        self.assertEqual(len(pair['ssr']['chapters']), 14)
        self.assertEqual(len(pair['ssr']['items']), 17)
        html = self.client.get(
            f"/analog-search/imports/{preview['import_id']}/preview")
        self.assertEqual(html.status_code, 200)
        page = html.get_data(as_text=True)
        self.assertIn('31372.62', page)
        self.assertIn('2023 / квартал 4', page)

        confirmed = self.client.post(
            f"/analog-search/import/{preview['import_id']}/confirm", json={})
        self.assertEqual(confirmed.status_code, 200)
        passport_id = confirmed.get_json()['passport_id']
        with app.app_context():
            version = AnalogPassportVersion.query.filter_by(
                passport_id=passport_id).order_by(
                    AnalogPassportVersion.version_no.desc()).first()
            passport = version.passport
            self.assertEqual(version.schema_version, '1.01')
            self.assertEqual((version.price_year, version.price_quarter), (2023, 4))
            self.assertEqual(version.total_cost, Decimal('31372.62'))
            self.assertEqual(passport.capacity_kw, Decimal('1800.000000'))
            payload = json.loads(version.payload_json)
            self.assertEqual(len(payload['ssr']['chapters']), 14)
            self.assertEqual(len(payload['ssr']['items']), 17)
        passport_page = self.client.get(
            f'/analog-search/passports/{passport_id}').get_data(as_text=True)
        self.assertIn('1.8 мВт', passport_page)
        self.assertIn('Сводный сметный расчёт (GGE)', passport_page)
        self.assertIn('Конструктивные решения.gsfx', passport_page)
        self.assertIn('WorkersSalary', passport_page)
        self.assertIn('111.13', passport_page)
        self.assertIn('ГРАНД-Смета', passport_page)

    def test_html_confirm_redirects_to_analog_search(self):
        response = self.client.post(
            '/analog-search/import/preview',
            data=self.files(self.conclusion_101, self.ssr_101 + b'\n '),
            content_type='multipart/form-data')
        preview = response.get_json()
        confirmed = self.client.post(
            f"/analog-search/import/{preview['import_id']}/confirm",
            data={}, follow_redirects=False)
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(confirmed.headers['Location'], '/analog-search/simple')

        duplicate = self.client.post(
            '/analog-search/import/preview',
            data=self.files(self.conclusion_101, self.ssr_101 + b'\n '),
            content_type='multipart/form-data',
            headers={'Accept': 'text/html'},
            follow_redirects=False)
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(duplicate.headers['Location'], '/analog-search/simple')
        page = self.client.get('/analog-search/simple').get_data(as_text=True)
        self.assertIn('Объект уже находится в общей таблице аналогов', page)

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
            passport = db.session.get(AnalogPassport, passport_id)
            self.assertEqual(
                passport.address,
                'Солнечногорский район, с.п. Кривцовское, д. Кривцово')
            self.assertIsNone(passport.capacity_kw)
            self.assertEqual(passport.teps[-1].value_text, '6,53')
            self.assertEqual(passport.teps[-1].unit, 'Гкал')
        duplicate = self.client.post('/analog-search/import/preview',
                                     data=self.files(conclusion),
                                     content_type='multipart/form-data')
        self.assertEqual(duplicate.get_json()['status'], 'duplicate')

    def test_global_abs_boundary_and_pagination(self):
        with app.app_context():
            before = AnalogPassport.query.filter_by(archived=False).count()
            for number, capacity in enumerate((90, 110, 89, 111), 1):
                db.session.add(AnalogPassport(
                    title=f'boundary-{number}', capacity_kw=capacity,
                    object_type='котельная', created_by=self.admin.id))
            db.session.commit()
        response = self.client.get('/analog-search/api/search?capacity_kw=100&per_page=2')
        payload = response.get_json()
        self.assertEqual(payload['good'], 2)
        self.assertEqual(payload['total'], before + 4)
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
                                    data=self.files(self.conclusion + b'\n \n'),
                                    content_type='multipart/form-data',
                                    headers={'Accept': 'text/html'},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        preview_url = response.headers['Location']
        self.assertRegex(preview_url,
                         r'/analog-search/imports/\d+/preview$')
        response = self.client.get(preview_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Загрузить данные в общую таблицу аналогов',
                      response.get_data(as_text=True))
        html = response.get_data(as_text=True)
        self.assertIn('analog-preview-page', html)
        self.assertIn('Солнечногорский район, с.п. Кривцовское, д. Кривцово', html)
        self.assertIn('Технико-экономические показатели', html)
        self.assertIn('Мощность котельной', html)
        self.assertIn('6,53 Гкал', html)
        self.assertIn('Климатический район, подрайон', html)
        self.assertIn('Геологические условия', html)
        self.assertIn('Сейсмическая активность (баллов)', html)

    def test_module_entry_offers_both_search_modes(self):
        page = self.client.get('/analog-search')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Упрощённый поиск', html)
        self.assertIn('Поиск по нормативно-правовому акту', html)
        self.assertIn('/analog-search/simple', html)
        self.assertIn('/analog-search/regulatory', html)

    def test_regulatory_search_applies_135pr_mandatory_criteria(self):
        with app.app_context():
            passport = AnalogPassport(
                title='Нормативный аналог', object_type='тепловая сеть',
                climate_zone='IIВ', work_type='Строительство',
                created_by=self.admin_id)
            db.session.add(passport)
            db.session.flush()
            version = AnalogPassportVersion(
                passport_id=passport.id, version_no=1,
                conclusion_number=f'normative-{passport.id}',
                examination_result='positive',
                source_hash=f'normative:{passport.id}',
                ssr_hash=f'normative:{passport.id}',
                imported_by=self.admin_id)
            db.session.add(version)
            db.session.flush()
            passport.current_version_id = version.id
            db.session.add_all([
                AnalogNetworkAttribute(
                    passport_id=passport.id, version_id=version.id,
                    key='length_m', value='100', value_number=Decimal('100'),
                    unit='м'),
                AnalogNetworkAttribute(
                    passport_id=passport.id, version_id=version.id,
                    key='diameter_mm', value='200',
                    value_number=Decimal('200'), unit='мм'),
            ])
            db.session.commit()
            passport_id = passport.id

        response = self.client.post(
            '/analog-search/regulatory/search',
            json={'work_type': 'Строительство', 'length_m': '105',
                  'diameter_mm': '210', 'climate_region': 'II',
                  'climate_subzone': 'В'})
        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.get_json()['items']
                    if row['id'] == passport_id)
        self.assertTrue(item['suitable'])
        self.assertEqual(item['algorithm_version'], '135pr-604pr-v1')

    def test_regulatory_page_contains_all_filters_from_reference_html(self):
        page = self.client.get('/analog-search/regulatory')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        for label in (
                'Год сметного уровня цен', 'Пропускная способность, Гкал/ч',
                'Инженерно-геологические условия', 'Материал трубопроводов',
                'Температура подачи', 'Байпас требуется',
                'Прокладка в сборных каналах', 'Замена запорной арматуры',
                'Наличие водяного перехода', 'Типы ЗОУИТ',
                'Ориентировочная стоимость на 1 м'):
            self.assertIn(label, html)

    def test_climate_filter_supports_region_and_exact_subregion(self):
        with app.app_context():
            for zone in ('IIА', 'IIВ', 'IIIА'):
                db.session.add(AnalogPassport(
                    title=f'climate-filter-fixture-{zone}',
                    object_type='котельная', climate_zone=zone,
                    created_by=self.admin_id))
            db.session.commit()

        region = self.client.get(
            '/analog-search/api/search',
            query_string={'q': 'climate-filter-fixture',
                          'climate_region': 'II'}).get_json()
        self.assertEqual(region['total'], 2)
        self.assertEqual(
            {item['climate_zone'] for item in region['items']},
            {'IIА', 'IIВ'})

        exact = self.client.get(
            '/analog-search/api/search',
            query_string={'q': 'climate-filter-fixture',
                          'climate_region': 'III',
                          'climate_zone': 'IIВ'}).get_json()
        self.assertEqual(exact['total'], 1)
        self.assertEqual(exact['items'][0]['climate_zone'], 'IIВ')

    def test_admin_can_remove_passport_from_analogs_table(self):
        with app.app_context():
            passport = AnalogPassport(
                title='Аналог для удаления', object_type='котельная',
                work_type='строительство', capacity_kw=100,
                created_by=self.admin_id)
            db.session.add(passport)
            db.session.commit()
            passport_id = passport.id

        page = self.client.get('/analog-search/simple')
        html = page.get_data(as_text=True)
        self.assertIn('Аналог для удаления', html)
        self.assertIn(
            f'/analog-search/passports/{passport_id}/archive', html)
        self.assertIn('Удалить', html)

        response = self.client.post(
            f'/analog-search/passports/{passport_id}/archive',
            headers={'Accept': 'text/html'}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/analog-search/simple')
        self.assertNotIn(
            'Аналог для удаления',
            self.client.get('/analog-search/simple').get_data(as_text=True))
        with app.app_context():
            self.assertTrue(db.session.get(AnalogPassport, passport_id).archived)

    def test_regular_user_cannot_remove_passport(self):
        with app.app_context():
            passport = AnalogPassport(
                title='Защищённый аналог', object_type='котельная',
                work_type='строительство', capacity_kw=100,
                created_by=self.admin_id)
            user = User(username='analog-delete-user', password_hash='unused',
                        role='user')
            user.password_change_required = False
            db.session.add_all([passport, user])
            db.session.commit()
            passport_id = passport.id
            user_id, user_version = user.id, int(user.password_version or 1)
        self.login_values(user_id, user_version)
        self.assertEqual(
            self.client.post(
                f'/analog-search/passports/{passport_id}/archive').status_code,
            403)

    def test_preview_get_is_owned_and_confirmed_preview_redirects(self):
        response = self.client.post('/analog-search/import/preview',
                                    data=self.files(self.conclusion + b'\n \n \n'),
                                    content_type='multipart/form-data')
        import_id = response.get_json()['import_id']
        preview_url = f'/analog-search/imports/{import_id}/preview'
        self.assertEqual(self.client.get(preview_url).status_code, 200)

        with app.app_context():
            user = User(username='analog-preview-other', password_hash='unused',
                        role='user')
            user.password_change_required = False
            db.session.add(user)
            db.session.commit()
            user_id, user_version = user.id, int(user.password_version or 1)
        self.login_values(user_id, user_version)
        self.assertEqual(self.client.get(preview_url).status_code, 403)

        self.login_values(self.admin_id, self.admin_version)
        confirmed = self.client.post(
            f'/analog-search/import/{import_id}/confirm', json={})
        passport_id = confirmed.get_json()['passport_id']
        reopened = self.client.get(preview_url, follow_redirects=False)
        self.assertEqual(reopened.status_code, 302)
        self.assertEqual(reopened.headers['Location'], '/analog-search/simple')

    def test_reupload_restores_archived_analog_to_table(self):
        conclusion = self.conclusion + b'\n  \n'
        preview = self.client.post('/analog-search/import/preview',
                                   data=self.files(conclusion),
                                   content_type='multipart/form-data').get_json()
        confirmed = self.client.post(
            f"/analog-search/import/{preview['import_id']}/confirm", json={})
        passport_id = confirmed.get_json()['passport_id']
        with app.app_context():
            version_count = len(
                db.session.get(AnalogPassport, passport_id).versions)
        archived = self.client.post(
            f'/analog-search/passports/{passport_id}/archive',
            headers={'Accept': 'text/html'})
        self.assertEqual(archived.status_code, 302)
        with app.app_context():
            self.assertTrue(db.session.get(AnalogPassport, passport_id).archived)

        duplicate = self.client.post(
            '/analog-search/import/preview', data=self.files(conclusion),
            content_type='multipart/form-data',
            headers={'Accept': 'text/html'})
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(duplicate.headers['Location'], '/analog-search/simple')
        page = self.client.get('/analog-search/simple').get_data(as_text=True)
        self.assertIn('снова добавлен в общую таблицу аналогов', page)
        with app.app_context():
            passport = db.session.get(AnalogPassport, passport_id)
            self.assertFalse(passport.archived)
            self.assertEqual(len(passport.versions), version_count)

    def test_html_xhr_parse_error_is_actionable_json(self):
        response = self.client.post(
            '/analog-search/import/preview',
            data=self.files(b'<broken'),
            content_type='multipart/form-data',
            headers={'Accept': 'text/html',
                     'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['diagnostics'][0]['code'], 'malformed_xml')

    def test_import_form_has_upload_and_processing_states(self):
        page = self.client.get('/analog-search/simple')
        html = page.get_data(as_text=True)
        self.assertIn('analog-import-progress', html)
        self.assertIn('xhr.upload.onprogress', html)
        self.assertIn('Сервер проверяет и разбирает данные', html)
        self.assertIn('button.disabled = true', html)
        self.assertIn('window.location.assign(finalUrl)', html)
        self.assertNotIn('document.write(xhr.responseText)', html)

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

    def test_climate_suggestion_requires_explicit_confirmation(self):
        with app.app_context():
            db.session.add_all([
                AnalogClimateReference(
                    address_pattern='Омская область', climate_zone='IВ',
                    climate_subzone='', source='СП 131.13330',
                    reference_version='2025-general', reference_date=date(2025, 1, 1)),
                AnalogClimateReference(
                    address_pattern='город Омск', climate_zone='IВ',
                    climate_subzone='IВ-2', source='СП 131.13330, приложение А',
                    reference_version='2025-omsk', reference_date=date(2025, 2, 1)),
            ])
            passport = AnalogPassport(
                title='Климатический тест', address='Омская область, город Омск',
                object_type='котельная', work_type='строительство', capacity_kw=10,
                created_by=self.admin_id)
            db.session.add(passport)
            db.session.commit()
            passport_id = passport.id
        proposal = self.client.get(
            '/analog-search/api/climate-suggestion',
            query_string={'address': 'Омская область, город Омск'}).get_json()
        self.assertEqual(proposal['reference_version'], '2025-omsk')
        rejected = self.client.post(
            f'/analog-search/passports/{passport_id}/climate/confirm',
            json={'reference_version': '2025-omsk'})
        self.assertEqual(rejected.status_code, 400)
        confirmed = self.client.post(
            f'/analog-search/passports/{passport_id}/climate/confirm',
            json={'confirmed': True, 'reference_version': '2025-omsk'})
        self.assertEqual(confirmed.status_code, 200)
        with app.app_context():
            saved = db.session.get(AnalogPassport, passport_id)
            self.assertEqual(saved.climate_subzone, 'IВ-2')
            self.assertEqual(saved.climate_reference_version, '2025-omsk')
            self.assertEqual(saved.climate_reference_date, date(2025, 2, 1))

    def test_versioned_cost_index_is_selected_and_saved_with_passport_version(self):
        with app.app_context():
            db.session.add_all([
                AnalogCostIndexReference(
                    effective_date=date(2025, 1, 1), price_level='2020/Q1',
                    factor='1.25', source='Согласованный индекс Минстроя',
                    reference_version='indices-2025-q1', reference_date=date(2025, 1, 10)),
                AnalogCostIndexReference(
                    effective_date=date(2025, 7, 1), price_level='2020/Q1',
                    factor='1.40', source='Согласованный индекс Минстроя',
                    reference_version='indices-2025-q3', reference_date=date(2025, 7, 10)),
            ])
            passport = AnalogPassport(
                title='Индексный тест', object_type='котельная',
                work_type='строительство', capacity_kw=10, created_by=self.admin_id)
            db.session.add(passport)
            db.session.flush()
            version = AnalogPassportVersion(
                passport_id=passport.id, version_no=1, conclusion_number='cost-test',
                examination_result='positive', source_hash='cost-source',
                ssr_hash='cost-ssr', total_cost=100, imported_by=self.admin_id)
            db.session.add(version)
            db.session.flush()
            passport.current_version_id = version.id
            db.session.commit()
            passport_id, version_id = passport.id, version.id
        missing = self.client.get('/analog-search/api/cost-index-suggestion',
                                  query_string={'price_level': 'unknown',
                                                'date': '2025-08-01'}).get_json()
        self.assertEqual(missing['status'], 'missing')
        rejected = self.client.post(
            f'/analog-search/passports/{passport_id}/versions/{version_id}/adjust',
            json={'price_level': '2020/Q1', 'calculation_date': '2025-08-01',
                  'reference_version': 'indices-2025-q3'})
        self.assertEqual(rejected.status_code, 400)
        saved = self.client.post(
            f'/analog-search/passports/{passport_id}/versions/{version_id}/adjust',
            json={'price_level': '2020/Q1', 'calculation_date': '2025-08-01',
                  'reference_version': 'indices-2025-q3', 'confirmed': True})
        self.assertEqual(saved.status_code, 200)
        with app.app_context():
            version = db.session.get(AnalogPassportVersion, version_id)
            self.assertEqual(str(version.adjusted_cost), '140.000000')
            self.assertEqual(version.adjustment_reference_version, 'indices-2025-q3')
            self.assertEqual(version.adjustment_reference_date, date(2025, 7, 10))


if __name__ == '__main__':
    unittest.main()
