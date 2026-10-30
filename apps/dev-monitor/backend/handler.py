"""Small route table kept here so the next stage can add card routes directly."""
from http.server import BaseHTTPRequestHandler
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import secrets
import sys
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards import Cards
from card_delivery import ingest_token, slack_config, grace
from urllib.parse import parse_qs, unquote, urlsplit

import accounts
import observability as observe
import devmon_cron
import settings
from collectors import collect
from store import Store, Catalog
from hub import Hub
import feedback


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, data, content_type='application/json'):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        owner = os.environ.get('STATIONDESK_OWNER')
        return bool(owner) and self.headers.get('Tailscale-User-Login') == owner

    def same_origin(self):
        # Serve terminates TLS before nginx; the engine supplies its public FQDN.
        fqdn = os.environ.get('STATIONDESK_FQDN')
        expected = 'https://' + fqdn if fqdn else 'http://' + self.headers.get('Host', '')
        return self.headers.get('Origin') == expected

    def do_GET(self):
        if not self.authorized():
            return self.reply(403, {'error': 'Owner only'})
        route = urlsplit(self.path)
        root = Path(os.environ['STATIONDESK_ROOT'])
        store = Store(root)
        try:
            if route.path == '/feedback/api/health':
                return self.reply(200, feedback.health())
            if route.path == '/api/owner/delivery-health':
                return self.reply(200, dict(slack='configured' if settings.slack_status()['configured'] else 'not configured', **Cards(root).delivery_health()))
            if route.path == '/api/owner/slack':
                return self.reply(200, settings.slack_status())
            if route.path == '/api/overview':
                return self.reply(200, dict(host=observe.host_info(), cpu=observe.cpu_info(), memory=observe.mem_info(), disk=observe.disk_info('/')))
            if route.path == '/api/services':
                return self.reply(200, observe._services_payload())
            if route.path == '/api/network':
                return self.reply(200, observe.network_info())
            if route.path == '/api/storage':
                return self.reply(200, {'items': observe.storage_info()})
            if route.path == '/api/history':
                return self.reply(200, observe.history_summary())
            if route.path == '/api/cron/jobs':
                return self.reply(200, devmon_cron.snapshot())
            if route.path == '/api/top':
                query = parse_qs(route.query)
                order = query.get('sort', ['cpu'])[0]
                count = int(query.get('n', ['10'])[0])
                return self.reply(200, dict(sort_by=order, processes=observe.top_processes(count, order)))
            if route.path == '/api/logs':
                query = parse_qs(route.query)
                unit = query.get('unit', ['stationdesk-dev-monitor'])[0]
                return self.reply(200, dict(unit=unit, lines=observe.recent_logs(unit, int(query.get('n', ['10'])[0]))))
            if route.path == '/accounts/status.json':
                return self.reply(200, accounts.status(root))
            if route.path in ('/health', '/api/health'):
                return self.reply(200, {'ok': True})
            if route.path in ('/api/owner/messages/preview', '/api/owner/preview'):
                return self.reply(200, Cards(root).preview())
            if route.path == '/api/owner/messages':
                query = parse_qs(route.query)
                scope = query.get('scope', ['archived' if query.get('archived') == ['1'] else 'active'])[0]
                return self.reply(200, Cards(root).feed(scope))
            if route.path == '/api/system':
                return self.reply(200, collect(root))
            if route.path == '/api/owner/home/order':
                return self.reply(200, Hub(root).order())
            if route.path == '/api/owner/apps':
                return self.reply(200, Hub(root).apps())
            if route.path == '/api/owner/updates/run':
                unit = parse_qs(route.query).get('run_id', [''])[0]
                progress = store.progress(unit)
                return self.reply(200, {'run': {'status': 'running' if progress['active'] else 'done' if progress['result'] == 'success' else 'failed', 'output': progress['output']}})
            if route.path == '/api/store':
                return self.reply(200, store.list())
            if route.path == '/api/store/progress':
                return self.reply(200, store.progress(parse_qs(route.query).get('unit', [''])[0]))
            if route.path == '/api/store/icon':
                directory, manifest = store.manifest(parse_qs(route.query).get('id', [''])[0])
                # An uninstalled company app's icon was fetched with the index.
                path = (directory or Catalog(root).directory) / manifest['icon']
            else:
                # Only the installed dashboard assets are public routes.
                filename = {'/': 'index.html', '/monitor.js': 'monitor.js', '/icon.svg': 'icon.svg', '/stationdesk-return.js': 'stationdesk-return.js', '/stationdesk-mark.svg': 'stationdesk-mark.svg'}.get(route.path)
                if filename is None and route.path.startswith('/devmon-') and '/' not in route.path[1:] and route.path.endswith(('.js', '.css')):
                    filename = route.path[1:]
                if filename is None:
                    return self.reply(404, {'error': 'Unknown route'})
                path = root / 'web/dev-monitor' / filename
            return self.reply(200, path.read_bytes(), mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            self.reply(400, {'error': str(error)})

    def do_POST(self):
        route = urlsplit(self.path).path
        ingest = route == '/api/ingest'
        if ingest:
            token = self.headers.get('X-StationDesk-Token') or self.headers.get('Authorization', '').removeprefix('Bearer ')
            if not token or not secrets.compare_digest(token, ingest_token(os.environ['STATIONDESK_ROOT'])):
                return self.reply(401, {'error': 'Ingest token required'})
        elif not self.authorized() or not self.same_origin():
            return self.reply(403, {'error': 'Owner and same origin required'})
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Content-Type must be application/json')
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            if not isinstance(data, dict):
                raise ValueError('Expected JSON object')
            root = os.environ['STATIONDESK_ROOT']
            if route == '/feedback/api/submit':
                ok, result = feedback.submit_feedback(data.get('text', ''), self.headers.get('Tailscale-User-Login', ''))
                return self.reply(200 if ok else 400, {'ok': True, **result} if ok else {'ok': False, 'error': result})
            if route == '/api/owner/slack':
                return self.reply(200, settings.save_slack(data))
            if route == '/api/owner/service/restart':
                ok, message = observe.restart_svc(data.get('name'))
                return self.reply(200 if ok else 400, dict(ok=ok, message=message))
            if ingest:
                return self.reply(202, {'status': Cards(root).ingest(data, grace(slack_config()))})
            hub = Hub(root)
            if route == '/api/owner/home/order':
                return self.reply(200, hub.order(data['order']))
            if route == '/api/owner/apps/links/add':
                return self.reply(200, hub.add_link(data.get('name'), data.get('url')))
            if route == '/api/owner/apps/remove-link':
                return self.reply(200, hub.remove_link(data['id']))
            if route == '/api/owner/apps/add':
                result = Store(root).add_source(data.get('source'))
                return self.reply(202, {'ok': True, 'run_id': result['unit']})
            if route == '/api/owner/updates/execute':
                action = data['action']
                if action not in ('app', 'platform'):
                    raise ValueError('bad_action')
                result = Store(root).start('update', data['id'] if action == 'app' else None)
                return self.reply(202, {'ok': True, 'run_id': result['unit']})
            app_prefix = '/api/owner/apps/'
            if route.startswith(app_prefix):
                app_id, action = route[len(app_prefix):].rsplit('/', 1)
                if action in ('install', 'remove'):
                    result = Store(root).start('add' if action == 'install' else 'remove', app_id)
                else:
                    raise ValueError('bad_action')
                return self.reply(202, {'ok': True, 'run_id': result['unit']})
            if route == '/api/owner/run':
                return self.reply(200, Cards(root).run(data['card_id'], data.get('params', {}), data.get('note', '')))
            if route == '/api/owner/run/window':
                return self.reply(200, Cards(root).window(data['card_id']))
            prefix = '/api/owner/messages/'
            if route.startswith(prefix):
                card_id, action = route[len(prefix):].rsplit('/', 1)
                card_id = unquote(card_id)
                cards = Cards(root)
                if action == 'run':
                    return self.reply(200, cards.run(card_id, data.get('params', {}), data.get('note', '')))
                if action in ('read', 'archive'):
                    return self.reply(200, cards.transition(card_id, action))
            if route == '/accounts/action':
                return self.reply(200, accounts.action(data['agent'], data['action'], root))
            if route == '/api/store':
                return self.reply(202, Store(root).start(data['action'], data['id']))
            return self.reply(404, {'error': 'Unknown route'})
        except urllib.error.HTTPError as error:
            self.reply(502, {'error': 'devterm rejected session', 'status': error.code})
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            self.reply(400, {'error': str(error)})
