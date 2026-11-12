#!/usr/bin/env bash
set -euo pipefail
# Cross-box card ingest: tailnet :19926 (the Airlock port, so senders need no change) behind a
# token-only gate. The engine renders the endpoint from this file and the fragment beside it.
dir="$STATIONDESK_STATE/dev-monitor"
mkdir -p "$dir"
cp "$STATIONDESK_APP_DIR/ingest.conf" "$dir/ingest.conf"
printf '[{"https_port": 19926, "gate_port": %d, "fragment": "dev-monitor/ingest.conf", "open": true}]\n' \
    "$((STATIONDESK_PORT + 1))" > "$dir/endpoints.json.tmp"
mv "$dir/endpoints.json.tmp" "$dir/endpoints.json"
