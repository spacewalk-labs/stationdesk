import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]


def identity(status, box):
    script = re.search(r"identity=\$\(python3 - \"\$root\" <<'PY'\n(.*?)\nPY\n", (PRODUCT / 'install.sh').read_text(), re.S).group(1)
    with tempfile.TemporaryDirectory() as base:
        base = Path(base)
        (base / 'bin').mkdir()
        fake = base / 'bin/tailscale'
        fake.write_text(f"#!/bin/sh\necho '{status}'\n")
        fake.chmod(0o755)
        (base / 'root/state').mkdir(parents=True)
        if box is not None:
            (base / 'root/state/box.toml').write_text(box)
        return subprocess.run(['python3', '-', str(base / 'root')], input=script, text=True, capture_output=True,
                              env=os.environ | {'PATH': f'{base / "bin"}:/usr/bin:/bin'})


class InstallIdentity(unittest.TestCase):
    def test_logged_out_tailscale_falls_back_to_box_toml(self):
        # `tailscale status --json` exits 0 with null Self/User while logged out (NeedsLogin).
        result = identity('{"BackendState":"NeedsLogin","Self":null,"User":null}',
                          'owner = "a@example.com"\nfqdn = "box.example.ts.net"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'box.example.ts.net')

    def test_tagged_node_needs_box_toml_owner(self):
        # A tagged node's User record is the node itself, not a person.
        status = ('{"Self":{"UserID":7,"DNSName":"box.example.ts.net.","Tags":["tag:mgmt"]},'
                  '"User":{"7":{"LoginName":"box.example.ts.net"}}}')
        self.assertNotEqual(identity(status, None).returncode, 0)
        result = identity(status, 'owner = "a@example.com"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'box.example.ts.net')


if __name__ == '__main__':
    unittest.main()
