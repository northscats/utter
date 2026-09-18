# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import datetime, timezone

from flask import Flask

from database import (
    Company, DailyReconciliationAlert, DailyReconciliationAlertDelivery,
    LimitBotOutbound, LimitRecipient, User, db,
)
from max_notifications import sync_daily_reconciliation_notifications


class DailyReconciliationNotificationTests(unittest.TestCase):
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
        recipient = LimitRecipient(
            max_user_id='max-1',
            organization_inn='1234567890',
            access_status='approved',
            consent_granted_at=datetime.now(timezone.utc),
        )
        db.session.add_all([company, recipient])
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

    def test_new_difference_queues_one_notification_without_rebuild_duplicate(self):
        sync_daily_reconciliation_notifications(
            self.company_id, [self.row(125.50)])
        sync_daily_reconciliation_notifications(
            self.company_id, [self.row(125.50)])

        alert = DailyReconciliationAlert.query.one()
        self.assertEqual(alert.status, 'open')
        self.assertEqual(alert.difference_cents, 12550)
        self.assertEqual(LimitBotOutbound.query.count(), 1)
        self.assertEqual(DailyReconciliationAlertDelivery.query.count(), 1)
        self.assertIn('Новое расхождение', LimitBotOutbound.query.one().content)

    def test_resolved_difference_closes_alert_and_queues_clear_status(self):
        sync_daily_reconciliation_notifications(
            self.company_id, [self.row(125.50)])
        sync_daily_reconciliation_notifications(
            self.company_id, [self.row(0)])
        sync_daily_reconciliation_notifications(
            self.company_id, [self.row(0)])

        alert = DailyReconciliationAlert.query.one()
        self.assertEqual(alert.status, 'resolved')
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(LimitBotOutbound.query.count(), 2)
        self.assertIn(
            'Расхождение закрыто',
            LimitBotOutbound.query.order_by(LimitBotOutbound.id.desc()).first().content)

    def test_removed_reconciliation_closes_existing_alert(self):
        sync_daily_reconciliation_notifications(
            self.company_id, [self.row(-50)])
        sync_daily_reconciliation_notifications(self.company_id, [])

        self.assertEqual(
            DailyReconciliationAlert.query.one().status, 'resolved')
        self.assertEqual(LimitBotOutbound.query.count(), 2)


if __name__ == '__main__':
    unittest.main()