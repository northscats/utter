# -*- coding: utf-8 -*-
"""Acceptance tests for the transactional limits-version lifecycle.

This file intentionally owns its SQLite database.  It is useful to run by
itself while the service is being changed:

    PYTHONPATH=osv-dashboard python -m unittest test_limits_lifecycle

The parser is patched with small, semantic snapshots.  They retain the 68
column shape of the GIS REB export without requiring a workbook in every
test.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from openpyxl import load_workbook


_TMP = tempfile.mkdtemp(prefix="limits_lifecycle_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/limits.db"
os.environ["INITIAL_ADMIN_PASSWORD"] = "Cedar!river7Orbit#lantern"
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from database import (
    LimitChange,
    LimitComparison,
    LimitDataset,
    LimitObject,
    LimitRowSnapshot,
    LimitVersion,
    User,
    db,
    utcnow,
)
from limits_service import (
    LimitsError,
    accept_version,
    create_comparison,
    export_comparison,
    parse_version,
    purge_version,
    resolve_comparison_issue,
    stage_upload,
)


def snapshot(value, uin="100.200", row_number=5):
    """Return one 68-column, parser-compatible synthetic export snapshot."""
    semantic = [
        ("uin", "identifier"),
        ("list_type", "programme"),
        ("ap_status", "status"),
        ("gp_code", "programme"),
        ("pp_code", "programme"),
        ("om_code", "programme"),
        ("dm_code", "programme"),
        ("limit_cost", "limit_cost"),
        ("annual_funding_2026_moscow_oblast_budget", "annual_funding"),
    ]
    columns = [
        {
            "key": key,
            "label": key,
            "category": category,
            "year": None,
            "source": None,
        }
        for key, category in semantic
    ]
    columns.extend(
        {
            "key": f"extra_{index:02d}",
            "label": f"extra_{index:02d}",
            "category": "requisite",
            "year": None,
            "source": None,
        }
        for index in range(59)
    )
    values = {
        "uin": (uin, "text"),
        "list_type": ("x", "text"),
        "ap_status": ("1", "text"),
        "gp_code": ("1", "text"),
        "pp_code": ("2", "text"),
        "om_code": ("3", "text"),
        "dm_code": ("4", "text"),
        "limit_cost": (str(value), "money"),
        "annual_funding_2026_moscow_oblast_budget": (str(value), "money"),
    }
    cells = {
        key: {
            "raw": raw,
            "value": raw,
            "kind": kind,
            "cell": f"{chr(65 + index % 26)}{row_number}",
        }
        for index, (key, (raw, kind)) in enumerate(values.items())
    }
    cells["annual_funding_2026_moscow_oblast_budget"]["cell"] = f"AP{row_number}"
    return {
        "columns": columns,
        "warnings": [],
        "metadata": {},
        "rows": [{"uin": uin, "row": row_number, "cells": cells}],
    }


def ambiguous_snapshot(rows):
    """Build two unmatched rows plus any stable row for export assertions."""
    result = snapshot("0")
    result["rows"] = []
    for row_number, uin, om_code, value in rows:
        row = snapshot(value, uin, row_number)["rows"][0]
        row["row"] = row_number
        row["cells"]["om_code"]["raw"] = om_code
        row["cells"]["om_code"]["value"] = om_code
        result["rows"].append(row)
    return result


class LimitsLifecycleAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.fake_tombstone_sidecar = {}

        def fake_record(version_id, dataset_id, source_path='', source_sha256=''):
            cls.fake_tombstone_sidecar[int(version_id)] = {
                'version_id': int(version_id),
                'dataset_id': dataset_id,
                'source_path': source_path,
                'source_sha256': source_sha256,
            }
            return list(cls.fake_tombstone_sidecar.values())

        cls._tombstone_transport = patch(
            'durable_data.record_limit_tombstone',
            side_effect=fake_record)
        cls._tombstone_transport.start()
        cls.addClassCleanup(cls._tombstone_transport.stop)
        init_db()

    def setUp(self):
        self._app_context = app.app_context()
        self._app_context.push()
        db.session.rollback()
        db.session.remove()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        admin = User(username="limits-lifecycle-admin", password_hash="x", role="admin")
        db.session.add(admin)
        db.session.flush()
        dataset = LimitDataset(name="Lifecycle", owner_id=admin.id)
        db.session.add(dataset)
        db.session.commit()
        self.admin_id = admin.id
        self.dataset_id = dataset.id

    def tearDown(self):
        db.session.remove()
        self._app_context.pop()

    def _dataset(self):
        return db.session.get(LimitDataset, self.dataset_id)

    def _draft(self, value, previous=None, uin="100.200"):
        """Create and parse a queued version without touching a real XLSX."""
        path = os.path.join(_TMP, f"source-{self.dataset_id}-{value}-{uin.replace('.', '_')}.xlsx")
        with open(path, "wb") as stream:
            stream.write(f"source-{value}-{uin}".encode())
        version = LimitVersion(
            dataset_id=self.dataset_id,
            uploaded_by=self.admin_id,
            source_date=date(2026, 1, 1),
            source_name="synthetic.xlsx",
            source_path=path,
            sha256=(f"{value}-{uin}" * 64)[:64],
            status="queued",
            previous_version_id=previous,
        )
        db.session.add(version)
        db.session.commit()
        with patch("limits_parser.parse_xlsx", return_value=snapshot(value, uin)):
            parsed = parse_version(version.id)
        return db.session.get(LimitVersion, parsed.id)

    def _accept_first(self, value="10"):
        version = self._draft(value)
        accept_version(version.id, self.admin_id)
        return db.session.get(LimitVersion, version.id)

    def _direct_rotation_fixture(self):
        """Seed ten accepted snapshots and a review candidate for eviction tests."""
        dataset = self._dataset()
        obj = LimitObject(dataset_id=dataset.id, uin="100.200", title="Object")
        db.session.add(obj)
        db.session.flush()
        previous = None
        accepted = []
        for number in range(1, 11):
            path = os.path.join(_TMP, f"rotation-{self.dataset_id}-{number}.xlsx")
            with open(path, "wb") as stream:
                stream.write(f"rotation-{number}".encode())
            version = LimitVersion(
                dataset_id=self.dataset_id,
                uploaded_by=self.admin_id,
                source_date=date(2026, 1, number),
                source_name=f"{number}.xlsx",
                source_path=path,
                sha256=(f"rotation-{self.dataset_id}-{number}" * 8)[:64],
                status="accepted",
                previous_version_id=previous,
                accepted_by=self.admin_id,
                accepted_at=utcnow() + timedelta(seconds=number),
                snapshot_json=json.dumps(snapshot(str(number))),
            )
            db.session.add(version)
            db.session.flush()
            db.session.add(
                LimitRowSnapshot(
                    version_id=version.id,
                    object_id=obj.id,
                    source_row=5,
                    stable_key="100.200|x|1|1|2|3|4",
                    cells_json=json.dumps(snapshot(str(number))["rows"][0]["cells"]),
                )
            )
            accepted.append(version)
            previous = version.id
        obj.last_seen_version_id = accepted[-1].id
        obj.is_present = True
        dataset.current_version_id = accepted[-1].id
        candidate = LimitVersion(
            dataset_id=self.dataset_id,
            uploaded_by=self.admin_id,
            source_date=date(2026, 1, 12),
            source_name="11.xlsx",
            source_path=os.path.join(_TMP, f"rotation-{self.dataset_id}-11.xlsx"),
            sha256=(f"rotation-{self.dataset_id}-11" * 8)[:64],
            status="review",
            previous_version_id=accepted[-1].id,
            snapshot_json=json.dumps(snapshot("11")),
        )
        db.session.add(candidate)
        db.session.flush()
        db.session.add(
            LimitRowSnapshot(
                version_id=candidate.id,
                object_id=obj.id,
                source_row=5,
                stable_key="100.200|x|1|1|2|3|4",
                cells_json=json.dumps(snapshot("11")["rows"][0]["cells"]),
            )
        )
        comparison = LimitComparison(
            old_version_id=accepted[-1].id,
            new_version_id=candidate.id,
            status="ready",
            summary_json="{}",
            issues_json="[]",
        )
        db.session.add(comparison)
        db.session.flush()
        db.session.commit()
        return accepted, candidate, obj

    def test_draft_parse_is_atomic_and_has_no_limit_object_side_effect(self):
        version = self._draft("10")
        self.assertEqual("review", version.status)
        self.assertEqual(0, LimitObject.query.count())
        self.assertEqual(1, LimitRowSnapshot.query.filter_by(version_id=version.id).count())
        self.assertIsNone(LimitRowSnapshot.query.filter_by(version_id=version.id).one().object_id)
        self.assertIsNone(self._dataset().current_version_id)

    def test_first_baseline_is_accepted(self):
        first = self._accept_first()
        self.assertEqual("accepted", first.status)
        self.assertEqual(first.id, self._dataset().current_version_id)

    def test_second_accepted_update_preserves_financial_changes(self):
        first = self._accept_first()
        second = self._draft("15", previous=first.id)
        comparison = create_comparison(first.id, second.id)
        self.assertEqual("ready", comparison.status)
        accepted = accept_version(second.id, self.admin_id)
        self.assertEqual("accepted", accepted.status)
        self.assertEqual(second.id, self._dataset().current_version_id)
        self.assertTrue(LimitChange.query.filter_by(comparison_id=comparison.id).count())

    def test_duplicate_stage_returns_existing_version(self):
        from werkzeug.datastructures import FileStorage

        dataset = self._dataset()
        first, duplicate = stage_upload(
            dataset,
            FileStorage(stream=io.BytesIO(b"same workbook"), filename="limits.xlsx"),
            "limits.xlsx",
            date(2026, 1, 1),
            self.admin_id,
            _TMP,
        )
        second, duplicate_again = stage_upload(
            dataset,
            FileStorage(stream=io.BytesIO(b"same workbook"), filename="limits.xlsx"),
            "limits.xlsx",
            date(2026, 1, 1),
            self.admin_id,
            _TMP,
        )
        self.assertFalse(duplicate)
        self.assertTrue(duplicate_again)
        self.assertEqual(first.id, second.id)
        self.assertEqual(1, LimitVersion.query.count())

    def test_resolved_added_absent_rows_export_with_metadata_and_sources(self):
        old_data = ambiguous_snapshot([
            (5, "100.200", "01", "1"),
            (6, "100.200", "02", "2"),
            (7, "300.400", "01", "3"),
            (9, "200.300", "01", "10"),
        ])
        new_data = ambiguous_snapshot([
            (5, "100.200", "03", "3"),
            (7, "300.400", "03", "4"),
            (8, "300.400", "04", "5"),
            (9, "200.300", "01", "20"),
        ])
        old = LimitVersion(
            dataset_id=self.dataset_id,
            uploaded_by=self.admin_id,
            source_date=date(2026, 1, 1),
            source_name="old.xlsx",
            source_path=os.path.join(_TMP, "old-export.xlsx"),
            sha256="e" * 64,
            status="accepted",
            snapshot_json=json.dumps(old_data),
        )
        new = LimitVersion(
            dataset_id=self.dataset_id,
            uploaded_by=self.admin_id,
            source_date=date(2026, 2, 1),
            source_name="new.xlsx",
            source_path=os.path.join(_TMP, "new-export.xlsx"),
            sha256="f" * 64,
            status="review",
            snapshot_json=json.dumps(new_data),
        )
        db.session.add_all([old, new])
        db.session.commit()
        db.session.get(LimitDataset, self.dataset_id).current_version_id = old.id
        new.previous_version_id = old.id
        db.session.commit()
        comparison = create_comparison(old.id, new.id)
        issue = json.loads(comparison.issues_json)[0]
        self.assertEqual("ambiguous_row_match", issue["code"])
        from app import _limits_history_ready
        from database import Setting
        _limits_history_ready.set()
        Setting.set('limits_history_ready', 'ready')
        db.session.commit()
        app.config['WTF_CSRF_ENABLED'] = False
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
            session['password_version'] = 1
        decisions = [
            {"old_row": "100.200@5", "action": "absent"},
            {"new_row": "300.400@8", "action": "added"},
        ]
        for decision in decisions:
            comparison = db.session.get(LimitComparison, comparison.id)
            issue = next(
                item for item in json.loads(comparison.issues_json)
                if item["uin"] == ("100.200" if decision.get("old_row") else "300.400")
            )
            page = client.get(f'/limit-notifications/comparisons/{comparison.id}')
            self.assertEqual(200, page.status_code)
            selected = decision.get('old_row') or decision['new_row']
            self.assertIn(f'value="{selected}"', page.get_data(as_text=True))
            response = client.post(f'/limit-notifications/comparisons/{comparison.id}/resolve',
                                   data=dict(decision, issue_key=issue['key']))
            self.assertEqual(302, response.status_code)
        comparison = db.session.get(LimitComparison, comparison.id)
        self.assertEqual("ready", comparison.status)
        self.assertEqual([], json.loads(comparison.issues_json))
        response = client.post(f'/limit-notifications/versions/{new.id}/accept', data={'confirm':'1'})
        self.assertEqual(302, response.status_code)
        self.assertEqual('accepted', db.session.get(LimitVersion, new.id).status)

        workbook = load_workbook(export_comparison(comparison.id), data_only=True)
        self.assertEqual([
            "Сводка", "Новые объекты", "Отсутствующие объекты",
            "Финансирование", "Сроки", "Реквизиты и коды", "Проблемы данных",
        ], workbook.sheetnames)
        summary = {
            row[0]: row[1]
            for row in workbook["Сводка"].iter_rows(min_row=2, values_only=True)
            if row[0]
        }
        self.assertEqual(f"{old.id} / {old.source_date}", summary["Версия до"])
        self.assertEqual(f"{new.id} / {new.source_date}", summary["Версия после"])

        requisite_rows = list(
            workbook["Реквизиты и коды"].iter_rows(min_row=2, values_only=True)
        )
        self.assertEqual(
            ["row_absent", "row_added"],
            sorted(row[2] for row in requisite_rows if row[2] in {"row_absent", "row_added"}),
        )
        self.assertEqual(
            {
                ("100.200@5", ""),
                ("", "300.400@8"),
            },
            {(row[11] or "", row[12] or "") for row in requisite_rows
             if row[2] in {"row_absent", "row_added"}},
        )

        funding_rows = list(
            workbook["Финансирование"].iter_rows(min_row=2, values_only=True)
        )
        changed_funding = next(row for row in funding_rows if row[0] == "200.300")
        self.assertEqual("10", changed_funding[7])
        self.assertEqual("20", changed_funding[8])
        self.assertEqual("AP9", changed_funding[11])
        self.assertEqual("AP9", changed_funding[12])
        self.assertEqual(["УИН", "Категория", "Поле", "Наименование", "Год",
                          "Источник", "Единица", "Было", "Стало", "Разница",
                          "Изменение, %", "Ячейка до", "Ячейка после"],
                         [cell.value for cell in workbook["Финансирование"][1]])
        self.assertEqual(1, workbook["Проблемы данных"].max_row)

    def test_rotation_deletes_old_version_and_comparisons_both_sides(self):
        accepted, candidate, obj = self._direct_rotation_fixture()
        old = accepted[0]
        other = accepted[1]
        old_path = old.source_path
        reverse = LimitComparison(
            old_version_id=other.id,
            new_version_id=old.id,
            status="ready",
            summary_json="{}",
            issues_json="[]",
        )
        db.session.add(reverse)
        db.session.flush()
        db.session.add(
            LimitChange(
                comparison_id=reverse.id,
                object_id=obj.id,
                uin=obj.uin,
                category="money",
                field_key="limit_cost",
                field_label="Cost",
                old_json='"1"',
                new_json='"2"',
            )
        )
        db.session.commit()
        with open(old_path, "ab") as stream:
            stream.write(b"source exists")
        accept_version(candidate.id, self.admin_id)
        self.assertIsNone(db.session.get(LimitVersion, old.id))
        self.assertFalse(os.path.exists(old_path))
        self.assertIsNotNone(db.session.get(LimitObject, obj.id))
        self.assertFalse(
            LimitComparison.query.filter(
                (LimitComparison.old_version_id == old.id)
                | (LimitComparison.new_version_id == old.id)
            ).first()
        )
        self.assertIsNone(db.session.get(LimitVersion, other.id).previous_version_id)

    def test_stale_comparison_is_rejected_after_concurrent_accept(self):
        first, _, obj = self._direct_rotation_fixture()
        # The fixture has ten accepted versions; use its current version as
        # the common base and prepare two competing review versions.
        current = first[-1]
        stale = LimitVersion(
            dataset_id=self.dataset_id,
            uploaded_by=self.admin_id,
            source_date=date(2026, 2, 1),
            source_name="stale.xlsx",
            source_path=os.path.join(_TMP, "stale.xlsx"),
            sha256="s" * 64,
            status="review",
            previous_version_id=current.id,
            snapshot_json=json.dumps(snapshot("20")),
        )
        concurrent = LimitVersion(
            dataset_id=self.dataset_id,
            uploaded_by=self.admin_id,
            source_date=date(2026, 2, 2),
            source_name="concurrent.xlsx",
            source_path=os.path.join(_TMP, "concurrent.xlsx"),
            sha256="c" * 64,
            status="review",
            previous_version_id=current.id,
            snapshot_json=json.dumps(snapshot("21")),
        )
        db.session.add_all([stale, concurrent])
        db.session.flush()
        for version in (stale, concurrent):
            db.session.add(
                LimitRowSnapshot(
                    version_id=version.id,
                    object_id=obj.id,
                    source_row=5,
                    stable_key="100.200|x|1|1|2|3|4",
                    cells_json=json.dumps(snapshot("20")["rows"][0]["cells"]),
                )
            )
            db.session.add(
                LimitComparison(
                    old_version_id=current.id,
                    new_version_id=version.id,
                    status="ready",
                    summary_json="{}",
                    issues_json="[]",
                )
            )
        db.session.commit()
        accept_version(concurrent.id, self.admin_id)
        with self.assertRaises(LimitsError):
            accept_version(stale.id, self.admin_id)
        self.assertEqual(concurrent.id, self._dataset().current_version_id)
        self.assertEqual("review", db.session.get(LimitVersion, stale.id).status)


if __name__ == "__main__":
    unittest.main()