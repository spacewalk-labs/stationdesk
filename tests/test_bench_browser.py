"""Exercise the shipped Bench shell and editor script in a real browser, with no services."""
import json
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

BENCH = Path(__file__).parents[1] / 'apps/bench'
APPS = {'apps': [
    {'id': 'photocraft', 'name': 'PhotoCraft', 'desc': 'photo', 'version': '1'},
    {'id': 'soundcraft', 'name': 'SoundCraft', 'desc': 'audio', 'version': '1'},
]}
PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
                    '890000000d4944415478da63fcffff3f0300050001a5f645400000000049454e44ae426082')


def serve(page, editor_head=''):
    shell = (BENCH / 'index.html').read_text().replace('@@ATTRIBUTION@@', '')
    editor = f'<!doctype html><head>{editor_head}<script src="/stationdesk-bench.js"></script></head><body>editor'

    def route(request):
        url = request.request.url.split('?')[0]
        path = url.removeprefix('https://bench.test')
        if path in ('/', '/index.html'):
            request.fulfill(body=shell, content_type='text/html')
        elif path == '/apps.json':
            request.fulfill(body=json.dumps(APPS), content_type='application/json')
        elif path.endswith('.png'):
            request.fulfill(body=PNG, content_type='image/png')
        elif path == '/stationdesk-bench.js':
            request.fulfill(body=(BENCH / 'browser.js').read_text(), content_type='application/javascript')
        elif path == '/stationdesk-bench.html':
            request.fulfill(body=(BENCH / 'browser.html').read_text(), content_type='text/html')
        else:
            request.fulfill(body=editor, content_type='text/html')
    page.route('**/*', route)


@unittest.skipIf(sync_playwright is None, 'Playwright is not installed')
class BenchBrowserTests(unittest.TestCase):
    def setUp(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(args=['--no-sandbox'])

    def tearDown(self):
        self.browser.close()
        self.playwright.stop()

    @staticmethod
    def favicon(page):
        return page.evaluate('document.getElementById("favicon").getAttribute("href")')

    def test_selected_editor_sets_title_and_icon_and_home_restores_them(self):
        for width in (1440, 390):
            with self.subTest(width=width):
                page = self.browser.new_page(viewport={'width': width, 'height': 844})
                serve(page)
                page.goto('https://bench.test/')
                page.locator('#cards .card').first.wait_for()
                expanded = page.get_attribute('#toggle', 'aria-expanded')
                self.assertEqual('false' if width == 390 else 'true', expanded)
                self.assertEqual('icon.png', self.favicon(page))
                if width == 390:  # the cards must stay readable beside the collapsed rail
                    self.assertGreaterEqual(page.locator('#cards .card').first.bounding_box()['width'], 280)
                    page.click('#toggle')
                    self.assertEqual('true', page.get_attribute('#toggle', 'aria-expanded'))
                page.click('#app-list button[data-id="soundcraft"]')
                self.assertEqual('SoundCraft — Bench', page.title())
                self.assertEqual('icons/soundcraft.png', self.favicon(page))
                if width == 390:  # choosing an app closes the overlay list
                    self.assertEqual('false', page.get_attribute('#toggle', 'aria-expanded'))
                page.click('#app-list button[data-id="photocraft"]')
                self.assertEqual('icons/photocraft.png', self.favicon(page))
                page.click('#app-list button[data-id=""]')
                self.assertEqual('Bench', page.title())
                self.assertEqual('icon.png', self.favicon(page))
                page.close()

    def test_editor_gets_its_icon_unless_it_declares_one(self):
        for head, expected in [('', '/icons/photocraft.png'),
                               ('<link rel="icon" href="/vendor.svg">', '/vendor.svg')]:
            with self.subTest(head=head):
                page = self.browser.new_page()
                serve(page, head)
                page.goto('https://bench.test/photocraft/')
                hrefs = page.evaluate('[...document.querySelectorAll("link[rel~=icon]")].map(l => l.getAttribute("href"))')
                self.assertEqual([expected], hrefs)
                page.close()

    def test_phone_wrapper_has_its_own_icon_title_and_way_back(self):
        page = self.browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)
        serve(page)
        page.goto('https://bench.test/stationdesk-bench.html?app=photocraft')
        page.wait_for_function('document.title === "PhotoCraft — Bench"')
        self.assertEqual('/icons/photocraft.png', self.favicon(page))
        self.assertEqual('/', page.get_attribute('#back', 'href'))
        page.close()


if __name__ == '__main__':
    unittest.main()
