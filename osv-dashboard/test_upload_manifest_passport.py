# -*- coding: utf-8 -*-
"""Focused consumer-side checks for persisted upload manifests.

These tests deliberately avoid Flask/database setup.  Acceptance persistence
is represented by plain Upload-shaped dictionaries so that the checks remain
safe to run against an isolated checkout.
"""

import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import key_indicators_service as service
from source_passport import build_source_passport


def _identity(relative_path, digest):
    return hashlib.sha256(
        f"{relative_path}\0{digest}".encode("utf-8")).hexdigest()


def _source(relative_path, digest, *, account="51", period="Январь 2026"):
    source_id = _identity(relative_path, digest)
    return {
        "accounts": {
            account: {
                "acc": account,
                "period": period,
                "rows": [],
                "daily": [],
                "warnings": [],
                "top": {
                    "snd": 0, "snk": 0, "obd": 0, "obk": 0,
                    "skd": 0, "skk": 0,
                },
            },
        },
        "_source_manifest": [{
            "source_file": os.path.basename(relative_path),
            "kind": "osv",
            "status": "parsed",
            "account": account,
            "period": period,
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "relative_path": relative_path,
            "sha256": digest,
            "source_id": source_id,
        }],
    }


def _upload(manifest, *, upload_id=7, uploaded_at="2026-09-14T12:00:00Z"):
    return {
        "id": upload_id,
        "uploaded_at": uploaded_at,
        "period_label": "Январь 2026",
        "accounts_loaded": '["51"]',
        "source_manifest": manifest,
        "source_manifest_status": "valid",
    }


class UploadManifestPassportTests(unittest.TestCase):
    def test_explicit_manifest_is_the_only_confirmed_link(self):
        relative_path = "20260914_120000/report.xls"
        digest = "a" * 64
        source_id = _identity(relative_path, digest)
        manifest = [{
            "source_id": source_id,
            "relative_path": relative_path,
            "sha256": digest,
            "account": "51",
            "period": "Январь 2026",
            "report_start": "2026-01-01",
            "report_end": "2026-01-31",
        }]

        passport = build_source_passport(
            _source(relative_path, digest), [_upload(manifest)])

        self.assertEqual(passport["status"], "complete")
        self.assertEqual(passport["files"][0]["association_status"], "explicit")
        self.assertEqual(passport["files"][0]["upload_id"], 7)
        self.assertEqual(passport["metadata_discrepancies"], [])

    def test_hash_mismatch_is_uncertain_and_never_timestamp_matched(self):
        relative_path = "20260914_120000/report.xls"
        actual_digest = "a" * 64
        persisted_digest = "b" * 64
        manifest = [{
            "source_id": _identity(relative_path, persisted_digest),
            "relative_path": relative_path,
            "sha256": persisted_digest,
            "account": "51",
            "period": "Январь 2026",
        }]

        passport = build_source_passport(
            _source(relative_path, actual_digest),
            [_upload(manifest, uploaded_at="2026-09-14T12:00:00Z")],
        )

        self.assertEqual(passport["status"], "partial")
        self.assertTrue(any(
            item["type"] == "source_manifest_mismatch"
            for item in passport["metadata_discrepancies"]
        ))
        self.assertEqual(
            passport["files"][0]["association_status"], "mismatch")

    def test_tampered_manifest_file_is_passport_only_not_calculated(self):
        company_id = 17
        with tempfile.TemporaryDirectory() as root:
            company_root = os.path.join(root, str(company_id))
            batch = os.path.join(company_root, "imap_20260914_120000")
            os.makedirs(batch)
            path = os.path.join(batch, "report.xls")
            with open(path, "wb") as stream:
                stream.write(b"replacement bytes")
            relative_path = "imap_20260914_120000/report.xls"
            persisted_digest = hashlib.sha256(b"accepted bytes").hexdigest()
            manifest = [{
                "source_id": _identity(relative_path, persisted_digest),
                "relative_path": relative_path,
                "sha256": persisted_digest,
                "account": "51",
                "period": "Январь 2026",
            }]

            with patch.object(
                    service, "parse_osv",
                    side_effect=AssertionError(
                        "tampered source must not be parsed")):
                paths, _owned, tampered, _candidates = (
                    service._manifest_source_paths(
                        [_upload(manifest)], company_root))
            source = service.assemble_source(paths, [], company_root)
            source["_source_manifest"].extend(tampered)
            passport = build_source_passport(source, [_upload(manifest)])

        self.assertEqual(paths, [])
        self.assertEqual(passport["accounts"], {})
        self.assertEqual(passport["files"][0]["status"], "tampered")
        self.assertEqual(
            passport["files"][0]["association_status"], "mismatch")
        self.assertTrue(any(
            item["type"] == "source_manifest_mismatch"
            for item in passport["metadata_discrepancies"]
        ))

    def test_missing_manifest_file_is_non_calculating_passport_evidence(self):
        relative_path = "imap_20260914_120000/missing.xls"
        digest = "a" * 64
        manifest = [{
            "source_id": _identity(relative_path, digest),
            "relative_path": relative_path,
            "sha256": digest,
            "account": "51",
            "period": "Январь 2026",
        }]
        source = _source(relative_path, digest)
        source["_source_manifest"] = []

        passport = build_source_passport(source, [_upload(manifest)])

        file_record = passport["files"][0]
        self.assertEqual(file_record["status"], "missing")
        self.assertEqual(file_record["association_status"], "manifest_missing")
        self.assertTrue(any(
            item["type"] == "source_manifest_file_missing"
            for item in passport["metadata_discrepancies"]
        ))
        self.assertEqual(
            passport["accounts"]["51"]["files"], [])

    def test_account_and_period_mismatch_is_uncertain(self):
        relative_path = "20260914_120000/report.xls"
        digest = "a" * 64
        manifest = [{
            "source_id": _identity(relative_path, digest),
            "relative_path": relative_path,
            "sha256": digest,
            "account": "62",
            "period": "Февраль 2026",
            "report_start": "2026-02-01",
            "report_end": "2026-02-28",
        }]

        passport = build_source_passport(
            _source(relative_path, digest), [_upload(manifest)])

        record = passport["files"][0]
        self.assertEqual(record["association_status"], "mismatch")
        fields = {
            mismatch["field"]
            for mismatch in record["association_mismatches"]
        }
        self.assertIn("account", fields)
        self.assertIn("period", fields)
        self.assertIn("report_start", fields)
        self.assertTrue(any(
            item["type"] == "source_manifest_mismatch"
            for item in passport["metadata_discrepancies"]
        ))

    def test_legacy_upload_has_visible_uncertainty_without_nearest_guess(self):
        relative_path = "20260914_120000/report.xls"
        digest = "a" * 64
        legacy_upload = {
            "id": 7,
            # Deliberately equal to the batch folder: this must not link.
            "uploaded_at": "2026-09-14T12:00:00Z",
            "period_label": "Январь 2026",
            "accounts_loaded": '["51"]',
            "source_manifest": None,
            "source_manifest_status": "absent",
        }

        passport = build_source_passport(
            _source(relative_path, digest), [legacy_upload])

        file_record = passport["files"][0]
        self.assertIsNone(file_record.get("upload_id"))
        self.assertEqual(file_record["association_status"], "legacy_unlinked")
        self.assertTrue(any(
            item["type"] == "file_upload_link_missing"
            for item in passport["metadata_discrepancies"]
        ))

    def test_empty_manifest_is_explicit_modern_allowlist(self):
        passport = build_source_passport(
            {"accounts": {}, "_source_manifest": []},
            [_upload([])],
        )

        self.assertEqual(passport["status"], "empty")
        self.assertEqual(passport["files"], [])
        self.assertEqual(passport["metadata_discrepancies"], [])

    def test_service_attaches_relative_path_hash_and_source_id(self):
        with tempfile.TemporaryDirectory() as root:
            batch = os.path.join(root, "20260914_120000")
            os.makedirs(batch)
            path = os.path.join(batch, "report.xls")
            with open(path, "wb") as stream:
                stream.write(b"accepted")

            parsed = {
                "acc": "51",
                "period": "Январь 2026",
                "rows": [],
                "daily": [],
                "warnings": [],
                "top": {
                    "snd": 0, "snk": 0, "obd": 0, "obk": 0,
                    "skd": 0, "skk": 0,
                },
            }
            with patch.object(service, "parse_osv", return_value=parsed):
                source = service.assemble_source([path], [], root)

        record = source["_source_manifest"][0]
        self.assertEqual(record["relative_path"],
                         "20260914_120000/report.xls")
        self.assertEqual(record["sha256"],
                         hashlib.sha256(b"accepted").hexdigest())
        self.assertEqual(record["source_id"],
                         _identity(record["relative_path"], record["sha256"]))

    def test_empty_manifest_period_still_calculates_with_unknown_period(self):
        company_id = 17
        with tempfile.TemporaryDirectory() as root:
            company_root = os.path.join(root, str(company_id))
            batch = os.path.join(company_root, "imap_20260104_120000")
            os.makedirs(batch)
            path = os.path.join(batch, "no_period.xls")
            with open(path, "wb") as stream:
                stream.write(b"accepted without period")
            relative_path = "imap_20260104_120000/no_period.xls"
            digest = hashlib.sha256(b"accepted without period").hexdigest()
            manifest = [{
                "source_id": _identity(relative_path, digest),
                "relative_path": relative_path,
                "sha256": digest,
                "account": "51",
                "period": "",
            }]
            parsed = {
                "acc": "51",
                "period": "",
                "rows": [{
                    "lvl": 0, "label": "Оборот",
                    "snd": 0, "snk": 0, "obd": 0, "obk": 0,
                    "skd": 10, "skk": 0,
                }],
                "daily": [],
                "warnings": [],
                "top": {
                    "snd": 0, "snk": 0, "obd": 0, "obk": 0,
                    "skd": 10, "skk": 0,
                },
            }

            with patch.object(service, "parse_osv", return_value=parsed):
                paths, _owned, tampered, _candidates = (
                    service._manifest_source_paths(
                        [_upload(manifest)], company_root))
                source = service.assemble_source(
                    paths, [], company_root)
            source["_source_manifest"].extend(tampered)
            passport = build_source_passport(source, [_upload(manifest)])

        self.assertEqual(paths, [os.path.realpath(path)])
        self.assertEqual(
            source["accounts"]["51"]["rows"][0]["label"], "Оборот")
        self.assertEqual(source["accounts"]["51"]["top"]["skd"], 10.0)
        self.assertEqual(passport["files"][0]["association_status"], "explicit")
        self.assertIsNone(passport["accounts"]["51"]["actual_period"]["start"])
        self.assertIsNone(passport["accounts"]["51"]["actual_period"]["end"])
        self.assertEqual(passport["status"], "partial")

    def test_runtime_imap_manifest_excludes_rejected_siblings(self):
        company_id = 17
        with tempfile.TemporaryDirectory() as root:
            company_root = os.path.join(root, str(company_id))
            batch = os.path.join(company_root, "imap_20260101_120000")
            os.makedirs(batch)
            path = os.path.join(batch, "accepted.xls")
            duplicate_path = os.path.join(batch, "duplicate.xls")
            invalid_path = os.path.join(batch, "invalid.xls")
            with open(path, "wb") as stream:
                stream.write(b"runtime accepted")
            for sibling in (duplicate_path, invalid_path):
                with open(sibling, "wb") as stream:
                    stream.write(b"rejected sibling")
            relative_path = "imap_20260101_120000/accepted.xls"
            digest = hashlib.sha256(b"runtime accepted").hexdigest()
            manifest = [{
                "source_id": _identity(relative_path, digest),
                "relative_path": relative_path,
                "sha256": digest,
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
            }]

            parsed = {
                "acc": "51",
                "period": "Январь 2026",
                "rows": [],
                "daily": [],
                "warnings": [],
                "top": {
                    "snd": 0, "snk": 0, "obd": 0, "obk": 0,
                    "skd": 0, "skk": 0,
                },
            }

            class _Column:
                def asc(self):
                    return self

                def __eq__(self, _other):
                    return self

            class _UploadRow:
                id = 7
                uploaded_at = "2026-02-01T00:00:00+00:00"
                period_label = "Январь 2026"
                accounts_loaded = '["51"]'
                files_count = 1
                source = "manual"
                dashboard_file = "dashboard_current.html"
                metrics_json = "{}"
                source_manifest_json = json.dumps(manifest)

            class _UploadQuery:
                def filter_by(self, **_filters):
                    return self

                def order_by(self, *_columns):
                    return self

                def all(self):
                    return [_UploadRow()]

            class _Upload:
                id = _Column()
                uploaded_at = _Column()
                query = _UploadQuery()

            class _DailyQuery:
                def filter_by(self, **_filters):
                    return self

                def order_by(self, *_columns):
                    return self

                def all(self):
                    return []

            class _DailyRow:
                id = _Column()
                uploaded_at = _Column()
                company_id = _Column()
                date = _Column()
                acc = _Column()
                query = _DailyQuery()

            class _Session:
                def query(self, *_columns):
                    return self

                def filter(self, *_conditions):
                    return self

                def one(self):
                    return 0, None, None

            class _Database:
                session = _Session()

            class _Functions:
                def count(self, *_columns):
                    return self

                def max(self, *_columns):
                    return self

            service._cache.clear()
            parsed_calls = []

            def parse(path_to_parse):
                parsed_calls.append(path_to_parse)
                if path_to_parse != path:
                    raise AssertionError(
                        "rejected manifest sibling was parsed")
                return parsed

            with patch.object(service, "Upload", _Upload), \
                    patch.object(service, "DailyRow", _DailyRow), \
                    patch.object(service, "db", _Database()), \
                    patch.object(service, "func", _Functions()), \
                    patch.object(service, "parse_osv", side_effect=parse):
                source, _updated_at = service.load_company_source(
                    company_id, root)

        self.assertEqual(
            source["_source_manifest"][0]["relative_path"], relative_path)
        self.assertEqual(parsed_calls, [path])
        passport_file = source["source_passport"]["files"][0]
        self.assertEqual(passport_file["relative_path"], relative_path)
        self.assertEqual(passport_file["association_status"], "explicit")
        self.assertEqual(passport_file["upload_id"], 7)
        self.assertEqual(source["source_passport"]["status"], "complete")

    def test_legacy_discovery_excludes_manifest_owned_batch(self):
        with tempfile.TemporaryDirectory() as root:
            batch = "20260101_120000"
            directory = os.path.join(root, "17", batch)
            os.makedirs(directory)
            for filename in ("accepted.xls", "rejected.xls"):
                with open(os.path.join(directory, filename), "wb") as stream:
                    stream.write(filename.encode())

            discovered = service.monthly_files(root, 17)
            excluded = service.monthly_files(
                root, 17, excluded_batches={batch})

        self.assertEqual(len(discovered), 2)
        self.assertEqual(excluded, [])

    def test_manifest_allowlist_wins_over_legacy_sibling_discovery(self):
        company_id = 17
        with tempfile.TemporaryDirectory() as root:
            company_root = os.path.join(root, str(company_id))
            batch = "20260102_120000"
            directory = os.path.join(company_root, batch)
            os.makedirs(directory)
            accepted = os.path.join(directory, "accepted.xls")
            rejected = os.path.join(directory, "rejected.xls")
            with open(accepted, "wb") as stream:
                stream.write(b"accepted")
            with open(rejected, "wb") as stream:
                stream.write(b"rejected")
            relative_path = f"{batch}/accepted.xls"
            digest = hashlib.sha256(b"accepted").hexdigest()
            manifest = [{
                "source_id": _identity(relative_path, digest),
                "relative_path": relative_path,
                "sha256": digest,
                "account": "51",
                "period": "Январь 2026",
            }]

            manifest_paths, owned_batches, _tampered, _candidates = (
                service._manifest_source_paths(
                    [_upload(manifest)], company_root))
            fallback = service.monthly_files(
                root, company_id, excluded_batches=owned_batches)

        self.assertEqual(manifest_paths, [os.path.realpath(accepted)])
        self.assertEqual(fallback, [])

    def test_daily_only_empty_manifest_does_not_scan_orphan_directory(self):
        company_id = 17
        with tempfile.TemporaryDirectory() as root:
            orphan_dir = os.path.join(
                root, str(company_id), "20260103_120000")
            os.makedirs(orphan_dir)
            with open(os.path.join(orphan_dir, "orphan.xls"), "wb") as stream:
                stream.write(b"must not be read")

            class _Column:
                def asc(self):
                    return self

                def __eq__(self, _other):
                    return self

            class _UploadRow:
                id = 8
                uploaded_at = "2026-01-03T12:00:00+00:00"
                period_label = ""
                accounts_loaded = "[]"
                files_count = 0
                source = "auto_imap"
                dashboard_file = ""
                metrics_json = "{}"
                source_manifest_json = "[]"

            class _UploadQuery:
                def filter_by(self, **_filters):
                    return self

                def order_by(self, *_columns):
                    return self

                def all(self):
                    return [_UploadRow()]

            class _Upload:
                id = _Column()
                uploaded_at = _Column()
                query = _UploadQuery()

            class _DailyQuery:
                def filter_by(self, **_filters):
                    return self

                def order_by(self, *_columns):
                    return self

                def all(self):
                    return []

            class _DailyRow:
                id = _Column()
                uploaded_at = _Column()
                company_id = _Column()
                date = _Column()
                acc = _Column()
                query = _DailyQuery()

            class _Session:
                def query(self, *_columns):
                    return self

                def filter(self, *_conditions):
                    return self

                def one(self):
                    return 0, None, None

            class _Database:
                session = _Session()

            class _Functions:
                def count(self, *_columns):
                    return self

                def max(self, *_columns):
                    return self

            service._cache.clear()
            with patch.object(service, "Upload", _Upload), \
                    patch.object(service, "DailyRow", _DailyRow), \
                    patch.object(service, "db", _Database()), \
                    patch.object(service, "func", _Functions()), \
                    patch.object(
                        service, "monthly_files",
                        side_effect=AssertionError(
                            "explicit empty manifest must disable fallback")), \
                    patch.object(
                        service, "parse_osv",
                        side_effect=AssertionError(
                            "orphan file must not be parsed")):
                source, _updated_at = service.load_company_source(
                    company_id, root)

        self.assertEqual(source["accounts"], {})
        self.assertEqual(source["source_passport"]["status"], "empty")
        self.assertEqual(source["source_passport"]["files"], [])

    def test_upload_metadata_keeps_manifest_in_revision(self):
        class Row:
            id = 7
            uploaded_at = "2026-09-14T12:00:00+00:00"
            period_label = "Январь 2026"
            accounts_loaded = '["51"]'
            files_count = 1
            source = "manual"
            dashboard_file = "dashboard_current.html"
            metrics_json = "{}"
            source_manifest_json = json.dumps([{
                "source_id": "id",
                "relative_path": "report.xls",
                "sha256": "a" * 64,
                "account": "51",
                "period": "Январь 2026",
            }])

        metadata = service._upload_metadata(Row())
        self.assertEqual(metadata["source_manifest_status"], "valid")
        self.assertEqual(len(metadata["source_manifest"]), 1)
        changed = dict(metadata, _source_manifest_json='different')
        self.assertNotEqual(
            service._upload_revision([metadata]),
            service._upload_revision([changed]),
        )


if __name__ == "__main__":
    unittest.main()