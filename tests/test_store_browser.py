"""Exercise the shipped store UI with failed CLI results at both viewport sizes."""
import json
from pathlib import Path
import re
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class StoreBrowserTests(unittest.TestCase):
    def test_company_rows_keep_failures_and_links_follow_install_remove_state(self):
        source = (Path(__file__).parents[1] / 'hub/web/index.html').read_text()
        modal = source[source.index('<div class="store-modal"'):source.index('<main>')]
        script = source.split('<script>\n// Owner app store', 1)[1].split('</script>', 1)[0]
        styles = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', source, re.S))
        document = ('<meta charset="utf-8"><style>' + styles + '</style><button id="store-open"><span id="store-badge"></span></button>'
                    + modal + '<script>' + (Path(__file__).parents[1] / 'hub/web/assets/i18n.js').read_text() + '</script><script>async function j(){return {email:"owner@example.test"}};'
                    'function stationdeskWhen(){return ""};function stationdeskIconNode(){return document.createElement("span")};'
                    'window.stationdeskRefreshHubTiles=async()=>true;</script><script>// Owner app store' + script + '</script>')
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for width in (1440, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={'width': width, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    state = {'enabled': True, 'link': 'install', 'reads': 0}
                    mutations = []

                    def route(request):
                        url = request.request.url
                        if url == 'https://station.test/':
                            return request.fulfill(body=document, content_type='text/html')
                        if '/updates/run' in url:
                            run_id = url.split('run_id=', 1)[1]
                            if run_id == 'company-app':
                                answer = {'run': {'status': 'failed', 'output': '회사 앱 원본을 읽을 수 없습니다.'}}
                            else:
                                state['link'] = 'installed' if run_id == 'install' else 'install'
                                answer = {'run': {'status': 'done'}}
                        elif request.request.method == 'POST':
                            path = request.request.url.removeprefix('https://station.test')
                            mutations.append(path)
                            answer = {'ok': True, 'run_id': 'company-app' if '/company-app/' in path else path.rsplit('/', 1)[1]}
                        else:
                            state['reads'] += 1
                            answer = {'company_enabled': state['enabled'], 'rows': [
                                {'id': 'company-app', 'name': 'Company app', 'origin': 'company', 'kind': 'app', 'state': 'install'},
                                {'id': 'company-link', 'name': 'Company link', 'origin': 'company', 'kind': 'link', 'state': state['link']}]}
                        request.fulfill(body=json.dumps(answer), content_type='application/json')

                    page.route('**/*', route)
                    page.goto('https://station.test/')
                    page.locator('#store-open').click()
                    company_tab = page.locator('#store-tab-company')
                    self.assertTrue(company_tab.is_visible())
                    company_tab.click()
                    app = page.locator('[data-store-row=company-app]')
                    app.locator('[data-store-action=install]').click()
                    app.locator('.store-run-note').filter(has_text='원본을 읽을 수 없습니다').wait_for()
                    link = page.locator('[data-store-row=company-link]')
                    reads_before = state['reads']
                    link.get_by_role('button', name='Show on home', exact=True).click()
                    link.get_by_role('button', name='Remove from home', exact=True).wait_for()
                    self.assertIn('Added to home.', link.inner_text())
                    self.assertNotIn('Install — complete', link.inner_text())
                    self.assertGreater(state['reads'], reads_before)
                    self.assertIn('원본을 읽을 수 없습니다', app.inner_text())
                    self.assertNotIn('원본을 읽을 수 없습니다', link.inner_text())
                    reads_before = state['reads']
                    link.get_by_role('button', name='Remove from home', exact=True).click()
                    link.get_by_role('button', name='Show on home', exact=True).wait_for()
                    self.assertIn('Removed from home.', link.inner_text())
                    self.assertGreater(state['reads'], reads_before)
                    self.assertEqual([
                        '/monitor/api/owner/apps/company-app/install',
                        '/monitor/api/owner/apps/company-link/install',
                        '/monitor/api/owner/apps/company-link/remove'], mutations)
                    self.assertIn('원본을 읽을 수 없습니다', app.inner_text())
                    state['enabled'] = False
                    page.evaluate('window.dispatchEvent(new Event("stationdesk-apps-changed"))')
                    company_tab.wait_for(state='hidden')
                    self.assertEqual('true', page.locator('#store-tab-addon').get_attribute('aria-selected'))
                    self.assertTrue(page.locator('#store-pane-addon').is_visible())
                    self.assertEqual([], errors)
                    page.close()
            browser.close()

    def test_builtin_failure_stays_with_its_row_and_addon_failure_with_its_form(self):
        source = (Path(__file__).parents[1] / 'hub/web/index.html').read_text()
        modal = source[source.index('<div class="store-modal"'):source.index('<main>')]
        script = source.split('<script>\n// Owner app store', 1)[1].split('</script>', 1)[0]
        styles = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', source, re.S))
        document = ('<meta charset="utf-8"><style>' + styles + '</style><button id="store-open"><span id="store-badge"></span></button>'
                    + modal + '<script>' + (Path(__file__).parents[1] / 'hub/web/assets/i18n.js').read_text() + '</script><script>async function j(){return {email:"owner@example.test"}};'
                    'function stationdeskWhen(){return ""};function stationdeskIconNode(){return document.createElement("span")};'
                    'window.stationdeskRefreshHubTiles=async()=>true;</script><script>// Owner app store' + script + '</script>')
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for width in (1440, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={'width': width, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    finished = {'cad': False}

                    def route(request):
                        url = request.request.url
                        if url == 'https://station.test/':
                            return request.fulfill(body=document, content_type='text/html')
                        if '/updates/run' in url:
                            answer = {'run': {'status': 'running' if 'run_id=cad' in url and not finished['cad'] else 'failed', 'output': 'curl: (22) HTTP 404\nCAD 파일을 내려받지 못했습니다. 비공개 Release는 GH_TOKEN이 필요합니다.'}}
                        elif '/home/order' in url:
                            answer = {'order': ['sample-link']}
                        elif request.request.method == 'POST':
                            answer = {'ok': True, 'run_id': 'cad' if '/cad/' in url else 'addon'}
                        else:
                            answer = {'rows': [{'id': 'cad', 'name': 'CAD', 'origin': 'builtin', 'kind': 'app', 'state': 'install'},
                                               {'id': 'sample-link', 'name': 'Sample link', 'origin': 'link', 'kind': 'link', 'state': 'install'}]}
                        request.fulfill(body=json.dumps(answer), content_type='application/json')

                    page.route('**/*', route)
                    page.goto('https://station.test/')
                    page.locator('#store-open').click()
                    row = page.locator('[data-store-row=cad]')
                    row.locator('button').click()
                    note = row.locator('.store-run-note')
                    note.filter(has_text='in progress').wait_for()
                    page.locator('#store-tab-link').click()
                    link = page.locator('[data-store-row=sample-link]')
                    link.locator('[data-store-action=place-link]').click()
                    link.locator('.store-run-note').filter(has_text='Added to home.').wait_for()
                    finished['cad'] = True
                    page.locator('#store-tab-builtin').click()
                    note.filter(has_text='GH_TOKEN').wait_for()
                    self.assertNotIn('GH_TOKEN', link.inner_text())
                    self.assertEqual('true', page.locator('#store-tab-builtin').get_attribute('aria-selected'))
                    self.assertTrue(note.is_visible())
                    self.assertIn('HTTP 404', note.inner_text())
                    self.assertIn('CAD Install', note.inner_text())
                    page.locator('#store-tab-addon').click()
                    page.locator('#store-addon-source').fill('/missing/app')
                    page.locator('#store-addon-submit').click()
                    page.locator('#store-pane-addon #store-alert').filter(has_text='GH_TOKEN').wait_for()
                    self.assertEqual('true', page.locator('#store-tab-addon').get_attribute('aria-selected'))
                    self.assertTrue(page.locator('#store-alert').is_visible())
                    # A late Built-in result must leave another form's error visible.
                    finished['cad'] = False
                    page.locator('#store-tab-builtin').click()
                    row.locator('button').click()
                    note.filter(has_text='in progress').wait_for()
                    page.locator('#store-tab-addon').click()
                    page.locator('#store-addon-source').fill('/another/missing/app')
                    page.locator('#store-addon-submit').click()
                    alert = page.locator('#store-pane-addon #store-alert')
                    alert.filter(has_text='GH_TOKEN').wait_for()
                    finished['cad'] = True
                    page.wait_for_function('document.querySelector("[data-store-row=cad] .store-run-note").textContent.includes("GH_TOKEN")')
                    self.assertTrue(alert.is_visible())
                    self.assertEqual('true', page.locator('#store-tab-addon').get_attribute('aria-selected'))
                    self.assertEqual([], errors)
                    page.close()
            browser.close()

    def test_addon_and_link_forms_keep_failures_after_a_late_addon_result(self):
        source = (Path(__file__).parents[1] / 'hub/web/index.html').read_text()
        modal = source[source.index('<div class="store-modal"'):source.index('<main>')]
        script = source.split('<script>\n// Owner app store', 1)[1].split('</script>', 1)[0]
        styles = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', source, re.S))
        document = ('<meta charset="utf-8"><style>' + styles + '</style><button id="store-open"><span id="store-badge"></span></button>'
                    + modal + '<script>' + (Path(__file__).parents[1] / 'hub/web/assets/i18n.js').read_text() + '</script><script>async function j(){return {email:"owner@example.test"}};'
                    'function stationdeskWhen(){return ""};function stationdeskIconNode(){return document.createElement("span")};'
                    'window.stationdeskRefreshHubTiles=async()=>true;</script><script>// Owner app store' + script + '</script>')
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for width in (1440, 390):
                with self.subTest(width=width):
                    page = browser.new_page(viewport={'width': width, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    addon = {'attempts': 0, 'finished': False}

                    def route(request):
                        url = request.request.url
                        status = 200
                        if url == 'https://station.test/':
                            return request.fulfill(body=document, content_type='text/html')
                        if '/updates/run' in url:
                            answer = {'run': {'status': 'failed' if addon['finished'] else 'running',
                                              'output': '추가 앱 저장소를 찾을 수 없습니다.'}}
                        elif request.request.method == 'POST' and '/apps/links/add' in url:
                            status = 400
                            answer = {'ok': False, 'error': '링크 주소를 확인할 수 없습니다.'}
                        elif request.request.method == 'POST' and '/apps/add' in url:
                            addon['attempts'] += 1
                            if addon['attempts'] == 1:
                                status = 400
                                answer = {'ok': False, 'error': '추가 앱 폴더를 읽을 수 없습니다.'}
                            else:
                                answer = {'ok': True, 'run_id': 'addon-late'}
                        else:
                            answer = {'rows': []}
                        request.fulfill(status=status, body=json.dumps(answer), content_type='application/json')

                    page.route('**/*', route)
                    page.goto('https://station.test/')
                    page.locator('#store-open').click()
                    page.locator('#store-tab-addon').click()
                    page.locator('#store-addon-source').fill('/missing/app')
                    page.locator('#store-addon-submit').click()
                    addon_alert = page.locator('#store-pane-addon .store-alert')
                    addon_alert.filter(has_text='폴더를 읽을 수 없습니다').wait_for()
                    page.locator('#store-addon-source').fill('/late/missing/app')
                    page.locator('#store-addon-submit').click()
                    addon_alert.filter(has_text='in progress').wait_for()
                    page.locator('#store-tab-link').click()
                    page.locator('#store-link-name').fill('Bad link')
                    page.locator('#store-link-url').fill('https://example.test/missing')
                    page.locator('#store-link-submit').click()
                    link_alert = page.locator('#store-pane-link .store-alert')
                    link_alert.filter(has_text='링크 주소를 확인할 수 없습니다').wait_for()
                    addon['finished'] = True
                    addon_alert.filter(has_text='저장소를 찾을 수 없습니다').wait_for(state='attached')
                    self.assertEqual('true', page.locator('#store-tab-link').get_attribute('aria-selected'))
                    self.assertEqual(1, link_alert.count())
                    self.assertTrue(link_alert.is_visible())
                    self.assertIn('링크 주소를 확인할 수 없습니다', link_alert.inner_text())
                    page.locator('#store-tab-addon').click()
                    self.assertTrue(addon_alert.is_visible())
                    self.assertIn('저장소를 찾을 수 없습니다', addon_alert.inner_text())
                    page.locator('#store-tab-link').click()
                    self.assertIn('링크 주소를 확인할 수 없습니다', link_alert.inner_text())
                    self.assertEqual([], errors)
                    page.close()
            browser.close()
