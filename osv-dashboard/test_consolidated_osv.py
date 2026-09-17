# -*- coding: utf-8 -*-
import os
import json
import hashlib
import unittest
import tempfile
from unittest.mock import patch

from consolidated_osv import parse_consolidated_osv
from key_indicators import (
    _top_balance,
    _top_opening_balance,
    build_key_indicators,
)
from key_indicators_service import assemble_source
import key_indicators_service
import app as app_module
from database import Company, Upload, User, db


CONTROL = os.path.join(
    os.path.dirname(__file__),
    'control_snapshots', '3', 'osv_all_2026_h1_control.xls')


class ConsolidatedOsvTests(unittest.TestCase):
    def test_balance_comparison_reports_each_control_difference(self):
        current = {
            'opening_debit': 100, 'opening_credit': 50,
            'turnover_debit': 300, 'turnover_credit': 250,
            'closing_debit': 150, 'closing_credit': 75,
        }
        previous = {
            'balance': {
                'opening_debit': 90, 'opening_credit': 50,
                'turnover_debit': 310, 'turnover_credit': 230,
                'closing_debit': 150, 'closing_credit': 80,
            },
        }
        comparison = app_module._consolidated_balance_comparison(
            current, previous)
        self.assertTrue(comparison['has_differences'])
        self.assertEqual(
            [(row['delta_debit'], row['delta_credit'])
             for row in comparison['rows']],
            [(10.0, 0.0), (-10.0, 20.0), (0.0, -5.0)])
        self.assertIsNone(
            app_module._consolidated_balance_comparison(current, None))

    def test_parser_splits_complete_snapshot_by_root_account(self):
        parsed = parse_consolidated_osv(CONTROL)
        self.assertIn('60', parsed)
        self.assertIn('90', parsed)
        row = next(item for item in parsed['60']['rows']
                   if item['label'] == '60.01')
        self.assertEqual(row['p1'], '60.01')
        self.assertEqual(row['skk'], 392339162.24)

    def test_control_snapshot_reconciles_all_key_indicators(self):
        source = assemble_source([CONTROL], [])
        source['dates'] = ['2026-01-01', '2026-06-30']
        cards = {item['id']: item for item in build_key_indicators(
            source, period='day',
            date_from='2026-01-01', date='2026-06-30')['cards']}
        expected = {
            'cash': 1954600.71,
            'revenue': 418611756.49,
            'collection': 71.82,
            'ar': 437405275.57,
            'ap': 392339162.24,
            'cogs': 494453141.09,
            'profit': -139649730.99,
            'credits': 0.0,
            'salary': 21021429.72,
            'capex': 122644969.44,
        }
        self.assertEqual(
            {key: cards[key]['value'] for key in expected}, expected)
        self.assertEqual(cards['ar']['consumer_advances'], 53124872.0)
        self.assertEqual(cards['ap']['credit_turnover'], 611964238.16)
        self.assertEqual(
            [(item['account'], item['value'])
             for item in cards['cogs']['recognized_costs']],
            [('90.02', 494453141.09),
             ('90.07', 0.0),
             ('90.08', 63808346.39)])
        self.assertEqual(
            [(item['account'], item['value'])
             for item in cards['cogs']['cost_movements']],
            [('20', 505774189.71),
             ('25', 67871065.11),
             ('26', 63808346.39),
             ('44', 0.0)])
        self.assertEqual(cards['salary']['credit_turnover'], 184556144.31)
        self.assertEqual(
            cards['salary']['insurance']['credit_balance'], 11176101.24)
        self.assertEqual(cards['capex']['credit_turnover'], 122644969.44)

    def test_complete_snapshot_wins_balance_and_opening_over_partial_revision(self):
        complete = {
            'report_start': '2026-01-01',
            'report_end': '2026-06-30',
            'source_updated_at': '2026-07-01T00:00:00+00:00',
            'complete_snapshot': True,
            'top': {
                'snd': 10.0, 'snk': 0.0, 'obd': 90.0, 'obk': 80.0,
                'skd': 20.0, 'skk': 0.0,
            },
            'rows': [],
        }
        later_partial = {
            'report_start': '2026-01-01',
            'report_end': '2026-06-30',
            'source_updated_at': '2026-09-01T00:00:00+00:00',
            'complete_snapshot': False,
            'top': {
                'snd': 999.0, 'snk': 0.0, 'obd': 0.0, 'obk': 0.0,
                'skd': 999.0, 'skk': 0.0,
            },
            'rows': [],
        }
        source = {
            'accounts': {
                '08': {
                    'balance_reports': [complete, later_partial],
                    'balance_observations': [],
                    'daily': [],
                },
            },
            '_complete_snapshots': [{
                'start': '2026-01-01',
                'end': '2026-06-30',
            }],
        }
        from datetime import date
        self.assertEqual(_top_opening_balance(
            source, '08', date(2026, 1, 1)), 10.0)
        self.assertEqual(_top_balance(
            source, '08', date(2026, 6, 30))[0], 20.0)

    def test_complete_snapshot_wins_over_salary_interim_rollforward(self):
        complete = {
            'report_start': '2026-01-01',
            'report_end': '2026-06-30',
            'source_updated_at': '2026-07-01T00:00:00+00:00',
            'complete_snapshot': True,
            'top': {
                'snd': 0.0, 'snk': 10.0, 'obd': 90.0, 'obk': 100.0,
                'skd': 0.0, 'skk': 20.0,
            },
            'rows': [],
        }
        source = {
            'accounts': {
                '70': {
                    'report_start': '2025-01-01',
                    'report_end': '2026-09-15',
                    'top': {
                        'snd': 0.0, 'snk': 999.0,
                        'obd': 0.0, 'obk': 0.0,
                        'skd': 0.0, 'skk': 999.0,
                    },
                    'daily': [{
                        'date': '2026-06-30', 'month': '2026-06',
                        'account': '70', 'path1': '', 'path2': '', 'path3': '',
                        'obd': 0.0, 'obk': 500.0,
                    }],
                    'balance_reports': [complete],
                    'balance_observations': [],
                },
            },
            '_complete_snapshots': [{
                'start': '2026-01-01',
                'end': '2026-06-30',
            }],
        }
        from datetime import date
        self.assertEqual(_top_balance(
            source, '70', date(2026, 6, 30), credit=True)[0], 20.0)

    def test_missing_account_is_confirmed_zero_inside_complete_snapshot(self):
        source = {
            'accounts': {
                '66': {
                    'report_start': '2025-01-01',
                    'report_end': '2025-12-31',
                    'top': {
                        'snd': 0.0, 'snk': 99.0,
                        'obd': 0.0, 'obk': 0.0,
                        'skd': 0.0, 'skk': 99.0,
                    },
                    'daily': [],
                    'balance_reports': [],
                    'balance_observations': [],
                },
            },
            '_complete_snapshots': [{
                'start': '2026-01-01',
                'end': '2026-06-30',
                'accounts': ['50', '51', '60', '62', '70', '90'],
            }],
        }
        from datetime import date
        self.assertEqual(
            _top_balance(
                source, '66', date(2026, 3, 31), credit=True),
            (0.0, None))


class ConsolidatedOsvConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app_module.app.config.update(
            TESTING=True, WTF_CSRF_ENABLED=False)
        self.uploads = os.path.join(self.temp.name, 'uploads')
        self.controls = os.path.join(self.temp.name, 'control_snapshots')
        self.legacy_controls = os.path.join(
            self.temp.name, 'legacy_control_snapshots')
        os.makedirs(self.uploads)
        os.makedirs(self.controls)
        os.makedirs(self.legacy_controls)
        self.patches = [
            patch.object(app_module, 'UPLOADS', self.uploads),
            patch.object(app_module, 'CONTROL_SNAPSHOT_UPLOADS', self.controls),
            patch.object(
                app_module, 'LEGACY_CONTROL_SNAPSHOTS', self.legacy_controls),
            patch(
                'key_indicators_service._CONTROL_SNAPSHOT_ROOT',
                self.legacy_controls),
            patch('key_indicators_service._UPLOADED_CONTROL_ROOT', self.controls),
            patch.object(app_module, '_durable_backup'),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temp.cleanup)
        with app_module.app.app_context():
            db.drop_all()
            db.create_all()
            user = User(
                username='operator', password_hash='unused', role='admin')
            db.session.add(user)
            db.session.flush()
            company = Company(name='Test company', created_by=user.id)
            db.session.add(company)
            db.session.commit()
            self.user_id = user.id
            self.company_id = company.id
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
            session['password_version'] = 1

    def tearDown(self):
        with app_module.app.app_context():
            db.session.remove()

    def _preview(self):
        with open(CONTROL, 'rb') as stream:
            response = self.client.post(
                f'/company/{self.company_id}/consolidated-osv/preview',
                data={'consolidated_file': (stream, 'Сводная ОСВ.xls')})
        self.assertEqual(response.status_code, 302)
        return response.headers['Location'].split('consolidated_preview=')[1]

    def test_preview_does_not_trust_file_until_explicit_confirmation(self):
        preview_id = self._preview()
        self.assertFalse(os.path.isdir(
            os.path.join(
                self.controls, str(self.company_id), 'control_snapshots')))
        page = self.client.get(
            f'/company/{self.company_id}?consolidated_preview={preview_id}')
        self.assertIn('2026-01-01', page.get_data(as_text=True))
        self.assertIn('Подтвердить контрольный снимок', page.get_data(as_text=True))

        confirmed = self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{preview_id}/confirm',
            data={'confirm_complete_scope': '1'})
        self.assertEqual(confirmed.status_code, 302)
        files = os.listdir(os.path.join(
            self.controls, str(self.company_id), 'control_snapshots'))
        self.assertEqual(files, ['consolidated_2026-01-01_2026-06-30.xls'])
        with app_module.app.app_context():
            self.assertEqual(Upload.query.count(), 0)

    def test_second_preview_names_revision_that_will_be_replaced(self):
        first = self._preview()
        self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{first}/confirm',
            data={'confirm_complete_scope': '1'})
        second = self._preview()
        page = self.client.get(
            f'/company/{self.company_id}?consolidated_preview={second}')
        body = page.get_data(as_text=True)
        self.assertIn('Будет заменена ревизия', body)
        self.assertIn('consolidated_2026-01-01_2026-06-30.xls', body)
        self.assertIn('Расхождение Дт / Кт', body)
        self.assertIn('Расхождений по контрольным суммам нет', body)

    def test_changed_staged_file_cannot_be_confirmed(self):
        preview_id = self._preview()
        stage_dir = os.path.join(
            self.uploads, str(self.company_id), 'control_stage')
        with open(os.path.join(stage_dir, f'{preview_id}.xls'), 'ab') as stream:
            stream.write(b'changed')
        response = self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{preview_id}/confirm',
            data={'confirm_complete_scope': '1'},
            follow_redirects=True)
        self.assertIn(
            'Файл изменился после предпросмотра',
            response.get_data(as_text=True))
        self.assertFalse(os.path.isdir(
            os.path.join(
                self.controls, str(self.company_id), 'control_snapshots')))

    def test_stale_preview_cannot_replace_newer_confirmation(self):
        first = self._preview()
        stale = self._preview()
        accepted = self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{first}/confirm',
            data={'confirm_complete_scope': '1'})
        self.assertEqual(accepted.status_code, 302)
        rejected = self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{stale}/confirm',
            data={'confirm_complete_scope': '1'},
            follow_redirects=True)
        self.assertIn(
            'Доверенная ревизия изменилась после предпросмотра',
            rejected.get_data(as_text=True))
        files = os.listdir(os.path.join(
            self.controls, str(self.company_id), 'control_snapshots'))
        self.assertEqual(files, ['consolidated_2026-01-01_2026-06-30.xls'])

    def test_complete_scope_acknowledgement_is_required(self):
        preview_id = self._preview()
        response = self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{preview_id}/confirm',
            follow_redirects=True)
        self.assertIn(
            'Подтвердите, что файл сформирован без отбора счетов',
            response.get_data(as_text=True))

    def test_uploaded_revision_supersedes_legacy_snapshot_for_same_period(self):
        legacy_dir = os.path.join(
            self.legacy_controls, str(self.company_id))
        os.makedirs(legacy_dir)
        legacy_path = os.path.join(legacy_dir, 'legacy.xls')
        import shutil
        shutil.copy2(CONTROL, legacy_path)

        preview_id = self._preview()
        page = self.client.get(
            f'/company/{self.company_id}?consolidated_preview={preview_id}')
        self.assertIn('legacy.xls', page.get_data(as_text=True))
        self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{preview_id}/confirm',
            data={'confirm_complete_scope': '1'})

        def parsed_for(path):
            accounts = ('60',) if path != legacy_path else ('60', '90')
            return {
                account: {
                    'period': (
                        'Оборотно-сальдовая ведомость за '
                        '1 полугодие 2026 г.'),
                }
                for account in accounts
            }

        with patch.object(
                key_indicators_service, 'parse_consolidated_osv',
                side_effect=parsed_for):
            paths = key_indicators_service._control_source_paths(
                self.company_id)
        self.assertEqual(len(paths), 1)
        self.assertIn('control_snapshots', paths[0])
        self.assertNotEqual(paths[0], legacy_path)

    def test_install_failure_keeps_previous_revision_when_extension_changes(self):
        first = self._preview()
        self.client.post(
            f'/company/{self.company_id}/consolidated-osv/{first}/confirm',
            data={'confirm_complete_scope': '1'})
        control_dir = os.path.join(
            self.controls, str(self.company_id), 'control_snapshots')
        old_path = os.path.join(
            control_dir, 'consolidated_2026-01-01_2026-06-30.xls')
        with open(old_path, 'rb') as stream:
            old_digest = hashlib.sha256(stream.read()).hexdigest()

        second = self._preview()
        stage_dir = os.path.join(
            self.uploads, str(self.company_id), 'control_stage')
        metadata_path = os.path.join(stage_dir, f'{second}.json')
        with open(metadata_path, encoding='utf-8') as stream:
            metadata = json.load(stream)
        old_stage = os.path.join(stage_dir, metadata['stored_name'])
        metadata['stored_name'] = f'{second}.xlsx'
        new_stage = os.path.join(stage_dir, metadata['stored_name'])
        parsed_preview = parse_consolidated_osv(old_stage)
        os.rename(old_stage, new_stage)
        with open(metadata_path, 'w', encoding='utf-8') as stream:
            json.dump(metadata, stream)

        with patch.object(
                app_module, 'parse_consolidated_osv',
                return_value=parsed_preview), patch.object(
                    app_module.os, 'replace',
                    side_effect=OSError('injected install failure')):
            response = self.client.post(
                f'/company/{self.company_id}/consolidated-osv/{second}/confirm',
                data={'confirm_complete_scope': '1'},
                follow_redirects=True)
        self.assertIn(
            'Предыдущий контрольный снимок сохранён',
            response.get_data(as_text=True))
        self.assertTrue(os.path.isfile(old_path))
        with open(old_path, 'rb') as stream:
            self.assertEqual(
                hashlib.sha256(stream.read()).hexdigest(), old_digest)
        self.assertFalse(os.path.exists(os.path.join(
            control_dir, 'consolidated_2026-01-01_2026-06-30.xlsx')))

if __name__ == '__main__':
    unittest.main()