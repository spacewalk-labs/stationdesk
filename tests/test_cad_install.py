"""CAD installer unit fixtures: local tiny assets and a fake download command."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

PRODUCT = Path(__file__).parents[1]


class CadInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.app = self.base / 'app'
        shutil.copytree(PRODUCT / 'apps/cad', self.app)
        self.assets = self.base / 'assets'
        self.assets.mkdir()
        web = self.assets / 'index.html'
        web.write_text('tiny web fixture')
        archive = self.assets / 'web'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(web, arcname='index.html')
        cli = self.assets / 'cli-x86_64'
        cli.write_text('tiny cli fixture')
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        (self.app / 'VERSION').write_text(
            f'web {archive} {sha(archive)}\n'
            f'cli-x86_64 {cli} {sha(cli)}\n')
        (self.app / 'web-bundle/VERSION').write_text(f'{sha(web)}  dist/index.html\n')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        (self.bin / 'curl').write_text(
            '#!/bin/bash\nprintf "download\\n" >> "$DOWNLOAD_LOG"\n'
            '[[ -z "${FAIL_DOWNLOAD:-}" ]] || exit 22\n'
            'cp "${@: -3:1}" "${@: -1}"\n')
        (self.bin / 'uname').write_text('#!/bin/bash\necho x86_64\n')
        for path in self.bin.iterdir():
            path.chmod(0o755)
        self.log = self.base / 'downloads'
        self.env = os.environ | {'HOME': str(self.base / 'home'),
                                 'STATIONDESK_ROOT': str(self.root),
                                 'STATIONDESK_APP_DIR': str(self.app),
                                 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                                 'DOWNLOAD_LOG': str(self.log), 'GH_TOKEN': ''}
        self.install()
        self.log.write_text('')

    def install(self, **extra):
        result = subprocess.run(['bash', str(self.app / 'install.sh')], env=self.env | extra,
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def snapshot(self):
        return {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_same_installed_hashes_download_zero_and_rewrite_zero(self):
        before = self.snapshot()
        self.install(FAIL_DOWNLOAD='yes')
        self.assertEqual('', self.log.read_text())
        self.assertEqual(before, self.snapshot())

    def test_only_changed_component_downloaded(self):
        (self.root / 'runtime/cad/cadcraft-cli').write_text('damaged')
        self.install()
        self.assertEqual('download\n', self.log.read_text())
        self.assertEqual('tiny cli fixture', (self.root / 'runtime/cad/cadcraft-cli').read_text())

    def test_failed_download_preserves_existing_files(self):
        (self.root / 'web/cad/app/index.html').write_text('damaged')
        before = self.snapshot()
        result = subprocess.run(['bash', str(self.app / 'install.sh')],
                                env=self.env | {'FAIL_DOWNLOAD': 'yes'}, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, self.snapshot())

    def test_later_component_failure_preserves_valid_web_installation(self):
        (self.assets / 'cli-x86_64').write_text('wrong download checksum')
        (self.root / 'runtime/cad/cadcraft-cli').write_text('old cli')
        (self.root / 'web/cad/app/index.html').write_text('old web')
        before = self.snapshot()
        result = subprocess.run(['bash', str(self.app / 'install.sh')], env=self.env, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, self.snapshot())
