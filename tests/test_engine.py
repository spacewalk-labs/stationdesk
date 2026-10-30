"""Engine integration within temporary directories; no live external commands."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('stationdesk', str(Path(__file__).parents[1] / 'bin/stationdesk'))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
loader.exec_module(engine)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'product'
        self.home = Path(self.temporary.name) / 'home'
        self.home.mkdir()
        self.root.mkdir()
        home_patch = patch.object(Path, 'home', return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.calls = []
        self.fixture_ports = {"hub": 19920, "hello": 19940, "publish": 19924}
        self.fail_nginx = False
        self.diff = 0
        command_patch = patch.object(engine, 'run', side_effect=self.command)
        command_patch.start()
        self.addCleanup(command_patch.stop)
        self.engine = engine.Engine(self.root)
        (self.root / 'nginx').mkdir()
        (self.root / 'nginx/site.tmpl').write_bytes((Path(__file__).parents[1] / 'nginx/site.tmpl').read_bytes())
        (self.root / 'state').mkdir()
        (self.root / 'state/box.toml').write_text('owner = "owner@example.test"\n')
        self.fixture('hub')

    def command(self, args, **kwargs):
        self.calls.append((args, kwargs))
        code, output = 0, ''
        if args[:2] == ['git', 'rev-parse']:
            output = 'a' * 40
        elif args[:2] == ['git', 'diff']:
            code = self.diff
        elif args[:2] == ['tailscale', 'status']:
            output = '{}'
        elif args[:2] == ['nginx', '-t'] and self.fail_nginx:
            code = 1
        elif args[:3] == ['systemctl', '--user', 'is-active']:
            output = 'active'
        return subprocess.CompletedProcess(args, code, output, 'fixture failure' if code else '')

    def fixture(self, app_id='hello', extra=''):
        app = self.root / ('hub' if app_id == 'hub' else f'apps/{app_id}')
        for directory in ('units', 'nginx', 'web'):
            (app / directory).mkdir(parents=True, exist_ok=True)
        port = self.fixture_ports.setdefault(app_id, 20000 + len(self.fixture_ports) * 10)
        (app / 'app.toml').write_text(f'label = "Hello"\nicon = "icon.svg"\nport = {port}\n' + extra)
        (app / 'icon.svg').write_text('<svg/>')
        (app / 'units' / f'stationdesk-{app_id}.service').write_text('[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=default.target\n')
        (app / 'nginx/main.conf').write_text(f'location /{app_id}/ {{}}\n')
        (app / 'web/index.html').write_text('hello')
        return app

    def skill_app(self):
        path = self.home / '.agents/skills/share-docs'
        app = self.fixture(extra=f'files = [{{path = "{path}", target = "share-docs"}}]\n')
        (app / 'share-docs').mkdir()
        return app, path

    def test_declared_link_install_update_and_remove(self):
        app, path = self.skill_app()
        self.engine.add('hello')
        self.assertEqual(str(app / 'share-docs'), path.readlink().as_posix())
        inode = path.lstat().st_ino
        self.engine.add('hello')
        self.assertEqual(inode, path.lstat().st_ino)
        self.assertIn({'kind': 'path', 'path': str(path), 'target': str(app / 'share-docs')},
                      self.engine.load()['apps']['hello']['artifacts'])
        self.engine.remove('hello')
        self.assertFalse(path.is_symlink())
        self.assertTrue((app / 'share-docs').is_dir())

    def test_readd_repairs_deleted_declared_link_without_installer(self):
        app, path = self.skill_app()
        self.engine.add('hello')
        path.unlink()
        self.engine.add('hello')
        self.assertEqual(app / 'share-docs', path.readlink())
        self.engine.remove('hello')
        self.assertFalse(path.is_symlink())

    def test_existing_skill_file_directory_and_dangling_link_are_not_owned(self):
        app, path = self.skill_app()
        path.parent.mkdir(parents=True)
        for kind in ('file', 'directory', 'link'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    path.write_text('company skill')
                elif kind == 'directory':
                    path.mkdir()
                    (path / 'SKILL.md').write_text('company skill')
                else:
                    path.symlink_to('/company/missing/share-docs')
                inode = path.lstat().st_ino
                self.engine.add('hello')
                self.engine.add('hello')
                self.assertFalse([a for a in self.engine.load()['apps']['hello']['artifacts']
                                  if a.get('path') == str(path)])
                self.engine.remove('hello')
                self.assertEqual(inode, path.lstat().st_ino)
                if kind == 'directory':
                    (path / 'SKILL.md').unlink()
                    path.rmdir()
                else:
                    path.unlink()

    def test_user_replaced_owned_link_survives_remove_and_update(self):
        for update in (False, True):
            with self.subTest(update=update):
                app, path = self.skill_app()
                self.engine.add('hello')
                path.unlink()
                path.symlink_to('/company/share-docs')
                if update:
                    self.engine.add('hello')
                    self.assertFalse([a for a in self.engine.load()['apps']['hello']['artifacts']
                                      if a.get('path') == str(path)])
                self.engine.remove('hello')
                self.assertEqual('/company/share-docs', str(path.readlink()))
                path.unlink()
                (app / 'share-docs').rmdir()

    def test_legacy_unconditional_ownership_is_migrated_without_deleting_company_skill(self):
        app, path = self.skill_app()
        for company in (False, True):
            with self.subTest(company=company):
                self.engine.add('hello')
                ledger = self.engine.load()
                for artifact in ledger['apps']['hello']['artifacts']:
                    artifact.pop('target', None)
                self.engine.save(ledger)
                if company:
                    path.unlink()
                    path.symlink_to('/company/share-docs')
                self.engine.add('hello')
                self.engine.remove('hello')
                self.assertEqual(company, path.is_symlink())
                if company:
                    self.assertEqual('/company/share-docs', str(path.readlink()))
                    path.unlink()

    def test_declared_link_retargets_when_app_source_moves(self):
        app, path = self.skill_app()
        self.engine.install('hello', directory=app, origin='folder')
        moved = app.with_name('moved')
        shutil.copytree(app, moved)
        self.engine.install('hello', directory=moved)
        self.assertEqual(str(moved / 'share-docs'), str(path.readlink()))
        self.engine.remove('hello')
        self.assertFalse(path.is_symlink())

    def test_legacy_declared_link_retargets_when_app_source_moves(self):
        app, path = self.skill_app()
        self.engine.install('hello', directory=app, origin='folder')
        ledger = self.engine.load()
        for artifact in ledger['apps']['hello']['artifacts']:
            artifact.pop('target', None)
        self.engine.save(ledger)
        moved = app.with_name('moved')
        shutil.copytree(app, moved)
        self.engine.install('hello', directory=moved)
        self.assertEqual(str(moved / 'share-docs'), str(path.readlink()))
        self.engine.remove('hello')
        self.assertFalse(path.is_symlink())

    def test_port_conflict_is_reported_before_install_and_batch_continues(self):
        self.fixture('bench', 'serve = 19970\n')
        self.engine.add('bench')
        for app_id, port in [('backend-conflict', self.fixture_ports['bench']),
                             ('gate-conflict', self.fixture_ports['bench'] + 1),
                             ('serve-conflict', 19970)]:
            app = self.fixture(app_id)
            manifest = app / 'app.toml'
            manifest.write_text(manifest.read_text().replace(str(self.fixture_ports[app_id]), str(port)))
            (app / 'install.sh').write_text('exit 0')
            self.calls.clear()
            with self.assertRaisesRegex(ValueError, f'포트 {port} 은 bench 이 사용 중'):
                self.engine.add(app_id)
            self.assertFalse(any(args[0] == 'bash' for args, _ in self.calls))
            self.assertNotIn(app_id, self.engine.load()['apps'])
        self.fixture('good')
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(1, self.engine.batch('add', ['gate-conflict', 'good']))
        self.assertIn('bench 이 사용 중', errors.getvalue())
        self.assertIn('good', self.engine.load()['apps'])
        self.engine.add('bench')  # own ports remain valid on re-add

    def test_builtin_app_ports_do_not_overlap(self):
        product = Path(__file__).parents[1]
        owners = {}
        for manifest in sorted([product / 'hub/app.toml', *(product / 'apps').glob('*/app.toml')]):
            app_id = manifest.parent.name
            for port in self.engine.ports(app_id, tomllib.loads(manifest.read_text())):
                self.assertNotIn(port, owners, f'{app_id} port {port} is used by {owners.get(port)}')
                owners[port] = app_id
        self.assertEqual({19940, 19942, 19943}, {p for p, a in owners.items() if a == 'orca'})

    def test_orca_gate_and_units_render(self):
        source = Path(__file__).parents[1] / 'apps/orca'
        app = self.root / 'apps/orca'
        for name in ('nginx', 'units'):
            shutil.copytree(source / name, app / name)
        for name in ('app.toml', 'orca.png'):
            shutil.copyfile(source / name, app / name)
        self.engine.add('hub')
        self.engine.add('orca')
        gate = (self.root / 'nginx/apps.d/orca-serve.conf').read_text()
        self.assertNotIn('${', gate)
        self.assertNotIn('$$', gate)
        self.assertIn(f'include "{self.root}/state/orca/pairing.conf";', gate)
        self.assertIn('return 302 /orca-web/web-index.html$orca_pairing;', gate)
        self.assertIn(f'alias {app}/web-bundle/dist/assets/;', gate)
        self.assertIn('proxy_pass http://127.0.0.1:19942;', gate)
        site = (self.root / 'nginx/site.conf').read_text()
        self.assertIn('listen 127.0.0.1:19943;', site)
        self.assertIn('return 302 https://:19940/;', (self.root / 'nginx/apps.d/orca.conf').read_text())
        serve = (self.home / '.config/systemd/user/stationdesk-orca.service').read_text()
        self.assertIn('Requires=stationdesk-orca-guard.service stationdesk-orca-xvfb.service', serve)
        self.assertIn(f'Environment="STATIONDESK_APP_DIR={app}"', serve)
        self.assertIn('Environment="STATIONDESK_PORT=19942"', serve)
        self.assertIn(['systemctl', '--user', 'enable', '--now', 'stationdesk-orca-guard.service'],
                      [args for args, _ in self.calls])

    def test_failure_tail_uses_stdout_and_masks_credentials(self):
        output = 'Cloning into source...\n' + '\n'.join(f'progress {i}' for i in range(20))
        output += '\nhttps://user:pass@example.test/?token=private\nDocker 필요 — sudo apt-get install -y docker.io\n'
        with patch.object(engine, 'run', return_value=subprocess.CompletedProcess([], 1, output, '')):
            with self.assertRaises(RuntimeError) as error:
                self.engine.command(['bash', 'install.sh'])
        message = str(error.exception)
        self.assertIn('Docker 필요 — sudo apt-get install -y docker.io', message)
        self.assertNotIn('Cloning', message)
        self.assertNotIn('private', message)
        self.assertNotIn('user:pass', message)

    def test_add_again_does_not_rewrite_files_or_restart_units(self):
        app = self.fixture()
        (app / 'install.sh').write_text('exit 0\n')
        self.engine.add('hello')
        files = [p for base in (self.root, self.home) for p in base.rglob('*') if p.is_file()]
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in files}
        self.calls.clear()
        self.engine.add('hello')
        after = {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in files}
        self.assertEqual(before, after)
        self.assertEqual(1, sum(args[0] == 'bash' for args, _ in self.calls))
        self.assertFalse(any(args[:3] in (['systemctl', '--user', 'restart'],
                                           ['systemctl', '--user', 'daemon-reload'])
                             for args, _ in self.calls))
        self.assertIn(['git', 'diff', '--quiet', 'a'*40, 'HEAD', '--', 'apps/hello'],
                      [args for args, _ in self.calls])

    def test_installer_changes_owned_file_restarts_once(self):
        app = self.fixture(extra='files = ["runtime/hello/config"]\n')
        (app / 'install.sh').write_text('fixture installer\n')
        self.engine.add('hello')
        self.calls.clear()

        def command(args, **kwargs):
            if args[0] == 'bash':
                target = self.root / 'runtime/hello/config'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('new external configuration')
            return self.command(args, **kwargs)

        with patch.object(engine, 'run', side_effect=command):
            self.engine.add('hello')
        self.assertEqual(1, sum(args[0] == 'bash' for args, _ in self.calls))
        self.assertEqual(1, sum(args == ['systemctl', '--user', 'restart', 'stationdesk-hello.service']
                                for args, _ in self.calls))
        self.assertNotIn(['systemctl', '--user', 'daemon-reload'], [a for a, _ in self.calls])

    def test_installer_dropin_change_reloads_units(self):
        dropin = self.home / '.config/systemd/user/stationdesk-hello.service.d'
        app = self.fixture(extra=f'files = ["{dropin}"]\n')
        (app / 'install.sh').write_text('fixture installer\n')
        self.engine.add('hello')
        self.calls.clear()

        def command(args, **kwargs):
            if args[0] == 'bash':
                dropin.mkdir(parents=True, exist_ok=True)
                (dropin / 'local.conf').write_text('[Service]\nEnvironment=CHANGED=1\n')
            return self.command(args, **kwargs)

        with patch.object(engine, 'run', side_effect=command):
            self.engine.add('hello')
        self.assertIn(['systemctl', '--user', 'daemon-reload'], [a for a, _ in self.calls])
        self.assertIn(['systemctl', '--user', 'restart', 'stationdesk-hello.service'], [a for a, _ in self.calls])

    def test_without_installer_unchanged_app_still_returns_early(self):
        self.fixture()
        self.engine.add('hello')
        self.calls.clear()
        self.engine.add('hello')
        self.assertFalse(any(args[0] == 'bash' or args[:3] == ['systemctl', '--user', 'restart']
                             for args, _ in self.calls))

    def test_add_recovers_stopped_nginx_with_unchanged_unit(self):
        self.fixture()
        self.engine.add('hello')
        self.calls.clear()
        running = False
        def command(args, **kwargs):
            nonlocal running
            if args == ['systemctl', '--user', 'enable', '--now', 'stationdesk-nginx.service']:
                running = True
            if args[:3] == ['nginx', '-s', 'reload'] and not running:
                return subprocess.CompletedProcess(args, 1, '', 'nginx.pid missing')
            return self.command(args, **kwargs)
        with patch.object(engine, 'run', side_effect=command):
            self.engine.add('hello')
        self.assertTrue(running)

    def test_changed_unit_and_changed_commit_restart(self):
        app = self.fixture()
        self.engine.add('hello')
        unit = app / 'units/stationdesk-hello.service'
        unit.write_text(unit.read_text() + 'Restart=always\n')
        self.calls.clear()
        self.engine.add('hello')
        self.assertIn(['systemctl', '--user', 'restart', unit.name], [a for a, _ in self.calls])
        self.calls.clear()
        self.diff = 1
        self.engine.add('hello')
        self.assertIn(['systemctl', '--user', 'restart', unit.name], [a for a, _ in self.calls])

    def test_remove_only_recorded_paths_preserves_user_data(self):
        self.fixture(extra='files = ["runtime/hello-tool"]\n')
        self.engine.add('hello')
        for relative in ('runtime/hello-tool', 'share/user.txt', 'state/user.db', 'nginx/apps.d/user.conf'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('keep unless recorded')
        self.engine.remove('hello')
        self.assertFalse((self.root / 'runtime/hello-tool').exists())
        self.assertFalse((self.root / 'web/hello').exists())
        self.assertFalse((self.root / 'nginx/apps.d/hello.conf').exists())
        self.assertFalse((self.engine.unit_dir / 'stationdesk-hello.service').exists())
        for relative in ('share/user.txt', 'state/user.db', 'nginx/apps.d/user.conf', 'apps/hello/app.toml'):
            self.assertTrue((self.root / relative).is_file())
        self.assertEqual({}, self.engine.load()['apps'])

    def test_vanished_source_does_not_block_other_removals(self):
        # sd-box 2026-10-27: a company folder deleted under installed apps made every
        # remove fail on the first missing app.toml, so nothing could be cleaned up.
        self.fixture('hello')
        self.fixture('other')
        self.engine.add('hello')
        self.engine.add('other')
        shutil.rmtree(self.root / 'apps/hello')
        # Online too: serve() walks the apps for Tailscale endpoints (sd-box had an fqdn).
        with contextlib.redirect_stderr(io.StringIO()) as stderr, \
                patch.object(self.engine, 'identity', return_value=('owner@example.test', 'box.example.ts.net')):
            self.engine.remove('other')
        self.assertIn('hello', stderr.getvalue())
        self.assertNotIn('other', self.engine.load()['apps'])
        self.assertFalse((self.root / 'nginx/apps.d/other.conf').exists())
        self.engine.remove('hello')
        self.assertEqual({}, self.engine.load()['apps'])
        self.assertFalse((self.engine.unit_dir / 'stationdesk-hello.service').exists())

    def test_symlink_ancestor_refused_and_outside_file_unchanged(self):
        self.fixture(extra='files = ["runtime/tool"]\n')
        self.engine.add('hello')
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        (outside / 'tool').write_text('outside')
        (self.root / 'runtime').symlink_to(outside, target_is_directory=True)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'symlink ancestor'):
            self.engine.remove('hello')
        self.assertEqual('outside', (outside / 'tool').read_text())
        self.assertIn('hello', self.engine.load()['apps'])
        self.assertEqual([], self.calls)

    def test_recorded_symlink_itself_removed_without_following(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('keep')
        link = self.root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        self.engine.safe_remove(link)
        self.assertFalse(link.is_symlink())
        self.assertEqual('keep', (outside / 'keep').read_text())

    def test_batch_continues_after_app_failure(self):
        self.fixture('good')
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            result = self.engine.batch('add', ['missing', 'good', 'also-missing'])
        self.assertEqual(1, result)
        self.assertIn('good', self.engine.load()['apps'])
        self.assertIn('missing:', errors.getvalue())
        self.assertIn('also-missing:', errors.getvalue())

    def test_nginx_failure_restores_previous_fragments_and_tiles(self):
        app = self.fixture()
        self.engine.add('hello')
        fragment = self.root / 'nginx/apps.d/hello.conf'
        tiles = self.root / 'web/hub/__stationdesk.json'
        before = fragment.read_bytes(), tiles.read_bytes()
        self.fixture('second')
        # Mimic a newly recorded app and a modified installed fragment.
        ledger = self.engine.load()
        ledger['apps']['second'] = dict(ledger['apps']['hello'], dir=str(self.root / 'apps/second'))
        self.engine.save(ledger)
        (app / 'nginx/main.conf').write_text('invalid nginx configuration')
        self.fail_nginx = True
        self.calls.clear()
        with self.assertRaises(RuntimeError):
            self.engine.project()
        self.assertEqual(before, (fragment.read_bytes(), tiles.read_bytes()))
        self.assertFalse((fragment.parent / 'second.conf').exists())
        self.assertNotIn(['nginx', '-s', 'reload'], [a for a, _ in self.calls])

    def test_remove_nginx_failure_restores_removed_fragment(self):
        self.fixture()
        self.engine.add('hello')
        fragment = self.root / 'nginx/apps.d/hello.conf'
        before = fragment.read_bytes()
        self.fail_nginx = True
        with self.assertRaises(RuntimeError):
            self.engine.remove('hello')
        self.assertEqual(before, fragment.read_bytes())
        self.fail_nginx = False
        self.engine.remove('hello')
        self.assertFalse(fragment.exists())
        self.assertEqual({}, self.engine.load()['apps'])

    def test_update_retires_removed_units_nginx_and_web_artifacts(self):
        app = self.fixture()
        self.engine.add('hello')
        unit = app / 'units/stationdesk-hello.service'
        unit.unlink()
        (app / 'nginx/main.conf').unlink()
        (app / 'web/index.html').unlink()
        (app / 'web').rmdir()
        self.diff = 1
        self.calls.clear()
        self.engine.add('hello')
        self.assertFalse((self.engine.unit_dir / unit.name).exists())
        self.assertFalse((self.root / 'nginx/apps.d/hello.conf').exists())
        self.assertFalse((self.root / 'web/hello/index.html').exists())
        self.assertTrue((self.root / 'web/hello/icon.svg').exists())
        for action in ('stop', 'disable'):
            self.assertIn(['systemctl', '--user', action, unit.name], [a for a, _ in self.calls])
        self.engine.remove('hello')
        self.assertFalse((self.root / 'web/hello/icon.svg').exists())
        self.assertEqual({}, self.engine.load()['apps'])

    def test_update_nginx_failure_keeps_ownership_for_retry_and_remove(self):
        app = self.fixture()
        self.engine.add('hello')
        (app / 'nginx/main.conf').unlink()
        fragment = self.root / 'nginx/apps.d/hello.conf'
        self.diff = 1
        self.fail_nginx = True
        with self.assertRaises(RuntimeError):
            self.engine.add('hello')
        self.assertTrue(fragment.exists())
        self.assertIn({'kind': 'path', 'path': str(fragment)},
                      self.engine.load()['apps']['hello']['artifacts'])
        self.fail_nginx = False
        self.diff = 0  # New commit was recorded; retry must still finish projection.
        self.engine.add('hello')
        self.assertFalse(fragment.exists())
        self.engine.remove('hello')
        self.assertFalse(fragment.exists())
        self.assertEqual({}, self.engine.load()['apps'])

    def test_static_app_readd_after_failed_remove_restores_web_and_icon(self):
        app = self.fixture()
        (app / 'units/stationdesk-hello.service').unlink()
        self.engine.add('hello')
        self.fail_nginx = True
        with self.assertRaises(RuntimeError):
            self.engine.remove('hello')
        self.assertFalse((self.root / 'web/hello/index.html').exists())
        self.fail_nginx = False
        self.engine.add('hello')
        self.assertEqual('hello', (self.root / 'web/hello/index.html').read_text())
        self.assertEqual('<svg/>', (self.root / 'web/hello/icon.svg').read_text())
        self.engine.remove('hello')
        self.assertEqual({}, self.engine.load()['apps'])

    def test_update_reprojects_when_no_app_changed(self):
        self.fixture()
        self.engine.add('hello')
        projected = []
        original = self.engine.project
        self.engine.project = lambda *a, **k: projected.append(1) or original(*a, **k)
        self.diff = 0
        self.engine.update()
        self.assertTrue(projected)

    def test_web_update_removes_deleted_pages(self):
        app = self.fixture()
        (app / 'web/legacy.html').write_text('retired page')
        self.engine.add('hello')
        (app / 'web/legacy.html').unlink()
        (app / 'web/index.html').write_text('new index')
        self.diff = 1
        self.engine.add('hello')
        self.assertFalse((self.root / 'web/hello/legacy.html').exists())
        self.assertEqual('new index', (self.root / 'web/hello/index.html').read_text())
        self.assertTrue((self.root / 'web/hello/icon.svg').exists())

    def test_install_failure_keeps_other_apps_running_in_batch(self):
        failed = self.fixture('failed')
        (failed / 'install.sh').write_text('exit 1')
        self.fixture('good')
        def command(args, **kwargs):
            if args[0] == 'bash':
                return subprocess.CompletedProcess(args, 1, '', 'install failed')
            return self.command(args, **kwargs)
        with patch.object(engine, 'run', side_effect=command):
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(1, self.engine.batch('add', ['failed', 'good']))
        self.assertIn('install failed', errors.getvalue())
        self.assertEqual(['good'], list(self.engine.load()['apps']))

    def test_environment_and_list_and_hub_projection(self):
        self.fixture('hello')
        app = self.fixture('hub', 'serve = 8443\nunknown = "ignored"\n')
        (app / 'install.sh').write_text('exit 0')
        self.engine.add('hello')
        self.engine.add('hub')
        environment = next(kw['env'] for args, kw in self.calls if args[0] == 'bash')
        for key, value in {'ROOT': self.root, 'APP_ID': 'hub', 'APP_DIR': app,
                           'PORT': 19920, 'SHARE': self.root / 'share', 'STATE': self.root / 'state',
                           'PORT_HELLO': 19940}.items():
            self.assertEqual(str(value), environment[f'STATIONDESK_{key}'])
        unit = (self.engine.unit_dir / 'stationdesk-hub.service').read_text()
        self.assertIn(f'Environment="STATIONDESK_ROOT={self.root}"', unit)
        self.assertEqual({'stationdesk-hub.service': 'active'}, self.engine.list()['hub']['units'])
        tiles = json.loads((self.root / 'web/hub/__stationdesk.json').read_text())
        self.assertEqual(['hello', 'hub'], [t['id'] for t in tiles])
        self.assertEqual(8443, tiles[1]['serve'])
        self.assertEqual({'email': 'owner@example.test'}, json.loads(
            (self.root / 'web/hub/__owner.json').read_text()))

    def test_app_failure_and_recovery_hooks_and_box_facts(self):
        self.fixture('hello')
        self.engine.add('hello')
        unit = (self.engine.unit_dir / 'stationdesk-hello.service').read_text()
        self.assertIn('OnFailure=stationdesk-alarm@%n.service', unit)
        self.assertIn('ExecStopPost=-', unit)
        self.assertIn('--unit-result "%n"', unit)
        self.assertIn('--resolves', unit)
        self.assertIn('ExecStartPost=-', unit)
        alarm = (self.engine.unit_dir / 'stationdesk-alarm@.service').read_text()
        self.assertIn('--group "unit:%i" --level urgent', alarm)
        facts = (self.root / 'state/box-facts.md').read_text()
        self.assertIn('stationdesk-card --group <id> --title <message>', facts)
        self.assertIn('| Hello | https://localhost/hello/ |', facts)

    def test_identity_from_tailscale_self_user_and_serve_mapping(self):
        self.fixture('hub')
        status = {'Self': {'UserID': 42, 'DNSName': 'desk.example.test.'},
                  'User': {'42': {'LoginName': 'live@example.test'}}}
        with patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'):
            with patch.object(engine, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(status), '')) as command:
                self.assertEqual(('live@example.test', 'desk.example.test'), self.engine.identity())
                self.engine.serve()
                self.assertEqual(['tailscale', 'serve', '--bg', '--https=443', 'http://127.0.0.1:19920'],
                                 command.call_args.args[0])

    def test_own_port_gate_mapping_and_removal(self):
        app = self.fixture('publish', 'serve = 8000\n')
        (app / 'nginx/serve.conf').write_text('location / { root ${STATIONDESK_SHARE}; }')
        status = {'Self': {'UserID': 42, 'DNSName': 'desk.example.test.'},
                  'User': {'42': {'LoginName': 'owner@example.test'}}}
        original = self.command
        def command(args, **kwargs):
            if args[:2] == ['tailscale', 'status']:
                return subprocess.CompletedProcess(args, 0, json.dumps(status), '')
            return original(args, **kwargs)
        with patch.object(engine.shutil, 'which', return_value='/usr/bin/tailscale'), \
                patch.object(engine, 'run', side_effect=command):
            self.engine.add('publish')
            site = (self.root / 'nginx/site.conf').read_text()
            self.assertIn('listen 127.0.0.1:19925;', site)
            self.assertIn('publish-serve.conf', site)
            fragment = self.root / 'nginx/apps.d/publish.conf'
            self.assertNotIn('root ', fragment.read_text())
            self.assertIn(['tailscale', 'serve', '--bg', '--https=8000',
                           'http://127.0.0.1:19925'], [a for a, _ in self.calls])
            self.assertEqual('/publish/', json.loads((self.root / 'web/hub/__stationdesk.json').read_text())[0]['path'])
            self.engine.remove('publish')
            self.assertFalse((fragment.parent / 'publish-serve.conf').exists())
            self.assertNotIn('19925', (self.root / 'nginx/site.conf').read_text())
            self.assertIn(['tailscale', 'serve', '--https=8000', 'off'], [a for a, _ in self.calls])

    def test_own_port_backend_without_nginx_fragment(self):
        app = self.fixture('standalone', 'serve = 8444\n')
        (app / 'nginx/main.conf').unlink()
        self.engine.add('standalone')
        fragment = self.root / 'nginx/apps.d/standalone-serve.conf'
        self.assertIn(f'proxy_pass http://127.0.0.1:{self.fixture_ports["standalone"]}', fragment.read_text())
        self.assertIn({'kind': 'path', 'path': str(fragment)},
                      self.engine.load()['apps']['standalone']['artifacts'])
        self.engine.remove('standalone')
        self.assertFalse(fragment.exists())

    def test_later_app_does_not_change_earlier_unit_on_readd(self):
        self.fixture('first')
        self.fixture('later')
        self.engine.add('first')
        unit = self.engine.unit_dir / 'stationdesk-first.service'
        before = unit.read_bytes(), unit.stat().st_mtime_ns
        self.engine.add('later')
        self.calls.clear()
        self.engine.add('first')
        self.assertEqual(before, (unit.read_bytes(), unit.stat().st_mtime_ns))
        self.assertNotIn(['systemctl', '--user', 'restart', unit.name], [a for a, _ in self.calls])

    def test_update_failed_fast_forward_does_not_install_apps(self):
        def command(args, **kwargs):
            if args[:2] == ['git', 'merge']:
                return subprocess.CompletedProcess(args, 1, '', 'not fast-forward')
            return self.command(args, **kwargs)
        with patch.object(engine, 'run', side_effect=command), patch.object(self.engine, 'batch') as batch:
            with self.assertRaisesRegex(RuntimeError, 'not fast-forward'):
                self.engine.update()
            batch.assert_not_called()

    def test_render_golden_owner_gate_and_explicit_includes(self):
        self.fixture('hello')
        rendered = self.engine.render_site({'hub': {}, 'hello': {}})
        normalized = rendered.replace(str(self.root), '/opt/stationdesk')
        expected = (Path(__file__).parent / 'golden/site.conf').read_text()
        self.assertEqual(expected, normalized)
        self.assertNotIn('apps.d/*', rendered)

    def test_site_and_fragment_rollback_together(self):
        self.fixture('hello')
        self.engine.add('hello')
        site = self.root / 'nginx/site.conf'
        before = site.read_bytes()
        (self.root / 'state/box.toml').write_text('owner = "changed@example.test"\n')
        self.fail_nginx = True
        with self.assertRaises(RuntimeError):
            self.engine.project()
        self.assertEqual(before, site.read_bytes())

    def test_doctor_zero_checks_fails(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1, self.engine.doctor())
        self.assertIn('확인 0 · 실패 0', output.getvalue())

    def test_doctor_checks_all_gates_and_continues_after_failed_smoke(self):
        app = self.fixture('hello')
        (app / 'smoke.sh').write_text('exit 1')
        self.engine.add('hello')
        def probes(args, **kwargs):
            if args[0] == 'bash':
                return subprocess.CompletedProcess(args, 1, '', 'smoke failure')
            if args[0] == 'curl':
                code = '200' if 'Tailscale-User-Login: owner@example.test' in args else '403'
                return subprocess.CompletedProcess(args, 0, code, '')
            return self.command(args, **kwargs)
        with patch.object(engine, 'run', side_effect=probes):
            with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, self.engine.doctor())
        self.assertIn('확인 4 · 실패 1', output.getvalue())
        self.assertIn('hello anonymous: OK', output.getvalue())

    def test_builtin_id_from_repository_directory_keeps_builtin_origin(self):
        with contextlib.chdir(self.root):
            self.engine.add('hub')
        self.assertEqual('builtin', self.engine.load()['apps']['hub']['origin'])

    def test_selected_app_update_fetches_and_installs_only_that_app(self):
        self.fixture('devterm')
        self.engine.add('devterm')
        with patch.object(self.engine, 'batch', return_value=0) as batch, \
                patch.object(self.engine, 'project') as project:
            self.assertEqual(0, self.engine.update('devterm'))
            batch.assert_called_once_with('add', ['devterm'])
            project.assert_called_once_with()
        self.assertEqual(['git', 'fetch'], self.calls[-2][0])
        self.assertEqual(30, self.calls[-2][1]['timeout'])
        self.assertEqual(['git', 'merge', '--ff-only', '@{u}'], self.calls[-1][0])

    def test_update_ff_only_then_installed_apps(self):
        with patch.object(self.engine, 'batch', return_value=0) as batch, \
                patch.object(self.engine, 'project') as project:
            self.assertEqual(0, self.engine.update())
            batch.assert_called_once_with('add', [])
            project.assert_called_once_with()
        self.assertEqual([['git', 'fetch'], ['git', 'merge', '--ff-only', '@{u}']],
                         [a for a, _ in self.calls])

    def test_g3_escapes_app_unit_but_not_transient_sibling(self):
        with patch.object(Path, 'read_text', return_value='0::/user.slice/stationdesk-hello.service'):
            with patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as command:
                self.assertEqual(0, engine.escape_app_cgroup())
                self.assertEqual(['systemd-run', '--user', '--collect', '--wait', '--pipe'],
                                 command.call_args.args[0][:5])
        with patch.object(Path, 'read_text', return_value='0::/user.slice/run-random.service'):
            self.assertIsNone(engine.escape_app_cgroup())
        with patch.object(Path, 'read_text', return_value='0::/user.slice/stationdesk-store-' + 'a' * 32 + '.service'):
            with patch.object(engine.subprocess, 'run') as command:
                self.assertIsNone(engine.escape_app_cgroup())
                command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
