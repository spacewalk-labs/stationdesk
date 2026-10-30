"""Existing installers must leave identical installed files in place on re-add."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

PRODUCT = Path(__file__).parents[1]


class InstallIdempotenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.env = os.environ | {'HOME': str(self.base / 'home'),
                                 'STATIONDESK_ROOT': str(self.root),
                                 'STATIONDESK_STATE': str(self.root / 'state'),
                                 'STATIONDESK_PORT': '19940',
                                 'PASEO_HOME': str(self.base / 'home/.paseo')}

    def install_twice(self, name, **extra):
        app = self.base / name
        env = self.env | {'STATIONDESK_APP_DIR': str(app)} | extra
        def install():
            result = subprocess.run(['bash', str(app / 'install.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertIn(result.returncode, (0, 2) if name == 'agy-acp' else (0,),
                          result.stdout + result.stderr)
        def snapshot():
            return {p.relative_to(self.base): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                    for base in (self.root, self.base / 'home') for p in base.rglob('*') if p.is_file()}
        install()
        before = snapshot()
        install()
        self.assertEqual(before, snapshot())

    def test_fileview_readd_rewrites_zero(self):
        shutil.copytree(PRODUCT / 'apps/fileview', self.base / 'fileview')
        runtime = self.root / 'runtime/fileview'
        runtime.mkdir(parents=True)
        binary = runtime / 'filebrowser'
        binary.write_text('#!/bin/sh\necho Filebrowser v2.63.18\n')
        binary.chmod(0o755)
        database = self.root / 'state/fileview/fb.db'
        database.parent.mkdir(parents=True)
        database.write_text('existing database')
        self.install_twice('fileview')

    def test_bench_readd_rewrites_zero(self):
        app = self.base / 'bench'
        shutil.copytree(PRODUCT / 'apps/bench', app)
        archive = self.base / 'fixture.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('figcraft-web-1.0/index.html', 'tiny bench fixture')
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        (app / 'VERSION').write_text(f'figcraft 1.0 {archive} {sha}\n')
        binpath = self.base / 'bin'
        binpath.mkdir()
        curl = binpath / 'curl'
        curl.write_text('#!/bin/bash\ncp "${@: -1}" "${@: -2:1}"\n')
        curl.chmod(0o755)
        self.install_twice('bench', STATIONDESK_BENCH_APPS='figcraft',
                           PATH=f'{binpath}:{os.environ["PATH"]}')

    def fake_node_tools(self):
        binpath = self.base / 'bin'
        binpath.mkdir()
        (binpath / 'node').write_text('#!/bin/sh\nif [ "$1" = -p ]; then echo 24; fi\n')
        (binpath / 'npm').write_text(
            '#!/bin/sh\necho npm >> "$HOME/npm-calls"\n'
            '[ "${FAIL_NPM:-}" != "$1" ] || exit 1\n'
            'mkdir -p node_modules/playwright node_modules/ws dist\n'
            'if [ "$1" = run ]; then\n'
            '  if [ -f src/e1-version ]; then cat src/e1-version > dist/cli.js; else echo built > dist/cli.js; fi\n'
            'fi\n')
        for path in binpath.iterdir():
            path.chmod(0o755)
        (self.base / 'home').mkdir()
        return f'{binpath}:{os.environ["PATH"]}'

    def test_paseo_browse_host_readd_rewrites_zero(self):
        shutil.copytree(PRODUCT / 'apps/paseo/browse-host', self.base / 'browse-host')
        self.install_twice('browse-host', PATH=self.fake_node_tools(), PASEO_WEBUI_DIR=str(self.base / 'webui'))
        self.assertEqual('npm\n', (self.base / 'home/npm-calls').read_text())

    def test_paseo_agy_readd_rewrites_zero(self):
        shutil.copytree(PRODUCT / 'apps/paseo/agy-acp', self.base / 'agy-acp')
        self.install_twice('agy-acp', PATH=self.fake_node_tools(), STATIONDESK_PASEO_AGY='true')
        self.assertEqual('npm\nnpm\n', (self.base / 'home/npm-calls').read_text())

    def test_publish_readd_leaves_skill_symlinks(self):
        app = self.base / 'publish'
        shutil.copytree(PRODUCT / 'apps/publish', app)
        env = self.env | {'STATIONDESK_APP_DIR': str(app), 'STATIONDESK_SHARE': str(self.root / 'share'),
                              'STATIONDESK_STATE': str(self.root / 'state'), 'STATIONDESK_PORT': '19924',
                              'STATIONDESK_FQDN': ''}
        def install():
            subprocess.run(['bash', str(app / 'install.sh')], env=env, check=True, capture_output=True)
        # The engine now owns link creation; the app installer leaves them alone.
        links = [self.base / f'home/{name}/skills/share-docs' for name in ('.claude', '.agents')]
        for link in links:
            link.parent.mkdir(parents=True)
            link.symlink_to(app / 'share-docs')
        install()
        before = [(p.lstat().st_ino, p.lstat().st_mtime_ns) for p in links]
        install()
        self.assertEqual(before, [(p.lstat().st_ino, p.lstat().st_mtime_ns) for p in links])

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the actual companion installer')
    def test_paseo_webui_companion_readd_rewrites_zero(self):
        webui = self.root / 'webui'
        webui.mkdir()
        source = PRODUCT / 'apps/paseo/browse-host'
        program = "require(process.argv[1]).installCompanion(process.argv[2], process.argv[3])"
        args = ['node', '-e', program, str(source / 'bin/patch-web-ui.js'),
                str(webui), str(source / 'web/browse-view-client.js')]
        subprocess.run(args, check=True, capture_output=True)
        target = webui / 'browse-view-client.js'
        before = target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns
        subprocess.run(args, check=True, capture_output=True)
        self.assertEqual(before, (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns))

    def test_agy_failed_update_keeps_installation_and_retries_build(self):
        app = self.base / 'agy-acp'
        shutil.copytree(PRODUCT / 'apps/paseo/agy-acp', app)
        (app / 'src/e1-version').write_text('version-one')
        path = self.fake_node_tools()
        self.install_twice('agy-acp', PATH=path, STATIONDESK_PASEO_AGY='true')
        installed = self.root / 'runtime/agy-acp'
        (app / 'src/e1-version').write_text('version-two')
        env = self.env | {'STATIONDESK_APP_DIR': str(app), 'PATH': path, 'STATIONDESK_PASEO_AGY': 'true'}
        args = ['bash', str(app / 'install.sh')]
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                  for p in installed.rglob('*') if p.is_file()}
        failed = subprocess.run(args, env=env | {'FAIL_NPM': 'run'}, capture_output=True)
        self.assertEqual(1, failed.returncode)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                                  for p in installed.rglob('*') if p.is_file()})
        subprocess.run(args, env=env, check=True, capture_output=True)
        self.assertEqual('version-two', (installed / 'dist/cli.js').read_text())
        self.assertEqual('version-two', (installed / 'src/e1-version').read_text())

    def test_browse_failed_dependency_update_retries_install(self):
        app = self.base / 'browse-host'
        shutil.copytree(PRODUCT / 'apps/paseo/browse-host', app)
        path = self.fake_node_tools()
        self.install_twice('browse-host', PATH=path, PASEO_WEBUI_DIR=str(self.base / 'webui'))
        package = app / 'package.json'
        updated = json.loads(package.read_text()) | {'version': '0.2.0'}
        package.write_text(json.dumps(updated))
        installed = self.root / 'runtime/paseo-browse-host'
        before = (installed / 'package.json').read_bytes()
        env = self.env | {'STATIONDESK_APP_DIR': str(app), 'PATH': path,
                          'PASEO_WEBUI_DIR': str(self.base / 'webui')}
        args = ['bash', str(app / 'install.sh')]
        failed = subprocess.run(args, env=env | {'FAIL_NPM': 'install'}, capture_output=True)
        self.assertEqual(1, failed.returncode)
        self.assertEqual(before, (installed / 'package.json').read_bytes())
        subprocess.run(args, env=env, check=True, capture_output=True)
        self.assertEqual(updated, json.loads((installed / 'package.json').read_text()))
