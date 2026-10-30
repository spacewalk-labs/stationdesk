"""Publish site name and favicon: read from the config environment, injected on external publish."""
import base64
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / 'apps/publish/server.py'
OWNER = 'owner@example.test'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
OWN_ICON = '<link rel="shortcut icon" href="data:image/png;base64,AAAA">'


class SiteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        self.env = {'STATIONDESK_SHARE': str(self.share), 'STATIONDESK_OWNER': OWNER,
                    'STATIONDESK_FQDN': '', 'STATIONDESK_ROOT': str(self.root),
                    'STATIONDESK_PUBLISH_PUBLIC_MODE': 'local',
                    'STATIONDESK_PUBLISH_BASE_URL': 'https://docs.example.test',
                    'STATIONDESK_PUBLISH_PUBLIC_DIR': str(self.root / 'public'),
                    'STATIONDESK_PUBLISH_GATED_DIR': str(self.root / 'gated'),
                    'STATIONDESK_PUBLISH_HTPASSWD_DIR': str(self.root / 'auth'),
                    'STATIONDESK_PUBLISH_STATE_DIR': str(self.root / 'state')}

    def load(self, **extra):
        context = patch.dict(os.environ, self.env | extra)
        context.start()
        self.addCleanup(context.stop)
        spec = importlib.util.spec_from_file_location('publish_site', BACKEND)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        return app

    def published(self, app, name='doc.html', **kwargs):
        ok, result = app.publish_public(name, 24, OWNER, **kwargs)
        self.assertTrue(ok, result)
        return (self.root / 'public' / result['slug'] / 'index.html').read_text()

    def test_defaults_are_the_product_name_and_icon(self):
        site = self.load().SITE
        self.assertEqual('Publish', site['name'])
        self.assertFalse(site['custom_name'])
        self.assertEqual('', site['favicon_source'])
        icon = (BACKEND.parent / 'icon.svg').read_bytes()
        self.assertEqual('data:image/svg+xml;base64,' + base64.b64encode(icon).decode(), site['favicon'])

    def test_configured_name_and_favicon_relative_to_state_dir(self):
        (self.root / 'state').mkdir()
        (self.root / 'state/brand.png').write_bytes(PNG)
        app = self.load(STATIONDESK_PUBLISH_SITE_NAME='  Team Docs ', STATIONDESK_PUBLISH_FAVICON='brand.png')
        site = app.site_settings()
        self.assertEqual('Team Docs', site['name'])
        self.assertTrue(site['favicon'].startswith('data:image/png;base64,'))
        self.assertEqual(str(self.root / 'state/brand.png'), site['favicon_source'])
        self.assertEqual('', site['favicon_error'])
        self.assertEqual(str(self.root / 'state/publish.env'), site['config_file'])
        self.assertEqual(('local', 'https://docs.example.test', str(self.share)),
                         (site['destination'], site['base_url'], site['share_dir']))

    def test_unusable_favicon_falls_back_and_says_why(self):
        for value in ('missing.png', 'notes.txt'):
            (self.root / 'notes.txt').write_text('x')
            site = self.load(STATIONDESK_PUBLISH_FAVICON=str(self.root / value)).SITE
            self.assertTrue(site['favicon'].startswith('data:image/svg+xml'))
            self.assertIn(value, site['favicon_error'])
            self.assertEqual('', site['favicon_source'])

    def test_unreadable_favicon_does_not_stop_the_server(self):
        locked = self.root / 'locked.png'
        locked.write_bytes(PNG)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)
        if os.access(locked, os.R_OK):
            self.skipTest('running as a user that ignores file modes')
        site = self.load(STATIONDESK_PUBLISH_FAVICON=str(locked)).SITE
        self.assertTrue(site['favicon'].startswith('data:image/svg+xml'))
        self.assertIn('locked.png', site['favicon_error'])

    def test_oversized_favicon_is_not_used(self):
        big = self.root / 'big.png'
        big.write_bytes(b'0' * (256 * 1024 + 1))
        self.assertIn('256 KB', self.load(STATIONDESK_PUBLISH_FAVICON=str(big)).SITE['favicon_error'])

    def test_external_publish_injects_favicon_when_page_has_none(self):
        (self.share / 'doc.html').write_text('<!doctype html><html><head><title>T</title></head><body>hi</body></html>')
        app = self.load()
        html = self.published(app)
        self.assertIn('<head><link rel="icon" href="data:image/svg+xml;base64,', html)
        self.assertEqual(1, html.count('rel="icon"'))
        self.assertIn('<title>T</title>', html)

    def test_pages_own_icon_is_kept(self):
        (self.share / 'doc.html').write_text(f'<html><head><title>T</title>{OWN_ICON}</head><body></body></html>')
        html = self.published(self.load())
        self.assertEqual(1, html.count('<link'))
        self.assertIn(OWN_ICON, html)

    def test_icon_in_a_comment_does_not_count_and_head_in_a_comment_is_skipped(self):
        (self.share / 'doc.html').write_text(
            '<!-- <head> --><html><head><title>T</title><!-- <link rel="icon" href="x"> --></head><body></body></html>')
        html = self.published(self.load())
        self.assertTrue(html.startswith('<!-- <head> --><html><head><link rel="icon" href="data:'))

    def test_headless_fragment_gets_the_icon_in_front(self):
        (self.share / 'doc.html').write_text('<title>T</title>plain')
        self.assertTrue(self.published(self.load()).startswith('<link rel="icon" href="data:'))

    def test_bundle_pages_each_get_the_icon_unless_they_have_one(self):
        (self.share / 'entry.html').write_text('<html><head><title>E</title></head><body><a href="other.html">o</a></body></html>')
        (self.share / 'other.html').write_text(f'<html><head><title>O</title>{OWN_ICON}</head><body></body></html>')
        app = self.load()
        ok, plan = app.plan_bundle('entry.html', owner=OWNER)
        self.assertTrue(ok, plan)
        docs = [doc['name'] for doc in plan['docs']]
        ok, result = app.publish_public('entry.html', 24, OWNER, docs=docs, plan_id=plan['plan_id'])
        self.assertTrue(ok, result)
        out = self.root / 'public' / result['slug']
        self.assertEqual(1, (out / 'index.html').read_text().count('rel="icon"'))
        other = (out / 'other.html').read_text()
        self.assertEqual(1, other.count('<link'))
        self.assertIn(OWN_ICON, other)

    def test_the_share_copy_is_never_modified(self):
        source = '<html><head><title>T</title></head><body></body></html>'
        (self.share / 'doc.html').write_text(source)
        self.published(self.load())
        self.assertEqual(source, (self.share / 'doc.html').read_text())


if __name__ == '__main__':
    unittest.main()
