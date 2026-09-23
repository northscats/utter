"""Durable versioned metric-card snapshots.

This module stores card scalars, rather than the (potentially very large)
source or dashboard payload.  A fingerprint covers accepted Upload metadata,
the accepted source manifest, analytic mappings and collection settings.  It
does not cover files outside accepted manifests, nor future calculation-code
changes; callers should treat it as a source/settings revision guard, not as
a reproducible-build hash.
"""
import hashlib
import json
from datetime import date

from database import (
    ActiveMetricSnapshot, Company, CostAnalyticMapping, DebtorAnalyticMapping,
    MetricSnapshot, MetricSnapshotValue, RevenueAnalyticMapping, Upload, db,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), default=str)


def _row_dict(row, fields):
    return {field: getattr(row, field) for field in fields}


def _revision(company_id):
    """Return the revision fingerprint and its JSON-safe evidence."""
    uploads = Upload.query.filter_by(company_id=company_id).order_by(Upload.id).all()
    upload_data = [_row_dict(row, (
        'id', 'uploaded_at', 'period_label', 'accounts_loaded', 'files_count',
        'source', 'dashboard_file', 'metrics_json', 'source_manifest_json'))
        for row in uploads]
    try:
        from sqlalchemy import func
        from database import DailyRow
        daily_query = DailyRow.query.filter_by(company_id=company_id)
        daily_data = {
            'count': daily_query.count(),
            'max_id': daily_query.with_entities(func.max(DailyRow.id)).scalar(),
            'max_uploaded_at': daily_query.with_entities(
                func.max(DailyRow.uploaded_at)).scalar(),
        }
    except ImportError:
        daily_data = {}
    mappings = []
    for model in (RevenueAnalyticMapping, CostAnalyticMapping,
                  DebtorAnalyticMapping):
        for row in model.query.filter_by(company_id=company_id).order_by(model.id):
            mappings.append({
                column.name: getattr(row, column.name)
                for column in model.__table__.columns
                if column.name not in ('id', 'company_id', 'created_at', 'updated_at')
            })
    # Collection rows are settings that affect the collection cards.  Keep
    # this import local so installations without that optional subsystem can
    # still create ordinary snapshots.
    collection = []
    try:
        from database import (ConsumerCollectionAccrual,
                              ConsumerCollectionNorm,
                              ConsumerPaymentAllocation)
        for model in (ConsumerCollectionAccrual, ConsumerPaymentAllocation,
                      ConsumerCollectionNorm):
            for row in model.query.filter_by(company_id=company_id).order_by(model.id):
                collection.append({
                    column.name: getattr(row, column.name)
                    for column in model.__table__.columns
                    if column.name not in ('id', 'company_id', 'imported_at')
                })
    except ImportError:
        pass
    evidence = {'uploads': upload_data, 'daily_rows': daily_data,
                'mappings': mappings, 'collection_settings': collection,
                'methodology_version': METHODOLOGY_VERSION}
    return hashlib.sha256(_json(evidence).encode('utf-8')).hexdigest(), evidence


def _latest_date(source):
    dates = []
    for account in (source.get('accounts') or {}).values():
        for row in account.get('daily') or []:
            try:
                dates.append(date.fromisoformat(str(row.get('date'))))
            except (TypeError, ValueError):
                continue
    return max(dates) if dates else date.today()


METHODOLOGY_VERSION = 'metric-snapshots-v1'


def _periods(source, endpoint):
    """Use exactly the option dates offered by the existing calculation."""
    from key_indicators import _iso, _options
    dates = [value for value in (_iso(item) for item in source.get('dates', []))
             if value is not None]
    if not dates:
        dates = [endpoint]
    periods = []
    for granularity in ('month', 'quarter', 'year'):
        for option in _options(dates, granularity):
            periods.append((granularity, date.fromisoformat(option['value'])))
    return periods


def _calculation_inputs(company_id):
    """Use the exact route mapping/collection contract without editing app.py."""
    from app import _key_indicator_mappings
    mappings, costs, debtors = _key_indicator_mappings(company_id)
    from database import (ConsumerCollectionAccrual, ConsumerPaymentAllocation,
                          ConsumerCollectionNorm)
    accruals = [{
        'date': row.accrual_date.isoformat(), 'account': row.account,
        'analytic_key': row.analytic_key, 'type': row.entry_type,
        'accrued': row.accrued, 'accrual_document': row.accrual_document,
        'source': row.source_name, 'version': row.source_version,
    } for row in ConsumerCollectionAccrual.query.filter_by(company_id=company_id).all()]
    allocations = [{
        'date': row.payment_date.isoformat(), 'payment_document': row.payment_document,
        'accrual_document': row.accrual_document, 'type': row.entry_type,
        'allocated_amount': row.allocated_amount, 'source': row.source_name,
        'version': row.source_version,
    } for row in ConsumerPaymentAllocation.query.filter_by(company_id=company_id).all()]
    norms = [{
        'category': row.category, 'plan_percent': row.plan_percent,
        'approved': row.approved, 'approved_at': row.approved_at.isoformat(),
        'valid_from': row.valid_from.isoformat(), 'valid_to': row.valid_to.isoformat(),
        'source': row.source_name, 'version': row.source_version,
    } for row in ConsumerCollectionNorm.query.filter_by(company_id=company_id).all()]
    return mappings, costs, debtors, accruals, allocations, norms


def build_metric_snapshot(company_id, uploads_root, period='month',
                          endpoint_date=None, date_from=None):
    """Build and atomically publish a snapshot for ``company_id``.

    ``uploads_root`` is passed explicitly to make the source boundary clear
    and to keep this builder usable from jobs and isolated tests.  Any source
    or calculation error rolls back the candidate and leaves the old pointer.
    """
    from key_indicators import build_key_indicators
    from key_indicators_service import load_company_source

    company = db.session.get(Company, company_id)
    if company is None:
        raise ValueError('Unknown company')
    fingerprint, _ = _revision(company_id)
    source, _updated = load_company_source(company_id, uploads_root)
    endpoint = endpoint_date or _latest_date(source)
    if isinstance(endpoint, str):
        endpoint = date.fromisoformat(endpoint)

    revenue, costs, debtors, accruals, allocations, norms = \
        _calculation_inputs(company_id)
    # One publication contains every available standard period.  ``period``
    # remains an optional warming hint, never a partial active snapshot.
    periods = _periods(source, endpoint)
    calculated = []
    for granularity, option_end in periods:
        payload = build_key_indicators(
            source, period=granularity, date=option_end.isoformat(),
            revenue_mappings=revenue, cost_mappings=costs,
            debtor_mappings=debtors, collection_accruals=accruals,
            collection_allocations=allocations, collection_norms=norms)
        # The calculation owns the bounds (including partial periods and
        # future option semantics); never reconstruct them from calendar math.
        calculated.append((granularity, payload.get('period', {}).get('start',
                                                                        option_end),
                           payload.get('period', {}).get('end', option_end),
                           payload.get('period', {}).get('date', option_end),
                           payload.get('cards') or []))

    # Recheck after the potentially slow parse/calculation.  A concurrent
    # upload/settings change therefore cannot publish this candidate.
    current_fingerprint, _ = _revision(company_id)
    if current_fingerprint != fingerprint:
        raise RuntimeError('Metric snapshot is stale; source or settings changed')

    pointer = (ActiveMetricSnapshot.query.filter_by(company_id=company_id)
               .with_for_update().first())
    previous = db.session.get(MetricSnapshot, pointer.snapshot_id) if pointer else None
    version = (previous.version + 1) if previous else 1
    candidate = MetricSnapshot(company_id=company_id, version=version,
                               revision_fingerprint=fingerprint,
                               period=period, endpoint_date=endpoint)
    for granularity, start, end, endpoint_value, cards in calculated:
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(end, str):
            end = date.fromisoformat(end)
        if isinstance(endpoint_value, str):
            endpoint_value = date.fromisoformat(endpoint_value)
        for card in cards:
            metric_id = str(card.get('id') or '').strip()
            if not metric_id:
                continue
            value = card.get('value')
            if isinstance(value, (dict, list)):
                continue
            candidate.values.append(MetricSnapshotValue(
                metric_id=metric_id, value_date=endpoint_value, period_start=start,
                period_end=end, granularity=granularity, value_json=_json(value)))
    try:
        db.session.add(candidate)
        db.session.flush()
        if pointer is None:
            pointer = ActiveMetricSnapshot(
                company_id=company_id, snapshot_id=candidate.id,
                revision_fingerprint=fingerprint)
            db.session.add(pointer)
        else:
            pointer.snapshot_id = candidate.id
            pointer.revision_fingerprint = fingerprint
        db.session.commit()
    except Exception:
        # In particular, a concurrent publisher may win the unique
        # (company_id, version) race.  Never leave a failed candidate or a
        # half-updated pointer in the caller's transaction.
        db.session.rollback()
        raise
    return candidate


# Short explicit aliases useful to jobs and callers.
build_snapshot = build_metric_snapshot


def get_active_snapshot(company_id):
    pointer = ActiveMetricSnapshot.query.filter_by(company_id=company_id).first()
    return pointer.snapshot if pointer else None


def get_metric_snapshot(company_id, metric_id=None, value_date=None):
    """Return the active snapshot or its compact reader result.

    With only a company id this returns the immutable snapshot record; adding
    a metric or date is equivalent to ``read_metric_values``.
    """
    if metric_id is None and value_date is None:
        return get_active_snapshot(company_id)
    return read_metric_values(company_id, metric_id=metric_id,
                              value_date=value_date)


def read_metric_values(company_id, metric_id=None, value_date=None):
    """Read active scalar rows, optionally narrowed by card and date."""
    snapshot = get_active_snapshot(company_id)
    if snapshot is None:
        return []
    query = MetricSnapshotValue.query.filter_by(snapshot_id=snapshot.id)
    if metric_id is not None:
        query = query.filter_by(metric_id=str(metric_id))
    if value_date is not None:
        if isinstance(value_date, str):
            value_date = date.fromisoformat(value_date)
        query = query.filter_by(value_date=value_date)
    return [{
        'company_id': company_id, 'snapshot_id': snapshot.id,
        'version': snapshot.version, 'metric_id': row.metric_id,
        'date': row.value_date.isoformat() if row.value_date else None,
        'period_start': row.period_start.isoformat() if row.period_start else None,
        'period_end': row.period_end.isoformat() if row.period_end else None,
        'granularity': row.granularity,
        'value': json.loads(row.value_json),
    } for row in query.order_by(MetricSnapshotValue.metric_id).all()]


read_snapshot_values = read_metric_values
read_metric_snapshot = read_metric_values
publish_metric_snapshot = build_metric_snapshot