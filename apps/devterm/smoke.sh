#!/usr/bin/env bash
set -euo pipefail
api="http://127.0.0.1:$STATIONDESK_PORT"
curl -fsS --max-time 10 -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$api/" | grep -F '<title>Terminal</title>' >/dev/null
curl -fsS --max-time 10 -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$api/token" | python3 -c 'import json,sys; assert "token" in json.load(sys.stdin)'
curl -fsS --max-time 10 -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$api/icon.svg" >/dev/null
for route in sessions tab-prefs recent-images; do
  curl -fsS --max-time 10 -H "Tailscale-User-Login: $STATIONDESK_OWNER" "$api/$route" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
done
printf 'devterm client, ttyd token, sessions, preferences, recent images: OK\n'
