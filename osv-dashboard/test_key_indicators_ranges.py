"""Focused contracts for compact facts and section payloads."""
import unittest

from key_indicators import (build_daily_prefix_facts, build_indicator_source,
                            build_key_indicators, daily_prefix_range)


class KeyIndicatorRangeContractTests(unittest.TestCase):
    def source(self):
        return build_indicator_source({
            '50': {'daily': [
                {'date': '2024-01-01', 'obd': 10, 'obk': 2},
                {'date': '2024-01-02', 'obd': 3, 'obk': None},
            ], 'balance_observations': [
                {'date': '2024-01-01', 'skd': 10, 'skk': 0},
                {'date': '2024-01-02', 'skd': 13, 'skk': 0},
            ]},
        })

    def test_prefix_preserves_missing_as_unknown(self):
        prefix = build_daily_prefix_facts(self.source())['50']
        self.assertEqual(prefix['2024-01-02']['obd'], 13.0)
        self.assertFalse(prefix['2024-01-02']['obk_known'])
        self.assertEqual(prefix['2024-01-02']['balance'],
                         {'debit': 13, 'credit': 0})
        self.assertIsNone(daily_prefix_range(prefix, '2024-01-01', '2024-01-02'))
        self.assertEqual(daily_prefix_range(prefix, '2024-01-01', '2024-01-01'),
                         {'obd': 10.0, 'obk': 2.0})

    def test_cards_section_omits_heavy_series(self):
        payload = build_key_indicators(
            self.source(), period='month', date='2024-01-02', section='cards')
        self.assertEqual(set(payload), {'period', 'cards', 'quality',
                                        'methodology'})
        self.assertTrue(all('series' not in card for card in payload['cards']))


if __name__ == '__main__':
    unittest.main()