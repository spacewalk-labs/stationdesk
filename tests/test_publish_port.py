"""Port parity: run backend functions and handlers in memory, without a server."""
import base64
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / 'apps/publish/server.py'
OWNER = 'owner@example.test'


class PublishPortTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        env = {'STATIONDESK_SHARE': str(self.share), 'STATIONDESK_OWNER': OWNER,
               'STATIONDESK_PUBLISH_TITLE_META': 'true',
               'STATIONDESK_PUBLISH_PUBLIC_MODE': 'local',
               'STATIONDESK_PUBLISH_BASE_URL': 'https://docs.example.test',
               'STATIONDESK_PUBLISH_PUBLIC_DIR': str(self.root / 'public'),
               'STATIONDESK_PUBLISH_GATED_DIR': str(self.root / 'gated'),
               'STATIONDESK_PUBLISH_HTPASSWD_DIR': str(self.root / 'auth'),
               'STATIONDESK_PUBLISH_STATE_DIR': str(self.root / 'state'),
               'STATIONDESK_PUBLISH_UPLOADS_DIR': str(self.root / 'uploads')}
        context = patch.dict(os.environ, env)
        context.start()
        self.addCleanup(context.stop)
        fqdn = patch.dict(os.environ, {'STATIONDESK_FQDN': ''})
        fqdn.start()
        self.addCleanup(fqdn.stop)
        spec = importlib.util.spec_from_file_location('publish_port', BACKEND)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        (self.share / 'entry.html').write_text('<title>Entry</title><a href="other.html">other</a>')
        (self.share / 'other.html').write_text('<title>Other</title>hello')

    def request(self, path, method='GET', data=None, owner=OWNER, origin='http://desk.test'):
        handler = object.__new__(self.app.Handler)
        handler.path = path
        handler.headers = Message()
        raw = json.dumps(data or {}).encode()
        for name, value in [('Host', 'desk.test'), ('Tailscale-User-Login', owner),
                            ('Origin', origin), ('Content-Type', 'application/json'),
                            ('Content-Length', str(len(raw)))]:
            if value is not None:
                handler.headers[name] = value
        handler.rfile = io.BytesIO(raw)
        replies = []
        handler._json = lambda *args: replies.append(args)
        getattr(handler, 'do_' + method)()
        return replies[0]

    def test_owner_and_same_origin_apply_before_mutation(self):
        for owner in (None, '', 'other@example.test'):
            self.assertEqual(403, self.request('/list', owner=owner)[0])
            self.assertEqual(403, self.request('/repair-broken', 'POST', owner=owner)[0])
        for origin in (None, 'http://evil.test', 'http://desk.test/', 'http://desk.test:8000', 'http://desk.test?x=1', 'https://desk.test#fragment'):
            self.assertEqual(403, self.request('/repair-broken', 'POST', origin=origin)[0])
        with patch.dict(os.environ, {'STATIONDESK_FQDN': 'desk.example.test'}):
            self.assertEqual(200, self.request('/repair-broken', 'POST', origin='https://desk.test')[0])
            self.assertEqual(200, self.request('/repair-broken', 'POST', origin='http://desk.test')[0])

    def test_existing_publish_route_and_hidden_smoke_name_remain_supported(self):
        source = self.root / 'source.txt'
        source.write_text('original')
        code, response = self.request('/publish', 'POST', {'source': str(source), 'name': '.smoke.txt'})
        self.assertEqual((200, {'ok': True}), (code, response))
        self.assertTrue((self.share / '.smoke.txt').is_symlink())
        self.assertEqual(200, self.request('/unpublish', 'POST', {'name': '.smoke.txt'})[0])
        self.assertFalse((self.share / '.smoke.txt').is_symlink())
        self.assertTrue(source.exists())

    def test_doctor_health_has_no_identity_requirement(self):
        for path in ('/', '/health', '/api/health'):
            self.assertEqual(200, self.request(path, owner=None)[0])

    def test_list_contains_original_rich_metadata_and_meta_title(self):
        code, response = self.request('/publish/api/list')
        self.assertEqual(200, code)
        item = response['items'][0]
        self.assertTrue({'name', 'type', 'target', 'target_dir', 'isLink', 'isDir', 'isBroken', 'size', 'mtime', 'mtime_epoch'} <= item.keys())
        self.assertEqual('Entry', self.request('/meta?name=entry.html')[1]['title'])

    def test_direct_delete_requires_retyped_name_and_preserves_directories(self):
        self.assertFalse(self.app.unpublish_direct('entry.html', 'wrong')[0])
        (self.share / 'folder').mkdir()
        self.assertFalse(self.app.unpublish_direct('folder', 'folder')[0])
        self.assertTrue(self.app.unpublish_direct('entry.html', 'entry.html')[0])

    def test_batch_unlinks_originals_and_only_explicitly_deletes_direct_files(self):
        source = self.root / 'original.txt'
        source.write_text('original')
        (self.share / 'linked').symlink_to(source)
        results = self.app.unpublish_batch(['linked', 'entry.html'])
        self.assertTrue(results[0]['ok'])
        self.assertFalse(results[1]['ok'])
        self.assertTrue(source.exists())
        self.assertTrue(self.app.unpublish_batch(['entry.html'], include_direct=True)[0]['ok'])

    def test_broken_worktree_link_repairs_to_main_and_cleans_unrepairable(self):
        source = self.root / 'repo' / 'doc.html'
        source.parent.mkdir()
        source.write_text('original')
        (self.share / 'repair.html').symlink_to(self.root / 'repo-wt/old/doc.html')
        (self.share / 'lost.html').symlink_to(self.root / 'gone.html')
        results = self.app.repair_or_cleanup_all()
        self.assertEqual(2, len(results))
        self.assertTrue((self.share / 'repair.html').exists())
        self.assertFalse((self.share / 'lost.html').is_symlink())

    def test_uploads_keep_data_and_use_configured_path(self):
        for name in ('../image.txt', 'image.txt'):
            ok, response = self.app.save_uploaded_file(name, base64.b64encode(b'data').decode())
            self.assertTrue(ok)
            self.assertEqual(b'data', Path(response['path']).read_bytes())
            self.assertEqual(self.root / 'uploads', Path(response['path']).parent)
        ok, response = self.app.save_uploaded_image(base64.b64encode(b'\xff\xd8\xffjpeg').decode())
        self.assertTrue(ok)
        self.assertTrue(Path(response['path']).is_file())
        with patch.object(self.app.time, 'time', return_value=self.app.time.time() + 86401):
            self.assertEqual(3, self.app.cleanup_old_uploads())

    def test_upload_cleanup_preserves_existing_user_files_and_symlinks(self):
        uploads = self.root / 'uploads'
        uploads.mkdir()
        for name in ('important-report.txt', 'image001-20261023-010203.jpg.backup',
                     'file001-20261023-010203.txt.backup'):
            manual = uploads / name
            manual.write_bytes(b'keep')
            os.utime(manual, (1, 1))
        link = uploads / 'file002-20261023-010203.txt'
        link.symlink_to(uploads / 'important-report.txt')
        for name in ('notes.txt', 'README', 'archive.tar.gz'):
            ok, result = self.app.save_uploaded_file(name, base64.b64encode(b'new').decode())
            self.assertTrue(ok, result)
            self.assertRegex(result['name'], r'^file[0-9]{3,}-[0-9]{8}-[0-9]{6}\.[a-z0-9]{1,8}$')
        with patch.object(self.app.time, 'time', return_value=self.app.time.time() + 86401):
            self.assertEqual(3, self.app.cleanup_old_uploads())
        self.assertEqual(b'keep', (uploads / 'important-report.txt').read_bytes())
        self.assertTrue((uploads / 'image001-20261023-010203.jpg.backup').exists())
        self.assertTrue((uploads / 'file001-20261023-010203.txt.backup').exists())
        self.assertTrue(link.is_symlink())

    def test_manager_upload_appears_in_share_and_survives_temporary_cleanup(self):
        data = base64.b64encode(b'<h1>Uploaded document</h1>').decode()
        for expected in ('report.html', 'report-2.html'):
            code, response = self.request('/publish/api/upload-share', 'POST',
                                          {'name': 'report.html', 'data': data})
            self.assertEqual(200, code)
            self.assertEqual(expected, response['name'])
            self.assertEqual(self.share / expected, Path(response['path']))
            self.assertEqual(b'<h1>Uploaded document</h1>', (self.share / expected).read_bytes())
            self.assertIn(expected, [item['name'] for item in self.request('/list')[1]['items']])
        with patch.object(self.app.time, 'time', return_value=self.app.time.time() + 86401):
            self.app.cleanup_old_uploads()
        self.assertTrue((self.share / 'report.html').is_file())
        code, temporary = self.request('/upload-file', 'POST', {'name': 'card.txt', 'data': data})
        self.assertEqual(200, code)
        self.assertEqual(self.root / 'uploads', Path(temporary['path']).parent)

    def test_public_snapshot_expiry_revoke_and_owner_scope(self):
        ok, result = self.app.publish_public('entry.html', 1, OWNER)
        self.assertTrue(ok, result)
        slug = result['slug']
        snapshot = self.root / 'public' / slug / 'index.html'
        self.assertTrue(snapshot.is_file())
        self.assertEqual([], self.app.public_list('other@example.test')['items'])
        self.assertFalse(self.app.public_revoke(slug, 'other@example.test')['ok'])
        self.assertTrue(self.app.public_set_expiry(slug, OWNER, 24)['ok'])
        self.assertTrue(self.app.public_revoke(slug, OWNER)['ok'])
        self.assertFalse(snapshot.exists())
        self.assertTrue((self.share / 'entry.html').exists())

    def test_expiry_sweep_removes_snapshot_without_page_visit(self):
        ok, result = self.app.publish_public('entry.html', 1, OWNER)
        self.assertTrue(ok, result)
        with patch.object(self.app.time, 'time', return_value=result['expiry'] + 1):
            self.assertIn(result['slug'], self.app._local_sweep_only())
        self.assertFalse((self.root / 'public' / result['slug']).exists())

    def test_expired_open_and_gated_keep_history_republish_url_and_remove(self):
        self.app.GATED_ENABLED = True
        for mode in ('open', 'gated'):
            with self.subTest(mode=mode), patch.object(self.app, '_htpasswd_hash', return_value='user:$2y$hash'):
                ok, published = self.app.publish_public('entry.html', 1, OWNER, mode, 'test-password')
                self.assertTrue(ok, published)
                slug = published['slug']
                content_root = self.root / ('public' if mode == 'open' else 'gated')
                with patch.object(self.app.time, 'time', return_value=published['expiry'] + 1):
                    self.assertIn(slug, self.app._local_sweep_only())
                    self.assertFalse((content_root / slug).exists())
                    self.assertFalse((self.root / 'auth' / (slug + '.htpasswd')).exists())
                    auth = self.root / 'auth' / (slug + '.htpasswd')
                    if mode == 'gated':
                        # Simulate a stale credential left after content cleanup.
                        # Reconcile must remove it even when sweep can skip the
                        # already-expired, absent snapshot directory.
                        auth.write_text('user:$2y$stale')
                    # Listing repeatedly reconciles disk and state; the expired
                    # history must survive both sweeps and a subsequent visit.
                    for _ in range(2):
                        items = self.app.public_list(OWNER)['items']
                        self.assertEqual([slug], [item['slug'] for item in items])
                        self.assertTrue(items[0]['expired'])
                        self.assertEqual(published['url'], items[0]['url'])
                        self.assertFalse(auth.exists())
                    self.assertEqual([], self.app.public_list('other@example.test')['items'])
                    self.assertFalse(self.app.public_revoke(slug, 'other@example.test')['ok'])
                ok, republished = self.app.publish_public('entry.html', 24, OWNER, mode, 'test-password')
                self.assertTrue(ok, republished)
                self.assertEqual(published['url'], republished['url'])
                self.assertTrue((content_root / slug / 'index.html').is_file())
                self.assertFalse(self.app.public_list(OWNER)['items'][0]['expired'])
                with patch.object(self.app.time, 'time', return_value=republished['expiry'] + 1):
                    self.app._local_sweep_only()
                    self.assertTrue(self.app.public_revoke(slug, OWNER)['ok'])
                    self.assertEqual([], self.app.public_list(OWNER)['items'])
                    self.assertNotIn(slug, json.loads(Path(self.app.STATE_FILE).read_text())['items'])

    def test_password_gate_hash_is_not_in_public_snapshot_or_state(self):
        self.app.GATED_ENABLED = True
        with patch.object(self.app, '_htpasswd_hash', return_value='user:$2y$hash'):
            ok, result = self.app.publish_public('entry.html', 24, OWNER, 'gated', 'my-password')
        self.assertTrue(ok, result)
        self.assertIn('/g/', result['url'])
        self.assertFalse((self.root / 'public' / result['slug']).exists())
        self.assertTrue((self.root / 'gated' / result['slug'] / 'index.html').is_file())
        auth = self.root / 'auth' / (result['slug'] + '.htpasswd')
        self.assertIn('$2y$', auth.read_text())
        self.assertNotIn('my-password', Path(self.app.STATE_FILE).read_text())
        self.assertTrue(self.app.public_revoke(result['slug'], OWNER)['ok'])
        self.assertFalse(auth.exists())

    def test_bundle_plan_rewrites_internal_links_and_is_single_use(self):
        ok, plan = self.app.plan_bundle('entry.html', owner=OWNER)
        self.assertTrue(ok, plan)
        docs = [doc['name'] for doc in plan['docs']]
        self.assertEqual({'entry.html', 'other.html'}, set(docs))
        ok, result = self.app.publish_public('entry.html', 24, OWNER, docs=docs, plan_id=plan['plan_id'])
        self.assertTrue(ok, result)
        self.assertEqual(['index.html', 'other.html'], result['bundle'])
        self.assertFalse(self.app.publish_public('entry.html', 24, OWNER, docs=docs, plan_id=plan['plan_id'])[0])

    def test_bundle_changed_after_plan_is_rejected(self):
        ok, plan = self.app.plan_bundle('entry.html', owner=OWNER)
        self.assertTrue(ok, plan)
        (self.share / 'other.html').write_text('<title>changed</title>')
        ok, error = self.app.publish_public('entry.html', 24, OWNER,
            docs=[doc['name'] for doc in plan['docs']], plan_id=plan['plan_id'])
        self.assertFalse(ok)
        self.assertIn('source changed', error)


if __name__ == '__main__':
    unittest.main()
