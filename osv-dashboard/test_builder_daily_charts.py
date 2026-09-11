# -*- coding: utf-8 -*-
"""Focused regressions for sparse legacy daily chart inputs."""
import unittest

from builder import build_daily_charts, build_daily_temporal


def day(value, **fields):
    return dict(date=value, month=value[:7], **fields)


class DailyChartBuilderTests(unittest.TestCase):
    def test_missing_account_dates_are_null_and_explicit_zero_survives(self):
        result = build_daily_charts({
            '51': {
                'daily': [
                    day('2026-05-01', obd=0),
                    day('2026-05-03', obd=125, obk=0),
                ],
            },
            '62': {
                'daily': [
                    day('2026-05-01', obd=0, obk=0, skd=10, skk=0),
                    day('2026-05-03', obd=0, obk=0, skd=0, skk=0),
                ],
            },
        })

        self.assertEqual(
            result['dates'],
            ['2026-05-01', '2026-05-02', '2026-05-03'],
        )
        self.assertEqual(result['accounts']['51']['obd'], [0, None, 125])
        self.assertEqual(result['accounts']['51']['obk'], [None, None, 0])
        self.assertEqual(result['accounts']['62']['balance'], [10, None, 0])

    def test_one_unknown_balance_side_does_not_become_zero(self):
        result = build_daily_charts({
            '62': {
                'daily': [
                    day('2026-05-01', obd=0, obk=0, skd=10),
                    day('2026-05-02', obd=0, obk=0, skd=0, skk=0),
                ],
            },
        })

        self.assertIsNone(result['accounts']['62']['balance'][0])
        self.assertEqual(result['accounts']['62']['balance'][1], 0)

    def test_daily_temporal_values_are_not_monthly_distributions(self):
        charts = build_daily_charts({
            '90': {
                'daily': [
                    day('2026-05-01', obd=70, obk=100),
                    day('2026-05-03', obd=0, obk=0),
                ],
            },
            '26': {
                'daily': [day('2026-05-01', obd=0, obk=5)],
            },
            '51': {
                'daily': [day('2026-05-01', obd=20, obk=3)],
            },
        })
        temporal = build_daily_temporal(
            {
                '90': {'daily': [
                    day('2026-05-01', obd=70, obk=100),
                    day('2026-05-03', obd=0, obk=0),
                ]},
                '26': {'daily': [day('2026-05-01', obd=0, obk=5)]},
                '51': {'daily': [day('2026-05-01', obd=20, obk=3)]},
            },
            charts,
            has_path3=False,
        )

        self.assertEqual(temporal['cash']['inn'], [20, None, None])
        self.assertEqual(temporal['cash']['out'], [3, None, None])
        self.assertEqual(temporal['pl']['rev'], [100, None, 0])
        self.assertEqual(temporal['pl']['cost'], [70, None, 0])
        self.assertEqual(temporal['pl']['adm'], [5, None, None])
        self.assertEqual(temporal['pl']['nds'], [None, None, None])
        self.assertEqual(temporal['pl']['sales'], [None, None, None])
        self.assertEqual(temporal['pl']['net_rev'], [None, None, None])

    def test_unknown_requested_field_stays_null_in_temporal_component(self):
        charts = build_daily_charts({
            '90': {
                'daily': [day('2026-05-01', obd=5, obk=None)],
            },
        })
        temporal = build_daily_temporal(
            {'90': {'daily': [
                day('2026-05-01', obd=5, obk=None),
            ]}},
            charts,
            has_path3=False,
        )

        self.assertEqual(charts['accounts']['90']['obd'], [5])
        self.assertEqual(charts['accounts']['90']['obk'], [None])
        self.assertEqual(temporal['pl']['rev'], [None])

    def test_filtered_component_does_not_turn_null_source_into_zero(self):
        data = {
            '90': {'daily': [
                day('2026-05-01', obd=5, obk=None,
                    path3='90.01.1'),
                day('2026-05-01', obd=10, obk=10,
                    path3='90.02.1'),
            ]},
        }
        charts = build_daily_charts(data)
        temporal = build_daily_temporal(data, charts, has_path3=True)

        self.assertEqual(temporal['pl']['rev'], [None])
        self.assertEqual(temporal['pl']['cost'], [10])

    def test_net_revenue_requires_observed_vat(self):
        data = {'90': {'daily': [
            day('2026-05-01', obk=100, obd=0, path3='90.01'),
            day('2026-05-01', obk=0, obd=20, path3='90.03'),
            day('2026-05-02', obk=100, obd=0, path3='90.01'),
        ]}}
        temporal = build_daily_temporal(
            data, build_daily_charts(data), has_path3=True)
        self.assertEqual(temporal['pl']['rev'], [100, 100])
        self.assertEqual(temporal['pl']['net_rev'], [80, None])


if __name__ == '__main__':
    unittest.main()