# -*- coding: utf-8 -*-
import os
import unittest

from consolidated_osv import parse_consolidated_osv
from key_indicators import (
    _top_balance,
    _top_opening_balance,
    build_key_indicators,
)
from key_indicators_service import assemble_source


CONTROL = os.path.join(
    os.path.dirname(__file__),
    'control_snapshots', '3', 'osv_all_2026_h1_control.xls')


class ConsolidatedOsvTests(unittest.TestCase):
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

if __name__ == '__main__':
    unittest.main()