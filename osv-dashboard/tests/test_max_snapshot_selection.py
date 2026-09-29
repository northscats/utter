"""Immutable source regression tests; no real MAX transport."""
import io
import json
from datetime import date
from unittest.mock import Mock, patch

from openpyxl import load_workbook

from test_max_digest import MaxDigestTests
from database import (db, Setting, LimitVersion, LimitDataset, LimitObject,
                      LimitComparison, LimitChange, MaxLimitsOutbox, MaxLimitsUin)
from max_dataset_scope import (select_version, selected_version_id,
                               migrate_selected_version, selected_dataset_id)
import max_bot_config
import max_digest
import max_limits
import max_registration
from max_object_browser import browse
from pathlib import Path


class SnapshotSelectionTests(MaxDigestTests):
    def test_all_paths_stay_on_selected_file_and_cancel_after_switch(self):
        with patch.object(max_bot_config, 'CONFIG', Path(self.temp.name) / 'private' / 'max-bot.json'):
            max_bot_config.save('A' * 40, '-123')
            max_bot_config.set_enabled(True)
            with self.app.app_context():
                old = db.session.get(LimitVersion, 2)
                snapshot = json.loads(old.snapshot_json)
                snapshot['columns'] = [{'key': 'funding', 'category': 'annual_funding',
                                        'year': 2026, 'label': 'Финансирование 2026'}]
                snapshot['rows'][0]['cells']['funding'] = {'kind': 'money', 'value': '222'}
                old.snapshot_json = json.dumps(snapshot)
                newer = LimitVersion(
                    dataset_id=1, previous_version_id=2, uploaded_by=1,
                    source_date=date(2026, 2, 1), status='accepted',
                    source_name='newer.xlsx', source_path='', sha256='c' * 64,
                    snapshot_json=json.dumps({'columns': snapshot['columns'], 'rows': [
                        {'uin': '100.200', 'cells': {
                            'object_name': {'value': 'NEW TITLE'},
                            'address': {'value': 'NEW ADDRESS'},
                            'funding': {'kind': 'money', 'value': '999'}}},
                        {'uin': 'NEW-UIN', 'cells': {'object_name': {'value': 'NEW ROW'}}}]}))
                db.session.add(newer)
                db.session.flush()
                db.session.get(LimitDataset, 1).current_version_id = newer.id
                obj = db.session.get(LimitObject, 1)
                obj.title, obj.address = 'REGISTRY TITLE', 'REGISTRY ADDRESS'
                pair = LimitComparison(old_version_id=2, new_version_id=newer.id, status='ready')
                db.session.add(pair)
                db.session.flush()
                db.session.add(LimitChange(comparison_id=pair.id, uin='100.200',
                    field_key='future', field_label='FUTURE', category='annual_funding',
                    source_json='{"year":2026}', old_json='222', new_json='999'))
                db.session.commit()
                self.assertEqual(selected_version_id(), 2)
                self.assertEqual(max_limits.queue_accepted_changes(newer, pair), 0)
                self.assertFalse(max_registration._valid_uin('NEW-UIN'))
                info = max_limits.current_information('100.200')
                self.assertIn('222', info)
                self.assertIn('Старый адрес', info)
                self.assertNotIn('999', info)
                self.assertNotIn('FUTURE', max_limits.accepted_changes('100.200'))
                sub = MaxLimitsUin.query.filter_by(user_id=50).one()
                card = browse(50, f'browse:sources:info:{sub.id}:0')['text']
                self.assertIn('Тестовый объект', card)
                self.assertIn('222', card)
                for data in (max_registration.catalog_bytes(), max_digest.changes_excel_bytes(50)):
                    book = load_workbook(io.BytesIO(data), read_only=True)
                    cells = str(list(book.active.values))
                    book.close()
                    self.assertIn('Тестовый объект', cells)
                    self.assertIn('Старый адрес', cells)
                    self.assertNotIn('NEW', cells)
                    self.assertNotIn('REGISTRY', cells)
                self.assertEqual(max_digest.prepare_digest('a' * 32), (1, 1))
                api = Mock(return_value={'ok': True})
                self.assertEqual(max_limits.drain_outbox(api_call=api), 1)
                self.assertIn('Тестовый объект', api.call_args.args[4]['text'])
                max_digest.prepare_digest('b' * 32)
                pending = MaxLimitsOutbox.query.filter_by(delivered_at=None).one()
                select_version(newer.id)
                self.assertIsNotNone(pending.cancelled_at)
                self.assertEqual(max_limits.drain_outbox(api_call=api), 0)
                self.assertTrue(max_registration._valid_uin('NEW-UIN'))
                self.assertIn('999', max_limits.current_information('100.200'))
                db.session.remove()
                self.assertEqual(selected_version_id(), newer.id)

    def test_migration_once_and_no_implicit_dataset(self):
        with patch.object(max_bot_config, 'CONFIG', Path(self.temp.name) / 'private' / 'max-bot.json'):
            with self.app.app_context():
                db.session.delete(db.session.get(Setting, 'max_limits_version_id'))
                db.session.commit()
                self.assertIsNone(migrate_selected_version())
                Setting.set('max_limits_dataset_id', '1')
                db.session.commit()
                self.assertIsNone(migrate_selected_version())
                db.session.delete(db.session.get(Setting, 'max_limits_version_id'))
                db.session.commit()
                self.assertEqual(migrate_selected_version().id, 2)
                self.assertEqual(selected_dataset_id(), 1)
                Setting.set('max_limits_dataset_id', '999')
                db.session.commit()
                self.assertEqual(migrate_selected_version().id, 2)
                with self.assertRaises(ValueError):
                    select_version(999)