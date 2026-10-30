"""Exercise the original terminal APIs through the StationDesk owner gate."""
import base64
from email.message import Message
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from test_devterm import devterm


class TerminalParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, key, value in (
                (devterm, 'OWNER', 'owner@example.test'),
                (devterm.terminal, 'UPLOADS', str(self.root / 'uploads')),
                (devterm.terminal, 'PREFS_DIR', str(self.root / 'prefs')),
                (devterm.terminal, 'PREFS_PATH', str(self.root / 'prefs/tabs.json')),
                (devterm.terminal, 'FILEVIEW_HOME', str(self.root))):
            patcher = patch.object(module, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, path, data=None, method='GET', raw=None, extra=None):
        handler = object.__new__(devterm.Handler)
        handler.path, handler.command = path, method
        handler.headers = Message()
        headers = {'Host': 'devterm.test', 'Tailscale-User-Login': 'owner@example.test',
                   'Origin': 'http://devterm.test', 'Content-Type': 'application/json'}
        headers.update(extra or {})
        for key, value in headers.items():
            handler.headers[key] = value
        body = raw if raw is not None else json.dumps(data or {}).encode()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        replies = []
        def reply(code, value, content_type='application/json'):
            if content_type.startswith('application/json') and isinstance(value, bytes):
                value = json.loads(value)
            replies.append((code, value))
        handler.reply = reply
        handler.dispatch()
        return replies[0]

    def test_shell_attach_preserves_exact_names_and_start_folder(self):
        binary = self.root / 'tmux'
        binary.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                          'open(os.environ["TEST_TMUX_ARGS"], "w").write(json.dumps(sys.argv[1:]))\n')
        binary.chmod(0o755)
        recorded = self.root / 'args.json'
        shell = Path(devterm.__file__).parents[1] / 'bin/devterm-shell'
        for name in ('한글 작업', 'space name', '-leading'):
            subprocess.run(['bash', str(shell), name, str(self.root)], check=True,
                           env={**os.environ, 'PATH': str(self.root) + ':' + os.environ['PATH'],
                                'TEST_TMUX_ARGS': str(recorded)})
            self.assertEqual(['new', '-A', '-s', name, '-c', str(self.root)],
                             json.loads(recorded.read_text()))

    def test_tab_preferences_survive_across_requests(self):
        prefs = {'order': ['main', 'second'], 'hidden': ['third'], 'colors': {'main': 'red'},
                 'theme': 'dark', 'agents': [{'name': 'Shell', 'command': 'bash'}]}
        self.assertEqual((200, {'ok': True}), self.request('/tab-prefs', prefs, 'POST'))
        self.assertEqual((200, prefs), self.request('/tab-prefs'))
        self.assertEqual(403, self.request('/tab-prefs', {}, 'POST',
                                         extra={'Origin': 'https://elsewhere.test'})[0])

    def test_upload_cleanup_preserves_user_files_and_symlinks(self):
        uploads = self.root / 'uploads'
        uploads.mkdir()
        manual = uploads / 'important-report.txt'
        generated = uploads / 'file001-20261023-010203.txt'
        image = uploads / 'image001-20261023-010203.jpg'
        malformed = uploads / 'file001-20261023-010203.txt.backup'
        for path in (manual, generated, image, malformed):
            path.write_bytes(b'keep unless generated')
            os.utime(path, (1, 1))
        link = uploads / 'image002-20261023-010203.jpg'
        link.symlink_to(manual)
        code, result = self.request('/upload-file', method='POST', raw=b'new upload',
                                    extra={'X-Filename': 'new.txt'})
        self.assertEqual(200, code)
        self.assertTrue(result['ok'])
        self.assertTrue(manual.exists())
        self.assertTrue(malformed.exists())
        self.assertTrue(link.is_symlink())
        self.assertFalse(generated.exists())
        self.assertFalse(image.exists())

    def test_upload_image_recent_thumbnail_and_raw_file(self):
        image = b'\xff\xd8\xff\xe0unit-jpeg\xff\xd9'
        code, uploaded = self.request('/upload-image',
                                      {'image': base64.b64encode(image).decode()}, 'POST')
        self.assertEqual(200, code)
        self.assertTrue(uploaded['ok'])
        self.assertEqual(str(self.root / 'uploads' / uploaded['name']), uploaded['path'])
        code, recent = self.request('/recent-images')
        self.assertEqual(uploaded['name'], recent['images'][0]['name'])
        self.assertEqual(uploaded['path'], recent['images'][0]['path'])
        self.assertEqual((200, image), self.request('/recent-image?name=' + uploaded['name']))
        binary = b'\x00\xffarbitrary-file'
        code, uploaded = self.request('/upload-file', method='POST', raw=binary,
                                      extra={'X-Filename': '../../notes.txt'})
        self.assertEqual(200, code)
        self.assertTrue(uploaded['name'].endswith('.txt'))
        self.assertEqual(binary, (self.root / 'uploads' / uploaded['name']).read_bytes())
        self.assertEqual(404, self.request('/recent-image?name=../../notes.txt')[0])

    def test_file_uploaded_images_are_annotation_candidates(self):
        for suffix in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            with self.subTest(suffix=suffix):
                content = b'uploaded-image-' + suffix.encode()
                code, uploaded = self.request('/upload-file', method='POST', raw=content,
                                              extra={'X-Filename': 'drawing.' + suffix})
                self.assertEqual(200, code)
                code, recent = self.request('/recent-images')
                self.assertIn(uploaded['name'], [item['name'] for item in recent['images']])
                self.assertEqual((200, content), self.request('/recent-image?name=' + uploaded['name']))
        self.assertEqual(404, self.request('/recent-image?name=../drawing.png')[0])

    def test_pane_split_capture_and_layout_reach_tmux(self):
        with patch.object(devterm.terminal, '_tmux_r', new_callable=AsyncMock,
                          return_value=(True, '')) as tmux, \
             patch.object(devterm.terminal, '_pane_status', new_callable=AsyncMock,
                          return_value=(True, False, 2)):
            self.assertEqual(200, self.request('/pane', {'session': 'main', 'action': 'split-h'}, 'POST')[0])
            tmux.assert_awaited_with(None, 'split-window', '-h', '-t', 'main')
            self.assertEqual(200, self.request('/layout', {'session': 'main', 'layout': 'tiled'}, 'POST')[0])
            tmux.assert_awaited_with(None, 'select-layout', '-t', 'main', 'tiled')
        with patch.object(devterm.terminal, '_tmux_out_r', new_callable=AsyncMock,
                          return_value=(True, 'terminal scrollback')):
            code, captured = self.request('/pane', {'session': 'main', 'action': 'capture'}, 'POST')
            self.assertEqual(200, code)
            self.assertEqual('terminal scrollback', captured['text'])

    def test_local_named_session_operations_preserve_names(self):
        for name in ('한글 작업', 'space name', '-leading'):
            with patch.object(devterm.terminal, '_tmux_r', new_callable=AsyncMock,
                              return_value=(True, '')) as run, \
                 patch.object(devterm.terminal, '_pane_status', new_callable=AsyncMock,
                              return_value=(True, False, 2)):
                self.assertEqual(200, self.request('/pane', {'session': name, 'action': 'split-v'}, 'POST')[0])
                run.assert_awaited_with(None, 'split-window', '-v', '-t', name)
                self.assertEqual(200, self.request('/layout', {'session': name, 'layout': 'tiled'}, 'POST')[0])
                run.assert_awaited_with(None, 'select-layout', '-t', name, 'tiled')
            with patch.object(devterm.terminal, '_tmux', new_callable=AsyncMock,
                              return_value=(True, '')) as run:
                self.assertEqual(200, self.request('/rename-session', {'from': name, 'to': name + '2'}, 'POST')[0])
                run.assert_awaited_with('rename-session', '-t', name, name + '2')
                self.assertEqual(200, self.request('/kill-session', {'name': name}, 'POST')[0])
                run.assert_awaited_with('kill-session', '-t', name)
            with patch.object(devterm.terminal, '_tmux_out', new_callable=AsyncMock,
                              return_value=(True, str(self.root))) as run:
                import asyncio
                self.assertEqual(str(self.root), asyncio.run(devterm.terminal._pane_cwd(name)))
                run.assert_awaited_with('display-message', '-p', '-t', name, '#{pane_current_path}')

    def test_fileview_resolves_home_and_refuses_escaping_symlink(self):
        inside = self.root / 'notes.md'
        inside.write_text('inside')
        (self.root / 'outside').symlink_to('/etc/hostname')
        with patch.object(devterm.terminal, '_pane_cwd', new_callable=AsyncMock, return_value=None):
            code, result = self.request('/resolve?path=' + str(inside))
            self.assertEqual(200, code)
            self.assertTrue(result['url'].startswith('/fileview/?path='))
            self.assertEqual('outside_home', self.request('/resolve?path=' + str(self.root / 'outside'))[1]['reason'])


if __name__ == '__main__':
    unittest.main()
