#!/usr/bin/env bash
set -euo pipefail
base="http://127.0.0.1:${STATIONDESK_PORT}"
curl --fail --silent --show-error -H "Tailscale-User-Login: ${STATIONDESK_OWNER}" "$base/" >/dev/null
curl --fail --silent --show-error \
    -H "Tailscale-User-Login: ${STATIONDESK_OWNER}" "$base/__stationdesk.json" |
    python3 -c 'import json,os,pathlib,sys; tiles=json.load(sys.stdin); ledger=json.loads((pathlib.Path(os.environ["STATIONDESK_STATE"])/"installed.json").read_text()); assert len([t for t in tiles if t.get("kind") != "link"]) == len([a for a in ledger["apps"].values() if a.get("kind") != "link"])'
curl --silent --show-error "$base/" | python3 -c 'import sys; assert "owner" in sys.stdin.read().lower()'
