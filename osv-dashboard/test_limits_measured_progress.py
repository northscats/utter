"""Cheap isolated progress contract checks; no application or external transport."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from limits_progress import Progress, presentation
from limits_parser import compare_snapshots


class ProgressTests(unittest.TestCase):
    def test_throttling_and_measured_stage_eta(self):
        version = MagicMock(id=42)
        with patch('limits_progress.db') as database, \
                patch('sqlalchemy.orm.attributes.set_committed_value'), \
                patch('limits_progress.time.monotonic', side_effect=[10, 10.1, 12, 16, 17]):
            progress = Progress(version)
            progress('rows', 0, 1000)
            progress('rows', 1, 1000)  # throttled
            progress('rows', 100, 1000)
            progress('rows', 300, 1000)
            statement = database.engine.begin.return_value.__enter__.return_value.execute.call_args[0][0]
            value = json.loads(statement.compile().params['progress_json'])
            self.assertEqual(14, value['eta'])
            self.assertEqual(21, value['percent'])
            self.assertEqual(3, database.engine.begin.call_count)
            progress('applying')
            statement = database.engine.begin.return_value.__enter__.return_value.execute.call_args[0][0]
            self.assertIsNone(json.loads(statement.compile().params['progress_json'])['eta'])
            database.session.commit.assert_not_called()

    def test_terminal_states_and_legacy_rows(self):
        version = SimpleNamespace(progress_json='{}')
        self.assertEqual(100, presentation(version, 'accepted')['percent'])
        self.assertEqual(0, presentation(version, 'queued')['percent'])
        self.assertIsNone(presentation(version, 'parsing')['eta'])
        version.progress_json = json.dumps(dict(stage='rows', percent=20, started=10,
                                                updated=20, eta=7, done=2, total=10))
        for state in ('error', 'needs_resolution', 'interrupted'):
            result = presentation(version, state)
            self.assertEqual(10, result['elapsed'])
            self.assertIsNone(result['eta'])
            self.assertLess(result['percent'], 100)

    def test_comparison_callback_preserves_results(self):
        row = dict(uin='1.1', row=3, cells={})
        snapshot = dict(rows=[row], columns=[])
        events = []
        actual = compare_snapshots(snapshot, snapshot, progress=lambda *v: events.append(v))
        expected = compare_snapshots(snapshot, snapshot)
        self.assertEqual(expected['changes'], actual['changes'])
        self.assertTrue(any(e[0] == 'matching' for e in events))
        self.assertTrue(any(e[0] == 'comparing' for e in events))

    def test_additive_migration_is_repeatable_and_preserves_old_data(self):
        from sqlalchemy import create_engine, text, inspect
        from limits_schema import migrate_limits_schema
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE limit_versions (id INTEGER PRIMARY KEY, source_name TEXT)'))
            connection.execute(text("INSERT INTO limit_versions VALUES (1, 'original.xlsx')"))
        with patch('limits_schema.db', SimpleNamespace(engine=engine)):
            migrate_limits_schema()
            migrate_limits_schema()
        self.assertIn('progress_json', {c['name'] for c in inspect(engine).get_columns('limit_versions')})
        with engine.connect() as connection:
            row = connection.execute(text('SELECT source_name, progress_json FROM limit_versions')).one()
            self.assertEqual(('original.xlsx', '{}'), tuple(row))
        engine.dispose()


if __name__ == '__main__':
    unittest.main()