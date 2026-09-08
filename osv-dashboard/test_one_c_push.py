# -*- coding: utf-8 -*-
"""Изолированные проверки строгого протокола 1С (без подключения к БД)."""
import unittest

from one_c import (MAX_BODY, MAX_DEPTH, NumberLexeme, StrictJSONError, _cell,
                   _columns, _depth, parse_strict)


class OneCStrictJsonTest(unittest.TestCase):
    def test_precision_and_scalar_kinds_are_preserved(self):
        value, raw = parse_strict(
            b'{"n":-12345678901234567890.4500,"account":"001","z":0}')
        self.assertEqual(raw, '{"n":-12345678901234567890.4500,'
                              '"account":"001","z":0}')
        self.assertIsInstance(value['n'], NumberLexeme)
        self.assertEqual(_cell(value['n']),
                         {'kind': 'number',
                          'text': '-12345678901234567890.4500'})
        self.assertEqual(_cell(value['account']),
                         {'kind': 'string', 'text': '001'})

    def test_duplicate_keys_and_trailing_junk_are_rejected(self):
        for body in (b'{"x":1,"x":2}', b'{"x":1} explanation'):
            with self.subTest(body=body):
                with self.assertRaises(StrictJSONError):
                    parse_strict(body)

    def test_utf8_is_strict(self):
        with self.assertRaises(StrictJSONError):
            parse_strict(b'{"x":"\xff"}')

    def test_depth_budget(self):
        allowed = 0
        for _ in range(MAX_DEPTH - 1):
            allowed = [allowed]
        _depth(allowed)
        with self.assertRaises(StrictJSONError):
            _depth([allowed])

    def test_union_uses_late_keys_and_numeric_analytics_order(self):
        rows = [
            {'Организация': 'A', 'Аналитика10': 'x', 'Значение': NumberLexeme('1')},
            {'Аналитика2': 'y', 'ПозднееПоле': None},
            {'Аналитика1': 'z'},
        ]
        self.assertEqual(
            _columns(rows),
            ['Организация', 'Аналитика1', 'Аналитика2', 'Аналитика10',
             'Значение', 'ПозднееПоле'])

    def test_nested_values_are_safe_json_text(self):
        value, _ = parse_strict(
            '{"x":{"html":"<script>","n":1.2300}}'.encode('utf-8'))
        cell = _cell(value['x'])
        self.assertEqual(cell['kind'], 'json')
        self.assertEqual(cell['text'], '{"html":"<script>","n":1.2300}')

    def test_documented_request_budget_is_eight_mib(self):
        self.assertEqual(MAX_BODY, 8 * 1024 * 1024)

    def test_sample_shape_can_be_split_by_six_inns_without_precision_loss(self):
        rows = [
            {'ОрганизацияИНН': inn, 'Значение': NumberLexeme('-0.0100')}
            for inn in ('1', '2', '3', '4', '5', '6')
            for _ in range(2)
        ]
        packets = {
            inn: [row for row in rows if row['ОрганизацияИНН'] == inn]
            for inn in {row['ОрганизацияИНН'] for row in rows}
        }
        self.assertEqual(len(packets), 6)
        self.assertTrue(all(len({r['ОрганизацияИНН'] for r in packet}) == 1
                            for packet in packets.values()))
        self.assertEqual(_cell(rows[0]['Значение'])['text'], '-0.0100')

    def test_raw_mixed_inns_are_detectable(self):
        value, _ = parse_strict(
            '{"ErrorCode":0,"ErrorDescription":"","Result":['
            '{"ОрганизацияИНН":"1"},{"ОрганизацияИНН":"2"}]}'.encode('utf-8'))
        self.assertEqual(
            {row['ОрганизацияИНН'] for row in value['Result']}, {'1', '2'})


if __name__ == '__main__':
    unittest.main()