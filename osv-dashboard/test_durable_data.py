# -*- coding: utf-8 -*-
"""Isolated regressions for versioned durable backup and merge restore."""
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="durable_data_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["DURABLE_BACKUP_DIR"] = f"{_TMP}/backup"
os.environ["ENCRYPTION_KEY"] = (
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (OUTPUT, UPLOADS, _DURABLE_MODELS, _bg_company_new, app,
                 init_db)
from database import (Company, DailyRow, HistoricalImport, HistoricalMapping,
                      HistoricalMetricValue, HistoricalRawValue,
                      HistoricalYearVersion, Setting, Upload, User, db)
from durable_data import (ConcurrentPromotionError, LATEST_KEY, _GcsStore,
                          _LocalStore, backup, restore)

app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db()


def check(label, condition):
    print(("PASS" if condition else "FAIL"), label)
    if not condition:
        raise AssertionError(label)


backup_root = Path(os.environ["DURABLE_BACKUP_DIR"])
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    company = Company(name="Durable", created_by=admin.id)
    db.session.add(company)
    db.session.commit()

    upload_dir = Path(UPLOADS, str(company.id), "batch")
    output_dir = Path(OUTPUT, str(company.id))
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = upload_dir / "osv.xls"
    dashboard = output_dir / "dashboard_current.html"
    source.write_bytes(b"durable source")
    dashboard.write_text("durable dashboard", encoding="utf-8")

    upload = Upload(company_id=company.id, uploaded_by=admin.id,
                    period_label="2025", accounts_loaded=json.dumps(["51"]))
    daily = DailyRow(company_id=company.id, date=date(2025, 1, 1), acc="51",
                     snd=1, snk=2, obd=3, obk=4, skd=5, skk=6)
    imported = HistoricalImport(
        company_id=company.id, uploaded_by=admin.id, original_filename="a.xlsx",
        stored_path=str(source), file_hash="a" * 64, status="active")
    db.session.add_all((upload, daily, imported))
    db.session.flush()
    version = HistoricalYearVersion(import_id=imported.id, company_id=company.id,
                                    year=2025, version_no=1, is_active=True)
    db.session.add(version)
    db.session.flush()
    metric = HistoricalMetricValue(
        year_version_id=version.id, section_key="assets", section_label="Assets",
        metric_key="total", metric_label="Total", series_key="value",
        value_numeric=1.0)
    db.session.add(metric)
    db.session.flush()
    db.session.add(HistoricalRawValue(metric_id=metric.id, raw_text="1"))
    db.session.add(HistoricalMapping(
        company_id=company.id, kind="metric", source_label="Total",
        source_normalized="total", canonical_key="total",
        canonical_label="Total", confirmed_by=admin.id))
    db.session.commit()

    first = backup(
        db, UPLOADS, OUTPUT, _DURABLE_MODELS, "initial_complete")
    latest_path = backup_root / LATEST_KEY
    first_pointer = json.loads(latest_path.read_text(encoding="utf-8"))
    check("first immutable version is promoted",
          first["promoted"] and first_pointer["snapshot_id"] == first["snapshot_id"]
          and "User" in json.loads(
              (backup_root / first_pointer["manifest"]).read_text(
                  encoding="utf-8"))["tables"])

    DailyRow.query.delete()
    db.session.add(DailyRow(
        id=999,
        company_id=company.id, date=date(2025, 1, 2), acc="51",
        snd=10, snk=20, obd=30, obk=40, skd=50, skk=60))
    db.session.commit()
    rejected = backup(
        db, UPLOADS, OUTPUT, _DURABLE_MODELS, "unexpected_shrink")
    unchanged_pointer = json.loads(latest_path.read_text(encoding="utf-8"))
    manifests = list(
        (backup_root / "osv-dashboard" / "snapshots").glob("*/manifest.json"))
    check("unexpected shrink keeps previous latest and both versions",
          not rejected["promoted"]
          and rejected["shrink_rejected"] == ["daily_rows"]
          and unchanged_pointer == first_pointer
          and len(manifests) == 2)

    # Completely erase every table owned by the local JSON backend, including
    # User and Company, and both numeric file trees.
    for model in reversed(_DURABLE_MODELS):
        model.query.delete()
    db.session.commit()
    shutil.rmtree(Path(UPLOADS, str(company.id)))
    shutil.rmtree(Path(OUTPUT, str(company.id)))

    outcome = restore(db, UPLOADS, OUTPUT, _DURABLE_MODELS)
    check("latest restores all protected tables and both file areas",
          outcome["rows"] == 9
          and User.query.count() == 1
          and Company.query.count() == 1
          and Upload.query.count() == 1
          and DailyRow.query.count() == 1
          and HistoricalRawValue.query.count() == 1
          and source.read_bytes() == b"durable source"
          and dashboard.read_text(encoding="utf-8") == "durable dashboard")

    admin = User.query.filter_by(role="admin").first()
    admin.password_hash = "newer-password-state"
    Setting.set("unrelated_newer_setting", "keep-me")
    DailyRow.query.delete()
    db.session.commit()
    partial = restore(db, UPLOADS, OUTPUT, _DURABLE_MODELS)
    check("partial shrink safely merges without replacing unrelated live data",
          partial["recovery_needed"]
          and partial["rows"] == 1
          and DailyRow.query.count() == 1
          and admin.password_hash == "newer-password-state"
          and Setting.get("unrelated_newer_setting") == "keep-me")

    protected_company_id = company.id
    Upload.query.filter_by(company_id=protected_company_id).delete()
    DailyRow.query.filter_by(company_id=protected_company_id).delete()
    HistoricalRawValue.query.delete()
    HistoricalMetricValue.query.delete()
    HistoricalYearVersion.query.delete()
    HistoricalMapping.query.filter_by(company_id=protected_company_id).delete()
    HistoricalImport.query.filter_by(company_id=protected_company_id).delete()
    db.session.delete(company)
    db.session.commit()
    missing_parent = restore(db, UPLOADS, OUTPUT, _DURABLE_MODELS)
    company = db.session.get(Company, protected_company_id)
    check("partial loss restores a missing company and its protected data",
          missing_parent["recovery_needed"]
          and company is not None
          and Upload.query.filter_by(company_id=protected_company_id).count() == 1
          and HistoricalYearVersion.query.filter_by(
              company_id=protected_company_id).count() == 1)

    Upload.query.delete()
    admin.username = "unrelated-user-with-same-id"
    db.session.commit()
    identity_conflict = restore(db, UPLOADS, OUTPUT, _DURABLE_MODELS)
    check("parent identity conflict skips dependent rows",
          identity_conflict["recovery_needed"]
          and identity_conflict["conflicts"] >= 2
          and Upload.query.count() == 0
          and admin.username == "unrelated-user-with-same-id")
    admin.username = "admin"
    db.session.commit()

    failed_company = Company(name="Parse failure", created_by=admin.id)
    db.session.add(failed_company)
    db.session.commit()
    failed_dir = Path(UPLOADS, str(failed_company.id), "first")
    failed_dir.mkdir(parents=True, exist_ok=True)
    failed_file = failed_dir / "bad.xls"
    failed_file.write_bytes(b"not an xls")
    _bg_company_new("durable-regression-job", failed_company.id, str(failed_dir),
                    [str(failed_file)], "first", "/", failed_company.name, admin.id)
    check("failed first parse preserves company and source file",
          db.session.get(Company, failed_company.id) is not None
          and failed_file.is_file())

    pointer_store = _LocalStore(backup_root)
    _, pointer_generation = pointer_store.get_json_versioned(LATEST_KEY)
    pointer_store.put_json(
        LATEST_KEY, {"snapshot_id": "newer", "manifest": "newer-manifest"},
        if_generation_match=pointer_generation)
    stale_rejected = False
    try:
        pointer_store.put_json(
            LATEST_KEY, {"snapshot_id": "stale", "manifest": "stale-manifest"},
            if_generation_match=pointer_generation)
    except ConcurrentPromotionError:
        stale_rejected = True
    check("stale process cannot overwrite a newer latest pointer",
          stale_rejected)

    class FakePreconditionFailed(Exception):
        pass

    class FakeNotFound(Exception):
        pass

    class RacingBlob:
        def __init__(self):
            self.current_generation = 1
            self.generation = None
            self.payloads = {
                1: '{"snapshot_id":"older","manifest":"older-manifest"}',
                2: '{"snapshot_id":"newer","manifest":"newer-manifest"}',
            }
            self.raced = False

        def reload(self):
            self.generation = self.current_generation

        def download_as_text(self, if_generation_match=None):
            if not self.raced:
                self.raced = True
                self.current_generation = 2
            if if_generation_match != self.current_generation:
                raise FakePreconditionFailed()
            return self.payloads[self.current_generation]

    class FakeBucket:
        def __init__(self, blob):
            self._blob = blob

        def blob(self, _key):
            return self._blob

    racing_store = _GcsStore.__new__(_GcsStore)
    racing_store.bucket = FakeBucket(RacingBlob())
    racing_store.prefix = ""
    racing_store._not_found = FakeNotFound
    racing_store._precondition_failed = FakePreconditionFailed
    atomic_pointer, atomic_generation = racing_store.get_json_versioned(
        LATEST_KEY)
    check("GCS pointer payload and generation are read atomically",
          atomic_generation == 2
          and atomic_pointer["snapshot_id"] == "newer")

print("\nDURABLE DATA TESTS PASSED")