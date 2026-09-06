import importlib.util
import signal
import sys
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


if __name__ == '__main__':
    unittest.main()