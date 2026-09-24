"""Read-only audit of the ten operational cards and active scalar publication.

Example: cd osv-dashboard && python verify_metric_card_catalog.py --company-id 3
Does not rebuild or publish snapshots.
"""
import argparse
import json
from app import UPLOADS, app
from database import Company, db
from key_indicators import _iso, _options, build_key_indicators
from key_indicators_service import load_company_source
from metric_snapshots import (_calculation_inputs, _revision,
                              get_active_snapshot, read_metric_values)


EXPECTED = (
    'cash', 'cash_dz', 'revenue', 'collection', 'ap', 'cogs',
    'profit', 'credits', 'salary', 'capex',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--company-id', type=int, required=True)
    args = parser.parse_args()
    with app.app_context():
        company = db.session.get(Company, args.company_id)
        if company is None:
            parser.error('Предприятие не найдено')
        source, _ = load_company_source(args.company_id, UPLOADS)
        dates = sorted(x for x in (_iso(value) for value in source.get('dates', []))
                       if x is not None)
        if not dates:
            parser.error('В источнике нет дат')
        inputs = _calculation_inputs(args.company_id)
        snapshot = get_active_snapshot(args.company_id)
        current = snapshot and snapshot.revision_fingerprint == _revision(args.company_id)[0]
        print(f'company={company.name}; snapshot={snapshot.id if snapshot else "none"}; '
              f'current={bool(current)}')
        failures = []
        for period in ('month', 'quarter', 'year'):
            endpoint = _options(dates, period)[-1]['value']
            payload = build_key_indicators(
                source, period=period, date=endpoint,
                revenue_mappings=inputs[0], cost_mappings=inputs[1],
                debtor_mappings=inputs[2], collection_accruals=inputs[3],
                collection_allocations=inputs[4], collection_norms=inputs[5])
            cards = payload['cards']
            ids = tuple(card['id'] for card in cards)
            if ids != EXPECTED:
                failures.append(f'{period}: IDs {ids!r}')
            if current:
                rows = [row for row in read_metric_values(args.company_id, value_date=endpoint)
                        if row['granularity'] == period
                        and row['period_start'] == payload['period']['start']
                        and row['period_end'] == payload['period']['end']]
                by_id = {row['metric_id']: row['value'] for row in rows}
                expected = {card['id']: card['value'] for card in cards}
                if by_id != expected:
                    failures.append(f'{period}: snapshot != operational; '
                                    f'missing={sorted(set(expected) - set(by_id))}; '
                                    f'different={sorted(key for key in by_id if by_id[key] != expected.get(key))}')
            missing = {card['id']: card.get('reason') for card in cards
                       if card.get('value') is None}
            print(json.dumps({'period': period, 'end': endpoint, 'count': len(cards),
                              'null_reasons': missing}, ensure_ascii=False))
        if failures:
            raise SystemExit('\n'.join(failures))
        print('card catalog OK; snapshot equality ' +
              ('OK' if current else 'skipped: active snapshot is stale or not built'))


if __name__ == '__main__':
    main()