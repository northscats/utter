"""Isolated read-only MAX browser tests."""
import json
import os
import tempfile
import unittest
from datetime import date

from flask import Flask

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitObject, LimitVersion,
    MaxLimitsUin, Setting, User, db,
)
from max_object_browser import browse


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(self.temp.name, 'test.db'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(username='browser-test', password_hash='unused')
        db.session.add(user)
        db.session.flush()
        self.owner = user.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.temp.cleanup()

    def source(self, uin, name, snapshot=None):
        dataset = LimitDataset(name=name, scope='', owner_id=self.owner)
        db.session.add(dataset)
        db.session.flush()
        version = LimitVersion(
            dataset_id=dataset.id, uploaded_by=self.owner, source_date=date(2025, 1, 1),
            status='accepted', source_name='source.xlsx', source_path='',
            sha256='a' * 64, snapshot_json=json.dumps(snapshot or {}))
        db.session.add(version)
        db.session.flush()
        dataset.current_version_id = version.id
        obj = LimitObject(dataset_id=dataset.id, uin=uin, title='Школа ' + name,
                          address='Улица 1', is_present=True,
                          last_seen_version_id=version.id)
        db.session.add(obj)
        db.session.flush()
        if not Setting.get('max_limits_dataset_id'):
            Setting.set('max_limits_dataset_id', str(dataset.id))
        return dataset, version, obj

    def subscription(self, user_id, uin, status='approved'):
        row = MaxLimitsUin(user_id=user_id, uin=uin, status=status)
        db.session.add(row)
        db.session.flush()
        return row

    def test_forty_subscriptions_reachable_by_paging(self):
        self.source('CATALOG', 'Selected')
        for i in range(40):
            self.subscription(101, f'UIN-{i:03d}')
        db.session.commit()
        seen = []
        for page in range(5):
            result = browse(101, f'browse:list:info:{page}')
            links = [payload for row in result['buttons'] for _, payload in row
                     if payload.startswith('browse:sources:')]
            self.assertEqual(len(links), 8)
            seen.extend(links)
        self.assertEqual(len(set(seen)), 40)
        self.assertIn('browse:list:info:4',
                      [p for row in browse(101, 'browse:list:info:3')['buttons']
                       for _, p in row])
        changes = browse(101, 'browse:list:changes:0')
        self.assertEqual(len([p for line in changes['buttons'] for _, p in line
                              if p.startswith('browse:sources:changes:')]), 8)
        self.assertIsNone(browse(101, 'menu:home'))
        self.assertIn('Неверная', browse(101, 'browse:' + 'a' * 101)['text'])

    def test_foreign_and_revoked_access_denied_on_every_card(self):
        own = self.subscription(101, 'OWN')
        foreign = self.subscription(202, 'OTHER')
        _, _, own_obj = self.source('OWN', 'A')
        _, _, foreign_obj = self.source('OTHER', 'B')
        db.session.commit()
        for payload in (
            f'browse:sources:info:{foreign.id}:0',
            f'browse:card:info:{foreign.id}:{foreign_obj.id}:0',
            f'browse:card:changes:{own.id}:{foreign_obj.id}:0',
        ):
            self.assertNotIn('Школа B', browse(101, payload)['text'])
        self.assertIn('Школа A', browse(101, f'browse:card:info:{own.id}:{own_obj.id}:0')['text'])
        own.status = 'inactive'
        db.session.commit()
        self.assertIn('недоступна', browse(
            101, f'browse:card:info:{own.id}:{own_obj.id}:0')['text'])

    def test_datasets_and_accepted_changes_are_separate(self):
        subscription = self.subscription(101, 'ONE')
        a, old_a, obj_a = self.source('ONE', 'Источник A')
        b, old_b, obj_b = self.source('ONE', 'Источник B')
        for dataset, old, obj, marker in ((a, old_a, obj_a, 'A-only'),
                                           (b, old_b, obj_b, 'B-only')):
            new = LimitVersion(
                dataset_id=dataset.id, uploaded_by=self.owner, source_date=date(2025, 2, 1),
                status='accepted', source_name='new.xlsx', source_path='',
                sha256='b' * 64, snapshot_json='{}')
            db.session.add(new)
            db.session.flush()
            dataset.current_version_id = new.id
            obj.last_seen_version_id = new.id
            pair = LimitComparison(old_version_id=old.id, new_version_id=new.id,
                                   status='ready')
            db.session.add(pair)
            db.session.flush()
            db.session.add(LimitChange(
                comparison_id=pair.id, uin='ONE', category='field',
                field_key=marker, field_label=marker,
                old_json=json.dumps({'kind': 'money', 'value': '0'}),
                new_json=json.dumps({'kind': 'money', 'value': '1234.50'})))
            db.session.add(LimitChange(
                comparison_id=pair.id, uin='ONE', category='field',
                field_key='extra', field_label='Дополнительное изменение',
                old_json=json.dumps('старое'), new_json=json.dumps('новое')))
        db.session.commit()
        sources = browse(101, f'browse:sources:changes:{subscription.id}:0')
        self.assertNotIn('Источники по УИН', sources['text'])
        self.assertIn('A-only', sources['text'])
        self.assertNotIn('B-only', sources['text'])
        card = browse(101, f'browse:card:changes:{subscription.id}:{obj_a.id}:0')['text']
        self.assertIn('A-only', card)
        self.assertIn('1 234,50', card)
        self.assertNotIn('B-only', card)
        self.assertEqual(card.count('Обновление '), 1)
        self.assertIn('Обновление 01.02.2025 · Было на 01.01.2025', card)
        Setting.set('max_limits_dataset_id', str(b.id))
        db.session.commit()
        stale = browse(101, f'browse:card:changes:{subscription.id}:{obj_a.id}:0')
        self.assertNotIn('A-only', stale['text'])
        reopened = browse(101, f'browse:sources:changes:{subscription.id}:0')
        self.assertIn('B-only', reopened['text'])

    def test_missing_selection_never_picks_arbitrary_dataset(self):
        row = self.subscription(101, 'ONE')
        self.source('ONE', 'A')
        Setting.set('max_limits_dataset_id', '')
        db.session.commit()
        result = browse(101, f'browse:sources:info:{row.id}:0')
        self.assertIn('не выбрал набор', result['text'])

    def test_long_values_are_paginated_without_loss(self):
        long_value = ''.join(str(i % 10) for i in range(9200))
        snapshot = {
            'columns': [
                {'key': 'cost', 'label': 'Стоимость, тыс. руб.',
                 'category': 'limit_cost'},
                {'key': 'missing', 'label': 'Нет суммы, руб.',
                 'category': 'funding_snapshot'},
                {'key': 'zero', 'label': 'Ноль, руб.',
                 'category': 'annual_funding'},
            ],
            'rows': [{'uin': 'ONE', 'row': 9, 'cells': {
                'cost': {'kind': 'text', 'value': long_value},
                'missing': {'kind': 'blank', 'value': None},
                'zero': {'kind': 'money', 'value': '0'},
            }}],
        }
        row = self.subscription(101, 'ONE')
        _, _, obj = self.source('ONE', 'A', snapshot)
        db.session.commit()
        pages = []
        for page in range(10):
            result = browse(101, f'browse:card:info:{row.id}:{obj.id}:{page}')
            if 'На этой странице' in result['text']:
                break
            self.assertLessEqual(len(result['text']), 3800)
            pages.append(result['text'].split('\n', 6)[-1])
        self.assertGreater(len(pages), 2)
        rendered = ''.join(pages)
        value_start = rendered.index('Стоимость, тыс. руб. — ')
        value_end = rendered.index('Нет суммы, руб. — ')
        actual_value = rendered[value_start:value_end].split(' — ', 1)[1]
        self.assertEqual(actual_value.replace('\n', ''), long_value)
        self.assertIn('Нет суммы, руб. — не указано', ''.join(pages))
        self.assertIn('Ноль, руб. — 0', ''.join(pages))
        self.assertNotIn('Строка 9', rendered)


if __name__ == '__main__':
    unittest.main()