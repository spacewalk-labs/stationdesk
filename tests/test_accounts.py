"""Only vendor CLI results drive account state; raw output never leaves the backend."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'apps/dev-monitor/backend'))

spec = importlib.util.spec_from_file_location('accounts', Path(__file__).parents[1] / 'apps/dev-monitor/backend/accounts.py')
accounts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accounts)


class AccountsTests(unittest.TestCase):
    def test_status_recognizes_logout_and_hides_output(self):
        samples = [('claude', '{"loggedIn":false,"email":"private"}', 1),
                   ('codex', 'Not logged in', 1),
                   ('opencode', '└  0 credentials', 0)]
        with patch.object(accounts, 'executable', return_value='/vendor/cli'):
            for agent, output, code in samples:
                with patch.object(accounts.subprocess, 'run', return_value=SimpleNamespace(stdout=output, stderr='', returncode=code)):
                    row = accounts.status_one(agent)
                    self.assertIs(row['logged_in'], False)
                    self.assertEqual(row['status'], 'logged_out')
                    self.assertNotIn('private', str(row))
            with patch.object(accounts.subprocess, 'run') as run:
                self.assertEqual('unsupported', accounts.status_one('agy')['status'])
                run.assert_not_called()

    def test_cli_failure_is_unknown_and_logout_drives_red_ring(self):
        with patch.object(accounts, 'executable', return_value='/vendor/cli'), patch.object(accounts.subprocess, 'run', return_value=SimpleNamespace(stdout='bad output', stderr='', returncode=1)):
            self.assertIsNone(accounts.status_one('codex')['logged_in'])
        with tempfile.TemporaryDirectory() as directory:
            current = dict.fromkeys(accounts.COMMANDS, False)
            def row(agent):
                return dict(id=agent, logged_in=current[agent])
            with patch.object(accounts, 'status_one', side_effect=row):
                # Unknown / unsupported on a fresh install also stays stationary.
                current['agy'] = None
                first = accounts.status(directory)
                self.assertEqual('ok', first['status'])
                self.assertTrue(first['needs_login'])
                current['codex'] = True
                signed_in = accounts.status(directory)
                self.assertEqual('ok', signed_in['status'])
                self.assertFalse(signed_in['needs_login'])
                self.assertEqual({'logged_in': ['codex']}, json.loads((Path(directory) / 'state/accounts.json').read_text()))
                current['codex'] = False
                for _ in range(2):
                    self.assertEqual('critical', accounts.status(directory)['status'])
                current['codex'] = None
                self.assertEqual('warning', accounts.status(directory)['status'])
                current['codex'] = True
                self.assertEqual('ok', accounts.status(directory)['status'])

    def test_login_only_creates_official_devterm_session(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'apps/devterm'
            app.mkdir(parents=True)
            (app / 'app.toml').write_text('port=19923\n')
            with patch.object(accounts, 'executable', return_value='/vendor/cli'), patch.object(accounts.urllib.request, 'urlopen') as urlopen:
                response = urlopen.return_value.__enter__.return_value
                response.read.return_value = b'{"ok":true}'
                for agent, expected in [('claude',['auth','login']),('codex',['login','--device-auth']),('opencode',['auth','login']),('agy',[])]:
                    result = accounts.action(agent, 'login', root)
                    payload = json.loads(urlopen.call_args.args[0].data)
                    self.assertEqual(expected, payload['params'])
                    self.assertTrue(payload['launch'])
                    self.assertTrue(result['url'].startswith('/devterm/?session=account-'))
