# -*- coding: utf-8 -*-
"""Focused regressions for the annual financial-stability cards."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


_TMP = tempfile.TemporaryDirectory(prefix="annual-stability-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    _TMP.name, "annual-stability.db")

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
from historical_parser import canonical_metric_key


STABILITY_IDS = {
    "autonomy",
    "net_working_capital",
    "quick_liquidity",
    "absolute_liquidity",
    "short_term_liabilities",
    "long_term_liabilities",
    "short_term_share",
    "long_term_share",
    "interest_coverage",
}
DIRECT_STABILITY_IDS = set()
DERIVED_STABILITY_IDS = STABILITY_IDS - DIRECT_STABILITY_IDS

COMPONENTS = (
    ("equity", "liabilities", "Собственный капитал"),
    ("assets", "assets", "ВАЛЮТА БАЛАНСА"),
    ("current_assets", "assets", "Оборотные активы, всего"),
    (
        "short_term_liabilities",
        "liabilities",
        "Краткосрочные обязательства, всего",
    ),
    (
        "long_term_liabilities",
        "liabilities",
        "Долгосрочные обязательства, всего",
    ),
    (
        "cash",
        "assets",
        "денежные средства и краткосрочные фин. вложения",
    ),
    ("accounts_receivable", "assets", "дебиторская задолженность"),
    ("ebit", "financial_results", "EBIT (прибыль до % и налогов)"),
    ("interest_expense", "financial_results", "Проценты к уплате"),
)

DEFAULT_VALUES = {
    "equity": 300,
    "assets": 1000,
    "current_assets": 500,
    "short_term_liabilities": 200,
    "long_term_liabilities": 300,
    "cash": 50,
    "accounts_receivable": 100,
    "ebit": 120,
    "interest_expense": 30,
}


class AnnualStabilityTests(unittest.TestCase):
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
                username="annual_stability_admin",
                password_hash="x",
                role="admin",
            )
            db.session.add(cls.admin)
            db.session.flush()
            cls.company = Company(
                name="Годовая устойчивость",
                created_by=cls.admin.id,
            )
            db.session.add(cls.company)
            db.session.commit()
            # Keep scalar identity attributes available after leaving context.
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
        if self.ctx is not None:
            db.session.remove()
            self.ctx.pop()

    def _version(self, year, version_no=1):
        historical_import = HistoricalImport(
            company_id=self.company_id,
            uploaded_by=self.admin_id,
            original_filename=f"stability-{year}.xlsx",
            stored_path="/must/not/be/read.xlsx",
            file_hash=f"stability-{year}-{version_no}",
            status="active",
            created_at=datetime(year, 1, 2, tzinfo=timezone.utc),
        )
        version = HistoricalYearVersion(
            historical_import=historical_import,
            company_id=self.company_id,
            year=year,
            version_no=version_no,
            is_active=True,
            activated_at=datetime(year, 2, 3, tzinfo=timezone.utc),
            activated_by=self.admin_id,
        )
        db.session.add(version)
        db.session.flush()
        return version

    def _metric(
        self,
        version,
        section,
        label,
        value,
        unit="thousand_rubles",
        missing=False,
        upper=False,
        cell=None,
        series_key="value",
    ):
        metric = HistoricalMetricValue(
            year_version=version,
            section_key=section,
            section_label=section,
            metric_key=canonical_metric_key(label),
            metric_label=label,
            series_key=series_key,
            period_label=str(version.year),
            value_numeric=value,
            unit=unit,
            is_missing=missing,
            is_upper_bound=upper,
        )
        metric.raw_value = HistoricalRawValue(
            raw_text="" if missing else str(value),
            sheet_name=section,
            cell_ref=cell or f"B{version.year}",
        )
        db.session.add(metric)
        return metric

    def _add_components(self, version, values=None, upper=()):
        values = {**DEFAULT_VALUES, **(values or {})}
        upper = set(upper)
        for key, section, label in COMPONENTS:
            self._metric(
                version,
                section,
                label,
                values[key],
                upper=key in upper,
                cell=f"{key[:2]}{version.year}",
            )

    @staticmethod
    def _cards(payload):
        return {card["id"]: card for card in payload["cards"]}

    def test_all_nine_stability_cards_are_available_for_one_year(self):
        version = self._version(2024)
        self._add_components(version)
        db.session.commit()

        payload = build_annual_key_indicators(self.company, requested_year=2024)
        cards = self._cards(payload)
        self.assertEqual(STABILITY_IDS, set(cards) & STABILITY_IDS)
        self.assertEqual(len(payload["cards"]), 19)
        expected = {
            "autonomy": (0.3, "ratio"),
            "net_working_capital": (300000.0, "RUB"),
            "quick_liquidity": (0.75, "ratio"),
            "absolute_liquidity": (0.25, "ratio"),
            "short_term_liabilities": (200000.0, "RUB"),
            "long_term_liabilities": (300000.0, "RUB"),
            "short_term_share": (40.0, "percent"),
            "long_term_share": (60.0, "percent"),
            "interest_coverage": (4.0, "ratio"),
        }
        for card_id, (value, unit) in expected.items():
            with self.subTest(card=card_id):
                self.assertAlmostEqual(cards[card_id]["value"], value)
                self.assertEqual(cards[card_id]["unit"], unit)
                self.assertIsNone(cards[card_id]["missing_reason"])
                self.assertFalse(cards[card_id]["upper_bound"])
                self.assertEqual(cards[card_id]["series"][0]["year"], 2024)
                self.assertAlmostEqual(cards[card_id]["series"][0]["value"], value)
                self.assertTrue(cards[card_id]["formula"])
                self.assertTrue(cards[card_id]["calculation_note"])

    def test_range_uses_final_balance_boundary_and_sums_icr_flows(self):
        first = self._version(2023)
        last = self._version(2024)
        self._add_components(
            first,
            {
                "equity": 100,
                "assets": 500,
                "current_assets": 200,
                "short_term_liabilities": 100,
                "long_term_liabilities": 100,
                "cash": 20,
                "accounts_receivable": 30,
                "ebit": 100,
                "interest_expense": 10,
            },
        )
        self._add_components(
            last,
            {
                "equity": 200,
                "assets": 1000,
                "current_assets": 400,
                "short_term_liabilities": 200,
                "long_term_liabilities": 300,
                "cash": 50,
                "accounts_receivable": 100,
                "ebit": 20,
                "interest_expense": 5,
            },
        )
        db.session.commit()

        payload = build_annual_key_indicators(
            self.company, year_from=2023, year_to=2024)
        cards = self._cards(payload)
        self.assertEqual(payload["timeline_years"], [2023, 2024])
        expected = {
            "autonomy": 0.2,
            "net_working_capital": 200000.0,
            "quick_liquidity": 0.75,
            "absolute_liquidity": 0.25,
            "short_term_liabilities": 200000.0,
            "long_term_liabilities": 300000.0,
            "short_term_share": 40.0,
            "long_term_share": 60.0,
        }
        for card_id, value in expected.items():
            with self.subTest(card=card_id):
                self.assertAlmostEqual(cards[card_id]["value"], value)
                self.assertEqual(cards[card_id]["period_label"], "на 31.12.2024")
                if card_id in DERIVED_STABILITY_IDS:
                    self.assertEqual(
                        {item["year"]
                         for item in cards[card_id]["provenance_components"]},
                        {2024},
                    )
                else:
                    self.assertEqual(
                        set(cards[card_id]["provenance_by_year"]),
                        {2024},
                    )

        # (100 + 20) / (10 + 5) = 8; averaging annual ICRs would give 7.
        self.assertAlmostEqual(cards["interest_coverage"]["value"], 8.0)
        self.assertEqual(
            [item["value"] for item in cards["interest_coverage"]["series"]],
            [10.0, 4.0],
        )
        self.assertEqual(
            [(item["year"], item["component"])
             for item in cards["interest_coverage"]["provenance_components"]],
            [
                (2023, "ebit"),
                (2023, "interest_expense"),
                (2024, "ebit"),
                (2024, "interest_expense"),
            ],
        )

    def test_missing_year_blocks_icr_but_keeps_last_balance_boundary(self):
        first = self._version(2022)
        last = self._version(2024)
        self._add_components(first)
        self._add_components(last)
        db.session.commit()

        payload = build_annual_key_indicators(
            self.company, year_from=2022, year_to=2024)
        cards = self._cards(payload)
        self.assertEqual(payload["timeline_years"], [2022, 2023, 2024])
        self.assertTrue(any("пропуск" in warning for warning in payload["warnings"]))
        self.assertTrue(any("неполный" in warning for warning in payload["warnings"]))

        balance_ids = STABILITY_IDS - {"interest_coverage"}
        for card_id in balance_ids:
            with self.subTest(card=card_id):
                self.assertIsNotNone(cards[card_id]["value"])
                self.assertEqual(cards[card_id]["series"][1]["year"], 2023)
                self.assertIsNone(cards[card_id]["series"][1]["value"])
                self.assertEqual(cards[card_id]["series"][2]["year"], 2024)
        self.assertIsNone(cards["interest_coverage"]["value"])
        self.assertIn("2023", cards["interest_coverage"]["missing_reason"])

    def test_zero_numerators_are_valid_when_denominators_are_positive(self):
        version = self._version(2024)
        self._add_components(
            version,
            {
                "equity": 0,
                "current_assets": 0,
                "cash": 0,
                "accounts_receivable": 0,
                "ebit": 0,
            },
        )
        db.session.commit()

        cards = self._cards(build_annual_key_indicators(self.company, 2024))
        expected_zeroes = {
            "autonomy": 0.0,
            "quick_liquidity": 0.0,
            "absolute_liquidity": 0.0,
            "interest_coverage": 0.0,
        }
        for card_id, value in expected_zeroes.items():
            with self.subTest(card=card_id):
                self.assertEqual(cards[card_id]["value"], value)
                self.assertIsNone(cards[card_id]["missing_reason"])
        self.assertEqual(cards["net_working_capital"]["value"], -200000.0)
        self.assertEqual(cards["short_term_share"]["value"], 40.0)
        self.assertEqual(cards["long_term_share"]["value"], 60.0)

    def _assert_nonpositive_denominators(self, values):
        version = self._version(2024)
        self._add_components(version, values)
        db.session.commit()
        cards = self._cards(build_annual_key_indicators(self.company, 2024))
        ratio_ids = {
            "autonomy",
            "quick_liquidity",
            "absolute_liquidity",
            "short_term_share",
            "long_term_share",
            "interest_coverage",
        }
        for card_id in ratio_ids:
            with self.subTest(card=card_id):
                self.assertIsNone(cards[card_id]["value"])
                self.assertIn("должен быть положительным",
                              cards[card_id]["missing_reason"])

    def test_zero_denominators_are_rejected(self):
        self._assert_nonpositive_denominators(
            {
                "assets": 0,
                "short_term_liabilities": 0,
                "long_term_liabilities": 0,
                "interest_expense": 0,
            })

    def test_negative_denominators_are_rejected(self):
        self._assert_nonpositive_denominators(
            {
                "assets": -100,
                "short_term_liabilities": -200,
                "long_term_liabilities": 100,
                "interest_expense": -30,
            })

    def test_wrong_units_and_sections_never_substitute_stability_components(self):
        version = self._version(2024)
        wrong_rows = (
            ("liabilities", "Собственный капитал", 300, "ratio"),
            ("liabilities", "ВАЛЮТА БАЛАНСА", 1000, "thousand_rubles"),
            ("liabilities", "Оборотные активы, всего", 500, "thousand_rubles"),
            (
                "liabilities",
                "Краткосрочные обязательства, всего",
                200,
                "ratio",
            ),
            (
                "assets",
                "Долгосрочные обязательства, всего",
                300,
                "thousand_rubles",
            ),
            (
                "liabilities",
                "денежные средства и краткосрочные фин. вложения",
                50,
                "thousand_rubles",
            ),
            ("assets", "дебиторская задолженность", 100, "ratio"),
            ("assets", "EBIT (прибыль до % и налогов)", 120, "thousand_rubles"),
            ("financial_results", "Проценты к уплате", 30, "percent"),
        )
        for index, (section, label, value, unit) in enumerate(wrong_rows):
            self._metric(
                version,
                section,
                label,
                value,
                unit=unit,
                cell=f"wrong{index}",
            )
        db.session.commit()

        cards = self._cards(build_annual_key_indicators(self.company, 2024))
        for card_id in STABILITY_IDS:
            with self.subTest(card=card_id):
                self.assertIsNone(cards[card_id]["value"])
                self.assertFalse(cards[card_id]["upper_bound"])

    def test_upper_bound_components_do_not_produce_derived_values(self):
        version = self._version(2024)
        self._add_components(version, upper=DEFAULT_VALUES)
        db.session.commit()

        cards = self._cards(build_annual_key_indicators(self.company, 2024))
        derived_ids = DERIVED_STABILITY_IDS
        for card_id in derived_ids:
            with self.subTest(card=card_id):
                self.assertIsNone(cards[card_id]["value"])
                self.assertTrue(cards[card_id]["upper_bound"])
                self.assertIn("верхнюю границу",
                              cards[card_id]["missing_reason"])
        self.assertIsNone(cards["short_term_liabilities"]["value"])
        self.assertTrue(cards["short_term_liabilities"]["upper_bound"])
        self.assertIsNone(cards["long_term_liabilities"]["value"])
        self.assertTrue(cards["long_term_liabilities"]["upper_bound"])

    def test_every_stability_card_exposes_source_provenance_and_timeline(self):
        first = self._version(2023)
        last = self._version(2024)
        self._add_components(first)
        self._add_components(last)
        db.session.commit()

        payload = build_annual_key_indicators(
            self.company, year_from=2023, year_to=2024)
        cards = self._cards(payload)
        self.assertEqual(payload["timeline_years"], [2023, 2024])
        for card_id in STABILITY_IDS:
            with self.subTest(card=card_id):
                card = cards[card_id]
                self.assertEqual(
                    [item["year"] for item in card["series"]],
                    [2023, 2024],
                )
                self.assertTrue(card["provenance"])
                self.assertTrue(card["provenance_by_year"])
                if card_id in DERIVED_STABILITY_IDS:
                    self.assertTrue(card["provenance_components"])
                    for record in card["provenance_components"]:
                        self.assertIn("component", record)
                        self.assertIn("source_label", record)
                        self.assertIn("provenance", record)

        # Point-in-time cards cite only their selected 31 December boundary.
        for card_id in DERIVED_STABILITY_IDS - {"interest_coverage"}:
            self.assertEqual(
                {item["year"] for item in cards[card_id]["provenance_components"]},
                {2024},
            )
        for card_id in DIRECT_STABILITY_IDS:
            self.assertEqual(set(cards[card_id]["provenance_by_year"]), {2024})
        self.assertEqual(
            {item["year"] for item in cards["interest_coverage"]["provenance_components"]},
            {2023, 2024},
        )


if __name__ == "__main__":
    unittest.main()