"""Concurrent 60-company KPI section benchmark on isolated SQLite.

Run: cd osv-dashboard && python benchmark_metric_ranges.py
Eight workers submit 60 independent cold company requests at once. The fixture
has thousands of daily analytic rows, not a two-row account. The real KGH
numeric-parity test is separate; this checks contention and transfer size.
"""
import json
import os
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix='metric-range-load-') as directory:
    os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(directory, 'load.db')

    from app import _key_indicator_section, app
    from database import (ActiveMetricSnapshot, Company, MetricSnapshot,
                          MetricSnapshotSource, User, db)
    from key_indicators import build_indicator_source
    from metric_snapshots import (_pack_source, get_precomputed_key_indicators)

    days = [f'2026-01-{day:02d}' for day in range(1, 15)]
    source = build_indicator_source({
        '51': {
            'acc': '51', 'period': 'Январь 2026',
            'top': {'snd': 10, 'snk': 0, 'obd': 140, 'obk': 0,
                    'skd': 150, 'skk': 0},
            'daily': [{'date': day, 'obd': 10, 'obk': 0} for day in days],
        },
        '62': {
            'acc': '62', 'period': 'Январь 2026',
            'top': {'snd': 0, 'snk': 0, 'obd': 0, 'obk': 0,
                    'skd': 1000, 'skk': 0},
            'daily': [{
                'date': day, 'path1': '62.01', 'path2': f'Контрагент {i}',
                'obd': i + 1, 'obk': 0,
            } for day in days for i in range(150)],
        },
    })
    blob = _pack_source(source)
    with app.app_context():
        db.create_all()
        user = User(username='range-load', password_hash='x', role='admin')
        db.session.add(user)
        db.session.flush()
        ids = []
        for i in range(60):
            company = Company(name=f'Range load {i}', created_by=user.id)
            db.session.add(company)
            db.session.flush()
            snapshot = MetricSnapshot(
                company_id=company.id, version=1,
                revision_fingerprint='benchmark', period='source')
            snapshot.source_cache = MetricSnapshotSource(
                revision_fingerprint='benchmark', source_blob=blob)
            db.session.add(snapshot)
            db.session.flush()
            db.session.add(ActiveMetricSnapshot(
                company_id=company.id, snapshot_id=snapshot.id,
                revision_fingerprint='benchmark'))
            ids.append(company.id)
        db.session.commit()

    def request_company(company_id):
        with app.app_context():
            started = time.perf_counter()
            sections = []
            section_bytes = {}
            cards_ms = None
            for section in ('meta', 'cards', 'tables', 'charts'):
                payload = get_precomputed_key_indicators(
                    company_id, period='day',
                    endpoint_date=days[-1], date_from=days[0])
                assert payload is not None and payload['period']['start'] == days[0]
                projected = _key_indicator_section(payload, section)
                sections.append(projected)
                section_bytes[section] = len(json.dumps(
                    projected, ensure_ascii=False,
                    default=str).encode('utf-8'))
                if section == 'cards':
                    cards_ms = (time.perf_counter() - started) * 1000
            assert 'cards' not in sections[2], 'Tables duplicate card details'
            assert sections[1]['cards'][0]['id'] == sections[3]['cards'][0]['id']
            return cards_ms, (time.perf_counter() - started) * 1000, section_bytes

    with patch('metric_snapshots._revision',
               return_value=('benchmark', {})), \
            patch('metric_snapshots._calculation_inputs',
                  return_value=([], [], [], [], [], [])), \
            patch('key_indicators_service.load_company_source',
                  side_effect=AssertionError('Raw ОСВ must not be opened')):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            samples = list(pool.map(request_company, ids))
        wall = time.perf_counter() - started

    def p95(values):
        return sorted(values)[int(len(values) * .95) - 1]

    card_times, total_times, sizes = zip(*samples)
    median_bytes = {section: int(statistics.median(
        sample[section] for sample in sizes))
        for section in ('meta', 'cards', 'tables', 'charts')}
    print(f'companies=60 workers=8 daily_analytic_rows=2100 '
          f'source_compressed_bytes={len(blob)} '
          f'cards_p95_ms={p95(card_times):.1f} '
          f'total_p95_ms={p95(total_times):.1f} '
          f'wall_s={wall:.1f} '
          f'section_bytes_median={median_bytes} '
          f'total_bytes_median={sum(median_bytes.values())}')
    # Deliberately generous for shared CI hardware; catches process-wide
    # serialization and accidental 60x rescans, not microsecond regressions.
    assert p95(card_times) < 30000 and wall < 120, (
        'Concurrent KPI selection exceeded the load budget')