"""Verify the distributed recovery modules, pins and all-or-nothing integration."""
import hashlib
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

APP = pathlib.Path(__file__).resolve().parents[1] / 'apps/paseo'
BUNDLE = APP / 'vendor/guarded-0.8.0'


class CrashRecoveryBundleTest(unittest.TestCase):
    def test_archive_pins_and_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            members = [line.split()[1].removeprefix('@getpaseo/server/')
                       for line in (BUNDLE / 'INSTALLED_SHA256SUMS').read_text().splitlines()
                       if line.split()[1].startswith('@getpaseo/server/')]
            with tarfile.open(BUNDLE / 'getpaseo-server-0.8.0.tgz') as archive:
                archive.extractall(root, members=[archive.getmember('package/' + name) for name in members], filter='data')
            package = root / 'package'
            for line in (BUNDLE / 'INSTALLED_SHA256SUMS').read_text().splitlines():
                digest, filename = line.split()
                if filename.startswith('@getpaseo/server/'):
                    self.assertEqual(digest, hashlib.sha256((package / filename.removeprefix('@getpaseo/server/')).read_bytes()).hexdigest(), filename)
            agent = package / 'dist/server/server/agent'
            self.assertEqual((APP / 'patches/crash-recovery.js').read_bytes(), (agent / 'crash-recovery.js').read_bytes())
            targets = [agent / 'agent-storage.js', agent / 'agent-manager.js', agent.parent / 'bootstrap.js', agent / 'lifecycle-command.js']
            patcher = ['node', str(APP / 'patches/crash-recovery.mjs'), *map(str, targets)]
            self.assertEqual(10, subprocess.run(patcher, capture_output=True).returncode)
            originals = [p.read_bytes() for p in targets]
            targets[0].write_text(targets[0].read_text().replace('[paseo-crash-recovery]', '[other]'))
            self.assertEqual(20, subprocess.run(patcher, capture_output=True).returncode)
            self.assertFalse(list(package.rglob('*.paseo-new.mjs')))
            # All markers missing plus upstream drift must still write no candidate.
            for target, original in zip(targets, originals):
                target.write_bytes(original.replace(b'[paseo-crash-recovery]', b'[other]'))
            targets[2].write_text(targets[2].read_text().replace('await agentStorage.initialize();', 'await changedStorage.initialize();'))
            before = [p.read_bytes() for p in targets]
            self.assertEqual(20, subprocess.run(patcher, capture_output=True).returncode)
            self.assertEqual(before, [p.read_bytes() for p in targets])
            self.assertFalse(list(package.rglob('*.paseo-new.mjs')))


if __name__ == '__main__':
    unittest.main()
