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
        cls.conclusion = (ROOT / '50-1-1-2-018685-2026_26-04-29_11-32-17_signature_1789461292785.xml').read_bytes()
        cls.ssr = (ROOT / 'ССР_Кривцово_1789461305316.gge').read_bytes()
        cls.conclusion_101 = (ROOT / '56-1-1-2-044346-2024_24-08-06_09-12-42_signature_1789537746147.xml').read_bytes()
        cls.ssr_101 = (ROOT / 'Сводный_сметный_расчет_1789537733540.gge').read_bytes()

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
        self.assertEqual(conclusion['climate_zone'], 'IIВ')
        self.assertEqual(conclusion['climate_details'], {
            'climate_zone': 'IIВ',
            'geological_conditions': 'I',
            'wind_region': 'I',
            'snow_region': 'III',
            'seismicity': '5',
        })
        self.assertEqual(
            conclusion['climate_conditions'],
            'Климатический район, подрайон: IIВ\n'
            'Геологические условия: I\n'
            'Ветровой район: I\n'
            'Снеговой район: III\n'
            'Сейсмическая активность (баллов): 5')
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
        self.assertEqual(capacity['normalized_unit'], 'Гкал')
        self.assertEqual(capacity['normalized_value'], Decimal('6.53'))
        pair = parse_pair(self.conclusion, self.ssr)
        self.assertTrue(pair['can_confirm'])
        self.assertEqual(pair['conclusion']['cost_rows'][0]['price_level'], '2025/Q2')

    def test_gge_schema_version_comes_from_file_metadata_each_time(self):
        changed_software = self.ssr.replace(
            b'<Version>2026.1 Windows x64 (', b'<Version>1.01 (')
        self.assertEqual(parse_gge(changed_software)['schema_version'], '3.01')
        self.assertEqual(parse_gge(changed_software)['schema_version'], '3.01')

    def test_summary_estimate_101_pair_month_and_values(self):
        ssr = parse_gge(self.ssr_101)
        self.assertEqual(ssr['schema_version'], '1.01')
        self.assertEqual(ssr['name'],
                         'Строительство БМК 1,8 МВт по ул. Ленинграсдская в г. Соль-Илецке Оребургской области')
        self.assertEqual(str(ssr['approved_date']), '2023-11-01')
        self.assertEqual(ssr['summary']['total'], Decimal('31372.62'))
        self.assertEqual((ssr['price_year'], ssr['price_quarter']), (2023, 4))
        self.assertEqual(len(ssr['chapters']), 14)
        self.assertEqual(len(ssr['items']), 17)
        local = next(item for item in ssr['items']
                     if item['name'] == 'Конструктивные решения')
        self.assertEqual(local['link'], 'Конструктивные решения.gsfx')
        self.assertEqual(local['details']['Building']['WorkersSalary'], '111.13')
        self.assertEqual(local['details']['Building']['LaborCosts'], '0.3401')
        self.assertEqual(local['details']['Equipment']['Price'], '0')
        self.assertEqual(ssr['software_name'], 'ГРАНД-Смета')
        self.assertEqual(ssr['price_month'], 11)
        pair = parse_pair(self.conclusion_101, self.ssr_101)
        self.assertTrue(pair['can_confirm'], pair['diagnostics'])
        capacity = next(item for item in pair['conclusion']['teps']
                        if item['name'] == 'Производственная мощность')
        self.assertEqual(capacity['value'], Decimal('1.8'))
        self.assertEqual(capacity['unit'], 'МВт')
        self.assertEqual(capacity['normalized_value'], Decimal('1800.0'))
        self.assertEqual(capacity['normalized_unit'], 'кВт')
        self.assertFalse(any(row['code'] == 'name_mismatch'
                             for row in pair['diagnostics']))

    def test_different_object_name_is_rejected(self):
        changed = self.ssr_101.replace(
            'Строительство БМК 1,8 МВт по ул. Ленинграсдская в г. Соль-Илецке Оребургской области'.encode(),
            'Строительство школы на 1200 мест в городе Москве'.encode())
        pair = parse_pair(self.conclusion_101, changed)
        self.assertFalse(pair['can_confirm'])
        self.assertTrue(any(row['code'] == 'name_mismatch'
                            for row in pair['diagnostics']))

    def test_unknown_or_conflicting_summary_schema_is_rejected(self):
        unknown = self.ssr_101.replace(
            b'<Version>1.01</Version>', b'<Version>2.01</Version>').replace(
            b'SummaryEstimate-1_01.xsd', b'SummaryEstimate-2_01.xsd')
        conflicting = self.ssr_101.replace(
            b'SummaryEstimate-1_01.xsd', b'SummaryEstimate-3_01.xsd')
        for raw in (unknown, conflicting):
            with self.assertRaises(AnalogParseError) as error:
                parse_gge(raw)
            self.assertEqual(error.exception.code, 'schema')

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