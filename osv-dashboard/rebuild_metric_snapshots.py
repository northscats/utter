"""Explicit offline backfill for compact, versioned metric snapshots.

Run after the application schema is initialized:
    cd osv-dashboard && python rebuild_metric_snapshots.py --company-id 3
Web requests only enqueue work; the background worker performs the build.
"""
import argparse

from app import UPLOADS, app
from database import Company, MetricSnapshotJob, db
from metric_snapshots import enqueue_metric_snapshot, process_metric_snapshot_job


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
                enqueue_metric_snapshot(company_id)
                db.session.commit()
                process_metric_snapshot_job(UPLOADS, company_id=company_id)
                job = db.session.get(MetricSnapshotJob, company_id)
                if job.status != 'ready':
                    raise RuntimeError(f'build {job.status}: {job.error or "retry pending"}')
                print(f'company={company_id} ready')
            except Exception as exc:
                db.session.rollback()
                print(f'company={company_id} failed: {type(exc).__name__}: {exc}')
                if args.company_id is not None:
                    raise


if __name__ == '__main__':
    main()