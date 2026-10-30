"""External sources use real Git; service commands remain isolated fixtures."""
from pathlib import Path
import json
import unittest
import subprocess
from unittest.mock import patch

import test_engine
from test_engine import engine


class AddSourceTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    fixture = test_engine.EngineTests.fixture
    command = test_engine.EngineTests.command

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()

    def source(self):
        source = Path(self.temporary.name) / 'source'
        source.mkdir()
        self.git('init', '-b', 'main', cwd=source)
        for app_id in ('one', 'two'):
            app = self.fixture(app_id)
            target = source / 'apps' / app_id
            target.parent.mkdir(exist_ok=True)
            app.rename(target)
        self.git('add', '.', cwd=source)
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'apps', cwd=source)
        bare = Path(self.temporary.name) / 'source.git'
        self.git('clone', '--bare', str(source), str(bare))
        return source, bare

    def real_git(self, args, **kwargs):
        if args[0] == 'git':
            return subprocess.run(args, text=True, capture_output=True, **kwargs)
        return self.command(args, **kwargs)

    def test_repo_multi_app_update_and_last_remove_owns_clone(self):
        source, bare = self.source()
        with patch.object(engine, 'run', side_effect=self.real_git):
            # Engine core also has a tracking upstream for update.
            self.git('clone', str(bare), str(self.root / 'core'))
            def commands(args, **kwargs):
                if kwargs.get('cwd') == self.root and args[0] == 'git':
                    kwargs['cwd'] = self.root / 'core'
                return self.real_git(args, **kwargs)
            with patch.object(engine, 'run', side_effect=commands):
                self.engine.add(bare.as_uri() + '#main')
                rows = self.engine.load()['apps']
                self.assertEqual({'one', 'two'}, set(rows))
                self.assertEqual(bare.as_uri() + '#main', rows['one']['origin'])
                clone = Path(rows['one']['repo'])
                (source / 'apps/one/web/index.html').write_text('updated')
                self.git('add', '.', cwd=source)
                self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'update', cwd=source)
                self.git('push', str(bare), 'main', cwd=source)
                self.assertEqual(0, self.engine.update())
                self.assertEqual('updated', (self.root / 'web/one/index.html').read_text())
                data = self.root / 'share/keep'
                data.parent.mkdir(exist_ok=True)
                data.write_text('data')
                self.engine.remove('one')
                self.assertTrue(clone.exists())
                self.engine.remove('two')
                self.assertFalse(clone.exists())
                self.assertEqual('data', data.read_text())

    def test_folder_readd_detects_changes_and_preserves_source(self):
        folder = Path(self.temporary.name) / 'folder-app'
        self.fixture('folder-app').rename(folder)
        self.engine.add(str(folder))
        self.assertEqual(str(folder), self.engine.load()['apps']['folder-app']['origin'])
        self.calls.clear()
        self.engine.add(str(folder))
        self.assertFalse(any(a[:3] == ['systemctl', '--user', 'restart'] for a, _ in self.calls))
        (folder / 'web/index.html').write_text('changed')
        self.engine.add(str(folder))
        self.assertEqual('changed', (self.root / 'web/folder-app/index.html').read_text())
        self.engine.remove('folder-app')
        self.assertTrue((folder / 'app.toml').exists())

    def test_folder_change_appears_in_hub_updates_and_clears_after_selected_update(self):
        from hub import Hub
        folder = Path(self.temporary.name) / 'folder-app'
        self.fixture('folder-app').rename(folder)
        self.engine.add(str(folder))
        self.assertEqual([], Hub(self.root).updates()['apps'])
        self.assertEqual(engine.folder_digest(folder), self.engine.load()['apps']['folder-app']['commit'])
        (folder / 'web/index.html').write_text('changed after install')
        updates = Hub(self.root).updates()
        self.assertEqual(['folder-app'], [row['id'] for row in updates['apps']])
        self.assertNotIn('folder-app', updates['unknown_apps'])
        self.assertEqual(0, self.engine.update('folder-app'))
        self.assertEqual('changed after install', (self.root / 'web/folder-app/index.html').read_text())
        self.assertEqual([], Hub(self.root).updates()['apps'])

    def test_links_project_without_install_or_doctor_probes(self):
        self.engine.link_add('팀 위키', 'https://example.test/wiki')
        rows = self.engine.load()['apps']
        app_id = next(iter(rows))
        self.assertEqual([], rows[app_id]['artifacts'])
        tiles = json.loads((self.root / 'web/hub/__stationdesk.json').read_text())
        self.assertEqual('https://example.test/wiki', tiles[0]['url'])
        self.assertEqual('link', tiles[0]['kind'])
        self.engine.link_remove('팀 위키')
        self.assertEqual({}, self.engine.load()['apps'])
        self.assertEqual([], json.loads((self.root / 'web/hub/__stationdesk.json').read_text()))
        with self.assertRaises(ValueError):
            self.engine.link_add('bad', 'javascript:alert(1)')

    def test_external_app_cannot_replace_builtin_or_link(self):
        self.fixture('hello')
        self.engine.add('hello')
        folder = Path(self.temporary.name) / 'hello'
        self.fixture('one').rename(folder)
        with self.assertRaisesRegex(RuntimeError, 'already installed'):
            self.engine.add(str(folder))

    def test_root_repo_accepts_commit_ref_and_keeps_pinned_commit(self):
        source, bare = self.source()
        # A single app at the repository root takes its ID from the repository name.
        for path in (source / 'apps/one').iterdir():
            path.rename(source / path.name)
        self.git('add', '.', cwd=source)
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'root app', cwd=source)
        self.git('push', str(bare), 'main', cwd=source)
        commit = self.git('rev-parse', 'HEAD', cwd=source)
        with patch.object(engine, 'run', side_effect=self.real_git):
            self.engine.add(bare.as_uri() + '#' + commit)
            row = self.engine.load()['apps']['source']
            self.assertEqual(commit, row['commit'])
            self.assertEqual(str(Path(row['repo'])), row['dir'])
            self.engine.remove('source')
            self.assertFalse(Path(row['repo']).exists())

    def test_addon_non_fast_forward_stops_before_reinstallation(self):
        folder = self.fixture('hello')
        self.engine.add('hello')
        ledger = self.engine.load()
        ledger['apps']['hello']['repo'] = str(folder)
        self.engine.save(ledger)
        def command(args, **kwargs):
            if kwargs.get('cwd') == str(folder) and args[:2] == ['git', 'merge']:
                return subprocess.CompletedProcess(args, 1, '', 'not fast-forward')
            return self.command(args, **kwargs)
        with patch.object(engine, 'run', side_effect=command), patch.object(self.engine, 'batch') as batch:
            with self.assertRaisesRegex(RuntimeError, 'not fast-forward'):
                self.engine.update()
            batch.assert_not_called()

    def test_clone_symlink_ancestor_refused_before_artifacts_are_deleted(self):
        source, bare = self.source()
        with patch.object(engine, 'run', side_effect=self.real_git):
            self.engine.add(bare.as_uri())
        self.engine.remove('one')
        addons = self.root / 'addons'
        outside = Path(self.temporary.name) / 'moved-addons'
        addons.rename(outside)
        addons.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor'):
            self.engine.remove('two')
        self.assertTrue((self.root / 'web/two/index.html').exists())
        self.assertIn('two', self.engine.load()['apps'])

    def test_invalid_repo_leaves_no_unrecorded_clone(self):
        source = Path(self.temporary.name) / 'empty-source'
        source.mkdir()
        self.git('init', '-b', 'main', cwd=source)
        (source / 'README').write_text('no app')
        self.git('add', '.', cwd=source)
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'no app', cwd=source)
        with patch.object(engine, 'run', side_effect=self.real_git):
            with self.assertRaisesRegex(ValueError, 'no app.toml'):
                self.engine.add(source.as_uri())
        self.assertEqual([], list((self.root / 'addons').iterdir()))

    def test_readd_records_origin_for_existing_builtin(self):
        self.fixture('hello')
        self.engine.add('hello')
        ledger = self.engine.load()
        del ledger['apps']['hello']['origin']
        self.engine.save(ledger)
        self.engine.add('hello')
        self.assertEqual('builtin', self.engine.load()['apps']['hello']['origin'])

    def test_folder_symlink_directory_changes_are_reinstalled(self):
        folder = Path(self.temporary.name) / 'folder-app'
        self.fixture('folder-app').rename(folder)
        shared = Path(self.temporary.name) / 'shared-assets'
        shared.mkdir()
        (shared / 'theme.css').write_text('old theme')
        (folder / 'web/assets').symlink_to(shared, target_is_directory=True)
        self.engine.add(str(folder))
        (shared / 'theme.css').write_text('new theme')
        self.engine.add(str(folder))
        self.assertEqual('new theme', (self.root / 'web/folder-app/assets/theme.css').read_text())

    def test_last_clone_survives_nginx_failure_and_readd_recovers(self):
        source, bare = self.source()
        with patch.object(engine, 'run', side_effect=self.real_git):
            self.engine.add(bare.as_uri())
            self.engine.remove('one')
            clone = Path(self.engine.load()['apps']['two']['repo'])
            self.fail_nginx = True
            with self.assertRaises(RuntimeError):
                self.engine.remove('two')
            self.assertTrue((clone / 'apps/two/app.toml').exists())
            self.fail_nginx = False
            self.engine.project()
            self.engine.add('two')
            self.assertTrue((self.root / 'web/two/index.html').exists())
            self.engine.remove('two')
            self.assertFalse(clone.exists())

    def test_failed_clone_cleanup_remains_recorded_for_retry(self):
        source, bare = self.source()
        with patch.object(engine, 'run', side_effect=self.real_git):
            self.engine.add(bare.as_uri())
            self.engine.remove('one')
            clone = Path(self.engine.load()['apps']['two']['repo'])
            original = self.engine.safe_remove
            def remove(path):
                if Path(path) == clone:
                    raise OSError('cleanup failure')
                return original(path)
            with patch.object(self.engine, 'safe_remove', side_effect=remove):
                with self.assertRaisesRegex(OSError, 'cleanup failure'):
                    self.engine.remove('two')
            self.assertEqual(str(clone), self.engine.load()['apps']['two']['repo'])
            self.assertTrue(clone.exists())
            self.engine.remove('two')
            self.assertFalse(clone.exists())
