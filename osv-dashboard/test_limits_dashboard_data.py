"""Financial dashboard calculations must not invent money from blank cells."""
import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from flask import Flask

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitObject, LimitVersion,
    User, db,
)
from limits_dashboard_data import dashboard_context


def snapshot(rows):
    return json.dumps({
        'columns': [
            {'key': 'limit_cost', 'label': 'Предельная стоимость объекта',
             'category': 'limit_cost'},
            {'key': 'annual_funding_2027_federal_budget',
             'label': 'Федеральный бюджет, 2027', 'category': 'annual_funding'},
        ],
        'rows': rows,
    })


def report_row(uin, cost, funding=None):
    def money(value):
        return {'kind': 'money', 'value': value} if value is not None else {
            'kind': 'blank', 'value': None}
    return {'uin': uin, 'cells': {
        'limit_cost': money(cost),
        'annual_funding_2027_federal_budget': money(funding),
    }}


class LimitsDashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add(User(id=1, username='analyst', password_hash='unused'))
        db.session.add(LimitDataset(id=1, owner_id=1, name='Основной'))
        db.session.add(LimitDataset(id=2, owner_id=1, name='Чужой'))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def add_version(self, id, rows, *, dataset_id=1, status='accepted',
                    source_date=date(2026, 1, 1), previous=None):
        version = LimitVersion(
            id=id, dataset_id=dataset_id, uploaded_by=1, status=status,
            source_date=source_date, source_name=f'{id}.xlsx',
            source_path=f'/tmp/{id}.xlsx', sha256=f'{id:064x}',
            previous_version_id=previous, snapshot_json=snapshot(rows))
        db.session.add(version)
        db.session.commit()
        return version

    def test_empty_and_nonaccepted_versions_are_not_shown(self):
        dataset = db.session.get(LimitDataset, 1)
        empty = dashboard_context(dataset)
        self.assertFalse(empty['has_data'])
        self.assertEqual('objects_count', empty['metrics'][0]['key'])
        self.assertEqual([], empty['metrics'][0]['points'])
        self.assertFalse(empty['changes']['available'])
        self.add_version(1, [report_row('100.1', '999')], status='review')
        self.add_version(2, [report_row('100.2', '900')], dataset_id=2)
        self.assertEqual([], dashboard_context(dataset)['versions'])

    def test_object_count_default_metric_uses_distinct_uin_and_previous_version(self):
        self.add_version(1, [
            report_row('100.1', None), report_row('100.1', None),
            report_row('100.2', None),
        ])
        self.add_version(2, [
            report_row('100.1', None),
        ], source_date=date(2026, 2, 1), previous=1)
        dataset = db.session.get(LimitDataset, 1)
        dataset.current_version_id = 2
        db.session.commit()
        chart = dashboard_context(dataset)
        count = chart['metrics'][0]
        self.assertEqual('Количество объектов', count['label'])
        self.assertEqual('объектов', count['unit'])
        self.assertEqual([2, 1], count['points'])
        self.assertEqual(1, count['latest'])
        self.assertEqual(-1, count['delta'])
        self.assertIsNone(chart['metrics'][1]['latest'])

    def test_repeated_uin_money_zero_and_missing_are_distinct(self):
        self.add_version(1, [
            report_row('100.1', '0', '10.2'),
            report_row('100.1', '7.5', None),
            report_row('100.2', '2.5', '12.3'),
        ])
        dataset = db.session.get(LimitDataset, 1)
        dataset.current_version_id = 1
        db.session.commit()
        chart = dashboard_context(dataset)
        self.assertEqual(2, chart['versions'][0]['objects'])
        self.assertEqual('objects_count', chart['metrics'][0]['key'])
        cost = next(item for item in chart['metrics'] if item['key'] == 'limit_cost')
        funding = next(item for item in chart['metrics'] if item['key'].startswith('annual_'))
        self.assertEqual([10], cost['points'])
        self.assertIsNone(funding['points'][0], 'partial sum must not masquerade as total')
        self.assertIsNone(cost['delta'])
        self.assertFalse(chart['changes']['available'])

    def test_current_pointer_controls_comparison_and_per_metric_delta(self):
        self.add_version(1, [report_row('100.1', '0', '10')])
        self.add_version(2, [report_row('100.1', '20', '20')],
                         source_date=date(2026, 2, 1), previous=1)
        self.add_version(3, [report_row('100.1', '5', '30')],
                         source_date=date(2026, 3, 1), previous=1)
        # Version #2 remains current even though #3 has a newer source date.
        dataset = db.session.get(LimitDataset, 1)
        dataset.current_version_id = 2
        db.session.add(LimitObject(id=1, dataset_id=1, uin='100.1', title='Библиотека'))
        db.session.add(LimitComparison(id=1, old_version_id=1, new_version_id=2))
        db.session.flush()
        db.session.add_all([
            LimitChange(comparison_id=1, uin='100.1', category='limit_cost',
                        field_key='limit_cost', absolute_delta=Decimal('12')),
            LimitChange(comparison_id=1, uin='100.1', category='limit_cost',
                        field_key='limit_cost', absolute_delta=Decimal('8')),
            LimitChange(comparison_id=1, uin='100.1', category='annual_funding',
                        field_key='annual_funding_2027_federal_budget',
                        absolute_delta=Decimal('10')),
        ])
        db.session.commit()
        chart = dashboard_context(dataset)
        self.assertEqual([1, 2, 3], [v['id'] for v in chart['versions']])
        self.assertEqual('01.02.2026 · №2', chart['current_label'])
        cost = next(item for item in chart['metrics'] if item['key'] == 'limit_cost')
        self.assertEqual([0, 20, 5], cost['points'])
        self.assertEqual(20, cost['latest'])
        self.assertEqual(20, cost['delta'])
        self.assertEqual(3, chart['changes']['total'])
        self.assertEqual(1, chart['current_objects'])
        self.assertEqual(1, chart['changes']['objects'])
        self.assertEqual([12, 10, 8], [r['delta'] for r in chart['changes']['ranking']])
        self.assertEqual('Библиотека', chart['changes']['ranking'][0]['title'])

    def test_annual_only_ranking_preserves_year_source_and_rows(self):
        self.add_version(1, [report_row('100.1', '100', '0')])
        self.add_version(2, [report_row('100.1', '100', '50')], previous=1,
                         source_date=date(2026, 2, 1))
        dataset = db.session.get(LimitDataset, 1)
        dataset.current_version_id = 2
        db.session.add(LimitComparison(id=1, old_version_id=1, new_version_id=2))
        db.session.flush()
        for year, source, delta, row in [
            (2027, 'federal_budget', '50', 10),
            (2028, 'regional_budget', '-50', 11),
            (2027, 'federal_budget', '5', 12),
            (2029, 'federal_budget', None, 13),
            (2030, 'federal_budget', '0', 14),
        ]:
            db.session.add(LimitChange(
                comparison_id=1, uin='100.1', category='annual_funding',
                field_key=f'annual_funding_{year}_{source}',
                field_label='Финансирование',
                source_json=json.dumps(dict(year=year, source=source, new_row=row)),
                absolute_delta=Decimal(delta) if delta is not None else None))
        db.session.commit()
        changes = dashboard_context(dataset)['changes']
        self.assertEqual(3, changes['ranking_total'])
        self.assertEqual(1, changes['ranking_unknown'])
        self.assertEqual([50, -50, 5], [r['delta'] for r in changes['ranking']])
        first = changes['ranking'][0]
        self.assertIn('2027', first['field_label'])
        self.assertIn('federal_budget', first['field_label'])
        self.assertIn('строка 10', first['field_label'])
        self.assertEqual('тыс. руб.', first['unit'])
        self.assertEqual('annual_funding', first['category'])

    def test_removed_controls_and_object_only_trend(self):
        root = Path(__file__).resolve().parent
        files = (root / 'templates/limits_dashboard.html').read_text()
        template = (root / 'templates/limits_analytics.html').read_text()
        script = (root / 'static/limits_analytics.js').read_text()
        self.assertNotIn('Контрольные количества', files)
        self.assertNotIn("url_for('limit_version_review'", files)
        self.assertNotIn('id="la-metric"', template)
        self.assertNotIn("getElementById('la-metric')", script)
        self.assertIn("drawTrend(metrics.find(function (m) { return m.key === 'objects_count'; }))", script)


if __name__ == '__main__':
    unittest.main()