import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ROOT / 'artifacts' / 'osv-app' / 'scripts' / 'run-osv.sh'
CANONICAL = (
    'https://gisuzter.replit.app/limit-notifications/max/webhook'
)


class ProductionLaunchTest(unittest.TestCase):
    def _run(self, **environment):
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / 'python3'
            fake_python.write_text(textwrap.dedent("""\
                #!/usr/bin/env bash
                printf '%s' "${LIMITS_MAX_WEBHOOK_URL:-}"
            """))
            fake_python.chmod(0o755)
            env = os.environ.copy()
            env.update(environment)
            env['PATH'] = f'{directory}:{env["PATH"]}'
            env.setdefault('PORT', '8080')
            return subprocess.run(
                ['bash', str(LAUNCH)], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False)

    def test_deployment_wrapper_supplies_canonical_url_to_gunicorn(self):
        result = self._run(REPLIT_DEPLOYMENT='true', LIMITS_MAX_WEBHOOK_URL='')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(CANONICAL, result.stdout)

    def test_preview_does_not_inherit_production_url(self):
        result = self._run(REPLIT_DEPLOYMENT='', LIMITS_MAX_WEBHOOK_URL='')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('', result.stdout)

    def test_deployment_rejects_invalid_configured_url_before_gunicorn(self):
        result = self._run(
            REPLIT_DEPLOYMENT='1',
            LIMITS_MAX_WEBHOOK_URL='http://attacker.example/webhook?secret=1')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('LIMITS_MAX_WEBHOOK_URL', result.stderr)


if __name__ == '__main__':
    unittest.main()
