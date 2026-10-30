"""Per-document search-engine indexing: off by default, a marker file when on, kept across Update."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / 'apps/publish/server.py'
OWNER = 'owner@example.test'


class IndexingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        (self.share / 'doc.html').write_text('<html><head><title>Doc</title></head><body>x</body></html>')
        self.env = {'STATIONDESK_SHARE': str(self.share), 'STATIONDESK_OWNER': OWNER,
                    'STATIONDESK_FQDN': '', 'STATIONDESK_ROOT': str(self.root),
                    'STATIONDESK_PUBLISH_PUBLIC_MODE': 'local',
                    'STATIONDESK_PUBLISH_BASE_URL': 'https://docs.example.test',
                    'STATIONDESK_PUBLISH_PUBLIC_DIR': str(self.root / 'public'),
                    'STATIONDESK_PUBLISH_GATED_DIR': str(self.root / 'gated'),
                    'STATIONDESK_PUBLISH_HTPASSWD_DIR': str(self.root / 'auth'),
                    'STATIONDESK_PUBLISH_STATE_DIR': str(self.root / 'state'),
                    'STATIONDESK_PUBLISH_FUNNEL_PORT': '10000'}

    def load(self, **extra):
        context = patch.dict(os.environ, self.env | extra)
        context.start()
        self.addCleanup(context.stop)
        spec = importlib.util.spec_from_file_location('publish_indexing', BACKEND)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        return app

    def publish(self, app, **kwargs):
        ok, result = app.publish_public('doc.html', 24, OWNER, **kwargs)
        self.assertTrue(ok, result)
        return result['slug']

    def marker(self, slug):
        return (self.root / 'public' / slug / '.indexable').exists()

    def listed(self, app, slug):
        return next(i for i in app.public_list(OWNER)['items'] if i['slug'] == slug)

    def test_default_is_off(self):
        app = self.load()
        slug = self.publish(app)
        self.assertFalse(self.marker(slug))
        self.assertFalse(self.listed(app, slug)['indexed'])

    def test_switch_writes_and_removes_the_marker(self):
        app = self.load()
        slug = self.publish(app)
        self.assertEqual({'ok': True, 'slug': slug, 'indexed': True}, app.public_set_indexed(slug, OWNER, True))
        self.assertTrue(self.marker(slug))
        self.assertTrue(self.listed(app, slug)['indexed'])
        self.assertTrue(app.public_set_indexed(slug, OWNER, False)['ok'])
        self.assertFalse(self.marker(slug))

    def test_update_keeps_the_choice_and_revoke_removes_everything(self):
        app = self.load()
        slug = self.publish(app)
        app.public_set_indexed(slug, OWNER, True)
        self.assertEqual(slug, self.publish(app))
        self.assertTrue(self.marker(slug))
        self.assertTrue(app.public_revoke(slug, OWNER)['ok'])
        self.assertFalse((self.root / 'public' / slug).exists())

    def test_password_page_is_never_indexable(self):
        app = self.load()
        if not app.gated_available():
            self.skipTest('htpasswd not installed')
        slug = self.publish(app)
        app.public_set_indexed(slug, OWNER, True)
        self.publish(app, mode='gated', password='pw')
        self.assertFalse(self.marker(slug))
        self.assertFalse(self.listed(app, slug)['indexed'])
        self.assertFalse(app.public_set_indexed(slug, OWNER, True)['ok'])

    def test_list_reports_snapshot_time_and_linked_documents(self):
        app = self.load()
        slug = self.publish(app)
        first = self.listed(app, slug)
        self.assertFalse(first['bundled'])
        with patch.object(app.time, 'time', return_value=first['snapshot_at'] + 100):
            self.publish(app)
        again = self.listed(app, slug)
        self.assertGreater(again['snapshot_at'], first['snapshot_at'])   # Update moves the snapshot time
        (self.root / 'public' / slug / 'child.html').write_text('x')
        self.assertTrue(self.listed(app, slug)['bundled'])

    def test_list_carries_the_document_title(self):
        app = self.load()
        (self.share / 'plain.html').write_text('<p>no title</p>')
        (self.share / 'note.txt').write_text('x')
        titles = {i['name']: i['title'] for i in app.list_items()}
        self.assertEqual({'doc.html': 'Doc', 'plain.html': '', 'note.txt': ''}, titles)

    def test_title_cache_follows_edits_and_forgets_removed_files(self):
        app = self.load()
        target = self.root / 'target.html'
        target.write_text('<title>One</title>')
        os.symlink(target, self.share / 'live.html')
        titles = lambda: {i['name']: i['title'] for i in app.list_items()}
        self.assertEqual('One', titles()['live.html'])
        with patch.object(app, 'doc_title', wraps=app.doc_title) as read:
            titles()
            self.assertEqual(0, read.call_count)   # unchanged files are not re-read
        target.write_text('<title>Two!</title>')   # edited through the link; size differs
        self.assertEqual('Two!', titles()['live.html'])
        (self.share / 'live.html').unlink()
        (self.share / 'doc.html').unlink()
        titles()
        self.assertEqual({}, app._TITLE_CACHE)

    def test_unreadable_title_is_not_cached(self):
        app = self.load()
        doc = self.share / 'doc.html'
        doc.chmod(0)
        with patch.object(app, 'doc_title', return_value=None):   # root bypasses chmod; simulate the failed read
            self.assertEqual('', app.list_items()[0]['title'])
        self.assertEqual({}, app._TITLE_CACHE)
        doc.chmod(0o644)
        self.assertEqual('Doc', app.list_items()[0]['title'])

    def test_list_mtime_follows_a_live_symlink_target(self):
        app = self.load()
        target = self.root / 'target.html'
        target.write_text('<p>x</p>')
        os.utime(target, (1_000_000_000, 1_000_000_000))
        os.symlink(target, self.share / 'live.html')
        os.symlink(self.root / 'gone.html', self.share / 'broken.html')
        listed = {i['name']: i for i in app.list_items()}
        self.assertEqual(1_000_000_000, listed['live.html']['mtime_epoch'])
        self.assertEqual(int(os.lstat(self.share / 'broken.html').st_mtime), listed['broken.html']['mtime_epoch'])

    def test_other_owner_cannot_switch(self):
        app = self.load()
        slug = self.publish(app)
        self.assertFalse(app.public_set_indexed(slug, 'other@example.test', True)['ok'])
        self.assertFalse(self.marker(slug))

    def test_unavailable_targets_say_why(self):
        own = self.load(STATIONDESK_PUBLISH_FUNNEL_PORT='')
        self.assertFalse(own.indexing_status()['available'])
        self.assertIn('web server', own.indexing_status()['reason'])
        self.assertFalse(own.public_set_indexed('any-slug', OWNER, True)['ok'])
        remote = self.load(STATIONDESK_PUBLISH_PUBLIC_MODE='remote', STATIONDESK_PUBLISH_INGEST_URL='https://i.example.test',
                           STATIONDESK_PUBLISH_FUNNEL_PORT='')
        self.assertIn('ingest', remote.indexing_status()['reason'])
        self.assertFalse(remote.public_set_indexed('any-slug', OWNER, True)['ok'])


if __name__ == '__main__':
    unittest.main()
