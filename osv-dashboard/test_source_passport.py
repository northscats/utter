"""Isolated source-passport contract tests.

These tests do not import the Flask application or connect to its database.
Every source and Upload value is an in-memory fixture.
"""

import copy
import hashlib
import json
import unittest

from key_indicators import build_indicator_source
from key_indicators_service import _upload_revision, assemble_source
from source_passport import (
    build_period_passport,
    build_source_passport,
    enrich_source_passport,
)


def _report(period, source_file, amount, dates, top=None):
    return {
        "acc": "51",
        "period": period,
        "source_file": source_file,
        "rows": [],
        "daily": [
            {
                "date": value,
                "month": value[:7],
                "path1": "",
                "path2": "",
                "path3": "",
                "obd": amount,
                "obk": 0,
            }
            for value in dates
        ],
        "top": top or {
            "snd": 10,
            "snk": 0,
            "obd": amount,
            "obk": 0,
            "skd": 20,
            "skk": 0,
        },
        "warnings": [],
        "check_ok": True,
    }


def _manifest_record(relative_path, digest, account, period,
                     report_start=None, report_end=None):
    record = {
        "source_id": hashlib.sha256(
            f"{relative_path}\0{digest}".encode("utf-8")).hexdigest(),
        "relative_path": relative_path,
        "sha256": digest,
        "account": account,
        "period": period,
    }
    if report_start is not None:
        record["report_start"] = report_start
    if report_end is not None:
        record["report_end"] = report_end
    return record


class SourcePassportTests(unittest.TestCase):
    def test_duplicate_balance_precedence_is_not_filename_order(self):
        older = _report("за январь 2026 г.", "z-old.xls", 0, [],
                        top={"snd": 10, "snk": 0, "skd": 10, "skk": 0})
        newer = _report("за январь 2026 г.", "a-new.xls", 0, [],
                        top={"snd": 20, "snk": 0, "skd": 20, "skk": 0})
        newer["balance_reports"] = [older, copy.deepcopy(newer)]
        source = build_indicator_source({"51": newer})
        passport = build_source_passport(source, [])
        exact = passport["accounts"]["51"]["exact_balances"]
        for role in ("opening", "closing"):
            self.assertEqual(exact[role]["debit"], 20)
            self.assertEqual(exact[role]["source_file"], "a-new.xls")
        source["source_passport"] = passport
        period = build_period_passport(source, {"period": {
            "start": "2026-01-01", "end": "2026-01-31",
            "date": "2026-01-31"}, "cards": []})
        self.assertEqual(period["accounts"]["51"]["balances"]["closing"]["debit"], 20)

    def source(self, reports, manifest):
        parsed = {}
        for report in reports:
            parsed.setdefault(report["acc"], report)
        source = build_indicator_source(parsed)
        source["_source_manifest"] = manifest
        return source

    def test_upload_timestamp_is_separate_from_actual_report_period(self):
        source = self.source(
            [_report(
                "за январь 2026 г.", "january.xls", 10,
                ["2026-01-01", "2026-01-02"],
            )],
            [{
                "source_file": "january.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "за январь 2026 г.",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
                "daily_dates": ["2026-01-01", "2026-01-02"],
            }],
        )
        passport = build_source_passport(source, [{
            "id": 7,
            "uploaded_at": "2026-02-14T09:00:00+00:00",
            "period_label": "Январь 2026",
            "accounts_loaded": '["51"]',
            "files_count": 1,
        }])

        self.assertEqual(passport["latest_upload_at"], "2026-02-14T09:00:00+00:00")
        self.assertEqual(
            passport["actual_period"],
            {"start": "2026-01-01", "end": "2026-01-31"},
        )
        self.assertEqual(passport["available_accounts"], ["51"])
        types = {item["type"] for item in passport["metadata_discrepancies"]}
        self.assertIn("file_upload_link_missing", types)
        self.assertIn("upload_without_reliably_matched_files", types)
        json.dumps(passport, ensure_ascii=False)

    def test_metadata_is_compared_with_parsed_files_not_used_as_source(self):
        relative_path = "20260201_000000/actual.xls"
        digest = "a" * 64
        manifest_record = _manifest_record(
            relative_path, digest, "51", "Январь 2026",
            "2026-01-01", "2026-01-31")
        source = self.source(
            [_report("Январь 2026", "actual.xls", 10, ["2026-01-01"])],
            [{
                "source_file": "actual.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
                "relative_path": relative_path,
                "sha256": digest,
                "source_id": manifest_record["source_id"],
            }],
        )
        passport = build_source_passport(source, [{
            "id": 8,
            "uploaded_at": "2026-02-01T00:00:00+00:00",
            "period_label": "Февраль 2026",
            "accounts_loaded": '["20", "51"]',
            "files_count": 9,
            "source_manifest": [manifest_record],
            "source_manifest_status": "valid",
        }])

        self.assertEqual(passport["available_accounts"], ["51"])
        types = {item["type"] for item in passport["metadata_discrepancies"]}
        self.assertIn("metadata_accounts_missing_in_source", types)
        self.assertIn("metadata_period_mismatch", types)
        self.assertIn("metadata_files_count_mismatch", types)

    def test_exact_boundary_balances_and_turnover_gaps_are_preserved(self):
        source = self.source(
            [_report(
                "за январь 2026 г.", "january.xls", 10,
                ["2026-01-01", "2026-01-03"],
                top={
                    "snd": 11,
                    "snk": 2,
                    "obd": 10,
                    "obk": 4,
                    "skd": 17,
                    "skk": 6,
                },
            )],
            [{
                "source_file": "january.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "за январь 2026 г.",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
                "daily_dates": ["2026-01-01", "2026-01-03"],
            }],
        )
        passport = build_source_passport(source)
        account = passport["accounts"]["51"]

        self.assertEqual(account["exact_balances"]["opening"]["date"], "2026-01-01")
        self.assertEqual(account["exact_balances"]["opening"]["debit"], 11)
        self.assertEqual(account["exact_balances"]["closing"]["date"], "2026-01-31")
        self.assertEqual(account["exact_balances"]["closing"]["credit"], 6)
        coverage = account["turnover_coverage"]
        # The report covers January even when dated turnover rows exist only
        # for two days: zero-transaction days are not missing source days.
        self.assertEqual(coverage["covered_days"], 31)
        self.assertEqual(coverage["expected_days"], 31)
        self.assertEqual(coverage["observed_days"], 2)
        self.assertEqual(coverage["gaps"], [])

    def test_duplicate_and_overlap_evidence_is_not_hidden_by_latest_wins(self):
        reports = [
            _report("Январь 2026", "old.xls", 10, ["2026-01-01"]),
            _report("Январь 2026", "same-period.xls", 20, ["2026-01-02"]),
            _report("за 15.01.2026 - 15.02.2026", "overlap.xls", 30, ["2026-01-15"]),
        ]
        manifest = [
            {
                "source_file": name,
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": period,
                "report_start": start,
                "report_end": end,
            }
            for name, period, start, end in (
                ("old.xls", "Январь 2026", "2026-01-01", "2026-01-31"),
                ("same-period.xls", "Январь 2026", "2026-01-01", "2026-01-31"),
                ("overlap.xls", "за 15.01.2026 - 15.02.2026",
                 "2026-01-15", "2026-02-15"),
            )
        ]
        passport = build_source_passport(self.source(reports, manifest))

        self.assertEqual(len(passport["duplicates"]), 1)
        self.assertEqual(len(passport["overlaps"]), 1)
        self.assertEqual(passport["overlaps"][0]["pair_count"], 2)
        self.assertEqual(
            {item["type"] for item in passport["overlaps"]},
            {"overlapping_period"},
        )

    def test_period_passport_explains_unavailable_card_without_changing_value(self):
        source = self.source(
            [_report("Январь 2026", "january.xls", 10, ["2026-01-01"])],
            [{
                "source_file": "january.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
                "upload_id": 3,
            }],
        )
        enrich_source_passport(source, [{
            "id": 3,
            "uploaded_at": "2026-02-02T00:00:00+00:00",
            "period_label": "Январь 2026",
            "accounts_loaded": '["51"]',
            "files_count": 1,
        }])
        payload = {
            "period": {
                "start": "2026-01-01",
                "end": "2026-01-31",
                "label": "Январь 2026",
            },
            "cards": [{
                "id": "cash",
                "title": "Денежные средства",
                "value": None,
                "status": "unavailable",
                "reason": "Нет точного сальдо",
            }],
        }
        period = build_period_passport(source, payload)
        card = period["cards"][0]
        self.assertFalse(card["available"])
        self.assertEqual(card["reason"], "Нет точного сальдо")
        self.assertEqual(card["missing_accounts"], ["50", "52", "55"])
        self.assertEqual(period["accounts"]["51"]["turnover_coverage"]["status"],
                         "complete")
        self.assertEqual(period["actual_period"]["end"], "2026-01-31")
        self.assertEqual(
            period["latest_upload_at"], "2026-02-02T00:00:00+00:00")
        self.assertEqual(period["uploads"][0]["id"], 3)

    def test_selected_period_outside_report_is_gap_not_excluded_denominator(self):
        source = self.source(
            [_report("Январь 2026", "january.xls", 10, ["2026-01-01"])],
            [{
                "source_file": "january.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
            }],
        )
        enrich_source_passport(source, [{
            "id": 3,
            "uploaded_at": "2026-02-02T00:00:00+00:00",
            "period_label": "Январь 2026",
            "accounts_loaded": '["51"]',
            "files_count": 1,
        }])
        period = build_period_passport(source, {
            "period": {
                "start": "2026-02-01",
                "end": "2026-02-28",
                "label": "Февраль 2026",
            },
            "cards": [],
        })
        coverage = period["turnover_coverage"]
        self.assertEqual(coverage["expected_days"], 28)
        self.assertEqual(coverage["covered_days"], 0)
        self.assertEqual(coverage["coverage_ratio"], 0)
        self.assertEqual(coverage["gaps"], [{
            "start": "2026-02-01",
            "end": "2026-02-28",
            "days": 28,
        }])
        self.assertEqual(
            period["accounts"]["51"]["turnover_coverage"]["gaps"],
            coverage["gaps"],
        )
        json.dumps(period, ensure_ascii=False)

    def test_upload_association_does_not_guess_nearest_week(self):
        source = self.source(
            [_report("Январь 2026", "january.xls", 10, ["2026-01-01"])],
            [{
                "source_file": "january.xls",
                "path": "/uploads/1/20260101_120000/january.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
            }],
        )
        passport = build_source_passport(source, [{
            "id": 44,
            "uploaded_at": "2026-01-05T12:00:00+00:00",
            "period_label": "Январь 2026",
            "accounts_loaded": '["51"]',
            "files_count": 1,
        }])
        file = passport["files"][0]
        self.assertEqual(file["association_status"], "legacy_unlinked")
        self.assertIsNone(file.get("upload_id"))
        self.assertIn(
            "file_upload_link_missing",
            {item["type"] for item in passport["metadata_discrepancies"]},
        )
        self.assertIn(
            "upload_without_reliably_matched_files",
            {item["type"] for item in passport["metadata_discrepancies"]},
        )

    def test_daily_observation_opening_and_closing_are_normalised(self):
        source = self.source(
            [_report("Январь 2026", "january.xls", 10, ["2026-01-01"])],
            [{
                "source_file": "january.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
            }],
        )
        source["accounts"]["51"]["balance_observations"] = [{
            "date": "2026-01-01",
            "snd": 101,
            "snk": 7,
            "skd": 111,
            "skk": 9,
        }, {
            "date": "2026-01-31",
            "snd": 111,
            "snk": 9,
            "skd": 121,
            "skk": 11,
        }]
        enrich_source_passport(source, [])
        period = build_period_passport(source, {
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "cards": [],
        })
        balances = period["accounts"]["51"]["balances"]
        self.assertEqual(balances["opening"]["opening_debit"], 101)
        self.assertEqual(balances["opening"]["opening_credit"], 7)
        self.assertEqual(balances["opening"]["kind"], "daily_opening")
        self.assertEqual(balances["closing"]["debit"], 121)
        self.assertEqual(balances["closing"]["credit"], 11)
        self.assertEqual(balances["closing"]["kind"], "daily_closing")
        self.assertNotIn("opening_debit", balances["closing"])
        self.assertNotIn("opening_credit", balances["closing"])

    def test_dated_only_account_is_partial_not_complete(self):
        report = _report("", "dated-only.xls", 10, ["2026-01-15"])
        source = self.source([report], [{
            "source_file": "dated-only.xls",
            "kind": "osv",
            "status": "parsed",
            "account": "51",
            "period": "",
            "daily_dates": ["2026-01-15"],
        }])
        passport = build_source_passport(source)
        self.assertEqual(
            passport["accounts"]["51"]["turnover_coverage"]["status"],
            "observed_without_declared_period",
        )
        self.assertEqual(passport["status"], "partial")

    def test_duplicate_findings_are_aggregated_for_cumulative_uploads(self):
        source = self.source(
            [_report("Январь 2026", "base.xls", 10, ["2026-01-01"])],
            [{
                "source_file": f"copy-{index}.xls",
                "kind": "osv",
                "status": "parsed",
                "account": "51",
                "period": "Январь 2026",
                "report_start": "2026-01-01",
                "report_end": "2026-01-31",
            } for index in range(1000)],
        )
        passport = build_source_passport(source)
        self.assertEqual(len(passport["duplicates"]), 1)
        finding = passport["duplicates"][0]
        self.assertEqual(finding["file_count"], 1000)
        self.assertEqual(len(finding["files"]), 8)
        self.assertTrue(finding["truncated"])
        self.assertLessEqual(len(passport["accounts"]["51"]["duplicates"]), 1)

    def test_account_gaps_are_not_masked_by_another_account_union(self):
        jan = _report("Январь 2026", "51-jan.xls", 10, ["2026-01-01"])
        mar = _report("Март 2026", "51-mar.xls", 10, ["2026-03-01"])
        feb = _report("Февраль 2026", "62-feb.xls", 10, ["2026-02-01"])
        feb["acc"] = "62"
        reports = [jan, mar, feb]
        manifest = [{
            "source_file": filename,
            "kind": "osv",
            "status": "parsed",
            "account": account,
            "period": label,
            "report_start": start,
            "report_end": end,
        } for filename, account, label, start, end in (
            ("51-jan.xls", "51", "Январь 2026", "2026-01-01", "2026-01-31"),
            ("51-mar.xls", "51", "Март 2026", "2026-03-01", "2026-03-31"),
            ("62-feb.xls", "62", "Февраль 2026", "2026-02-01", "2026-02-28"),
        )]
        source = self.source(reports, manifest)
        passport = build_source_passport(source)
        coverage = passport["turnover_coverage"]
        self.assertEqual(passport["status"], "partial")
        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["coverage_basis"], "account_day_weighted")
        self.assertLess(coverage["coverage_ratio"], 1)
        self.assertEqual(coverage["expected_days"], 118)
        self.assertEqual(coverage["covered_days"], 90)

        period = build_period_passport(source, {
            "period": {"start": "2026-01-01", "end": "2026-03-31"},
            "cards": [],
        })
        self.assertEqual(period["period_status"], "partial")
        self.assertEqual(period["turnover_coverage"]["status"], "partial")
        self.assertEqual(period["turnover_coverage"]["coverage_ratio"], 0.5)

    def test_daily_upload_time_is_latest_source_freshness(self):
        source = assemble_source([], [{
            "acc": "51",
            "date": "2026-03-15",
            "snd": 1,
            "snk": 0,
            "obd": 0,
            "obk": 0,
            "skd": 1,
            "skk": 0,
            "uploaded_at": "2026-03-20T12:00:00+00:00",
        }])
        self.assertEqual(
            source["source_daily_uploaded_at"],
            ["2026-03-20T12:00:00+00:00"],
        )
        # An older monthly Upload must not hide the explicitly identified
        # newer DailyRow upload timestamp.
        enrich_source_passport(source, [{
            "id": 91,
            "uploaded_at": "2026-03-01T12:00:00+00:00",
            "period_label": "Февраль 2026",
            "accounts_loaded": '["51"]',
            "files_count": 0,
        }])
        self.assertEqual(
            source["source_passport"]["latest_upload_at"],
            "2026-03-20T12:00:00+00:00",
        )

    def test_metadata_json_changes_invalidate_upload_revision(self):
        base = {
            "id": 1,
            "uploaded_at": "2026-01-01T00:00:00+00:00",
            "period_label": "Январь 2026",
            "accounts_loaded": '["51"]',
            "files_count": 1,
            "source": "manual",
            "dashboard_file": "dashboard_current.html",
            "metrics_json": '{"revenue": 1}',
        }
        changed = copy.deepcopy(base)
        changed["accounts_loaded"] = '["51", "62"]'
        changed["metrics_json"] = '{"revenue": 2}'
        self.assertNotEqual(_upload_revision([base]), _upload_revision([changed]))


if __name__ == "__main__":
    unittest.main()