"""Explicit offline backfill for compact, versioned metric snapshots.

Run after the application schema is initialized:
    cd osv-dashboard && python rebuild_metric_snapshots.py --company-id 3
No web request starts a potentially long all-period backfill.
"""
import argparse

from app import UPLOADS, app
from database import Company, db
from metric_snapshots import build_metric_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--company-id', type=int)
    group.add_argument('--all', action='store_true')
    args = parser.parse_args()
    with app.app_context():
        ids = ([args.company_id] if args.company_id is not None
               else [row[0] for row in db.session.query(Company.id).order_by(Company.id)])
        for company_id in ids:
            try:
                snapshot = build_metric_snapshot(company_id, UPLOADS)
                print(f'company={company_id} snapshot={snapshot.id} version={snapshot.version}')
            except Exception as exc:
                db.session.rollback()
                print(f'company={company_id} failed: {type(exc).__name__}: {exc}')
                if args.company_id is not None:
                    raise


if __name__ == '__main__':
    main()