# -*- coding: utf-8 -*-
"""Deployment regression for the bundled МУП КГХ account-55 source."""
import os
import tempfile
import unittest
from datetime import date, timedelta

_TMP = tempfile.mkdtemp(prefix='bundled_mup_kgh_55_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')

from app import (UPLOADS, _BUNDLED_MUP_KGH_ACCOUNT55,
                 _import_bundled_mup_kgh_account55, app)
from database import Company, DailyRow, Upload, User, db
from key_indicators import build_key_indicators
from key_indicators_service import load_company_source
from osvparser import parse_osv


class BundledMupKghAccount55Tests(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        with app.app_context():
            db.drop_all()
            db.create_all()
            admin = User(
                username='admin', password_hash='unused', role='admin')
            db.session.add(admin)
            db.session.flush()
            company = Company(name='МУП  КГХ', created_by=admin.id)
            db.session.add(company)
            db.session.flush()
            day = date(2026, 7, 1)
            while day <= date(2026, 9, 15):
                for account in ('50', '51', '52'):
                    db.session.add(DailyRow(
                        company_id=company.id, date=day, acc=account,
                        snd=0, snk=0, obd=0, obk=0, skd=0, skk=0))
                day += timedelta(days=1)
            db.session.commit()

    def test_clean_database_import_is_idempotent_and_closes_cash_coverage(self):
        parsed = parse_osv(_BUNDLED_MUP_KGH_ACCOUNT55)
        self.assertEqual(parsed['acc'], '55')
        self.assertEqual(
            parsed['period'],
            'Оборотно-сальдовая ведомость по счету 55 '
            'за 01.07.2026 - 15.09.2026')
        self.assertEqual(
            [parsed['top'][key]
             for key in ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')],
            [0, 0, 0, 0, 0, 0])

        with app.app_context():
            self.assertTrue(_import_bundled_mup_kgh_account55())
            company = Company.query.filter(Company.name.ilike('%КГХ%')).one()
            first_uploaded_at = DailyRow.query.filter_by(
                company_id=company.id, acc='55').first().uploaded_at
            self.assertTrue(_import_bundled_mup_kgh_account55())
            rows = DailyRow.query.filter_by(
                company_id=company.id, acc='55').order_by(DailyRow.date).all()
            self.assertEqual(len(rows), 77)
            self.assertEqual((rows[0].date, rows[-1].date),
                             (date(2026, 7, 1), date(2026, 9, 15)))
            self.assertEqual(
                Upload.query.filter_by(company_id=company.id).count(), 1)
            self.assertEqual(rows[0].uploaded_at, first_uploaded_at)

            source, _ = load_company_source(company.id, UPLOADS)
            day = date(2026, 7, 1)
            while day <= date(2026, 9, 15):
                payload = build_key_indicators(
                    source, period='day', date=day.isoformat())
                cash = next(
                    card for card in payload['cards'] if card['id'] == 'cash')
                self.assertEqual(cash['coverage'], 'complete')
                self.assertEqual(
                    set(cash['accounts']), {'50', '51', '52', '55'})
                day += timedelta(days=1)


if __name__ == '__main__':
    unittest.main()