# -*- coding: utf-8 -*-
"""Isolated integration checks for atomic monthly upload acceptance.

The worker is exercised with a real temporary SQLite database and storage
root.  Parser/dashboard work is stubbed at the worker boundary so these tests
focus on the acceptance transaction and the persisted source-manifest
contract, not on Excel fixtures or the rendering pipeline.
"""

import atexit
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from email.message import EmailMessage
from unittest.mock import patch


_TMP = tempfile.mkdtemp(prefix="upload_manifest_acceptance_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
os.environ.pop("DURABLE_BACKUP_DIR", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module
from app import (
    _bg_company_new,
    _bg_upload_all,
    _do_imap_sync,
    _find_monthly_xls,
    _process_files,
    app,
    init_db,
)
from database import Company, DailyRow, EmailRule, Setting, Upload, User, db
from jobs import create_job, get_job


app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db(perform_durable=False)

app_module._ensure_imap_thread = lambda: None
app_module.UPLOADS = os.path.join(_TMP, "uploads")
app_module.OUTPUT = os.path.join(_TMP, "output")
os.makedirs(app_module.UPLOADS, exist_ok=True)
os.makedirs(app_module.OUTPUT, exist_ok=True)


@atexit.register
def _cleanup():
    with app.app_context():
        db.session.remove()
    shutil.rmtree(_TMP, ignore_errors=True)


class _FakeImap:
    def __init__(self, raw_message):
        self.raw_message = raw_message
        self.stored = []
        self.logged_in = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, username, password):
        self.logged_in = (username, password)
        return "OK", []

    def select(self, folder):
        return "OK", [b"1"]

    def search(self, charset, criterion):
        return "OK", [b"1"]

    def fetch(self, uid, query):
        return "OK", [(b"RFC822", self.raw_message)]

    def store(self, uid, operation, flag):
        self.stored.append((uid, operation, flag))

    def logout(self):
        return "BYE", []


class _CaptureThread:
    calls = []

    def __init__(self, *args, **kwargs):
        self.target = kwargs.get("target", args[0] if args else None)
        self.args = kwargs.get("args", args[1] if len(args) > 1 else ())
        type(self).calls.append((self.target, self.args))

    def start(self):
        return None


class UploadManifestAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with app.app_context():
            cls.admin_id = User.query.filter_by(role="admin").first().id

    def setUp(self):
        with app.app_context():
            company = Company(
                name=f"Manifest test {self._testMethodName}",
                created_by=self.admin_id,
            )
            db.session.add(company)
            db.session.commit()
            self.company_id = company.id

    @staticmethod
    def _parsed_file(path):
        return {
            "51": {
                "acc": "51",
                "period": "за январь 2026 г.",
                "rows": [],
                "daily": [],
                "top": {},
                "warnings": [],
                "check_ok": True,
            }
        }, [{
            "file": os.path.basename(path),
            "acc": "51",
            "status": "разобран",
            "ok": True,
        }]

    def _run_worker(self, *, rebuild, build_manifest=None):
        stage = os.path.join(
            app_module.UPLOADS, str(self.company_id), "stage_20260102_030405")
        os.makedirs(stage, exist_ok=True)
        path = os.path.join(stage, "monthly.xlsx")
        payload = b"accepted monthly source"
        with open(path, "wb") as stream:
            stream.write(payload)

        def parse_many(paths):
            return self._parsed_file(paths[0])

        def upsert_daily(company_id, paths):
            # This pending DB change must commit with Upload on success and
            # disappear with Upload on worker error/cancellation.
            db.session.add(DailyRow(
                company_id=company_id,
                date=date(2026, 1, 31),
                acc="51",
            ))
            return 1

        def save_kpi_cache(_dataset, out_dir):
            with open(os.path.join(out_dir, "kpi_cache.json"),
                      "w", encoding="utf-8") as stream:
                stream.write("new kpi cache")

        patches = [
            patch.object(app_module, "parse_many", parse_many),
            patch.object(app_module, "_is_daily_xls", return_value=False),
            patch.object(app_module, "_upsert_daily_rows", upsert_daily),
            patch.object(app_module, "_compute_discrepancy", return_value=[]),
            patch.object(app_module, "_rebuild_dashboard_with_daily", rebuild),
            patch.object(
                app_module,
                "render_dashboard",
                return_value="<html>test</html>",
            ),
            patch.object(app_module, "_save_kpi_cache",
                         side_effect=save_kpi_cache),
            patch.object(app_module, "_dataset_accounts", return_value=["51"]),
            patch.object(app_module, "_dataset_metrics_json", return_value="{}"),
            patch.object(app_module, "_dataset_closure_status", return_value=None),
            patch.object(app_module, "_durable_backup"),
            patch.object(app_module, "log_event"),
            patch.object(app_module.traceback, "print_exc"),
        ]
        if build_manifest is not None:
            patches.append(patch.object(
                app_module, "build_manifest", side_effect=build_manifest))

        for active in patches:
            active.start()
        try:
            with app.app_context():
                job_id = create_job(self.company_id)
            _bg_upload_all(
                job_id,
                self.company_id,
                "Manifest test",
                stage,
                "20260102_030405",
                self.admin_id,
            )
            return job_id, payload
        finally:
            for active in reversed(patches):
                active.stop()

    @staticmethod
    def _dataset_result(*args, **kwargs):
        return (
            {
                "period_label": "Январь 2026",
                "brief": {"lead": "Данные загружены."},
            },
            [],
        )

    def _admin_client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin_id)
            session["_fresh"] = True
        return client

    def _configure_imap(self):
        with app.app_context():
            for key, value in (
                ("imap_host", "imap.test"),
                ("imap_port", "993"),
                ("imap_login", "mailbox@example.test"),
                ("imap_password", "secret"),
                ("imap_folder", "INBOX"),
            ):
                Setting.set(key, value)
            db.session.add(EmailRule(
                company_id=self.company_id,
                sender_pattern=f"sender-{self.company_id}@example.test",
                subject_pattern="*",
            ))
            db.session.commit()

    @staticmethod
    def _email_with_attachments(attachments, sender="sender@example.test"):
        message = EmailMessage()
        message["From"] = sender
        message["To"] = "dashboard@example.test"
        message["Subject"] = "ОСВ"
        message.set_content("attached source reports")
        for filename, payload, subtype in attachments:
            message.add_attachment(
                payload,
                maintype="application",
                subtype=subtype,
                filename=filename,
            )
        return message.as_bytes()

    def _imap_patches(self, parse_many, render_dashboard):
        return [
            patch("imaplib.IMAP4_SSL"),
            patch.object(app_module, "parse_many", side_effect=parse_many),
            patch.object(app_module, "_is_daily_xls", return_value=False),
            patch.object(app_module, "_rebuild_dashboard_with_daily",
                         return_value=self._dataset_result()),
            patch.object(app_module, "render_dashboard",
                         side_effect=render_dashboard),
            patch.object(app_module, "_save_kpi_cache"),
            patch.object(app_module, "_dataset_accounts",
                         return_value=["51"]),
            patch.object(app_module, "_dataset_metrics_json",
                         return_value="{}"),
            patch.object(app_module, "_dataset_closure_status",
                         return_value=None),
            patch.object(app_module, "_durable_backup"),
            patch.object(app_module, "log_event"),
        ]

    def test_initial_company_daily_only_skips_monthly_parser(self):
        with tempfile.TemporaryDirectory() as root:
            daily = os.path.join(root, "daily.xlsx")
            with open(daily, "wb") as stream:
                stream.write(b"daily")
            with patch.object(app_module, "_is_daily_xls", return_value=True), \
                    patch.object(
                        app_module, "parse_many",
                        side_effect=AssertionError(
                            "daily source must not enter parse_many")):
                result = _process_files(
                    [daily], self.company_id, "batch", "/company/test")
        self.assertEqual(result, (None, []))

    def test_initial_company_mixed_batch_aligns_monthly_paths_and_audit(self):
        with tempfile.TemporaryDirectory() as root:
            daily = os.path.join(root, "daily.xlsx")
            monthly = os.path.join(root, "monthly.xlsx")
            for path in (daily, monthly):
                with open(path, "wb") as stream:
                    stream.write(os.path.basename(path).encode())
            parsed = {
                "51": {
                    "acc": "51",
                    "period": "Январь 2026",
                    "daily": [],
                    "top": {},
                    "rows": [],
                    "warnings": [],
                }
            }
            audit = [{
                "file": "monthly.xlsx",
                "acc": "51",
                "status": "разобран",
                "ok": True,
            }]

            def classify(path):
                return os.path.basename(path) == "daily.xlsx"

            with patch.object(app_module, "_is_daily_xls",
                              side_effect=classify), \
                    patch.object(app_module, "parse_many",
                                 return_value=(parsed, audit)) as parser, \
                    patch.object(app_module, "build_dataset",
                                 return_value={"brief": {}, "period_label": ""}), \
                    patch.object(app_module, "render_dashboard",
                                 return_value="<html></html>"), \
                    patch.object(app_module, "_save_kpi_cache"), \
                    patch.object(app_module, "_dataset_closure_status",
                                 return_value=None), \
                    patch.object(app_module, "_dataset_metrics_json",
                                 return_value="{}"), \
                    patch.object(app_module, "_monthly_manifest_json",
                                 return_value='[]') as manifest:
                with patch.object(app_module, "OUTPUT",
                                  os.path.join(root, "output")):
                    record_kwargs, result_audit = _process_files(
                        [daily, monthly], self.company_id,
                        "batch", "/company/test")

            parser.assert_called_once_with([monthly])
            manifest_args = manifest.call_args.args
            self.assertEqual(manifest_args[0], [monthly])
            self.assertIs(manifest_args[1], parsed)
            self.assertIs(manifest_args[2], result_audit)
            self.assertEqual(record_kwargs["files_count"], 2)

    def test_browser_existing_company_same_names_survive_staging_and_manifest(self):
        client = self._admin_client()
        _CaptureThread.calls = []
        payloads = [b"PK browser first", b"PK browser second"]
        with patch.object(app_module.threading, "Thread", _CaptureThread):
            response = client.post(
                f"/company/{self.company_id}/upload-all",
                data={"files": [
                    (io.BytesIO(payload), "report.xlsx")
                    for payload in payloads
                ]},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        target, args = _CaptureThread.calls[-1]
        stage_dir = args[3]
        staged = sorted(os.listdir(stage_dir))
        self.assertEqual(staged, ["report.xlsx", "report_2.xlsx"])
        staged_bytes = []
        for name in staged:
            with open(os.path.join(stage_dir, name), "rb") as stream:
                staged_bytes.append(stream.read())
        self.assertEqual(staged_bytes, payloads)

        def parse_many(paths):
            result = {}
            audit = []
            for path in sorted(paths):
                account = "62" if os.path.basename(path) == "report_2.xlsx" else "51"
                result[account] = {
                    "acc": account,
                    "period": "Январь 2026",
                    "rows": [],
                    "daily": [],
                    "top": {},
                    "warnings": [],
                    "check_ok": True,
                }
                audit.append({
                    "file": os.path.basename(path),
                    "acc": account,
                    "status": "разобран",
                    "ok": True,
                })
            return result, audit

        patches = [
            patch.object(app_module, "parse_many", side_effect=parse_many),
            patch.object(app_module, "_is_daily_xls", return_value=False),
            patch.object(app_module, "_upsert_daily_rows", return_value=0),
            patch.object(app_module, "_compute_discrepancy", return_value=[]),
            patch.object(app_module, "_rebuild_dashboard_with_daily",
                         return_value=self._dataset_result()),
            patch.object(app_module, "render_dashboard",
                         return_value="<html>browser upload</html>"),
            patch.object(app_module, "_save_kpi_cache"),
            patch.object(app_module, "_dataset_accounts",
                         return_value=["51", "62"]),
            patch.object(app_module, "_dataset_metrics_json",
                         return_value="{}"),
            patch.object(app_module, "_dataset_closure_status",
                         return_value=None),
            patch.object(app_module, "_durable_backup"),
            patch.object(app_module, "log_event"),
        ]
        for active in patches:
            active.start()
        try:
            target(*args)
        finally:
            for active in reversed(patches):
                active.stop()

        with app.app_context():
            upload = Upload.query.filter_by(
                company_id=self.company_id).one()
            manifest = json.loads(upload.source_manifest_json)
        self.assertEqual(len(manifest), 2)
        batch_name = args[4]
        self.assertEqual(
            sorted(item["relative_path"] for item in manifest),
            [f"{batch_name}/report.xlsx",
             f"{batch_name}/report_2.xlsx"])
        self.assertEqual(
            sorted(item["sha256"] for item in manifest),
            sorted(hashlib.sha256(payload).hexdigest() for payload in payloads))

    def test_browser_new_company_same_names_survive_staging_and_manifest(self):
        client = self._admin_client()
        _CaptureThread.calls = []
        payloads = [b"PK new browser first", b"PK new browser second"]
        company_name = f"Browser same-name {self.company_id}"
        with patch.object(app_module.threading, "Thread", _CaptureThread):
            response = client.post(
                "/company/new",
                data={
                    "org_name": company_name,
                    "files": [
                        (io.BytesIO(payload), "report.xlsx")
                        for payload in payloads
                    ],
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        target, args = _CaptureThread.calls[-1]
        final_dir = args[2]
        staged = sorted(os.listdir(final_dir))
        self.assertEqual(staged, ["report.xlsx", "report_2.xlsx"])
        staged_bytes = []
        for name in staged:
            with open(os.path.join(final_dir, name), "rb") as stream:
                staged_bytes.append(stream.read())
        self.assertEqual(staged_bytes, payloads)

        def parse_many(paths):
            result = {}
            audit = []
            for path in sorted(paths):
                account = "62" if os.path.basename(path) == "report_2.xlsx" else "51"
                result[account] = {
                    "acc": account,
                    "period": "Январь 2026",
                    "rows": [],
                    "daily": [],
                    "top": {},
                    "warnings": [],
                    "check_ok": True,
                }
                audit.append({
                    "file": os.path.basename(path),
                    "acc": account,
                    "status": "разобран",
                    "ok": True,
                })
            return result, audit

        patches = [
            patch.object(app_module, "parse_many", side_effect=parse_many),
            patch.object(app_module, "_is_daily_xls", return_value=False),
            patch.object(app_module, "_upsert_daily_rows", return_value=0),
            patch.object(app_module, "build_dataset",
                         return_value=self._dataset_result()[0]),
            patch.object(app_module, "render_dashboard",
                         return_value="<html>new browser upload</html>"),
            patch.object(app_module, "_save_kpi_cache"),
            patch.object(app_module, "_dataset_metrics_json",
                         return_value="{}"),
            patch.object(app_module, "_dataset_closure_status",
                         return_value=None),
            patch.object(app_module, "_durable_backup"),
            patch.object(app_module, "log_event"),
        ]
        for active in patches:
            active.start()
        try:
            target(*args)
        finally:
            for active in reversed(patches):
                active.stop()

        with app.app_context():
            company = Company.query.filter_by(name=company_name).one()
            upload = Upload.query.filter_by(company_id=company.id).one()
            manifest = json.loads(upload.source_manifest_json)
        self.assertEqual(len(manifest), 2)
        self.assertEqual(
            sorted(item["relative_path"] for item in manifest),
            [f"{os.path.basename(final_dir)}/report.xlsx",
             f"{os.path.basename(final_dir)}/report_2.xlsx"])
        self.assertEqual(
            sorted(item["sha256"] for item in manifest),
            sorted(hashlib.sha256(payload).hexdigest() for payload in payloads))

    def test_imap_duplicate_attachment_names_are_reserved_and_failed_batch_removed(self):
        self._configure_imap()
        raw = self._email_with_attachments([
            ("report.xlsx", b"first report", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("report.xlsx", b"second report", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ], sender=f"sender-{self.company_id}@example.test")
        fake_imap = _FakeImap(raw)
        render_calls = []

        def parse_many(paths):
            account = "62" if os.path.basename(paths[0]) == "report_2.xlsx" else "51"
            return {
                account: {
                    "acc": account,
                    "period": "Январь 2026",
                    "rows": [],
                    "daily": [],
                    "top": {},
                    "warnings": [],
                    "check_ok": True,
                }
            }, [{
                "file": os.path.basename(paths[0]),
                "acc": account,
                "status": "разобран",
                "ok": True,
            }]

        def render_dashboard(*args, **kwargs):
            render_calls.append(True)
            if len(render_calls) == 2:
                raise RuntimeError("simulated IMAP render failure")
            return "<html>first IMAP dashboard</html>"

        out_dir = os.path.join(app_module.OUTPUT, str(self.company_id))
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "dashboard_current.html"), "w",
                  encoding="utf-8") as stream:
            stream.write("old dashboard")
        patches = self._imap_patches(parse_many, render_dashboard)
        imap_constructor = patches[0].start()
        imap_constructor.return_value = fake_imap
        for active in patches[1:]:
            active.start()
        try:
            with app.app_context():
                result = _do_imap_sync(self.admin_id)
        finally:
            for active in reversed(patches):
                active.stop()

        with app.app_context():
            uploads = Upload.query.filter_by(
                company_id=self.company_id).order_by(Upload.id).all()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(len(uploads), 1, result)
        manifest = json.loads(uploads[0].source_manifest_json)
        self.assertEqual(len(manifest), 1)
        self.assertTrue(manifest[0]["relative_path"].endswith(
            "/report.xlsx"))
        company_root = os.path.join(
            app_module.UPLOADS, str(self.company_id))
        dirs = sorted(os.listdir(company_root))
        self.assertEqual(len(dirs), 1)
        self.assertTrue(dirs[0].startswith("imap_"))
        with open(os.path.join(out_dir, "dashboard_current.html"),
                  encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "<html>first IMAP dashboard</html>")

    def test_imap_zip_duplicate_inner_basenames_keep_unique_persisted_names(self):
        self._configure_imap()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zfile:
            zfile.writestr("one/report.xlsx", b"first archive report")
            zfile.writestr("two/report.xlsx", b"second archive report")
        raw = self._email_with_attachments([
            ("reports.zip", archive.getvalue(), "zip"),
        ], sender=f"sender-{self.company_id}@example.test")
        fake_imap = _FakeImap(raw)

        def parse_many(paths):
            result = {}
            audit = []
            for path in sorted(paths):
                account = "62" if os.path.basename(path) == "report_2.xlsx" else "51"
                result[account] = {
                    "acc": account,
                    "period": "Январь 2026",
                    "rows": [],
                    "daily": [],
                    "top": {},
                    "warnings": [],
                    "check_ok": True,
                }
                audit.append({
                    "file": os.path.basename(path),
                    "acc": account,
                    "status": "разобран",
                    "ok": True,
                })
            return result, audit

        patches = self._imap_patches(
            parse_many, lambda *args, **kwargs: "<html>zip dashboard</html>")
        imap_constructor = patches[0].start()
        imap_constructor.return_value = fake_imap
        for active in patches[1:]:
            active.start()
        try:
            with app.app_context():
                result = _do_imap_sync(self.admin_id)
        finally:
            for active in reversed(patches):
                active.stop()

        with app.app_context():
            uploads = Upload.query.filter_by(
                company_id=self.company_id).all()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(len(uploads), 1, result)
        upload = uploads[0]
        manifest = json.loads(upload.source_manifest_json)
        names = sorted(item["relative_path"] for item in manifest)
        self.assertEqual(
            [os.path.basename(name) for name in names],
            ["report.xlsx", "report_2.xlsx"])
        company_root = os.path.join(
            app_module.UPLOADS, str(self.company_id))
        zip_dirs = [name for name in os.listdir(company_root)
                    if name.startswith("zip_")]
        self.assertEqual(len(zip_dirs), 1)
        self.assertEqual(os.listdir(
            os.path.join(company_root, zip_dirs[0])), ["reports.zip"])

    def test_rebuild_excludes_unaccepted_stage_sources(self):
        with tempfile.TemporaryDirectory() as root:
            original_uploads = app_module.UPLOADS
            app_module.UPLOADS = root
            try:
                company_root = os.path.join(root, str(self.company_id))
                for folder in ("accepted_1", "stage_1", "zip_1", "daily_1"):
                    directory = os.path.join(company_root, folder)
                    os.makedirs(directory, exist_ok=True)
                    with open(os.path.join(directory, f"{folder}.xlsx"), "wb") as stream:
                        stream.write(folder.encode())
                paths = _find_monthly_xls(self.company_id)
            finally:
                app_module.UPLOADS = original_uploads
        self.assertEqual(
            [os.path.basename(os.path.dirname(path)) for path in paths],
            ["accepted_1"])

    def test_success_commits_upload_manifest_and_pending_rows_together(self):
        job_id, payload = self._run_worker(rebuild=self._dataset_result)

        with app.app_context():
            upload = Upload.query.filter_by(company_id=self.company_id).one()
            manifest = json.loads(upload.source_manifest_json)
            daily_count = DailyRow.query.filter_by(
                company_id=self.company_id).count()

        self.assertEqual(get_job(job_id)["status"], "done")
        self.assertEqual(daily_count, 1)
        self.assertEqual(len(manifest), 1)
        record = manifest[0]
        self.assertEqual(record["relative_path"],
                         "20260102_030405/monthly.xlsx")
        self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(record["account"], "51")
        self.assertEqual(record["period"], "за январь 2026 г.")
        self.assertEqual(record["report_start"], "2026-01-01")
        self.assertEqual(record["report_end"], "2026-01-31")
        self.assertEqual(len({item["source_id"] for item in manifest}), 1)

    def test_daily_only_background_upload_persists_explicit_empty_manifest(self):
        stage = os.path.join(
            app_module.UPLOADS, str(self.company_id), "stage_daily_only")
        os.makedirs(stage, exist_ok=True)
        with open(os.path.join(stage, "daily.xlsx"), "wb") as stream:
            stream.write(b"daily source")

        reconciliation_sync = patch.object(
            app_module, "_sync_daily_reconciliation_alerts").start()
        patches = [
            patch.object(app_module, "_is_daily_xls", return_value=True),
            patch.object(app_module, "_upsert_daily_rows", return_value=1),
            patch.object(app_module, "_rebuild_dashboard_with_daily",
                         return_value=self._dataset_result()),
            patch.object(app_module, "render_dashboard",
                         return_value="<html>daily only</html>"),
            patch.object(app_module, "_dataset_accounts", return_value=[]),
            patch.object(app_module, "_dataset_metrics_json",
                         return_value="{}"),
            patch.object(app_module, "_dataset_closure_status",
                         return_value=None),
            patch.object(app_module, "_durable_backup"),
            patch.object(app_module, "log_event"),
            patch.object(app_module.traceback, "print_exc"),
        ]
        for active in patches:
            active.start()
        try:
            with app.app_context():
                job_id = create_job(self.company_id)
            _bg_upload_all(
                job_id,
                self.company_id,
                "Manifest test",
                stage,
                "20260103_030405",
                self.admin_id,
            )
        finally:
            for active in reversed(patches):
                active.stop()
            reconciliation_sync.stop()

        with app.app_context():
            upload = Upload.query.filter_by(company_id=self.company_id).one()
            self.assertEqual(upload.source_manifest_json, "[]")
            self.assertEqual(upload.source_manifest, [])
        self.assertEqual(get_job(job_id)["status"], "done")
        reconciliation_sync.assert_called_once_with(self.company_id)

    def test_failed_new_company_batch_removes_sources_and_restores_outputs(self):
        final_dir = os.path.join(
            app_module.UPLOADS, str(self.company_id), "new_company_batch")
        os.makedirs(final_dir, exist_ok=True)
        with open(os.path.join(final_dir, "monthly.xlsx"), "wb") as stream:
            stream.write(b"new company source")
        final_paths = [os.path.join(final_dir, "monthly.xlsx")]

        out_dir = os.path.join(app_module.OUTPUT, str(self.company_id))
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "dashboard_current.html"), "w",
                  encoding="utf-8") as stream:
            stream.write("old dashboard")
        with open(os.path.join(out_dir, "kpi_cache.json"), "w",
                  encoding="utf-8") as stream:
            stream.write("old kpi cache")

        def failed_process(*args, **kwargs):
            with open(os.path.join(out_dir, "dashboard_current.html"), "w",
                      encoding="utf-8") as stream:
                stream.write("orphan dashboard")
            with open(os.path.join(out_dir, "kpi_cache.json"), "w",
                      encoding="utf-8") as stream:
                stream.write("orphan kpi cache")
            raise RuntimeError("new company acceptance failed")

        with app.app_context():
            job_id = create_job(self.company_id)
        with patch.object(app_module, "_process_files",
                          side_effect=failed_process), \
                patch.object(app_module.traceback, "print_exc"):
            _bg_company_new(
                job_id, self.company_id, final_dir, final_paths,
                "new_company_batch", "/company/test",
                "Manifest test", self.admin_id)

        with app.app_context():
            self.assertEqual(
                Upload.query.filter_by(company_id=self.company_id).count(), 0)
        self.assertEqual(get_job(job_id)["status"], "error")
        self.assertFalse(os.path.exists(final_dir))
        with open(os.path.join(out_dir, "dashboard_current.html"),
                  encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "old dashboard")
        with open(os.path.join(out_dir, "kpi_cache.json"),
                  encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "old kpi cache")

    def test_dashboard_error_rolls_back_pending_rows_and_manifest(self):
        def failed_rebuild(*args, **kwargs):
            return None, [{"status": "ошибка сборки", "ok": False}]

        job_id, _payload = self._run_worker(rebuild=failed_rebuild)

        with app.app_context():
            upload_count = Upload.query.filter_by(
                company_id=self.company_id).count()
            daily_count = DailyRow.query.filter_by(
                company_id=self.company_id).count()

        self.assertEqual(get_job(job_id)["status"], "error")
        self.assertEqual(upload_count, 0)
        self.assertEqual(daily_count, 0)
        self.assertEqual(os.listdir(
            os.path.join(app_module.UPLOADS, str(self.company_id))), [])

    def test_cancellation_during_manifest_preparation_never_confirms_upload(self):
        class UploadCancelled(Exception):
            pass

        def cancel_manifest(*args, **kwargs):
            raise UploadCancelled("upload cancelled")

        out_dir = os.path.join(app_module.OUTPUT, str(self.company_id))
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "dashboard_current.html"), "w",
                  encoding="utf-8") as stream:
            stream.write("old dashboard")
        with open(os.path.join(out_dir, "kpi_cache.json"), "w",
                  encoding="utf-8") as stream:
            stream.write("old kpi cache")

        job_id, _payload = self._run_worker(
            rebuild=self._dataset_result,
            build_manifest=cancel_manifest,
        )

        with app.app_context():
            upload_count = Upload.query.filter_by(
                company_id=self.company_id).count()
            daily_count = DailyRow.query.filter_by(
                company_id=self.company_id).count()

        self.assertEqual(get_job(job_id)["status"], "error")
        self.assertEqual(upload_count, 0)
        self.assertEqual(daily_count, 0)
        self.assertEqual(os.listdir(
            os.path.join(app_module.UPLOADS, str(self.company_id))), [])
        with open(os.path.join(out_dir, "dashboard_current.html"),
                  encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "old dashboard")
        with open(os.path.join(out_dir, "kpi_cache.json"),
                  encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "old kpi cache")

        # A retry must see only its own staged file, never an orphan left by
        # the cancelled batch.
        success_job, _payload = self._run_worker(rebuild=self._dataset_result)
        with app.app_context():
            upload = Upload.query.filter_by(company_id=self.company_id).one()
            manifest = json.loads(upload.source_manifest_json)
        self.assertEqual(get_job(success_job)["status"], "done")
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["relative_path"],
                         "20260102_030405/monthly.xlsx")


if __name__ == "__main__":
    unittest.main(verbosity=2)
