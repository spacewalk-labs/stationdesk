#!/usr/bin/env bash
set -euo pipefail
python3 - "$STATIONDESK_ROOT/web/bench/apps.json" "$STATIONDESK_PORT" <<'PY'
import json
import sys
import urllib.request

apps = json.load(open(sys.argv[1]))['apps']
if not apps:
    raise SystemExit('Bench has no installed apps')
for app in apps:
    with urllib.request.urlopen(f'http://127.0.0.1:{sys.argv[2]}/{app["id"]}/', timeout=10) as response:
        if response.status != 200:
            raise SystemExit(f'{app["id"]}: HTTP {response.status}')
    print(f'{app["id"]}: HTTP 200')
PY
