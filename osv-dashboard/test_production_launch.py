import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ROOT / 'artifacts' / 'osv-app' / 'scripts' / 'run-osv.sh'


class ProductionLaunchTest(unittest.TestCase):
    def _run(self, **environment):
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / 'python3'
            fake_python.write_text(textwrap.dedent("""\
                #!/usr/bin/env bash
                printf 'gunicorn started'
            """))
            fake_python.chmod(0o755)
            env = os.environ.copy()
            env.update(environment)
            env['PATH'] = f'{directory}:{env["PATH"]}'
            env.setdefault('PORT', '8080')
            return subprocess.run(
                ['bash', str(LAUNCH)], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False)

    def test_launch_requires_only_port_and_starts_gunicorn(self):
        result = self._run(REPLIT_DEPLOYMENT='true')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('gunicorn started', result.stdout)

    def test_launch_requires_port(self):
        result = self._run(PORT='')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('PORT', result.stderr)


if __name__ == '__main__':
    unittest.main()
