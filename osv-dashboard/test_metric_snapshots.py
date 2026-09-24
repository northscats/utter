"""SQLite-only regression tests for the durable metric snapshot boundary."""
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory(prefix='metric-snapshots-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_tmp.name, 'test.db')

import app as app_module
from database import (ActiveMetricSnapshot, Company, CompanyMembership, DailyRow,
                      IndicatorSourceCache, MetricSnapshot, MetricSnapshotJob,
                      Upload, User, UserModuleAccess, db)
from metric_snapshots import (_periods, build_metric_snapshot,
                              get_precomputed_key_indicators,
                              process_metric_snapshot_job,
                              publish_source_snapshot, read_metric_values,
                              recover_metric_snapshot_jobs)
import metric_snapshots


class MetricSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = app_module.app.app_context()
        cls.ctx.push()
        db.drop_all()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.ctx.pop()
        _tmp.cleanup()

    def setUp(self):
        db.session.remove()
        db.drop_all()
        db.create_all()
        user = User(username='snapshot-user', password_hash='x', role='admin')
        db.session.add(user)
        db.session.flush()
        self.company = Company(name='Snapshot Co', created_by=user.id)
        db.session.add(self.company)
        db.session.commit()
        self.company_id = self.company.id
        self.user_id = user.id

    def test_publishes_dynamic_cards_without_full_payload(self):
        source = {'version': 1, 'accounts': {}}
        cards = [{'id': 'new-card', 'value': 12.5},
                 {'id': 'missing', 'value': None},
                 {'id': 'chart', 'value': {'points': [1, 2]}}]
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      return_value={'cards': cards}):
            snapshot = build_metric_snapshot(self.company.id, '/unused',
                                             endpoint_date=date(2026, 1, 31))
        self.assertEqual(snapshot.version, 1)
        self.assertEqual(
            {(row['metric_id'], row['value']) for row in
             read_metric_values(self.company.id)},
            {('missing', None), ('new-card', 12.5)})
        self.assertFalse(hasattr(snapshot, 'payload_json'))

    def test_sparse_source_uses_only_real_period_endpoints(self):
        source = {'dates': ['2025-01-31', '2025-03-31', '2026-02-28']}
        periods = _periods(source, date(2026, 2, 28))
        self.assertEqual(
            {(kind, end.isoformat()) for kind, end in periods},
            {('month', '2025-01-31'), ('month', '2025-03-31'),
             ('month', '2026-02-28'), ('quarter', '2025-03-31'),
             ('quarter', '2026-02-28'), ('year', '2025-03-31'),
             ('year', '2026-02-28')})

    def test_source_only_publication_serves_arbitrary_range_without_files(self):
        from key_indicators import build_indicator_source
        source = build_indicator_source({'51': {
            'acc': '51', 'period': 'Январь 2026',
            'daily': [{'date': '2026-01-01', 'obd': 3, 'obk': 0},
                      {'date': '2026-01-02', 'obd': 2, 'obk': 0}],
            'top': {'snd': 0, 'snk': 0, 'obd': 5, 'obk': 0,
                    'skd': 5, 'skk': 0},
        }})
        self.assertIsNone(get_precomputed_key_indicators(self.company.id))
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)) as loader:
            snapshot = publish_source_snapshot(self.company.id, '/unused')
        self.assertEqual(loader.call_count, 1)
        self.assertIsInstance(snapshot, IndicatorSourceCache)
        self.assertIsNone(ActiveMetricSnapshot.query.first())
        self.assertEqual(MetricSnapshot.query.count(), 0)
        with patch('key_indicators_service.load_company_source',
                   side_effect=AssertionError('Unexpected file read')):
            payload = get_precomputed_key_indicators(
                self.company.id, period='day', endpoint_date='2026-01-02',
                date_from='2026-01-01', section='cards')
            self.assertEqual(payload['period']['start'], '2026-01-01')
            self.assertIn('cards', payload)
            with patch('metric_snapshots._revision',
                       return_value=('changed-revision', {})):
                self.assertIsNone(get_precomputed_key_indicators(
                    self.company.id, period='day',
                    endpoint_date='2026-01-02'))

    def test_calculation_failure_does_not_replace_active_snapshot(self):
        source = {'version': 1, 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      return_value={'cards': [{'id': 'stable', 'value': 1}]}):
            build_metric_snapshot(self.company.id, '/unused',
                                  endpoint_date=date(2026, 1, 31))
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      side_effect=ValueError('bad source')):
            with self.assertRaises(ValueError):
                build_metric_snapshot(self.company.id, '/unused',
                                      endpoint_date=date(2026, 1, 31))
        self.assertEqual(read_metric_values(self.company.id)[0]['value'], 1)

    def test_summary_requires_access_and_reports_not_built(self):
        app_module.app.config.update(TESTING=True)
        company_id = self.company.id
        user_id = self.company.created_by
        url = (f'/analytics/summary?company_ids={company_id}'
               '&metric_ids=revenue&granularity=quarter'
               '&from=2026-01-01&to=2026-03-31')
        db.session.remove()
        self.ctx.pop()  # Request clients need independent Flask-Login contexts.
        try:
            anonymous = app_module.app.test_client()
            self.assertEqual(anonymous.get(url).status_code, 302)
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(user_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.location)
                self.assertEqual(response.json['companies'][0]['status'], 'not_built')
                self.assertEqual(response.json['values'], [])
                self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
                self.assertEqual(client.get('/analytics/summary?company_ids=bogus').status_code, 400)
        finally:
            self.ctx.push()

    def test_admin_home_alerts_current_failures_on_both_overviews(self):
        failed_at = datetime(2026, 9, 24, 12, 35, tzinfo=timezone.utc)
        second = Company(name='Вторая <РСО>', created_by=self.user_id)
        archived = Company(name='Архивная РСО', created_by=self.user_id,
                           status='archived')
        member = User(username='snapshot-member', password_hash='x', role='user')
        db.session.add_all([second, archived, member])
        db.session.flush()
        db.session.add(CompanyMembership(company_id=self.company_id,
                                         user_id=member.id))
        db.session.add(UserModuleAccess(user_id=member.id, module_key='analytics'))
        db.session.add_all([
            MetricSnapshotJob(company_id=self.company_id, status='failed',
                              error='broken', updated_at=failed_at),
            MetricSnapshotJob(company_id=second.id, status='failed',
                              error='broken', updated_at=failed_at),
            MetricSnapshotJob(company_id=archived.id, status='failed',
                              error='broken', updated_at=failed_at),
        ])
        db.session.commit()
        second_id, member_id = second.id, member.id
        app_module.app.config.update(TESTING=True)
        db.session.remove()
        self.ctx.pop()
        try:
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(self.user_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                for path in ('/home', '/home?legacy=1'):
                    html = client.get(path).get_data(as_text=True)
                    self.assertIn('Ошибка пересборки основных показателей: 2', html)
                    self.assertIn('24.09.2026 12:35', html)
                    self.assertIn(f'/company/{second_id}/key-indicators#ki-snapshot-state', html)
                    self.assertIn('Вторая &lt;РСО&gt;', html)
                    self.assertNotIn('Архивная РСО', html)
                with app_module.app.app_context():
                    db.session.get(MetricSnapshotJob, second_id).status = 'stale'
                    db.session.commit()
                self.assertIn('Ошибка пересборки основных показателей: 1',
                              client.get('/home').get_data(as_text=True))
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(member_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                for path in ('/home', '/home?legacy=1'):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn('Ошибка пересборки основных показателей',
                                     response.get_data(as_text=True))
        finally:
            self.ctx.push()

    def test_company_deletion_removes_snapshot_children(self):
        source = {'version': 1, 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
                patch('key_indicators.build_key_indicators',
                      return_value={'cards': [{'id': 'new-card', 'value': 1}]}):
            build_metric_snapshot(self.company.id, '/unused',
                                  endpoint_date=date(2026, 1, 31))
        company_id = self.company.id
        app_module._delete_company_records(self.company)
        db.session.commit()
        self.assertIsNone(db.session.get(Company, company_id))
        self.assertEqual(MetricSnapshot.query.count(), 0)
        self.assertEqual(ActiveMetricSnapshot.query.count(), 0)
        self.assertEqual(MetricSnapshotJob.query.count(), 0)

    def _source_upload(self):
        db.session.add(Upload(company_id=self.company.id,
                              uploaded_by=self.company.created_by,
                              period_label='2026', source='manual'))
        db.session.commit()

    def test_ingestion_and_settings_enqueue_in_same_transaction(self):
        self._source_upload()
        job = db.session.get(MetricSnapshotJob, self.company.id)
        self.assertEqual((job.generation, job.status), (1, 'stale'))
        db.session.add(DailyRow(company_id=self.company.id, date=date(2026, 1, 1),
                                acc='51'))
        db.session.flush()
        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(db.session.get(MetricSnapshotJob, self.company.id).generation, 1)
        row = Upload.query.first()
        row.period_label = '2027'
        db.session.commit()
        self.assertEqual(db.session.get(MetricSnapshotJob, self.company.id).generation, 2)

    def test_worker_failure_preserves_pointer_and_recovery_requeues(self):
        self._source_upload()
        company_id = self.company_id
        source = {'dates': ['2026-01-31'], 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
             patch('key_indicators.build_key_indicators',
                   return_value={'cards': [{'id': 'stable', 'value': 1}]}):
            self.assertTrue(process_metric_snapshot_job('/unused'))
        pointer = ActiveMetricSnapshot.query.filter_by(company_id=company_id).one()
        initial_id = pointer.snapshot_id
        Upload.query.first().period_label = '2027'
        db.session.commit()
        with patch('key_indicators_service.load_company_source',
                   side_effect=ValueError('bad source')):
            process_metric_snapshot_job('/unused')
        self.assertEqual(db.session.get(MetricSnapshotJob, company_id).status, 'failed')
        self.assertEqual(ActiveMetricSnapshot.query.first().snapshot_id, initial_id)
        self.assertFalse(process_metric_snapshot_job('/unused'))
        failed_generation = db.session.get(MetricSnapshotJob, company_id).generation
        recover_metric_snapshot_jobs()
        job = db.session.get(MetricSnapshotJob, company_id)
        self.assertEqual((job.status, job.generation),
                         ('failed', failed_generation))
        Upload.query.first().period_label = '2028'
        db.session.commit()
        job = db.session.get(MetricSnapshotJob, company_id)
        self.assertEqual(job.status, 'stale')
        job.status = 'building'
        db.session.commit()
        recover_metric_snapshot_jobs()
        self.assertEqual(db.session.get(MetricSnapshotJob, company_id).status, 'stale')

    def test_admin_retry_failed_job_preserves_publication_and_sources(self):
        self._source_upload()
        company_id = self.company_id
        source = {'dates': ['2026-01-31'], 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
             patch('key_indicators.build_key_indicators',
                   return_value={'cards': [{'id': 'revenue', 'value': 5}]}):
            process_metric_snapshot_job('/unused')
        original_id = ActiveMetricSnapshot.query.first().snapshot_id
        Upload.query.first().period_label = '2027'
        db.session.commit()
        # The upload schedules a parsed-source warmup before the full rebuild.
        # It must not take the place of the last complete scalar publication.
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)):
            cache = metric_snapshots.publish_indicator_source_cache(
                company_id, '/unused')
        self.assertIsInstance(cache, IndicatorSourceCache)
        self.assertEqual(ActiveMetricSnapshot.query.first().snapshot_id, original_id)
        self.assertEqual(read_metric_values(company_id)[0]['value'], 5)
        with patch('key_indicators_service.load_company_source',
                   side_effect=ValueError('bad source')):
            process_metric_snapshot_job('/unused')
        generation = db.session.get(MetricSnapshotJob, company_id).generation
        uploads = Upload.query.count()
        app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        db.session.remove()
        self.ctx.pop()
        try:
            with app_module.app.test_client() as client:
                path = f'/analytics/summary/{company_id}/retry'
                self.assertNotEqual(client.post(path).status_code, 200)
                with client.session_transaction() as session:
                    session['_user_id'] = str(self.user_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                self.assertEqual(client.post(path, headers={'Accept': 'application/json'}).status_code, 403)
                token = client.get('/rating-api/csrf').json['csrf_token']
                headers = {'X-CSRFToken': token, 'Accept': 'application/json'}
                response = client.post(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['status'], 'stale')
                self.assertEqual(client.post(path, headers=headers).status_code, 409)
                with app_module.app.app_context():
                    job = db.session.get(MetricSnapshotJob, company_id)
                    self.assertEqual((job.status, job.generation, job.error),
                                     ('stale', generation + 1, None))
                    self.assertEqual(ActiveMetricSnapshot.query.first().snapshot_id,
                                     original_id)
                    self.assertEqual(Upload.query.count(), uploads)
                with patch('key_indicators_service.load_company_source',
                           side_effect=ValueError('still bad')):
                    with app_module.app.app_context():
                        process_metric_snapshot_job('/unused')
                url = (f'/analytics/summary?company_ids={company_id}'
                       '&metric_ids=revenue&granularity=month'
                       '&from=2026-01-31&to=2026-01-31')
                item = client.get(url).json['companies'][0]
                self.assertEqual((item['status'], item['error'], item['snapshot_id']),
                                 ('failed', 'still bad', original_id))
                with app_module.app.app_context():
                    self.assertEqual(Upload.query.count(), uploads)
                    self.assertEqual(read_metric_values(company_id)[0]['value'], 5)
                self.assertEqual(client.post(path, headers=headers).status_code, 200)
                with patch('key_indicators_service.load_company_source',
                           return_value=(source, None)), \
                     patch('key_indicators.build_key_indicators',
                           return_value={'cards': [{'id': 'revenue', 'value': 8}]}):
                    with app_module.app.app_context():
                        process_metric_snapshot_job('/unused')
                        self.assertEqual(db.session.get(
                            MetricSnapshotJob, company_id).status, 'ready')
                        self.assertEqual(Upload.query.count(), uploads)
                        self.assertNotEqual(ActiveMetricSnapshot.query.first().snapshot_id,
                                            original_id)
                        self.assertEqual(read_metric_values(company_id)[0]['value'], 8)
        finally:
            self.ctx.push()

    def test_retry_rejects_non_admin_and_non_failed_jobs(self):
        self._source_upload()
        user = User(username='reader', password_hash='x', role='user')
        db.session.add(user)
        db.session.commit()
        reader_id = user.id
        company_id = self.company_id
        app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        db.session.remove()
        self.ctx.pop()
        try:
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(self.user_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                token = client.get('/rating-api/csrf').json['csrf_token']
                with client.session_transaction() as session:
                    session['_user_id'] = str(reader_id)
                headers = {'X-CSRFToken': token, 'Accept': 'application/json'}
                self.assertEqual(client.post(
                    f'/analytics/summary/{company_id}/retry',
                    headers=headers).status_code, 403)
            with app_module.app.app_context():
                job = db.session.get(MetricSnapshotJob, company_id)
                self.assertEqual((job.status, job.generation), ('stale', 1))
        finally:
            self.ctx.push()

    def test_arriving_upload_cannot_publish_obsolete_candidate(self):
        self._source_upload()
        company_id, user_id = self.company_id, self.user_id
        source = {'dates': ['2026-01-31'], 'accounts': {}}
        def during_calculation(*args, **kwargs):
            # A separate app context/session represents a concurrent ingestion.
            with app_module.app.app_context():
                db.session.add(Upload(company_id=company_id,
                                      uploaded_by=user_id,
                                      period_label='2027', source='manual'))
                db.session.commit()
            return {'cards': [{'id': 'stable', 'value': 1}]}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
             patch('key_indicators.build_key_indicators',
                   side_effect=during_calculation):
            process_metric_snapshot_job('/unused')
        self.assertEqual(MetricSnapshot.query.count(), 0)
        self.assertEqual(db.session.get(MetricSnapshotJob, company_id).status, 'stale')
        self.assertGreaterEqual(
            db.session.get(MetricSnapshotJob, company_id).generation, 2)

    def test_summary_exposes_queue_state_without_hiding_old_publication(self):
        self._source_upload()
        company_id = self.company_id
        source = {'dates': ['2026-01-31'], 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
             patch('key_indicators.build_key_indicators',
                   return_value={'cards': [{'id': 'revenue', 'value': 5}]}):
            process_metric_snapshot_job('/unused')
        snapshot_id = ActiveMetricSnapshot.query.first().snapshot_id
        job = db.session.get(MetricSnapshotJob, company_id)
        self.assertEqual(job.status, 'ready')
        # A changed upload marks the publication stale without deleting it.
        Upload.query.first().period_label = '2027'
        db.session.commit()
        app_module.app.config.update(TESTING=True)
        db.session.remove()
        self.ctx.pop()
        try:
            with app_module.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(self.user_id)
                    session['_fresh'] = True
                    session['password_version'] = 1
                url = (f'/analytics/summary?company_ids={company_id}'
                       '&metric_ids=revenue&granularity=month'
                       '&from=2026-01-31&to=2026-01-31')
                for state in ('stale', 'building', 'failed'):
                    with app_module.app.app_context():
                        job = db.session.get(MetricSnapshotJob, company_id)
                        job.status = state
                        job.error = 'bad source' if state == 'failed' else None
                        db.session.commit()
                    item = client.get(url).json['companies'][0]
                    self.assertEqual(item['status'], state)
                    self.assertEqual(item['snapshot_id'], snapshot_id)
                    self.assertEqual(item['error'], 'bad source' if state == 'failed' else None)
        finally:
            self.ctx.push()

    def test_methodology_change_requeues_ready_publication(self):
        self._source_upload()
        source = {'dates': ['2026-01-31'], 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
             patch('key_indicators.build_key_indicators',
                   return_value={'cards': [{'id': 'revenue', 'value': 5}]}):
            process_metric_snapshot_job('/unused')
        with patch('metric_snapshots.METHODOLOGY_VERSION',
                   'metric-snapshots-next-methodology'):
            recover_metric_snapshot_jobs()
        self.assertEqual(db.session.get(MetricSnapshotJob, self.company_id).status,
                         'stale')

    def test_methodology_change_requeues_failed_first_build(self):
        self._source_upload()
        with patch('key_indicators_service.load_company_source',
                   side_effect=ValueError('bad source')):
            process_metric_snapshot_job('/unused')
        job = db.session.get(MetricSnapshotJob, self.company_id)
        self.assertEqual(job.status, 'failed')
        generation = job.generation
        recover_metric_snapshot_jobs()
        self.assertEqual(db.session.get(MetricSnapshotJob, self.company_id).status,
                         'failed')
        with patch('metric_snapshots.METHODOLOGY_VERSION',
                   'metric-snapshots-next-methodology'):
            recover_metric_snapshot_jobs()
        job = db.session.get(MetricSnapshotJob, self.company_id)
        self.assertEqual((job.status, job.generation), ('stale', generation + 1))

    def test_direct_daily_bulk_delete_enqueues_before_commit(self):
        company_id = self.company_id
        db.session.add(DailyRow(company_id=company_id, date=date(2026, 1, 31),
                                acc='51'))
        db.session.commit()
        source = {'dates': ['2026-01-31'], 'accounts': {}}
        with patch('key_indicators_service.load_company_source',
                   return_value=(source, None)), \
             patch('key_indicators.build_key_indicators',
                   return_value={'cards': [{'id': 'cash', 'value': 5}]}):
            process_metric_snapshot_job('/unused')
        original = metric_snapshots.enqueue_metric_snapshot
        observed = []
        def record_enqueue(company_id):
            observed.append(DailyRow.query.filter_by(company_id=company_id).count())
            original(company_id)
        app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        db.session.remove()
        self.ctx.pop()
        try:
            with patch('metric_snapshots.enqueue_metric_snapshot',
                       side_effect=record_enqueue):
                with app_module.app.test_client() as client:
                    with client.session_transaction() as session:
                        session['_user_id'] = str(self.user_id)
                        session['_fresh'] = True
                        session['password_version'] = 1
                    response = client.delete(
                        f'/company/{company_id}/coverage-data',
                        json={'mode': 'period', 'date_from': '2026-01-01',
                              'date_to': '2026-01-31', 'accounts': ['51']})
                    self.assertEqual(response.status_code, 200)
            with app_module.app.app_context():
                job = db.session.get(MetricSnapshotJob, company_id)
                self.assertEqual(job.status, 'stale')
                self.assertGreaterEqual(job.generation, 2)
                self.assertEqual(ActiveMetricSnapshot.query.first().company_id,
                                 company_id)
            self.assertEqual(observed, [0])
        finally:
            app_module.app.config['WTF_CSRF_ENABLED'] = True
            self.ctx.push()


if __name__ == '__main__':
    unittest.main()
