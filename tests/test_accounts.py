"""Only vendor CLI results drive account state; raw output never leaves the backend."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import os
import shutil
import subprocess
import tempfile
import time
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

    def test_signed_in_status_carries_only_named_fields(self):
        samples = [('claude', json.dumps(dict(loggedIn=True, email='owner@example.com', subscriptionType='max',
                                              orgId='secret-org', configDirectory='/home/owner/.claude')), 0,
                    dict(plan='Max', email='owner@example.com')),
                   ('codex', 'Logged in using an API key - sk-proj-***abcd', 0, dict(method='API key')),
                   ('codex', 'Logged in using ChatGPT', 0, dict(method='ChatGPT')),
                   ('opencode', '┌  Credentials ~/.local/share/opencode/auth.json\n│\n●  OpenAI \x1b[90mapi\n│\n●  Anthropic \x1b[90mapi\n│\n└  2 credentials\n\n'
                                '┌  Environment\n│\n●  Google \x1b[90mGEMINI_API_KEY\n│\n└  1 environment variable', 0,
                    dict(credentials=2, providers=['OpenAI', 'Anthropic']))]
        with patch.object(accounts, 'executable', return_value='/vendor/cli'):
            for agent, output, code, expected in samples:
                with self.subTest(agent=agent), patch.object(accounts.subprocess, 'run', return_value=SimpleNamespace(stdout=output, stderr='', returncode=code)):
                    row = accounts.status_one(agent)
                    self.assertEqual('logged_in', row['status'])
                    self.assertEqual(expected, {key: row[key] for key in expected})
                    self.assertEqual({'id', 'installed', 'logged_in', 'status', *expected}, set(row))
                    for leaked in ('secret-org', '/home/owner', 'sk-proj'):
                        self.assertNotIn(leaked, str(row))

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
                self.assertEqual(['codex'], signed_in['known'])
                current['codex'] = False
                for _ in range(2):
                    self.assertEqual('critical', accounts.status(directory)['status'])
                current['codex'] = None
                self.assertEqual('warning', accounts.status(directory)['status'])
                current['codex'] = True
                self.assertEqual('ok', accounts.status(directory)['status'])

    @unittest.skipIf(shutil.which('tmux') is None, 'tmux is not installed')
    def test_sign_in_reads_link_and_hands_input_through_tmux(self):
        # Fake CLIs print what the real ones printed on 2026-10-30, then record their input.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = {
                'claude': 'echo "If the browser didn\'t open, visit: https://claude.com/cai/oauth/authorize?code=true&state=x"; '
                          'printf "Paste code here if prompted > "; read code; echo "$code" > got',
                'codex': 'echo "   https://auth.openai.com/codex/device"; echo "2. Enter this one-time code (expires in 15 minutes)"; '
                         'echo "   PEFB-WHRWW"; sleep 30',
                'opencode': 'echo "$@" > args; echo "◆  Enter your API key"; read key; echo "$key" > got',
                'agy': 'echo " Select login method:"; read pick; echo " https://accounts.google.com/o/oauth2/auth?x=1"; '
                       'echo " paste the authorization code below:"; read code; echo "$code" > got; '
                       '[ "$code" = bad ] && exit 1; clear; echo " Signed in"; sleep 30',
            }
            for agent, body in scripts.items():
                (root / agent).write_text('#!/bin/sh\ncd "%s"\n%s\n' % (root, body))
                (root / agent).chmod(0o755)
            socket = 'stationdesk-test-%d' % os.getpid()
            try:
                with patch.object(accounts, 'TMUX_SOCKET', socket), \
                        patch.object(accounts, 'executable', side_effect=lambda agent: str(root / agent)):
                    flow = accounts.action('claude', 'login', root)
                    self.assertEqual(dict(ok=True, url='https://claude.com/cai/oauth/authorize?code=true&state=x', code=None, input='code'), flow)
                    accounts.action('claude', 'submit', root, 'abc#def-123')
                    self.assertEqual('abc#def-123', self.wait_for(root / 'got'))
                    with self.assertRaises(ValueError):
                        accounts.action('claude', 'submit', root, 'two\nlines')

                    flow = accounts.action('codex', 'login', root)
                    self.assertEqual(('https://auth.openai.com/codex/device', 'PEFB-WHRWW', None), (flow['url'], flow['code'], flow['input']))
                    self.assertTrue(accounts.action('codex', 'check', root)['running'])
                    accounts.action('codex', 'cancel', root)
                    self.assertFalse(accounts.action('codex', 'check', root)['running'])

                    (root / 'got').unlink()
                    with self.assertRaises(ValueError):
                        accounts.action('opencode', 'login', root, '--provider=x;rm')
                    self.assertEqual('key', accounts.action('opencode', 'login', root, 'openai')['input'])
                    self.assertEqual('auth login -p openai -m Manually enter API Key', self.wait_for(root / 'args'))
                    accounts.action('opencode', 'submit', root, 'sk-secret')
                    self.assertEqual('sk-secret', self.wait_for(root / 'got'))

                    (root / 'got').unlink()
                    flow = accounts.action('agy', 'login', root)
                    self.assertEqual(('https://accounts.google.com/o/oauth2/auth?x=1', 'code'), (flow['url'], flow['input']))
                    self.assertTrue(accounts.action('agy', 'check', root)['waiting'])
                    accounts.action('agy', 'submit', root, '4/0Ab-code')
                    self.assertEqual('4/0Ab-code', self.wait_for(root / 'got'))
                    time.sleep(0.5)
                    # Signed in: still running, past the prompt (and the session is let go).
                    self.assertEqual(dict(ok=True, running=True, waiting=False), accounts.action('agy', 'check', root))
                    self.assertFalse(accounts.action('agy', 'check', root)['running'])

                    # A rejected code ends agy: that is not running, never "signed in".
                    (root / 'got').unlink()
                    accounts.action('agy', 'login', root)
                    accounts.action('agy', 'submit', root, 'bad')
                    self.wait_for(root / 'got')
                    time.sleep(0.5)
                    self.assertEqual(dict(ok=True, running=False, waiting=False), accounts.action('agy', 'check', root))
            finally:
                subprocess.run(['tmux', '-L', socket, 'kill-server'], capture_output=True)

    def wait_for(self, path):
        for _ in range(50):
            if path.exists() and path.read_text().strip():
                return path.read_text().strip()
            time.sleep(0.1)
        self.fail(f'{path.name} was never written')

    def test_logout_runs_the_official_command_and_needs_a_provider_for_opencode(self):
        with patch.object(accounts, 'executable', return_value='/vendor/cli'), \
                patch.object(accounts.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            accounts.action('claude', 'logout', '/root')
            self.assertEqual(['/vendor/cli', 'auth', 'logout'], run.call_args.args[0])
            accounts.action('opencode', 'logout', '/root', 'OpenAI')
            self.assertEqual(['/vendor/cli', 'auth', 'logout', 'OpenAI'], run.call_args.args[0])
            with self.assertRaises(ValueError):
                accounts.action('opencode', 'logout', '/root')
        with self.assertRaises(ValueError):
            accounts.action('claude', 'install', '/root')
