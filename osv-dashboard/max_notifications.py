# -*- coding: utf-8 -*-
"""Durable MAX notifications for daily/control ОСВ reconciliation."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from database import (
    Company, DailyReconciliationAlert, DailyReconciliationAlertDelivery,
    LimitBotOutbound, LimitRecipient, db, utcnow,
)


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


def _recipients(company):
    inn = str(company.inn or '').strip()
    if not inn:
        return []
    return LimitRecipient.query.filter_by(
        organization_inn=inn, access_status='approved'
    ).filter(
        LimitRecipient.stopped_at.is_(None),
        LimitRecipient.consent_granted_at.isnot(None),
    ).order_by(LimitRecipient.id).all()


def _content(company, alert, transition):
    side = 'дебет' if alert.side == 'debit' else 'кредит'
    period = (
        f'{alert.period_start.strftime("%d.%m.%Y")}–'
        f'{alert.period_end.strftime("%d.%m.%Y")}')
    if transition == 'resolved':
        return (
            f'Расхождение закрыто: {company.name}\n'
            f'Счёт {alert.account}, {side}, период {period}.\n'
            'Дневные обороты теперь совпадают с контрольной ОСВ.')
    amount = Decimal(alert.difference_cents) / 100
    return (
        f'Новое расхождение дневных оборотов: {company.name}\n'
        f'Счёт {alert.account}, {side}, период {period}.\n'
        f'Контрольная ОСВ минус дневные данные: {amount:,.2f} руб.\n'
        'Не используйте период как подтверждённый до устранения расхождения.'
    ).replace(',', ' ')


def _queue_transition(company, alert, recipients, transition):
    for recipient in recipients:
        existing = DailyReconciliationAlertDelivery.query.filter_by(
            alert_id=alert.id,
            recipient_id=recipient.id,
            generation=alert.generation,
            transition=transition,
        ).first()
        if existing is not None:
            continue
        outbound = LimitBotOutbound(
            target_id=recipient.max_user_id,
            content=_content(company, alert, transition),
        )
        db.session.add(outbound)
        db.session.flush()
        db.session.add(DailyReconciliationAlertDelivery(
            alert_id=alert.id,
            recipient_id=recipient.id,
            generation=alert.generation,
            transition=transition,
            outbound_id=outbound.id,
        ))


def sync_daily_reconciliation_notifications(company_id, reconciliations):
    """Persist open/resolved states and atomically enqueue each transition once."""
    company = db.session.get(Company, int(company_id))
    if company is None:
        raise ValueError('Предприятие для уведомления не найдено.')
    recipients = _recipients(company)
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
            _queue_transition(company, alert, recipients, 'opened')
        elif alert is not None and alert.status == 'open':
            alert.status = 'resolved'
            alert.resolved_at = now
            alert.difference_cents = 0
            _queue_transition(company, alert, recipients, 'resolved')

    # A removed reconciliation is also resolved: the accepted source no longer
    # contains the discrepancy identity that originally opened the alert.
    for identity, alert in by_identity.items():
        if identity not in current and alert.status == 'open':
            alert.status = 'resolved'
            alert.resolved_at = now
            alert.difference_cents = 0
            _queue_transition(company, alert, recipients, 'resolved')
    db.session.commit()
    return by_identity
