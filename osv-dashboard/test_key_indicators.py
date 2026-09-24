# -*- coding: utf-8 -*-
import unittest
from datetime import date, timedelta
import time

from key_indicators import (build_indicator_source, build_key_indicators,
                            revenue_analytic_key, _period_bounds,
                            _collection_components_from_rows,
                            _collection_period_table,
                            _revenue_activity_groups)


def account(acc, daily=(), rows=(), top=None, period='за январь 2026 г.'):
    if top is None:
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0)
    return dict(acc=acc, daily=list(daily), rows=list(rows), top=top,
                period=period, warnings=[], check_ok=True)


def day(dt, p3='', p1='', obd=0, obk=0, **balance):
    return dict(date=dt, month=dt[:7], path1=p1, path2='', path3=p3,
                obd=obd, obk=obk, **balance)

class CashSalaryOriginalKeyIndicatorTests(unittest.TestCase):
    def test_card_titles_use_short_display_names(self):
        cards = {card['id']: card for card in build_key_indicators(
            build_indicator_source({
                '90': account('90', [day('2026-01-31', '90.02', obd=10)])
            }))['cards']}
        self.assertEqual(cards['profit']['title'], 'Прибыль от продаж')
        self.assertEqual(cards['collection']['title'], 'Уровень собираемости')

    def test_account90_categories_classify_full_paths_and_reconcile_totals(self):
        rows = [
            day('2026-01-31', '90.01.1', p1='Тепловая ЭНЕРГИЯ',
                obk=2_000_000),
            day('2026-01-31', '90.01', p1='ВОДОСНАБЖЕНИЕ',
                obk=1_000_000),
            day('2026-01-31', '90.02.1', p1='водоотведение',
                obd=300_000),
            day('2026-01-31', '90.02', p1='ТЕХНОЛОГИЧЕСКОЕ ПРИСОЕДИНЕНИЕ',
                obd=200_000),
            day('2026-01-31', '90.01', p1='Неизвестная услуга', obk=500_000),
            day('2026-01-31', '90.08.1', obd=300_000),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}))
        table = result['account90_categories']
        by_id = {row['id']: row for row in table['categories']}
        self.assertEqual(
            [row['label'] for row in table['categories']],
            ['Тепловая энергия', 'Водоснабжение', 'Водоотведение',
             'Технологическое присоединение', 'Прочие виды деятельности'])
        self.assertEqual(by_id['thermal_energy']['revenue'], 2)
        self.assertEqual(by_id['water_supply']['revenue'], 1)
        self.assertEqual(by_id['wastewater']['cogs'], 0.3)
        self.assertEqual(by_id['technological_connection']['cogs'], 0.2)
        self.assertEqual(by_id['other']['revenue'], 0.5)
        self.assertEqual(table['totals']['revenue'], 3.5)
        self.assertEqual(table['totals']['cogs'], 0.5)
        self.assertEqual(table['totals']['ohr'], 0.3)

    def test_account90_categories_hide_empty_and_allocate_ohr_by_revenue(self):
        rows = [
            day('2026-01-31', '90.01', p1='Тепловая энергия', obk=3_000_000),
            day('2026-01-31', '90.02', p1='Тепловая энергия', obd=1_000_000),
            day('2026-01-31', '90.08', obd=600_000),
        ]
        table = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
        )['account90_categories']
        by_id = {row['id']: row for row in table['categories']}
        self.assertFalse(by_id['water_supply']['visible'])
        self.assertEqual(by_id['thermal_energy']['ohr'], 0.6)
        self.assertEqual(by_id['thermal_energy']['gross_profit'], 1.4)
        self.assertEqual(by_id['thermal_energy']['profitability'],
                         46.666667)

    def test_account90_categories_zero_revenue_does_not_allocate_ohr(self):
        rows = [day('2026-01-31', '90.08', p1='Тепло', obd=500_000)]
        table = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
        )['account90_categories']
        self.assertEqual(table['totals']['ohr'], 0)
        self.assertTrue(any('выручка равна нулю' in warning
                            for warning in table['warnings']))

    def test_account90_categories_use_account26_before_period_closing(self):
        rows90 = [
            day('2026-01-31', '90.01', p1='Тепловая энергия',
                obk=3_000_000),
            day('2026-01-31', '90.02', p1='Тепловая энергия',
                obd=1_000_000),
        ]
        rows26 = [
            day('2026-01-31', '26', p1='Общехозяйственные расходы',
                obd=600_000),
        ]
        table = build_key_indicators(build_indicator_source({
            '90': account('90', rows90),
            '26': account('26', rows26),
        }))['account90_categories']
        thermal = next(row for row in table['categories']
                       if row['id'] == 'thermal_energy')
        self.assertEqual(table['ohr_basis'], '26')
        self.assertEqual(table['totals']['ohr'], 0.6)
        self.assertEqual(thermal['ohr'], 0.6)
        self.assertEqual(thermal['gross_profit'], 1.4)

    def test_salary_balance_rolls_forward_daily_movements_from_report_opening(self):
        raw = account(
            '70',
            [
                day('2025-12-31', 'Работник А', obd=10, obk=30),
                day('2026-01-01', 'Работник А'),
                day('2026-06-30', 'Работник А', obd=20, obk=50),
                day('2026-06-30', 'Работник Б', obd=30, obk=30),
            ],
            top=dict(snd=0, snk=100, obd=60, obk=110, skd=0, skk=150),
            period='за 01.01.2025 - 15.09.2026',
        )
        cards = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'70': raw}),
            period='day', date_from='2026-01-01', date='2026-06-30',
        )['cards']}
        self.assertEqual(cards['salary']['value'], 150)
        self.assertEqual(cards['salary']['debit_balance'], 0)

    def test_salary_exposes_comparable_apg_accruals_and_preserves_missing(self):
        raw = account(
            '70',
            [
                day('2025-01-31', 'Работник А', obk=80),
                day('2025-02-28', 'Работник А', obk=0),
                day('2026-01-31', 'Работник А', obk=100),
            ],
            period='за 01.01.2025 - 28.02.2026',
        )
        salary = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'70': raw}),
            period='month', date_from='2026-01-31', date='2026-02-28',
        )['cards']}['salary']

        self.assertEqual(salary['previous_year_accrual'], 80)
        self.assertEqual(salary['accrual_delta'], 20)
        self.assertEqual(salary['accrual_delta_percent'], 25.0)
        self.assertEqual(
            [row['current_accrual'] for row in salary['monthly_comparison']],
            [100, None])
        self.assertEqual(
            [row['previous_year_accrual'] for row in salary['monthly_comparison']],
            [80, 0])
        self.assertEqual(
            [row['employee_debt'] for row in salary['monthly_comparison']],
            [None, 0])
        self.assertEqual(
            [row['delta'] for row in salary['monthly_comparison']],
            [20, None])

    def test_salary_apg_percent_is_missing_for_zero_or_missing_comparison_base(self):
        raw = account(
            '70',
            [day('2025-01-31', 'Работник А', obk=0),
             day('2026-01-31', 'Работник А', obk=100)],
            period='за 01.01.2025 - 31.01.2026',
        )
        salary = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'70': raw}),
            period='month', date_from='2026-01-31', date='2026-01-31',
        )['cards']}['salary']
        self.assertEqual(salary['accrual_delta'], 100)
        self.assertIsNone(salary['accrual_delta_percent'])

    def test_quality_missing_accounts_use_account_numbers_not_metric_ids(self):
        result = build_key_indicators(build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.01', obk=100)]),
        }))
        missing = result['quality']['missing_accounts']
        self.assertIn('62', missing)
        self.assertIn('08', missing)
        self.assertNotIn('collection', missing)
        self.assertNotIn('capex', missing)
        self.assertEqual(len(missing), len(set(missing)))
        for number in missing:
            self.assertRegex(number, r'^\d{2}(?:\.\d+)?$')

    def test_absent_is_null_but_explicit_zero_is_zero(self):
        data = {'90': account('90', [day('2026-01-31', '90.01', obk=0),
                                     day('2026-01-31', '90.03', obd=0),
                                     day('2026-01-31', '90.02', obd=0)])}
        result = build_key_indicators(build_indicator_source(data))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 0)
        self.assertIsNone(cards['cash']['value'])

    def test_period_filter_and_missing_vat_is_gross(self):
        rows = [day('2026-01-31', '90.01', obk=100),
                day('2026-01-31', '90.02', obd=70),
                day('2026-02-28', '90.01', obk=900),
                day('2026-02-28', '90.02', obd=800)]
        result = build_key_indicators(build_indicator_source(
            {'90': account('90', rows, period='за 1 квартал 2026 г.')}),
            date='2026-01-31')
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 100)
        self.assertIn('брутто', cards['revenue']['formula'])

    def test_cogs_prefers_latest_full_report_daily_rows_to_old_exact_report(self):
        raw = account(
            '90',
            [
                day('2026-01-01', '90.02'),
                day('2026-01-31', '90.01', obk=150),
                day('2026-01-31', '90.03', obd=20),
                day('2026-01-31', '90.02', obd=90),
                day('2026-06-30', '90.02'),
            ],
            period='за 01.01.2025 - 15.09.2026',
        )
        raw['balance_reports'] = [{
            'period': 'за 1 полугодие 2026 г.',
            'rows': [
                dict(lvl=1, label='90.01', p0='90', p1='90.01',
                     obd=0, obk=120),
                dict(lvl=1, label='90.03', p0='90', p1='90.03',
                     obd=20, obk=0),
                dict(lvl=1, label='90.02', p0='90', p1='90.02',
                     obd=70, obk=0),
            ],
        }]
        result = build_key_indicators(
            build_indicator_source({'90': raw}),
            period='day', date_from='2026-01-01', date='2026-06-30')
        cards = {item['id']: item for item in result['cards']}
        self.assertEqual(cards['cogs']['value'], 90)

    def test_cumulative_reports_restore_vat_and_year_over_year(self):
        raw = account('90', [
            day('2025-09-15', '90.01', obk=100),
        ])
        raw['balance_reports'] = [
            {
                'period': 'за 2025 г.',
                'top': dict(obd=20, obk=100),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=100),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=20, obk=0),
                ],
            },
            {
                'period': 'за 01.01.2025 - 15.09.2026',
                'top': dict(obd=50, obk=300),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=300),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=50, obk=0),
                ],
            },
            {
                # A narrower report accepted later must not replace the
                # complete account 90 report for the same interval.
                'period': 'за 01.01.2025 - 15.09.2026',
                'top': dict(obd=0, obk=999),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=999),
                ],
            },
        ]
        result = build_key_indicators(
            build_indicator_source({'90': raw}),
            period='year', date='2026-09-15')
        revenue = {card['id']: card for card in result['cards']}['revenue']
        details = {item['label']: item['value']
                   for item in revenue['details']}
        self.assertEqual(details['Выручка брутто'], 200)
        self.assertEqual(details['НДС'], 30)
        self.assertEqual(revenue['value'], 170)
        self.assertEqual(revenue['year_over_year']['current'], 200)
        self.assertEqual(revenue['year_over_year']['previous'], 100)
        self.assertEqual(revenue['year_over_year']['delta'], 100)
        self.assertEqual(revenue['year_over_year']['basis'], 'gross')

    def test_revenue_yoy_uses_confirmed_net_totals_when_vat_exists_for_both_periods(self):
        raw = account('90')
        raw['balance_reports'] = [
            {
                'period': 'за 1 полугодие 2025 г.',
                'top': dict(obd=40000000, obk=231929419.40),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=231929419.40),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=40000000, obk=0),
                ],
            },
            {
                'period': 'за 1 полугодие 2026 г.',
                'top': dict(obd=92094976.73, obk=510714933.29),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=510714933.29),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=92094976.73, obk=0),
                ],
            },
        ]
        revenue = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'90': raw}),
            period='year', date='2026-06-30')['cards']}['revenue']
        self.assertEqual(revenue['value'], 418619956.56)
        self.assertEqual(revenue['year_over_year']['previous'], 191929419.40)
        self.assertEqual(revenue['year_over_year']['delta'], 226690537.16)
        self.assertEqual(revenue['year_over_year']['percent'], 118.11)
        self.assertEqual(revenue['year_over_year']['basis'], 'net')
        comparison = revenue['year_over_year_components']
        self.assertEqual(comparison['start'], '2025-01-01')
        self.assertEqual(comparison['end'], '2025-06-30')
        self.assertEqual(comparison['metrics']['gross'], {
            'current': 510714933.29,
            'previous': 231929419.4,
            'delta': 278785513.89,
            'percent': 120.2,
            'available': True,
        })
        self.assertEqual(comparison['metrics']['vat'], {
            'current': 92094976.73,
            'previous': 40000000.0,
            'delta': 52094976.73,
            'percent': 130.24,
            'available': True,
        })
        self.assertEqual(comparison['metrics']['net'], {
            'current': 418619956.56,
            'previous': 191929419.4,
            'delta': 226690537.16,
            'percent': 118.11,
            'available': True,
        })
        self.assertAlmostEqual(
            comparison['metrics']['gross']['previous']
            - comparison['metrics']['vat']['previous'],
            comparison['metrics']['net']['previous'])

    def test_revenue_yoy_components_keep_missing_vat_unconfirmed(self):
        raw = account('90')
        raw['balance_reports'] = [
            {
                'period': 'за 1 полугодие 2025 г.',
                'top': dict(obd=0, obk=100),
                'rows': [dict(lvl=1, label='90.01', p0='90', p1='90.01',
                              obd=0, obk=100)],
            },
            {
                'period': 'за 1 полугодие 2026 г.',
                'top': dict(obd=20, obk=150),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=150),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=20, obk=0),
                ],
            },
        ]
        revenue = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'90': raw}),
            period='year', date='2026-06-30')['cards']}['revenue']
        metrics = revenue['year_over_year_components']['metrics']
        self.assertTrue(metrics['gross']['available'])
        self.assertFalse(metrics['vat']['available'])
        self.assertFalse(metrics['net']['available'])
        self.assertIsNone(metrics['net']['previous'])

    def test_revenue_yoy_component_zero_base_has_no_percent(self):
        raw = account('90')
        raw['balance_reports'] = [
            {
                'period': 'за 1 полугодие 2025 г.',
                'top': dict(obd=0, obk=0),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=0),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=0, obk=0),
                ],
            },
            {
                'period': 'за 1 полугодие 2026 г.',
                'top': dict(obd=20, obk=120),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=120),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=20, obk=0),
                ],
            },
        ]
        revenue = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'90': raw}),
            period='year', date='2026-06-30')['cards']}['revenue']
        metrics = revenue['year_over_year_components']['metrics']
        for metric in metrics.values():
            self.assertTrue(metric['available'])
            self.assertEqual(metric['previous'], 0)
            self.assertIsNone(metric['percent'])

    def test_cumulative_report_can_subtract_tiled_prefix_for_quarter_vat(self):
        raw = account('90')
        raw['balance_reports'] = [
            {
                'period': 'за 2025 г.',
                'top': dict(obd=20, obk=100),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=100),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=20, obk=0),
                ],
            },
            {
                'period': 'за 1 полугодие 2026 г.',
                'top': dict(obd=40, obk=200),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=200),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=40, obk=0),
                ],
            },
            {
                'period': 'за 01.01.2025 - 15.09.2026',
                'top': dict(obd=90, obk=450),
                'rows': [
                    dict(lvl=1, label='90.01', p0='90', p1='90.01',
                         obd=0, obk=450),
                    dict(lvl=1, label='90.03', p0='90', p1='90.03',
                         obd=90, obk=0),
                ],
            },
        ]
        result = build_key_indicators(
            build_indicator_source({'90': raw}),
            period='quarter', date='2026-09-15')
        revenue = {card['id']: card for card in result['cards']}['revenue']
        details = {item['label']: item['value']
                   for item in revenue['details']}
        self.assertEqual(details['Выручка брутто'], 150)
        self.assertEqual(details['НДС'], 30)
        self.assertEqual(revenue['value'], 120)

    def test_month_range_sums_from_selected_start_through_end(self):
        rows = [
            day('2026-08-31', '90.01', obk=10),
            day('2026-09-30', '90.01', obk=20),
            day('2026-10-31', '90.01', obk=30),
        ]
        result = build_key_indicators(
            build_indicator_source({
                '90': account('90', rows, period='за 3 квартал 2026 г.'),
            }),
            period='month', date='2026-10-31', date_from='2026-08-31')
        revenue = {card['id']: card for card in result['cards']}['revenue']
        self.assertEqual(revenue['value'], 60)
        self.assertEqual(result['period']['start'], '2026-08-01')
        self.assertEqual(result['period']['end'], '2026-10-31')
        self.assertEqual(result['period']['date_from'], '2026-08-31')
        self.assertIsNone(revenue['delta'])

    def test_day_range_sums_selected_dates_inclusively(self):
        rows = [
            day('2026-10-01', '90.01', obk=10),
            day('2026-10-02', '90.01', obk=20),
            day('2026-10-03', '90.01', obk=30),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            period='day', date='2026-10-03', date_from='2026-10-01')
        revenue = {card['id']: card for card in result['cards']}['revenue']
        self.assertEqual(revenue['value'], 60)
        self.assertEqual(result['period']['start'], '2026-10-01')
        self.assertEqual(result['period']['end'], '2026-10-03')
        self.assertEqual(result['period']['date_from'], '2026-10-01')
        self.assertIsNone(revenue['delta'])
        self.assertEqual([p['value'] for p in revenue['series']], [10, 20, 30])

    def test_quarter_range_charts_use_daily_slots_without_changing_totals(self):
        rows = [day('2026-03-31', obd=100),
                day('2026-04-30', obd=10), day('2026-05-31', obd=20),
                day('2026-06-30', obd=30), day('2026-08-31', obd=40),
                day('2026-09-30', obd=50), day('2026-12-31', obd=900)]
        source = build_indicator_source({'08': account('08', rows)})
        result = build_key_indicators(source, period='quarter',
                                      date_from='2026-06-30', date='2026-09-30')
        card = next(c for c in result['cards'] if c['id'] == 'capex')
        self.assertEqual(card['value'], 150)
        self.assertEqual(result['period']['start'], '2026-04-01')
        self.assertEqual(result['period']['end'], '2026-09-30')
        self.assertEqual(result['period']['series_kind'], 'day')
        self.assertEqual(len(card['series']), 183)
        series = {point['date']: point['value'] for point in card['series']}
        self.assertEqual(series['2026-04-30'], 10)
        self.assertEqual(series['2026-05-31'], 20)
        self.assertEqual(series['2026-06-30'], 30)
        self.assertEqual(series['2026-08-31'], 40)
        self.assertEqual(series['2026-09-30'], 50)
        self.assertIsNone(series['2026-07-31'])
        self.assertIsNone(card['delta'])
        with self.assertRaises(ValueError):
            build_key_indicators(source, period='quarter',
                                 date_from='2026-09-30', date='2026-06-30')

    def test_daily_chart_preserves_gaps_and_exact_balances(self):
        rows = [day('2026-10-01', obd=10, skd=10, skk=0),
                day('2026-10-03', obd=30, skd=40, skk=0),
                day('2026-10-05', obd=500, skd=540, skk=0)]
        result = build_key_indicators(
            build_indicator_source({'08': account('08', rows)}),
            period='day', date_from='2026-10-01', date='2026-10-03')
        card = next(c for c in result['cards'] if c['id'] == 'capex')
        self.assertEqual([p['value'] for p in card['series']], [10, None, 30])
        self.assertEqual([p['value'] for p in card['balance_series']], [10, None, 40])
        self.assertEqual(card['closing_balance'], 40)

    def test_ar_period_chart_rolls_daily_balance_from_opening(self):
        raw = account(
            '62',
            [
                day('2026-01-01', '62.01', obd=10, obk=0, p1='Покупатель А'),
                day('2026-01-01', '62.01', obd=5, obk=0, p1='Покупатель Б'),
                day('2026-01-03', '62.01', obd=0, obk=20),
            ],
            period='за 01.01.2026 - 03.01.2026',
        )
        raw['rows'] = [
            dict(lvl=1, label='62.01', p0='62', p1='62.01',
                 p2='', p3='', snd=100, snk=0, obd=15, obk=20,
                 skd=95, skk=0),
        ]
        result = build_key_indicators(
            build_indicator_source({'62': raw}),
            period='day', date_from='2026-01-01', date='2026-01-03')
        card = next(item for item in result['cards'] if item['id'] == 'cash_dz')
        self.assertEqual(
            [point['value'] for point in card['series']],
            [115, 115, 95])
        self.assertEqual(card['granularity'], 'day')

    def test_ap_balance_uses_latest_full_report_daily_movements(self):
        raw = account(
            '60',
            [
                day('2025-12-31', '60.01', obd=20, obk=10,
                    p1='Поставщик А'),
                day('2026-01-01', '60.01', p1='Поставщик А'),
                day('2026-06-30', '60.01', obd=30, obk=50,
                    p1='Поставщик А'),
            ],
            period='за 01.01.2025 - 15.09.2026',
        )
        raw['rows'] = [
            dict(lvl=1, label='60.01', p0='60', p1='60.01',
                 p2='', p3='', snd=0, snk=100, obd=50, obk=60,
                 skd=0, skk=110),
        ]
        result = build_key_indicators(
            build_indicator_source({'60': raw}),
            period='day', date_from='2026-01-01', date='2026-06-30')
        card = next(item for item in result['cards'] if item['id'] == 'ap')
        self.assertEqual(card['value'], 110)
        self.assertEqual(card['opening_value'], 90)

    def test_balance_only_at_original_report_boundary(self):
        top = dict(snd=1, snk=0, obd=0, obk=0, skd=25, skk=0)
        src = build_indicator_source({
            '51': account('51', [day('2026-01-31'), day('2026-02-28')],
                          top=top, period='за 1 квартал 2026 г.')
        })
        jan = build_key_indicators(src, date='2026-01-31')
        self.assertIsNone({x['id']: x for x in jan['cards']}['cash']['value'])

    def test_balances_roll_to_any_date_inside_reconciled_reports(self):
        ar = account(
            '62',
            [
                day('2026-01-01', '62.01', obd=50),
                day('2026-08-31', '62.01', obd=100, obk=20),
                day('2026-12-31', '62.01', obk=30),
            ],
            period='за 01.01.2026 - 31.12.2026',
        )
        ar['rows'] = [
            dict(lvl=1, label='62.01', p0='62', p1='62.01',
                 p2='', p3='', snd=200, snk=0, obd=150, obk=50,
                 skd=300, skk=0),
        ]
        ap = account(
            '60',
            [
                day('2026-01-01', '60.01', obd=10, obk=20),
                day('2026-08-31', '60.01', obd=30, obk=50),
                day('2026-12-31', '60.01', obd=10),
            ],
            period='за 01.01.2026 - 31.12.2026',
        )
        ap['rows'] = [
            dict(lvl=1, label='60.01', p0='60', p1='60.01',
                 p2='', p3='', snd=0, snk=100, obd=50, obk=70,
                 skd=0, skk=120),
        ]
        result = build_key_indicators(
            build_indicator_source({
                '62': ar,
                '60': ap,
                '66': account(
                    '66',
                    [day('2026-08-31', obk=40),
                     day('2026-12-31', obd=10)],
                    top=dict(snd=0, snk=0, obd=10, obk=40,
                             skd=0, skk=30),
                    period='за 01.01.2026 - 31.12.2026'),
                '67': account(
                    '67', [],
                    top=dict(snd=0, snk=0, obd=0, obk=0,
                             skd=0, skk=0),
                    period='за 01.01.2026 - 31.12.2026'),
                '70': account(
                    '70',
                    [day('2026-08-31', obd=30, obk=50),
                     day('2026-12-31', obd=20)],
                    top=dict(snd=0, snk=100, obd=50, obk=50,
                             skd=0, skk=100),
                    period='за 01.01.2026 - 31.12.2026'),
            }),
            period='day', date_from='2026-01-01', date='2026-08-31',
        )
        cards = {item['id']: item for item in result['cards']}
        self.assertEqual(cards['cash_dz']['value'], 330)
        self.assertEqual(cards['ap']['value'], 130)
        self.assertTrue(all(
            'previous_year_value' in item
            and 'previous_year_delta_percent' in item
            for item in cards['ap']['components']))
        self.assertEqual(cards['credits']['value'], 40)
        self.assertEqual(cards['salary']['value'], 120)

    def test_intermediate_balance_requires_reconciled_daily_movements(self):
        top = dict(snd=10, snk=0, obd=100, obk=0, skd=110, skk=0)
        src = build_indicator_source({
            '51': account(
                '51', [day('2026-01-31', obd=20)], top=top,
                period='за 01.01.2026 - 31.12.2026')
        })
        result = build_key_indicators(
            src, period='day', date='2026-01-31',
            date_from='2026-01-31')
        self.assertIsNone(
            {item['id']: item for item in result['cards']}['cash']['value'])

    def test_objects_reconcile_and_no_parent_child_double_count(self):
        rows = [
            day('2026-01-31', '90.01', p1='Услуга', obk=100),
            day('2026-01-31', '90.02', p1='Услуга', obd=60),
            day('2026-01-31', '90.03', p1='Услуга', obd=20),
        ]
        result = build_key_indicators(build_indicator_source({'90': account('90', rows)}))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(result['objects']['totals']['revenue'], cards['revenue']['value'])
        self.assertEqual(result['objects']['totals']['profit'], cards['profit']['value'])

    def test_salary_is_not_called_overdue(self):
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=50)
        result = build_key_indicators(build_indicator_source({
            '70': account('70', [day('2026-01-31')], top=top)
        }))
        salary = {x['id']: x for x in result['cards']}['salary']
        self.assertEqual(salary['value'], 50)
        self.assertIn('не подтверждает просрочку', salary['notes'][0])

    def test_salary_detail_keeps_balances_turnovers_and_analytics_separate(self):
        src = build_indicator_source({
            '70': account('70', [
                day('2026-01-31', obd=80, obk=100, skd=0, skk=20),
                day('2026-01-31', p1='Подразделение А',
                    obd=80, obk=100, skd=0, skk=20),
            ], top=dict(snd=0, snk=0, obd=80, obk=100, skd=0, skk=20)),
            '69': account('69', [
                day('2026-01-31', obd=24, obk=30, skd=0, skk=6),
                day('2026-01-31', p1='Взносы А',
                    obd=24, obk=30, skd=0, skk=6),
            ], top=dict(snd=0, snk=0, obd=24, obk=30, skd=0, skk=6)),
        })
        salary = {item['id']: item for item in
                  build_key_indicators(src)['cards']}['salary']
        self.assertEqual(salary['value'], 20)
        self.assertEqual(salary['credit_turnover'], 100)
        self.assertEqual(salary['debit_turnover'], 80)
        self.assertEqual(len(salary['analytics']), 1)
        self.assertEqual(salary['insurance']['credit_balance'], 6)
        self.assertEqual(salary['insurance']['credit_turnover'], 30)
        self.assertEqual(salary['insurance']['turnover_ratio'], 30)
        self.assertEqual(len(salary['insurance']['analytics']), 1)
        self.assertIn('не как подтверждённый ФОТ', salary['notes'][1])

    def test_missing_insurance_account_is_not_reported_as_zero(self):
        src = build_indicator_source({
            '70': account('70', [day('2026-01-31', obd=8, obk=10)],
                          top=dict(snd=0, snk=0, obd=8, obk=10,
                                   skd=0, skk=2)),
        })
        salary = {item['id']: item for item in
                  build_key_indicators(src)['cards']}['salary']
        self.assertIsNone(salary['insurance']['credit_balance'])
        self.assertIsNone(salary['insurance']['credit_turnover'])
        self.assertIsNone(salary['insurance']['turnover_ratio'])

    def test_capex_detail_separates_turnover_balance_and_analytics(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-01-31', obd=30, obk=5, skd=35, skk=0),
                day('2026-01-31', p1='Объект А',
                    obd=30, obk=5, skd=35, skk=0),
            ], top=dict(snd=10, snk=0, obd=30, obk=5, skd=35, skk=0)),
        })
        capex = {item['id']: item for item in
                 build_key_indicators(src)['cards']}['capex']
        self.assertEqual(capex['value'], 30)
        self.assertEqual(capex['debit_turnover'], 30)
        self.assertEqual(capex['credit_turnover'], 5)
        self.assertEqual(capex['opening_balance'], 10)
        self.assertEqual(capex['closing_balance'], 35)
        self.assertEqual(len(capex['analytics']), 1)
        self.assertEqual(capex['analytics'][0]['value'], 35)
        self.assertEqual(capex['analytics'][0]['turnover_share'], 100)
        self.assertEqual(capex['analytics'][0]['balance_share'], 100)

    def test_missing_capex_balance_is_not_reported_as_zero(self):
        src = build_indicator_source({
            '08': account('08', [day('2026-01-31', obd=12, obk=2)],
                          top=None),
        })
        capex = {item['id']: item for item in
                 build_key_indicators(src)['cards']}['capex']
        self.assertEqual(capex['value'], 12)
        self.assertEqual(capex['opening_balance'], 0)
        self.assertEqual(capex['closing_balance'], 0)

    def test_capex_comparison_uses_two_loaded_quarters(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-03-31', obd=10),
                day('2026-06-30', obd=25),
            ], period='за 1 полугодие 2026 г.'),
        })
        capex = {item['id']: item for item in build_key_indicators(
            src, period='quarter', date='2026-06-30')['cards']}['capex']
        self.assertEqual(capex['value'], 25)
        self.assertEqual(capex['delta']['value'], 15)

    def test_capex_includes_previous_year_value_and_series(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2025-01-31', obd=12, obk=2, skd=20),
                day('2026-01-31', obd=30, obk=5, skd=45),
            ], top=dict(snd=20, snk=0, obd=30, obk=5, skd=45, skk=0)),
        })
        capex = {item['id']: item for item in
                 build_key_indicators(src)['cards']}['capex']
        self.assertEqual(capex['previous_year_value'], 12)
        self.assertEqual(capex['previous_year_period'], {
            'start': '2025-01-01', 'end': '2025-01-31'})
        self.assertEqual(capex['previous_year_series'][-1]['value'], 12)
        apg = next(
            item for item in capex['details']
            if item['label'] == 'Дебетовый оборот 08 АПГ')
        self.assertEqual(apg['value'], 12)
        self.assertEqual(apg['series'][-1]['value'], 12)

    def test_false_day_balance_and_cross_period_leakage(self):
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=100, skk=0)
        src = build_indicator_source({
            '51': account('51', [day('2026-01-30'), day('2026-01-31')],
                          top=top)
        })
        result = build_key_indicators(src, 'day', '2026-01-30')
        self.assertIsNone({x['id']: x for x in result['cards']}['cash']['value'])

    def test_partial_period_warning_and_invalid_inputs(self):
        src = build_indicator_source({'08': account('08', [day('2026-03-15', obd=4)],
                                                     period='за 1 квартал 2026 г.')})
        result = build_key_indicators(src, 'year')
        self.assertTrue(result['period']['warnings'])
        with self.assertRaises(ValueError):
            build_key_indicators(src, 'week')
        with self.assertRaises(ValueError):
            build_key_indicators(src, 'month', '2025-01-01')

    def test_ar_known_scope_and_other_76_unmapped(self):
        row = dict(lvl=1, label='62.01', p0='62', p1='62.01', p2='', p3='',
                   snd=0, snk=0, obd=0, obk=0, skd=12, skk=0)
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')], [row]),
            '76': account('76', [day('2026-01-31')]),
        })
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['cash_dz']
        self.assertEqual(ar['value'], 12)
        self.assertIn('76', ar['notes'][0])

    def test_ar_detail_exposes_opening_and_analytic_balance(self):
        report_row = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                          p2='', p3='', snd=80, snk=0, obd=20, obk=0,
                          skd=100, skk=0)
        analytic = day('2026-01-31', '62.01', p1='Покупатель А',
                       obd=20, obk=0, snd=80, snk=0, skd=100, skk=0)
        src = build_indicator_source({
            '62': account('62', [analytic], [report_row])
        })
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['cash_dz']
        self.assertEqual(ar['value'], 100)
        self.assertEqual(ar['opening_value'], 80)
        self.assertEqual(len(ar['analytics']), 1)
        self.assertEqual(ar['analytics'][0]['value'], 100)
        self.assertIn('Покупатель А', ar['analytics'][0]['label'])

    def test_cash_dz_uses_consumer_balances_and_percent_apg_comparison(self):
        rows = [
            day('2025-01-31', '62.01', skd=100, skk=0),
            day('2025-01-31', '62.02', skd=0, skk=20),
            day('2026-01-31', '62.01', skd=150, skk=0),
            day('2026-01-31', '62.02', skd=0, skk=30),
        ]
        result = build_key_indicators(
            build_indicator_source({'62': account('62', rows)}),
            period='month', date='2026-01-31')
        cards = {item['id']: item for item in result['cards']}
        dz = cards['cash_dz']

        self.assertEqual(dz['value'], 150)
        self.assertEqual(dz['consumer_series'][-1]['value'], 150)
        self.assertEqual(dz['advances_series'], [])
        self.assertEqual(dz['previous_year_consumer_series'][-1]['value'], None)
        self.assertEqual(dz['period_change']['value'], None)
        self.assertEqual(dz['monthly_comparison'], [{
            'month': '2026-01',
            'label': 'Янв 2026',
            'date': '2026-01-31',
            'current_dz': 150,
            'apg_dz': 100,
            'previous_year_dz': 100,
            'delta': 50,
            'percent_delta': 50.0,
        }])
        self.assertEqual(cards['cash_dz']['value'], 150)

    def test_ap_detail_exposes_components_opening_turnover_and_analytics(self):
        report_row = dict(lvl=1, label='60.01', p0='60', p1='60.01',
                          p2='', p3='', snd=0, snk=80, obd=0, obk=20,
                          skd=0, skk=100)
        supplier = day('2026-01-31', '60.01', p1='Поставщик А',
                       obd=0, obk=20, snd=0, snk=80, skd=0, skk=100)
        src = build_indicator_source({
            '60': account('60', [supplier], [report_row]),
            '68': account('68', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=12)),
            '69': account('69', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=3)),
            '70': account('70', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=5)),
        })
        ap = {x['id']: x for x in build_key_indicators(src)['cards']}['ap']
        self.assertEqual(ap['value'], 100)
        self.assertEqual(ap['opening_value'], 80)
        self.assertEqual(ap['credit_turnover'], 20)
        self.assertEqual(ap['known_total'], 120)
        self.assertEqual(ap['analytics'][0]['value'], 100)
        self.assertIn('Поставщик А', ap['analytics'][0]['label'])
        self.assertEqual(
            {item['account']: item['value'] for item in ap['components']},
            {'60.01': 100, '68': 12, '69': 3, '70': 5})

    def test_ap_monthly_balances_compare_same_dates_without_inventing_apg(self):
        def report(start, end, opening, closing):
            balance = dict(snd=0, snk=opening, obd=0,
                           obk=closing-opening, skd=0, skk=closing)
            return {
                'period': f'за {start} - {end}',
                'top': balance,
                'rows': [dict(lvl=1, label='60.01', p0='60', p1='60.01',
                              p2='', p3='', **balance)],
            }

        raw = account('60', [
            day('2025-01-31', p1='60.01', obk=20),
            day('2026-01-31', p1='60.01', obk=20),
            day('2026-02-28', p1='60.01', obk=30),
        ], [], period='за 01.01.2026 - 28.02.2026')
        raw['balance_reports'] = [
            report('01.01.2025', '31.01.2025', 50, 70),
            report('01.01.2026', '31.01.2026', 80, 100),
            report('01.02.2026', '28.02.2026', 100, 130),
        ]
        source = build_indicator_source({'60': raw})
        result = build_key_indicators(
            source, period='month', date_from='2026-01-31',
            date='2026-02-28')
        ap = next(card for card in result['cards'] if card['id'] == 'ap')
        rows = ap['monthly_comparison']
        self.assertEqual([row['date'] for row in rows],
                         ['2026-01-31', '2026-02-28'])
        self.assertEqual(rows[0]['previous_year_date'], '2025-01-31')
        self.assertEqual(rows[0]['value'], 100)
        self.assertEqual(rows[0]['change'], 20)
        self.assertEqual(rows[0]['previous_year_value'], 70)
        self.assertEqual(rows[0]['previous_year_delta'], 30)
        self.assertEqual(rows[1]['value'], ap['value'])
        self.assertEqual(rows[1]['change'], 30)
        self.assertIsNone(rows[1]['previous_year_value'])
        self.assertIsNone(rows[1]['previous_year_delta'])
        self.assertIsNone(rows[1]['previous_year_delta_percent'])
        self.assertEqual(
            [(point['date'], point['source_date'], point['value'])
             for point in ap['previous_year_series']],
            [('2026-01-31', '2025-01-31', 70),
             ('2026-02-28', '2025-02-28', None)])

    def test_ap_detail_uses_mixed_proven_boundaries_for_august_2026(self):
        def report(period, top, rows=(), complete=False, updated=''):
            return {
                'period': period,
                'top': top,
                'rows': list(rows),
                'complete_snapshot': complete,
                'source_updated_at': updated,
            }

        def credit_top(opening, debit, credit):
            closing = opening + credit - debit
            return dict(
                snd=0, snk=opening, obd=debit, obk=credit,
                skd=0, skk=closing)

        supplier_current = dict(
            lvl=1, label='60.01', p0='60', p1='60.01', p2='', p3='',
            **credit_top(80, 30, 70))
        supplier_current_analytic = dict(
            lvl=2, label='Поставщик А', p0='60', p1='60.01',
            p2='Поставщик А', p3='', **credit_top(80, 30, 70))
        supplier_previous = dict(
            lvl=1, label='60.01', p0='60', p1='60.01', p2='', p3='',
            **credit_top(50, 20, 50))
        raw_60 = account('60', [], [], period='за 01.01.2026 - 31.08.2026')
        raw_60['balance_reports'] = [
            report(
                'за 01.01.2026 - 31.08.2026',
                credit_top(80, 30, 70),
                [supplier_current, supplier_current_analytic],
                updated='2026-09-02T00:00:00+00:00'),
            # A later narrow revision must not displace the complete parent
            # report at the same exact boundary.
            report(
                'за 01.01.2026 - 31.08.2026',
                credit_top(999, 0, 0), [], updated='2026-09-03T00:00:00+00:00'),
            report(
                'за 01.01.2025 - 31.08.2025',
                credit_top(50, 20, 50), [supplier_previous]),
        ]

        raw_68 = account('68', [], [], period='за 01.01.2026 - 31.08.2026')
        raw_68['balance_reports'] = [
            report(
                'за 01.01.2026 - 31.08.2026',
                credit_top(30, 20, 60), complete=True),
            report(
                'за 01.01.2025 - 31.08.2025',
                credit_top(20, 10, 40), complete=True),
        ]

        raw_69 = account(
            '69',
            [
                day('2025-08-31', obd=5, obk=15),
                day('2026-08-31', obd=10, obk=30),
                day('2026-09-15', obd=0, obk=5),
            ],
            period='за 01.01.2025 - 15.09.2026',
            top=credit_top(25, 15, 50),
        )
        raw_70 = account('70', [], [], period='')
        raw_70['balance_observations'] = [
            dict(date='2025-08-31', skd=0, skk=40),
            dict(date='2026-08-31', skd=0, skk=50),
        ]

        source = build_indicator_source({
            '60': raw_60, '68': raw_68, '69': raw_69, '70': raw_70,
        })
        cards = {item['id']: item for item in build_key_indicators(
            source, period='day',
            date_from='2026-01-01', date='2026-08-31')['cards']}
        ap = cards['ap']
        components = {item['account']: item for item in ap['components']}

        self.assertEqual(
            {key: components[key]['value'] for key in components},
            {'60.01': 120, '68': 70, '69': 55, '70': 50})
        self.assertEqual(
            {key: components[key]['previous_year_value'] for key in components},
            {'60.01': 80, '68': 50, '69': 35, '70': 40})
        self.assertEqual(ap['known_total'], 295)
        self.assertEqual(len(ap['analytics']), 1)
        self.assertIn('Поставщик А', ap['analytics'][0]['label'])
        self.assertEqual(ap['analytics'][0]['value'], 120)
        self.assertEqual(
            [round(item['value'] / ap['known_total'] * 100, 2)
             for item in ap['components']],
            [40.68, 23.73, 18.64, 16.95])

    def test_ap_detail_keeps_missing_when_intermediate_balance_is_unproven(self):
        raw = account(
            '68', [
                day('2026-01-01'),
                day('2026-08-31', obd=10, obk=20),
            ],
            period='за 01.01.2026 - 15.09.2026',
            top=dict(snd=0, snk=30, obd=999, obk=999, skd=0, skk=30),
        )
        source = build_indicator_source({'68': raw})
        ap = {item['id']: item for item in build_key_indicators(
            source, period='day',
            date_from='2026-01-01', date='2026-08-31')['cards']}['ap']
        taxes = next(
            item for item in ap['components'] if item['account'] == '68')
        self.assertIsNone(taxes['value'])
        self.assertIn('исходную границу', taxes['reason'])

    def test_cogs_detail_keeps_recognized_and_forming_costs_separate(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.01', p1='Тепло', obk=100),
                day('2026-01-31', '90.03', p1='Тепло', obd=20),
                day('2026-01-31', '90.02', p1='Тепло', obd=70),
                day('2026-01-31', '90.07', obd=5),
                day('2026-01-31', '90.08', obd=3),
            ]),
            '20': account('20', [day('2026-01-31', p1='Топливо', obd=40)]),
            '25': account('25', [day('2026-01-31', p1='Цех', obd=4)]),
            '26': account('26', [day('2026-01-31', p1='АУП', obd=3)]),
            '44': account('44', [day('2026-01-31', p1='Сбыт', obd=2)]),
        })
        cogs = {x['id']: x for x in build_key_indicators(src)['cards']}['cogs']
        self.assertEqual(cogs['value'], 70)
        self.assertEqual(cogs['analytics'][0]['value'], 70)
        self.assertIn('Тепло', cogs['analytics'][0]['label'])
        self.assertEqual(
            {item['account']: item['value'] for item in cogs['recognized_costs']},
            {'90.02': 70, '90.07': 5, '90.08': 3})
        self.assertEqual(
            {item['account']: item['value'] for item in cogs['cost_movements']},
            {'20': 40, '25': 4, '26': 3, '44': 2})
        self.assertIn('не прибавляются', cogs['notes'][0])

    def test_cost_articles_reconcile_to_selected_recognized_total(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.02', obd=70),
                day('2026-01-31', '90.07', obd=5),
            ]),
            '20': account('20', [
                day('2026-01-31', p1='Газ', obd=40),
                day('2026-01-31', p1='Зарплата', obd=30),
            ]),
        })
        mappings = [
            {'account': '20', 'analytic_key': revenue_analytic_key([label]),
             'analytic_label': f'«{label}»', 'category': category,
             'target_account': '90.02', 'valid_from': '2026-01-01',
             'valid_to': '2026-12-31'}
            for label, category in (('Газ', 'fuel'), ('Зарплата', 'payroll'))
        ]
        card = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=mappings)['cards']}['cogs']
        structure = card['cost_structure']
        self.assertTrue(structure['reconciled'])
        self.assertEqual(structure['recognized_total'], 70)
        self.assertEqual(sum(item['share'] or 0
                             for item in structure['categories']), 100)
        self.assertEqual(structure['selected_targets'], ['90.02'])

    def test_cost_structure_rejects_overlap_and_more_than_one_hundred_percent(self):
        src = build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.02', obd=50)]),
            '20': account('20', [day('2026-01-31', p1='Газ', obd=60)]),
        })
        base = {'account': '20',
                'analytic_key': revenue_analytic_key(['Газ']),
                'analytic_label': '«Газ»', 'target_account': '90.02',
                'valid_from': '2026-01-01', 'valid_to': '2026-12-31'}
        mappings = [dict(base, category='fuel'),
                    dict(base, category='other', valid_from='2026-01-15')]
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=mappings)['cards']}['cogs']['cost_structure']
        self.assertFalse(structure['structure_valid'])
        self.assertTrue(structure['conflicts'])
        self.assertTrue(all(item['share'] is None
                            for item in structure['categories']))

    def test_cost_mapping_changes_category_on_its_effective_date(self):
        src = build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.02', obd=70)]),
            '20': account('20', [
                day('2026-01-10', p1='Энергоресурс', obd=40),
                day('2026-01-20', p1='Энергоресурс', obd=30),
            ]),
        })
        common = {
            'account': '20',
            'analytic_key': revenue_analytic_key(['Энергоресурс']),
            'analytic_label': '«Энергоресурс»',
            'target_account': '90.02',
        }
        mappings = [
            dict(common, category='fuel', valid_from='2026-01-01',
                 valid_to='2026-01-15'),
            dict(common, category='electricity', valid_from='2026-01-16',
                 valid_to='2026-01-31'),
        ]
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=mappings)['cards']}['cogs']['cost_structure']
        amounts = {item['id']: item['value'] for item in structure['categories']}
        self.assertEqual(amounts['fuel'], 40)
        self.assertEqual(amounts['electricity'], 30)
        self.assertTrue(structure['reconciled'])

    def test_cost_structure_suppresses_shares_when_forming_total_exceeds_target(self):
        src = build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.02', obd=50)]),
            '20': account('20', [day('2026-01-31', p1='Газ', obd=60)]),
        })
        mapping = {
            'account': '20', 'analytic_key': revenue_analytic_key(['Газ']),
            'analytic_label': '«Газ»', 'category': 'fuel',
            'target_account': '90.02', 'valid_from': '2026-01-01',
            'valid_to': '2026-12-31'}
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=[mapping])['cards']}['cogs']['cost_structure']
        self.assertFalse(structure['structure_valid'])
        self.assertIn('превышает', structure['conflicts'][0]['message'])
        self.assertTrue(all(item['share'] is None
                            for item in structure['categories']))

    def test_cost_mapping_outside_selected_period_is_unclassified(self):
        src = build_indicator_source({
            '20': account('20', [day('2026-01-31', p1='Газ', obd=10)]),
        })
        mapping = {
            'account': '20', 'analytic_key': revenue_analytic_key(['Газ']),
            'analytic_label': '«Газ»', 'category': 'fuel',
            'target_account': '90.02', 'valid_from': '2025-01-01',
            'valid_to': '2025-12-31'}
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=[mapping])['cards']}['cogs']['cost_structure']
        self.assertEqual(len(structure['unclassified']), 1)

    def test_profit_detail_separates_gross_sales_other_and_pretax_results(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.01', p1='Тепло', obk=100),
                day('2026-01-31', '90.03', p1='Тепло', obd=20),
                day('2026-01-31', '90.02', p1='Тепло', obd=70),
                day('2026-01-31', '90.07', obd=5),
                day('2026-01-31', '90.08', obd=3),
            ]),
            '91': account('91', [
                day('2026-01-31', '91.01', p1='Прочий доход', obk=4),
                day('2026-01-31', '91.02', p1='Прочий расход', obd=1),
            ]),
            '26': account('26', [day('2026-01-31', obd=3)]),
            '44': account('44', [day('2026-01-31', obd=5)]),
        })
        result = build_key_indicators(src)
        profit = {item['id']: item for item in result['cards']}['profit']
        self.assertEqual(profit['gross_profit'], 10)
        self.assertEqual(profit['profit_from_sales'], 2)
        self.assertEqual(profit['value'], 2)
        self.assertEqual(profit['other_income'], 4)
        self.assertEqual(profit['other_expense'], 1)
        self.assertEqual(profit['other_result'], 3)
        self.assertEqual(profit['profit_before_tax'], 5)
        self.assertEqual(result['objects']['totals']['profit'], 10)
        self.assertNotEqual(profit['value'], -6)

    def test_profit_does_not_assume_missing_recognized_expenses_are_zero(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.01', obk=100),
                day('2026-01-31', '90.03', obd=20),
                day('2026-01-31', '90.02', obd=70),
            ]),
            '91': account('91', [
                day('2026-01-31', '91.01', obk=4),
            ]),
        })
        profit = {item['id']: item for item in
                  build_key_indicators(src)['cards']}['profit']
        self.assertEqual(profit['value'], 10)
        self.assertIsNone(profit['profit_from_sales'])
        self.assertIsNone(profit['other_result'])
        self.assertIsNone(profit['profit_before_tax'])
        self.assertIn('Валовая прибыль', profit['title'])

    def test_profit_exposes_comparable_gross_profit_and_preserves_missing(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2025-01-31', '90.01', obk=80),
                day('2025-01-31', '90.03', obd=20),
                day('2025-01-31', '90.02', obd=40),
                day('2025-02-28', '90.01', obk=0),
                day('2025-02-28', '90.03', obd=0),
                day('2025-02-28', '90.02', obd=0),
                day('2026-01-31', '90.01', obk=100),
                day('2026-01-31', '90.03', obd=20),
                day('2026-01-31', '90.02', obd=50),
            ], period='за 01.01.2025 - 28.02.2026'),
        })
        profit = {item['id']: item for item in build_key_indicators(
            src, period='month', date_from='2026-01-31',
            date='2026-02-28')['cards']}['profit']

        self.assertEqual(profit['gross_profit'], 30)
        self.assertEqual(profit['previous_year_gross_profit'], 20)
        self.assertEqual(profit['gross_profit_delta'], 10)
        self.assertEqual(
            [row['current_gross_profit']
             for row in profit['monthly_comparison']],
            [30, None])
        self.assertEqual(
            [row['previous_year_gross_profit']
             for row in profit['monthly_comparison']],
            [20, 0])
        self.assertEqual(
            [row['delta'] for row in profit['monthly_comparison']],
            [10, None])

    def test_credit_detail_exposes_accounts_turnovers_and_analytics_without_double_count(self):
        src = build_indicator_source({
            '66': account('66', [
                day('2026-01-31', obd=6, obk=10, skd=0, skk=30),
                day('2026-01-31', p1='Банк А', obd=6, obk=10,
                    skd=0, skk=30),
            ], top=dict(snd=0, snk=20, obd=6, obk=10, skd=0, skk=30)),
            '67': account('67', [
                day('2026-01-31', obd=7, obk=12, skd=0, skk=70),
                day('2026-01-31', p1='Банк Б', obd=7, obk=12,
                    skd=0, skk=70),
            ], top=dict(snd=0, snk=65, obd=7, obk=12, skd=0, skk=70)),
        })
        credits = {item['id']: item for item in
                   build_key_indicators(src)['cards']}['credits']
        self.assertEqual(credits['value'], 100)
        self.assertTrue(credits['coverage']['complete'])
        self.assertEqual(
            {item['account']: item['value'] for item in credits['accounts']},
            {'66': 30, '67': 70})
        self.assertEqual(
            {item['account']: item['debit_turnover']
             for item in credits['accounts']},
            {'66': 6, '67': 7})
        self.assertEqual(len(credits['analytics']), 2)
        self.assertEqual(
            {item['label']: item['value'] for item in credits['analytics']},
            {'«Банк А»': 30, '«Банк Б»': 70})

    def test_collection_turnover_is_not_collection(self):
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31', obd=100, obk=90)])
        })
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertIsNone(card['details'][0]['value'])
        self.assertIsNone(card['turnover_ratio'])
        self.assertIsNone(card['turnover_series'][-1]['value'])
        self.assertIn('62.01', card['reason'])

    def test_collection_analytics_do_not_double_count_parent_rows(self):
        rows = [
            day('2026-01-31', '62.01', p1='Население', obd=100, obk=80),
            day('2026-01-31', '62.01.1', p1='Население', obd=100, obk=80),
        ]
        src = build_indicator_source({'62': account('62', rows)})
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertIsNone(card['turnover_ratio'])
        self.assertEqual(len(card['analytics']), 2)
        self.assertEqual(card['analytics'][0]['debit'], 100)
        self.assertEqual(card['analytics'][0]['credit'], 80)
        self.assertEqual(card['analytics'][0]['turnover_ratio'], 80)

    def test_empty_source_returns_ten_unavailable_cards(self):
        result = build_key_indicators(build_indicator_source({}))
        self.assertEqual(len(result['cards']), 10)
        self.assertTrue(all(x['value'] is None for x in result['cards']))

    def test_cash_dz_is_the_only_specialised_debtor_card(self):
        source = build_indicator_source({
            '51': account('51', [
                day('2026-01-30', skd=100),
                day('2026-01-31', skd=150),
            ]),
            '62': account('62', [
                day('2026-01-31', '62.01', skd=150),
                day('2026-01-31', '62.02', skk=30),
            ]),
        })
        result = build_key_indicators(source, 'month', '2026-01-31')
        self.assertEqual(
            [card['id'] for card in result['cards'][:3]],
            ['cash', 'cash_dz', 'revenue'])
        cards = {card['id']: card for card in result['cards']}
        cash = cards['cash']
        cash_dz = cards['cash_dz']
        self.assertEqual(cash_dz['title'], 'Дебиторская задолженность')
        self.assertEqual(cash_dz['value'], 150)
        self.assertEqual(cash_dz['consumer_series'][-1]['value'], 150)
        self.assertEqual(cash_dz['advances_series'], [])
        self.assertEqual(cash_dz['monthly_comparison'][-1]['current_dz'], 150)
        self.assertNotIn('ar', cards)
        self.assertIsNot(cash_dz['breakdown'], cash['breakdown'])

    def test_parent_child_dedup_and_missing_vat_profit_null(self):
        rows = [
            day('2026-01-31', '90.01', obk=100),
            day('2026-01-31', '90.01.1', obk=100),
            day('2026-01-31', '90.02.1', obd=60),
        ]
        result = build_key_indicators(build_indicator_source({'90': account('90', rows)}))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 100)
        self.assertIsNone(cards['profit']['value'])
        self.assertIsNone(result['objects']['totals']['profit'])

    def test_balance_observation_is_exact_date_only(self):
        raw = account('51', [day('2026-01-30'), day('2026-01-31')],
                      period='')
        raw['balance_observations'] = [
            dict(date='2026-01-30', skd=15, skk=0)
        ]
        src = build_indicator_source({'51': raw})
        exact = build_key_indicators(src, 'day', '2026-01-30')
        other = build_key_indicators(src, 'day', '2026-01-31')
        self.assertEqual({x['id']: x for x in exact['cards']}['cash']['value'], 15)
        self.assertIsNone({x['id']: x for x in other['cards']}['cash']['value'])

    def test_declared_half_year_is_not_partial_for_month(self):
        raw = account('08', [day('2026-06-29', obd=5)],
                      period='за 1 полугодие2026 г.')
        result = build_key_indicators(build_indicator_source({'08': raw}),
                                      'month', '2026-06-30')
        self.assertFalse(any('неполное' in x for x in result['period']['warnings']))

    def test_exact_company_mappings_reconcile_segments(self):
        rows = [
            day('2026-01-31', '90.01', p1='Тепло физлица', obk=120),
            day('2026-01-31', '90.03', p1='НДС тепло', obd=20),
        ]
        mappings = [
            {'account': '90.01',
             'analytic_key': revenue_analytic_key(['Тепло физлица']),
             'analytic_label': '«Тепло физлица»',
             'category': 'population'},
            {'account': '90.03',
             'analytic_key': revenue_analytic_key(['НДС тепло']),
             'analytic_label': '«НДС тепло»',
             'category': 'population'},
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=mappings)
        segments = result['revenue_segments']
        population = next(x for x in segments['categories']
                          if x['id'] == 'population')
        self.assertEqual(population['value'], 100)
        self.assertTrue(segments['reconciled'])
        self.assertEqual(segments['unclassified'], [])

    def test_revenue_activity_groups_use_matching_leaf_rows(self):
        rows = [
            {
                'date': '2026-09-15', 'account': '90.01.1',
                'label': 'Теплоснабжение',
                'hierarchy_path': ['90', '90.01', '90.01.1', '<...>',
                                   'Теплоснабжение'],
                'obk': 120, 'obk_known': True,
            },
            {
                'date': '2026-09-15', 'account': '90.03',
                'label': 'Теплоснабжение',
                'hierarchy_path': ['90', '90.03', '<...>',
                                   'Теплоснабжение'],
                'obd': 20, 'obd_known': True,
            },
            {
                'date': '2026-09-15', 'account': '90.01.1',
                'label': '<...>',
                'hierarchy_path': ['90', '90.01', '90.01.1', '<...>'],
                'obk': 120, 'obk_known': True,
            },
        ]
        result = _revenue_activity_groups(rows, 120, 20, 100)
        self.assertTrue(result['reconciled'])
        self.assertEqual(result['rows'], [{
            'id': revenue_analytic_key(['Теплоснабжение']),
            'label': 'Теплоснабжение',
            'gross': 120.0,
            'vat': 20.0,
            'value': 100.0,
            'basis': 'net',
        }])

    def test_revenue_activity_groups_reject_crossed_parent_paths(self):
        def row(account_name, parent, side, amount):
            account_path = '90.01.1' if account_name == '90.01.1' else '90.03'
            return {
                'date': '2026-09-15', 'account': account_name,
                'label': 'Теплоснабжение',
                'hierarchy_path': ['90', account_path, parent,
                                   'Теплоснабжение'],
                side: amount, f'{side}_known': True,
            }

        crossed = _revenue_activity_groups([
            row('90.01.1', 'Котельная А', 'obk', 120),
            row('90.03', 'Котельная Б', 'obd', 20),
        ], 120, 20, 100)
        self.assertFalse(crossed['reconciled'])
        self.assertEqual(crossed['rows'], [])

        matched = _revenue_activity_groups([
            row('90.01.1', 'Котельная А', 'obk', 120),
            row('90.03', 'Котельная А', 'obd', 20),
            row('90.01.1', 'Котельная Б', 'obk', 60),
            row('90.03', 'Котельная Б', 'obd', 10),
        ], 180, 30, 150)
        self.assertTrue(matched['reconciled'])
        self.assertEqual(matched['rows'][0]['gross'], 180)
        self.assertEqual(matched['rows'][0]['vat'], 30)
        self.assertEqual(matched['rows'][0]['value'], 150)

    def test_unmapped_and_conflicting_analytics_are_explicit(self):
        rows = [
            day('2026-01-31', '90.01', p1='Новая аналитика', obk=120),
            day('2026-01-31', '90.03', p1='Новая аналитика', obd=20),
        ]
        mappings = [
            {'account': '90.01',
             'analytic_key': revenue_analytic_key(['Новая аналитика']),
             'analytic_label': '«Новая аналитика»',
             'category': 'population'},
            {'account': '90.03',
             'analytic_key': revenue_analytic_key(['Новая аналитика']),
             'analytic_label': '«Новая аналитика»',
             'category': 'budget'},
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=mappings)
        self.assertFalse(result['revenue_segments']['reconciled'])
        self.assertEqual(len(result['revenue_segments']['conflicts']), 1)

    def test_full_analytic_coordinate_is_mapped_exactly(self):
        rows = [
            day('2026-01-31', '90.01', p1='Услуга', obk=100),
            dict(day('2026-01-31', '90.01', p1='Услуга', obk=200),
                 path2='Другой договор'),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=[{
                'account': '90.01',
                'analytic_key': revenue_analytic_key(['Услуга']),
                'analytic_label': '«Услуга»',
                'category': 'population',
            }])
        analytics = result['revenue_segments']['analytics']
        self.assertEqual(
            {item['analytic_label'] for item in analytics},
            {'«Услуга»', '«Услуга» / «Другой договор»'})
        self.assertEqual(len(result['revenue_segments']['unclassified']), 1)

    def test_unknown_revenue_amount_remains_null(self):
        rows = [day('2026-01-31', '90.01', p1='Услуга', obk=None)]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=[{
                'account': '90.01',
                'analytic_key': revenue_analytic_key(['Услуга']),
                'analytic_label': '«Услуга»',
                'category': 'population',
            }])
        revenue = next(card for card in result['cards']
                       if card['id'] == 'revenue')
        population = next(row for row in result['revenue_segments']['categories']
                          if row['id'] == 'population')
        self.assertIsNone(revenue['value'])
        self.assertIsNone(population['value'])
        self.assertEqual(len(result['revenue_segments']['unknown_amounts']), 1)
        self.assertIsNone(result['objects']['rows'][0]['revenue'])

    def test_structured_analytic_key_has_no_display_delimiter_collision(self):
        self.assertNotEqual(
            revenue_analytic_key(['A › B']),
            revenue_analytic_key(['A', 'B']))

    def test_cash_does_not_compare_when_account_set_changes(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ]),
            '55': account('55', [day('2026-02-28', skd=5)], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-02-28')['cards']}['cash']
        self.assertEqual(cash['date'], '2026-02-28')
        self.assertEqual(cash['accounts'], ['51', '55'])
        self.assertEqual(cash['missing_accounts'], ['50', '52'])
        self.assertEqual(cash['coverage'], 'partial')
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-01-31')

    def test_cash_same_partial_account_set_is_comparable(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-02-28')['cards']}['cash']
        self.assertEqual(cash['coverage'], 'partial')
        self.assertEqual(cash['comparison']['accounts'], ['51'])
        self.assertTrue(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['value'], 10)
        self.assertEqual(cash['delta']['coverage'], 'partial')
        self.assertEqual(cash['delta']['accounts'], ['51'])
        self.assertEqual(cash['delta']['previous_date'], '2026-01-31')
        self.assertEqual(cash['delta']['date'], '2026-02-28')
        self.assertIn('2026-01-31', cash['delta']['label'])
        self.assertIn('2026-02-28', cash['delta']['label'])

    def test_cash_zero_balances_are_complete_and_available(self):
        src = build_indicator_source({
            account_number: account(
                account_number, [day('2026-01-31', skd=0, skk=0)])
            for account_number in ('50', '51', '52', '55')
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-31')['cards']}['cash']
        self.assertEqual(cash['value'], 0)
        self.assertEqual(cash['accounts'], ['50', '51', '52', '55'])
        self.assertEqual(cash['missing_accounts'], [])
        self.assertEqual(cash['coverage'], 'complete')
        self.assertFalse(cash['series'][0]['comparison']['comparable'])

    def test_cash_series_does_not_bridge_a_missing_daily_point(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-03-31', skd=30),
            ]),
        })
        result = build_key_indicators(
            src, 'month', '2026-03-31', date_from='2026-01-31')
        series = {item['date']: item for item in
                  next(card for card in result['cards']
                       if card['id'] == 'cash')['series']}
        self.assertFalse(series['2026-01-31']['comparison']['comparable'])
        self.assertFalse(series['2026-03-31']['comparison']['comparable'])
        self.assertEqual(
            series['2026-03-31']['comparison']['previous_date'],
            '2026-03-30')

    def test_cash_day_series_has_one_exact_date_point(self):
        src = build_indicator_source({
            '51': account('51', [day('2026-01-31', skd=15)]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-31')['cards']}['cash']
        self.assertEqual(len(cash['series']), 1)
        self.assertEqual(cash['series'][0]['date'], '2026-01-31')
        self.assertEqual(cash['series'][0]['value'], 15)
        self.assertIsNone(cash['series'][0]['comparison']['previous_date'])
        self.assertFalse(cash['series'][0]['comparison']['comparable'])

    def test_cash_chart_keeps_exact_report_boundary_balance(self):
        raw = account('51', [])
        raw.update({
            'report_start': '2026-01-01',
            'report_end': '2026-01-31',
            'top': {
                'snd': 0, 'snk': 0, 'obd': 25, 'obk': 10,
                'skd': 15, 'skk': 0,
            },
        })
        cash = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'51': raw}), 'month', '2026-01-31')['cards']}['cash']

        available = [
            point for point in cash['series'] if point['value'] is not None]
        self.assertEqual(len(available), 1)
        self.assertEqual(available[0]['date'], '2026-01-31')
        self.assertEqual(available[0]['value'], 15)
        self.assertEqual(available[0]['sources'], {'51': 'report_boundary'})

    def test_day_period_accepts_custom_dates_inside_source_range(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', obd=10, skd=10),
                day('2026-03-31', obd=20, skd=30),
            ])
        })
        result = build_key_indicators(
            src, 'day', '2026-03-01', date_from='2026-02-01')

        self.assertEqual(result['period']['date_from'], '2026-02-01')
        self.assertEqual(result['period']['date'], '2026-03-01')
        self.assertEqual(result['period']['start'], '2026-02-01')
        self.assertEqual(result['period']['end'], '2026-03-01')
        self.assertEqual(result['period']['label'], '01.02.2026 — 01.03.2026')

    def test_cash_series_has_independent_cumulative_debit_and_credit(self):
        src = build_indicator_source({
            '50': account('50', [
                day('2026-01-01', snd=10, snk=2, obd=3, obk=4,
                    skd=9, skk=2),
                day('2026-01-02', snd=9, snk=2, obd=5, obk=1,
                    skd=13, skk=2),
            ]),
            '51': account('51', [
                day('2026-01-01', snd=20, snk=7, obd=2, obk=3,
                    skd=12, skk=0),
                day('2026-01-02', snd=12, snk=0, obd=0, obk=6,
                    skd=6, skk=0),
            ]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-02', date_from='2026-01-01')['cards']}['cash']

        self.assertEqual(
            [(point['debit_value'], point['credit_value'])
             for point in cash['series']],
            [(35, 16), (40, 23)])
        self.assertEqual(
            cash['series'][0]['cumulative_accounts'], ['50', '51'])
        self.assertEqual(cash['series'][0]['cumulative_coverage'], 'partial')

    def test_cash_previous_year_series_uses_same_shifted_period(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2025-01-01', snd=40, snk=5, obd=10, obk=2,
                    skd=43, skk=0),
                day('2025-01-02', snd=43, snk=0, obd=7, obk=3,
                    skd=47, skk=0),
                day('2026-01-01', snd=100, snk=0, obd=20, obk=5,
                    skd=115, skk=0),
                day('2026-01-02', snd=115, snk=0, obd=10, obk=2,
                    skd=123, skk=0),
            ]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-02', date_from='2026-01-01')['cards']}['cash']

        self.assertEqual(
            [point['date'] for point in cash['previous_year_series']],
            ['2025-01-01', '2025-01-02'])
        self.assertEqual(
            [(point['debit_value'], point['credit_value'])
             for point in cash['previous_year_series']],
            [(50, 7), (57, 10)])
        self.assertEqual(
            [point['value'] for point in cash['previous_year_series']],
            [43, 47])
        self.assertEqual(cash['previous_year_period'], {
            'start': '2025-01-01',
            'end': '2025-01-02',
        })

    def test_cash_monthly_table_uses_month_turnovers_and_end_balances(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2025-01-31', snd=70, snk=0, obd=4, obk=1,
                    skd=73, skk=0),
                day('2025-02-01', snd=73, snk=0, obd=6, obk=2,
                    skd=77, skk=0),
                day('2026-01-31', snd=90, snk=0, obd=10, obk=2,
                    skd=98, skk=0),
                day('2026-02-01', snd=98, snk=0, obd=7, obk=1,
                    skd=104, skk=0),
            ]),
        })
        cash = next(card for card in build_key_indicators(
            src, 'day', '2026-02-01',
            date_from='2026-01-31')['cards'] if card['id'] == 'cash')
        january, february = cash['monthly_comparison']
        self.assertEqual(
            (january['date'], january['value'],
             january['previous_year_date'],
             january['previous_year_value'], january['delta']),
            ('2026-01-31', 98, '2025-01-31', 73, 25))
        self.assertEqual(
            (january['debit_turnover'], january['credit_turnover'],
             january['previous_year_debit_turnover'],
             january['previous_year_credit_turnover']),
            (10, 2, 4, 1))
        self.assertEqual(
            (february['value'], february['previous_year_value'],
             february['debit_turnover'], february['credit_turnover'],
             february['previous_year_debit_turnover'],
             february['previous_year_credit_turnover']),
            (104, 77, 7, 1, 6, 2))

    def test_cash_cumulative_prefers_exact_db_turnovers_over_monthly_row(self):
        raw = account('51', [
            day('2026-01-01', snd=10, snk=1, obd=0, obk=0,
                skd=10, skk=1),
            day('2026-01-02', snd=10, snk=1, obd=0, obk=0,
                skd=10, skk=1),
        ])
        raw['balance_observations'] = [
            dict(date='2026-01-01', snd=10, snk=1, obd=5, obk=2,
                 skd=13, skk=1, source_type='reconciled_turnovers'),
            dict(date='2026-01-02', snd=13, snk=1, obd=7, obk=3,
                 skd=17, skk=1, source_type='reconciled_turnovers'),
        ]
        cash = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'51': raw}), 'day', '2026-01-02',
            date_from='2026-01-01')['cards']}['cash']

        self.assertEqual(
            [(point['debit_value'], point['credit_value'])
             for point in cash['series']],
            [(15, 3), (22, 6)])
        self.assertEqual(
            cash['series'][0]['cumulative_sources'],
            {'51': 'reconciled_turnovers'})

    def test_cash_cumulative_series_stops_at_unknown_or_missing_day(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', snd=10, snk=0, obd=0, obk=0,
                    skd=10, skk=0),
                day('2026-01-03', snd=10, snk=0, obd=5, obk=0,
                    skd=15, skk=0),
            ]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-03', date_from='2026-01-01')['cards']}['cash']

        self.assertEqual(
            [point['debit_value'] for point in cash['series']],
            [10, None, None])
        self.assertEqual(
            [point['credit_value'] for point in cash['series']],
            [0, None, None])
        self.assertEqual(cash['series'][1]['cumulative_coverage'], 'missing')
        self.assertIsNone(
            cash['monthly_comparison'][0]['debit_turnover'])
        self.assertIsNone(
            cash['monthly_comparison'][0]['credit_turnover'])

    def test_cash_cumulative_series_restarts_at_selected_period(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', snd=100, snk=0, obd=10, obk=30,
                    skd=80, skk=0),
                day('2026-01-02', snd=80, snk=0, obd=5, obk=10,
                    skd=75, skk=0),
            ]),
        })
        full = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-02', date_from='2026-01-01')['cards']}['cash']
        shortened = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-02', date_from='2026-01-02')['cards']}['cash']

        self.assertEqual(
            (full['series'][-1]['debit_value'],
             full['series'][-1]['credit_value']),
            (115, 40))
        self.assertEqual(
            (shortened['series'][-1]['debit_value'],
             shortened['series'][-1]['credit_value']),
            (85, 10))

    def test_cash_day_delta_does_not_bridge_an_omitted_calendar_day(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', skd=10),
                day('2026-01-03', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-03')['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-01-02')
        self.assertEqual(cash['comparison']['date'], '2026-01-03')
        self.assertIn('слота', cash['comparison']['reason'])

    def test_cash_month_delta_requires_the_prior_calendar_month_end(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-03-31', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-03-31')['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-02-28')
        self.assertEqual(cash['comparison']['date'], '2026-03-31')
        self.assertIn('слота', cash['comparison']['reason'])

    def test_cash_quarter_delta_requires_the_prior_quarter_end(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-03-31', skd=10),
                day('2026-09-30', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'quarter', '2026-09-30')['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-06-30')
        self.assertEqual(cash['comparison']['date'], '2026-09-30')
        self.assertIn('слота', cash['comparison']['reason'])

    def test_cash_series_suppresses_account_change_and_compares_same_partial_set(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ], period=''),
            '55': account('55', [day('2026-02-28', skd=5)], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'quarter', '2026-02-28')['cards']}['cash']
        points = {item['date']: item for item in cash['series']
                  if item['date'] in ('2026-01-31', '2026-02-28')}
        self.assertFalse(points['2026-02-28']['comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['comparison']['reason'],
                         'Нет точного остатка')
        self.assertFalse(points['2026-02-28']['interval_comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['interval_comparison']['reason'],
                         'Изменился состав счетов')

        same_partial = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            same_partial, 'quarter', '2026-02-28')['cards']}['cash']
        points = {item['date']: item for item in cash['series']
                  if item['date'] in ('2026-01-31', '2026-02-28')}
        self.assertFalse(points['2026-02-28']['comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['comparison']['reason'],
                         'Нет точного остатка')
        self.assertTrue(points['2026-02-28']['interval_comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['interval_comparison']['value'], 10)
        self.assertEqual(points['2026-02-28']['coverage'], 'partial')

    def test_cash_month_series_uses_daily_slots_and_metadata(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', skd=10),
                day('2026-01-03', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-01-03')['cards']}['cash']
        self.assertEqual(
            [point['date'] for point in cash['series']],
            ['2026-01-01', '2026-01-02', '2026-01-03'])
        self.assertEqual(
            [point['value'] for point in cash['series']], [10, None, 30])
        self.assertEqual(cash['series'][0]['accounts'], ['51'])
        self.assertEqual(cash['series'][1]['coverage'], 'missing')
        self.assertFalse(cash['series'][2]['comparison']['comparable'])

    def test_cash_daily_series_keeps_period_boundary_only_at_endpoint(self):
        src = build_indicator_source({
            '51': account(
                '51', [], top=dict(snd=0, snk=0, obd=0, obk=0, skd=500, skk=0),
                period='за январь 2026 г.'),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-01-31')['cards']}['cash']
        # The authentic report boundary is one real chart point. It must not
        # be copied into any of the preceding daily slots.
        self.assertEqual(cash['value'], 500)
        self.assertTrue(all(
            point['value'] is None for point in cash['series'][:-1]))
        self.assertEqual(cash['series'][-1]['value'], 500)
        self.assertEqual(cash['series'][-1]['sources'],
                         {'51': 'report_boundary'})
        self.assertEqual(cash['series'][-1]['granularity'], 'day')

    def test_cash_daily_series_uses_exact_rows_inclusive_and_keeps_zero(self):
        rows = [
            day('2026-01-01', skd=10),
            day('2026-01-03', skd=30),
        ]
        src = build_indicator_source({
            '51': account('51', rows, period=''),
            '50': account('50', [day('2026-01-01', skd=0, skk=0),
                                 day('2026-01-03', skd=0, skk=0)], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-01-03')['cards']}['cash']
        points = {point['date']: point for point in cash['series']}
        self.assertEqual(
            [point['date'] for point in cash['series']],
            ['2026-01-01', '2026-01-02', '2026-01-03'])
        self.assertEqual(points['2026-01-01']['value'], 10)
        self.assertIsNone(points['2026-01-02']['value'])
        self.assertEqual(points['2026-01-03']['value'], 30)
        self.assertEqual(points['2026-01-01']['accounts'], ['50', '51'])

    def test_cash_daily_series_reports_four_account_coverage_per_date(self):
        src = build_indicator_source({
            '50': account('50', [day('2026-01-01', skd=10),
                                 day('2026-01-02', skd=11)], period=''),
            '51': account('51', [day('2026-01-01', skd=20),
                                 day('2026-01-02', skd=21)], period=''),
            '52': account('52', [day('2026-01-01', skd=30)], period=''),
            '55': account('55', [day('2026-01-01', skd=40)], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-01-02')['cards']}['cash']
        points = {point['date']: point for point in cash['series']}

        self.assertEqual(points['2026-01-01']['value'], 100)
        self.assertEqual(points['2026-01-01']['accounts'],
                         ['50', '51', '52', '55'])
        self.assertEqual(points['2026-01-01']['missing_accounts'], [])
        self.assertEqual(points['2026-01-01']['coverage'], 'complete')
        self.assertEqual(points['2026-01-02']['value'], 32)
        self.assertEqual(points['2026-01-02']['accounts'], ['50', '51'])
        self.assertEqual(points['2026-01-02']['missing_accounts'], ['52', '55'])
        self.assertEqual(points['2026-01-02']['coverage'], 'partial')

    def test_cash_daily_series_preserves_account_provenance_without_filling_gaps(self):
        src = build_indicator_source({
            '50': account('50', [], period=''),
            '51': account('51', [], period=''),
        })
        src['accounts']['50']['balance_observations'] = [{
            'date': '2026-01-01', 'skd': 0, 'skk': 0,
            'source_type': 'confirmed_zero',
        }]
        src['accounts']['51']['balance_observations'] = [{
            'date': '2026-01-02', 'skd': 15, 'skk': 0,
            'source_type': 'reconciled_turnovers',
        }]
        src['dates'] = ['2026-01-01', '2026-01-02']

        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-01-02')['cards']}['cash']
        points = {point['date']: point for point in cash['series']}
        account_50 = next(
            item for item in cash['breakdown'] if item['label'] == 'Счёт 50')

        self.assertEqual(points['2026-01-01']['value'], 0)
        self.assertEqual(points['2026-01-01']['sources']['50'], 'confirmed_zero')
        self.assertEqual(
            points['2026-01-02']['sources']['51'], 'reconciled_turnovers')
        self.assertIsNone(account_50['series'][1]['value'])
        self.assertIsNone(account_50['series'][1]['source_type'])

    def test_cash_quarter_series_uses_daily_slots_without_spreading_balances(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-03-31', skd=30),
            ], period='за 1 квартал 2026 г.'),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'quarter', '2026-03-31')['cards']}['cash']
        self.assertEqual(len(cash['series']), 90)
        series = {point['date']: point for point in cash['series']}
        self.assertEqual(series['2026-01-31']['value'], 10)
        self.assertIsNone(series['2026-02-28']['value'])
        self.assertEqual(series['2026-03-31']['value'], 30)
        self.assertEqual(series['2026-01-30']['coverage'], 'missing')
        self.assertEqual(series['2026-02-28']['coverage'], 'missing')
        self.assertEqual(series['2026-03-30']['coverage'], 'missing')
        self.assertFalse(series['2026-03-31']['comparison']['comparable'])

    def test_cash_multi_quarter_range_has_unavailable_card_comparison(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-03-31', skd=10),
                day('2026-06-30', skd=20),
                day('2026-09-30', skd=30),
            ], period='за 3 квартал 2026 г.'),
        })
        result = build_key_indicators(
            src, 'quarter', '2026-09-30', date_from='2026-03-31')
        cash = {item['id']: item for item in result['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertIsNone(cash['comparison']['previous_date'])
        self.assertIn('диапазона', cash['comparison']['reason'])
        self.assertEqual(len(cash['series']), 273)
        self.assertEqual(cash['series'][0]['date'], '2026-01-01')
        self.assertEqual(cash['series'][-1]['date'], '2026-09-30')
class CashSalaryKeyIndicatorTests(unittest.TestCase):
    def test_absent_is_null_but_explicit_zero_is_zero(self):
        data = {'90': account('90', [day('2026-01-31', '90.01', obk=0),
                                     day('2026-01-31', '90.03', obd=0),
                                     day('2026-01-31', '90.02', obd=0)])}
        result = build_key_indicators(build_indicator_source(data))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 0)
        self.assertIsNone(cards['cash']['value'])

    def test_period_filter_and_missing_vat_is_gross(self):
        rows = [day('2026-01-31', '90.01', obk=100),
                day('2026-01-31', '90.02', obd=70),
                day('2026-02-28', '90.01', obk=900),
                day('2026-02-28', '90.02', obd=800)]
        result = build_key_indicators(build_indicator_source(
            {'90': account('90', rows, period='за 1 квартал 2026 г.')}),
            date='2026-01-31')
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 100)
        self.assertIn('брутто', cards['revenue']['formula'])

    def test_month_range_sums_from_selected_start_through_end(self):
        rows = [
            day('2026-08-31', '90.01', obk=10),
            day('2026-09-30', '90.01', obk=20),
            day('2026-10-31', '90.01', obk=30),
        ]
        result = build_key_indicators(
            build_indicator_source({
                '90': account('90', rows, period='за 3 квартал 2026 г.'),
            }),
            period='month', date='2026-10-31', date_from='2026-08-31')
        revenue = {card['id']: card for card in result['cards']}['revenue']
        self.assertEqual(revenue['value'], 60)
        self.assertEqual(result['period']['start'], '2026-08-01')
        self.assertEqual(result['period']['end'], '2026-10-31')
        self.assertEqual(result['period']['date_from'], '2026-08-31')
        self.assertIsNone(revenue['delta'])

    def test_day_range_sums_selected_dates_inclusively(self):
        rows = [
            day('2026-10-01', '90.01', obk=10),
            day('2026-10-02', '90.01', obk=20),
            day('2026-10-03', '90.01', obk=30),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            period='day', date='2026-10-03', date_from='2026-10-01')
        revenue = {card['id']: card for card in result['cards']}['revenue']
        self.assertEqual(revenue['value'], 60)
        self.assertEqual(result['period']['start'], '2026-10-01')
        self.assertEqual(result['period']['end'], '2026-10-03')
        self.assertEqual(result['period']['date_from'], '2026-10-01')
        self.assertIsNone(revenue['delta'])
        self.assertEqual([p['value'] for p in revenue['series']], [10, 20, 30])

    def test_quarter_range_charts_only_selected_months(self):
        rows = [day('2026-03-31', obd=100),
                day('2026-04-30', obd=10), day('2026-05-31', obd=20),
                day('2026-06-30', obd=30), day('2026-08-31', obd=40),
                day('2026-09-30', obd=50), day('2026-12-31', obd=900)]
        source = build_indicator_source({'08': account('08', rows)})
        result = build_key_indicators(source, period='quarter',
                                      date_from='2026-06-30', date='2026-09-30')
        card = next(c for c in result['cards'] if c['id'] == 'capex')
        self.assertEqual(card['value'], 150)
        self.assertEqual(result['period']['start'], '2026-04-01')
        self.assertEqual(result['period']['end'], '2026-09-30')
        self.assertEqual(result['period']['series_kind'], 'day')
        self.assertEqual(len(card['series']), 183)
        series = {point['date']: point['value'] for point in card['series']}
        self.assertEqual(series['2026-04-30'], 10)
        self.assertEqual(series['2026-05-31'], 20)
        self.assertEqual(series['2026-06-30'], 30)
        self.assertEqual(series['2026-08-31'], 40)
        self.assertEqual(series['2026-09-30'], 50)
        self.assertIsNone(series['2026-07-31'])
        self.assertIsNone(card['delta'])
        with self.assertRaises(ValueError):
            build_key_indicators(source, period='quarter',
                                 date_from='2026-09-30', date='2026-06-30')

    def test_daily_chart_preserves_gaps_and_exact_balances(self):
        rows = [day('2026-10-01', obd=10, skd=10, skk=0),
                day('2026-10-03', obd=30, skd=40, skk=0),
                day('2026-10-05', obd=500, skd=540, skk=0)]
        result = build_key_indicators(
            build_indicator_source({'08': account('08', rows)}),
            period='day', date_from='2026-10-01', date='2026-10-03')
        card = next(c for c in result['cards'] if c['id'] == 'capex')
        self.assertEqual([p['value'] for p in card['series']], [10, None, 30])
        self.assertEqual([p['value'] for p in card['balance_series']], [10, None, 40])
        self.assertEqual(card['closing_balance'], 40)

    def test_balance_only_at_original_report_boundary(self):
        top = dict(snd=1, snk=0, obd=0, obk=0, skd=25, skk=0)
        src = build_indicator_source({
            '51': account('51', [day('2026-01-31'), day('2026-02-28')],
                          top=top, period='за 1 квартал 2026 г.')
        })
        jan = build_key_indicators(src, date='2026-01-31')
        self.assertIsNone({x['id']: x for x in jan['cards']}['cash']['value'])

    def test_objects_reconcile_and_no_parent_child_double_count(self):
        rows = [
            day('2026-01-31', '90.01', p1='Услуга', obk=100),
            day('2026-01-31', '90.02', p1='Услуга', obd=60),
            day('2026-01-31', '90.03', p1='Услуга', obd=20),
        ]
        result = build_key_indicators(build_indicator_source({'90': account('90', rows)}))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(result['objects']['totals']['revenue'], cards['revenue']['value'])
        self.assertEqual(result['objects']['totals']['profit'], cards['profit']['value'])

    def test_salary_is_not_called_overdue(self):
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=50)
        result = build_key_indicators(build_indicator_source({
            '70': account('70', [day('2026-01-31')], top=top)
        }))
        salary = {x['id']: x for x in result['cards']}['salary']
        self.assertEqual(salary['value'], 50)
        self.assertIn('не подтверждает просрочку', salary['notes'][0])

    def test_salary_detail_keeps_balances_turnovers_and_analytics_separate(self):
        src = build_indicator_source({
            '70': account('70', [
                day('2026-01-31', obd=80, obk=100, skd=0, skk=20),
                day('2026-01-31', p1='Подразделение А',
                    obd=80, obk=100, skd=0, skk=20),
            ], top=dict(snd=0, snk=0, obd=80, obk=100, skd=0, skk=20)),
            '69': account('69', [
                day('2026-01-31', obd=24, obk=30, skd=0, skk=6),
                day('2026-01-31', p1='Взносы А',
                    obd=24, obk=30, skd=0, skk=6),
            ], top=dict(snd=0, snk=0, obd=24, obk=30, skd=0, skk=6)),
        })
        salary = {item['id']: item for item in
                  build_key_indicators(src)['cards']}['salary']
        self.assertEqual(salary['value'], 20)
        self.assertEqual(salary['credit_turnover'], 100)
        self.assertEqual(salary['debit_turnover'], 80)
        self.assertEqual(len(salary['analytics']), 1)
        self.assertEqual(salary['insurance']['credit_balance'], 6)
        self.assertEqual(salary['insurance']['credit_turnover'], 30)
        self.assertEqual(salary['insurance']['turnover_ratio'], 30)
        self.assertEqual(len(salary['insurance']['analytics']), 1)
        self.assertIn('не как подтверждённый ФОТ', salary['notes'][1])

    def test_missing_insurance_account_is_not_reported_as_zero(self):
        src = build_indicator_source({
            '70': account('70', [day('2026-01-31', obd=8, obk=10)],
                          top=dict(snd=0, snk=0, obd=8, obk=10,
                                   skd=0, skk=2)),
        })
        salary = {item['id']: item for item in
                  build_key_indicators(src)['cards']}['salary']
        self.assertIsNone(salary['insurance']['credit_balance'])
        self.assertIsNone(salary['insurance']['credit_turnover'])
        self.assertIsNone(salary['insurance']['turnover_ratio'])

    def test_capex_detail_separates_turnover_balance_and_analytics(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-01-31', obd=30, obk=5, skd=35, skk=0),
                day('2026-01-31', p1='Объект А',
                    obd=30, obk=5, skd=35, skk=0),
            ], top=dict(snd=10, snk=0, obd=30, obk=5, skd=35, skk=0)),
        })
        capex = {item['id']: item for item in
                 build_key_indicators(src)['cards']}['capex']
        self.assertEqual(capex['value'], 30)
        self.assertEqual(capex['debit_turnover'], 30)
        self.assertEqual(capex['credit_turnover'], 5)
        self.assertEqual(capex['opening_balance'], 10)
        self.assertEqual(capex['closing_balance'], 35)
        self.assertEqual(len(capex['analytics']), 1)
        self.assertEqual(capex['analytics'][0]['value'], 35)
        self.assertEqual(capex['analytics'][0]['turnover_share'], 100)
        self.assertEqual(capex['analytics'][0]['balance_share'], 100)

    def test_missing_capex_balance_is_not_reported_as_zero(self):
        src = build_indicator_source({
            '08': account('08', [day('2026-01-31', obd=12, obk=2)],
                          top=None),
        })
        capex = {item['id']: item for item in
                 build_key_indicators(src)['cards']}['capex']
        self.assertEqual(capex['value'], 12)
        self.assertEqual(capex['opening_balance'], 0)
        self.assertEqual(capex['closing_balance'], 0)

    def test_capex_comparison_uses_two_loaded_quarters(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-03-31', obd=10),
                day('2026-06-30', obd=25),
            ], period='за 1 полугодие 2026 г.'),
        })
        capex = {item['id']: item for item in build_key_indicators(
            src, period='quarter', date='2026-06-30')['cards']}['capex']
        self.assertEqual(capex['value'], 25)
        self.assertEqual(capex['delta']['value'], 15)

    def test_capex_monthly_table_adds_quarters_half_and_object_details(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-01-15', p1='08.04.1', p3='Объект А',
                    obd=100, obk=80),
                day('2026-02-12', p1='08.04.1', p3='Объект А',
                    obd=200, obk=200),
                day('2026-03-03', p1='08.04.2', p3='Объект Б',
                    obd=300, obk=250),
                day('2026-04-10', p1='08.04.2', p3='Объект Б',
                    obd=400, obk=500),
                day('2026-05-10', p1='08.04.2', p3='Объект Б',
                    obd=500, obk=500),
                day('2026-06-10', p1='08.04.2', p3='Объект Б',
                    obd=600, obk=600),
            ], top=dict(snd=0, snk=0, obd=2100, obk=2130,
                        skd=2100, skk=0),
               period='01.01.2026 - 30.06.2026'),
        })
        capex = {item['id']: item for item in build_key_indicators(
            src, period='year', date='2026-06-30')['cards']}['capex']
        rows = capex['monthly_comparison']
        self.assertEqual(
            [row['kind'] for row in rows],
            ['month', 'month', 'month', 'quarter', 'month', 'month',
             'month', 'quarter', 'half'])
        self.assertEqual(rows[3]['label'], '1 квартал')
        self.assertEqual(rows[3]['invested'], 600)
        self.assertEqual(rows[3]['developed'], 530)
        self.assertEqual(rows[3]['not_developed'], 70)
        self.assertEqual(rows[-1]['label'], '1 полугодие')
        self.assertEqual(rows[-1]['invested'], 2100)
        self.assertEqual(rows[-1]['developed'], 2130)
        self.assertEqual(rows[-1]['not_developed'], -30)
        self.assertEqual(rows[0]['details'][0]['object'], 'Объект А')
        self.assertEqual(rows[0]['details'][0]['investment_dates'],
                         ['2026-01-15'])
        self.assertEqual(rows[0]['details'][0]['development_dates'],
                         ['2026-01-15'])

    def test_capex_monthly_balance_reconciles_distinct_debit_and_credit(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-01-15', obd=100, obk=80),
                day('2026-02-12', obd=20, obk=15),
            ], top=dict(snd=10, snk=0, obd=120, obk=95,
                        skd=35, skk=0),
               period='01.01.2026 - 28.02.2026'),
        })
        card = {item['id']: item for item in build_key_indicators(
            src, period='year', date='2026-02-28')['cards']}['capex']
        self.assertEqual(card['debit_turnover'], 120)
        self.assertEqual(card['credit_turnover'], 95)
        rows = card['monthly_comparison']
        self.assertEqual([row['invested'] for row in rows], [100, 20])
        self.assertEqual([row['developed'] for row in rows], [80, 15])
        self.assertEqual([row['not_developed_total'] for row in rows],
                         [30, 35])

    def test_capex_chart_keeps_monthly_lines_separate_from_period_totals(self):
        top = dict(snd=10, snk=0, obd=150, obk=125, skd=35, skk=0)
        raw = account('08', [
            day('2025-01-31', obd=7),
            day('2025-02-28', obd=9),
            day('2026-01-31', obd=100, obk=80),
            day('2026-02-28', obd=20, obk=15),
        ], top=top, period='01.01.2026 - 28.02.2026')
        raw['balance_reports'] = [{
            'period': '01.01.2026 - 28.02.2026',
            'source_file': 'half.xlsx', 'top': top,
            'rows': [dict(lvl=0, label='08', p0='08', p1='', p2='', p3='',
                          **top)],
        }]
        src = build_indicator_source({'08': raw})
        result = build_key_indicators(
            src, period='month', date='2026-02-28',
            date_from='2026-01-31')
        card = next(item for item in result['cards'] if item['id'] == 'capex')
        self.assertEqual(result['period']['series_kind'], 'period')
        self.assertEqual(card['debit_turnover'], 150)
        self.assertEqual(card['credit_turnover'], 125)
        chart = card['chart_monthly_series']
        self.assertEqual(
            [point['value'] for point in chart['debit']], [100, 20])
        self.assertEqual(
            [point['value'] for point in chart['credit']], [80, 15])
        self.assertEqual(
            [point['value'] for point in chart['apg']], [7, 9])
        self.assertEqual(
            [point['source_date'] for point in chart['apg']],
            ['2025-01-31', '2025-02-28'])
        reconciliation = card['chart_monthly_reconciliation']
        self.assertEqual(reconciliation['debit']['difference'], 30)
        self.assertEqual(reconciliation['credit']['difference'], 30)
        self.assertEqual(card['series'][0]['value'], 150)

    def test_capex_does_not_infer_balance_from_conflicting_report(self):
        src = build_indicator_source({
            '08': account('08', [
                day('2026-01-15', obd=100, obk=80),
                day('2026-02-12', obd=20, obk=15),
            ], top=dict(snd=10, snk=0, obd=120, obk=95,
                        skd=50, skk=0),
               period='01.01.2026 - 28.02.2026'),
        })
        rows = {item['id']: item for item in build_key_indicators(
            src, period='year', date='2026-02-28')['cards']}[
                'capex']['monthly_comparison']
        self.assertIsNone(rows[0]['not_developed_total'])
        self.assertEqual(rows[1]['not_developed_total'], 50)

    def test_false_day_balance_and_cross_period_leakage(self):
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=100, skk=0)
        src = build_indicator_source({
            '51': account('51', [day('2026-01-30'), day('2026-01-31')],
                          top=top)
        })
        result = build_key_indicators(src, 'day', '2026-01-30')
        self.assertIsNone({x['id']: x for x in result['cards']}['cash']['value'])

    def test_partial_period_warning_and_invalid_inputs(self):
        src = build_indicator_source({'08': account('08', [day('2026-03-15', obd=4)],
                                                     period='за 1 квартал 2026 г.')})
        result = build_key_indicators(src, 'year')
        self.assertTrue(result['period']['warnings'])
        with self.assertRaises(ValueError):
            build_key_indicators(src, 'week')
        with self.assertRaises(ValueError):
            build_key_indicators(src, 'month', '2025-01-01')

    def test_ar_known_scope_and_other_76_unmapped(self):
        row = dict(lvl=1, label='62.01', p0='62', p1='62.01', p2='', p3='',
                   snd=0, snk=0, obd=0, obk=0, skd=12, skk=0)
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')], [row]),
            '76': account('76', [day('2026-01-31')]),
        })
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['cash_dz']
        self.assertEqual(ar['value'], 12)
        self.assertIn('76', ar['notes'][0])

    def test_ar_detail_exposes_opening_and_analytic_balance(self):
        report_row = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                          p2='', p3='', snd=80, snk=0, obd=20, obk=0,
                          skd=100, skk=0)
        analytic = day('2026-01-31', '62.01', p1='Покупатель А',
                       obd=20, obk=0, snd=80, snk=0, skd=100, skk=0)
        src = build_indicator_source({
            '62': account('62', [analytic], [report_row])
        })
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['cash_dz']
        self.assertEqual(ar['value'], 100)
        self.assertEqual(ar['opening_value'], 80)
        self.assertEqual(len(ar['analytics']), 1)
        self.assertEqual(ar['analytics'][0]['value'], 100)
        self.assertIn('Покупатель А', ar['analytics'][0]['label'])

    def test_ap_detail_exposes_components_opening_turnover_and_analytics(self):
        report_row = dict(lvl=1, label='60.01', p0='60', p1='60.01',
                          p2='', p3='', snd=0, snk=80, obd=0, obk=20,
                          skd=0, skk=100)
        supplier = day('2026-01-31', '60.01', p1='Поставщик А',
                       obd=0, obk=20, snd=0, snk=80, skd=0, skk=100)
        src = build_indicator_source({
            '60': account('60', [supplier], [report_row]),
            '68': account('68', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=12)),
            '69': account('69', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=3)),
            '70': account('70', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=5)),
        })
        ap = {x['id']: x for x in build_key_indicators(src)['cards']}['ap']
        self.assertEqual(ap['value'], 100)
        self.assertEqual(ap['opening_value'], 80)
        self.assertEqual(ap['credit_turnover'], 20)
        self.assertEqual(ap['known_total'], 120)
        self.assertEqual(ap['analytics'][0]['value'], 100)
        self.assertIn('Поставщик А', ap['analytics'][0]['label'])
        self.assertEqual(
            {item['account']: item['value'] for item in ap['components']},
            {'60.01': 100, '68': 12, '69': 3, '70': 5})

    def test_cogs_detail_keeps_recognized_and_forming_costs_separate(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.01', p1='Тепло', obk=100),
                day('2026-01-31', '90.03', p1='Тепло', obd=20),
                day('2026-01-31', '90.02', p1='Тепло', obd=70),
                day('2026-01-31', '90.07', obd=5),
                day('2026-01-31', '90.08', obd=3),
            ]),
            '20': account('20', [day('2026-01-31', p1='Топливо', obd=40)]),
            '25': account('25', [day('2026-01-31', p1='Цех', obd=4)]),
            '26': account('26', [day('2026-01-31', p1='АУП', obd=3)]),
            '44': account('44', [day('2026-01-31', p1='Сбыт', obd=2)]),
        })
        cogs = {x['id']: x for x in build_key_indicators(src)['cards']}['cogs']
        self.assertEqual(cogs['value'], 70)
        self.assertEqual(cogs['analytics'][0]['value'], 70)
        self.assertIn('Тепло', cogs['analytics'][0]['label'])
        self.assertEqual(
            {item['account']: item['value'] for item in cogs['recognized_costs']},
            {'90.02': 70, '90.07': 5, '90.08': 3})
        self.assertEqual(
            {item['account']: item['value'] for item in cogs['cost_movements']},
            {'20': 40, '25': 4, '26': 3, '44': 2})
        self.assertIn('не прибавляются', cogs['notes'][0])

    def test_cost_articles_reconcile_to_selected_recognized_total(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.02', obd=70),
                day('2026-01-31', '90.07', obd=5),
            ]),
            '20': account('20', [
                day('2026-01-31', p1='Газ', obd=40),
                day('2026-01-31', p1='Зарплата', obd=30),
            ]),
        })
        mappings = [
            {'account': '20', 'analytic_key': revenue_analytic_key([label]),
             'analytic_label': f'«{label}»', 'category': category,
             'target_account': '90.02', 'valid_from': '2026-01-01',
             'valid_to': '2026-12-31'}
            for label, category in (('Газ', 'fuel'), ('Зарплата', 'payroll'))
        ]
        card = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=mappings)['cards']}['cogs']
        structure = card['cost_structure']
        self.assertTrue(structure['reconciled'])
        self.assertEqual(structure['recognized_total'], 70)
        self.assertEqual(sum(item['share'] or 0
                             for item in structure['categories']), 100)
        self.assertEqual(structure['selected_targets'], ['90.02'])

    def test_cost_structure_rejects_overlap_and_more_than_one_hundred_percent(self):
        src = build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.02', obd=50)]),
            '20': account('20', [day('2026-01-31', p1='Газ', obd=60)]),
        })
        base = {'account': '20',
                'analytic_key': revenue_analytic_key(['Газ']),
                'analytic_label': '«Газ»', 'target_account': '90.02',
                'valid_from': '2026-01-01', 'valid_to': '2026-12-31'}
        mappings = [dict(base, category='fuel'),
                    dict(base, category='other', valid_from='2026-01-15')]
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=mappings)['cards']}['cogs']['cost_structure']
        self.assertFalse(structure['structure_valid'])
        self.assertTrue(structure['conflicts'])
        self.assertTrue(all(item['share'] is None
                            for item in structure['categories']))

    def test_cost_mapping_changes_category_on_its_effective_date(self):
        src = build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.02', obd=70)]),
            '20': account('20', [
                day('2026-01-10', p1='Энергоресурс', obd=40),
                day('2026-01-20', p1='Энергоресурс', obd=30),
            ]),
        })
        common = {
            'account': '20',
            'analytic_key': revenue_analytic_key(['Энергоресурс']),
            'analytic_label': '«Энергоресурс»',
            'target_account': '90.02',
        }
        mappings = [
            dict(common, category='fuel', valid_from='2026-01-01',
                 valid_to='2026-01-15'),
            dict(common, category='electricity', valid_from='2026-01-16',
                 valid_to='2026-01-31'),
        ]
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=mappings)['cards']}['cogs']['cost_structure']
        amounts = {item['id']: item['value'] for item in structure['categories']}
        self.assertEqual(amounts['fuel'], 40)
        self.assertEqual(amounts['electricity'], 30)
        self.assertTrue(structure['reconciled'])

    def test_cost_structure_suppresses_shares_when_forming_total_exceeds_target(self):
        src = build_indicator_source({
            '90': account('90', [day('2026-01-31', '90.02', obd=50)]),
            '20': account('20', [day('2026-01-31', p1='Газ', obd=60)]),
        })
        mapping = {
            'account': '20', 'analytic_key': revenue_analytic_key(['Газ']),
            'analytic_label': '«Газ»', 'category': 'fuel',
            'target_account': '90.02', 'valid_from': '2026-01-01',
            'valid_to': '2026-12-31'}
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=[mapping])['cards']}['cogs']['cost_structure']
        self.assertFalse(structure['structure_valid'])
        self.assertIn('превышает', structure['conflicts'][0]['message'])
        self.assertTrue(all(item['share'] is None
                            for item in structure['categories']))

    def test_cost_mapping_outside_selected_period_is_unclassified(self):
        src = build_indicator_source({
            '20': account('20', [day('2026-01-31', p1='Газ', obd=10)]),
        })
        mapping = {
            'account': '20', 'analytic_key': revenue_analytic_key(['Газ']),
            'analytic_label': '«Газ»', 'category': 'fuel',
            'target_account': '90.02', 'valid_from': '2025-01-01',
            'valid_to': '2025-12-31'}
        structure = {item['id']: item for item in build_key_indicators(
            src, cost_mappings=[mapping])['cards']}['cogs']['cost_structure']
        self.assertEqual(len(structure['unclassified']), 1)

    def test_profit_detail_separates_gross_sales_other_and_pretax_results(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.01', p1='Тепло', obk=100),
                day('2026-01-31', '90.03', p1='Тепло', obd=20),
                day('2026-01-31', '90.02', p1='Тепло', obd=70),
                day('2026-01-31', '90.07', obd=5),
                day('2026-01-31', '90.08', obd=3),
            ]),
            '91': account('91', [
                day('2026-01-31', '91.01', p1='Прочий доход', obk=4),
                day('2026-01-31', '91.02', p1='Прочий расход', obd=1),
            ]),
            '26': account('26', [day('2026-01-31', obd=3)]),
            '44': account('44', [day('2026-01-31', obd=5)]),
        })
        result = build_key_indicators(src)
        profit = {item['id']: item for item in result['cards']}['profit']
        self.assertEqual(profit['gross_profit'], 10)
        self.assertEqual(profit['profit_from_sales'], 2)
        self.assertEqual(profit['value'], 2)
        self.assertEqual(profit['other_income'], 4)
        self.assertEqual(profit['other_expense'], 1)
        self.assertEqual(profit['other_result'], 3)
        self.assertEqual(profit['profit_before_tax'], 5)
        self.assertEqual(result['objects']['totals']['profit'], 10)
        self.assertNotEqual(profit['value'], -6)

    def test_profit_does_not_assume_missing_recognized_expenses_are_zero(self):
        src = build_indicator_source({
            '90': account('90', [
                day('2026-01-31', '90.01', obk=100),
                day('2026-01-31', '90.03', obd=20),
                day('2026-01-31', '90.02', obd=70),
            ]),
            '91': account('91', [
                day('2026-01-31', '91.01', obk=4),
            ]),
        })
        profit = {item['id']: item for item in
                  build_key_indicators(src)['cards']}['profit']
        self.assertEqual(profit['value'], 10)
        self.assertIsNone(profit['profit_from_sales'])
        self.assertIsNone(profit['other_result'])
        self.assertIsNone(profit['profit_before_tax'])
        self.assertIn('Валовая прибыль', profit['title'])

    def test_credit_detail_exposes_accounts_turnovers_and_analytics_without_double_count(self):
        src = build_indicator_source({
            '66': account('66', [
                day('2026-01-31', obd=6, obk=10, skd=0, skk=30),
                day('2026-01-31', p1='Банк А', obd=6, obk=10,
                    skd=0, skk=30),
            ], top=dict(snd=0, snk=20, obd=6, obk=10, skd=0, skk=30)),
            '67': account('67', [
                day('2026-01-31', obd=7, obk=12, skd=0, skk=70),
                day('2026-01-31', p1='Банк Б', obd=7, obk=12,
                    skd=0, skk=70),
            ], top=dict(snd=0, snk=65, obd=7, obk=12, skd=0, skk=70)),
        })
        credits = {item['id']: item for item in
                   build_key_indicators(src)['cards']}['credits']
        self.assertEqual(credits['value'], 100)
        self.assertTrue(credits['coverage']['complete'])
        self.assertEqual(
            {item['account']: item['value'] for item in credits['accounts']},
            {'66': 30, '67': 70})
        self.assertEqual(
            {item['account']: item['debit_turnover']
             for item in credits['accounts']},
            {'66': 6, '67': 7})
        self.assertEqual(len(credits['analytics']), 2)
        self.assertEqual(
            {item['label']: item['value'] for item in credits['analytics']},
            {'«Банк А»': 30, '«Банк Б»': 70})

    def test_collection_turnover_is_not_collection(self):
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31', obd=100, obk=90)])
        })
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertIsNone(card['details'][0]['value'])
        self.assertIsNone(card['turnover_ratio'])
        self.assertIsNone(card['turnover_series'][-1]['value'])
        self.assertIn('62.01', card['reason'])

    def test_collection_analytics_do_not_double_count_parent_rows(self):
        rows = [
            day('2026-01-31', '62.01', p1='Население', obd=100, obk=80),
            day('2026-01-31', '62.01.1', p1='Население', obd=100, obk=80),
        ]
        src = build_indicator_source({'62': account('62', rows)})
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertIsNone(card['turnover_ratio'])
        self.assertEqual(len(card['analytics']), 2)
        self.assertEqual(card['analytics'][0]['debit'], 100)
        self.assertEqual(card['analytics'][0]['credit'], 80)
        self.assertEqual(card['analytics'][0]['turnover_ratio'], 80)

    def test_empty_source_returns_ten_unavailable_cards(self):
        result = build_key_indicators(build_indicator_source({}))
        self.assertEqual(len(result['cards']), 10)
        self.assertTrue(all(x['value'] is None for x in result['cards']))

    def test_parent_child_dedup_and_missing_vat_profit_null(self):
        rows = [
            day('2026-01-31', '90.01', obk=100),
            day('2026-01-31', '90.01.1', obk=100),
            day('2026-01-31', '90.02.1', obd=60),
        ]
        result = build_key_indicators(build_indicator_source({'90': account('90', rows)}))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 100)
        self.assertIsNone(cards['profit']['value'])
        self.assertIsNone(result['objects']['totals']['profit'])

    def test_balance_observation_is_exact_date_only(self):
        raw = account('51', [day('2026-01-30'), day('2026-01-31')],
                      period='')
        raw['balance_observations'] = [
            dict(date='2026-01-30', skd=15, skk=0)
        ]
        src = build_indicator_source({'51': raw})
        exact = build_key_indicators(src, 'day', '2026-01-30')
        other = build_key_indicators(src, 'day', '2026-01-31')
        self.assertEqual({x['id']: x for x in exact['cards']}['cash']['value'], 15)
        self.assertIsNone({x['id']: x for x in other['cards']}['cash']['value'])

    def test_declared_half_year_is_not_partial_for_month(self):
        raw = account('08', [day('2026-06-29', obd=5)],
                      period='за 1 полугодие2026 г.')
        result = build_key_indicators(build_indicator_source({'08': raw}),
                                      'month', '2026-06-30')
        self.assertFalse(any('неполное' in x for x in result['period']['warnings']))

    def test_exact_company_mappings_reconcile_segments(self):
        rows = [
            day('2026-01-31', '90.01', p1='Тепло физлица', obk=120),
            day('2026-01-31', '90.03', p1='НДС тепло', obd=20),
        ]
        mappings = [
            {'account': '90.01',
             'analytic_key': revenue_analytic_key(['Тепло физлица']),
             'analytic_label': '«Тепло физлица»',
             'category': 'population'},
            {'account': '90.03',
             'analytic_key': revenue_analytic_key(['НДС тепло']),
             'analytic_label': '«НДС тепло»',
             'category': 'population'},
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=mappings)
        segments = result['revenue_segments']
        population = next(x for x in segments['categories']
                          if x['id'] == 'population')
        self.assertEqual(population['value'], 100)
        self.assertTrue(segments['reconciled'])
        self.assertEqual(segments['unclassified'], [])

    def test_unmapped_and_conflicting_analytics_are_explicit(self):
        rows = [
            day('2026-01-31', '90.01', p1='Новая аналитика', obk=120),
            day('2026-01-31', '90.03', p1='Новая аналитика', obd=20),
        ]
        mappings = [
            {'account': '90.01',
             'analytic_key': revenue_analytic_key(['Новая аналитика']),
             'analytic_label': '«Новая аналитика»',
             'category': 'population'},
            {'account': '90.03',
             'analytic_key': revenue_analytic_key(['Новая аналитика']),
             'analytic_label': '«Новая аналитика»',
             'category': 'budget'},
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=mappings)
        self.assertFalse(result['revenue_segments']['reconciled'])
        self.assertEqual(len(result['revenue_segments']['conflicts']), 1)

    def test_full_analytic_coordinate_is_mapped_exactly(self):
        rows = [
            day('2026-01-31', '90.01', p1='Услуга', obk=100),
            dict(day('2026-01-31', '90.01', p1='Услуга', obk=200),
                 path2='Другой договор'),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=[{
                'account': '90.01',
                'analytic_key': revenue_analytic_key(['Услуга']),
                'analytic_label': '«Услуга»',
                'category': 'population',
            }])
        analytics = result['revenue_segments']['analytics']
        self.assertEqual(
            {item['analytic_label'] for item in analytics},
            {'«Услуга»', '«Услуга» / «Другой договор»'})
        self.assertEqual(len(result['revenue_segments']['unclassified']), 1)

    def test_unknown_revenue_amount_remains_null(self):
        rows = [day('2026-01-31', '90.01', p1='Услуга', obk=None)]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=[{
                'account': '90.01',
                'analytic_key': revenue_analytic_key(['Услуга']),
                'analytic_label': '«Услуга»',
                'category': 'population',
            }])
        revenue = next(card for card in result['cards']
                       if card['id'] == 'revenue')
        population = next(row for row in result['revenue_segments']['categories']
                          if row['id'] == 'population')
        self.assertIsNone(revenue['value'])
        self.assertIsNone(population['value'])
        self.assertEqual(len(result['revenue_segments']['unknown_amounts']), 1)
        self.assertIsNone(result['objects']['rows'][0]['revenue'])

    def test_structured_analytic_key_has_no_display_delimiter_collision(self):
        self.assertNotEqual(
            revenue_analytic_key(['A › B']),
            revenue_analytic_key(['A', 'B']))

    def test_cash_does_not_compare_when_account_set_changes(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ]),
            '55': account('55', [day('2026-02-28', skd=5)], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-02-28')['cards']}['cash']
        self.assertEqual(cash['date'], '2026-02-28')
        self.assertEqual(cash['accounts'], ['51', '55'])
        self.assertEqual(cash['missing_accounts'], ['50', '52'])
        self.assertEqual(cash['coverage'], 'partial')
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-01-31')

    def test_cash_same_partial_account_set_is_comparable(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-02-28')['cards']}['cash']
        self.assertEqual(cash['coverage'], 'partial')
        self.assertEqual(cash['comparison']['accounts'], ['51'])
        self.assertTrue(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['value'], 10)
        self.assertEqual(cash['delta']['coverage'], 'partial')
        self.assertEqual(cash['delta']['accounts'], ['51'])
        self.assertEqual(cash['delta']['previous_date'], '2026-01-31')
        self.assertEqual(cash['delta']['date'], '2026-02-28')
        self.assertIn('2026-01-31', cash['delta']['label'])
        self.assertIn('2026-02-28', cash['delta']['label'])

    def test_cash_zero_balances_are_complete_and_available(self):
        src = build_indicator_source({
            account_number: account(
                account_number, [day('2026-01-31', skd=0, skk=0)])
            for account_number in ('50', '51', '52', '55')
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-31')['cards']}['cash']
        self.assertEqual(cash['value'], 0)
        self.assertEqual(cash['accounts'], ['50', '51', '52', '55'])
        self.assertEqual(cash['missing_accounts'], [])
        self.assertEqual(cash['coverage'], 'complete')
        self.assertFalse(cash['series'][0]['comparison']['comparable'])

    def test_cash_series_does_not_bridge_a_missing_daily_point(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-03-31', skd=30),
            ]),
        })
        result = build_key_indicators(
            src, 'month', '2026-03-31', date_from='2026-01-31')
        series = {item['date']: item for item in
                  next(card for card in result['cards']
                       if card['id'] == 'cash')['series']}
        self.assertFalse(series['2026-01-31']['comparison']['comparable'])
        self.assertFalse(series['2026-03-31']['comparison']['comparable'])
        self.assertEqual(
            series['2026-03-31']['comparison']['previous_date'],
            '2026-03-30')

    def test_cash_day_series_has_one_exact_date_point(self):
        src = build_indicator_source({
            '51': account('51', [day('2026-01-31', skd=15)]),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-31')['cards']}['cash']
        self.assertEqual(len(cash['series']), 1)
        self.assertEqual(cash['series'][0]['date'], '2026-01-31')
        self.assertEqual(cash['series'][0]['value'], 15)
        self.assertIsNone(cash['series'][0]['comparison']['previous_date'])
        self.assertFalse(cash['series'][0]['comparison']['comparable'])

    def test_cash_day_delta_does_not_bridge_an_omitted_calendar_day(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', skd=10),
                day('2026-01-03', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'day', '2026-01-03')['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-01-02')
        self.assertEqual(cash['comparison']['date'], '2026-01-03')
        self.assertIn('слота', cash['comparison']['reason'])

    def test_cash_month_delta_requires_the_prior_calendar_month_end(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-03-31', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-03-31')['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-02-28')
        self.assertEqual(cash['comparison']['date'], '2026-03-31')
        self.assertIn('слота', cash['comparison']['reason'])

    def test_cash_quarter_delta_requires_the_prior_quarter_end(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-03-31', skd=10),
                day('2026-09-30', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'quarter', '2026-09-30')['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertEqual(cash['comparison']['previous_date'], '2026-06-30')
        self.assertEqual(cash['comparison']['date'], '2026-09-30')
        self.assertIn('слота', cash['comparison']['reason'])

    def test_cash_series_suppresses_account_change_and_compares_same_partial_set(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ], period=''),
            '55': account('55', [day('2026-02-28', skd=5)], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'quarter', '2026-02-28')['cards']}['cash']
        points = {item['date']: item for item in cash['series']
                  if item['date'] in ('2026-01-31', '2026-02-28')}
        self.assertFalse(points['2026-02-28']['comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['comparison']['reason'],
                         'Нет точного остатка')
        self.assertFalse(points['2026-02-28']['interval_comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['interval_comparison']['reason'],
                         'Изменился состав счетов')

        same_partial = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-02-28', skd=20),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            same_partial, 'quarter', '2026-02-28')['cards']}['cash']
        points = {item['date']: item for item in cash['series']
                  if item['date'] in ('2026-01-31', '2026-02-28')}
        self.assertFalse(points['2026-02-28']['comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['comparison']['reason'],
                         'Нет точного остатка')
        self.assertTrue(points['2026-02-28']['interval_comparison']['comparable'])
        self.assertEqual(points['2026-02-28']['interval_comparison']['value'], 10)
        self.assertEqual(points['2026-02-28']['coverage'], 'partial')

    def test_cash_month_series_uses_daily_slots_and_metadata(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-01', skd=10),
                day('2026-01-03', skd=30),
            ], period=''),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'month', '2026-01-03')['cards']}['cash']
        self.assertEqual(
            [point['date'] for point in cash['series']],
            ['2026-01-01', '2026-01-02', '2026-01-03'])
        self.assertEqual(
            [point['value'] for point in cash['series']], [10, None, 30])
        self.assertEqual(cash['series'][0]['accounts'], ['51'])
        self.assertEqual(cash['series'][1]['coverage'], 'missing')
        self.assertFalse(cash['series'][2]['comparison']['comparable'])

    def test_cash_quarter_series_uses_daily_slots_without_spreading_balances(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-01-31', skd=10),
                day('2026-03-31', skd=30),
            ], period='за 1 квартал 2026 г.'),
        })
        cash = {item['id']: item for item in build_key_indicators(
            src, 'quarter', '2026-03-31')['cards']}['cash']
        self.assertEqual(len(cash['series']), 90)
        series = {point['date']: point for point in cash['series']}
        self.assertEqual(series['2026-01-31']['value'], 10)
        self.assertIsNone(series['2026-02-28']['value'])
        self.assertEqual(series['2026-03-31']['value'], 30)
        self.assertEqual(series['2026-01-30']['coverage'], 'missing')
        self.assertEqual(series['2026-02-28']['coverage'], 'missing')
        self.assertEqual(series['2026-03-30']['coverage'], 'missing')
        self.assertFalse(series['2026-03-31']['comparison']['comparable'])

    def test_cash_multi_quarter_range_has_unavailable_card_comparison(self):
        src = build_indicator_source({
            '51': account('51', [
                day('2026-03-31', skd=10),
                day('2026-06-30', skd=20),
                day('2026-09-30', skd=30),
            ], period='за 3 квартал 2026 г.'),
        })
        result = build_key_indicators(
            src, 'quarter', '2026-09-30', date_from='2026-03-31')
        cash = {item['id']: item for item in result['cards']}['cash']
        self.assertIsNone(cash['delta'])
        self.assertFalse(cash['comparison']['comparable'])
        self.assertIsNone(cash['comparison']['previous_date'])
        self.assertIn('диапазона', cash['comparison']['reason'])
        self.assertEqual(len(cash['series']), 273)
        self.assertEqual(cash['series'][0]['date'], '2026-01-01')
        self.assertEqual(cash['series'][-1]['date'], '2026-09-30')


if __name__ == '__main__':
    unittest.main()

class DebtorMappingKeyIndicatorTests(unittest.TestCase):
    def test_absent_is_null_but_explicit_zero_is_zero(self):
        data = {'90': account('90', [day('2026-01-31', '90.01', obk=0),
                                     day('2026-01-31', '90.03', obd=0),
                                     day('2026-01-31', '90.02', obd=0)])}
        result = build_key_indicators(build_indicator_source(data))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 0)
        self.assertIsNone(cards['cash']['value'])

    def test_period_filter_and_missing_vat_is_gross(self):
        rows = [day('2026-01-31', '90.01', obk=100),
                day('2026-01-31', '90.02', obd=70),
                day('2026-02-28', '90.01', obk=900),
                day('2026-02-28', '90.02', obd=800)]
        result = build_key_indicators(build_indicator_source(
            {'90': account('90', rows, period='за 1 квартал 2026 г.')}),
            date='2026-01-31')
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 100)
        self.assertIn('брутто', cards['revenue']['formula'])

    def test_balance_only_at_original_report_boundary(self):
        top = dict(snd=1, snk=0, obd=0, obk=0, skd=25, skk=0)
        src = build_indicator_source({
            '51': account('51', [day('2026-01-31'), day('2026-02-28')],
                          top=top, period='за 1 квартал 2026 г.')
        })
        jan = build_key_indicators(src, date='2026-01-31')
        self.assertIsNone({x['id']: x for x in jan['cards']}['cash']['value'])

    def test_objects_reconcile_and_no_parent_child_double_count(self):
        rows = [
            day('2026-01-31', '90.01', p1='Услуга', obk=100),
            day('2026-01-31', '90.02', p1='Услуга', obd=60),
            day('2026-01-31', '90.03', p1='Услуга', obd=20),
        ]
        result = build_key_indicators(build_indicator_source({'90': account('90', rows)}))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(result['objects']['totals']['revenue'], cards['revenue']['value'])
        self.assertEqual(result['objects']['totals']['profit'], cards['profit']['value'])

    def test_salary_is_not_called_overdue(self):
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=50)
        result = build_key_indicators(build_indicator_source({
            '70': account('70', [day('2026-01-31')], top=top)
        }))
        salary = {x['id']: x for x in result['cards']}['salary']
        self.assertEqual(salary['value'], 50)
        self.assertIn('не подтверждает просрочку', salary['notes'][0])

    def test_false_day_balance_and_cross_period_leakage(self):
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=100, skk=0)
        src = build_indicator_source({
            '51': account('51', [day('2026-01-30'), day('2026-01-31')],
                          top=top)
        })
        result = build_key_indicators(src, 'day', '2026-01-30')
        self.assertIsNone({x['id']: x for x in result['cards']}['cash']['value'])

    def test_partial_period_warning_and_invalid_inputs(self):
        src = build_indicator_source({'08': account('08', [day('2026-03-15', obd=4)],
                                                     period='за 1 квартал 2026 г.')})
        result = build_key_indicators(src, 'year')
        self.assertTrue(result['period']['warnings'])
        with self.assertRaises(ValueError):
            build_key_indicators(src, 'week')
        with self.assertRaises(ValueError):
            build_key_indicators(src, 'month', '2025-01-01')

    def test_ar_known_scope_and_other_76_unmapped(self):
        row = dict(lvl=1, label='62.01', p0='62', p1='62.01', p2='', p3='',
                   snd=0, snk=0, obd=0, obk=0, skd=12, skk=0)
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')], [row]),
            '76': account('76', [day('2026-01-31')]),
        })
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['cash_dz']
        self.assertEqual(ar['value'], 12)
        self.assertIn('76', ar['notes'][0])

    def test_ar_detail_exposes_opening_and_analytic_balance(self):
        report_row = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                          p2='', p3='', snd=80, snk=0, obd=20, obk=0,
                          skd=100, skk=0)
        analytic = day('2026-01-31', '62.01', p1='Покупатель А',
                       obd=20, obk=0, snd=80, snk=0, skd=100, skk=0)
        src = build_indicator_source({
            '62': account('62', [analytic], [report_row])
        })
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['cash_dz']
        self.assertEqual(ar['value'], 100)
        self.assertEqual(ar['opening_value'], 80)
        self.assertEqual(len(ar['analytics']), 1)
        self.assertEqual(ar['analytics'][0]['value'], 100)
        self.assertIn('Покупатель А', ar['analytics'][0]['label'])

    def test_ap_detail_exposes_components_opening_turnover_and_analytics(self):
        report_row = dict(lvl=1, label='60.01', p0='60', p1='60.01',
                          p2='', p3='', snd=0, snk=80, obd=0, obk=20,
                          skd=0, skk=100)
        supplier = day('2026-01-31', '60.01', p1='Поставщик А',
                       obd=0, obk=20, snd=0, snk=80, skd=0, skk=100)
        src = build_indicator_source({
            '60': account('60', [supplier], [report_row]),
            '68': account('68', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=12)),
            '69': account('69', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=3)),
            '70': account('70', [day('2026-01-31')],
                          top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=5)),
        })
        ap = {x['id']: x for x in build_key_indicators(src)['cards']}['ap']
        self.assertEqual(ap['value'], 100)
        self.assertEqual(ap['opening_value'], 80)
        self.assertEqual(ap['credit_turnover'], 20)
        self.assertEqual(ap['known_total'], 120)
        self.assertEqual(ap['analytics'][0]['value'], 100)
        self.assertIn('Поставщик А', ap['analytics'][0]['label'])
        self.assertEqual(
            {item['account']: item['value'] for item in ap['components']},
            {'60.01': 100, '68': 12, '69': 3, '70': 5})

    def test_collection_turnover_is_not_collection(self):
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31', obd=100, obk=90)])
        })
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertIsNone(card['details'][0]['value'])
        self.assertIsNone(card['turnover_ratio'])
        self.assertIsNone(card['turnover_series'][-1]['value'])
        self.assertIn('62.01', card['reason'])

    def test_collection_analytics_do_not_double_count_parent_rows(self):
        rows = [
            day('2026-01-31', '62.01', p1='Население', obd=100, obk=80),
            day('2026-01-31', '62.01.1', p1='Население', obd=100, obk=80),
        ]
        src = build_indicator_source({'62': account('62', rows)})
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertIsNone(card['turnover_ratio'])
        self.assertEqual(len(card['analytics']), 2)
        self.assertEqual(card['analytics'][0]['debit'], 100)
        self.assertEqual(card['analytics'][0]['credit'], 80)
        self.assertEqual(card['analytics'][0]['turnover_ratio'], 80)

    def test_empty_source_returns_ten_unavailable_cards(self):
        result = build_key_indicators(build_indicator_source({}))
        self.assertEqual(len(result['cards']), 10)
        self.assertTrue(all(x['value'] is None for x in result['cards']))

    def test_parent_child_dedup_and_missing_vat_profit_null(self):
        rows = [
            day('2026-01-31', '90.01', obk=100),
            day('2026-01-31', '90.01.1', obk=100),
            day('2026-01-31', '90.02.1', obd=60),
        ]
        result = build_key_indicators(build_indicator_source({'90': account('90', rows)}))
        cards = {x['id']: x for x in result['cards']}
        self.assertEqual(cards['revenue']['value'], 100)
        self.assertIsNone(cards['profit']['value'])
        self.assertIsNone(result['objects']['totals']['profit'])

    def test_balance_observation_is_exact_date_only(self):
        raw = account('51', [day('2026-01-30'), day('2026-01-31')],
                      period='')
        raw['balance_observations'] = [
            dict(date='2026-01-30', skd=15, skk=0)
        ]
        src = build_indicator_source({'51': raw})
        exact = build_key_indicators(src, 'day', '2026-01-30')
        other = build_key_indicators(src, 'day', '2026-01-31')
        self.assertEqual({x['id']: x for x in exact['cards']}['cash']['value'], 15)
        self.assertIsNone({x['id']: x for x in other['cards']}['cash']['value'])

    def test_declared_half_year_is_not_partial_for_month(self):
        raw = account('08', [day('2026-06-29', obd=5)],
                      period='за 1 полугодие2026 г.')
        result = build_key_indicators(build_indicator_source({'08': raw}),
                                      'month', '2026-06-30')
        self.assertFalse(any('неполное' in x for x in result['period']['warnings']))

    def test_exact_company_mappings_reconcile_segments(self):
        rows = [
            day('2026-01-31', '90.01', p1='Тепло физлица', obk=120),
            day('2026-01-31', '90.03', p1='НДС тепло', obd=20),
        ]
        mappings = [
            {'account': '90.01',
             'analytic_key': revenue_analytic_key(['Тепло физлица']),
             'analytic_label': '«Тепло физлица»',
             'category': 'population'},
            {'account': '90.03',
             'analytic_key': revenue_analytic_key(['НДС тепло']),
             'analytic_label': '«НДС тепло»',
             'category': 'population'},
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=mappings)
        segments = result['revenue_segments']
        population = next(x for x in segments['categories']
                          if x['id'] == 'population')
        self.assertEqual(population['value'], 100)
        self.assertTrue(segments['reconciled'])
        self.assertEqual(segments['unclassified'], [])

    def test_unmapped_and_conflicting_analytics_are_explicit(self):
        rows = [
            day('2026-01-31', '90.01', p1='Новая аналитика', obk=120),
            day('2026-01-31', '90.03', p1='Новая аналитика', obd=20),
        ]
        mappings = [
            {'account': '90.01',
             'analytic_key': revenue_analytic_key(['Новая аналитика']),
             'analytic_label': '«Новая аналитика»',
             'category': 'population'},
            {'account': '90.03',
             'analytic_key': revenue_analytic_key(['Новая аналитика']),
             'analytic_label': '«Новая аналитика»',
             'category': 'budget'},
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=mappings)
        self.assertFalse(result['revenue_segments']['reconciled'])
        self.assertEqual(len(result['revenue_segments']['conflicts']), 1)

    def test_full_analytic_coordinate_is_mapped_exactly(self):
        rows = [
            day('2026-01-31', '90.01', p1='Услуга', obk=100),
            dict(day('2026-01-31', '90.01', p1='Услуга', obk=200),
                 path2='Другой договор'),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=[{
                'account': '90.01',
                'analytic_key': revenue_analytic_key(['Услуга']),
                'analytic_label': '«Услуга»',
                'category': 'population',
            }])
        analytics = result['revenue_segments']['analytics']
        self.assertEqual(
            {item['analytic_label'] for item in analytics},
            {'«Услуга»', '«Услуга» / «Другой договор»'})
        self.assertEqual(len(result['revenue_segments']['unclassified']), 1)

    def test_unknown_revenue_amount_remains_null(self):
        rows = [day('2026-01-31', '90.01', p1='Услуга', obk=None)]
        result = build_key_indicators(
            build_indicator_source({'90': account('90', rows)}),
            revenue_mappings=[{
                'account': '90.01',
                'analytic_key': revenue_analytic_key(['Услуга']),
                'analytic_label': '«Услуга»',
                'category': 'population',
            }])
        revenue = next(card for card in result['cards']
                       if card['id'] == 'revenue')
        population = next(row for row in result['revenue_segments']['categories']
                          if row['id'] == 'population')
        self.assertIsNone(revenue['value'])
        self.assertIsNone(population['value'])
        self.assertEqual(len(result['revenue_segments']['unknown_amounts']), 1)
        self.assertIsNone(result['objects']['rows'][0]['revenue'])

    def test_structured_analytic_key_has_no_display_delimiter_collision(self):
        self.assertNotEqual(
            revenue_analytic_key(['A › B']),
            revenue_analytic_key(['A', 'B']))

    def test_debtor_categories_reconcile_and_76_requires_explicit_mapping(self):
        row62 = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                     p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=100, skk=0)
        analytic62 = day('2026-01-31', '62.01', p1='Население',
                         skd=100, skk=0)
        analytic76 = day('2026-01-31', '76.05', p1='Субсидия',
                         skd=40, skk=0)
        src = build_indicator_source({
            '62': account('62', [analytic62], [row62]),
            '76': account('76', [analytic76]),
        })
        mapping62 = {
            'account': '62.01',
            'analytic_key': revenue_analytic_key(['Население']),
            'analytic_label': '«Население»', 'category': 'population',
            'effective_from': '2026-01-01', 'effective_to': '2026-01-31',
        }
        without76 = {x['id']: x for x in build_key_indicators(
            src, debtor_mappings=[mapping62])['cards']}['cash_dz']
        self.assertEqual(without76['value'], 100)
        new76 = next(x for x in without76['debtor_segments']['new']
                     if x['account'] == '76.05')
        self.assertFalse(new76['included_in_total'])
        self.assertNotIn('analytics', without76['debtor_segments'])
        self.assertNotIn('value', new76)
        mapping76 = {
            'account': '76.05',
            'analytic_key': revenue_analytic_key(['Субсидия']),
            'analytic_label': '«Субсидия»', 'category': 'subsidy',
            'effective_from': '2026-01-01', 'effective_to': '2026-01-31',
        }
        mapped = {x['id']: x for x in build_key_indicators(
            src, debtor_mappings=[mapping62, mapping76])['cards']}['cash_dz']
        self.assertEqual(mapped['value'], 100)
        self.assertFalse(mapped['debtor_segments']['reconciled'])
        self.assertEqual(mapped['debtor_segments']['included_76'], 40)

    def test_report_hierarchy_supplies_leaf_balances_and_keeps_76_subaccounts(self):
        total62 = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                       p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                       skd=30, skk=0)
        buyer = dict(lvl=2, label='Покупатель', p0='62', p1='62.01',
                     p2='Покупатель', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=30, skk=0)
        grant05 = dict(lvl=2, label='Один должник', p0='76', p1='76.05',
                       p2='Один должник', p3='', snd=0, snk=0, obd=0, obk=0,
                       skd=10, skk=0)
        grant09 = dict(lvl=2, label='Один должник', p0='76', p1='76.09',
                       p2='Один должник', p3='', snd=0, snk=0, obd=0, obk=0,
                       skd=20, skk=0)
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')],
                          [total62, buyer]),
            '76': account('76', [day('2026-01-31')],
                          [grant05, grant09]),
        })
        key = revenue_analytic_key(['Один должник'])
        mapping = {
            'account': '76.05', 'analytic_key': key,
            'analytic_label': '«Один должник»', 'category': 'subsidy',
            'effective_from': '2026-01-01', 'effective_to': '2026-01-31',
        }
        card = {x['id']: x for x in build_key_indicators(
            src, debtor_mappings=[mapping])['cards']}['cash_dz']
        self.assertEqual(card['value'], 30)
        self.assertEqual(card['debtor_segments']['included_76'], 10)
        new_items = card['debtor_segments']['new']
        self.assertTrue(any(item['account'] == '62.01' for item in new_items))
        self.assertTrue(any(item['account'] == '76.09' for item in new_items))

    def test_debtor_report_subtotal_is_not_added_to_contract_leaves(self):
        rows = [
            dict(lvl=0, label='62.01', p0='62', p1='62.01',
                 p2='', p3='', skd=100, skk=0),
            dict(lvl=1, label='Должник', p0='62', p1='62.01',
                 p2='Должник', p3='', skd=100, skk=0),
            dict(lvl=2, label='Договор А', p0='62', p1='62.01',
                 p2='Должник', p3='Договор А', skd=60, skk=0),
            dict(lvl=2, label='Договор Б', p0='62', p1='62.01',
                 p2='Должник', p3='Договор Б', skd=40, skk=0),
        ]
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')], rows)})
        card = {item['id']: item for item in build_key_indicators(src)['cards']}['cash_dz']
        analytics = card['analytics']
        self.assertEqual(len(analytics), 2)
        self.assertEqual(sum(item['value'] for item in analytics), 100)
        self.assertTrue(all('Должник' in item['label'] for item in analytics))

    def test_specialised_debtor_card_excludes_sibling_account_76_analytics(self):
        rows = [
            dict(lvl=0, label='76.05', p0='76', p1='76.05',
                 p2='', p3='', skd=10, skk=0),
            dict(lvl=1, label='Должник А', p0='76', p1='76.05',
                 p2='Должник А', p3='', skd=10, skk=0),
            dict(lvl=0, label='76.09.1', p0='76', p1='76.09.1',
                 p2='', p3='', skd=20, skk=0),
            dict(lvl=1, label='Должник Б', p0='76', p1='76.09.1',
                 p2='Должник Б', p3='', skd=20, skk=0),
        ]
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')],
                          [dict(lvl=0, label='62.01', p0='62', p1='62.01',
                                p2='', p3='', skd=0, skk=0)]),
            '76': account('76', [day('2026-01-31')], rows),
        })
        mappings = [{
            'account': account_name,
            'analytic_key': revenue_analytic_key([debtor]),
            'analytic_label': debtor, 'category': 'legal',
            'effective_from': '2026-01-01', 'effective_to': '2026-01-31',
        } for account_name, debtor in (
            ('76.05', 'Должник А'), ('76.09.1', 'Должник Б'))]
        card = {item['id']: item for item in build_key_indicators(
            src, debtor_mappings=mappings)['cards']}['cash_dz']
        self.assertEqual(card['debtor_segments']['included_76'], 30)
        self.assertEqual(card['analytics'], [])
        self.assertEqual(
            {item['account'] for item in card['classification_analytics']},
            {'76.05', '76.09.1'})

    def test_debtor_identity_preserves_hierarchy_beyond_p3(self):
        rows = [
            dict(lvl=0, label='62.01', p0='62', p1='62.01',
                 p2='', p3='', skd=20, skk=0),
            dict(lvl=1, label='Должник', p0='62', p1='62.01',
                 p2='Должник', p3='', skd=20, skk=0),
            dict(lvl=2, label='Договор', p0='62', p1='62.01',
                 p2='Должник', p3='Договор', skd=20, skk=0),
            dict(lvl=3, label='Документ', p0='62', p1='62.01',
                 p2='Должник', p3='Договор', skd=20, skk=0),
            dict(lvl=4, label='Строка 1', p0='62', p1='62.01',
                 p2='Должник', p3='Договор', skd=10, skk=0),
            dict(lvl=4, label='Строка 2', p0='62', p1='62.01',
                 p2='Должник', p3='Договор', skd=10, skk=0),
        ]
        src = build_indicator_source({
            '62': account('62', [day('2026-01-31')], rows)})
        card = {item['id']: item for item in build_key_indicators(src)['cards']}['cash_dz']
        analytics = card['analytics']
        self.assertEqual(len(analytics), 2)
        self.assertNotEqual(analytics[0]['analytic_key'],
                            analytics[1]['analytic_key'])
        self.assertTrue(all('Документ' in item['label'] for item in analytics))

    def test_debtor_mapping_is_period_specific_and_conflicts_are_explicit(self):
        row62 = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                     p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=20, skk=0)
        analytic = day('2026-01-31', '62.01', p1='Новый должник',
                       skd=20, skk=0)
        key = revenue_analytic_key(['Новый должник'])
        common = {'account': '62.01', 'analytic_key': key,
                  'analytic_label': '«Новый должник»',
                  'effective_from': '2026-01-01',
                  'effective_to': '2026-01-31'}
        mappings = [dict(common, category='population'),
                    dict(common, category='budget',
                         effective_from='2026-01-15',
                         effective_to='2026-02-15')]
        card = {x['id']: x for x in build_key_indicators(
            build_indicator_source({'62': account('62', [analytic], [row62])}),
            debtor_mappings=mappings)['cards']}['cash_dz']
        self.assertEqual(len(card['debtor_segments']['conflicts']), 1)
        self.assertFalse(card['debtor_segments']['reconciled'])
        expired = dict(common, category='population',
                       effective_from='2025-01-01',
                       effective_to='2025-12-31')
        card = {x['id']: x for x in build_key_indicators(
            build_indicator_source({'62': account('62', [analytic], [row62])}),
            debtor_mappings=[expired])['cards']}['cash_dz']
        self.assertEqual(len(card['debtor_segments']['unmapped']), 1)

    def test_debtor_mapping_must_be_effective_on_balance_date(self):
        row62 = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                     p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=20, skk=0)
        analytic = day('2026-01-31', '62.01', p1='Должник',
                       skd=20, skk=0)
        key = revenue_analytic_key(['Должник'])
        mapping = {
            'account': '62.01', 'analytic_key': key,
            'analytic_label': '«Должник»', 'category': 'population',
            'effective_from': '2026-01-01', 'effective_to': '2026-01-15',
        }
        card = {x['id']: x for x in build_key_indicators(
            build_indicator_source({'62': account('62', [analytic], [row62])}),
            debtor_mappings=[mapping])['cards']}['cash_dz']
        segments = card['debtor_segments']
        self.assertEqual(len(segments['unmapped']), 1)
        self.assertEqual(segments['new'], [])
        self.assertEqual(segments['classified_total'], 0)

    def test_never_mapped_debtor_analytic_is_reported_as_new(self):
        row62 = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                     p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=20, skk=0)
        analytic = day('2026-01-31', '62.01', p1='Новый должник',
                       skd=20, skk=0)
        card = {x['id']: x for x in build_key_indicators(
            build_indicator_source({'62': account('62', [analytic], [row62])})
        )['cards']}['cash_dz']
        self.assertEqual(len(card['debtor_segments']['new']), 1)
        self.assertEqual(card['debtor_segments']['unmapped'], [])


class DailyIndexPerformanceTests(unittest.TestCase):
    def test_exact_balance_observation_is_used_for_known_date(self):
        source = build_indicator_source({
            '08': account('08', [
                day('2026-01-01', skd=10, skk=0),
                day('2026-12-31', skd=80, skk=0),
            ], period='за 2026 год'),
        })
        result = build_key_indicators(source, 'year', '2026-12-31')
        capex = {item['id']: item for item in result['cards']}['capex']
        self.assertEqual(capex['closing_balance'], 80)
        self.assertNotIn('_daily_index', source)
        self.assertNotIn('_balance_index', source)
        self.assertNotIn('_top_balance_index', source)

    def test_synthetic_year_with_94900_rows_stays_bounded(self):
        first = date(2026, 1, 1)
        dates = [(first + timedelta(days=index)).isoformat()
                 for index in range(365)]
        rows = [
            {
                'date': dates[index % len(dates)],
                'month': dates[index % len(dates)][:7],
                'path1': '', 'path2': '', 'path3': '',
                'obd': 1, 'obk': 2, 'skd': 100, 'skk': 0,
            }
            for index in range(94900)
        ]
        source = {
            'version': 1,
            'dates': dates,
            'accounts': {
                '51': {
                    'acc': '51', 'daily': rows, 'rows': [], 'top': None,
                    'period': 'за 2026 год', 'warnings': [], 'check_ok': True,
                    'report_start': '2026-01-01',
                    'report_end': '2026-12-31',
                },
            },
        }
        started = time.perf_counter()
        result = build_key_indicators(source, 'year', '2026-12-31')
        elapsed = time.perf_counter() - started
        cash = {item['id']: item for item in result['cards']}['cash']
        self.assertEqual(len(cash['series']), 365)
        self.assertLess(elapsed, 4.0)


class PeriodAggregateTests(unittest.TestCase):
    def _report(self, period, rows, top=None):
        if top is None:
            top = dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0)
        return {
            'period': period,
            'source_file': f'{period}.xlsx',
            'top': top,
            'rows': rows,
        }

    def test_n_months_period_is_a_real_report_boundary(self):
        self.assertEqual(
            _period_bounds('Оборотно-сальдовая ведомость за 9 месяцев 2026 г.'),
            (date(2026, 1, 1), date(2026, 9, 30)))

    def test_exact_period_rows_supply_cards_without_daily_fabrication(self):
        rows = [
            dict(lvl=0, label='90', p0='90', p1='', p2='', p3='',
                 snd=0, snk=0, obd=100, obk=100, skd=0, skk=0),
            dict(lvl=1, label='90.01', p0='90', p1='90.01', p2='', p3='',
                 snd=0, snk=0, obd=0, obk=100, skd=0, skk=100),
            dict(lvl=1, label='90.03', p0='90', p1='90.03', p2='', p3='',
                 snd=0, snk=0, obd=20, obk=0, skd=20, skk=0),
            dict(lvl=1, label='90.02', p0='90', p1='90.02', p2='', p3='',
                 snd=0, snk=0, obd=60, obk=0, skd=60, skk=0),
        ]
        raw = account('90', [day('2026-01-31', '90.01', obk=999)],
                      period='за январь 2026 г.')
        raw['balance_reports'] = [self._report('за январь 2026 г.', rows)]
        source = build_indicator_source({'90': raw})
        result = build_key_indicators(source, period='month',
                                      date='2026-01-31')
        cards = {item['id']: item for item in result['cards']}
        self.assertEqual(cards['revenue']['value'], 80)
        self.assertEqual(cards['cogs']['value'], 60)
        self.assertEqual(cards['profit']['value'], 20)
        self.assertEqual(cards['revenue']['period_provenance']['kind'],
                         'period_aggregate')
        self.assertEqual(result['period']['series_kind'], 'month')
        self.assertEqual(
            [point['value'] for point in cards['revenue']['series']], [80])
        self.assertEqual(
            [point['provenance'] for point in cards['revenue']['series']],
            ['period_aggregate'])

    def test_cogs_half_year_table_uses_confirmed_months_not_one_aggregate(self):
        rows = [
            dict(lvl=1, label='90.02', p0='90', p1='90.02',
                 p2='', p3='', snd=0, snk=0, obd=70, obk=0, skd=0, skk=0),
        ]
        raw = account('90', [
            day('2026-01-31', '90.02', obd=20),
            day('2026-02-28', '90.02', obd=50),
        ], period='за 6 месяцев 2026 г.')
        raw['balance_reports'] = [
            self._report('за 6 месяцев 2026 г.', rows)]
        result = build_key_indicators(
            build_indicator_source({'90': raw}), period='month',
            date='2026-06-30', date_from='2026-01-31')
        cogs = {card['id']: card for card in result['cards']}['cogs']
        self.assertEqual(cogs['value'], 70)
        self.assertEqual(len(cogs['series']), 1)
        months = cogs['monthly_comparison']
        self.assertEqual([row['label'] for row in months],
                         ['Янв 2026', 'Фев 2026', 'Мар 2026',
                          'Апр 2026', 'Май 2026', 'Июн 2026'])
        self.assertEqual([row['current_cost'] for row in months],
                         [20, 50, None, None, None, None])
        self.assertTrue(all(row['previous_year_cost'] is None
                            and row['delta'] is None for row in months))

    def test_complete_report_tiling_sums_only_disjoint_intervals(self):
        jan = [
            dict(lvl=1, label='90.01', p0='90', p1='90.01',
                 p2='', p3='', snd=0, snk=0, obd=0, obk=10, skd=0, skk=10),
        ]
        feb = [
            dict(lvl=1, label='90.01', p0='90', p1='90.01',
                 p2='', p3='', snd=0, snk=0, obd=0, obk=30, skd=0, skk=30),
        ]
        raw = account('90', period='за январь 2026 г.')
        raw['balance_reports'] = [
            self._report('за январь 2026 г.', jan),
            self._report('за февраль 2026 г.', feb),
        ]
        result = build_key_indicators(
            build_indicator_source({'90': raw}), period='month',
            date='2026-02-28', date_from='2026-01-31')
        revenue = {item['id']: item for item in result['cards']}['revenue']
        self.assertEqual(revenue['value'], 40)
        self.assertEqual(
            [point['value'] for point in revenue['series']], [10, 30])
        self.assertEqual(result['period']['series_kind'], 'month')

    def test_collection_exact_report_keeps_terminal_same_code_analytic_leaf(self):
        rows = [
            dict(lvl=0, label='62.01', p0='62', p1='62.01',
                 p2='', p3='', snd=0, snk=0, obd=60, obk=30,
                 skd=0, skk=0),
            dict(lvl=1, label='Покупатель', p0='62', p1='62.01',
                 p2='Покупатель', p3='', snd=0, snk=0, obd=50, obk=25,
                 skd=0, skk=0),
            dict(lvl=2, label='Договор 1', p0='62', p1='62.01',
                 p2='Покупатель', p3='Договор 1', snd=0, snk=0,
                 obd=40, obk=20, skd=0, skk=0),
            dict(lvl=0, label='62.02', p0='62', p1='62.02',
                 p2='', p3='', snd=0, snk=0, obd=10, obk=20,
                 skd=0, skk=0),
        ]
        raw = account(
            '62', [day('2026-01-31', '62.01')],
            period='за январь 2026 г.')
        raw['balance_reports'] = [self._report(
            'за январь 2026 г.', rows)]
        cards = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': raw}), period='month',
            date='2026-01-31')['cards']}
        collection = cards['collection']
        self.assertEqual(
            [item['value'] for item in collection['components']],
            [40, 20, 10, 20])
        self.assertEqual(collection['value'], 75)
        self.assertEqual(
            collection['formula'],
            '(ОбК(62.01) + ОбК(62.02) − ОбД(62.02)) / ОбД(62.01) × 100%')

    def test_collection_tiled_reports_keep_terminal_leaves_and_sum_components(self):
        def collection_rows(debit_01, credit_01, debit_02, credit_02):
            return [
                dict(lvl=1, label='Покупатель', p0='62', p1='62.01',
                     p2='Покупатель', p3='', snd=0, snk=0, obd=debit_01 + 5,
                     obk=credit_01 + 5, skd=0, skk=0),
                dict(lvl=2, label='Договор 1', p0='62', p1='62.01',
                     p2='Покупатель', p3='Договор 1', snd=0, snk=0,
                     obd=debit_01, obk=credit_01, skd=0, skk=0),
                dict(lvl=1, label='62.02', p0='62', p1='62.02',
                     p2='', p3='', snd=0, snk=0, obd=debit_02,
                     obk=credit_02, skd=0, skk=0),
            ]

        raw = account('62', [
            day('2026-01-31', '62.01'),
            day('2026-02-28', '62.01'),
        ], period='за январь 2026 г.')
        raw['balance_reports'] = [
            self._report('за январь 2026 г.',
                         collection_rows(40, 20, 10, 20)),
            self._report('за февраль 2026 г.',
                         collection_rows(60, 30, 20, 40)),
        ]
        result = build_key_indicators(
            build_indicator_source({'62': raw}), period='month',
            date='2026-02-28', date_from='2026-01-31')
        collection = {item['id']: item for item in result['cards']}[
            'collection']
        self.assertEqual(
            [item['value'] for item in collection['components']],
            [100, 50, 30, 60])
        self.assertEqual(collection['value'], 80)
        self.assertEqual(
            [point['value'] for point in collection['turnover_series']],
            [75, 83.33])

    def test_exact_report_rows_are_used_for_ar_and_ap_balances(self):
        row62 = dict(lvl=1, label='62.01', p0='62', p1='62.01',
                     p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=45, skk=5)
        row60 = dict(lvl=1, label='60.01', p0='60', p1='60.01',
                     p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                     skd=3, skk=28)
        raw62 = account('62', period='за январь 2026 г.')
        raw62['balance_reports'] = [self._report(
            'за январь 2026 г.', [row62],
            top=dict(snd=0, snk=0, obd=0, obk=0, skd=45, skk=5))]
        raw60 = account('60', period='за январь 2026 г.')
        raw60['balance_reports'] = [self._report(
            'за январь 2026 г.', [row60],
            top=dict(snd=0, snk=0, obd=0, obk=0, skd=3, skk=28))]
        result = build_key_indicators(build_indicator_source(
            {'62': raw62, '60': raw60}), period='month',
            date='2026-01-31')
        cards = {item['id']: item for item in result['cards']}
        self.assertEqual(cards['cash_dz']['value'], 40)
        self.assertEqual(cards['ap']['value'], 25)

    def test_missing_numeric_subaccount_does_not_fallback_to_root_balance(self):
        root62 = dict(lvl=0, label='62', p0='62', p1='', p2='', p3='',
                      snd=0, snk=0, obd=0, obk=0, skd=0, skk=0)
        buyer = dict(lvl=1, label='Покупатель', p0='62',
                     p1='Покупатель', p2='', p3='',
                     snd=0, snk=0, obd=0, obk=0, skd=420000, skk=0)
        root60 = dict(lvl=0, label='60', p0='60', p1='', p2='', p3='',
                      snd=0, snk=0, obd=0, obk=0, skd=0, skk=0)
        supplier = dict(lvl=1, label='Поставщик', p0='60',
                        p1='Поставщик', p2='', p3='',
                        snd=0, snk=0, obd=0, obk=5660000, skd=0, skk=5660000)
        raw62 = account('62', period='за январь 2026 г.')
        raw62['balance_reports'] = [self._report(
            'за январь 2026 г.', [root62, buyer], top=root62)]
        raw60 = account('60', period='за январь 2026 г.')
        raw60['balance_reports'] = [self._report(
            'за январь 2026 г.', [root60, supplier], top=root60)]
        cards = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': raw62, '60': raw60}),
            period='month', date='2026-01-31')['cards']}
        self.assertIsNone(cards['cash_dz']['value'])
        self.assertIn('отсутствует', cards['cash_dz']['reason'])
        self.assertIsNone(cards['ap']['value'])
        self.assertIn('отсутствует', cards['ap']['reason'])
        self.assertIsNone(cards['ap']['credit_turnover'])
        self.assertTrue(cards['ap']['analytics'])
        self.assertTrue(all(not item['included_in_total']
                            for item in cards['cash_dz']['analytics']))

    def test_period_leaf_deduplication_is_partitioned_by_report_interval(self):
        jan = [
            dict(lvl=1, label='90.01', p0='90', p1='90.01',
                 p2='', p3='', snd=0, snk=0, obd=0, obk=10, skd=0, skk=10),
        ]
        feb = [
            dict(lvl=2, label='90.01.1', p0='90', p1='90.01',
                 p2='90.01.1', p3='', snd=0, snk=0, obd=0, obk=30,
                 skd=0, skk=30),
        ]
        raw = account('90', period='за январь 2026 г.')
        raw['balance_reports'] = [
            self._report('за январь 2026 г.', jan),
            self._report('за февраль 2026 г.', feb),
        ]
        revenue = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'90': raw}), period='month',
            date='2026-02-28', date_from='2026-01-31')['cards']}['revenue']
        self.assertEqual(revenue['value'], 40)

    def test_period_mapping_must_cover_the_whole_aggregate_interval(self):
        jan = [dict(lvl=1, label='Газ', p0='20', p1='Газ',
                    p2='', p3='', snd=0, snk=0, obd=10, obk=0,
                    skd=0, skk=0)]
        feb = [dict(lvl=1, label='Газ', p0='20', p1='Газ',
                    p2='', p3='', snd=0, snk=0, obd=20, obk=0,
                    skd=0, skk=0)]
        raw = account('20', period='за январь 2026 г.')
        raw['balance_reports'] = [
            self._report('за январь 2026 г.', jan),
            self._report('за февраль 2026 г.', feb),
        ]
        mapping = {
            'account': '20',
            'analytic_key': revenue_analytic_key(['Газ']),
            'analytic_label': '«Газ»',
            'category': 'fuel',
            'target_account': '90.02',
            'valid_from': '2026-01-01',
            'valid_to': '2026-01-31',
        }
        result = build_key_indicators(
            build_indicator_source({'20': raw}), period='month',
            date='2026-02-28', date_from='2026-01-31',
            cost_mappings=[mapping])
        structure = {item['id']: item for item in result['cards']}[
            'cogs']['cost_structure']
        self.assertTrue(structure['conflicts'])
        self.assertEqual(structure['classified_total'], 0.0)
        self.assertEqual(structure['categories'][0]['value'], 0.0)

    def test_debtor_mapping_midperiod_is_not_used_for_aggregate_balance(self):
        report_rows = [
            dict(lvl=0, label='62.01', p0='62', p1='62.01',
                 p2='', p3='', snd=0, snk=0, obd=0, obk=0,
                 skd=100, skk=0),
            dict(lvl=1, label='Покупатель', p0='62', p1='62.01',
                 p2='Покупатель', p3='', snd=0, snk=0, obd=0, obk=0,
                 skd=100, skk=0),
        ]
        raw = account('62', [
            day('2026-01-31', '62.01'),
            day('2026-02-28', '62.01'),
        ], period='за 2 месяца 2026 г.')
        raw['balance_reports'] = [self._report(
            'за 2 месяца 2026 г.', report_rows)]
        mapping = {
            'account': '62.01',
            'analytic_key': revenue_analytic_key(['Покупатель']),
            'analytic_label': '«Покупатель»',
            'category': 'population',
            'effective_from': '2026-02-01',
            'effective_to': '2026-02-28',
        }
        ar = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': raw}), period='month',
            date='2026-02-28', date_from='2026-01-31',
            debtor_mappings=[mapping])['cards']}['cash_dz']
        self.assertEqual(ar['value'], 100)
        self.assertEqual(ar['debtor_segments']['classified_total'], 0.0)
        self.assertTrue(ar['debtor_segments']['unmapped'])

    def test_period_provenance_is_not_inherited_from_unrelated_accounts(self):
        raw90 = account('90', [
            day('2026-01-31', '90.02', obd=70),
        ], period='за январь 2026 г.')
        row20 = dict(lvl=1, label='Газ', p0='20', p1='Газ',
                     p2='', p3='', snd=0, snk=0, obd=10, obk=0,
                     skd=0, skk=0)
        raw20 = account('20', period='за январь 2026 г.')
        raw20['balance_reports'] = [self._report(
            'за январь 2026 г.', [row20])]
        cards = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'90': raw90, '20': raw20}),
            period='month', date='2026-01-31')['cards']}
        self.assertNotIn('period_provenance', cards['cogs'])
        movement = next(item for item in cards['cogs']['cost_movements']
                        if item['account'] == '20')
        self.assertEqual(movement['provenance'], 'period_aggregate')

    def test_collection_proxy_uses_the_four_explicit_components(self):
        rows = [
            day('2026-01-31', '62.01', obd=100, obk=50),
            day('2026-01-31', '62.02', obd=10, obk=20),
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}))['cards']}[
                'collection']
        self.assertEqual(card['value'], 60)
        self.assertEqual(
            card['formula'],
            '(ОбК(62.01) + ОбК(62.02) − ОбД(62.02)) / ОбД(62.01) × 100%')
        self.assertEqual(
            [item['value'] for item in card['components']],
            [100, 50, 10, 20])
        self.assertEqual(card['display_components'], [
            {
                'account': '62.01',
                'label': 'Начисления',
                'value': 100,
                'previous_year_value': None,
                'previous_year_delta': None,
            },
            {
                'account': '62 − 62.02',
                'label': 'Поступления',
                'value': 60,
                'previous_year_value': None,
                'previous_year_delta': None,
            },
        ])
    def test_collection_display_components_include_previous_year_and_delta(self):
        rows = [
            day('2026-01-31', '62.01', obd=100, obk=50),
            day('2026-01-31', '62.02', obd=10, obk=20),
            day('2025-01-31', '62.01', obd=80, obk=40),
            day('2025-01-31', '62.02', obd=5, obk=15),
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}),
            period='month', date='2026-01-31')['cards']}['collection']
        self.assertEqual(card['display_components'], [
            {
                'account': '62.01',
                'label': 'Начисления',
                'value': 100,
                'previous_year_value': 80,
                'previous_year_delta': 20,
            },
            {
                'account': '62 − 62.02',
                'label': 'Поступления',
                'value': 60,
                'previous_year_value': 50,
                'previous_year_delta': 10,
            },
        ])
        self.assertEqual(card['previous_year_value'], 62.5)
        self.assertEqual(card['previous_year_delta'], -2.5)

    def test_collection_proxy_prefers_daily_rows_over_truncated_period_hierarchy(self):
        daily_rows = [
            day('2026-01-31', '62.01', obd=100, obk=50),
            day('2026-01-31', '62.02', obd=10, obk=20),
        ]
        raw = account('62', daily_rows, period='за январь 2026 г.')
        raw['balance_reports'] = [self._report(
            'за январь 2026 г.', [
                dict(lvl=1, label='62.01', p0='62', p1='62.01',
                     p2='', p3='', snd=0, snk=0, obd=300, obk=150,
                     skd=0, skk=0),
                dict(lvl=2, label='Покупатель', p0='62', p1='62.01',
                     p2='Покупатель', p3='', snd=0, snk=0, obd=300,
                     obk=150, skd=0, skk=0),
                dict(lvl=1, label='62.02', p0='62', p1='62.02',
                     p2='', p3='', snd=0, snk=0, obd=30, obk=60,
                     skd=0, skk=0),
            ])]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': raw}),
            period='month', date='2026-01-31')['cards']}['collection']
        self.assertEqual(card['value'], 60)
        self.assertEqual(
            [item['value'] for item in card['components']],
            [100, 50, 10, 20])

    def test_collection_proxy_keeps_explicit_account_with_deep_daily_analytics(self):
        rows = [
            dict(date='2026-01-31', month='2026-01', account='62.01',
                 path1='Покупатель', path2='Услуга', path3='<...>',
                 obd=100, obk=50),
            dict(date='2026-01-31', month='2026-01', account='62.02',
                 path1='Покупатель', path2='Услуга', path3='<...>',
                 obd=10, obk=20),
        ]
        source = build_indicator_source({'62': account('62', rows)})
        self.assertEqual(
            {row['account'] for row in source['accounts']['62']['daily']},
            {'62.01', '62.02'})
        card = {item['id']: item for item in build_key_indicators(
            source, period='month', date='2026-01-31')['cards']}['collection']
        self.assertEqual(card['value'], 60)
        self.assertIsNone(card['reason'])

    def test_collection_proxy_missing_6202_is_not_zero(self):
        rows = [day('2026-01-31', '62.01', obd=100, obk=50)]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}))['cards']}[
                'collection']
        self.assertIsNone(card['value'])
        self.assertIn('62.02', card['reason'])
        self.assertIsNone(card['turnover_series'][-1]['value'])

    def test_collection_proxy_does_not_substitute_root_62(self):
        rows = [day('2026-01-31', '62', obd=100, obk=90)]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}))['cards']}[
                'collection']
        self.assertIsNone(card['value'])
        self.assertTrue(all(item['value'] is None for item in card['components']))

    def test_collection_proxy_rejects_zero_6201_debit(self):
        rows = [
            day('2026-01-31', '62.01', obd=0, obk=50),
            day('2026-01-31', '62.02', obd=10, obk=20),
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}))['cards']}[
                'collection']
        self.assertIsNone(card['value'])
        self.assertIn('нулю', card['reason'])

    def test_collection_proxy_h1_control_values_keep_extremes(self):
        expected = [73.66, 105.05, 32.36, 96.06, 116.10, 92.78]
        calculated = []
        for month, percentage in enumerate(expected, 1):
            point_date = date(2026, month, 1).isoformat()
            point = _collection_components_from_rows([
                day(point_date, '62.01', obd=10000,
                    obk=round(percentage * 100, 2)),
                day(point_date, '62.02', obd=0, obk=0),
            ])
            calculated.append(point['value'])
        self.assertEqual(calculated, expected)
        self.assertGreater(calculated[1], 100)
        self.assertLess(calculated[2], 50)
        self.assertGreater(calculated[4], 100)

    def test_collection_proxy_deduplicates_subaccount_parent_and_leaf(self):
        rows = [
            day('2026-01-31', '62.01', obd=100, obk=50),
            day('2026-01-31', '62.01.1', obd=40, obk=20),
            day('2026-01-31', '62.02', obd=10, obk=20),
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}))['cards']}[
                'collection']
        self.assertEqual(card['value'], 75)
        self.assertEqual(
            {item['label']: item['value'] for item in card['components']},
            {'Дебетовый оборот 62.01': 40,
             'Кредитовый оборот 62.01': 20,
             'Дебетовый оборот 62.02': 10,
             'Кредитовый оборот 62.02': 20})

    def test_collection_proxy_daily_gap_is_null_not_zero(self):
        rows = [
            day('2025-01-30', '62.01', obd=80, obk=60),
            day('2025-01-30', '62.02', obd=10, obk=20),
            day('2026-01-30', '62.01', obd=100, obk=50),
            day('2026-01-30', '62.02', obd=10, obk=20),
            day('2026-01-31', '62.01', obd=100, obk=50),
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}),
            period='day', date='2026-01-31',
            date_from='2026-01-30')['cards']}['collection']
        self.assertEqual([item['value'] for item in card['turnover_series']],
                         [60, None])
        self.assertEqual(
            [item['value'] for item in card['previous_year_turnover_series']],
            [87.5, None])
        self.assertEqual(
            card['previous_year_turnover_series'][0]['source_date'],
            '2025-01-30')

    def test_collection_period_table_recalculates_months_and_total(self):
        million = 1_000_000
        current_amounts = [
            value * million
            for value in (100, 110, 90, 120, 115, 105, 112, 120.19)]
        current_accruals = [
            value * million
            for value in (130, 140, 120, 150, 145, 140, 150, 163.68)]
        previous_amounts = [
            value * million for value in (40, 42, 44, 45, 46, 47, 48, 52)]
        previous_accruals = [
            value * million
            for value in (39, 41, 43, 44, 45, 46, 48, 57.62)]

        def series(amounts, accruals, source_year=None):
            return [{
                'date': f'2026-{month:02d}-01',
                'source_date': (
                    f'{source_year}-{month:02d}-01'
                    if source_year else None),
                'numerator': amount,
                'denominator': accrual,
                'coverage': {'state': 'complete'},
            } for month, (amount, accrual) in enumerate(
                zip(amounts, accruals), 1)]

        table = _collection_period_table(
            series(current_amounts, current_accruals),
            series(previous_amounts, previous_accruals, 2025))
        self.assertEqual(len(table['rows']), 8)
        self.assertAlmostEqual(table['rows'][0]['level'], 76.92)
        self.assertEqual(table['rows'][0]['previous_amount'], 40 * million)
        self.assertEqual(table['rows'][0]['delta'], 60 * million)
        self.assertEqual(table['rows'][0]['delta_percent'], 150)
        self.assertEqual(table['total']['amount'], 872.19 * million)
        self.assertEqual(table['total']['accrual'], 1138.68 * million)
        self.assertEqual(table['total']['level'], 76.6)
        self.assertEqual(table['total']['previous_amount'], 364 * million)
        self.assertEqual(
            table['total']['previous_accrual'], 363.62 * million)
        self.assertEqual(table['total']['previous_level'], 100.1)
        self.assertEqual(table['total']['delta'], 508.19 * million)
        self.assertEqual(table['total']['delta_percent'], 139.61)

    def test_collection_period_table_preserves_incomplete_and_zero_states(self):
        current = [
            {'date': '2026-01-01', 'numerator': 10, 'denominator': 0,
             'coverage': {'state': 'zero_denominator'}},
            {'date': '2026-02-01', 'numerator': None, 'denominator': None,
             'coverage': {'state': 'incomplete'}},
        ]
        previous = [
            {'date': '2026-01-01', 'numerator': 0, 'denominator': 10,
             'coverage': {'state': 'complete'}},
            {'date': '2026-02-01', 'numerator': 5, 'denominator': 10,
             'coverage': {'state': 'complete'}},
        ]
        table = _collection_period_table(current, previous)
        self.assertEqual(table['rows'][0]['amount'], 10)
        self.assertIsNone(table['rows'][0]['level'])
        self.assertIsNone(table['rows'][0]['delta_percent'])
        self.assertIsNone(table['rows'][1]['amount'])
        self.assertEqual(table['rows'][1]['coverage'], 'incomplete')
        self.assertIsNone(table['total']['amount'])
        self.assertIsNone(table['total']['level'])

    def test_linked_collection_is_calculated_by_confirmed_consumer_category(self):
        rows = [
            day('2026-01-31', '62.01', obd=100, obk=50),
            day('2026-01-31', '62.02', obd=10, obk=20),
        ]
        mappings = [{
            'account': '62.01', 'analytic_key': 'buyer-1', 'category': 'population',
            'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        }, {
            'account': '62.01', 'analytic_key': 'buyer-2', 'category': 'budget',
            'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        }]
        accruals = [
            {'date': '2026-01-15', 'account': '62.01', 'analytic_key': 'buyer-1',
             'type': 'accrual', 'accrued': 60, 'accrual_document': 'ACC-1',
             'source': '1С: Расчёты', 'version': '2026.01'},
            {'date': '2026-01-20', 'account': '62.01', 'analytic_key': 'buyer-2',
             'type': 'accrual', 'accrued': 40, 'accrual_document': 'ACC-2',
             'source': '1С: Расчёты', 'version': '2026.01'},
        ]
        allocations = [
            {'date': '2026-01-25', 'payment_document': 'PAY-1',
             'accrual_document': 'ACC-1', 'type': 'payment', 'allocated_amount': 54},
            {'date': '2026-01-26', 'payment_document': 'PAY-2',
             'accrual_document': 'ACC-2', 'type': 'payment', 'allocated_amount': 28},
        ]
        norms = [{
            'category': 'population', 'plan_percent': 85, 'approved': True,
            'source': 'Бюджет 2026', 'version': '1',
            'approved_at': '2025-12-20',
            'valid_from': '2026-01-01', 'valid_to': '2026-12-31',
        }]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', rows)}),
            debtor_mappings=mappings, collection_accruals=accruals,
            collection_allocations=allocations,
            collection_norms=norms)['cards']}['collection']
        linked = card['consumer_collection']
        self.assertTrue(linked['available'])
        self.assertEqual(linked['value'], 82)
        self.assertEqual(linked['reconciliation_difference'], 22)
        self.assertEqual(linked['accrual_sources'], [{
            'source': '1С: Расчёты', 'version': '2026.01', 'rows': 2,
            'accrual': 100, 'date_from': '2026-01-15',
            'date_to': '2026-01-20',
        }])
        population = next(row for row in linked['categories']
                          if row['id'] == 'population')
        budget = next(row for row in linked['categories']
                      if row['id'] == 'budget')
        self.assertEqual(population['value'], 90)
        self.assertEqual(population['deviation'], 5)
        self.assertEqual(population['status'], 'normal')
        self.assertIsNone(budget['plan'])
        self.assertIsNone(budget['deviation'])
        self.assertIsNone(budget['status'])

    def test_linked_collection_chart_preserves_current_and_apg_money(self):
        mappings = [{
            'account': '62.01', 'analytic_key': 'buyer',
            'category': 'population',
            'effective_from': '2025-01-01', 'effective_to': '2026-12-31',
        }]
        accruals = [
            {'date': '2025-01-15', 'account': '62.01',
             'analytic_key': 'buyer', 'type': 'accrual', 'accrued': 50,
             'accrual_document': 'OLD'},
            {'date': '2026-01-15', 'account': '62.01',
             'analytic_key': 'buyer', 'type': 'accrual', 'accrued': 100,
             'accrual_document': 'NEW'},
        ]
        allocations = [
            {'date': '2025-01-20', 'payment_document': 'OLD-PAY',
             'accrual_document': 'OLD', 'type': 'payment', 'allocated_amount': 40},
            {'date': '2026-01-20', 'payment_document': 'NEW-PAY',
             'accrual_document': 'NEW', 'type': 'payment', 'allocated_amount': 95},
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', [
                day('2025-01-31', '62.01', obd=50, obk=40),
                day('2026-01-31', '62.01', obd=100, obk=95),
            ])}), period='month', date='2026-01-31',
            debtor_mappings=mappings,
            collection_accruals=accruals,
            collection_allocations=allocations)['cards']}['collection']
        self.assertEqual(card['consumer_collection']['value'], 95)
        self.assertTrue(any(
            point['accrual'] == 100 and point['payment'] == 95
            for point in card['linked_monthly_series']))
        self.assertTrue(any(
            point['value'] == 80 and point['accrual'] == 50
            and point['payment'] == 40 and point['source_date'].startswith('2025-')
            for point in card['previous_year_linked_monthly_series']))
        self.assertEqual(card['linked_yearly_series'][0]['payment'], 95)
        self.assertEqual(
            card['previous_year_linked_yearly_series'][0]['value'], 80)

    def test_linked_collection_daily_curve_includes_later_payments_for_both_years(self):
        mappings = [{
            'account': '62.01', 'analytic_key': 'buyer',
            'category': 'population',
            'effective_from': '2025-01-01', 'effective_to': '2026-12-31',
        }]
        accruals = [
            {'date': f'{year}-01-01', 'account': '62.01',
             'analytic_key': 'buyer', 'type': 'accrual',
             'accrued': amount, 'accrual_document': str(year)}
            for year, amount in ((2025, 50), (2026, 100))
        ]
        allocations = [
            {'date': f'{year}-01-02', 'payment_document': f'PAY-{year}',
             'accrual_document': str(year), 'type': 'payment',
             'allocated_amount': amount}
            for year, amount in ((2025, 40), (2026, 95))
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({'62': account('62', [
                day('2025-01-01', '62.01', obd=50),
                day('2026-01-01', '62.01', obd=100),
            ])}), period='day', date='2026-01-02',
            date_from='2026-01-01',
            debtor_mappings=mappings,
            collection_accruals=accruals,
            collection_allocations=allocations)['cards']}['collection']
        self.assertIsNone(card['consumer_collection']['series'][1]['value'])
        self.assertEqual(
            card['linked_daily_cumulative_series'][1]['value'], 95)
        self.assertEqual(
            card['linked_daily_cumulative_series'][1]['payment'], 95)
        self.assertIsNone(card['previous_year_linked_series'][1]['value'])
        self.assertEqual(
            card['previous_year_linked_daily_cumulative_series'][1]['value'],
            80)
        self.assertEqual(
            card['previous_year_linked_daily_cumulative_series'][1]['source_date'],
            '2025-01-02')

    def test_linked_yearly_chart_includes_later_cohort_payments(self):
        source = build_indicator_source({'62': account('62', [
            day('2026-01-31', '62.01', obd=100),
            day('2026-02-28', '62.01', obk=95),
        ])})
        accruals = [{
            'date': '2026-01-15', 'account': '62.01',
            'analytic_key': 'buyer', 'type': 'accrual', 'accrued': 100,
            'accrual_document': 'JAN',
        }]
        allocations = [{
            'date': '2026-02-10', 'payment_document': 'FEB-PAY',
            'accrual_document': 'JAN', 'type': 'payment',
            'allocated_amount': 95,
        }]
        mappings = [{
            'account': '62.01', 'analytic_key': 'buyer',
            'category': 'population',
            'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        }]
        card = {item['id']: item for item in build_key_indicators(
            source, period='month', date_from='2026-01-31',
            date='2026-02-28', debtor_mappings=mappings,
            collection_accruals=accruals,
            collection_allocations=allocations)['cards']}['collection']
        self.assertEqual(card['linked_monthly_series'][0]['payment'], 0)
        self.assertIsNone(card['linked_monthly_series'][1]['value'])
        self.assertEqual(card['linked_yearly_series'][0]['value'], 95)
        self.assertEqual(card['linked_yearly_series'][0]['payment'], 95)

    def test_linked_collection_rejects_unmapped_and_unknown_amounts(self):
        accruals = [
            {'date': '2026-01-15', 'account': '62.01', 'analytic_key': 'unknown',
             'type': 'accrual', 'accrued': 10, 'accrual_document': 'ACC-1',
             'source': '1С', 'version': '2026.01'},
            {'date': '2026-01-16', 'account': '62.01', 'analytic_key': 'buyer',
             'type': 'accrual', 'accrued': None, 'accrual_document': 'ACC-2',
             'source': '1С', 'version': '2026.01'},
        ]
        card = {item['id']: item for item in build_key_indicators(
            build_indicator_source({}), date='2026-01-31',
            collection_accruals=accruals)['cards']}[
                'collection']
        linked = card['consumer_collection']
        self.assertFalse(linked['available'])
        self.assertEqual(len(linked['rejected']), 2)
        self.assertIsNone(linked['value'])
        self.assertEqual(linked['accrual_sources'], [])

    def test_collection_mapping_is_resolved_on_each_operation_date(self):
        source = build_indicator_source({
            '__consumer_collection__': {'accruals': [
                {'date': '2026-01-15', 'account': '62.01', 'analytic_key': 'buyer',
                 'type': 'accrual', 'accrued': 40, 'accrual_document': 'ACC-1'},
                {'date': '2026-02-15', 'account': '62.01', 'analytic_key': 'buyer',
                 'type': 'accrual', 'accrued': 60, 'accrual_document': 'ACC-2'},
            ], 'allocations': [
                {'date': '2026-01-20', 'payment_document': 'PAY-1',
                 'accrual_document': 'ACC-1', 'type': 'payment', 'allocated_amount': 20},
                {'date': '2026-02-10', 'payment_document': 'PAY-2',
                 'accrual_document': 'ACC-2', 'type': 'payment', 'allocated_amount': 45},
            ]}})
        mappings = [
            {'account': '62.01', 'analytic_key': 'buyer', 'category': 'population',
             'effective_from': '2026-01-01', 'effective_to': '2026-01-31'},
            {'account': '62.01', 'analytic_key': 'buyer', 'category': 'budget',
             'effective_from': '2026-02-01', 'effective_to': '2026-12-31'},
        ]
        card = {item['id']: item for item in build_key_indicators(
            source, period='month', date_from='2026-01-20',
            date='2026-02-15', debtor_mappings=mappings)['cards']}['collection']
        linked = card['consumer_collection']
        self.assertEqual(linked['value'], 65)
        self.assertEqual(
            next(row for row in linked['categories']
                 if row['id'] == 'population')['value'], 50)
        self.assertEqual(
            next(row for row in linked['categories']
                 if row['id'] == 'budget')['value'], 75)
        self.assertFalse(linked['reconciled'])

    def test_collection_cohort_excludes_future_payment_until_cutoff(self):
        source = build_indicator_source({
            '__consumer_collection__': {
                'accruals': [{
                    'date': '2026-01-15', 'account': '62.01',
                    'analytic_key': 'buyer', 'accrued': 100,
                    'type': 'accrual',
                    'accrual_document': 'ACC-1',
                }],
                'allocations': [{
                    'date': '2026-02-10', 'payment_document': 'PAY-1',
                    'accrual_document': 'ACC-1', 'allocated_amount': 80,
                    'type': 'payment',
                }],
            }})
        mappings = [{
            'account': '62.01', 'analytic_key': 'buyer',
            'category': 'population',
            'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        }]
        january = {item['id']: item for item in build_key_indicators(
            source, period='month', date='2026-01-15',
            debtor_mappings=mappings)['cards']}['collection']
        through_february = {item['id']: item for item in build_key_indicators(
            source, period='month', date='2026-02-10',
            date_from='2026-01-15', debtor_mappings=mappings)['cards']}[
                'collection']
        self.assertEqual(january['consumer_collection']['value'], 0)
        self.assertEqual(through_february['consumer_collection']['value'], 80)
        self.assertEqual(january['value'], 0)
        self.assertEqual(january['measurement_basis'], 'linked_accrual_payments')
