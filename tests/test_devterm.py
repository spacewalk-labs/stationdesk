"""Exercise request contracts without starting a local server or ttyd."""
from email.message import Message
import importlib.util
import io
import json
import os
import shlex
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('devterm', Path(__file__).parents[1] / 'apps/devterm/backend/devterm-gate.py')
devterm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(devterm)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('OWNER', 'owner@example.test'), ('SECRETS', self.root / 'secrets')]:
            patcher = patch.object(devterm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, path='/secret-list', data=None, method='POST', owner='owner@example.test', origin='http://devterm.test', content_type='application/json'):
        handler = object.__new__(devterm.Handler)
        handler.path, handler.command = path, method
        handler.headers = Message()
        for name, value in [('Host', 'devterm.test'), ('Tailscale-User-Login', owner), ('Origin', origin), ('Content-Type', content_type)]:
            if value is not None:
                handler.headers[name] = value
        body = json.dumps({} if data is None else data).encode()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        result = []
        handler.reply = lambda code, value, *args: result.append((code, value))
        handler.dispatch()
        return result[0]

    def test_backend_rechecks_owner_and_origin(self):
        for owner in ('other@example.test', ''):
            self.assertEqual(403, self.request(owner=owner)[0])
        for origin in ('https://evil.example', None, 'http://devterm.test/path'):
            self.assertEqual(403, self.request(origin=origin)[0])
        self.assertEqual(200, self.request()[0])
        self.assertEqual(405, self.request(method='GET')[0])
        self.assertEqual(415, self.request(content_type='text/plain')[0])

    def test_values_stay_in_file_with_private_modes_and_metadata_only(self):
        value = 'unit-only-sensitive-payload'
        code, result = self.request('/secret-put', {'name': 'sample', 'value': value})
        self.assertEqual(200, code)
        self.assertNotIn(value, json.dumps(result))
        target = devterm.SECRETS / 'sample.txt'
        self.assertEqual(value, target.read_text())
        self.assertEqual(0o700, devterm.SECRETS.stat().st_mode & 0o777)
        self.assertEqual(0o600, target.stat().st_mode & 0o777)
        code, result = self.request()
        self.assertEqual('sample', result['secrets'][0]['name'])
        self.assertNotIn(value, json.dumps(result))
        self.assertEqual(200, self.request('/secret-del', {'name': 'sample'})[0])
        self.assertFalse(target.exists())

    def test_short_ttl_sweeps_on_list_and_put_without_following_symlink(self):
        with patch.object(devterm, 'SECRET_TTL', 1):
            self.request('/secret-put', {'name': 'old', 'value': 'expired'})
            target = devterm.SECRETS / 'old.txt'
            old = target.stat().st_mtime
            with patch.object(devterm.time, 'time', return_value=old + 2):
                self.assertEqual([], self.request()[1]['secrets'])
            self.assertFalse(target.exists())
            self.request('/secret-put', {'name': 'old', 'value': 'expired'})
            old = target.stat().st_mtime
            with patch.object(devterm.time, 'time', return_value=old + 2):
                self.request('/secret-put', {'name': 'new', 'value': 'fresh'})
            self.assertFalse(target.exists())
        outside = self.root / 'outside'
        outside.write_text('keep')
        (devterm.SECRETS / 'linked.txt').symlink_to(outside)
        self.request('/secret-put', {'name': 'linked', 'value': 'replacement'})
        self.assertEqual('keep', outside.read_text())
        self.assertFalse((devterm.SECRETS / 'linked.txt').is_symlink())

    def test_local_session_names_survive_create_and_delete(self):
        for name in ('my project', '한글 작업', '-leading'):
            with patch.object(devterm, 'tmux', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(200, self.request('/sessions', {'name': name})[0])
                self.assertEqual(name, run.call_args.args[3])
                self.assertEqual(200, self.request('/session-delete', {'name': name})[0])
                self.assertEqual(('kill-session', '-t', '=' + name), run.call_args.args)

    def test_account_session_launches_official_argv_without_a_prompt(self):
        for agent, params in [('claude', ['auth', 'login']), ('codex', ['login', '--device-auth']),
                              ('opencode', ['auth', 'login']), ('agy', [])]:
            with patch.object(devterm, 'tmux', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(200, self.request('/sessions', {'name': 'account-test', 'agent': agent,
                                                               'params': params, 'launch': True})[0])
                command = run.call_args.args[6]
                self.assertIn(shlex.join([agent, *params]) + '; exec', command)
                self.assertNotIn(' run ', command)

    def test_invalid_names_do_not_write_outside_drop(self):
        for name in ('../outside', '', '/absolute', 'space name'):
            self.assertEqual(400, self.request('/secret-put', {'name': name, 'value': 'x'})[0])
        self.assertEqual([], list(devterm.SECRETS.glob('*.txt')))

    def test_run_session_uses_cwd_and_quotes_prompt(self):
        prompt = 'Inspect files; $(touch unwanted) "hello"'
        with patch.object(devterm, 'tmux', return_value=SimpleNamespace(returncode=0)) as run:
            code, value = self.request('/sessions', {'name': 'card-test', 'cwd': str(self.root),
                                                    'prompt': prompt, 'params': ['--verbose']})
            self.assertEqual(200, code)
            self.assertEqual('card-test', value['name'])
            args = run.call_args.args
            self.assertEqual(('new-session', '-d', '-s', 'card-test', '-c', str(self.root)), args[:6])
            self.assertIn(shlex.join(['claude', '--verbose', prompt]), args[6])
        with patch.object(devterm, 'tmux') as run:
            self.assertEqual(400, self.request('/sessions', {'name': 'bad', 'cwd': '/missing/cwd'})[0])
            self.assertEqual(400, self.request('/sessions', {'name': 'bad', 'params': 'shell injection'})[0])
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
