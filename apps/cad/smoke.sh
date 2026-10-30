#!/usr/bin/env bash
set -euo pipefail
curl -fSs "http://127.0.0.1:$STATIONDESK_PORT/cad/api/" |
    python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin)["folders"], list)'
