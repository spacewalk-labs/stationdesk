#!/usr/bin/env bash
set -euo pipefail
curl -fSs --max-time 10 -H "Tailscale-User-Login: $STATIONDESK_OWNER" \
    "http://127.0.0.1:$STATIONDESK_PORT/api/health" >/dev/null
