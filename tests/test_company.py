"""Company apps are bundles from a distribution place; a file:// directory stands in for it."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tomllib
import unittest
from unittest.mock import patch

import test_engine
from test_engine import engine
sys.path.insert(0, str(Path(__file__).parents[1] / 'apps/dev-monitor/backend'))
from hub import Hub


class CompanyTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    fixture = test_engine.EngineTests.fixture
    command = test_engine.EngineTests.command

    def place(self):
        place = Path(self.temporary.name) / 'dist'
        (place / 'icons').mkdir(parents=True, exist_ok=True)
        return place

    def publish(self, app_id, version, content='hello', links=(), extra=''):
        """Write <id>-<version>.tar.gz and an index.json naming it, like scripts/build.py."""
        place = self.place()
        app = self.fixture(app_id, extra)
        (app / 'web/index.html').write_text(content)
        archive = place / f'{app_id}-{version}.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            bundle.add(app, arcname='.')
        self.engine.safe_remove(app)
        (place / f'icons/{app_id}.svg').write_text('<svg/>')
        index = json.loads((place / 'index.json').read_text()) if (place / 'index.json').exists() else {'apps': [], 'links': []}
        index['apps'] = [row for row in index['apps'] if row['id'] != app_id] + [{
            'id': app_id, 'label': app_id.title(), 'description': 'fixture app',
            'icon': f'icons/{app_id}.svg', 'version': version,
            'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}]
        if links:
            index['links'] = list(links)
        (place / 'index.json').write_text(json.dumps(index))
        return place.as_uri() + '/'

    def rows(self):
        with patch.object(Hub, 'updates', return_value={
                'apps': [], 'platform': {'available': False}}):
            response = Hub(self.root).apps()
        return response, {row['id']: row for row in response['rows']}

    def upgrades(self):
        # No platform upstream in the fixture: only the company rows matter here.
        with patch('hub.subprocess.run', return_value=engine.subprocess.CompletedProcess([], 1, '', '')):
            return {row['id'] for row in Hub(self.root).updates()['apps']}

    def test_connect_install_links_disconnect_and_remove(self):
        url = self.publish('inline-app', 'v1', links=[
            {'id': 'docs', 'name': 'Company docs', 'url': 'https://example.test/company'}])
        box = self.root / 'state/box.toml'
        box.write_text('owner = "owner@example.test"\nfqdn = "desk.example.test"\n[preferences]\ncompact = true\n')
        response, _ = self.rows()
        self.assertFalse(response.get('company_enabled', False))
        self.engine.company(url.rstrip('/'))
        self.assertEqual(url, tomllib.loads(box.read_text())['company'])
        response, rows = self.rows()
        self.assertTrue(response['company_enabled'])
        for app_id in ('inline-app', 'docs'):
            self.assertEqual('company', rows[app_id]['origin'])
            self.assertEqual('install', rows[app_id]['state'])
        # Index rows carry what the tab shows before anything is downloaded.
        self.assertEqual('Inline-App', rows['inline-app']['name'])
        self.assertEqual('fixture app', rows['inline-app']['desc'])
        self.assertEqual(url, rows['inline-app']['source'])
        self.assertEqual('/monitor/api/store/icon?id=inline-app', rows['inline-app']['icon'])
        self.assertFalse((self.root / 'company/apps').exists())
        self.engine.add('company:inline-app')
        entry = self.engine.load()['apps']['inline-app']
        self.assertEqual(('company', 'v1', None), (entry['origin'], entry['version'], entry['repo']))
        self.assertEqual(self.root / 'company/apps/inline-app-v1', Path(entry['dir']))
        self.assertEqual('hello', (self.root / 'web/inline-app/index.html').read_text())
        self.assertEqual('installed', self.rows()[1]['inline-app']['state'])
        self.engine.company_link('docs', True)
        self.assertEqual('installed', self.rows()[1]['docs']['state'])
        tiles = json.loads((self.root / 'web/hub/__stationdesk.json').read_text())
        self.assertTrue(any(t.get('url') == 'https://example.test/company' for t in tiles))
        # Link visibility is personal state rather than an installed app.
        self.assertNotIn('docs', self.engine.load()['apps'])
        self.engine.company_link('docs', False)
        self.assertEqual('install', self.rows()[1]['docs']['state'])
        self.engine.company()
        facts = tomllib.loads(box.read_text())
        self.assertNotIn('company', facts)
        self.assertEqual('desk.example.test', facts['fqdn'])
        self.assertEqual({'compact': True}, facts['preferences'])
        response, rows = self.rows()
        self.assertFalse(response['company_enabled'])
        self.assertNotIn('docs', rows)
        self.assertEqual('addon', rows['inline-app']['origin'])
        self.engine.remove('inline-app')
        self.assertEqual({}, self.engine.load()['apps'])
        self.assertFalse((self.root / 'company/apps/inline-app-v1').exists())

    def test_new_bundle_version_is_an_update_and_replaces_the_old_folder(self):
        url = self.publish('inline-app', 'v1')
        self.engine.company(url)
        self.engine.add('company:inline-app')
        self.assertEqual(set(), self.upgrades())
        self.publish('inline-app', 'v2', 'company updated', links=[
            {'id': 'docs', 'name': 'Updated docs', 'url': 'https://example.test/new'}])
        self.engine.company_fetch()
        self.assertEqual({'inline-app'}, self.upgrades())
        # Re-adding reinstalls the unpacked bundle; update moves to the new version.
        self.engine.add('inline-app')
        self.assertEqual('v1', self.engine.load()['apps']['inline-app']['version'])
        self.assertEqual(0, self.engine.update('inline-app'))
        entry = self.engine.load()['apps']['inline-app']
        self.assertEqual('v2', entry['version'])
        self.assertEqual('company updated', (self.root / 'web/inline-app/index.html').read_text())
        self.assertFalse((self.root / 'company/apps/inline-app-v1').exists())
        self.assertEqual(set(), self.upgrades())
        self.assertEqual('Updated docs', self.rows()[1]['docs']['name'])

    def test_update_fetches_the_index_and_installs_the_new_bundle(self):
        url = self.publish('inline-app', 'v1')
        self.engine.company(url)
        self.engine.add('company:inline-app')
        self.publish('inline-app', 'v2', 'company updated')
        self.assertEqual(0, self.engine.update())
        self.assertEqual('v2', self.engine.load()['apps']['inline-app']['version'])
        self.assertEqual('company updated', (self.root / 'web/inline-app/index.html').read_text())

    def test_legacy_skill_link_follows_versioned_company_update(self):
        path = self.home / '.agents/skills/share-docs'
        original_fixture = self.fixture

        def fixture(app_id='hello', extra=''):
            app = original_fixture(app_id, extra)
            (app / 'share-docs').mkdir()
            return app

        self.fixture = fixture
        self.engine.company(self.publish('inline-app', 'v1', extra=f'files = ["{path}"]\n'))
        self.engine.add('company:inline-app')
        old = Path(self.engine.load()['apps']['inline-app']['dir'])
        path.parent.mkdir(parents=True)
        path.symlink_to(old / 'share-docs')
        self.publish('inline-app', 'v2', extra=f'files = [{{path = "{path}", target = "share-docs"}}]\n')
        self.assertEqual(0, self.engine.update('inline-app'))
        current = Path(self.engine.load()['apps']['inline-app']['dir'])
        self.assertNotEqual(old, current)
        self.assertFalse(old.exists())
        self.assertEqual(current / 'share-docs', path.readlink())
        self.assertTrue(path.is_dir())
        self.engine.remove('inline-app')
        self.assertFalse(path.is_symlink())

    def test_selected_builtin_update_leaves_company_apps_and_index_alone(self):
        url = self.publish('inline-app', 'v1')
        self.engine.company(url)
        self.engine.add('hub')
        self.engine.add('company:inline-app')
        self.publish('inline-app', 'v2', 'company updated')
        self.assertEqual(0, self.engine.update('hub'))
        self.assertEqual('v1', self.engine.load()['apps']['inline-app']['version'])
        self.assertEqual('v1', json.loads((self.root / 'company/index.json').read_text())['apps'][0]['version'])

    def test_install_after_a_new_build_uses_the_new_index(self):
        url = self.publish('inline-app', 'v1')
        self.engine.company(url)
        # The place rebuilds and drops v1 before this box's next daily check.
        self.publish('inline-app', 'v2', 'company updated')
        (self.place() / 'inline-app-v1.tar.gz').unlink()
        self.engine.add('company:inline-app')
        self.assertEqual('v2', self.engine.load()['apps']['inline-app']['version'])

    def test_daily_check_refreshes_the_index_when_git_fetch_fails(self):
        import updates
        self.engine.company(self.publish('inline-app', 'v1'))
        self.publish('inline-app', 'v2')
        failed = engine.subprocess.CompletedProcess([], 128, b'', b'')
        with patch('updates.subprocess.run', return_value=failed):
            snapshot = updates.refresh(self.root)
        self.assertIn('git fetch failed', snapshot['error'])
        self.assertEqual('v2', json.loads((self.root / 'company/index.json').read_text())['apps'][0]['version'])

    def test_sha256_mismatch_installs_nothing(self):
        url = self.publish('inline-app', 'v1')
        self.engine.company(url)
        archive = self.place() / 'inline-app-v1.tar.gz'
        archive.write_bytes(archive.read_bytes() + b'tampered')
        with self.assertRaisesRegex(RuntimeError, 'sha256'):
            self.engine.company_add('inline-app')
        self.assertNotIn('inline-app', self.engine.load()['apps'])
        self.assertEqual([], list((self.root / 'company/apps').iterdir()))

    def test_bundle_keeps_absolute_symlinks_of_a_prebuilt_venv(self):
        app = self.fixture('venv-app')
        (app / 'venv/bin').mkdir(parents=True)
        (app / 'venv/bin/python3').symlink_to(sys.executable)
        (app / 'venv/bin/python').symlink_to('python3')
        archive = self.place() / 'venv-app-v1.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            bundle.add(app, arcname='.')
        self.engine.safe_remove(app)
        (self.place() / 'index.json').write_text(json.dumps({'apps': [{
            'id': 'venv-app', 'label': 'Venv', 'version': 'v1',
            'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}]}))
        self.engine.company(self.place().as_uri())
        self.engine.add('company:venv-app')
        python = self.root / 'company/apps/venv-app-v1/venv/bin/python'
        self.assertEqual(Path(sys.executable).resolve(), python.resolve())

    def test_company_link_projects_catalog_icon_and_badge_without_remote_images(self):
        image = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
        (self.place() / 'icons/docs.svg').write_text(image)
        url = self.publish('inline-app', 'v1', links=[{
            'id': 'docs', 'name': 'Company docs', 'url': 'https://example.test/company',
            'icon': 'icons/docs.svg', 'glyph': 'app-chat',
            'badge_url': 'https://example.test/inquiries/api/badge'}])
        self.engine.company(url)
        icon_url = '/assets/company-links/docs.svg'
        self.assertEqual(icon_url, self.rows()[1]['docs']['icon'])
        self.assertEqual('app-chat', self.rows()[1]['docs']['glyph'])
        # Store icons are available before the link is placed on Home.
        self.assertEqual(image, (self.root / ('web/hub' + icon_url)).read_text())
        self.engine.company_link('docs', True)
        projection = json.loads((self.root / 'web/hub/__stationdesk-hub.json').read_text())
        tile = projection['apps']['docs']['tile']
        self.assertEqual(icon_url, tile['icon'])
        self.assertEqual('app-chat', tile['glyph'])
        self.assertEqual('https://example.test/inquiries/api/badge', tile['badge_url'])

    def test_failed_connection_reports_reason_and_preserves_box(self):
        box = self.root / 'state/box.toml'
        before = box.read_bytes()
        missing = (Path(self.temporary.name) / 'missing').as_uri()
        with self.assertRaisesRegex(RuntimeError, '회사 배포 장소'):
            self.engine.company(missing)
        self.assertEqual(before, box.read_bytes())
        self.assertFalse(self.rows()[0].get('company_enabled', False))

    def test_timer_and_template_units_are_tracked_without_starting_job_services(self):
        app = self.fixture('scheduled')
        units = app / 'units'
        definitions = {
            'stationdesk-scheduled.service': '[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=default.target\n',
            'stationdesk-scheduled.timer': '[Timer]\nOnBootSec=5min\n[Install]\nWantedBy=timers.target\n',
            'stationdesk-scheduled-job.service': '[Service]\nType=oneshot\nExecStart=/bin/true\n',
            'stationdesk-scheduled@.service': '[Service]\nExecStart=/bin/true %i\n[Install]\nWantedBy=default.target\n',
        }
        for name, content in definitions.items():
            (units / name).write_text(content)
        active = {'stationdesk-scheduled.service', 'stationdesk-scheduled.timer'}
        passive = set(definitions) - active
        self.engine.add('scheduled')
        tracked = {a['name']: a for a in self.engine.load()['apps']['scheduled']['artifacts']
                   if a['kind'] == 'unit'}
        self.assertEqual(set(definitions), set(tracked))
        for name in definitions:
            self.assertTrue((self.engine.unit_dir / name).is_file())
            self.assertEqual(name in active, tracked[name]['enabled'])
        for name in active:
            self.assertIn(['systemctl', '--user', 'enable', '--now', name],
                          [args for args, _ in self.calls])
        for name in passive:
            self.assertFalse(any(args[:3] == ['systemctl', '--user', 'enable']
                                 and name in args for args, _ in self.calls))
        # A changed package restarts only units intended to be active.
        for name in definitions:
            with (units / name).open('a') as stream:
                stream.write('# updated\n')
        self.calls.clear()
        self.engine.add('scheduled')
        for name in active:
            self.assertIn(['systemctl', '--user', 'restart', name],
                          [args for args, _ in self.calls])
        for name in passive:
            self.assertNotIn(['systemctl', '--user', 'restart', name],
                             [args for args, _ in self.calls])
        self.calls.clear()
        self.engine.remove('scheduled')
        for name in definitions:
            self.assertFalse((self.engine.unit_dir / name).exists())
        template = 'stationdesk-scheduled@.service'
        for action in ('stop', 'disable'):
            self.assertNotIn(['systemctl', '--user', action, template],
                             [args for args, _ in self.calls])
        for name in active:
            self.assertIn(['systemctl', '--user', 'disable', name],
                          [args for args, _ in self.calls])

    def endpoint_fixture(self):
        self.fixture('multiport', 'serve = 8449\nendpoints = "folio/editor-endpoints.json"\n')
        state = self.root / 'state'
        (state / 'folio/editors').mkdir(parents=True)
        (state / 'folio/editors/team.conf').write_text(
            'location / { proxy_pass http://127.0.0.1:6769; }\n')
        (state / 'folio/editors/next.conf').write_text(
            'location / { proxy_pass http://127.0.0.1:6770; }\n')
        path = state / 'folio/editor-endpoints.json'
        initial = dict(https_port=8448, gate_port=18823, fragment='folio/editors/team.conf')
        replacement = dict(https_port=8450, gate_port=18824, fragment='folio/editors/next.conf')
        path.write_text(json.dumps([initial]))
        return path, initial, replacement

    def test_dynamic_endpoints_share_owner_gate_and_reconcile_mapping_ownership(self):
        path, initial, replacement = self.endpoint_fixture()
        with patch.object(self.engine, 'identity', return_value=('owner@example.test', 'desk.example.test')):
            self.engine.add('multiport')
            self.assertEqual([initial], self.engine.endpoints('multiport'))
            site = (self.root / 'nginx/site.conf').read_text()
            fragment = self.root / 'nginx/apps.d/multiport-endpoint-18823.conf'
            self.assertIn('listen 127.0.0.1:18823;', site)
            self.assertIn('if ($http_tailscale_user_login != "owner@example.test") { return 403; }', site)
            self.assertIn(str(fragment), site)
            self.assertIn('proxy_pass http://127.0.0.1:6769;', fragment.read_text())
            self.assertNotIn('server {', fragment.read_text())
            ledger = self.engine.load()['apps']['multiport']
            self.assertEqual([initial], ledger['endpoints'])
            commands = [args for args, _ in self.calls]
            self.assertIn(['tailscale', 'serve', '--bg', '--https=8448',
                           'http://127.0.0.1:18823'], commands)
            # Existing scalar serve remains the representative app endpoint.
            self.assertIn(['tailscale', 'serve', '--bg', '--https=8449',
                           f'http://127.0.0.1:{self.fixture_ports["multiport"] + 1}'], commands)
            self.calls.clear()
            path.write_text(json.dumps([replacement]))
            self.engine.project()
            commands = [args for args, _ in self.calls]
            self.assertIn(['tailscale', 'serve', '--https=8448', 'off'], commands)
            self.assertIn(['tailscale', 'serve', '--bg', '--https=8450',
                           'http://127.0.0.1:18824'], commands)
            self.assertFalse(fragment.exists())
            self.assertNotIn('listen 127.0.0.1:18823;', (self.root / 'nginx/site.conf').read_text())
            self.assertEqual([replacement], self.engine.load()['apps']['multiport']['endpoints'])
            self.calls.clear()
            self.engine.remove('multiport')
            commands = [args for args, _ in self.calls]
            self.assertIn(['tailscale', 'serve', '--https=8450', 'off'], commands)
            self.assertIn(['tailscale', 'serve', '--https=8449', 'off'], commands)
            self.assertFalse((self.root / 'nginx/apps.d/multiport-endpoint-18824.conf').exists())
            self.assertNotIn('multiport', self.engine.load()['apps'])
            self.assertTrue(path.exists())

    def test_endpoint_nginx_failure_restores_previous_projection_and_mapping_ownership(self):
        path, initial, replacement = self.endpoint_fixture()
        with patch.object(self.engine, 'identity', return_value=('owner@example.test', 'desk.example.test')):
            self.engine.add('multiport')
            site = self.root / 'nginx/site.conf'
            fragment = self.root / 'nginx/apps.d/multiport-endpoint-18823.conf'
            before = site.read_bytes(), fragment.read_bytes()
            path.write_text(json.dumps([replacement]))
            self.fail_nginx = True
            self.calls.clear()
            with self.assertRaises(RuntimeError):
                self.engine.project()
            self.assertEqual(before, (site.read_bytes(), fragment.read_bytes()))
            self.assertFalse((self.root / 'nginx/apps.d/multiport-endpoint-18824.conf').exists())
            self.assertEqual([initial], self.engine.load()['apps']['multiport']['endpoints'])
            self.assertFalse(any(args[:2] == ['tailscale', 'serve'] for args, _ in self.calls))
            # The pending runtime definition can be retried without reinstalling.
            self.fail_nginx = False
            self.engine.project()
            self.assertEqual([replacement], self.engine.load()['apps']['multiport']['endpoints'])
            self.assertFalse(fragment.exists())

    def test_updated_company_app_retains_dynamic_endpoint_fragment_ownership(self):
        state = self.root / 'state'
        (state / 'folio/editors').mkdir(parents=True)
        (state / 'folio/editors/team.conf').write_text(
            'location / { proxy_pass http://127.0.0.1:6769; }\n')
        endpoint = dict(https_port=8448, gate_port=18823, fragment='folio/editors/team.conf')
        (state / 'folio/editor-endpoints.json').write_text(json.dumps([endpoint]))
        fragment = self.root / 'nginx/apps.d/inline-app-endpoint-18823.conf'
        artifact = dict(kind='path', path=str(fragment))
        extra = 'endpoints = "folio/editor-endpoints.json"\n'
        with patch.object(self.engine, 'identity', return_value=('owner@example.test', 'desk.example.test')):
            self.engine.company(self.publish('inline-app', 'v1', extra=extra))
            self.engine.add('company:inline-app')
            self.assertIn(artifact, self.engine.load()['apps']['inline-app']['artifacts'])
            self.publish('inline-app', 'v2', 'updated company app', extra=extra)
            self.engine.update('inline-app')
            self.assertEqual('updated company app',
                             (self.root / 'web/inline-app/index.html').read_text())
            self.assertTrue(fragment.is_file())
            self.assertIn(artifact, self.engine.load()['apps']['inline-app']['artifacts'])
            self.assertEqual([endpoint], self.engine.load()['apps']['inline-app']['endpoints'])
            self.calls.clear()
            self.engine.remove('inline-app')
            self.assertFalse(fragment.exists())
            self.assertNotIn(str(fragment), (self.root / 'nginx/site.conf').read_text())
            self.assertIn(['tailscale', 'serve', '--https=8448', 'off'],
                          [args for args, _ in self.calls])


if __name__ == '__main__':
    unittest.main()
