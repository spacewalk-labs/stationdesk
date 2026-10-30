"""Installer failure isolation and preservation of preexisting user skills."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PRODUCT = Path(__file__).parents[1]


class InstallTests(unittest.TestCase):
    def test_one_failed_vendor_does_not_skip_other_installers(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            curl = base / 'curl'
            curl.write_text('#!/bin/sh\nprintf "%s\\n" "$2" >> "$VENDOR_LOG"\ncase "$2" in *claude*) exit 22;; esac\nprintf "exit 0\\n"\n')
            curl.chmod(0o755)
            log = base / 'vendors'
            result = subprocess.run(['bash', str(PRODUCT / 'agents/install.sh')], env=os.environ | {'HOME':str(base), 'PATH':str(base)+':/usr/bin:/bin', 'VENDOR_LOG':str(log)}, capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertEqual(4, len(log.read_text().splitlines()))
            self.assertIn('failed: claude', result.stderr)

    def test_retry_preserves_healthy_agents_and_repairs_a_broken_one(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            curl = base / 'curl'
            curl.write_text('''#!/bin/sh
printf '%s\\n' "$2" >> "$VENDOR_LOG"
case "$2" in
    *claude*) agent=claude ;;
    *codex*) agent=codex ;;
    *opencode*) agent=opencode ;;
    *antigravity*) agent=agy ;;
    *) exit 22 ;;
esac
printf 'mkdir -p "$HOME/.local/bin"\n'
printf 'printf "#!/bin/sh\\\\nexit 0\\\\n" > "$HOME/.local/bin/%s"\n' "$agent"
printf 'chmod +x "$HOME/.local/bin/%s"\n' "$agent"
''')
            curl.chmod(0o755)
            log = base / 'vendors'
            env = os.environ | {'HOME':str(base), 'PATH':str(base)+':/usr/bin:/bin', 'VENDOR_LOG':str(log)}
            def install():
                return subprocess.run(['bash', str(PRODUCT / 'agents/install.sh')], env=env,
                                      capture_output=True, text=True, timeout=10)
            first = install()
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(4, len(log.read_text().splitlines()))
            retry = install()
            self.assertEqual(0, retry.returncode, retry.stderr)
            self.assertEqual(4, len(log.read_text().splitlines()))
            (base / '.local/bin/agy').write_text('#!/bin/sh\nexit 1\n')
            repair = install()
            self.assertEqual(0, repair.returncode, repair.stderr)
            requests = log.read_text().splitlines()
            self.assertEqual(5, len(requests))
            self.assertIn('antigravity', requests[-1])

    def test_publish_preserves_existing_skill_before_creating_any_links(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / '.agents/skills/share-docs'
            target.parent.mkdir(parents=True)
            target.symlink_to('/personal/skill')
            result = subprocess.run(['bash', str(PRODUCT / 'apps/publish/install.sh')], env=os.environ | {'HOME':str(base), 'STATIONDESK_SHARE':str(base/'share'), 'STATIONDESK_APP_DIR':str(PRODUCT/'apps/publish')}, capture_output=True)
            self.assertEqual(1, result.returncode)
            self.assertEqual('/personal/skill', os.readlink(target))
            self.assertFalse((base/'.claude/skills/share-docs').exists())
