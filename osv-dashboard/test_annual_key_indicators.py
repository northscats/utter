# -*- coding: utf-8 -*-
"""Isolated annual-key-indicator API regressions (temporary SQLite only)."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


_TMP = tempfile.TemporaryDirectory(prefix="annual-key-indicators-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP.name, "annual.db")

import app as app_module
from annual_key_indicators import build_annual_key_indicators
from database import (
    Company, CompanyMembership, HistoricalImport, HistoricalMetricValue,
    HistoricalRawValue, HistoricalYearVersion, User, UserModuleAccess, db,
)
from historical_parser import canonical_metric_key


class AnnualKeyIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(
            TESTING=True, WTF_CSRF_ENABLED=False,
            HISTORICAL_IMPORT_ENABLED=True)
        cls.imap_patch = patch.object(app_module, "_ensure_imap_thread")
        cls.imap_patch.start()
        with cls.app.app_context():
            db.create_all()
            cls.admin = User(username="annual_admin", password_hash="x", role="admin")
            cls.allowed = User(username="annual_allowed", password_hash="x", role="user")
            cls.outsider = User(username="annual_outsider", password_hash="x", role="user")
            db.session.add_all([cls.admin, cls.allowed, cls.outsider])
            db.session.flush()
            cls.company = Company(name="Годовая компания", created_by=cls.admin.id)
            cls.other = Company(name="Другая компания", created_by=cls.admin.id)
            db.session.add_all([cls.company, cls.other])
            db.session.flush()
            for user in (cls.allowed, cls.outsider):
                db.session.add(UserModuleAccess(user_id=user.id, module_key="analytics"))
            db.session.add(CompanyMembership(
                user_id=cls.allowed.id, company_id=cls.company.id))
            db.session.commit()
            cls.user_ids = {
                "admin": cls.admin.id,
                "allowed": cls.allowed.id,
                "outsider": cls.outsider.id,
            }
            # Keep scalar identity attributes available after leaving context.
            cls.company.id
            cls.company.name
            cls.other.id
            cls.other.name

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        cls.imap_patch.stop()
        _TMP.cleanup()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.rollback()
        HistoricalRawValue.query.delete()
        HistoricalMetricValue.query.delete()
        HistoricalYearVersion.query.delete()
        HistoricalImport.query.delete()
        db.session.commit()

    def tearDown(self):
        if self.ctx is not None:
            db.session.remove()
            self.ctx.pop()

    def _client(self, user=None):
        client = self.app.test_client()
        if user is not None:
            user_id = self.user_ids[user] if isinstance(user, str) else user
            with client.session_transaction() as session:
                session["_user_id"] = str(user_id)
                session["_fresh"] = True
        return client

    def _url(self, company=None, query=""):
        cid = (company or self.company).id
        return f"/company/{cid}/key-indicators/annual-data{query}"

    def _version(self, company, year, version_no=1, active=True,
                 status="active", filename=None, activated=None):
        attempt = HistoricalImport(
            company_id=company.id, uploaded_by=self.admin.id,
            original_filename=filename or f"{company.id}-{year}.xlsx",
            stored_path="/must/not/be/read.xlsx", file_hash=f"{company.id}{year}{version_no}",
            status=status, created_at=datetime(year, 1, 2, tzinfo=timezone.utc))
        version = HistoricalYearVersion(
            historical_import=attempt, company_id=company.id, year=year,
            version_no=version_no, is_active=active,
            activated_at=activated or datetime(year, 2, 3, tzinfo=timezone.utc),
            activated_by=self.admin.id)
        db.session.add(version)
        db.session.flush()
        return version

    def _metric(self, version, section, label, value, unit,
                missing=False, upper=False, cell="B2"):
        metric = HistoricalMetricValue(
            year_version=version, section_key=section, section_label=section,
            metric_key=canonical_metric_key(label), metric_label=label,
            series_key="value", period_label=str(version.year),
            value_numeric=value, unit=unit, is_missing=missing,
            is_upper_bound=upper)
        metric.raw_value = HistoricalRawValue(
            raw_text="" if missing else str(value),
            sheet_name=section, cell_ref=cell)
        db.session.add(metric)
        return metric

    def test_empty_contract_has_all_cards(self):
        payload = build_annual_key_indicators(self.company)
        self.assertEqual(payload["years"], [])
        self.assertIsNone(payload["year"])
        self.assertEqual(len(payload["cards"]), 10)
        self.assertTrue(all(card["value"] is None for card in payload["cards"]))

    def test_units_zero_bounds_negative_and_gaps(self):
        v2022 = self._version(self.company, 2022)
        v2024 = self._version(self.company, 2024)
        for version, revenue, profit in ((v2022, 0, -5), (v2024, 12.5, 2)):
            self._metric(version, "financial_results", "Выручка",
                         revenue, "thousand_rubles")
            self._metric(version, "financial_results", "Чистая прибыль (убыток)",
                         profit, "thousand_rubles")
        self._metric(v2024, "liquidity",
                     "Коэффициент текущей (общей) ликвидности",
                     0.01, "ratio", upper=True)
        db.session.commit()
        payload = build_annual_key_indicators(self.company, 2024)
        cards = {card["id"]: card for card in payload["cards"]}
        self.assertEqual(payload["timeline_years"], [2022, 2023, 2024])
        self.assertIsNone(payload["previous_year"])
        self.assertEqual(cards["revenue"]["value"], 12500.0)
        self.assertEqual(cards["revenue"]["series"][0]["value"], 0.0)
        self.assertIsNone(cards["revenue"]["series"][1]["value"])
        self.assertTrue(cards["current_liquidity"]["upper_bound"])
        self.assertIsNone(cards["revenue"]["delta"])  # never jumps over 2023

        v2023 = self._version(self.company, 2023)
        self._metric(v2023, "financial_results", "Чистая прибыль (убыток)",
                     -5, "thousand_rubles")
        db.session.commit()
        payload = build_annual_key_indicators(self.company, 2024)
        profit = next(card for card in payload["cards"] if card["id"] == "net_profit")
        self.assertEqual(profit["delta"], 7000.0)
        self.assertIsNone(profit["delta_percent"])
        self.assertIn("отрицательной", profit["comparison_note"])

    def test_money_value_ignores_percent_companion_and_marks_previous_bound(self):
        previous = self._version(self.company, 2024)
        current = self._version(self.company, 2025)
        self._metric(previous, "financial_results", "Выручка", 8,
                     "thousand_rubles", upper=True, cell="B2")
        self._metric(previous, "financial_results", "Выручка", 40,
                     "percent", cell="C2").series_key = "percent"
        self._metric(current, "financial_results", "Выручка", 10,
                     "thousand_rubles", cell="D2")
        self._metric(current, "financial_results", "Выручка", 50,
                     "percent", cell="E2").series_key = "percent"
        db.session.commit()

        payload = build_annual_key_indicators(self.company, 2025)
        revenue = next(card for card in payload["cards"]
                       if card["id"] == "revenue")
        self.assertEqual(revenue["value"], 10000.0)
        self.assertEqual(revenue["previous_value"], 8000.0)
        self.assertTrue(revenue["previous_upper_bound"])
        self.assertIsNone(revenue["delta"])
        self.assertIn("верхней границей", revenue["comparison_note"])
        self.assertEqual(revenue["provenance"]["cell"], "D2")

    def test_only_active_accepted_tenant_rows_and_version_switch(self):
        old = self._version(self.company, 2025, version_no=1, active=False)
        active = self._version(
            self.company, 2025, version_no=2, active=True,
            activated=datetime(2025, 5, 1, tzinfo=timezone.utc))
        preview = self._version(self.company, 2024, status="preview")
        foreign = self._version(self.other, 2023)
        for version, value in ((old, 1), (active, 2), (preview, 3), (foreign, 9)):
            self._metric(version, "financial_results", "Выручка",
                         value, "thousand_rubles")
        db.session.commit()
        payload = build_annual_key_indicators(self.company)
        self.assertEqual(payload["years"], [2025])
        self.assertEqual(payload["cards"][0]["value"], 2000.0)
        self.assertEqual(payload["sources"][0]["version"], "2")
        first_updated = payload["updated_at"]

        HistoricalYearVersion.query.filter_by(
            company_id=self.company.id, year=2025, is_active=True
        ).update({"is_active": False}, synchronize_session=False)
        db.session.flush()
        old.is_active = True
        old.activated_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
        db.session.commit()
        switched = build_annual_key_indicators(self.company)
        self.assertEqual(switched["cards"][0]["value"], 1000.0)
        self.assertNotEqual(switched["updated_at"], first_updated)

    def test_route_auth_scope_year_and_unit_mismatch(self):
        version = self._version(self.company, 2025)
        self._metric(version, "financial_results", "Выручка", 10, "ratio")
        db.session.commit()
        # Do not hold a shared application context while issuing requests:
        # Flask-Login caches current_user on that context.
        self.ctx.pop()
        self.ctx = None
        # These are fetch requests: use the same Accept header as both the
        # operational and annual key-indicator frontends.
        headers = {"Accept": "application/json"}
        self.assertEqual(
            self._client().get(self._url(), headers=headers).status_code, 302)
        self.assertEqual(
            self._client("outsider").get(
                self._url(), headers=headers).status_code, 403)
        self.assertEqual(
            self._client("allowed").get(
                self._url(self.other), headers=headers).status_code, 403)
        response = self._client("allowed").get(
            self._url(query="?year=2024"), headers=headers)
        self.assertEqual(response.status_code, 400)
        response = self._client("allowed").get(
            self._url(query="?year=bad"), headers=headers)
        self.assertEqual(response.status_code, 400)
        response = self._client("allowed").get(
            self._url(query="?year=2025"), headers=headers)
        self.assertEqual(response.status_code, 200)
        revenue = response.get_json()["cards"][0]
        self.assertIsNone(revenue["value"])
        self.assertIn("Единица", revenue["missing_reason"])
        self.assertEqual(revenue["provenance"]["cell"], "B2")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")


if __name__ == "__main__":
    unittest.main()