"""Isolated local durable restore after automatic worker-driven rotation."""
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='limits_worker_backup_')
os.environ['DATABASE_URL'] = 'sqlite:///' + _TEMP.name + '/worker.sqlite'
os.environ['DURABLE_BACKUP_DIR'] = _TEMP.name + '/durable'
os.environ.setdefault('ENCRYPTION_KEY', 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')

import app as application
import max_bot_config
from database import db, User, LimitVersion, LimitDataset, LimitComparison, Setting
from durable_data import restore
from limits_service import process_version, resolve_upload_dataset, stage_upload
from werkzeug.datastructures import FileStorage


class LimitsWorkerBackupTests(unittest.TestCase):
    def test_rotation_worker_promotes_new_upload_before_restore(self):
        app = application.app
        app.config.update(TESTING=True, DURABLE_BACKUP_ASYNC_TEST=False)
        uploads = Path(_TEMP.name) / 'uploads'
        output = Path(_TEMP.name) / 'output'
        uploads.mkdir(exist_ok=True)
        output.mkdir(exist_ok=True)
        snapshot = {
            'columns': [{'key': 'object_name', 'label': 'Название', 'category': 'requisite'}],
            'rows': [{'uin': '301.001', 'row': 3, 'cells': {
                'object_name': {'kind': 'text', 'value': 'Synthetic object'}}}],
            'warnings': [],
        }
        with patch.object(application, 'UPLOADS', str(uploads)), \
                patch.object(application, 'OUTPUT', str(output)), \
                patch.object(max_bot_config, 'CONFIG', Path(_TEMP.name) / 'private' / 'max.json'), \
                patch('limits_parser.parse_xlsx', return_value=snapshot):
            with app.app_context():
                db.create_all()
                user = User(username='worker-backup', role='admin', password_hash='unused')
                db.session.add(user)
                db.session.commit()
                user_id = user.id
                dataset = resolve_upload_dataset(user_id)
                dataset_id = dataset.id
                for number in range(1, 11):
                    version, _ = stage_upload(dataset, FileStorage(
                        stream=io.BytesIO(f'synthetic workbook {number}'.encode())),
                        f'{number}.xlsx', date(2026, 1, number), user_id, str(uploads))
                    process_version(version.id)
                first_id = LimitVersion.query.order_by(LimitVersion.id).first().id
                initial = application._durable_backup('ten files before automatic rotation')
                self.assertTrue(initial['promoted'])
                newest, _ = stage_upload(dataset, FileStorage(
                    stream=io.BytesIO(b'synthetic newest workbook')),
                    '11.xlsx', date(2026, 1, 11), user_id, str(uploads))
                newest_id = newest.id
                db.session.remove()

            # Exercise the real worker, real auto rotation, and real local
            # removal-aware promotion; no hourly cleanup/recovery job runs.
            with patch.object(application, '_durable_backup',
                              wraps=application._durable_backup) as backups:
                worker = application._limit_parse_in_background(newest_id)
                worker.join(timeout=30)
                self.assertFalse(worker.is_alive(), 'Worker did not finish durable promotion')
            self.assertGreaterEqual(len(backups.call_args_list), 2)
            self.assertIn('limit_versions',
                          backups.call_args_list[0].kwargs['allowed_removed_tables'])
            self.assertEqual(f'limits_parse:{newest_id}',
                             backups.call_args_list[-1].args[0])
            with app.app_context():
                self.assertEqual('0', Setting.get('limits_rotation_needs_backup'))
                self.assertEqual('accepted', db.session.get(LimitVersion, newest_id).status)
                self.assertIsNone(db.session.get(LimitVersion, first_id))
                db.session.remove()
                db.drop_all()
                db.create_all()
                shutil.rmtree(uploads)
                uploads.mkdir()
                restored = restore(db, str(uploads), str(output), application._DURABLE_MODELS)
                self.assertTrue(restored['recovery_needed'])
                self.assertIsNone(db.session.get(LimitVersion, first_id))
                newest = db.session.get(LimitVersion, newest_id)
                self.assertIsNotNone(newest, 'latest must contain the newly applied upload')
                self.assertEqual('accepted', newest.status)
                self.assertEqual(newest_id,
                                 db.session.get(LimitDataset, dataset_id).current_version_id)
                self.assertEqual(b'synthetic newest workbook', Path(newest.source_path).read_bytes())
                self.assertEqual('ready', LimitComparison.query.filter_by(
                    new_version_id=newest_id).one().status)


if __name__ == '__main__':
    unittest.main()