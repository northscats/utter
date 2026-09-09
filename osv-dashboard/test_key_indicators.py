# -*- coding: utf-8 -*-
import unittest

from key_indicators import (build_indicator_source, build_key_indicators,
                            revenue_analytic_key)


def account(acc, daily=(), rows=(), top=None, period='за январь 2026 г.'):
    if top is None:
        top = dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0)
    return dict(acc=acc, daily=list(daily), rows=list(rows), top=top,
                period=period, warnings=[], check_ok=True)


def day(dt, p3='', p1='', obd=0, obk=0, **balance):
    return dict(date=dt, month=dt[:7], path1=p1, path2='', path3=p3,
                obd=obd, obk=obk, **balance)


class KeyIndicatorTests(unittest.TestCase):
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
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['ar']
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
        ar = {x['id']: x for x in build_key_indicators(src)['cards']}['ar']
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
        self.assertEqual(card['details'][0]['value'], 90)
        self.assertEqual(card['turnover_ratio'], 90)
        self.assertEqual(card['turnover_series'][0]['value'], 90)
        self.assertIn('не собираемость', card['details'][0]['label'])

    def test_collection_analytics_do_not_double_count_parent_rows(self):
        rows = [
            day('2026-01-31', '62.01', p1='Население', obd=100, obk=80),
            day('2026-01-31', '62.01.1', p1='Население', obd=100, obk=80),
        ]
        src = build_indicator_source({'62': account('62', rows)})
        card = {x['id']: x for x in build_key_indicators(src)['cards']}['collection']
        self.assertIsNone(card['value'])
        self.assertEqual(card['turnover_ratio'], 80)
        self.assertEqual(len(card['analytics']), 1)
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


if __name__ == '__main__':
    unittest.main()