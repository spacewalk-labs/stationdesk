"""Real CLI processes share fixture files; external services are replaced in each process."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


RUNNER = '''
import importlib.machinery, importlib.util, json, os, pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
role = sys.argv[2]
loader = importlib.machinery.SourceFileLoader('stationdesk', str(root / 'bin/stationdesk'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
original_init = module.Engine.__init__
def fixture_init(self, directory):
    original_init(self, directory)
    self.unit_dir = root / 'fixture-units'
module.Engine.__init__ = fixture_init

def external(args, **kwargs):
    with (root / 'events').open('a') as stream:
        stream.write(json.dumps([role, args]) + '\\n')
    if args[0] == 'bash':
        (root / 'install-entered').touch()
        while not (root / 'release-install').exists():
            time.sleep(.01)
    output = 'a' * 40 if args[:2] == ['git', 'rev-parse'] else '{}'
    return subprocess.CompletedProcess(args, 0, output, '')
module.run = external
(root / (role + '.started')).touch()
sys.argv = ['stationdesk', *sys.argv[3:]]
sys.exit(module.main())
'''


class CliConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'product'
        for folder in ['bin', 'state', 'nginx', 'hub', 'apps/slow/web']:
            (self.root / folder).mkdir(parents=True)
        product = Path(__file__).parents[1]
        shutil.copyfile(product / 'bin/stationdesk', self.root / 'bin/stationdesk')
        shutil.copytree(product / 'lib', self.root / 'lib', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(product / 'nginx/site.tmpl', self.root / 'nginx/site.tmpl')
        (self.root / 'state/box.toml').write_text('owner="owner@example.test"\n')
        for app in [self.root / 'hub', self.root / 'apps/slow']:
            (app / 'app.toml').write_text('label="Fixture"\nicon="icon.svg"\nport=19920\n')
            (app / 'icon.svg').write_text('<svg/>')
        (self.root / 'apps/slow/install.sh').write_text('# The runner controls this external hook.\n')
        self.runner = self.root / 'runner.py'
        self.runner.write_text(RUNNER)
        self.processes = []
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def start(self, role, *arguments):
        process = subprocess.Popen([sys.executable, str(self.runner), str(self.root), role, *arguments],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        self.wait_for(self.root / (role + '.started'))
        return process

    def wait_for(self, path):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(path.exists(), str(path))

    def finish(self, process):
        out, err = process.communicate(timeout=5)
        self.assertEqual(0, process.returncode, err)
        return out

    def test_long_install_queues_project_and_link_but_allows_list_and_doctor(self):
        installer = self.start('installer', 'add', 'slow')
        self.wait_for(self.root / 'install-entered')
        projector = self.start('projector', 'project')
        linker = self.start('linker', 'link', 'add', 'Concurrent link', 'https://example.test')
        time.sleep(.1)
        self.assertIsNone(projector.poll())
        self.assertIsNone(linker.poll())
        events = [json.loads(line) for line in (self.root / 'events').read_text().splitlines()]
        self.assertFalse(any(role in ('projector', 'linker') for role, _ in events))
        self.assertEqual('{}', self.finish(self.start('reader', 'list', '--json')).strip())
        doctor = self.start('doctor', 'doctor')
        output, error = doctor.communicate(timeout=5)
        self.assertEqual(1, doctor.returncode, error)  # No installed apps to check yet.
        self.assertIn('확인 0 · 실패 0', output)
        (self.root / 'release-install').touch()
        for process in [installer, projector, linker]:
            self.finish(process)
        apps = json.loads((self.root / 'state/installed.json').read_text())['apps']
        self.assertIn('slow', apps)
        link = next(key for key, entry in apps.items() if entry['kind'] == 'link')
        projection = json.loads((self.root / 'web/hub/__stationdesk-hub.json').read_text())['apps']
        self.assertIn('slow', projection)
        self.assertIn(link, projection)

    def test_killed_cli_releases_the_kernel_lock(self):
        installer = self.start('installer', 'add', 'slow')
        self.wait_for(self.root / 'install-entered')
        projector = self.start('projector', 'project')
        time.sleep(.1)
        self.assertIsNone(projector.poll())
        installer.kill()
        installer.communicate(timeout=5)
        self.finish(projector)
