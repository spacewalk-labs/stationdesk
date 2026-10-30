#!/usr/bin/env bash
set -euo pipefail
api="http://127.0.0.1:$STATIONDESK_PORT/fileview/api"
probe=$(mktemp -d "$HOME/.stationdesk-fileview-smoke.XXXXXX")
trap 'rm -rf "$probe"' EXIT
printf 'inside-home\n' > "$probe/inside.txt"
ln -s /etc "$probe/outside"
relative=${probe#"$HOME"}
token=$(curl --fail --silent --show-error -X POST -H 'Content-Type: application/json' -d '{}' "$api/login")
inside=$(curl --fail --silent --show-error -H "X-Auth: $token" "$api/raw$relative/inside.txt?algo=none")
[ "$inside" = inside-home ]
code=$(curl --silent --show-error -o /dev/null -w '%{http_code}' -H "X-Auth: $token" "$api/raw$relative/outside/passwd?algo=none")
printf 'fileview home outside symlink: %s (expected 403)\n' "$code"
[ "$code" = 403 ]
