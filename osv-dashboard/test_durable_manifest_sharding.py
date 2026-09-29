# -*- coding: utf-8 -*-
"""Focused regressions for bounded durable manifest conversion."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from durable_data import (
    DurableDataError,
    LATEST_KEY,
    _LocalStore,
    _restore_json,
    _table_rows_from_manifest,
    _write_table_chunks,
    backup,
    convert_latest_to_sharded,
    restore,
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
            "files": [
                {"area": "uploads", "path": "source.xlsx",
                 "object": "osv-dashboard/blobs/example",
                 "sha256": "example"},
            ],
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
        self.assertEqual(old_manifest["counts"], converted["counts"])
        self.assertEqual(old_manifest["files"], converted["files"])
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

    def test_non_id_primary_key_snapshot_and_restore(self):
        """MAX profiles use user_id as their identity, not a synthetic id."""
        engine = create_engine(
            f"sqlite:///{Path(self.tmp.name) / 'profile-test.sqlite'}")
        Base = declarative_base()

        class MaxLimitsProfile(Base):
            __tablename__ = "max_limits_profiles"

            user_id = Column(Integer, primary_key=True)
            full_name = Column(String, nullable=False)

        class LegacyIdExample(Base):
            __tablename__ = "legacy_id_examples"

            id = Column(Integer, primary_key=True)
            value = Column(String, nullable=False)

        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        self.addCleanup(session.close)
        self.addCleanup(engine.dispose)
        session.add_all([
            MaxLimitsProfile(user_id=9, full_name="Nine"),
            MaxLimitsProfile(user_id=3, full_name="Three"),
            LegacyIdExample(id=4, value="Four"),
        ])
        session.commit()

        db = SimpleNamespace(engine=engine, session=session)
        uploads = Path(self.tmp.name) / "uploads"
        outputs = Path(self.tmp.name) / "outputs"
        uploads.mkdir()
        outputs.mkdir()
        result = backup(
            db, uploads, outputs, (MaxLimitsProfile, LegacyIdExample),
            "non_id_primary_key_test")
        self.assertTrue(result["promoted"])
        pointer = self.store.get_json(LATEST_KEY)
        manifest = self.store.get_json(pointer["manifest"])
        chunks = manifest["table_chunks"]
        legacy_ids = manifest["table_ids"]
        primary_keys = manifest["table_primary_keys"]
        pk_values = manifest["table_pk_values"]
        counts = manifest["table_row_counts"]
        self.assertEqual([], legacy_ids["MaxLimitsProfile"])
        self.assertEqual(["user_id"], primary_keys["MaxLimitsProfile"])
        self.assertEqual([3, 9], pk_values["MaxLimitsProfile"])
        self.assertEqual([4], legacy_ids["LegacyIdExample"])
        self.assertNotIn("LegacyIdExample", primary_keys)
        self.assertNotIn("LegacyIdExample", pk_values)
        self.assertEqual(
            [3, 9],
            [row["user_id"] for row in _table_rows_from_manifest(
                self.store, manifest, "MaxLimitsProfile")])

        session.query(MaxLimitsProfile).filter_by(user_id=9).delete()
        session.commit()
        with patch("durable_data._database_limit_tombstones", return_value=[]):
            restored, skipped, conflicts = _restore_json(
                db, manifest, (MaxLimitsProfile,), store=self.store)
        self.assertEqual((1, 1, 0), (restored, skipped, conflicts))
        self.assertEqual(2, session.query(MaxLimitsProfile).count())
        self.assertEqual(
            "Nine", session.get(MaxLimitsProfile, 9).full_name)

        with patch("durable_data._database_limit_tombstones", return_value=[]):
            restored, skipped, conflicts = _restore_json(
                db, manifest, (MaxLimitsProfile,), store=self.store)
        self.assertEqual((0, 2, 0), (restored, skipped, conflicts))

    def test_restore_converts_legacy_latest_before_loading_manifest(self):
        old_key = "osv-dashboard/snapshots/legacy/manifest.json"
        self.store.put_json(old_key, {
            "format": 2,
            "snapshot_id": "legacy",
            "created_at": "2026-09-01T00:00:00+00:00",
            "counts": {},
            "files": [],
            "tables": {
                "Example": [{"id": 1, "payload": "x" * 200}],
            },
        }, immutable=True)
        self.store.put_json(
            LATEST_KEY, {"snapshot_id": "legacy", "manifest": old_key})

        with patch("durable_data._store", return_value=self.store), \
                patch("durable_data._restore_json",
                      return_value=(0, 0, 0)) as restore_json, \
                patch("durable_data._restore_files", return_value=0):
            restore(object(), self.tmp.name, self.tmp.name, ())

        pointer = self.store.get_json(LATEST_KEY)
        converted = self.store.get_json(pointer["manifest"])
        self.assertEqual(3, converted["format"])
        self.assertNotIn("tables", converted)
        self.assertEqual(
            [{"id": 1, "payload": "x" * 200}],
            list(_table_rows_from_manifest(
                self.store, converted, "Example")))
        restore_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()