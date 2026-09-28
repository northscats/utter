import json
import unittest
from types import SimpleNamespace

from max_readable_changes import describe


class ReadableChangesTests(unittest.TestCase):
    def change(self, old, new):
        return SimpleNamespace(
            field_key='funding',
            field_label='Объем финансирования,\nтыс. руб. на 01.01.2023\n'
                        'Средства бюджета Московской области',
            old_json=json.dumps(old), new_json=json.dumps(new))

    def test_zero_to_amount_retains_year_unit_and_source(self):
        text = describe(self.change(
            {'kind': 'money', 'value': '0'},
            {'kind': 'money', 'value': '5831.79'}))
        self.assertIn('Изменилось с 0 на 5 831,79.', text)
        self.assertIn('тыс. руб. на 01.01.2023 Средства бюджета Московской области', text)
        self.assertNotIn('Строка', text)
        self.assertNotIn('→', text)

    def test_unknown_is_not_zero_and_removed_amount_is_explicit(self):
        for kind in ('blank', 'dash'):
            text = describe(self.change({'kind': kind}, {'kind': 'money', 'value': '5'}))
            self.assertIn('Раньше: не указано.', text)
            self.assertNotIn('с 0', text)
        text = describe(self.change({'kind': 'money', 'value': '0'}, None))
        self.assertIn('Раньше: 0.', text)
        self.assertIn('значение не указано', text)

    def test_non_financial_changes_are_readable(self):
        change = self.change('Старое название', 'Новое название')
        change.field_label = 'Наименование объекта'
        self.assertIn('с Старое название на Новое название', describe(change))
        change.field_key = 'object_added'
        self.assertEqual(describe(change), 'Объект добавлен в перечень.')

    def test_transfer_explains_only_recorded_codes(self):
        change = self.change(None, None)
        change.field_key = 'row_transferred'
        change.source_json = json.dumps({
            'old_programme': {'dm_code': '05', 'gp_code': '01'},
            'new_programme': {'dm_code': '12', 'gp_code': '01'}})
        text = describe(change)
        self.assertIn('Код ДМ: 05 → 12', text)
        self.assertNotIn('Код ГП', text)
        change.source_json = '{}'
        self.assertIn('не указано', describe(change))