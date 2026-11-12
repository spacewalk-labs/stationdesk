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

    def test_bottom_bar_clearance_updates_and_clamps_restored_position(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        document = '''<!doctype html><meta name="viewport" content="width=device-width">
          <style>:root{--stationdesk-porthole-bottom:52px}</style>
          <script src="/assets/porthole.js" data-badge="0" data-secret="0"></script>'''
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            def route(request):
                if request.request.url.endswith('porthole.js'):
                    request.fulfill(body=source, content_type='application/javascript')
                else:
                    request.fulfill(body=document, content_type='text/html')
            page.route('**/*', route)
            page.goto('https://station.test/')
            button = page.locator('#stationdesk-porthole')
            button.wait_for()
            box = button.bounding_box()
            self.assertAlmostEqual(390 - 14, box['x'] + box['width'], delta=1)
            self.assertAlmostEqual(844 - 52 - 20, box['y'] + box['height'], delta=1)
            page.evaluate("localStorage.setItem('stationdesk:btn-pos-v1', JSON.stringify({x:20,y:790}))")
            page.reload()
            button.wait_for()
            box = button.bounding_box()
            self.assertLessEqual(box['y'] + box['height'], 844 - 52)
            page.evaluate("document.documentElement.style.setProperty('--stationdesk-porthole-bottom','120px');window.dispatchEvent(new Event('stationdesk:layout-changed'))")
            box = button.bounding_box()
            self.assertLessEqual(box['y'] + box['height'], 844 - 120)
            page.set_viewport_size({'width':360,'height':740})
            page.wait_for_function("document.getElementById('stationdesk-porthole').getBoundingClientRect().bottom <= 620")
            box = button.bounding_box()
            self.assertLessEqual(box['y'] + box['height'], 740 - 120)
            button.click()
            self.assertEqual(0, page.get_by_text('Secret drop', exact=True).count())
            browser.close()

    def test_bottom_left_anchor_starts_left_and_menu_opens_up_and_right(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        document = '<!doctype html><meta name="viewport" content="width=device-width"><script src="/assets/porthole.js" data-badge="0" data-anchor="bottom-left"></script>'
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.route('**/*', lambda r: r.fulfill(
                body=source if r.request.url.endswith('porthole.js') else document,
                content_type='application/javascript' if r.request.url.endswith('porthole.js') else 'text/html'))
            page.goto('https://station.test/')
            button = page.locator('#stationdesk-porthole')
            button.wait_for()
            box = button.bounding_box()
            self.assertAlmostEqual(14, box['x'], delta=1)
            self.assertAlmostEqual(900 - 20, box['y'] + box['height'], delta=1)
            button.click()
            menu = page.locator('div:has(> button:has-text("Go to StationDesk"))').last
            menu.wait_for()
            m = menu.bounding_box()
            self.assertLess(m['y'] + m['height'], box['y'])
            self.assertAlmostEqual(box['x'], m['x'], delta=1)
            browser.close()

    def test_default_start_is_bottom_right_and_menu_opens_up_and_left(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        document = '<!doctype html><meta name="viewport" content="width=device-width"><script src="/assets/porthole.js" data-badge="0"></script>'
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for width, height in ((1440, 900), (390, 844)):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={'width': width, 'height': height})
                    page.route('**/*', lambda r: r.fulfill(
                        body=source if r.request.url.endswith('porthole.js') else document,
                        content_type='application/javascript' if r.request.url.endswith('porthole.js') else 'text/html'))
                    page.goto('https://station.test/')
                    button = page.locator('#stationdesk-porthole')
                    button.wait_for()
                    box = button.bounding_box()
                    self.assertAlmostEqual(width - 14, box['x'] + box['width'], delta=1)
                    self.assertAlmostEqual(height - 20, box['y'] + box['height'], delta=1)
                    button.click()
                    menu = page.locator('div:has(> button:has-text("Go to StationDesk"))').last
                    menu.wait_for()
                    m = menu.bounding_box()
                    self.assertLess(m['y'] + m['height'], box['y'])
                    self.assertAlmostEqual(box['x'] + box['width'], m['x'] + m['width'], delta=1)
                    self.assertGreaterEqual(m['x'], 8)
                    page.close()
            browser.close()

    def test_tab_icon_takes_the_box_colour_and_keeps_it_when_the_app_swaps_icons(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8" fill="#000"/></svg>'
        document = '''<!doctype html><link rel="icon" href="/a.svg" type="image/svg+xml">
          <script src="/assets/porthole.js" data-badge="0" data-secret="0"></script>'''
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for frame in (None, {'color': 'orange'}, {'color': 'nonsense'}):
                with self.subTest(frame=frame):
                    page = browser.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route(request, _=None, frame=frame):
                        url = request.request.url
                        if url.endswith('porthole.js'):
                            request.fulfill(body=source, content_type='application/javascript')
                        elif url.endswith('.svg'):
                            fill = '#000' if url.endswith('/a.svg') else '#fff'
                            request.fulfill(body=icon.replace('#000', fill), content_type='image/svg+xml')
                        elif url.endswith('/stationdesk-frame.json'):
                            request.fulfill(status=200 if frame else 404, body=json.dumps(frame or {}),
                                            content_type='application/json')
                        else:
                            request.fulfill(body=document, content_type='text/html')

                    page.route('**/*', route)
                    page.goto('https://station.test/')
                    attr = 'document.querySelector("link[rel~=icon]").getAttribute("href")'
                    if frame != {'color': 'orange'}:
                        page.wait_for_timeout(500)
                        self.assertEqual('/a.svg', page.evaluate(attr))
                    else:
                        page.wait_for_function(attr + '.startsWith("data:image/png")')
                        first = page.evaluate(attr)
                        # Paseo-style swap: the app writes href and reads it back to compare.
                        page.evaluate('document.querySelector("link[rel~=icon]").href = "/b.svg"')
                        page.wait_for_function(attr + ' !== ' + json.dumps(first))
                        self.assertTrue(page.evaluate(attr).startswith('data:image/png'))
                        self.assertEqual('https://station.test/b.svg',
                                         page.evaluate('document.querySelector("link[rel~=icon]").href'))
                        corner = page.evaluate('''() => new Promise(done => { const i = new Image();
                            i.onload = () => { const c = document.createElement("canvas"); c.width = c.height = 32;
                              const g = c.getContext("2d"); g.drawImage(i, 0, 0);
                              done(Array.from(g.getImageData(16, 1, 1, 1).data)); };
                            i.src = document.querySelector("link[rel~=icon]").getAttribute("href"); })''')
                        self.assertEqual([0xEA, 0x58, 0x0C, 255], corner)   # orange edge around the icon
                    self.assertEqual([], errors)
                    page.close()
            browser.close()

    def test_a_slow_icon_never_paints_over_a_newer_icon_or_a_cleared_colour(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8" fill="{}"/></svg>'
        document = '''<!doctype html><link rel="icon" href="/slow.svg" type="image/svg+xml">
          <script src="/assets/porthole.js" data-badge="0" data-secret="0"></script>'''
        attr = 'document.querySelector("link[rel~=icon]").getAttribute("href")'
        centre = '''() => new Promise(done => { const i = new Image();
            i.onload = () => { const c = document.createElement("canvas"); c.width = c.height = 32;
              const g = c.getContext("2d"); g.drawImage(i, 0, 0, 32, 32);
              done(Array.from(g.getImageData(16, 16, 1, 1).data)); };
            i.src = document.querySelector("link[rel~=icon]").getAttribute("href"); })'''
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for scene in ('newer icon', 'cleared colour'):
                with self.subTest(scene=scene):
                    page = browser.new_page()
                    held, frame = [], {'color': 'orange'}

                    def route(request, _=None):
                        url = request.request.url
                        if url.endswith('porthole.js'):
                            request.fulfill(body=source, content_type='application/javascript')
                        elif url.endswith('/slow.svg'):
                            held.append(request)          # answered only after the newer state is drawn
                        elif url.endswith('/b.svg'):
                            request.fulfill(body=icon.format('#fff'), content_type='image/svg+xml')
                        elif url.endswith('/stationdesk-frame.json'):
                            request.fulfill(status=200 if frame else 404, body=json.dumps(frame),
                                            content_type='application/json')
                        else:
                            request.fulfill(body=document, content_type='text/html')

                    page.route('**/*', route)
                    page.goto('https://station.test/')
                    page.wait_for_function('() => window.StationDeskFrame')
                    while not held:
                        page.wait_for_timeout(50)
                    if scene == 'newer icon':
                        page.evaluate('document.querySelector("link[rel~=icon]").href = "/b.svg"')
                        page.wait_for_function(attr + '.startsWith("data:image/png")')
                        expected = page.evaluate(attr)
                    else:
                        frame.clear()
                        page.evaluate('window.StationDeskFrame.reload()')
                        page.wait_for_function(attr + ' === "https://station.test/slow.svg"')
                        expected = page.evaluate(attr)
                    held[0].fulfill(body=icon.format('#000'), content_type='image/svg+xml')
                    page.wait_for_timeout(500)
                    self.assertEqual(expected, page.evaluate(attr))
                    if scene == 'newer icon':
                        self.assertEqual([255, 255, 255, 255], page.evaluate(centre))
                    page.close()
            browser.close()
