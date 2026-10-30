"""Exercise the native Ubuntu window's completion and retry decisions under Xvfb."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest

try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipUnless(tk and os.environ.get('DISPLAY'), 'Tk and an X display are required')
class GuiInstallerWindowTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).parents[1] / 'installer/linux/installer.py'
        spec = importlib.util.spec_from_file_location('stationdesk_installer', source)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.resources = Path(self.temp.name)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.app = self.module.Installer(self.root, self.resources)
        self.root.update()

    def backend(self, script):
        (self.resources / 'setup.sh').write_text('#!/bin/bash\n' + script)

    def finish(self):
        deadline = time.monotonic() + 5
        while self.app.busy:
            self.root.update()
            time.sleep(.02)
            self.assertLess(time.monotonic(), deadline, 'GUI backend did not settle')
        self.root.update()

    def test_exit_zero_without_address_stays_failed_not_ready(self):
        self.backend('echo "No verified address"\n')
        self.app.step = 4
        self.app.status['ready_url'] = 'https://stale.example.test/'
        self.app.run('install')
        self.finish()
        self.assertEqual(4, self.app.step)
        self.assertTrue(self.app.failed)
        self.assertNotIn('ready_url', self.app.status)

    def test_failure_then_retry_keeps_paseo_choice_and_uses_verified_result(self):
        self.backend('''printf '%s\\n' "$@" > "$(dirname "$0")/argv"
if [ ! -e "$(dirname "$0")/tried" ]; then
 touch "$(dirname "$0")/tried"
 printf 'SD_STATUS\\tready_url\\thttps://premature.example.test/\\n'
 exit 1
fi
printf 'SD_STATUS\\tready_url\\thttps://ready.example.test/\\n'
''')
        self.app.step = 4
        self.app.paseo.set(False)
        self.app.run('install')
        self.finish()
        self.assertTrue(self.app.failed)
        self.assertEqual(4, self.app.step)
        self.app.next()
        self.finish()
        self.assertFalse(self.app.failed)
        self.assertEqual(5, self.app.step)
        self.assertEqual('https://ready.example.test/', self.app.status['ready_url'])
        self.assertEqual(['install', '--no-paseo'], (self.resources / 'argv').read_text().splitlines())

    def test_language_switch_preserves_selection(self):
        self.app.step = 2
        self.app.paseo.set(False)
        self.app.language.set('한국어')
        self.app.render()
        self.root.update()
        self.assertFalse(self.app.paseo.get())
        self.assertEqual('완료', self.app.t('Ready', '완료'))
        for url in ('file:///etc/passwd', 'http://example.test', 'https://user:password@example.test'):
            self.assertFalse(self.module.allowed_url(url))
        self.assertFalse(self.module.allowed_url('https://example.test/login', login=True))
        self.assertTrue(self.module.allowed_url('https://login.tailscale.com/a/test', login=True))

    def test_apps_notice_and_navigation_fit_the_minimum_window_in_both_languages(self):
        self.root.geometry('720x560')
        self.app.step = 2
        for language in ('English', '한국어'):
            with self.subTest(language=language):
                self.app.language.set(language)
                self.app.render()
                self.root.update()
                widgets, pending = [], [self.root]
                while pending:
                    widget = pending.pop()
                    widgets.append(widget)
                    pending.extend(widget.winfo_children())
                notice = next(widget for widget in widgets if isinstance(widget, tk.Label)
                              and str(widget.cget('text')).startswith('Claude Code'))
                for widget in (notice, self.app.primary_button):
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                         self.root.winfo_rooty() + self.root.winfo_height())
