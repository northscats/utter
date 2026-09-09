"""Isolated SQLite regressions; never touches the application's real database."""
import copy
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory(prefix='key-indicators-test-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP.name, 'test.db')

import app as app_module
from database import Company, CompanyMembership, DailyRow, Upload, User, UserModuleAccess, db
import key_indicators_service as service
from key_indicators import build_indicator_source


def report(period, amount, dates):
    return {
        'acc': '51', 'period': period, 'rows': [], 'warnings': [],
        'top': {'snd': 0, 'snk': 0, 'obd': amount, 'obk': 0,
                'skd': amount, 'skk': 0},
        'daily': [{'date': day, 'month': day[:7], 'path1': '', 'path2': '',
                   'path3': '', 'obd': amount, 'obk': 0} for day in dates],
    }


class SourceAssemblyTests(unittest.TestCase):
    def test_annual_workbook_does_not_block_valid_osv(self):
        from openpyxl import Workbook
        annual = os.path.join(_TMP.name, 'annual-analysis.xlsx')
        book = Workbook()
        book.active.title = '1. Активы'
        book.active.append(['Показатель', '2024 г.', '2025 г.'])
        book.active.append(['Баланс', 100, 200])
        liabilities = book.create_sheet('2. Пассивы')
        liabilities.append(['Показатель', '2024 г.', '2025 г.'])
        liabilities.append(['Баланс', 100, 200])
        book.save(annual)
        book.close()
        osv = os.path.join(_TMP.name, 'valid-osv.xls')
        with open(osv, 'wb') as target:
            target.write(b'fixture')
        real_parser = service.parse_osv
        with patch.object(service, 'parse_osv', side_effect=lambda path:
                          report('Июнь 2026', 20, ['2026-06-20'])
                          if path == osv else real_parser(path)):
            source = service.assemble_source([annual, osv], [])
        self.assertEqual(list(source['accounts']), ['51'])
        self.assertEqual(source['accounts']['51']['daily'][0]['obd'], 20)
        self.assertTrue(any('годовую финансовую аналитику' in w for w in source['warnings']))

    def test_unknown_xlsx_is_not_silently_ignored(self):
        from openpyxl import Workbook
        path = os.path.join(_TMP.name, 'incomplete-osv.xlsx')
        book = Workbook()
        book.active.append(['Неполная выгрузка счёта 51'])
        book.save(path)
        book.close()
        with self.assertRaises(service.SourceReadError) as caught:
            service.assemble_source([path], [])
        self.assertIn('incomplete-osv.xlsx', str(caught.exception))

    def test_report_replacement_removes_old_zero_transaction_dates(self):
        first = os.path.join(_TMP.name, 'old.xls')
        latest = os.path.join(_TMP.name, 'new.xls')
        for path, mtime in ((first, 1), (latest, 2)):
            with open(path, 'wb') as target:
                target.write(b'test')
            os.utime(path, (mtime, mtime))
        reports = {
            first: report('Июнь 2026', 10, ['2026-06-10']),
            latest: report('Июнь 2026', 20, ['2026-06-20']),
        }
        daily = {
            'acc': '51', 'date': date(2026, 6, 21), 'snd': 0, 'snk': 0,
            'obd': 100, 'obk': 0, 'skd': 100, 'skk': 0,
        }
        with patch.object(service, 'parse_osv', side_effect=lambda p: copy.deepcopy(reports[p])):
            source = service.assemble_source([first, latest], [daily])
        rows = source['accounts']['51']['daily']
        self.assertEqual(sum(row['obd'] for row in rows), 20)
        self.assertEqual([row['date'] for row in rows], ['2026-06-20'])
        # The monthly turnover wins, but the independently observed daily
        # balance must remain available on June 21.
        balances = source['accounts']['51'].get('balance_observations', [])
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]['date'], '2026-06-21')

    def test_unreadable_source_is_explicit_error(self):
        path = os.path.join(_TMP.name, 'broken.xls')
        with open(path, 'wb') as target:
            target.write(b'invalid')
        with patch.object(service, 'parse_osv', side_effect=ValueError):
            with self.assertRaises(service.SourceReadError):
                service.assemble_source([path], [])

    def test_only_this_company_accepted_monthly_files(self):
        root = tempfile.mkdtemp(dir=_TMP.name)
        for folder in ('3/accepted', '3/historical', '3/zip_x', '3/stage_x',
                       '3/failed_x', '4/accepted'):
            os.makedirs(os.path.join(root, folder))
            with open(os.path.join(root, folder, 'report.xls'), 'wb') as f:
                f.write(b'test')
        paths = service.monthly_files(root, 3)
        self.assertEqual(len(paths), 1)
        self.assertIn('/3/accepted/', paths[0])

    def test_in_progress_batch_newer_than_committed_upload_is_not_read(self):
        from datetime import datetime
        root = tempfile.mkdtemp(dir=_TMP.name)
        for folder in ('20260601_120000', '20260602_120000'):
            target = os.path.join(root, '3', folder)
            os.makedirs(target)
            with open(os.path.join(target, 'report.xls'), 'wb') as f:
                f.write(b'test')
        paths = service.monthly_files(root, 3, datetime(2026, 6, 1, 12, 5))
        self.assertEqual(len(paths), 1)
        self.assertIn('20260601_120000', paths[0])


class KeyIndicatorRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.imap_patch = patch.object(app_module, '_ensure_imap_thread')
        cls.imap_patch.start()
        cls.addClassCleanup(cls.imap_patch.stop)
        with cls.app.app_context():
            db.create_all()
            admin = User(username='kpi_admin', password_hash='not-a-password', role='admin')
            allowed = User(username='kpi_allowed', password_hash='not-a-password', role='user')
            outsider = User(username='kpi_outsider', password_hash='not-a-password', role='user')
            no_module = User(username='kpi_no_module', password_hash='not-a-password', role='user')
            db.session.add_all([admin, allowed, outsider, no_module])
            db.session.flush()
            company = Company(name='Источник <script> не HTML', created_by=admin.id)
            other = Company(name='Недоступное предприятие', created_by=admin.id)
            db.session.add_all([company, other])
            db.session.flush()
            for user in (allowed, outsider):
                db.session.add(UserModuleAccess(user_id=user.id, module_key='analytics'))
            for user in (allowed, no_module):
                db.session.add(CompanyMembership(
                    user_id=user.id, company_id=company.id, participation_type='creator'))
            db.session.commit()
            cls.ids = {u.username: u.id for u in (admin, allowed, outsider, no_module)}
            cls.company = company.id
            cls.other = other.id

    def client(self, username=None):
        client = self.app.test_client()
        if username:
            with client.session_transaction() as session:
                session['_user_id'] = str(self.ids[username])
                session['_fresh'] = True
        return client

    def url(self, company=None, data=False):
        return f'/company/{company or self.company}/key-indicators' + ('/data' if data else '')

    def test_auth_and_company_boundaries_on_html_and_json(self):
        for data in (False, True):
            self.assertEqual(self.client().get(self.url(data=data)).status_code, 302)
            for user in ('kpi_outsider', 'kpi_no_module'):
                self.assertEqual(self.client(user).get(self.url(data=data)).status_code, 403)
            self.assertEqual(
                self.client('kpi_allowed').get(self.url(self.other, data)).status_code, 403)

    def test_page_and_safe_company_name(self):
        response = self.client('kpi_allowed').get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertIn('&lt;script&gt;', response.get_data(as_text=True))
        self.assertIn('key_indicators.js', response.get_data(as_text=True))
        self.assertIn('window.KI_CONFIG =', response.get_data(as_text=True))

    def test_coverage_days_include_loaded_account_indexes(self):
        with self.app.app_context():
            db.session.add_all([
                DailyRow(company_id=self.company, date=date(2026, 9, 1), acc='51'),
                DailyRow(company_id=self.company, date=date(2026, 9, 1), acc='62'),
                DailyRow(company_id=self.company, date=date(2026, 9, 2), acc='62'),
                Upload(
                    company_id=self.company,
                    uploaded_by=self.ids['kpi_admin'],
                    period_label='Август 2026',
                    accounts_loaded='["20"]',
                ),
            ])
            db.session.commit()
            coverage = app_module._annual_coverage(self.company, 2026)
            DailyRow.query.filter_by(company_id=self.company).delete()
            Upload.query.filter_by(company_id=self.company).delete()
            db.session.commit()

        self.assertEqual(coverage['accs'], ['20', '51', '62'])
        august = coverage['months'][7]
        august_days = {cell['day']: cell for cell in august['days'] if cell}
        self.assertEqual(august_days[1]['account_indexes'], [0])
        september = coverage['months'][8]
        days = {cell['day']: cell for cell in september['days'] if cell}
        self.assertEqual(days[1]['account_indexes'], [1, 2])
        self.assertEqual(days[2]['account_indexes'], [2])

    def test_coverage_lists_all_accounts_and_marks_missing(self):
        with app_module.app.app_context():
            coverage = app_module._annual_coverage(self.company, 2025)
        buttons = {item['number']: item for item in coverage['account_buttons']}

        self.assertEqual(list(buttons)[:3], ['01', '02', '03'])
        self.assertEqual(list(buttons)[-1], '99')
        self.assertEqual(len(buttons), 99)
        self.assertFalse(buttons['01']['available'])

    def test_coverage_recovers_accounts_from_accepted_osv_source(self):
        source = {
            'accounts': {
                '51': {
                    'report_start': '2026-07-01',
                    'report_end': '2026-08-17',
                    'daily': [{'date': '2026-07-02'}],
                },
                '62': {
                    'report_start': '2026-07-01',
                    'report_end': '2026-08-17',
                    'daily': [{'date': '2026-07-03'}],
                },
            },
        }
        with self.app.app_context(), patch.object(
                service, 'load_company_source', return_value=(source, None)):
            coverage = app_module._annual_coverage(self.company, 2026)

        self.assertEqual(coverage['accs'], ['51', '62'])
        july = coverage['months'][6]
        days = {cell['day']: cell for cell in july['days'] if cell}
        self.assertEqual(days[2]['account_indexes'], [0])
        self.assertEqual(days[3]['account_indexes'], [1])

    def test_empty_company_returns_real_unavailable_cards(self):
        with patch.object(service, 'load_company_source',
                          return_value=(build_indicator_source({}), None)):
            response = self.client('kpi_admin').get(self.url(data=True))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload['cards']), 10)
        self.assertTrue(all(card['value'] is None for card in payload['cards']))
        self.assertIsNone(payload['updated_at'])

    def test_invalid_period_rejected_before_source_load(self):
        with patch.object(service, 'load_company_source') as loader:
            for query in ('?period=nope', '?date=garbage'):
                response = self.client('kpi_allowed').get(self.url(data=True) + query)
                self.assertEqual(response.status_code, 400)
            loader.assert_not_called()

    def test_busy_and_read_errors_visible(self):
        for error, expected in (
            (service.SourceBusyError('Занято'), 503),
            (service.SourceReadError('Ошибка источника'), 422),
        ):
            with patch.object(service, 'load_company_source', side_effect=error):
                response = self.client('kpi_admin').get(self.url(data=True))
            self.assertEqual(response.status_code, expected)
            self.assertIn('error', response.get_json())


if __name__ == '__main__':
    unittest.main()