# -*- coding: utf-8 -*-
"""Focused regressions for bounded durable manifest conversion."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from durable_data import (
    DurableDataError,
    LATEST_KEY,
    _LocalStore,
    _table_rows_from_manifest,
    _write_table_chunks,
    convert_latest_to_sharded,
)


class DurableManifestShardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            prefix="durable_manifest_sharding_")
        self.addCleanup(self.tmp.cleanup)
        self.old_backup_dir = os.environ.get("DURABLE_BACKUP_DIR")
        os.environ["DURABLE_BACKUP_DIR"] = self.tmp.name
        self.addCleanup(self._restore_environment)
        self.store = _LocalStore(self.tmp.name)

    def _restore_environment(self):
        if self.old_backup_dir is None:
            os.environ.pop("DURABLE_BACKUP_DIR", None)
        else:
            os.environ["DURABLE_BACKUP_DIR"] = self.old_backup_dir

    def test_legacy_latest_is_converted_and_old_manifest_is_retained(self):
        old_key = "osv-dashboard/snapshots/legacy/manifest.json"
        old_manifest = {
            "format": 2,
            "snapshot_id": "legacy",
            "created_at": "2026-09-01T00:00:00+00:00",
            "counts": {"limit_versions": 2},
            "files": [],
            "tables": {
                "LimitVersion": [
                    {"id": 1, "snapshot_json": "x" * 150,
                     "nested": {"rows": [{"cells": {"value": 1.5}}]}},
                    {"id": 2, "snapshot_json": "y" * 150},
                ],
                "LimitDataset": [{"id": 1, "name": "Основной"}],
            },
        }
        self.store.put_json(old_key, old_manifest, immutable=True)
        self.store.put_json(
            LATEST_KEY, {"snapshot_id": "legacy", "manifest": old_key})

        result = convert_latest_to_sharded()

        pointer = self.store.get_json(LATEST_KEY)
        converted = self.store.get_json(pointer["manifest"])
        self.assertTrue(result["converted"])
        self.assertEqual(3, converted["format"])
        self.assertNotIn("tables", converted)
        self.assertEqual(2, converted["table_row_counts"]["LimitVersion"])
        self.assertEqual(
            old_manifest, self.store.get_json(old_key),
            "conversion must not alter the predecessor")
        self.assertEqual(
            [1, 2],
            [row["id"] for row in _table_rows_from_manifest(
                self.store, converted, "LimitVersion")])

    def test_chunks_are_byte_bounded_and_corruption_is_rejected(self):
        chunks, row_ids = _write_table_chunks(
            self.store, "snapshot", "Example",
            iter([
                {"id": 1, "value": "a" * 80},
                {"id": 2, "value": "b" * 10},
                {"id": 3, "value": "c" * 10},
            ]),
            target_bytes=64,
        )
        self.assertEqual([1, 2, 3], row_ids)
        self.assertEqual(2, len(chunks))
        manifest = {
            "format": 3,
            "table_chunks": {"Example": chunks},
            "table_row_counts": {"Example": 3},
            "table_ids": {"Example": row_ids},
        }
        self.assertEqual(
            3, len(list(_table_rows_from_manifest(
                self.store, manifest, "Example"))))

        damaged = Path(self.tmp.name) / chunks[0]["key"]
        damaged.write_text(json.dumps([{"id": 1, "value": "changed"}]),
                           encoding="utf-8")
        with self.assertRaises(DurableDataError):
            list(_table_rows_from_manifest(
                self.store, manifest, "Example"))


if __name__ == "__main__":
    unittest.main()