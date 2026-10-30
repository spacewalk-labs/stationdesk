"""Publish counts document views itself from the :8000 nginx views.log."""
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1] / 'apps/publish/server.py'
BROWSER = 'Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0 Safari/537.36'


def line(uri, status='200', agent=BROWSER, when='2026-10-26T20:52:20+09:00'):
    return f'{when}\t{uri}\t{status}\t{agent}\n'


class ViewCountTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.share, self.state = root / 'share', root / 'state'
        (self.share / 'folder').mkdir(parents=True)
        for name in ('a.html', 'folder/index.html', 'folder/page.html', 'folder/x.css', 'pic.png', 'one two.html'):
            (self.share / name).write_text('<title>t</title>')
        (self.share / 'link.html').symlink_to(self.share / 'a.html')
        env = {'STATIONDESK_SHARE': str(self.share), 'STATIONDESK_OWNER': 'o@example.test',
               'STATIONDESK_FQDN': '', 'STATIONDESK_ROOT': str(root),
               'STATIONDESK_PUBLISH_STATE_DIR': str(self.state)}
        context = patch.dict(os.environ, env)
        context.start()
        self.addCleanup(context.stop)
        spec = importlib.util.spec_from_file_location('publish_views', BACKEND)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.state.mkdir()
        self.log = self.state / 'views.log'

    def append(self, *lines):
        with open(self.log, 'a', encoding='utf-8') as fh:
            fh.write(''.join(lines))

    def views(self):
        return {name: entry['views'] for name, entry in self.app.ingest_views().items()}

    def test_only_successful_browser_requests_count(self):
        self.append(line('/a.html'), line('/a.html', '304'), line('/a.html', '404'), line('/a.html', '301'),
                    line('/a.html', agent='curl/8.5.0'), line('/a.html', agent='python-requests/2.31'),
                    line('/a.html', agent='Mozilla/5.0 HeadlessChrome/120.0'),
                    line('/a.html', agent='Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)'),
                    line('/a.html', agent='Mozilla/5.0 (compatible; Googlebot/2.1)'),
                    line('/a.html', agent='-'), 'garbage line\n')
        self.assertEqual({'a.html': 2}, self.views())

    def test_path_maps_to_the_share_entry(self):
        self.append(line('/a.html?x=1'), line('/one%20two.html'), line('/pic.png'), line('/link.html'),
                    line('/folder/'), line('/folder/index.html'), line('/folder/page.html?a=b'),
                    line('/folder/x.css'),          # a page's asset
                    line('/folder'),                # no slash: only the redirect
                    line('/pic.png/'), line('/missing.html'), line('/favicon.ico'),
                    line('/_assets/x.css'), line('/../a.html'), line('/%2e%2e/a.html'), line('/.hidden.html'))
        self.assertEqual({'a.html': 1, 'one two.html': 1, 'pic.png': 1, 'link.html': 1, 'folder': 3}, self.views())

    def test_only_new_lines_are_counted_and_a_half_line_waits(self):
        self.append(line('/a.html'))
        self.assertEqual({'a.html': 1}, self.views())
        self.assertEqual({'a.html': 1}, self.views())          # nothing new, nothing added
        whole = line('/a.html')
        self.append(whole[:20])
        self.assertEqual({'a.html': 1}, self.views())          # unfinished line is not read
        self.append(whole[20:])
        self.assertEqual({'a.html': 2}, self.views())

    def test_counts_survive_a_restart(self):
        self.append(line('/a.html', when='2026-10-26T20:52:20+09:00'), line('/a.html', when='2026-10-26T21:00:00+09:00'))
        self.app.ingest_views()
        stored = self.app._views_load()['docs']['a.html']
        self.assertEqual(2, stored['views'])
        self.assertEqual(int(datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc).timestamp()), stored['last_view'])   # 21:00 +09:00
        self.append(line('/a.html'))
        spec = importlib.util.spec_from_file_location('publish_views2', BACKEND)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)                          # a new process: only views.json remains
        self.assertEqual(3, fresh.ingest_views()['a.html']['views'])

    def test_replaced_or_truncated_log_starts_over(self):
        self.append(line('/a.html'), line('/a.html'))
        self.assertEqual({'a.html': 2}, self.views())
        rotated = self.state / 'views.log.new'                  # rotated: a new file replaces it (new inode;
        rotated.write_text(line('/a.html') + line('/a.html'))   # unlink-then-create may reuse the old one)
        os.replace(rotated, self.log)
        self.assertEqual({'a.html': 4}, self.views())
        self.log.write_text(line('/pic.png'))                   # same inode, shorter than the cursor
        self.assertEqual({'a.html': 4, 'pic.png': 1}, self.views())

    def test_a_big_fully_read_log_is_emptied_and_counting_continues(self):
        with patch.object(self.app, 'VIEWS_LOG_TRIM', 100):
            self.append(*[line('/a.html')] * 3)
            self.assertEqual({'a.html': 3}, self.views())
            self.assertEqual(0, self.log.stat().st_size)
            self.append(line('/a.html'))
            self.assertEqual({'a.html': 4}, self.views())

    def test_list_carries_views_and_unpublish_resets_them(self):
        self.append(line('/a.html'), line('/link.html'), line('/link.html'))
        items = {i['name']: i for i in self.app.list_items()}
        self.assertEqual((1, True), (items['a.html']['views'], items['a.html']['last_view'] > 0))
        self.assertEqual(2, items['link.html']['views'])
        self.assertNotIn('views', items['pic.png'])
        self.assertTrue(self.app.unpublish('link.html')[0])
        self.assertNotIn('link.html', self.app._views_load()['docs'])
        (self.share / 'link.html').symlink_to(self.share / 'pic.png')
        self.assertNotIn('views', {i['name']: i for i in self.app.list_items()}['link.html'])


if __name__ == '__main__':
    unittest.main()
