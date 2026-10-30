"""Drive the shipped account list, sheet and Porthole menu against a stub account API."""
import json
import os
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

WEB = Path(__file__).parents[1] / 'hub/web'
SHOTS = os.environ.get('STATIONDESK_SHOTS')
FLOWS = {
    'claude': dict(url='https://claude.com/cai/oauth/authorize?code=true', code=None, input='code'),
    'codex': dict(url='https://auth.openai.com/codex/device', code='PEFB-WHRWW', input=None),
    'opencode': dict(url=None, code=None, input='key'),
    'agy': dict(url='https://accounts.google.com/o/oauth2/auth?x=1', code=None, input='code'),
}


def status(**overrides):
    rows = dict(
        claude=dict(id='claude', installed=True, logged_in=True, status='logged_in', plan='Max', email='owner@example.com'),
        codex=dict(id='codex', installed=True, logged_in=False, status='logged_out'),
        opencode=dict(id='opencode', installed=True, logged_in=True, status='logged_in', credentials=1, providers=['OpenAI']),
        agy=dict(id='agy', installed=True, logged_in=None, status='unsupported'))
    for agent, row in overrides.items():
        rows[agent] = rows[agent] | row
    return dict(agents=list(rows.values()), known=['claude', 'codex'], needs_login=False, status='critical')


@unittest.skipIf(sync_playwright is None, 'Playwright is not installed')
class AccountsBrowserTests(unittest.TestCase):
    def open(self, browser, url, width=1280):
        page = browser.new_page(viewport={'width': width, 'height': 860})
        state = dict(status=status(), actions=[], waiting=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request):
            path = request.request.url.split('://', 1)[1].split('/', 1)[1].split('?', 1)[0]
            if path == 'accounts/status.json':
                return request.fulfill(json=state['status'])
            if path == 'accounts/action':
                body = json.loads(request.request.post_data)
                state['actions'].append(body)
                reply = dict(ok=True)
                if body['action'] == 'login':
                    reply |= FLOWS[body['agent']]
                if body['action'] == 'check':
                    reply |= dict(running=state.get('running', True), waiting=state['waiting'])
                return request.fulfill(json=reply)
            if path == 'accounts/':
                path = 'accounts.html'
            elif path == 'app/':
                return request.fulfill(body='<body style="background:#0f141c"><script src="/assets/porthole.js"></script></body>',
                                       content_type='text/html; charset=utf-8')
            elif path in ('', 'hub/'):
                path = 'index.html'
            elif not path.startswith('assets/'):
                return request.fulfill(status=404, body='')
            file = WEB / path
            kind = {'.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}.get(file.suffix, 'text/html')
            request.fulfill(body=file.read_bytes(), content_type=kind + '; charset=utf-8')

        page.route('**/*', route)
        page.add_init_script("localStorage.setItem('stationdesk-language','ko')")
        page.goto(url)
        return page, state, errors

    def shot(self, page, name):
        if SHOTS:
            page.wait_for_timeout(400)
            page.screenshot(path=f'{SHOTS}/{name}.png')

    def sent(self, state, action):
        return [a for a in state['actions'] if a['action'] == action]

    def test_settings_list_confirms_logout_and_codex_finishes_without_a_terminal(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            page, state, errors = self.open(browser, 'https://station.test/')
            page.click('#settings-open')
            rows = page.locator('#set-accounts .acc-row')
            rows.first.wait_for()
            self.assertEqual(4, rows.count())
            self.assertIn('Max · owner@example.com', rows.nth(0).inner_text())
            self.assertIn('다시 로그인', rows.nth(1).inner_text())
            self.assertIn('OpenAI', rows.nth(2).inner_text())
            # agy cannot report its state, so it offers both actions.
            self.assertEqual(['로그인', '로그아웃'], rows.nth(3).locator('button').all_inner_texts())
            self.assertEqual('1개 다시 로그인 필요', page.inner_text('#set-acc-sum'))
            self.shot(page, 'settings')

            rows.nth(0).get_by_role('button', name='로그아웃').click()
            self.assertIn('이 박스에서만', rows.nth(0).inner_text())
            self.shot(page, 'settings-confirm')
            rows.nth(0).get_by_role('button', name='취소').click()
            self.assertEqual([], state['actions'])

            # Escape closes only the sheet, stops the hidden sign-in and returns focus.
            rows.nth(1).get_by_role('button', name='로그인').click()
            page.locator('.acc-code').wait_for()
            self.assertEqual('acc-x', page.evaluate('document.activeElement.className'))
            page.keyboard.press('Escape')
            self.assertEqual(0, page.locator('.acc-veil').count())
            self.assertFalse(page.locator('#settings-panel').is_hidden())
            self.assertEqual('codex', page.evaluate('document.activeElement.closest(".acc-row").dataset.agent'))
            page.wait_for_timeout(200)
            self.assertEqual([dict(agent='codex', action='cancel')], self.sent(state, 'cancel'))

            rows.nth(1).get_by_role('button', name='로그인').click()
            code = page.locator('.acc-code')
            code.wait_for()
            self.assertEqual('PEFB-WHRWW', code.inner_text())
            self.assertEqual('https://auth.openai.com/codex/device', page.locator('a.acc-open').get_attribute('href'))
            self.assertEqual(0, page.locator('iframe').count())
            self.shot(page, 'sheet-codex')
            state['status'] = status(codex=dict(logged_in=True, status='logged_in', method='ChatGPT'))
            page.get_by_text('로그인되었습니다').wait_for(timeout=8000)
            self.assertIn('ChatGPT 계정', page.inner_text('.acc-done'))
            self.shot(page, 'sheet-done')
            page.get_by_role('button', name='완료').click()
            self.assertEqual(0, page.locator('.acc-veil').count())
            self.assertIn('ChatGPT 계정', rows.nth(1).inner_text())
            self.assertEqual(1, len(self.sent(state, 'cancel')))   # a finished sign-in is not cancelled
            self.assertEqual([], errors)
            browser.close()

    def test_claude_code_and_opencode_key_are_pasted_into_the_sheet(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            page, state, errors = self.open(browser, 'https://station.test/accounts/')
            state['status'] = status(claude=dict(logged_in=False, status='logged_out'))
            page.evaluate('StationDeskAccounts.load()')
            rows = page.locator('.acc-row')
            rows.nth(0).get_by_role('button', name='로그인').click()
            page.locator('a.acc-open').wait_for()
            self.assertEqual(FLOWS['claude']['url'], page.locator('a.acc-open').get_attribute('href'))
            self.shot(page, 'sheet-claude')
            page.fill('.acc-entry input', '  abc#123  ')
            page.keyboard.press('Enter')
            page.get_by_text('확인하는 중…').wait_for()
            self.assertEqual([dict(agent='claude', action='submit', value='abc#123')], self.sent(state, 'submit'))
            state['status'] = status()
            page.get_by_text('로그인되었습니다').wait_for(timeout=8000)
            page.get_by_role('button', name='완료').click()

            rows.nth(2).get_by_role('button', name='로그아웃').click()
            rows.nth(2).get_by_role('button', name='로그아웃').click()
            self.assertEqual([dict(agent='opencode', action='logout', value='OpenAI')], self.sent(state, 'logout'))

            state['status'] = status(opencode=dict(logged_in=False, status='logged_out', credentials=0, providers=[]))
            page.evaluate('StationDeskAccounts.load()')
            rows.nth(2).get_by_role('button', name='로그인').click()
            page.locator('select.acc-input').select_option('anthropic')
            page.fill('input[type=password]', 'sk-ant-test')
            self.shot(page, 'sheet-opencode')
            page.get_by_role('button', name='저장').click()
            page.get_by_text('저장하는 중…').wait_for()
            page.wait_for_timeout(300)
            self.assertIn(dict(agent='opencode', action='login', value='anthropic'), state['actions'])
            self.assertIn(dict(agent='opencode', action='submit', value='sk-ant-test'), state['actions'])
            state['status'] = status(opencode=dict(credentials=1, providers=['Anthropic']))
            state['running'] = False
            page.get_by_text('로그인되었습니다').wait_for(timeout=8000)
            page.get_by_role('button', name='완료').click()

            # Replacing a stored key keeps the count at one and still succeeds.
            state['running'] = True
            rows.nth(2).get_by_role('button', name='로그아웃').wait_for()
            page.evaluate("StationDeskAccounts.openSheet('opencode', 'login')")
            page.locator('select.acc-input').select_option('anthropic')
            page.fill('input[type=password]', 'sk-ant-new')
            page.get_by_role('button', name='저장').click()
            page.get_by_text('저장하는 중…').wait_for()
            page.wait_for_timeout(600)
            self.assertEqual(0, page.locator('.acc-error').count())
            state['running'] = False
            page.get_by_text('로그인되었습니다').wait_for(timeout=8000)
            self.assertEqual([], errors)
            browser.close()

    def test_a_sheet_opened_before_any_status_read_loads_it_first(self):
        # Dev Monitor opens the sheet from its own cards, before this module read anything.
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            page, state, errors = self.open(browser, 'https://station.test/')
            page.evaluate("StationDeskAccounts.openSheet('opencode', 'logout')")
            page.get_by_role('button', name='OpenAI 키 삭제').click()
            page.wait_for_timeout(300)
            self.assertEqual([dict(agent='opencode', action='logout', value='OpenAI')], self.sent(state, 'logout'))
            self.assertEqual([], errors)
            browser.close()

    def test_porthole_menu_lists_accounts_and_logout_needs_a_click_in_the_panel(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for width in (1280, 390):
                with self.subTest(width=width):
                    page, state, errors = self.open(browser, 'https://station.test/app/', width)
                    porthole = page.locator('#stationdesk-porthole')
                    porthole.wait_for()
                    porthole.click()
                    menu = page.get_by_text('에이전트 계정', exact=True).locator('xpath=../..')
                    menu.get_by_text('Max · owner@example.com').wait_for()
                    self.assertIn('1개 다시 로그인 필요', menu.inner_text())
                    self.shot(page, f'porthole-{width}')

                    # Opening the logout page signs nobody out; the click inside it does.
                    menu.get_by_role('button', name='로그아웃').first.click()
                    panel = page.frame_locator('iframe[src*="do=logout"]')
                    panel.get_by_role('button', name='로그아웃').wait_for()
                    self.assertEqual([], self.sent(state, 'logout'))
                    self.shot(page, f'porthole-logout-{width}')
                    panel.get_by_role('button', name='로그아웃').click()
                    page.wait_for_timeout(300)
                    self.assertEqual([dict(agent='claude', action='logout')], self.sent(state, 'logout'))
                    page.keyboard.press('Escape')

                    porthole.click()
                    menu.get_by_role('button', name='로그인').first.click()
                    panel = page.frame_locator('iframe[src*="do=login"]')
                    panel.locator('.acc-code').wait_for()
                    self.shot(page, f'porthole-panel-{width}')
                    self.assertEqual([], errors)
                    page.close()
            browser.close()


if __name__ == '__main__':
    unittest.main()
