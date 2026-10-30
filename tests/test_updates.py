"""Remote discovery uses local Git fixtures and never contacts an external service."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'apps/dev-monitor/backend'))
from hub import Hub
import updates


class UpdateTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    def test_fetch_discovers_remote_app_commit_and_records_check_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            origin = base / 'origin'
            origin.mkdir()
            self.git(origin, 'init', '-b', 'main')
            self.git(origin, 'config', 'user.name', 'Fixture')
            self.git(origin, 'config', 'user.email', 'fixture@example.test')
            for name in ('hub', 'apps/devterm'):
                directory = origin / name
                directory.mkdir(parents=True)
                (directory / 'app.toml').write_text('label="App"\nicon="icon.svg"\nport=19930\n')
            self.git(origin, 'add', '.')
            self.git(origin, 'commit', '-m', 'initial')
            old = self.git(origin, 'rev-parse', 'HEAD')
            clone = base / 'clone'
            self.git(base, 'clone', str(origin), str(clone))
            (clone / 'state').mkdir()
            (clone / 'state/installed.json').write_text(json.dumps({'apps': {'hub': {'commit': old}, 'devterm': {'commit': old}}}))
            (origin / 'apps/devterm/change.txt').write_text('new version')
            self.git(origin, 'add', '.')
            self.git(origin, 'commit', '-m', 'terminal update')
            snapshot = updates.refresh(clone)
            self.assertIsNone(snapshot['error'])
            self.assertTrue(snapshot['checkedAt'])
            measured = Hub(clone).updates()
            self.assertEqual(['devterm'], [row['id'] for row in measured['apps']])
            self.assertTrue(measured['platform']['available'])
            self.assertEqual(old, self.git(clone, 'rev-parse', 'HEAD'))

    def test_timeout_preserves_last_success_and_reports_failed_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'state').mkdir()
            (root / 'state/updates-fetch.json').write_text(json.dumps({'checkedAt': 'previous'}))
            with patch.object(updates.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 30)):
                result = updates.refresh(root)
            self.assertEqual('previous', result['checkedAt'])
            self.assertEqual('git fetch timed out', result['error'])
            self.assertTrue(result['attemptedAt'])
