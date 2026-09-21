import importlib.util
import json
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def _load_gunicorn_config():
    path = Path(__file__).with_name('gunicorn.conf.py')
    spec = importlib.util.spec_from_file_location('osv_gunicorn_config', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HealthWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.config = _load_gunicorn_config()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config.health_watchdog_event_spool = str(
            Path(self.temp_dir.name) / 'watchdog-events.json')
        self.config._pending_recovery_events.clear()
        self.config._recovery_worker_pid = None

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_timeout_terminates_the_only_worker(self):
        worker = Mock(pid=4321)
        server = Mock()
        server.WORKERS = {worker.pid: worker}

        with patch.object(self.config.os, 'kill') as kill:
            restarted = self.config._terminate_unresponsive_worker(server)

        self.assertTrue(restarted)
        kill.assert_called_once_with(worker.pid, signal.SIGABRT)

    def test_recovery_is_skipped_if_worker_count_is_not_one(self):
        server = Mock()
        server.WORKERS = {1: Mock(pid=1), 2: Mock(pid=2)}

        with patch.object(self.config.os, 'kill') as kill:
            restarted = self.config._terminate_unresponsive_worker(server)

        self.assertFalse(restarted)
        kill.assert_not_called()

    def test_failed_probe_triggers_recovery(self):
        server = Mock()
        server.WORKERS = {1: Mock(pid=1)}
        stop_event = Mock()
        stop_event.is_set.side_effect = [False]
        stop_event.wait.side_effect = [False, True]

        with patch.object(self.config, '_probe_health', return_value=False), \
                patch.object(
                    self.config,
                    '_terminate_unresponsive_worker',
                    return_value=True,
                ) as terminate:
            self.config._watchdog_loop(server, stop_event)

        terminate.assert_called_once_with(server)

    def test_replacement_worker_gets_a_fresh_startup_grace(self):
        class Worker:
            def __init__(self, pid):
                self.pid = pid

        class Server:
            def __init__(self):
                self._worker_sets = [
                    {1: Worker(1)},
                    {2: Worker(2)},
                ]
                self.log = Mock()

            @property
            def WORKERS(self):
                if len(self._worker_sets) > 1:
                    return self._worker_sets.pop(0)
                return self._worker_sets[0]

        server = Server()
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, False]
        stop_event.wait.side_effect = [False, False, True]

        with patch.object(self.config, '_probe_health', return_value=True) as probe:
            self.config._watchdog_loop(server, stop_event)

        self.assertEqual(probe.call_count, 1)
        self.assertEqual(
            stop_event.wait.call_args_list[1].args[0],
            self.config.health_watchdog_startup_grace,
        )

    def test_recovery_event_is_queued_before_worker_is_terminated(self):
        worker = Mock(pid=4321)
        server = Mock()
        server.WORKERS = {worker.pid: worker}

        with patch.object(self.config.os, 'kill'):
            self.config._terminate_unresponsive_worker(server)

        self.assertEqual(len(self.config._pending_recovery_events), 1)
        self.assertEqual(
            self.config._pending_recovery_events[0]['worker_pid'], worker.pid)
        self.assertIn(
            'occurred_at', self.config._pending_recovery_events[0])
        self.assertIn(
            'event_id', self.config._pending_recovery_events[0])
        with open(
                self.config.health_watchdog_event_spool,
                encoding='utf-8') as spool:
            persisted = json.load(spool)
        self.assertEqual(persisted, self.config._pending_recovery_events)

    def test_same_worker_is_not_terminated_twice_while_replacement_is_pending(self):
        worker = Mock(pid=4321)
        server = Mock()
        server.WORKERS = {worker.pid: worker}

        with patch.object(self.config.os, 'kill') as kill:
            self.assertTrue(
                self.config._terminate_unresponsive_worker(server))
            self.assertFalse(
                self.config._terminate_unresponsive_worker(server))

        kill.assert_called_once_with(worker.pid, signal.SIGABRT)
        self.assertEqual(len(self.config._pending_recovery_events), 1)

    def test_queued_event_is_delivered_separately(self):
        server = Mock()
        self.config._pending_recovery_events.extend([
            {'event_id': 'event-1',
             'occurred_at': '2026-09-06T10:00:00+00:00', 'worker_pid': 1},
            {'event_id': 'event-2',
             'occurred_at': '2026-09-06T10:01:00+00:00', 'worker_pid': 2},
        ])
        response = Mock()
        response.status = 204
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch.object(
                self.config.urllib.request,
                'urlopen',
                side_effect=[response, response]) as urlopen:
            delivered = self.config._deliver_recovery_events(server)

        self.assertTrue(delivered)
        self.assertEqual(self.config._pending_recovery_events, [])
        self.assertEqual(urlopen.call_count, 2)
        bodies = [
            json.loads(call.args[0].data.decode('utf-8'))
            for call in urlopen.call_args_list
        ]
        self.assertEqual([body['worker_pid'] for body in bodies], [1, 2])
        with open(
                self.config.health_watchdog_event_spool,
                encoding='utf-8') as spool:
            self.assertEqual(json.load(spool), [])

    def test_pending_events_are_restored_after_master_restart(self):
        events = [{
            'event_id': 'event-1',
            'occurred_at': '2026-09-06T10:00:00+00:00',
            'worker_pid': 1,
        }]
        with open(
                self.config.health_watchdog_event_spool,
                'w',
                encoding='utf-8') as spool:
            json.dump(events, spool)

        self.config._restore_pending_recovery_events(Mock())

        self.assertEqual(self.config._pending_recovery_events, events)


class HealthEndpointTest(unittest.TestCase):
    def test_health_endpoint_does_not_start_imap_or_query_database(self):
        try:
            import app
        except (KeyError, SystemExit):
            self.skipTest('application secrets/database are unavailable')

        app.app.config['TESTING'] = True
        with patch.object(app, '_ensure_imap_thread') as ensure_imap, \
                patch.object(app.db.session, 'get') as db_get:
            response = app.app.test_client().get('/healthz')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})
        ensure_imap.assert_not_called()
        db_get.assert_not_called()

    def test_watchdog_event_endpoint_records_local_event(self):
        try:
            import app
        except (KeyError, SystemExit):
            self.skipTest('application secrets/database are unavailable')

        app.app.config['TESTING'] = True
        with app.app.app_context():
            with patch.object(app, 'log_event') as log_event, \
                    patch.object(app, '_ensure_imap_thread') as ensure_imap, \
                    patch.object(app.AuditLog, 'query') as audit_query:
                log_event.return_value = True
                audit_query.filter_by.return_value.filter.return_value.first.return_value = None
                response = app.app.test_client().post(
                    '/healthz/watchdog-recovery',
                    json={
                        'event_id': 'event-4321',
                        'occurred_at': '2026-09-06T10:00:00+00:00',
                        'worker_pid': 4321,
                    },
                    headers={'X-Watchdog-Event': '1'},
                    environ_base={'REMOTE_ADDR': '127.0.0.1'},
                )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(log_event.call_count, 1)
        self.assertEqual(
            log_event.call_args.args[0], 'http_worker_watchdog_recovery')
        self.assertEqual(
            log_event.call_args.args[1],
            {
                'event_id': 'event-4321',
                'occurred_at': '2026-09-06T10:00:00+00:00',
                'worker_pid': 4321,
            })
        self.assertEqual(log_event.call_args.kwargs, {'ip': '127.0.0.1'})
        ensure_imap.assert_not_called()

    def test_watchdog_event_endpoint_rejects_remote_request(self):
        try:
            import app
        except (KeyError, SystemExit):
            self.skipTest('application secrets/database are unavailable')

        app.app.config['TESTING'] = True
        with patch.object(app, 'log_event') as log_event:
            response = app.app.test_client().post(
                '/healthz/watchdog-recovery',
                json={'event_id': 'event-4321',
                      'occurred_at': '2026-09-06T10:00:00+00:00',
                      'worker_pid': 4321},
                headers={'X-Watchdog-Event': '1'},
                environ_base={'REMOTE_ADDR': '10.0.0.1'},
            )

        self.assertEqual(response.status_code, 404)
        log_event.assert_not_called()


if __name__ == '__main__':
    unittest.main()
