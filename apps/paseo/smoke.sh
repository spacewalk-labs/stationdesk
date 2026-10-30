#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$STATIONDESK_APP_DIR/VERSION"
"$STATIONDESK_ROOT/runtime/node/bin/node" --version
actual=$("$STATIONDESK_ROOT/runtime/node/bin/node" -p "require(process.argv[1]).version" "$STATIONDESK_ROOT/runtime/paseo/lib/node_modules/@getpaseo/cli/package.json")
[ "$actual" = "$PASEO_VERSION" ]
code() { curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$@"; }
backend=$(code "http://127.0.0.1:$STATIONDESK_PORT/")
gate=$((STATIONDESK_PORT + 1))
owner=$(code -H "Tailscale-User-Login: $STATIONDESK_OWNER" "http://127.0.0.1:$gate/")
deny=$(code -H 'Tailscale-User-Login: nobody@example.invalid' "http://127.0.0.1:$gate/")
anonymous=$(code "http://127.0.0.1:$gate/")
[[ "$backend" = 200 || "$backend" = 302 ]]
[[ "$owner" = 200 || "$owner" = 302 ]]
[ "$deny" = 403 ] && [ "$anonymous" = 403 ]
headers=$(curl -sS --max-time 10 -D - -o /dev/null -H "Tailscale-User-Login: $STATIONDESK_OWNER" \
    "http://127.0.0.1:$gate/airlock-ui-state/v2/sidebar-project-workspace-order")
printf '%s\n' "$headers" | grep -qi '^X-Airlock-Revision:'
for service in stationdesk-paseo stationdesk-paseo-uistate stationdesk-paseo-browse-host; do
    systemctl --user is-active --quiet "$service.service"
done
printf '[paseo smoke] version=%s backend=%s owner=%s deny=%s anonymous=%s uistate-revision=yes services=3-active\n' \
    "$actual" "$backend" "$owner" "$deny" "$anonymous"
