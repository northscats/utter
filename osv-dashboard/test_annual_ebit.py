# -*- coding: utf-8 -*-
"""Focused EBIT parser/API regressions.

This module deliberately owns a separate temporary database so the persisted
legacy-unit case cannot depend on the annual stability fixture.
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


_TMP = tempfile.TemporaryDirectory(prefix="annual-ebit-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    _TMP.name, "annual-ebit.db")

import app as app_module
from annual_key_indicators import build_annual_key_indicators
from database import (
    Company,
    HistoricalImport,
    HistoricalMetricValue,
    HistoricalRawValue,
    HistoricalYearVersion,
    User,
    db,
)
from historical_parser import (
    _unit,
    canonical_metric_key,
    normalize_name,
    parse_historical_xlsx,
)
from historical_service import _persist_version_values


_SAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "attached_assets",
    "Balashikha_Kommunalnye_Sistemy_2022_2025_1788419445681.xlsx",
)
_EBIT = "EBIT (прибыль до % и налогов)"
_INTEREST = "Проценты к уплате"


class AnnualEbitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            HISTORICAL_IMPORT_ENABLED=True,
        )
        cls.imap_patch = patch.object(app_module, "_ensure_imap_thread")
        cls.imap_patch.start()
        with cls.app.app_context():
            db.create_all()
            cls.admin = User(
                username="annual_ebit_admin",
                password_hash="x",
                role="admin",
            )
            db.session.add(cls.admin)
            db.session.flush()
            cls.company = Company(
                name="Годовой EBIT",
                created_by=cls.admin.id,
            )
            db.session.add(cls.company)
            db.session.commit()
            cls.admin_id = cls.admin.id
            cls.company_id = cls.company.id
            cls.company.id
            cls.company.name

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
        db.session.remove()
        self.ctx.pop()

    def _version(self, year):
        historical_import = HistoricalImport(
            company_id=self.company_id,
            uploaded_by=self.admin_id,
            original_filename=f"ebit-{year}.xlsx",
            stored_path="/must/not/be/read.xlsx",
            file_hash=f"ebit-{year}",
            status="active",
            created_at=datetime(year, 1, 2, tzinfo=timezone.utc),
        )
        version = HistoricalYearVersion(
            historical_import=historical_import,
            company_id=self.company_id,
            year=year,
            version_no=1,
            is_active=True,
            activated_at=datetime(year, 2, 3, tzinfo=timezone.utc),
            activated_by=self.admin_id,
        )
        db.session.add(version)
        db.session.flush()
        return version

    @staticmethod
    def _metric(version, label, value, unit, raw_text=None, period=None):
        metric = HistoricalMetricValue(
            year_version=version,
            section_key="financial_results",
            section_label="financial_results",
            metric_key=canonical_metric_key(label),
            metric_label=label,
            series_key="value",
            period_label=period or str(version.year),
            value_numeric=value,
            unit=unit,
        )
        metric.raw_value = HistoricalRawValue(
            raw_text=str(value) if raw_text is None else raw_text,
            sheet_name="financial_results",
            cell_ref="B2",
        )
        db.session.add(metric)
        return metric

    @staticmethod
    def _sample_row(parsed, label):
        section = next(
            section for section in parsed["sections"]
            if section["key"] == "financial_results")
        return next(
            row for row in section["rows"]
            if normalize_name(row["label"]) == normalize_name(label))

    def test_real_sample_parser_to_annual_api_uses_money_ebit(self):
        parsed = parse_historical_xlsx(_SAMPLE)
        ebit = self._sample_row(parsed, _EBIT)
        interest = self._sample_row(parsed, _INTEREST)
        self.assertEqual(ebit["unit"], "thousand_rubles")
        self.assertTrue(all(
            value["unit"] == "thousand_rubles"
            for value in ebit["values"] if not value["is_missing"]))

        ebit_value = next(value for value in ebit["values"] if value["year"] == 2025)
        interest_value = next(
            value for value in interest["values"] if value["year"] == 2025)
        version = self._version(2025)
        _persist_version_values(version, parsed, 2025)
        db.session.commit()

        payload = build_annual_key_indicators(self.company, requested_year=2025)
        card = next(
            card for card in payload["cards"] if card["id"] == "interest_coverage")
        self.assertAlmostEqual(
            card["value"], ebit_value["value"] / interest_value["value"])
        self.assertNotIn("correction_note", card)
        persisted = HistoricalMetricValue.query.filter_by(
            year_version_id=version.id,
            section_key="financial_results",
            metric_key=canonical_metric_key(_EBIT),
        ).one()
        self.assertEqual(persisted.unit, "thousand_rubles")

    def test_legacy_persisted_percent_ebit_is_read_only_corrected(self):
        version = self._version(2025)
        legacy = self._metric(
            version, _EBIT, 120, "percent", raw_text="120", period="2025 г.")
        self._metric(version, _INTEREST, 30, "thousand_rubles")
        db.session.commit()

        payload = build_annual_key_indicators(self.company, requested_year=2025)
        card = next(
            card for card in payload["cards"] if card["id"] == "interest_coverage")
        self.assertEqual(card["value"], 4.0)
        self.assertTrue(card["correction_note"])
        self.assertEqual(legacy.unit, "percent")
        self.assertEqual(legacy.value_numeric, 120.0)

    def test_explicit_percent_evidence_still_wins_for_ebit_label(self):
        unit, _factor = _unit(_EBIT, "2025 г., % к выручке", "financial_results", 12)
        self.assertEqual(unit, "percent")
        unit, _factor = _unit(_EBIT, "2025 г.", "financial_results", "12%")
        self.assertEqual(unit, "percent")


if __name__ == "__main__":
    unittest.main()