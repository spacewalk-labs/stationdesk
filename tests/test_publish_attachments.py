"""External publish carries the share-dir files a page links to, so its download links work."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / 'apps/publish/server.py'
OWNER = 'owner@example.test'


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        env = {'STATIONDESK_SHARE': str(self.share), 'STATIONDESK_OWNER': OWNER,
               'STATIONDESK_FQDN': '', 'STATIONDESK_ROOT': str(self.root),
               'STATIONDESK_PUBLISH_PUBLIC_MODE': 'local',
               'STATIONDESK_PUBLISH_BASE_URL': 'https://docs.example.test',
               'STATIONDESK_PUBLISH_PUBLIC_DIR': str(self.root / 'public'),
               'STATIONDESK_PUBLISH_GATED_DIR': str(self.root / 'gated'),
               'STATIONDESK_PUBLISH_HTPASSWD_DIR': str(self.root / 'auth'),
               'STATIONDESK_PUBLISH_STATE_DIR': str(self.root / 'state')}
        context = patch.dict(os.environ, env)
        context.start()
        self.addCleanup(context.stop)
        spec = importlib.util.spec_from_file_location('publish_attachments', BACKEND)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)

    def publish(self, body):
        (self.share / 'doc.html').write_text(f'<html><head><title>T</title></head><body>{body}</body></html>')
        return self.app.publish_public('doc.html', 24, OWNER)

    def test_linked_file_travels_and_the_link_points_at_it(self):
        (self.share / 'demo.mp4').write_bytes(b'\0video')
        ok, result = self.publish('<a href="/demo.mp4" download>get</a> <a href="https://x.test/a.zip">ext</a>')
        self.assertTrue(ok, result)
        self.assertEqual(['demo.mp4'], result['attachments'])
        out = self.root / 'public' / result['slug']
        self.assertEqual(b'\0video', (out / 'demo.mp4').read_bytes())
        page = (out / 'index.html').read_text()
        self.assertIn('href="demo.mp4"', page)
        self.assertIn('href="https://x.test/a.zip"', page)

    def test_file_name_needing_escapes_keeps_a_working_link(self):
        (self.share / 'LH #1.mp4').write_bytes(b'v')
        ok, result = self.publish('<a href="LH%20%231.mp4">get</a>')
        self.assertTrue(ok, result)
        self.assertEqual(['LH #1.mp4'], result['attachments'])
        self.assertIn('href="LH%20%231.mp4"', (self.root / 'public' / result['slug'] / 'index.html').read_text())

    def test_total_limit_is_checked_before_reading(self):
        (self.share / 'a.bin').write_bytes(b'x' * 100)
        (self.share / 'b.bin').write_bytes(b'y' * 100)
        opened = []
        real_open = open
        def tracking_open(path, *args, **kwargs):
            opened.append(os.path.basename(str(path)))
            return real_open(path, *args, **kwargs)
        with patch.object(self.app, 'LOCAL_MAX_BYTES', 250), patch('builtins.open', tracking_open):
            ok, result = self.publish('<a href="a.bin">a</a><a href="b.bin">b</a>')
        self.assertFalse(ok)
        self.assertNotIn('b.bin', opened)

    def test_missing_file_and_html_links_are_not_attachments(self):
        (self.share / 'other.html').write_text('<title>O</title>')
        ok, result = self.publish('<a href="gone.pdf">x</a><a href="other.html">o</a><a href="#top">t</a>')
        self.assertTrue(ok, result)
        self.assertEqual([], result['attachments'])

    def test_path_outside_the_share_is_never_read(self):
        (self.root / 'secret.txt').write_text('no')
        ok, result = self.publish('<a href="../secret.txt">x</a><a href="..%2Fsecret.txt">y</a>')
        self.assertTrue(ok, result)
        self.assertEqual([], result['attachments'])

    def test_oversized_attachment_fails_before_reading(self):
        (self.share / 'big.bin').write_bytes(b'12345')
        with patch.object(self.app, 'MAX_BUNDLE_MEMBER_BYTES', 4):
            ok, result = self.publish('<a href="big.bin">x</a>')
        self.assertFalse(ok)
        self.assertIn('per-file limit', result)

    def test_total_over_the_limit_fails(self):
        (self.share / 'a.bin').write_bytes(b'x' * 100)
        with patch.object(self.app, 'LOCAL_MAX_BYTES', 150):
            ok, result = self.publish('<a href="a.bin">x</a>')
        self.assertFalse(ok)
        self.assertIn('total size', result)


if __name__ == '__main__':
    unittest.main()
