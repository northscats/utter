# -*- coding: utf-8 -*-
"""Regressions for exact daily cash balances derived from dated turnovers."""
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix='cash_daily_derivation_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')

from app import _derive_cash_daily_rows, _upsert_daily_rows, app
from database import DailyRow, db


class CashDailyDerivationTests(unittest.TestCase):
    def test_preserves_movements_and_carries_verified_balance(self):
        derived = _derive_cash_daily_rows({
            'acc': '51',
            'period': 'ОСВ по счету 51 за 01.07.2026 - 03.07.2026',
            'top': {'snd': 100, 'snk': 0, 'obd': 25, 'obk': 10,
                    'skd': 115, 'skk': 0},
            'daily': [
                {'date': '2026-07-01', 'obd': 25, 'obk': 0},
                {'date': '2026-07-03', 'obd': 0, 'obk': 10},
            ],
        })

        self.assertEqual([row['skd'] for row in derived], [125, 125, 115])
        self.assertEqual([row['obd'] for row in derived], [25, 0, 0])

    def test_rejects_report_when_dated_turnovers_do_not_reconcile(self):
        self.assertEqual(_derive_cash_daily_rows({
            'acc': '51',
            'period': 'за 01.07.2026 - 01.07.2026',
            'top': {'snd': 100, 'snk': 0, 'obd': 10, 'obk': 0,
                    'skd': 110, 'skk': 0},
            'daily': [],
        }), [])

    def test_does_not_derive_non_cash_account(self):
        self.assertEqual(_derive_cash_daily_rows({
            'acc': '62',
            'period': 'за 01.07.2026 - 01.07.2026',
            'top': {'snd': 100, 'snk': 0, 'obd': 0, 'obk': 0,
                    'skd': 100, 'skk': 0},
            'daily': [],
        }), [])

    def test_rejects_unknown_totals_and_out_of_period_movements(self):
        base = {
            'acc': '51',
            'period': 'за 01.07.2026 - 01.07.2026',
            'top': {'snd': 100, 'snk': 0, 'obd': 0, 'obk': 0,
                    'skd': 100, 'skk': 0},
            'daily': [],
        }
        unknown = {**base, 'top': {**base['top'], 'skd': None}}
        outside = {
            **base,
            'top': {**base['top'], 'obd': 10, 'obk': 10},
            'daily': [{'date': '2026-06-30', 'obd': 10, 'obk': 10}],
        }

        self.assertEqual(_derive_cash_daily_rows(unknown), [])
        self.assertEqual(_derive_cash_daily_rows(outside), [])

    def test_upsert_replaces_existing_observation_without_duplicate(self):
        observed = date(2026, 7, 1)
        parsed = {
            'acc': '51',
            'top': {'snd': 10, 'snk': 1, 'obd': 2, 'obk': 3,
                    'skd': 8, 'skk': 0},
        }
        with app.app_context():
            db.drop_all()
            db.create_all()
            original_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            db.session.add(DailyRow(
                company_id=1, date=observed, acc='51',
                snd=1, snk=0, obd=0, obk=0, skd=1, skk=0,
                uploaded_at=original_time))
            db.session.commit()
            with patch('osvparser.extract_daily_date', return_value=observed), \
                    patch('osvparser.parse_osv', return_value=parsed):
                self.assertEqual(_upsert_daily_rows(1, ['report.xls']), 1)
            db.session.commit()
            rows = DailyRow.query.filter_by(
                company_id=1, date=observed, acc='51').all()

            self.assertEqual(len(rows), 1)
            self.assertEqual(
                (rows[0].snd, rows[0].snk, rows[0].obd, rows[0].obk,
                 rows[0].skd, rows[0].skk),
                (10, 1, 2, 3, 8, 0))
            stored_time = rows[0].uploaded_at
            if stored_time.tzinfo is None:
                stored_time = stored_time.replace(tzinfo=timezone.utc)
            self.assertGreater(stored_time, original_time)


if __name__ == '__main__':
    unittest.main()