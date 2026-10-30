"""Store and HTTP contracts without running a server or user services."""
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / 'apps/dev-monitor/backend'
sys.path.insert(0, str(BACKEND))


def load(name):
    spec = importlib.util.spec_from_file_location(name, BACKEND / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load('store')
with patch.dict(sys.modules, {'store': store, 'collectors': load('collectors')}):
    handler = load('handler')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app('hub', 'Hub', location='hub')
        self.app('hello', 'Example')
        self.app('dev-monitor', 'Monitor', location='apps/dev-monitor')
        self.store = store.Store(self.root)

    def app(self, app_id, label, location=None):
        directory = self.root / (location or 'examples/' + app_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'app.toml').write_text('label = ' + json.dumps(label) + '\nicon = "icon.svg"\n')
        (directory / 'icon.svg').write_text('<svg/>')

    def install(self, apps):
        (self.root / 'state').mkdir(exist_ok=True)
        (self.root / 'state/installed.json').write_text(json.dumps({'apps': apps}))

    def test_catalog_uses_apps_before_examples_and_installed_ledger(self):
        self.app('hello', 'Repository hello', location='apps/hello')
        (self.root / 'state').mkdir()
        (self.root / 'state/installed.json').write_text(json.dumps({'apps': {'hello': {'version': '1'}}}))
        apps = {app['id']: app for app in self.store.list()['apps']}
        self.assertEqual({'hello', 'hub', 'dev-monitor'}, set(apps))
        self.assertEqual('Repository hello', apps['hello']['label'])
        self.assertTrue(apps['hello']['installed'])
        self.assertFalse(apps['dev-monitor']['installed'])
        self.assertTrue(apps['dev-monitor']['required'])
        self.assertFalse(apps['hello']['required'])
        self.assertEqual('/monitor/api/store/icon?id=hello', apps['hello']['icon'])
        self.assertEqual('builtin', apps['hello']['origin'])
        self.assertEqual('builtin', apps['dev-monitor']['origin'])

    def test_external_apps_use_recorded_directory_and_origin(self):
        with tempfile.TemporaryDirectory() as source:
            directory = Path(source)
            (directory / 'app.toml').write_text('label = "External"\nicon = "icon.svg"\n')
            (directory / 'icon.svg').write_text('<svg/>')
            for origin in ('https://example.test/company.git', source):
                with self.subTest(origin=origin):
                    self.install({'external': {'dir': source, 'origin': origin}})
                    apps = {app['id']: app for app in self.store.list()['apps']}
                    self.assertEqual('External', apps['external']['label'])
                    self.assertEqual(origin, apps['external']['origin'])
                    self.assertTrue(apps['external']['installed'])
                    self.assertFalse(apps['external']['required'])
                    self.assertEqual(directory, self.store.manifest('external')[0])
                    with patch.object(store, 'command', return_value='') as command:
                        self.store.start('add', 'external')
                        self.assertEqual(['add', 'external'], command.call_args.args[0][-2:])

    def test_links_need_no_manifest_and_only_start_link_remove(self):
        link = {'kind': 'link', 'label': 'Example site', 'url': 'https://example.test/a?x=1#part',
                'origin': 'https://example.test/a?x=1#part', 'artifacts': []}
        self.install({'link-example': link})
        apps = {app['id']: app for app in self.store.list()['apps']}
        self.assertEqual(dict(id='link-example', label=link['label'], url=link['url'],
                              origin=link['origin'], icon='', kind='link',
                              installed=True, required=False), apps['link-example'])
        with patch.object(store, 'command', return_value='') as command:
            self.store.start('remove', 'link-example')
            self.assertEqual(['link', 'remove', 'link-example'], command.call_args.args[0][-3:])
        with patch.object(store, 'command') as command:
            with self.assertRaises(ValueError):
                self.store.start('add', 'link-example')
            command.assert_not_called()

    def test_runner_detaches_self_update_and_remove_in_systemd(self):
        for action, app_id in [('add', 'dev-monitor'), ('remove', 'hello'), ('remove', 'hub')]:
            with self.subTest(action=action, app_id=app_id), patch.object(store.subprocess, 'run', return_value=SimpleNamespace(stdout='')) as run:
                result = self.store.start(action, app_id)
                args = run.call_args.args[0]
                self.assertEqual(['systemd-run', '--user', '--collect'], args[:3])
                self.assertRegex(result['unit'], r'^stationdesk-store-[a-f0-9]{32}\.service$')
                self.assertIn('--property=Type=oneshot', args)
                self.assertIn('--no-block', args)
                self.assertIn('--unit=' + result['unit'], args)
                self.assertIn('--setenv=STATIONDESK_ROOT=' + str(self.root), args)
                self.assertEqual([str(self.root / 'bin/stationdesk'), action, app_id], args[-3:])
                self.assertNotIn('--wait', args)
                self.assertNotIn('--pipe', args)

    def test_addon_source_is_passed_unchanged_to_cli_without_copying(self):
        for source in ('https://example.test/owner/repo.git', '/owner/My tools/app'):
            with self.subTest(source=source), patch.object(store, 'command', return_value='') as command:
                result = self.store.add_source(source)
                self.assertEqual([str(self.root / 'bin/stationdesk'), 'add', source], command.call_args.args[0][-3:])
                self.assertTrue(result['unit'].startswith('stationdesk-store-'))
        self.assertFalse((self.root / 'state/personal-apps.json').exists())

    def test_native_ordering_queues_behind_existing_store_units(self):
        earlier = 'stationdesk-store-' + 'b' * 32 + '.service'
        with patch.object(store, 'command', side_effect=[earlier + ' loaded activating start CLI\n', '']) as command:
            self.store.start('add', 'hello')
            self.assertIn('--property=After=' + earlier, command.call_args.args[0])

    def test_invalid_requests_never_start_systemd(self):
        for app_id in ('../hello', '', '/hello', 'HELLO', None, ['hello']):
            with self.subTest(app_id=app_id), patch.object(store.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    self.store.start('add', app_id)
                run.assert_not_called()
        for action, app_id in [('execute', 'hello'), ('add', 'missing')]:
            with self.subTest(action=action, app_id=app_id), patch.object(store.subprocess, 'run') as run:
                with self.assertRaises((ValueError, KeyError)):
                    self.store.start(action, app_id)
                run.assert_not_called()

    def test_failure_progress_keeps_last_app_lines_and_scrubs_credentials(self):
        unit = 'stationdesk-store-' + 'a' * 32 + '.service'
        records = [{'MESSAGE': 'Cloning into source...'}]
        records += [{'MESSAGE': f'download {i}'} for i in range(10)]
        records += [{'MESSAGE': 'API_TOKEN=private https://user:pass@example.test/'},
                    {'MESSAGE': 'Docker 필요 — sudo apt-get install -y docker.io'},
                    {'MESSAGE': 'Main process exited', 'SYSLOG_IDENTIFIER': 'systemd'},
                    {'MESSAGE': 'Failed', 'UNIT_RESULT': 'exit-code'}]
        with patch.object(store, 'command', side_effect=['ActiveState=failed\nResult=exit-code\n', '\n'.join(map(json.dumps, records))]):
            progress = self.store.progress(unit)
        self.assertFalse(progress['active'])
        self.assertEqual('exit-code', progress['result'])
        self.assertIn('Docker 필요 — sudo apt-get install -y docker.io', progress['output'])
        for hidden in ['Cloning', 'private', 'user:pass', 'Main process exited']:
            self.assertNotIn(hidden, progress['output'])

    def test_progress_reads_native_journal_after_unit_is_collected(self):
        unit = 'stationdesk-store-' + 'a' * 32 + '.service'
        records = [{'MESSAGE': 'Installed hello'}, {'MESSAGE': 'Finished', 'JOB_TYPE': 'start', 'JOB_RESULT': 'done'}]
        with patch.object(store, 'command', side_effect=['ActiveState=inactive\nResult=\n', '\n'.join(map(json.dumps, records))]) as command:
            self.assertEqual({'active': False, 'result': 'success', 'output': 'Installed hello'}, self.store.progress(unit))
            self.assertEqual(['journalctl', '--user', '-u', unit, '--no-pager', '-o', 'json'], command.call_args.args[0])
        with patch.object(store, 'command', side_effect=['ActiveState=inactive\nResult=success\n', '']):
            self.assertTrue(self.store.progress(unit)['active'])
        with patch.object(store, 'command', side_effect=['ActiveState=active\nResult=success\n', '']):
            self.assertTrue(self.store.progress(unit)['active'])
        with patch.object(store, 'command', side_effect=['ActiveState=failed\nResult=exit-code\n', json.dumps({'UNIT_RESULT': 'exit-code', 'MESSAGE': 'Failed'})]):
            self.assertEqual('exit-code', self.store.progress(unit)['result'])
        with patch.object(store, 'command') as command:
            with self.assertRaises(ValueError):
                self.store.progress('unrelated.service')
            command.assert_not_called()


class HandlerTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'STATIONDESK_OWNER': 'owner@example.test', 'STATIONDESK_ROOT': '/unused'})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('STATIONDESK_FQDN', None)

    def request(self, body=b'{"action":"add","id":"hello"}', owner='owner@example.test', origin='http://desk.test', path='/api/store', method='POST', content_type='application/json'):
        request = object.__new__(handler.Handler)
        request.path = path
        request.headers = Message()
        for name, value in [('Host', 'desk.test'), ('Tailscale-User-Login', owner), ('Origin', origin), ('Content-Type', content_type), ('Content-Length', str(len(body)))]:
            if value is not None:
                request.headers[name] = value
        request.rfile = io.BytesIO(body)
        replies = []
        request.reply = lambda *args: replies.append(args)
        getattr(request, 'do_' + method)()
        return replies[0]

    def test_posts_require_owner_and_exact_origin_before_store_action(self):
        with patch.object(handler.Store, 'start', return_value={'unit': 'test'}) as start:
            for owner in (None, '', 'other@example.test'):
                self.assertEqual(403, self.request(owner=owner)[0])
            for origin in (None, 'https://desk.test', 'http://desk.test/', 'http://desk.test.evil', 'http://evil.test'):
                self.assertEqual(403, self.request(origin=origin)[0])
            start.assert_not_called()
            self.assertEqual((202, {'unit': 'test'}), self.request())
            start.assert_called_once_with('add', 'hello')

    def test_tls_termination_uses_public_fqdn_origin(self):
        with patch.dict(os.environ, {'STATIONDESK_FQDN': 'desk.tail.test'}), patch.object(handler.Store, 'start', return_value={'unit': 'test'}) as start:
            self.assertEqual(403, self.request(origin='http://desk.test')[0])
            self.assertEqual(403, self.request(origin='https://desk.tail.test/path')[0])
            self.assertEqual(202, self.request(origin='https://desk.tail.test')[0])
            start.assert_called_once()

    def test_bad_body_returns_400_without_starting(self):
        with patch.object(handler.Store, 'start') as start:
            for body in (b'{', b'null', b'[]', b'"hello"', b'{}', b'{"id":"hello"}'):
                with self.subTest(body=body):
                    self.assertEqual(400, self.request(body=body)[0])
            self.assertEqual(400, self.request(content_type='text/plain')[0])
            start.assert_not_called()

    def test_link_validation_returns_readable_400_without_enqueueing(self):
        name_error = '링크 이름을 입력해 주세요. 줄바꿈이나 제어 문자는 사용할 수 없습니다.'
        url_error = '공백 없는 https:// 주소를 입력해 주세요. 사용자 이름이나 비밀번호는 주소에 넣을 수 없습니다.'
        address_error = '링크 주소를 확인해 주세요. 호스트와 포트 번호가 올바르지 않습니다.'
        cases = [(None, 'https://example.test', name_error),
                 (' ', 'https://example.test', name_error),
                 ('Docs\n', 'https://example.test', name_error),
                 ('Docs', None, url_error),
                 ('Docs', 'http://example.test', url_error),
                 ('Docs', 'https://user:password@example.test', url_error),
                 ('Docs', 'https://example.test/a b', url_error),
                 ('Docs', 'https://[broken', address_error),
                 ('Docs', 'https://example.test:wrong', address_error),
                 ('Docs', 'https://example.test:70000', address_error)]
        with patch.object(handler.Hub, '__init__', return_value=None), patch.object(handler.Store, 'enqueue') as enqueue:
            for name, url, message in cases:
                with self.subTest(name=name, url=url):
                    body = json.dumps(dict(name=name, url=url)).encode()
                    self.assertEqual((400, {'error': message}), self.request(body=body, path='/api/owner/apps/links/add'))
            enqueue.assert_not_called()

    def test_health_rechecks_owner(self):
        self.assertEqual(403, self.request(method='GET', path='/api/health', owner=None)[0])
        self.assertEqual((200, {'ok': True}), self.request(method='GET', path='/api/health'))
        with patch.dict(os.environ, {'STATIONDESK_OWNER': ''}):
            self.assertEqual(403, self.request(method='GET', path='/api/health')[0])


if __name__ == '__main__':
    unittest.main()
