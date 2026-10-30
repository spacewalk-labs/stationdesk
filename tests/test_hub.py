"""Launcher persistence and the original owner-facing store/run contracts."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'apps/dev-monitor/backend'))
from hub import Hub
from cards import Cards


class HubTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'state').mkdir()
        (self.root / 'web/hub').mkdir(parents=True)
        (self.root / 'state/installed.json').write_text(json.dumps({'apps': {'hub': {}, 'devterm': {}, 'fileview': {}}}))
        (self.root / 'web/hub/__stationdesk-hub.json').write_text(json.dumps({'product': 'StationDesk', 'apps': {'devterm': {'tile': {'label': 'Terminal'}}}}))
        self.hub = Hub(self.root)

    def test_smoke_counts_apps_with_personal_and_company_links(self):
        binary = self.root / 'bin/curl'
        binary.parent.mkdir()
        binary.write_text(f'#!{sys.executable}\nimport os,sys\n'
                          'print(os.environ["FIXTURE_TILES"] if sys.argv[-1].endswith(".json") else "owner")\n')
        binary.chmod(0o755)
        smoke = Path(__file__).parents[1] / 'hub/smoke.sh'
        for links in (False, True):
            for missing_app in (False, True):
                with self.subTest(links=links, missing_app=missing_app):
                    apps = {'hub': {'kind': 'app'}, 'publish': {'kind': 'app'}}
                    tiles = [{'id': app, 'kind': 'app'} for app in apps]
                    if links:
                        apps['personal'] = {'kind': 'link'}
                        tiles.extend([{'id': 'personal', 'kind': 'link'},
                                      {'id': 'company', 'kind': 'link'}])
                    if missing_app:
                        tiles.pop(0)
                    (self.root / 'state/installed.json').write_text(json.dumps({'apps': apps}))
                    result = subprocess.run(['bash', str(smoke)], capture_output=True,
                        env=os.environ | {'PATH': str(binary.parent) + ':' + os.environ['PATH'],
                            'STATIONDESK_PORT': '19920', 'STATIONDESK_OWNER': 'owner@example.test',
                            'STATIONDESK_STATE': str(self.root / 'state'),
                            'FIXTURE_TILES': json.dumps(tiles)})
                    self.assertEqual(1 if missing_app else 0, result.returncode, result.stderr)

    def test_edit_preserves_unseen_apps_and_named_lines_across_instances(self):
        self.hub.order(['fileview', {'line': 'Tools'}, 'devterm'])
        result = Hub(self.root).order([{'line': 'Work'}, 'devterm'])
        self.assertEqual([{'line': 'Work'}, 'devterm', 'fileview'], result['order'])
        self.assertNotIn('hub', result['installed'])
        self.assertEqual(['dev-monitor', 'devterm', 'fileview', 'hub', 'publish'], result['required'])
        self.assertEqual(result, Hub(self.root).order())

    def test_link_mutations_delegate_to_the_existing_cli_queue(self):
        with patch.object(self.hub.store, 'enqueue', return_value={'unit':'fixture'}) as enqueue:
            result = self.hub.add_link('Docs', 'https://example.test/docs')
        self.assertEqual('fixture', result['run_id'])
        enqueue.assert_called_once_with(['link', 'add', 'Docs', 'https://example.test/docs'])
        ledger = json.loads((self.root / 'state/installed.json').read_text())
        ledger['apps'][result['id']] = dict(kind='link', label='Docs', url='https://example.test/docs')
        (self.root / 'state/installed.json').write_text(json.dumps(ledger))
        with patch.object(self.hub.store, 'start', return_value={'unit':'remove'}) as remove:
            self.assertEqual('remove', self.hub.remove_link(result['id'])['run_id'])
        remove.assert_called_once_with('remove', result['id'])

    def test_link_rejects_credentials_and_http_without_writing(self):
        for url in ['http://example.test', 'https://user:password@example.test', 'https://example.test/a b']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.hub.add_link('Docs', url)
        self.assertEqual({}, self.hub.links())

    def test_bundled_apps_and_examples_are_builtin_and_links_have_own_category(self):
        for directory in ('hub', 'apps/devterm', 'examples/hello'):
            target = self.root / directory
            target.mkdir(parents=True)
            (target / 'app.toml').write_text('label="App"\nicon="icon.svg"\nport=19940\n')
        link = 'link-fixture'
        ledger = json.loads((self.root / 'state/installed.json').read_text())
        ledger['apps'][link] = dict(kind='link', label='Docs', url='https://example.test/docs')
        (self.root / 'state/installed.json').write_text(json.dumps(ledger))
        with patch.object(Hub, 'updates', return_value={'apps': [], 'platform': {'available': None}}):
            rows = {row['id']: row for row in self.hub.apps()['rows']}
        self.assertEqual('builtin', rows['devterm']['origin'])
        # The hub is the platform row, and an example stays out of the store until installed.
        self.assertNotIn('hub', rows)
        self.assertNotIn('hello', rows)
        ledger['apps']['hello'] = dict(dir=str(self.root / 'examples/hello'))
        (self.root / 'state/installed.json').write_text(json.dumps(ledger))
        with patch.object(Hub, 'updates', return_value={'apps': [], 'platform': {'available': None}}):
            rows = {row['id']: row for row in self.hub.apps()['rows']}
        self.assertEqual('builtin', rows['hello']['origin'])
        self.assertEqual('StationDesk', rows['hello']['source'])
        self.assertEqual('link', rows[link]['origin'])
        self.assertFalse((self.root / 'state/personal-apps.json').exists())

    def test_run_note_reaches_terminal_and_window_measures_actual_session(self):
        cards = Cards(self.root)
        cards.ingest(dict(id='task', group='task', source='test', level='normal', title='Run me', run=dict(cwd='/tmp', prompt='Fix it')))
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true}')
        with patch.dict(os.environ, {'STATIONDESK_PORT_DEVTERM': '19930'}), patch('urllib.request.urlopen', return_value=response) as request:
            result = cards.run('task', {}, 'Keep the public API')
            prompt = json.loads(request.call_args.args[0].data)['prompt']
            self.assertIn('Owner note: Keep the public API', prompt)
            self.assertEqual({'id': result['ran_window'], 'name': result['ran_window']}, result['session'])
            response.__enter__.return_value = io.BytesIO(json.dumps({'sessions': [{'name': result['session']['name']}]}).encode())
            self.assertEqual('active', cards.window('task')['state'])
            response.__enter__.return_value = io.BytesIO(b'{"sessions":[]}')
            self.assertEqual('ended', cards.window('task')['state'])
