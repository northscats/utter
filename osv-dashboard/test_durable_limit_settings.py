"""Allowlisted MAX/source settings survive durable recovery without secrets."""
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

_TEMP = tempfile.TemporaryDirectory(prefix="durable_limit_settings_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEMP.name}/settings.sqlite"
os.environ["DURABLE_BACKUP_DIR"] = f"{_TEMP.name}/backup"
os.environ.setdefault("ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")

from app import _DURABLE_MODELS, app
from database import LimitDataset, LimitVersion, Setting, User, db
from durable_data import (
    DURABLE_SETTING_KEYS, LATEST_KEY, _LocalStore, _restore_json,
    _table_rows_from_manifest, backup, restore,
)
from max_dataset_scope import migrate_selected_version, selected_version_id


class DurableLimitSettingsTests(unittest.TestCase):
    def setUp(self):
        self.backup_dir = tempfile.TemporaryDirectory(
            prefix="durable_limit_snapshot_")
        self.addCleanup(self.backup_dir.cleanup)
        previous_dir = os.environ.get("DURABLE_BACKUP_DIR")
        os.environ["DURABLE_BACKUP_DIR"] = self.backup_dir.name
        self.addCleanup(
            lambda: os.environ.pop("DURABLE_BACKUP_DIR", None)
            if previous_dir is None
            else os.environ.__setitem__("DURABLE_BACKUP_DIR", previous_dir))
        self.context = app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        db.drop_all()
        db.create_all()
        self.uploads = Path(_TEMP.name) / "uploads"
        self.outputs = Path(_TEMP.name) / "output"
        self.uploads.mkdir(exist_ok=True)
        self.outputs.mkdir(exist_ok=True)
        self.store = _LocalStore(os.environ["DURABLE_BACKUP_DIR"])
        user = User(username="settings-owner", role="admin", password_hash="hash")
        db.session.add(user)
        db.session.flush()
        dataset = LimitDataset(name="Technical source", owner_id=user.id)
        db.session.add(dataset)
        db.session.flush()
        versions = [
            LimitVersion(dataset_id=dataset.id, uploaded_by=user.id,
                         source_date=date(2026, 1, 1), status="accepted",
                         source_name=f"{number}.xlsx", source_path=f"nonexistent-{number}",
                         sha256=str(number) * 64, snapshot_json='{"rows":[]}')
            for number in (1, 2)
        ]
        db.session.add_all(versions)
        db.session.flush()
        dataset.current_version_id = versions[1].id
        self.selected_id = versions[0].id
        self.newest_id = versions[1].id
        self.dataset_id = dataset.id
        db.session.commit()

    def _backup(self):
        result = backup(db, self.uploads, self.outputs, _DURABLE_MODELS,
                        "allowlisted MAX choices")
        self.assertTrue(result["promoted"])
        return self.store.get_json(self.store.get_json(LATEST_KEY)["manifest"])

    def test_full_restore_preserves_explicit_historical_selection_and_excludes_secrets(self):
        choices = {
            "limits_upload_dataset_id": str(self.dataset_id),
            "max_limits_version_id": str(self.selected_id),
            "max_limits_dataset_id": str(self.dataset_id),
            "max_limits_min_funding_year": "2025",
        }
        for key, value in choices.items():
            Setting.set(key, value)
        Setting.set("unrelated_service_token", "NEVER_BACK_UP_THIS_SECRET")
        db.session.commit()

        manifest = self._backup()
        self.assertEqual(DURABLE_SETTING_KEYS, set(choices))
        self.assertEqual(
            choices,
            {row["key"]: row["value"] for row in
             _table_rows_from_manifest(self.store, manifest, "Setting")})
        self.assertEqual(
            sorted(choices), sorted(manifest["table_pk_values"]["Setting"]))
        self.assertNotIn("NEVER_BACK_UP_THIS_SECRET", str(manifest))
        for chunk in manifest["table_chunks"]["Setting"]:
            self.assertNotIn(
                "NEVER_BACK_UP_THIS_SECRET",
                (Path(os.environ["DURABLE_BACKUP_DIR"]) / chunk["key"]).read_text())

        # Additive repair restores missing keys, but does not overwrite live
        # administrator changes, including an explicitly cleared selection.
        db.session.delete(db.session.get(Setting, "limits_upload_dataset_id"))
        Setting.set("max_limits_version_id", "")
        db.session.commit()
        restore(db, self.uploads, self.outputs, _DURABLE_MODELS)
        self.assertEqual(str(self.dataset_id), Setting.get("limits_upload_dataset_id"))
        self.assertEqual("", Setting.get("max_limits_version_id"))
        self.assertIsNone(selected_version_id())

        # Simulate total DB loss, rather than clearing only protected models.
        db.drop_all()
        db.create_all()
        result = restore(db, self.uploads, self.outputs, _DURABLE_MODELS)
        self.assertTrue(result["recovery_needed"])
        for key, value in choices.items():
            self.assertEqual(value, Setting.get(key), key)
        self.assertIsNone(db.session.get(Setting, "unrelated_service_token"))
        self.assertEqual(self.newest_id, db.session.get(LimitDataset, self.dataset_id).current_version_id)
        self.assertEqual(self.selected_id, selected_version_id())
        self.assertEqual(self.selected_id, migrate_selected_version().id)

    def test_empty_selected_version_row_survives_and_blocks_legacy_migration(self):
        Setting.set("max_limits_dataset_id", str(self.dataset_id))
        Setting.set("max_limits_version_id", "")
        db.session.commit()
        self._backup()
        db.drop_all()
        db.create_all()
        restore(db, self.uploads, self.outputs, _DURABLE_MODELS)
        self.assertIsNotNone(db.session.get(Setting, "max_limits_version_id"))
        self.assertEqual("", Setting.get("max_limits_version_id"))
        self.assertIsNone(migrate_selected_version())
        self.assertEqual("", Setting.get("max_limits_version_id"))

    def test_unallowlisted_setting_in_legacy_manifest_is_never_restored(self):
        manifest = {
            "tables": {
                "Setting": [
                    {"key": "max_limits_version_id", "value": ""},
                    {"key": "unrelated_service_token", "value": "DO_NOT_IMPORT"},
                ],
            },
        }
        restored, skipped, conflicts = _restore_json(
            db, manifest, (Setting,), store=self.store)
        self.assertEqual((1, 1, 0), (restored, skipped, conflicts))
        self.assertEqual("", Setting.get("max_limits_version_id"))
        self.assertIsNone(db.session.get(Setting, "unrelated_service_token"))


if __name__ == "__main__":
    unittest.main()