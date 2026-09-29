"""Isolated regression: slow durable rotation must not monopolize parsing."""
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import date
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='limits_worker_progress_')
os.environ['DATABASE_URL'] = 'sqlite:///' + _TEMP.name + '/progress.sqlite'
os.environ['DURABLE_BACKUP_DIR'] = _TEMP.name + '/durable'
os.environ.setdefault('ENCRYPTION_KEY', 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')

import app as application
import max_bot_config
from database import db, User, LimitVersion, LimitDataset, LimitRowSnapshot, Setting
from limits_service import process_version, resolve_upload_dataset, stage_upload
from werkzeug.datastructures import FileStorage


class LimitsWorkerProgressTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        self.config_patch = patch.object(
            max_bot_config, 'CONFIG', Path(_TEMP.name) / 'private' / 'max.json')
        self.config_patch.start()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
            user = User(username='progress', role='admin', password_hash='unused')
            db.session.add(user)
            db.session.commit()
            dataset = resolve_upload_dataset(user.id)
            self.dataset_id = dataset.id
            self.ids = []
            for number in (1, 2):
                version, _ = stage_upload(
                    dataset, FileStorage(stream=io.BytesIO(f'workbook {number}'.encode())),
                    f'{number}.xlsx', date(2026, 1, number), user.id, _TEMP.name)
                self.ids.append(version.id)
        self.snapshot = {
            'columns': [{'key': 'object_name', 'label': 'Название', 'category': 'requisite'}],
            'rows': [{'uin': '301.001', 'row': 3, 'cells': {
                'object_name': {'kind': 'text', 'value': 'Regression object'}}}],
            'warnings': [],
        }

    def tearDown(self):
        self.config_patch.stop()

    def test_blocked_rotation_does_not_block_next_parser(self):
        backup_entered = threading.Event()
        release_backup = threading.Event()
        second_parsed = threading.Event()
        workers = []

        def slow_backup(*args, **kwargs):
            backup_entered.set()
            if not release_backup.wait(10):
                raise TimeoutError('test backup was not released')

        # Use the actual service pipeline, including all existing MAX/service
        # locks. Only the parser and expensive external backup are substituted.
        def process(identifier):
            result = process_version(identifier)
            if identifier == self.ids[1]:
                second_parsed.set()
            return result

        with application.app.app_context():
            Setting.set('limits_rotation_needs_backup', '1')
            db.session.commit()
        with patch.object(application, '_limits_parse_slots', threading.Semaphore(1)), \
                patch.object(application, '_schedule_limits_rotation_backup', side_effect=slow_backup), \
                patch.object(application, '_schedule_durable_safety_backup'), \
                patch('limits_parser.parse_xlsx', return_value=self.snapshot), \
                patch('limits_service.process_version', side_effect=process):
            try:
                workers.append(application._limit_parse_in_background(self.ids[0]))
                self.assertTrue(backup_entered.wait(5), 'first upload never reached backup')
                workers.append(application._limit_parse_in_background(self.ids[1]))
                self.assertTrue(second_parsed.wait(5), 'rotation backup holds the parser slot')
                with application.app.app_context():
                    self.assertEqual('accepted', db.session.get(LimitVersion, self.ids[1]).status)
                    from durable_data import _snapshot_rows
                    import json
                    rows = _snapshot_rows([LimitVersion])['LimitVersion']
                    self.assertTrue(all(json.loads(row['progress_json'])['stage'] == 'applying'
                                        for row in rows))
                    self.assertEqual(self.ids[1], db.session.get(
                        LimitDataset, self.dataset_id).current_version_id)
            finally:
                release_backup.set()
                for worker in workers:
                    worker.join(10)
                    self.assertFalse(worker.is_alive())

    def test_interrupted_parse_is_recovered_once_under_nested_max_lock(self):
        with application.app.app_context():
            version = db.session.get(LimitVersion, self.ids[0])
            version.status = 'parsing'
            db.session.commit()
        errors = []

        def retry():
            with application.app.app_context():
                try:
                    # Same nesting used by publication -> queue_accepted_changes.
                    with max_bot_config.locked():
                        process_version(self.ids[0])
                except Exception as exc:
                    errors.append(exc)

        with patch('limits_parser.parse_xlsx', return_value=self.snapshot) as parser:
            workers = [threading.Thread(target=retry, daemon=True) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
                self.assertFalse(worker.is_alive(), 'nested MAX/service lock deadlocked')
            self.assertEqual([], errors)
            self.assertEqual(1, parser.call_count)
        with application.app.app_context():
            self.assertEqual('accepted', db.session.get(LimitVersion, self.ids[0]).status)
            self.assertEqual(1, LimitRowSnapshot.query.filter_by(
                version_id=self.ids[0]).count())


if __name__ == '__main__':
    unittest.main()