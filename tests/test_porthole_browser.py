"""Exercise the shipped floating Porthole across a real cross-origin iframe."""
import json
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'Playwright is not installed')
class PortholeBrowserTests(unittest.TestCase):
    def test_iframe_crossing_drag_saves_and_restores_position(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        document = '''<!doctype html><iframe src="https://embedded.test/"
          style="position:fixed;inset:0;width:100%;height:100%;border:0"></iframe>
          <script src="/assets/porthole.js" data-badge="0"></script>'''
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for width in (1440, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={'width': width, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route(request):
                        url = request.request.url
                        if url == 'https://station.test/app/':
                            request.fulfill(body=document, content_type='text/html')
                        elif url == 'https://station.test/assets/porthole.js':
                            request.fulfill(body=source, content_type='application/javascript')
                        else:
                            request.fulfill(body='<body>Embedded app</body>', content_type='text/html')

                    page.route('**/*', route)
                    page.goto('https://station.test/app/')
                    button = page.locator('#stationdesk-porthole')
                    button.wait_for()
                    initial = button.bounding_box()
                    start_x = initial['x'] + initial['width'] / 2
                    start_y = initial['y'] + initial['height'] / 2
                    end_x, end_y = round(width * .65), 260
                    self.assertEqual('IFRAME', page.evaluate(
                        '([x,y]) => document.elementFromPoint(x,y).tagName', [end_x, end_y]))
                    page.mouse.move(start_x, start_y)
                    page.mouse.down()
                    # One move stays in the button; the next crosses into the iframe.
                    page.mouse.move(start_x + 10, start_y - 10)
                    page.mouse.move(end_x, end_y)
                    page.mouse.up()
                    final = button.bounding_box()
                    saved = page.evaluate('JSON.parse(localStorage.getItem("stationdesk:btn-pos-v1"))')
                    print(json.dumps({'width': width, 'final': final, 'saved': saved}), flush=True)
                    self.assertAlmostEqual(end_x - 22, final['x'], delta=1)
                    self.assertAlmostEqual(end_y - 22, final['y'], delta=1)
                    self.assertEqual({'x': final['x'], 'y': final['y']}, saved)
                    page.reload()
                    button.wait_for()
                    restored = button.bounding_box()
                    self.assertEqual(final, restored)
                    self.assertEqual([], errors)
                    page.close()
            browser.close()
