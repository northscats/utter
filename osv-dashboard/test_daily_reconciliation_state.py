# -*- coding: utf-8 -*-
"""Financial discrepancy lifecycle without a notification transport."""
import os
import tempfile
import unittest

from flask import Flask

from database import Company, DailyReconciliationAlert, User, db
from daily_reconciliation_state import sync_daily_reconciliation_state


class DailyReconciliationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(
                self.temp.name, 'test.db'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(username='admin', password_hash='x', role='admin')
        db.session.add(user)
        db.session.flush()
        company = Company(
            name='Тестовое предприятие', inn='1234567890',
            created_by=user.id)
        db.session.add(company)
        db.session.commit()
        self.company_id = company.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.temp.cleanup()

    @staticmethod
    def row(difference):
        return {
            'account': '51', 'side': 'debit',
            'start': '2026-09-01', 'end': '2026-09-18',
            'difference': difference, 'source_file': 'control.xlsx',
        }

    def test_new_difference_is_idempotent(self):
        sync_daily_reconciliation_state(
            self.company_id, [self.row(125.50)])
        sync_daily_reconciliation_state(
            self.company_id, [self.row(125.50)])
        alert = DailyReconciliationAlert.query.one()
        self.assertEqual(alert.status, 'open')
        self.assertEqual(alert.difference_cents, 12550)
        self.assertEqual(alert.generation, 1)

    def test_resolved_difference_closes_alert_and_reopening_increments_generation(self):
        sync_daily_reconciliation_state(self.company_id, [self.row(125.50)])
        sync_daily_reconciliation_state(self.company_id, [self.row(0)])
        sync_daily_reconciliation_state(self.company_id, [self.row(0)])
        alert = DailyReconciliationAlert.query.one()
        self.assertEqual(alert.status, 'resolved')
        self.assertIsNotNone(alert.resolved_at)
        sync_daily_reconciliation_state(self.company_id, [self.row(-50)])
        self.assertEqual(alert.status, 'open')
        self.assertEqual(alert.generation, 2)
        self.assertEqual(alert.difference_cents, -5000)

    def test_removed_reconciliation_closes_existing_alert(self):
        sync_daily_reconciliation_state(self.company_id, [self.row(-50)])
        sync_daily_reconciliation_state(self.company_id, [])
        self.assertEqual(DailyReconciliationAlert.query.one().status, 'resolved')


if __name__ == '__main__':
    unittest.main()