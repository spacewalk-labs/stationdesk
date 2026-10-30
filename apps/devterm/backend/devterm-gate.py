#!/usr/bin/env python3
"""Loopback owner gate: static client, ttyd relay, local tmux and secret drop.

Transport and same-origin checks retain the old devterm gate's contracts.
No request target, body or exception text is logged: bodies may contain secrets.
"""
import asyncio
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import select
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

APP = Path(__file__).resolve().parents[1]
terminal_spec = importlib.util.spec_from_file_location('stationdesk_terminal', APP / 'backend/terminal.py')
terminal = importlib.util.module_from_spec(terminal_spec)
terminal_spec.loader.exec_module(terminal)
HOME = Path.home().resolve()
OWNER = os.environ.get('STATIONDESK_OWNER', '').lower()
PORT = int(os.environ.get('STATIONDESK_PORT', '19930'))
TTYD_PORT = PORT + 1
SECRETS = HOME / '.config/stationdesk/secrets'
SECRET_TTL = 1800
SECRET_LOCK = threading.Lock()
NAME = re.compile(r'[A-Za-z0-9_-]+\Z')


def same_origin(headers, required=False):
    origin = headers.get('Origin')
    if not origin:
        return not required
    parsed = urlsplit(origin)
    return (parsed.scheme in ('http', 'https') and not parsed.path
            and not parsed.query and not parsed.fragment
            and parsed.netloc.lower() == headers.get('Host', '').lower())


def secret_metadata():
    """Caller holds SECRET_LOCK. Sweep only the drop's own regular files."""
    SECRETS.mkdir(parents=True, exist_ok=True, mode=0o700)
    SECRETS.chmod(0o700)
    result = []
    for path in sorted(SECRETS.glob('*.txt')):
        if path.is_symlink() or not path.is_file():
            continue
        expires = path.stat().st_mtime + SECRET_TTL
        if expires <= time.time():
            path.unlink()
        else:
            result.append({'name': path.stem, 'expires_at': expires})
    return result


def tmux(*args):
    return subprocess.run(['tmux', *args], capture_output=True, text=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value, content_type='application/json'):
        body = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            # Second owner check, including direct loopback access and WS upgrades.
            if not OWNER or self.headers.get('Tailscale-User-Login', '').strip().lower() != OWNER:
                return self.reply(403, {'error': 'owner required'})
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            websocket = path == '/ws'
            secret = path in ('/secret-put', '/secret-list', '/secret-del')
            if self.command == 'POST' or websocket:
                if not same_origin(self.headers, required=secret):
                    return self.reply(403, {'error': 'same origin required'})
            if secret:
                if self.command != 'POST':
                    return self.reply(405, {'error': 'JSON POST required'})
                if self.headers.get_content_type() != 'application/json':
                    return self.reply(415, {'error': 'JSON required'})
            if path in ('/ws', '/token') and self.command == 'GET':
                return self.proxy()
            body = b''
            if self.command == 'POST':
                if self.headers.get('Transfer-Encoding'):
                    return self.reply(400, {'error': 'Content-Length required'})
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0:
                    return self.reply(400, {'error': 'invalid length'})
                body = self.rfile.read(length)
            original_headers = {key.lower().encode('latin1'): value.encode('latin1')
                                for key, value in self.headers.items()}
            if asyncio.run(terminal.dispatch(self.path, self.command, original_headers, body, self.reply)):
                return
            data = json.loads(body) if body and path != '/upload' else {}
            if not isinstance(data, dict):
                return self.reply(400, {'error': 'JSON object required'})
            if secret:
                return self.secret(path, data)
            if path in ('/sessions', '/session-delete') and self.command == 'POST':
                name = data.get('name', '')
                if not isinstance(name, str) or not name:
                    return self.reply(400, {'error': 'invalid session name'})
                if path == '/sessions':
                    cwd = data.get('cwd', str(HOME))
                    prompt = data.get('prompt')
                    params = data.get('params', [])
                    agent = data.get('agent', 'claude')
                    if (not isinstance(cwd, str) or not Path(cwd).expanduser().is_dir()
                            or (prompt is not None and not isinstance(prompt, str))
                            or not isinstance(params, list)
                            or not all(isinstance(arg, str) for arg in params)
                            or agent not in ('claude', 'codex', 'opencode', 'agy')):
                        return self.reply(400, {'error': 'invalid session launch'})
                    args = ['new-session', '-d', '-s', name, '-c', str(Path(cwd).expanduser())]
                    if prompt is not None or params or data.get('launch') is True:
                        # Keep an interactive pane after CLI completion for inspection.
                        argv = [agent, *params]
                        if prompt is not None:
                            argv += ['run', prompt] if agent == 'opencode' else [prompt]
                        args.append('export PATH=' + shlex.quote(str(HOME / '.local/bin'))
                                    + ':' + shlex.quote(str(HOME / '.opencode/bin')) + ':"$PATH"; ' + shlex.join(argv)
                                    + '; exec "${SHELL:-/bin/bash}" -l')
                    result = tmux(*args)
                else:
                    result = tmux('kill-session', '-t', '=' + name)
                return self.reply(200 if result.returncode == 0 else 409,
                                  {'ok': result.returncode == 0, 'name': name})
            if path == '/upload' and self.command == 'POST':
                name = Path(query.get('filename', ['upload'])[0]).name
                uploads = HOME / 'uploads'
                uploads.mkdir(exist_ok=True, mode=0o700)
                fd, target = tempfile.mkstemp(prefix='file-', suffix='-' + name, dir=uploads)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body)
                return self.reply(200, {'path': target})
            if path == '/file' and self.command == 'GET':
                target = Path(query.get('path', [''])[0]).expanduser().resolve()
                if not target.is_relative_to(HOME):
                    return self.reply(403, {'error': 'outside home'})
                return self.reply(200, target.read_bytes(), 'application/octet-stream')
            if self.command != 'GET':
                return self.reply(404, {'error': 'not found'})
            web = (Path(os.environ['STATIONDESK_ROOT']) / 'web/devterm').resolve()
            target = (web / unquote(path).lstrip('/')).resolve()
            if target == web:
                target = web / 'index.html'
            if not target.is_relative_to(web):
                return self.reply(403, {'error': 'outside web root'})
            return self.reply(200, target.read_bytes(), mimetypes.guess_type(target)[0] or 'application/octet-stream')
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error': 'invalid request'})
        except FileNotFoundError:
            self.reply(404, {'error': 'not found'})
        except (OSError, ConnectionError):
            # Fixed message only: errors can otherwise reveal paths or request data.
            self.close_connection = True

    def secret(self, path, data):
        with SECRET_LOCK:
            if path in ('/secret-put', '/secret-list'):
                items = secret_metadata()
            if path == '/secret-list':
                return self.reply(200, {'secrets': items})
            name = data.get('name', '')
            if not isinstance(name, str) or not NAME.fullmatch(name):
                return self.reply(400, {'error': 'invalid name'})
            target = SECRETS / (name + '.txt')
            if path == '/secret-del':
                target.unlink(missing_ok=True)
                return self.reply(200, {'name': name, 'deleted': True})
            value = data.get('value')
            if not isinstance(value, str):
                return self.reply(400, {'error': 'value must be text'})
            # Atomic replacement avoids following an existing name's symlink.
            fd, temporary = tempfile.mkstemp(dir=SECRETS)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(value)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self.reply(200, {'name': name, 'expires_at': target.stat().st_mtime + SECRET_TTL})

    def proxy(self):
        # One HTTP request per connection; a WS owns its connection after upgrade.
        with socket.create_connection(('127.0.0.1', TTYD_PORT)) as upstream:
            lines = [f'{self.command} {self.path} HTTP/1.1']
            lines += [f'{k}: {v}' for k, v in self.headers.items() if k.lower() != 'connection']
            lines += ['Connection: Upgrade' if self.path.split('?')[0] == '/ws' else 'Connection: close', '', '']
            upstream.sendall('\r\n'.join(lines).encode('latin1'))
            self.close_connection = True
            while True:
                readable, _, _ = select.select([self.connection, upstream], [], [])
                for source in readable:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    (upstream if source is self.connection else self.connection).sendall(chunk)


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
