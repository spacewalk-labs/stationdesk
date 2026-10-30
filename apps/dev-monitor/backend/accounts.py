"""Account status and official CLI flows. Credentials belong exclusively to vendors."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

from state_write import WRITE_LOCK

COMMANDS = {
    'claude': (['auth', 'status'], ['auth', 'login'], ['auth', 'logout']),
    'codex': (['login', 'status'], ['login', '--device-auth'], ['logout']),
    'opencode': (['auth', 'list'], ['auth', 'login'], ['auth', 'logout']),
    # agy has no documented noninteractive status/logout interface.
    'agy': (None, [], []),
}


def executable(agent):
    path = os.pathsep.join([str(Path.home() / '.local/bin'), str(Path.home() / '.opencode/bin'), os.environ.get('PATH', '')])
    return shutil.which(agent, path=path)


def status_one(agent):
    command, _, _ = COMMANDS[agent]
    binary = executable(agent)
    row = dict(id=agent, installed=bool(binary), logged_in=None, status='unknown')
    if not binary:
        return row | dict(status='not_installed')
    if command is None:
        return row | dict(status='unsupported')
    try:
        result = subprocess.run([binary, *command], capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        if agent == 'claude':
            logged_in = json.loads(result.stdout)['loggedIn']
            if not isinstance(logged_in, bool):
                raise ValueError('Invalid status')
        elif agent == 'codex':
            if 'Not logged in' in output:
                logged_in = False
            elif result.returncode == 0 and 'Logged in' in output:
                logged_in = True
            else:
                raise ValueError('Unknown status')
        else:
            import re
            match = re.search(r'(\d+) credentials?', output)
            if result.returncode or match is None:
                raise ValueError('Unknown status')
            logged_in = int(match[1]) > 0
        return row | dict(logged_in=logged_in, status='logged_in' if logged_in else 'logged_out')
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return row


def status(root):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(status_one, COMMANDS))
    # Remember only agent IDs, never vendor output or credentials. Keep an ID after
    # logout so the warning survives repeated polls and backend restarts.
    path = Path(root) / 'state/accounts.json'
    with WRITE_LOCK:
        known = set(json.loads(path.read_text())['logged_in']) if path.exists() else set()
        observed = {row['id'] for row in rows if row['logged_in'] is True}
        if observed - known:
            known |= observed
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix='.accounts.', dir=path.parent)
            try:
                with os.fdopen(descriptor, 'w') as handle:
                    handle.write(json.dumps(dict(logged_in=sorted(known))) + '\n')
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    critical = any(row['id'] in known and row['logged_in'] is False for row in rows)
    warning = any(row['id'] in known and row['logged_in'] is None for row in rows)
    return dict(agents=rows, needs_login=not known,
                status='critical' if critical else 'warning' if warning else 'ok')


def action(agent, operation, root):
    if agent not in COMMANDS or operation not in ('login', 'logout'):
        raise ValueError('Unknown account action')
    binary = executable(agent)
    if not binary:
        raise ValueError('CLI is not installed')
    command = COMMANDS[agent][1 if operation == 'login' else 2]
    if command is None:
        raise ValueError('This CLI has no documented logout command')
    if operation == 'logout' and agent not in ('opencode', 'agy'):
        result = subprocess.run([binary, *command], capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError('Official logout command failed')
        return dict(ok=True)
    # OpenCode logout is interactive (provider selection), just like login.
    name = 'account-' + uuid.uuid4().hex[:12]
    import tomllib
    port = tomllib.loads((Path(root) / 'apps/devterm/app.toml').read_text())['port']
    data = dict(name=name, agent=agent, params=command, launch=True)
    request = urllib.request.Request(f'http://127.0.0.1:{port}/sessions', data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', 'Tailscale-User-Login': os.environ.get('STATIONDESK_OWNER', '')})
    with urllib.request.urlopen(request, timeout=10) as response:
        if not json.load(response).get('ok'):
            raise ValueError('devterm session failed')
    return dict(ok=True, url='/devterm/?session=' + name)
