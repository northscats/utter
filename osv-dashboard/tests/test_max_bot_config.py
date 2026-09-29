"""Private MAX file lifecycle and closed-archive transfer."""
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import max_bot_config
import max_limits
from flask import Flask


class MaxBotConfigTests(unittest.TestCase):
    def test_public_webhook_matches_deployment_base_path(self):
        root_url = 'https://example.org/limit-notifications/max/webhook'
        nested_url = 'https://example.org/osv/limit-notifications/max/webhook'
        with patch.dict(os.environ, {'SCRIPT_NAME': '/osv'}):
            self.assertTrue(max_limits.valid_webhook_url(nested_url))
            self.assertFalse(max_limits.valid_webhook_url(root_url))
            self.assertFalse(max_limits.valid_webhook_url(nested_url + '?token=x'))
            self.assertFalse(max_limits.valid_webhook_url(
                'http://example.org/osv/limit-notifications/max/webhook'))
        with patch.dict(os.environ, {'SCRIPT_NAME': ''}):
            self.assertTrue(max_limits.valid_webhook_url(root_url))
            self.assertFalse(max_limits.valid_webhook_url(nested_url))
        project = Path(__file__).resolve().parents[1]
        self.assertIn('MAX_LIMITS_WEBHOOK_URL: ${MAX_LIMITS_WEBHOOK_URL:-}',
                      (project / 'docker-compose.yml').read_text())
        self.assertIn('INITIAL_ADMIN_PASSWORD: ${INITIAL_ADMIN_PASSWORD}',
                      (project / 'docker-compose.yml').read_text())
        self.assertIn('MAX_LIMITS_WEBHOOK_URL=https://домен.ru/osv/',
                      (project / '.env.example').read_text())
        with tempfile.TemporaryDirectory() as root, \
             patch.object(max_bot_config, 'CONFIG', Path(root) / 'private' / 'max-bot.json'), \
             patch.dict(os.environ, {
                 'SCRIPT_NAME': '/osv',
                 'MAX_LIMITS_REQUIRE_WEBHOOK': '1',
                 'MAX_LIMITS_WEBHOOK_URL': '',
                 'REPLIT_DEPLOYMENT': '',
             }):
            max_bot_config.save('F' * 40, '-345')
            max_bot_config.set_enabled(True)
            app = Flask(__name__)
            app.config['DURABLE_STARTUP_READY'] = True
            self.assertTrue(max_limits.requires_webhook())
            with patch.object(max_limits, '_worker_thread', None):
                self.assertFalse(max_limits.start_max_limits_worker(app))

    def test_rapid_stop_start_replaces_stale_polling_and_webhook_workers(self):
        class ThreadStub:
            def __init__(self, target=None, args=(), **kwargs):
                self.target, self.args = target, args
                self.started = False

            def start(self):
                self.started = True

            def is_alive(self):
                return True

        for webhook in ('', 'https://example.org/limit-notifications/max/webhook'):
            with self.subTest(webhook=bool(webhook)), tempfile.TemporaryDirectory() as root:
                token = 'F' * 40
                app = Flask(__name__)
                app.config['DURABLE_STARTUP_READY'] = True
                with patch.object(max_bot_config, 'CONFIG', Path(root) / 'private' / 'max-bot.json'), \
                     patch.object(max_limits, '_worker_thread', None), \
                     patch.object(max_limits, '_worker_token', None), \
                     patch.object(max_limits, '_worker_generation', None), \
                     patch.object(max_limits, '_connected_token', None), \
                     patch.object(max_limits, '_connected_generation', None), \
                     patch.object(max_limits, '_last_error', False), \
                     patch.object(max_limits.threading, 'Thread', ThreadStub), \
                     patch.object(max_limits, '_unregister_webhook'), \
                     patch.dict(os.environ, {'MAX_LIMITS_WEBHOOK_URL': webhook}):
                    max_bot_config.save(token, '-345')
                    max_bot_config.set_enabled(True)
                    self.assertTrue(max_limits.start_max_limits_worker(app))
                    first = max_limits._worker_thread
                    old_generation = max_limits._worker_generation
                    max_limits._connected_token = token
                    max_limits._connected_generation = old_generation
                    self.assertEqual(max_limits.status(), 'connected')
                    max_limits.stop_max_limits_worker()
                    self.assertEqual(max_limits.status(), 'stopped')
                    max_bot_config.set_enabled(True)
                    self.assertTrue(max_limits.start_max_limits_worker(app))
                    self.assertIsNot(first, max_limits._worker_thread)
                    self.assertEqual(max_limits.status(), 'connecting')
                    self.assertNotEqual(old_generation, max_limits._worker_generation)
                    with patch.object(max_limits, '_verify_group') as verify:
                        if webhook:
                            max_limits._webhook_outbox_loop(
                                app, token, webhook, old_generation)
                        else:
                            max_limits._worker_loop(app, token, old_generation)
                        verify.assert_not_called()
                    max_limits._connected_token = token
                    max_limits._connected_generation = max_limits._worker_generation
                    self.assertEqual(max_limits.status(), 'connected')
                    max_bot_config.save('', '-456')
                    max_limits.stop_max_limits_worker()
                    max_bot_config.set_enabled(True)
                    prior = max_limits._worker_thread
                    self.assertTrue(max_limits.start_max_limits_worker(app))
                    self.assertIsNot(prior, max_limits._worker_thread)
                    self.assertEqual(max_bot_config.read()['group_id'], -456)
                    self.assertEqual(max_limits.status(), 'connecting')

    def test_docker_replacement_keeps_mounted_settings_and_rechecks_connection(self):
        """A rebuilt image has no secret; the mounted volume remains the source."""
        project = Path(__file__).resolve().parents[1]
        compose = (project / 'docker-compose.yml').read_text()
        dockerfile = (project / 'Dockerfile').read_text()
        self.assertIn('max_bot_private:/app/private', compose)
        self.assertIn('max_bot_private:', compose)
        self.assertIn('chmod 0700 /app/private', dockerfile)
        with tempfile.TemporaryDirectory() as root:
            volume_file = Path(root) / 'volume' / 'max-bot.json'
            with patch.object(max_bot_config, 'CONFIG', volume_file):
                max_bot_config.save('E' * 40, '-345')
                max_bot_config.set_enabled(True)
                self.assertEqual(max_limits.status(), 'connecting')
            # Stand in for a replacement web process: it starts with no
            # in-memory connection proof, but sees the same mounted file.
            script = (
                "import sys; from pathlib import Path; "
                "import max_bot_config as c; import max_limits as m; "
                "c.CONFIG=Path(sys.argv[1]); "
                "assert c.read()['enabled']; "
                "assert c.read()['group_id']==-345; "
                "assert m.status()=='connecting'; "
                "assert c.active('E'*40)"
            )
            subprocess.run([sys.executable, '-c', script, str(volume_file)],
                           cwd=project, check=True, capture_output=True, text=True)
            with patch.object(max_bot_config, 'CONFIG', volume_file):
                self.assertEqual(stat.S_IMODE(volume_file.stat().st_mode), 0o600)

    def test_validation_atomic_permissions_and_private_transfer(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'source' / 'osv-dashboard' / 'private' / 'max-bot.json'
            with patch.object(max_bot_config, 'CONFIG', source):
                with self.assertRaises(ValueError):
                    max_bot_config.save('short', '-345')
                self.assertFalse(source.exists())
                token = 'B' * 40
                max_bot_config.save(token, '-345')
                self.assertFalse(max_bot_config.read()['enabled'])
                self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
                max_bot_config.set_enabled(True)
                self.assertTrue(max_bot_config.active(token))
                with patch.dict(os.environ, {
                    'MAX_LIMITS_BOT_TOKEN': 'legacy-env-value',
                    'MAX_LIMITS_MODERATION_CHAT_ID': '-999',
                }):
                    self.assertEqual(max_bot_config.read()['group_id'], -345)
                    self.assertEqual(max_bot_config.read()['token'], token)
                max_bot_config.set_enabled(False)
                self.assertFalse(max_bot_config.active(token))
                max_bot_config.set_enabled(True)
                with self.assertRaises(ValueError):
                    max_bot_config.save('', 'not-an-id')
                self.assertTrue(max_bot_config.active(token))
                max_bot_config.save('C' * 40, '-345')
                self.assertFalse(max_bot_config.active(token))
                self.assertFalse(max_bot_config.read()['enabled'])
                max_bot_config.set_enabled(True)
                archive = Path(root) / 'closed.tar'
                with tarfile.open(archive, 'w') as tar:
                    tar.add(source, arcname='osv-dashboard/private/max-bot.json')
                target = Path(root) / 'target'
                with tarfile.open(archive) as tar:
                    tar.extractall(target, filter='data')
                restored = target / 'osv-dashboard' / 'private' / 'max-bot.json'
                os.chmod(restored.parent, 0o700)
                os.chmod(restored, 0o600)
            with patch.object(max_bot_config, 'CONFIG', restored):
                self.assertTrue(max_bot_config.active('C' * 40))
                max_bot_config.set_enabled(False)
                self.assertFalse(max_bot_config.active('C' * 40))

    def test_stopped_queue_is_preserved_and_delivery_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(max_bot_config, 'CONFIG', Path(root) / 'private' / 'max-bot.json'):
                max_bot_config.save('D' * 40, '-345')
                max_bot_config.set_enabled(False)
                with patch('max_limits.selected_dataset_id') as dataset:
                    self.assertEqual(max_limits.drain_outbox(), 0)
                    dataset.assert_not_called()
                with self.assertRaises(RuntimeError):
                    max_limits.dispatch_update('D' * 40, {'update_type': 'bot_started'})