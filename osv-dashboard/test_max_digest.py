"""Isolated tests for idempotent administrator-triggered MAX digests."""
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask

from database import (
    LimitChange, LimitComparison, LimitDataset, LimitObject, LimitVersion,
    MaxLimitsOutbox, MaxLimitsUin, Setting, User, db,
)
import max_bot_config
import max_digest


class MaxDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='max-digest-test-')
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(self.temp.name) / 'test.db'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()
            db.session.add(User(id=1, username='admin', password_hash='x', role='admin'))
            db.session.add(LimitDataset(
                id=1, name='Тестовый набор', scope='', owner_id=1,
                current_version_id=None))
            db.session.flush()
            db.session.add_all([
                LimitVersion(
                    id=1, dataset_id=1, uploaded_by=1, source_date=date(2025, 1, 1),
                    status='accepted', source_name='old.xlsx', source_path='old.xlsx',
                    sha256='a' * 64),
                LimitVersion(
                    id=2, dataset_id=1, previous_version_id=1, uploaded_by=1,
                    source_date=date(2026, 1, 1), status='accepted',
                    source_name='new.xlsx', source_path='new.xlsx', sha256='b' * 64),
            ])
            db.session.flush()
            db.session.get(LimitDataset, 1).current_version_id = 2
            db.session.add(LimitComparison(
                id=1, old_version_id=1, new_version_id=2,
                status='ready', summary_json='{}'))
            db.session.add(LimitObject(
                id=1, dataset_id=1, uin='100.200', title='Тестовый объект'))
            db.session.add(LimitChange(
                id=1, comparison_id=1, object_id=1, uin='100.200',
                category='annual_funding', field_key='annual_funding_2026_amount',
                field_label='Финансирование', source_json='{"year": 2026}'))
            db.session.add(MaxLimitsUin(
                user_id=50, uin='100.200', status='approved'))
            db.session.get(LimitVersion, 1).snapshot_json = '{"rows":[]}'
            db.session.get(LimitVersion, 2).snapshot_json = json.dumps({'rows': [
                {'uin': '100.200', 'cells': {
                    'object_name': {'value': 'Тестовый объект'},
                    'address': {'value': 'Старый адрес'}}}]})
            Setting.set('max_limits_version_id', '2')
            Setting.set('max_limits_min_funding_year', '2026')
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_new_confirmed_actions_queue_identical_text_and_retry_is_idempotent(self):
        first_run = 'a' * 32
        second_run = 'b' * 32
        config_patch = patch.object(
            max_bot_config, 'CONFIG', Path(self.temp.name) / 'private' / 'max-bot.json')
        config_patch.start()
        self.addCleanup(config_patch.stop)
        max_bot_config.save('A' * 40, '-123')
        max_bot_config.set_enabled(True)
        api = Mock(return_value={'ok': True})
        with self.app.app_context():
            self.assertEqual((1, 1), max_digest.prepare_digest(first_run))
            self.assertEqual((0, 0), max_digest.prepare_digest(first_run))
            from max_limits import drain_outbox
            self.assertEqual(1, drain_outbox(api_call=api))
            self.assertEqual((1, 1), max_digest.prepare_digest(second_run))
            self.assertEqual(1, drain_outbox(api_call=api))

            rows = MaxLimitsOutbox.query.order_by(MaxLimitsOutbox.id).all()
            self.assertEqual(2, len(rows))
            self.assertEqual([
                f'{max_digest.PREFIX}2026:{first_run}:0',
                f'{max_digest.PREFIX}2026:{second_run}:0',
            ], [row.uin for row in rows])
            self.assertEqual(rows[0].text, rows[1].text)
            self.assertEqual(['100.200'], json.loads(rows[0].text))
            self.assertTrue(all(row.delivered_at for row in rows))
            self.assertEqual(2, api.call_count)
            self.assertEqual(api.call_args_list[0].args[4]['text'],
                             api.call_args_list[1].args[4]['text'])

    def test_run_id_generation_and_validation(self):
        with self.app.app_context():
            self.assertEqual((1, 1), max_digest.prepare_digest())
            row = MaxLimitsOutbox.query.one()
            self.assertRegex(row.uin, re.escape(max_digest.PREFIX) + r'2026:[0-9a-f]{32}:0')

            for invalid in ('', 'a' * 31, 'g' * 32, 'a' * 32 + '0', None):
                if invalid is None:
                    continue  # None intentionally requests a server-generated run ID.
                with self.assertRaises(ValueError):
                    max_digest.prepare_digest(invalid)
            self.assertEqual((0, 0), max_digest.prepare_digest(row.uin.split(':')[2].upper()))
            self.assertEqual(1, MaxLimitsOutbox.query.count())

    def test_legacy_and_run_markers_revalidate_and_filter_changes_cancel_delivery(self):
        config_patch = patch.object(
            max_bot_config, 'CONFIG', Path(self.temp.name) / 'private' / 'max-bot.json')
        config_patch.start()
        self.addCleanup(config_patch.stop)
        max_bot_config.save('A' * 40, '-123')
        max_bot_config.set_enabled(True)
        api = Mock(return_value={'ok': True})

        with self.app.app_context():
            self.assertEqual((1, 1), max_digest.prepare_digest('c' * 32))
            legacy = MaxLimitsOutbox(
                user_id=50, version_id=2, uin=f'{max_digest.PREFIX}2026:0',
                text='["100.200"]')
            db.session.add(legacy)
            db.session.commit()

            import max_limits
            self.assertEqual(2, max_limits.drain_outbox(api_call=api))
            sent_texts = [call.args[4]['text'] for call in api.call_args_list]
            self.assertEqual(2, len(sent_texts))
            self.assertTrue(all('Тестовый объект' in text for text in sent_texts))
            self.assertEqual((1, 1), max_digest.prepare_digest('e' * 32))

            # A queued marker from another funding-year selection is cancelled
            # before delivery; neither legacy nor UUID markers bypass this check.
            Setting.set('max_limits_min_funding_year', '2025')
            self.assertEqual((1, 1), max_digest.prepare_digest('d' * 32))
            before_calls = api.call_count
            self.assertEqual(1, max_limits.drain_outbox(api_call=api))
            self.assertEqual(before_calls + 1, api.call_count)
            old_filter = MaxLimitsOutbox.query.filter_by(
                uin=f'{max_digest.PREFIX}2026:{"e" * 32}:0').one()
            selected_filter = MaxLimitsOutbox.query.filter_by(
                uin=f'{max_digest.PREFIX}2025:{"d" * 32}:0').one()
            self.assertIsNotNone(old_filter.cancelled_at)
            self.assertIsNone(selected_filter.cancelled_at)

            Setting.set('max_limits_min_funding_year', '2026')
            self.assertEqual((1, 1), max_digest.prepare_digest('e' * 32))
            self.assertEqual(1, MaxLimitsOutbox.query.filter_by(
                uin=f'{max_digest.PREFIX}2026:{"e" * 32}:0').count())
            self.assertIsNone(old_filter.cancelled_at)


if __name__ == '__main__':
    unittest.main()