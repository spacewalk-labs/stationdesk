#!/usr/bin/env bash
# Orca gate health: backend reachable, owner in, everyone else 403, patched client
# served with the Porthole, vendor client URL redirected to it.
set -uo pipefail
gate="http://127.0.0.1:$((STATIONDESK_PORT + 1))"
owner=(-H "Tailscale-User-Login: $STATIONDESK_OWNER")
other=(-H "Tailscale-User-Login: nobody@example.com")
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$@"; }
backend=$(code "http://127.0.0.1:$STATIONDESK_PORT/")
root_owner=$(code "${owner[@]}" "$gate/")
root_other=$(code "${other[@]}" "$gate/")
root_anonymous=$(code "$gate/")
client_owner=$(code "${owner[@]}" "$gate/orca-web/web-index.html")
client_other=$(code "${other[@]}" "$gate/orca-web/web-index.html")
client_anonymous=$(code "$gate/orca-web/web-index.html")
vendor=$(code "${owner[@]}" "$gate/web-index.html")
body=$(curl -s --max-time 6 "${owner[@]}" "$gate/orca-web/web-index.html")
echo "orca smoke: backend=$backend/200|302 owner=$root_owner/302 other=$root_other/403 anonymous=$root_anonymous/403"
echo "orca smoke: client owner=$client_owner/200 other=$client_other/403 anonymous=$client_anonymous/403 vendor=$vendor/302"
fail=0
[[ "$backend" == 200 || "$backend" == 302 ]] || { echo "FAIL backend (orca serve not reachable on 127.0.0.1:$STATIONDESK_PORT)"; fail=1; }
[[ "$root_owner" == 302 ]] || { echo 'FAIL owner'; fail=1; }
[[ "$root_other" == 403 && "$root_anonymous" == 403 ]] || { echo 'FAIL gate hole at /'; fail=1; }
[[ "$client_owner" == 200 ]] || { echo 'FAIL patched client not served at /orca-web/web-index.html'; fail=1; }
[[ "$client_other" == 403 && "$client_anonymous" == 403 ]] || { echo 'FAIL gate hole at /orca-web/'; fail=1; }
[[ "$vendor" == 302 ]] || { echo 'FAIL vendor /web-index.html not redirected to the patched client'; fail=1; }
[[ "$body" == *stationdesk-porthole.js* ]] || { echo 'FAIL Porthole not injected into the patched client'; fail=1; }
exit "$fail"
