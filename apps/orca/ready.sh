#!/usr/bin/env bash
# ExecStartPost of stationdesk-orca.service: the unit is active once the runtime
# listens. Its web client URL carries the pairing and is printed only to serve.log;
# keep the #pairing=<blob> fragment so the gate root opens the workbench with no
# paste. systemd's TimeoutStartSec bounds the wait for the listener.
set -euo pipefail
until ss -ltnH "sport = :$STATIONDESK_PORT" | grep -q .; do sleep 1; done
pairing=""
for _ in $(seq 30); do
    url=$(grep -oE 'https://[^ "]*web-index\.html#pairing=[A-Za-z0-9._~%+/=-]+' "$RUNTIME_DIRECTORY/serve.log" | tail -1 || true)
    if [[ -n "$url" ]]; then pairing="#pairing=${url#*#pairing=}"; break; fi
    sleep 1
done
[[ -n "$pairing" ]] || echo 'Orca printed no pairing URL; /orca-web/ will ask to pair once' >&2
conf="$STATIONDESK_STATE/orca/pairing.conf"
line="set \$orca_pairing \"$pairing\";"
if [[ "$(cat "$conf" 2>/dev/null)" != "$line" ]]; then
    (umask 077; printf '%s\n' "$line" > "$conf.tmp")
    mv "$conf.tmp" "$conf"
    # The blob is stable across restarts; a new one reaches a running nginx here.
    if [[ -s "$STATIONDESK_STATE/nginx.pid" ]]; then
        /usr/sbin/nginx -q -s reload -c "$STATIONDESK_STATE/nginx.conf" || true
    fi
fi
