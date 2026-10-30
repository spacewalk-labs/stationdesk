"""Owner launcher state and app-store projection over the StationDesk ledger."""
import hashlib
import json
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import home_order
from store import Store, REQUIRED, Catalog
from state_write import WRITE_LOCK


# Import the installer's existing digest definition; its CLI main guard stays idle.
_loader = importlib.machinery.SourceFileLoader('stationdesk_engine', str(Path(__file__).resolve().parents[3] / 'bin/stationdesk'))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_engine = importlib.util.module_from_spec(_spec)
_loader.exec_module(_engine)


class Hub:
    def __init__(self, root):
        self.root = Path(root)
        self.store = Store(root)
        self.order_path = self.root / 'state/home-order.json'

    def installed(self):
        return list(self.store.installed()) + list(Catalog(self.root).visible_links())

    def links(self):
        links = {key: dict(name=row['label'], url=row['url']) for key, row in self.store.installed().items() if row.get('kind') == 'link'}
        links.update(Catalog(self.root).links())
        return links

    def order(self, value=None):
        if value is None:
            return self._order()
        with WRITE_LOCK:
            return self._order(value)

    def _order(self, value=None):
        installed = [app_id for app_id in self.installed() if app_id != 'hub']
        order = (home_order.read_order(installed, self.order_path) if value is None else
                 home_order.write_order(value, installed, self.order_path))
        return dict(order=order, installed=installed, required=sorted(REQUIRED))

    def add_link(self, name, url):
        # Same personal-link input contract as the source launcher.
        if not isinstance(name, str) or not name.strip() or any(ord(c) < 32 for c in name):
            raise ValueError('링크 이름을 입력해 주세요. 줄바꿈이나 제어 문자는 사용할 수 없습니다.')
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError('공백 없는 https:// 주소를 입력해 주세요. 사용자 이름이나 비밀번호는 주소에 넣을 수 없습니다.')
        try:
            parsed = urlsplit(url)
            _ = parsed.port
        except ValueError:
            raise ValueError('링크 주소를 확인해 주세요. 호스트와 포트 번호가 올바르지 않습니다.') from None
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or '\\' in url or '%' in parsed.netloc:
            raise ValueError('공백 없는 https:// 주소를 입력해 주세요. 사용자 이름이나 비밀번호는 주소에 넣을 수 없습니다.')
        app_id = 'link-' + hashlib.sha256(name.strip().encode()).hexdigest()[:12]
        result = self.store.enqueue(['link', 'add', name.strip(), url])
        return dict(ok=True, id=app_id, run_id=result['unit'])

    def remove_link(self, app_id):
        if app_id not in self.links():
            raise ValueError('app_not_found')
        result = self.store.start('remove', app_id)
        return dict(ok=True, run_id=result['unit'])

    def updates(self):
        ledger_path = self.root / 'state/installed.json'
        ledger = json.loads(ledger_path.read_text())['apps'] if ledger_path.exists() else {}
        fetch_path = self.root / 'state/updates-fetch.json'
        fetch = json.loads(fetch_path.read_text()) if fetch_path.exists() else {}
        upstream = subprocess.run(['git', 'rev-parse', '--verify', '@{u}'], cwd=self.root, text=True, capture_output=True)
        target = upstream.stdout.strip() if upstream.returncode == 0 else 'HEAD'
        rows, unknown = [], []
        company = Catalog(self.root).apps()
        for app_id, installed in ledger.items():
            if installed.get('kind') == 'link':
                continue
            if installed.get('origin') == 'company':
                # A company app is current when it runs the index's bundle version.
                if app_id in company and company[app_id]['version'] != installed.get('version'):
                    rows.append(dict(id=app_id, action='upgrade', available=True))
                continue
            directory, _ = self.store.manifest(app_id)
            if installed.get('origin', 'builtin') != 'builtin' and not installed.get('repo'):
                if _engine.folder_digest(directory) != installed['commit']:
                    rows.append(dict(id=app_id, action='upgrade', available=True))
                continue
            repo = Path(installed.get('repo') or directory) if installed.get('origin', 'builtin') != 'builtin' else self.root
            tracked = subprocess.run(['git', 'rev-parse', '--verify', '@{u}'], cwd=repo, text=True, capture_output=True)
            ref = tracked.stdout.strip() if tracked.returncode == 0 else 'HEAD'
            result = subprocess.run(['git', 'diff', '--quiet', installed['commit'], ref, '--', str(directory.relative_to(repo))], cwd=repo, capture_output=True)
            if result.returncode == 1:
                rows.append(dict(id=app_id, action='upgrade', available=True))
            elif result.returncode != 0:
                unknown.append(app_id)
        ahead = subprocess.run(['git', 'rev-list', '--count', 'HEAD..' + target], cwd=self.root, text=True, capture_output=True)
        available = bool(int(ahead.stdout.strip())) if ahead.returncode == 0 and upstream.returncode == 0 else None
        error = fetch.get('error') or (None if upstream.returncode == 0 else 'Tracked upstream unavailable')
        return dict(checkedAt=fetch.get('checkedAt'), attemptedAt=fetch.get('attemptedAt'), error=error,
                    apps=rows, unknown_apps=unknown, platform=dict(available=available), harness=[])

    def apps(self):
        installed = set(self.installed())
        catalog = Catalog(self.root)
        company_links = catalog.links()
        updates = self.updates()
        updating = {r['id'] for r in updates['apps'] if r['action'] == 'upgrade'}
        if updates.get('error'):
            description = 'Update check failed: ' + updates['error']
            description_ko = '업데이트 확인 실패: ' + updates['error']
        elif updates['platform']['available']:
            description, description_ko = 'A new platform version is available', '새 플랫폼 버전이 있습니다'
        elif updates.get('checkedAt'):
            description, description_ko = 'No new platform version', '새 플랫폼 버전 없음'
        else:
            description, description_ko = 'Checking for updates', '업데이트 확인 중'
        rows = [dict(id='platform', name='StationDesk platform', name_ko='StationDesk 플랫폼',
                     origin='builtin', kind='platform', source='StationDesk', icon='/assets/favicon.svg',
                     state='update' if updates['platform']['available'] else 'installed',
                     desc=description, desc_ko=description_ko, detail={})]
        for app_id in self.store.candidates():
            directory, manifest = self.store.manifest(app_id)
            # The hub is the platform row; examples are for developers, listed only once installed.
            if app_id == 'hub' or (directory is not None and directory.parent == self.root / 'examples'
                                   and app_id not in installed):
                continue
            origin = self.store.origin(app_id)
            source = self.store.installed().get(app_id, {}).get('origin', manifest.get('source', 'StationDesk'))
            if origin == 'company':
                source = catalog.url or 'company'
                if not catalog.enabled:
                    origin = 'addon'
            elif origin != 'builtin':
                origin = 'addon'
            icon = '/monitor/api/store/icon?id=' + app_id if manifest['icon'] else ''
            if app_id in ('devterm', 'fileview', 'publish', 'dev-monitor', 'paseo', 'cad', 'bench'):
                icon = '/assets/app-icons/' + app_id + ('.svg' if app_id == 'cad' else '.png')
            rows.append(dict(id=app_id, name=manifest['label'], origin=origin, kind='app',
                             state=('update' if app_id in updating else 'installed') if app_id in installed else 'install',
                             icon=icon, desc=manifest.get('description', ''), desc_ko=manifest.get('description_ko', ''), source=source,
                             visibility=manifest.get('visibility'), detail={'Path': str(directory)} if directory is not None else {}))
        for app_id, link in self.links().items():
            rows.append(dict(id=app_id, name=link['name'], url=link['url'], origin='company' if app_id in company_links else 'link', kind='link', icon=link.get('icon', ''), glyph=link.get('glyph', ''), desc=link.get('desc', ''), source=link['url'], state='installed' if app_id in installed else 'install', detail={}))
        return dict(rows=rows, updates=updates, company_enabled=catalog.enabled)
