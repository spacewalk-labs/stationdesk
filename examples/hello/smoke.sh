#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error "http://127.0.0.1:${STATIONDESK_PORT}/" |
    python3 -c 'import sys; assert "Hello from StationDesk" in sys.stdin.read()'
