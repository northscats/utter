# -*- coding: utf-8 -*-
"""Persist financial reconciliation states without messaging side effects."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from database import Company, DailyReconciliationAlert, db, utcnow


def _cents(value):
    try:
        return int((Decimal(str(value)) * 100).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _identity(item):
    try:
        return (
            str(item.get('account') or ''),
            str(item.get('side') or ''),
            date.fromisoformat(str(item.get('start'))),
            date.fromisoformat(str(item.get('end'))),
        )
    except (TypeError, ValueError):
        return None


def sync_daily_reconciliation_state(company_id, reconciliations):
    """Persist open/resolved discrepancies and return their latest state."""
    company = db.session.get(Company, int(company_id))
    if company is None:
        raise ValueError('Предприятие для сверки не найдено.')
    current = {}
    for item in reconciliations or []:
        identity = _identity(item)
        if identity is not None:
            current[identity] = (item, _cents(item.get('difference')))

    alerts = DailyReconciliationAlert.query.filter_by(
        company_id=company.id).all()
    by_identity = {
        (row.account, row.side, row.period_start, row.period_end): row
        for row in alerts
    }
    now = utcnow()
    for identity, (item, difference_cents) in current.items():
        alert = by_identity.get(identity)
        if difference_cents:
            if alert is None:
                alert = DailyReconciliationAlert(
                    company_id=company.id,
                    account=identity[0],
                    side=identity[1],
                    period_start=identity[2],
                    period_end=identity[3],
                    difference_cents=difference_cents,
                    source_file=str(item.get('source_file') or ''),
                )
                db.session.add(alert)
                db.session.flush()
                by_identity[identity] = alert
            elif alert.status == 'resolved':
                alert.status = 'open'
                alert.generation += 1
                alert.opened_at = now
                alert.resolved_at = None
            alert.difference_cents = difference_cents
            alert.source_file = str(item.get('source_file') or '')
        elif alert is not None and alert.status == 'open':
            alert.status = 'resolved'
            alert.resolved_at = now
            alert.difference_cents = 0

    # A removed reconciliation is also resolved: its discrepancy is gone.
    for identity, alert in by_identity.items():
        if identity not in current and alert.status == 'open':
            alert.status = 'resolved'
            alert.resolved_at = now
            alert.difference_cents = 0
    db.session.commit()
    return by_identity