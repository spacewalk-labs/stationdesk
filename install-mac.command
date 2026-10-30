#!/usr/bin/env bash
# OrbStack runs the Ubuntu installer; macOS never runs the Python engine.
set -euo pipefail
command -v orb >/dev/null || { echo 'Install OrbStack, then run this file again.' >&2; exit 1; }
orb info stationdesk >/dev/null 2>&1 || orb create ubuntu:24.04 stationdesk
orb -m stationdesk bash -s <<'UBUNTU'
set -euo pipefail
command -v tailscale >/dev/null || { echo "Install Tailscale and log in inside this Ubuntu machine, then run this .command file again." >&2; exit 1; }
installer=$(mktemp)
trap 'rm -f "$installer"' EXIT
curl -fsSL https://raw.githubusercontent.com/spacewalk-labs/stationdesk/main/install.sh -o "$installer"
bash "$installer"
UBUNTU
