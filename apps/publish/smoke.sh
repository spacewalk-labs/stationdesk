#!/usr/bin/env bash
set -euo pipefail
name=".stationdesk-smoke-$$.txt"
source_file=$(mktemp "$HOME/.stationdesk-publish-XXXXXX")
cleanup() { rm -f "$STATIONDESK_SHARE/$name" "$STATIONDESK_SHARE/$name-folder" "$STATIONDESK_SHARE/_assets/$name" "$source_file"; rmdir "$STATIONDESK_SHARE/_assets" 2>/dev/null || true; }
trap cleanup EXIT
printf 'StationDesk publish smoke\n' > "$source_file"
api="http://127.0.0.1:$STATIONDESK_PORT"
gate="http://127.0.0.1:$((STATIONDESK_PORT + 1))"
data=$(python3 - "$source_file" "$name" <<'PY'
import json, sys
print(json.dumps({'source': sys.argv[1], 'name': sys.argv[2]}))
PY
)
curl -fsS -H "Tailscale-User-Login: $STATIONDESK_OWNER" -H "Origin: $api" -H 'Content-Type: application/json' -d "$data" "$api/publish" >/dev/null
[[ $(curl -sS -o /dev/null -w '%{http_code}' -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$gate/$name") == 200 ]]
curl -fsS -H "Tailscale-User-Login: $STATIONDESK_OWNER" -H "Origin: $api" -H 'Content-Type: application/json' -d "{\"name\":\"$name\"}" "$api/unpublish" >/dev/null
[[ $(curl -sS -o /dev/null -w '%{http_code}' -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$gate/$name") == 404 ]]
curl -fsS -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$gate/_assets/doc.css" >/dev/null
# The share's own _assets/ answers what the built-in assets do not have.
mkdir -p "$STATIONDESK_SHARE/_assets" && printf 'share asset\n' > "$STATIONDESK_SHARE/_assets/$name"
[[ $(curl -fsS -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$gate/_assets/$name") == 'share asset' ]]
# A directory without a trailing slash must keep the browser on its HTTPS origin.
data=$(python3 - "$STATIONDESK_APP_DIR/share-docs/assets" "$name-folder" <<'PYDATA'
import json, sys
print(json.dumps({'source': sys.argv[1], 'name': sys.argv[2]}))
PYDATA
)
curl -fsS -H "Tailscale-User-Login: $STATIONDESK_OWNER" -H "Origin: $api" -H 'Content-Type: application/json' -d "$data" "$api/publish" >/dev/null
headers=$(curl -sS -D - -o /dev/null -H 'Host: desk.example.test:8000' -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$gate/$name-folder")
grep -qi "^Location: /$name-folder/" <<< "$headers"
curl -fsS -H "Tailscale-User-Login: $STATIONDESK_OWNER" -H "Origin: $api" -H 'Content-Type: application/json' -d "{\"name\":\"$name-folder\"}" "$api/unpublish" >/dev/null
printf 'publish: shared file 200 -> unpublish 404; /_assets/doc.css 200; share _assets 200; folder redirect stays relative\n'
