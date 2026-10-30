"""Public seed boundary and the GUI's real shell-adapter protocol."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).parents[1] / 'installer'


class GuiInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = os.environ | {'HOME': str(self.home), 'PATH': f'{self.bin}:{os.environ["PATH"]}'}
        self.command('sudo', 'exit 0')

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def run_setup(self, *args):
        return subprocess.run(['bash', str(INSTALLER / 'setup.sh'), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def tailscale(self, status):
        self.command('tailscale', "cat <<'JSON'\n" + json.dumps(status) + '\nJSON')

    def test_owner_identity_is_read_from_actual_tailscale_user(self):
        self.tailscale({'BackendState': 'Running', 'Self': {'UserID': 42, 'DNSName': 'my-box.example.ts.net.'},
                        'User': {'42': {'LoginName': 'owner@example.test'}}})
        result = self.run_setup('check')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('SD_STATUS\tsigned_in\towner@example.test\n', result.stdout)
        self.assertNotIn('ready_url', result.stdout)

    def test_needs_login_is_not_installation_success(self):
        for status in ({'BackendState': 'NeedsLogin'},
                       {'BackendState': 'Running', 'Self': {'DNSName': 'tagged.example.ts.net.'}}):
            with self.subTest(status=status):
                self.tailscale(status)
                result = self.run_setup('check')
                self.assertEqual(0, result.returncode)
                self.assertIn('SD_STATUS\tneeds_login\ttrue', result.stdout)
                self.assertNotIn('ready_url', result.stdout)

    def test_unknown_operations_are_not_shell_commands(self):
        marker = self.base / 'unexpected'
        result = self.run_setup(f'check; touch {marker}')
        self.assertEqual(2, result.returncode)
        self.assertFalse(marker.exists())
        self.assertEqual(2, self.run_setup('install', '--anything').returncode)

    def test_mac_uses_only_dedicated_machine_and_preserves_argv(self):
        self.command('uname', 'echo Darwin')
        orb = self.home / '.orbstack/bin/orb'
        orb.parent.mkdir(parents=True)
        orb.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$HOME/orb-argv"\nexit 0\n')
        orb.chmod(0o755)
        result = self.run_setup('install', '--no-paseo')
        self.assertEqual(0, result.returncode, result.stderr)
        arguments = (self.home / 'orb-argv').read_text().splitlines()
        self.assertEqual(['info', 'stationdesk', '-m', 'stationdesk', 'bash'], arguments[:5])
        self.assertEqual('/mnt/mac' + str(INSTALLER / 'setup.sh'), arguments[5])
        self.assertEqual(['install', '--no-paseo'], arguments[6:])

    def test_seed_contains_only_product_and_no_private_git_history(self):
        source = self.base / 'product'
        source.mkdir()
        (source / 'install.sh').write_text('public installer')
        (self.base / 'private-token.txt').write_text('private sentinel')
        output = self.base / 'source.bundle'
        result = subprocess.run(['bash', str(INSTALLER / 'build-seed.sh'), str(source), str(output)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        clone = self.base / 'clone'
        subprocess.run(['git', 'clone', '--quiet', str(output), str(clone)], check=True, capture_output=True)
        self.assertEqual('public installer', (clone / 'install.sh').read_text())
        files = subprocess.check_output(['git', '-C', str(clone), 'ls-files'], text=True).splitlines()
        self.assertEqual(['install.sh'], files)
        self.assertEqual('1', subprocess.check_output(['git', '-C', str(clone), 'rev-list', '--count', 'HEAD'], text=True).strip())
