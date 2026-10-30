#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$STATIONDESK_SHARE"

# External publishing. With no target configured in state/publish.env, publish through
# Tailscale Funnel (10000, else 8443; never 443, which is the hub). An explicitly
# configured target keeps its own behaviour and opens no Funnel.
dir="$STATIONDESK_STATE/publish"
mkdir -p "$dir"
python3 - "$dir" "$STATIONDESK_STATE/publish.env" "$STATIONDESK_FQDN" "$STATIONDESK_PORT" "$STATIONDESK_APP_DIR/funnel.conf" <<'PY'
import json, os, re, shutil, subprocess, sys
directory, env_file, fqdn, port, template = sys.argv[1:]
endpoints = os.path.join(directory, 'funnel-endpoints.json')
funnel_env = os.path.join(directory, 'funnel.env')
configured = os.path.exists(env_file) and re.search(
    r'(?m)^\s*(?:export\s+)?STATIONDESK_PUBLISH_(?:PUBLIC_MODE|INGEST_URL|BASE_URL)\s*=', open(env_file).read())

def tailscale_json(*args):
    try:
        result = subprocess.run(['tailscale', *args, '--json'], text=True, capture_output=True, timeout=10)
        return json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}

def write(path, content):
    with open(path + '.tmp', 'w') as stream:
        stream.write(content)
    os.replace(path + '.tmp', path)

def previous_port():
    try:
        return json.load(open(endpoints))[0]['https_port']
    except (OSError, ValueError, IndexError, KeyError):
        return None

chosen = None
if not configured and fqdn and shutil.which('tailscale'):
    taken = {int(p) for p in (tailscale_json('serve', 'status').get('TCP') or {})}
    chosen = previous_port() or next((p for p in (10000, 8443) if p not in taken), None)
    if chosen is None:
        print('publish: ports 10000 and 8443 are both taken; external publishing stays off', file=sys.stderr)
if chosen is None:
    write(endpoints, '[]\n')
    if os.path.exists(funnel_env):
        os.remove(funnel_env)
    sys.exit(0)
public = os.path.join(directory, 'public')
for name in ('public', 'gated', 'htpasswd'):
    os.makedirs(os.path.join(directory, name), exist_ok=True)
shutil.copyfile(template, os.path.join(directory, 'funnel.conf'))
write(funnel_env, '\n'.join([
    'STATIONDESK_PUBLISH_PUBLIC_MODE=local',
    f'STATIONDESK_PUBLISH_BASE_URL=https://{fqdn}:{chosen}',
    f'STATIONDESK_PUBLISH_PUBLIC_DIR={public}',
    f'STATIONDESK_PUBLISH_GATED_DIR={directory}/gated',
    f'STATIONDESK_PUBLISH_HTPASSWD_DIR={directory}/htpasswd',
    f'STATIONDESK_PUBLISH_FUNNEL_PORT={chosen}', '']))
write(endpoints, json.dumps([{'https_port': chosen, 'gate_port': int(port) + 2,
                              'fragment': 'publish/funnel.conf', 'funnel': True}]) + '\n')
PY
