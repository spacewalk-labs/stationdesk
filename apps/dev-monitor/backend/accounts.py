"""Account status and official CLI flows. Credentials belong exclusively to vendors.

Sign-in runs each vendor's own login command in a private tmux server and reads the
link, device code or prompt it prints, so the owner approves in a browser and pastes
back only what the vendor asks for — no terminal, and nothing here stores a token.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

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
        # Only named fields leave the backend; the owner chose to see the account email.
        detail = {}
        if agent == 'claude':
            data = json.loads(result.stdout)
            logged_in = data['loggedIn']
            if not isinstance(logged_in, bool):
                raise ValueError('Invalid status')
            if logged_in:
                plan, email = data.get('subscriptionType'), data.get('email')
                detail = dict(plan=plan.capitalize() if isinstance(plan, str) and plan else None,
                              email=email if isinstance(email, str) and email else None)
        elif agent == 'codex':
            if 'Not logged in' in output:
                logged_in = False
            elif result.returncode == 0 and 'Logged in' in output:
                logged_in = True
                detail = dict(method='ChatGPT' if 'ChatGPT' in output else 'API key' if 'API key' in output else None)
            else:
                raise ValueError('Unknown status')
        else:
            plain = ANSI.sub('', output)
            match = re.search(r'(\d+) credentials?', plain)
            if result.returncode or match is None:
                raise ValueError('Unknown status')
            logged_in = int(match[1]) > 0
            # Provider names from the stored-credentials block, never the environment block.
            stored = plain.split('Environment', 1)[0]
            detail = dict(credentials=int(match[1]),
                          providers=re.findall(r'●\s+(.+?)\s+(?:api|oauth|wellknown)\s*$', stored, re.M))
        return row | detail | dict(logged_in=logged_in, status='logged_in' if logged_in else 'logged_out')
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
    return dict(agents=rows, needs_login=not known, known=sorted(known),
                status='critical' if critical else 'warning' if warning else 'ok')



# ---- sign-in ----------------------------------------------------------------
# What each login prints and what it then waits for (verified against the CLIs
# 2026-10-30). `input` is what the owner pastes back: an approval code or an API key.
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
LOGIN = {
    'claude': dict(link=r'https://claude\.com/cai/oauth/authorize\S+', input='code'),
    'codex': dict(link=r'https://auth\.openai\.com/codex/device\S*', code=r'\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b', input=None),
    'opencode': dict(ready='Enter your API key', input='key'),
    'agy': dict(link=r'https://accounts\.google\.com/o/oauth2/auth\S+', choose='Select login method', input='code'),
}
LOGIN_WAIT = 20
TMUX_SOCKET = 'stationdesk-accounts'
PROVIDER = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}')


def tmux(*args, data=None):
    # A private server keeps login sessions out of devterm's session list.
    return subprocess.run(['tmux', '-L', TMUX_SOCKET, *args], input=data,
                          capture_output=True, timeout=10)


def screen(agent):
    result = tmux('capture-pane', '-J', '-p', '-t', 'login-' + agent)
    return None if result.returncode else ANSI.sub('', result.stdout.decode('utf-8', 'replace'))


def login_start(agent, binary, provider):
    flow = LOGIN[agent]
    if agent == 'opencode':
        if not isinstance(provider, str) or not PROVIDER.fullmatch(provider):
            raise ValueError('Choose a provider')
        argv = [binary, 'auth', 'login', '-p', provider, '-m', 'Manually enter API Key']
    else:
        argv = [binary, *COMMANDS[agent][1]]
    tmux('kill-session', '-t', 'login-' + agent)
    started = tmux('new-session', '-d', '-x', '1000', '-y', '50', '-s', 'login-' + agent,
                   '-e', 'BROWSER=/bin/false', *argv)
    if started.returncode:
        raise ValueError('Could not start the official sign-in')
    chosen = False
    deadline = time.monotonic() + LOGIN_WAIT
    while time.monotonic() < deadline:
        text = screen(agent)
        if text is None:
            break
        if flow.get('choose') and not chosen and flow['choose'] in text:
            tmux('send-keys', '-t', 'login-' + agent, 'Enter')
            chosen = True
        link = re.search(flow['link'], text) if 'link' in flow else None
        code = re.search(flow['code'], text) if 'code' in flow else None
        if (link and ('code' not in flow or code)) or ('ready' in flow and flow['ready'] in text):
            return dict(ok=True, url=link[0] if link else None, code=code[0] if code else None,
                        input=flow['input'])
        time.sleep(0.5)
    tmux('kill-session', '-t', 'login-' + agent)
    if agent == 'agy':
        raise ValueError('agy did not ask to sign in; it may already be signed in')
    raise ValueError('The official sign-in did not show a link')


def login_submit(agent, value):
    if LOGIN[agent]['input'] is None:
        raise ValueError('This sign-in needs no input')
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 4096 or re.search(r'[\x00-\x1f\x7f]', value.strip()):
        raise ValueError('Paste the code or key on one line')
    # The value reaches the CLI through a tmux buffer read from stdin: never argv, never disk.
    name = 'login-' + agent
    if screen(agent) is None:
        raise ValueError('The sign-in has ended. Start again.')
    if tmux('load-buffer', '-b', name, '-', data=value.strip().encode()).returncode \
            or tmux('paste-buffer', '-d', '-b', name, '-t', name).returncode \
            or tmux('send-keys', '-t', name, 'Enter').returncode:
        raise ValueError('Could not hand the value to the sign-in')
    return dict(ok=True)


def login_check(agent):
    # agy has no status command: its sign-in is done once it stops asking for a code.
    text = screen(agent)
    running = text is not None
    waiting = running and 'authorization code' in text
    if running and not waiting and agent == 'agy':
        tmux('kill-session', '-t', 'login-' + agent)   # signed in; agy's own prompt is not needed
    return dict(ok=True, running=running, waiting=waiting)


def action(agent, operation, root, value=None):
    if agent not in COMMANDS or operation not in ('login', 'submit', 'check', 'cancel', 'logout'):
        raise ValueError('Unknown account action')
    if operation == 'cancel':
        tmux('kill-session', '-t', 'login-' + agent)
        return dict(ok=True)
    if operation == 'submit':
        return login_submit(agent, value)
    if operation == 'check':
        return login_check(agent)
    binary = executable(agent)
    if not binary:
        raise ValueError('CLI is not installed')
    if operation == 'login':
        return login_start(agent, binary, value)
    if agent == 'agy':
        # agy signs out from inside its own prompt.
        tmux('kill-session', '-t', 'login-agy')
        if tmux('new-session', '-d', '-x', '200', '-y', '50', '-s', 'login-agy', binary).returncode:
            raise ValueError('Could not start agy')
        time.sleep(3)
        tmux('send-keys', '-t', 'login-agy', '-l', '/logout')
        tmux('send-keys', '-t', 'login-agy', 'Enter')
        time.sleep(3)
        tmux('kill-session', '-t', 'login-agy')
        return dict(ok=True)
    command = COMMANDS[agent][2]
    if agent == 'opencode':
        if not isinstance(value, str) or not PROVIDER.fullmatch(value):
            raise ValueError('Choose a provider to sign out of')
        command = [*command, value]
    result = subprocess.run([binary, *command], capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('Official logout command failed')
    return dict(ok=True)
