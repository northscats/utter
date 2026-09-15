# -*- coding: utf-8 -*-
"""Isolated regression tests for accepted monthly source manifests."""

import hashlib
import json
import os
import tempfile
import unittest
from datetime import date

from key_indicators import _period_bounds as indicator_period_bounds
from source_manifest import (
    SourceManifestError,
    accepted_parsed_files,
    build_manifest,
    dump_manifest,
    report_bounds,
)
from source_passport import _period_bounds as passport_period_bounds


class SourceManifestTests(unittest.TestCase):
    def test_manifest_is_relative_hashed_and_keeps_report_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            batch = os.path.join(root, "20260914_120000")
            os.makedirs(batch)
            path = os.path.join(batch, "ОСВ_счет_51.xlsx")
            payload = b"accepted report"
            with open(path, "wb") as stream:
                stream.write(payload)

            records = build_manifest(
                [(path, {
                    "acc": "51",
                    "period": "за январь–июнь 2026 г.",
                })],
                root,
            )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertTrue(record["source_id"])
        self.assertEqual(record["relative_path"],
                         "20260914_120000/ОСВ_счет_51.xlsx")
        self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(record["account"], "51")
        self.assertEqual(record["period"], "за январь–июнь 2026 г.")
        self.assertEqual(record["report_start"], "2026-01-01")
        self.assertEqual(record["report_end"], "2026-06-30")

    def test_manifest_is_explicit_empty_array_when_no_monthly_file_was_accepted(self):
        self.assertEqual(dump_manifest([]), "[]")

    def test_manifest_distinguishes_dated_rows_from_monthly_analytics(self):
        with tempfile.TemporaryDirectory() as root:
            monthly_path = os.path.join(root, "monthly.xls")
            daily_path = os.path.join(root, "daily.xls")
            for path in (monthly_path, daily_path):
                with open(path, "wb") as stream:
                    stream.write(path.encode())
            records = build_manifest([
                (monthly_path, {
                    "acc": "08",
                    "period": "Январь–Июнь 2026",
                    "daily": [],
                }),
                (daily_path, {
                    "acc": "08",
                    "period": "Январь–Июнь 2026",
                    "daily": [
                        {"date": "2026-02-07"},
                        {"date": "2026-02-07"},
                        {"date": "2026-03-02"},
                    ],
                }),
            ], root)

        self.assertEqual(records[0]["granularity"], "month")
        self.assertNotIn("observed_dates", records[0])
        self.assertEqual(records[1]["granularity"], "day")
        self.assertEqual(
            records[1]["observed_dates"], ["2026-02-07", "2026-03-02"])
        self.assertEqual(records[1]["observed_days"], 2)
        self.assertEqual(records[1]["observed_start"], "2026-02-07")
        self.assertEqual(records[1]["observed_end"], "2026-03-02")

    def test_only_parse_many_acceptances_enter_the_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            accepted = os.path.join(root, "accepted.xls")
            duplicate = os.path.join(root, "duplicate.xls")
            rejected = os.path.join(root, "rejected.xls")
            for path in (accepted, duplicate, rejected):
                with open(path, "wb") as stream:
                    stream.write(path.encode())
            parsed = {"51": {"acc": "51", "period": "Январь 2026"}}
            audit = [
                {"file": "accepted.xls", "acc": "51", "status": "разобран",
                 "ok": True},
                {"file": "duplicate.xls", "acc": "51",
                 "status": "ДУБЛЬ счета — файл пропущен", "ok": False},
                {"file": "rejected.xls", "acc": "?",
                 "status": "ОШИБКА: не ОСВ", "ok": False},
            ]
            result = accepted_parsed_files(
                (accepted, duplicate, rejected), parsed, audit)
        self.assertEqual(result, [(accepted, parsed["51"])])

    def test_manifest_does_not_allow_paths_outside_company_root(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.NamedTemporaryFile() as outside:
            with self.assertRaises(SourceManifestError):
                build_manifest(
                    [(outside.name, {"acc": "51", "period": "Январь 2026"})],
                    root,
                )

    def test_json_contract_is_an_array(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "file.xls")
            with open(path, "wb") as stream:
                stream.write(b"x")
            payload = dump_manifest(build_manifest(
                [(path, {"acc": "20", "period": "Январь 2026"})],
                root,
            ))
        self.assertIsInstance(json.loads(payload), list)

    def test_ambiguous_period_is_not_inferred_from_upload_time(self):
        self.assertEqual(report_bounds("период 2026"), (None, None))

    def test_month_period_boundaries_are_shared_by_producer_and_consumer(self):
        self.assertIs(passport_period_bounds, indicator_period_bounds)
        expected = {
            "Март 2026": (date(2026, 3, 1), date(2026, 3, 31)),
            "за январь–июнь 2026 г.": (
                date(2026, 1, 1), date(2026, 6, 30)),
        }
        for period, bounds in expected.items():
            self.assertEqual(indicator_period_bounds(period), bounds)
            self.assertEqual(passport_period_bounds(period), bounds)
            self.assertEqual(
                report_bounds(period),
                tuple(value.isoformat() for value in bounds),
            )

    def test_same_basename_files_follow_audit_acceptance_order(self):
        with tempfile.TemporaryDirectory() as root:
            first_dir = os.path.join(root, "first")
            second_dir = os.path.join(root, "second")
            os.makedirs(first_dir)
            os.makedirs(second_dir)
            first = os.path.join(first_dir, "report.xlsx")
            second = os.path.join(second_dir, "report.xlsx")
            for path in (first, second):
                with open(path, "wb") as stream:
                    stream.write(path.encode())
            parsed = {
                "51": {"acc": "51", "period": "Январь 2026"},
                "62": {"acc": "62", "period": "Февраль 2026"},
            }
            audit = [
                {"file": "report.xlsx", "acc": "51",
                 "status": "разобран", "ok": True},
                {"file": "report.xlsx", "acc": "62",
                 "status": "разобран", "ok": True},
                {"file": "report.xlsx", "acc": "51",
                 "status": "ДУБЛЬ счета — файл пропущен", "ok": False},
            ]
            accepted = accepted_parsed_files(
                [second, first], parsed, audit)

        self.assertEqual([path for path, _parsed in accepted], [first, second])
        self.assertEqual(
            [parsed_result["acc"] for _path, parsed_result in accepted],
            ["51", "62"])


if __name__ == "__main__":
    unittest.main()
