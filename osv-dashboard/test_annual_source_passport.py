# -*- coding: utf-8 -*-
"""Isolated read-only regressions for the annual source passport."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


_TMP = tempfile.TemporaryDirectory(prefix="annual-source-passport-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    _TMP.name, "passport.db")

import app as app_module
from annual_source_passport import build_annual_source_passport
from database import (
    Company,
    HistoricalImport,
    HistoricalYearVersion,
    User,
    db,
)


class AnnualSourcePassportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.imap_patch = patch.object(
            app_module, "_ensure_imap_thread")
        cls.imap_patch.start()
        with cls.app.app_context():
            db.create_all()
            cls.user = User(
                username="passport_admin",
                password_hash="x",
                role="admin",
            )
            db.session.add(cls.user)
            db.session.flush()
            cls.company = Company(
                name="Паспорт предприятия",
                created_by=cls.user.id,
            )
            cls.other = Company(
                name="Другое предприятие",
                created_by=cls.user.id,
            )
            db.session.add_all([cls.company, cls.other])
            db.session.commit()
            cls.user_id = cls.user.id
            cls.company_id = cls.company.id
            cls.other_id = cls.other.id

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
        HistoricalYearVersion.query.delete()
        HistoricalImport.query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _upload(
        self,
        company_id,
        year,
        *,
        version_no=1,
        active=True,
        status="active",
        file_hash=None,
        created_at=None,
        confirmed_at=None,
        activated_at=None,
    ):
        created_at = created_at or datetime(
            year, 1, 2, tzinfo=timezone.utc)
        historical_import = HistoricalImport(
            company_id=company_id,
            uploaded_by=self.user_id,
            original_filename=f"{company_id}-{year}-{version_no}.xlsx",
            stored_path="/never/read.xlsx",
            file_hash=file_hash or f"{company_id}-{year}-{version_no}",
            status=status,
            created_at=created_at,
            confirmed_at=confirmed_at,
        )
        version = HistoricalYearVersion(
            historical_import=historical_import,
            company_id=company_id,
            year=year,
            version_no=version_no,
            is_active=active,
            activated_at=activated_at or datetime(
                year, 2, 3, tzinfo=timezone.utc),
            activated_by=self.user_id,
        )
        db.session.add(version)
        db.session.flush()
        return historical_import, version

    def test_no_accepted_data_and_card_reason_summary_are_explicit(self):
        self._upload(
            self.company_id,
            2024,
            active=False,
            status="cancelled",
        )
        db.session.commit()
        before = (
            HistoricalImport.query.count(),
            HistoricalYearVersion.query.count(),
        )
        passport = build_annual_source_passport(
            self.company,
            year_from=2024,
            year_to=2024,
            cards=[{
                "id": "revenue",
                "title": "Выручка",
                "value": None,
                "missing_reason": "Нет принятой версии",
            }],
        )
        after = (
            HistoricalImport.query.count(),
            HistoricalYearVersion.query.count(),
        )
        self.assertEqual(before, after)
        self.assertEqual(passport["status"], "no_accepted_data")
        self.assertTrue(passport["no_accepted_data"])
        self.assertEqual(passport["reason"], "Нет принятых активных годовых данных")
        self.assertEqual(passport["selected_range"]["missing_years"], [2024])
        self.assertIsNone(passport["factual_as_of"])
        self.assertEqual(
            passport["card_reason_summary"],
            [{
                "id": "revenue",
                "title": "Выручка",
                "available": False,
                "missing_reason": "Нет принятой версии",
            }],
        )
        self.assertEqual(
            passport["card_missing_reasons"],
            {"revenue": "Нет принятой версии"},
        )

    def test_dates_range_duplicates_and_inactive_history_are_distinct(self):
        first_import, first = self._upload(
            self.company_id,
            2023,
            active=False,
            version_no=1,
            file_hash="same-content",
            created_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
            confirmed_at=datetime(2024, 1, 6, tzinfo=timezone.utc),
            activated_at=datetime(2024, 1, 7, tzinfo=timezone.utc),
        )
        second_import, second = self._upload(
            self.company_id,
            2024,
            version_no=1,
            file_hash="same-content",
            created_at=datetime(2025, 1, 5, tzinfo=timezone.utc),
            confirmed_at=datetime(2025, 1, 6, tzinfo=timezone.utc),
            activated_at=datetime(2025, 1, 7, tzinfo=timezone.utc),
        )
        db.session.commit()
        passport = build_annual_source_passport(
            self.company,
            versions=[second],
            year_from=2023,
            year_to=2025,
            cards=[],
        )
        self.assertEqual(passport["status"], "accepted")
        self.assertEqual(passport["accepted_years"], [2024])
        self.assertEqual(
            passport["selected_range"]["missing_years"], [2023, 2025])
        self.assertIsNone(passport["factual_as_of"])
        accepted = passport["accepted_versions"][0]
        self.assertEqual(accepted["uploaded_at"], "2025-01-05T00:00:00")
        self.assertEqual(accepted["confirmed_at"], "2025-01-06T00:00:00")
        self.assertEqual(accepted["activated_at"], "2025-01-07T00:00:00")
        self.assertEqual(accepted["factual_period_end"], "2024-12-31")
        self.assertEqual(len(passport["duplicate_uploads"]), 1)
        self.assertEqual(
            passport["duplicate_uploads"][0]["import_ids"],
            [first_import.id, second_import.id],
        )
        self.assertEqual(len(passport["inactive_version_history"]), 1)
        self.assertEqual(
            passport["inactive_version_history"][0]["history_role"],
            "inactive_version_history",
        )
        self.assertEqual(passport["active_conflicts"], [])

    def test_history_and_hash_groups_are_tenant_scoped(self):
        own_import, own = self._upload(
            self.company_id,
            2024,
            file_hash="tenant-local-hash",
        )
        self._upload(
            self.other_id,
            2025,
            file_hash="tenant-local-hash",
        )
        db.session.commit()
        passport = build_annual_source_passport(
            self.company,
            versions=[own],
            year_from=2024,
            year_to=2024,
        )
        self.assertEqual(
            [record["import_id"] for record in passport["accepted_versions"]],
            [own_import.id],
        )
        self.assertEqual(passport["duplicate_uploads"], [])
        self.assertEqual(
            {record["year"] for record in passport["inactive_version_history"]},
            set(),
        )


if __name__ == "__main__":
    unittest.main()