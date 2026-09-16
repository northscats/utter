# -*- coding: utf-8 -*-
import hashlib
import os
import tempfile
import unittest
from datetime import date

_TMP = tempfile.TemporaryDirectory(prefix='collection-source-test-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP.name, 'test.db')

from app import app
from database import (Company, ConsumerCollectionNorm,
                      ConsumerCollectionAccrual, ConsumerPaymentAllocation,
                      User, db)


class CollectionSourceRouteTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.admin = User(
            username='collection-admin', password_hash='not-a-password',
            role='admin')
        db.session.add(self.admin)
        db.session.flush()
        self.company = Company(
            name='Тестовое предприятие', created_by=self.admin.id)
        db.session.add(self.company)
        db.session.commit()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin.id)
            session['_fresh'] = True
            session['password_version'] = 1

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_admin_can_replace_durable_linked_source(self):
        analytic_key = hashlib.sha256(b'buyer').hexdigest()
        response = self.client.post(
            f'/company/{self.company.id}/key-indicators/collection-source',
            json={
                'accruals': [{
                    'date': '2026-01-15', 'account': '62.01',
                    'analytic_key': analytic_key, 'accrued': 100,
                    'type': 'accrual',
                    'accrual_document': 'ACC-1',
                    'source': '1С', 'version': '2026.01',
                }],
                'allocations': [{
                    'date': '2026-02-10', 'payment_document': 'PAY-1',
                    'accrual_document': 'ACC-1', 'allocated_amount': 85,
                    'type': 'payment',
                    'source': '1С', 'version': '2026.02',
                }],
                'norms': [{
                    'category': 'population', 'plan_percent': 80,
                    'approved': True, 'approved_at': '2025-12-20T10:00:00+00:00',
                    'valid_from': '2026-01-01', 'valid_to': '2026-12-31',
                    'source': 'Бюджет', 'version': '1',
                }],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConsumerCollectionAccrual.query.count(), 1)
        self.assertEqual(ConsumerPaymentAllocation.query.count(), 1)
        self.assertEqual(ConsumerCollectionNorm.query.count(), 1)
        accrual = ConsumerCollectionAccrual.query.one()
        allocation = ConsumerPaymentAllocation.query.one()
        self.assertEqual(accrual.accrual_date, date(2026, 1, 15))
        self.assertEqual(allocation.payment_date, date(2026, 2, 10))
        self.assertEqual(allocation.allocated_amount, 85)

    def test_invalid_source_does_not_replace_existing_rows(self):
        existing = ConsumerCollectionAccrual(
            company_id=self.company.id, accrual_date=date(2026, 1, 1),
            account='62.01', analytic_key='a' * 64, accrued=10,
            entry_type='accrual',
            accrual_document='OLD',
            source_name='1С', source_version='old')
        db.session.add(existing)
        db.session.commit()
        response = self.client.post(
            f'/company/{self.company.id}/key-indicators/collection-source',
            json={'accruals': [{'date': 'bad'}],
                  'allocations': [], 'norms': []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ConsumerCollectionAccrual.query.count(), 1)
        self.assertEqual(
            ConsumerCollectionAccrual.query.one().accrual_document, 'OLD')


if __name__ == '__main__':
    unittest.main()