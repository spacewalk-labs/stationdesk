"""Keep the Codex child-activity fix in the reproducible, installed bundle."""
import hashlib
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

APP = pathlib.Path(__file__).resolve().parents[1] / 'apps/paseo'
BUNDLE = APP / 'vendor/guarded-0.8.0'
MEMBER = 'dist/server/server/agent/providers/codex-app-server-agent.js'


class SubagentActivityBundleTest(unittest.TestCase):
    def test_shipped_module_and_patch_contract(self):
        with tarfile.open(BUNDLE / 'getpaseo-server-0.8.0.tgz') as archive:
            data = archive.extractfile('package/' + MEMBER).read()
        pins = {name: digest for digest, name in
                (line.split() for line in (BUNDLE / 'INSTALLED_SHA256SUMS').read_text().splitlines())}
        self.assertEqual(pins['@getpaseo/server/' + MEMBER], hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'codex-agent.js'
            target.write_bytes(data)
            command = ['node', str(APP / 'patches/codex-subagent-activity.mjs'), str(target)]
            self.assertEqual(10, subprocess.run(command, capture_output=True).returncode)
            self.assertFalse(pathlib.Path(str(target) + '.paseo-new.mjs').exists())
            # An unsupported upstream module must remain intact, with no partial candidate.
            target.write_bytes(data.replace(b'[paseo-codex-subagent-activity]', b'[other]'))
            before = target.read_bytes()
            self.assertEqual(20, subprocess.run(command, capture_output=True).returncode)
            self.assertEqual(before, target.read_bytes())
            self.assertFalse(pathlib.Path(str(target) + '.paseo-new.mjs').exists())


if __name__ == '__main__':
    unittest.main()
