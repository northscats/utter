# -*- coding: utf-8 -*-
"""Focused fixture and security checks for task 117's parser."""
import pathlib
import unittest
from decimal import Decimal

from analog_parser import AnalogParseError, parse_conclusion, parse_gge, parse_pair


ROOT = pathlib.Path(__file__).parent.parent / 'attached_assets'


class AnalogParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conclusion = next(ROOT.glob('*signature_*.xml')).read_bytes()
        cls.ssr = next(ROOT.glob('*.gge')).read_bytes()

    def test_attached_pair_exact_fields(self):
        conclusion = parse_conclusion(self.conclusion)
        ssr = parse_gge(self.ssr)
        self.assertEqual(conclusion['conclusion_number'], '50-1-1-2-018685-2026')
        self.assertEqual(str(conclusion['approval_date']), '2026-04-29')
        self.assertEqual(conclusion['work_type'], 'Капитальный ремонт')
        self.assertEqual(conclusion['cadastral_number'], '50:09:0030701:4627')
        self.assertEqual(conclusion['address'],
                         'Солнечногорский район, с.п. Кривцовское, д. Кривцово')
        self.assertEqual(conclusion['region'], '50')
        self.assertEqual(conclusion['climate_conditions'], 'IIВ I I III 5')
        self.assertIn('Котельная Кривцово', conclusion['construction_object'])
        self.assertEqual(conclusion['cost_rows'][0]['vat'], '22%')
        self.assertTrue(conclusion['cost_rows'][0]['footnote'])
        self.assertEqual(str(ssr['summary']['total']), '2837.88')
        self.assertEqual(len(conclusion['documents']), 24)
        self.assertEqual(len(conclusion['cost_rows']), 13)
        self.assertEqual(len(conclusion['teps']), 3)
        capacity = next(item for item in conclusion['teps']
                        if item['name'] == 'Мощность котельной')
        self.assertEqual(capacity['value_text'], '6,53')
        self.assertEqual(capacity['value'], Decimal('6.53'))
        self.assertEqual(capacity['unit'], 'Гкал')
        self.assertEqual(capacity['normalized_unit'], 'кВт')
        self.assertEqual(capacity['normalized_value'],
                         Decimal('6.53') * Decimal('1162.222222222222'))
        pair = parse_pair(self.conclusion, self.ssr)
        self.assertTrue(pair['can_confirm'])
        self.assertEqual(pair['conclusion']['cost_rows'][0]['price_level'], '2025/Q2')

    def test_gge_schema_version_comes_from_file_metadata_each_time(self):
        changed_software = self.ssr.replace(
            b'<Version>2026.1 Windows x64 (', b'<Version>1.01 (')
        self.assertEqual(parse_gge(changed_software)['schema_version'], '3.01')
        self.assertEqual(parse_gge(changed_software)['schema_version'], '3.01')

    def test_xxe_and_malformed_are_rejected(self):
        with self.assertRaises(AnalogParseError):
            parse_gge(b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><Construction/>')
        with self.assertRaises(AnalogParseError):
            parse_gge(b'<Construction><Chapter1></Construction>')

    def test_negative_result_blocks_pair(self):
        negative = b'<Conclusion><ExaminationResult>2</ExaminationResult></Conclusion>'
        result = parse_pair(negative, self.ssr)
        self.assertFalse(result['can_confirm'])
        self.assertIn('negative_conclusion',
                      [item['code'] for item in result['diagnostics']])


if __name__ == '__main__':
    unittest.main()