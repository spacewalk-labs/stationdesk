#!/usr/bin/env bash
# Run as the future StationDesk owner on Ubuntu 24.04.
set -euo pipefail

paseo=true
for argument in "$@"; do
    case "$argument" in
        --no-paseo) paseo=false ;;
        *) echo "Unknown option: $argument" >&2; exit 1 ;;
    esac
done
root=/opt/stationdesk
repo=${STATIONDESK_REPO:-https://github.com/spacewalk-labs/stationdesk.git}
for command in python3 systemctl git curl sudo; do
    command -v "$command" >/dev/null || { echo "Required command: $command" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
    echo 'Python 3.11 or newer is required.' >&2; exit 1;
}
systemctl --user show-environment >/dev/null || {
    echo 'Run from a user session with systemd --user available.' >&2; exit 1;
}
# LXD smoke uses the same owner fallback as the engine, without tailnet credentials.
identity=$(python3 - "$root" <<'PY'
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

owner = fqdn = ''
if shutil.which('tailscale'):
    result = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True)
    if result.returncode == 0:
        status = json.loads(result.stdout)
        self = status.get('Self') or {}
        user = status.get('User', {}).get(str(self.get('UserID')), {})
        owner = user.get('LoginName', '') or self.get('LoginName', '')
        fqdn = self.get('DNSName', '').rstrip('.')
box = Path(sys.argv[1]) / 'state/box.toml'
if box.exists():
    values = tomllib.loads(box.read_text())
    owner = owner or values.get('owner', '')
    fqdn = fqdn or values.get('fqdn', '')
if not owner:
    sys.exit('Log in to Tailscale before installing StationDesk.')
print(fqdn)
PY
)

printf '%s\n' 'sudo will perform:' \
    '  apt: install nginx, ttyd, tmux, python3-venv and unzip if missing' \
    '  disable --now ttyd.service (distribution login listener)' \
    "  create $root owned by $(id -un)" \
    "  enable linger for $(id -un)" \
    "  set Tailscale operator to $(id -un) (if Tailscale is installed)"
sudo bash -s -- "$(id -un)" "$(id -gn)" "$root" <<'ROOT'
set -euo pipefail
if ! dpkg-query -W -f='${Status}' nginx 2>/dev/null | grep -q 'install ok installed'; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
    systemctl disable --now nginx
fi
missing=()
for package in ttyd tmux python3-venv unzip xz-utils; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
        missing+=("$package")
    fi
done
if ((${#missing[@]})); then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
fi
systemctl disable --now ttyd.service
install -d -m 755 -o "$1" -g "$2" "$3"
loginctl enable-linger "$1"
if command -v tailscale >/dev/null; then
    tailscale set --operator="$1"
fi
ROOT

if [[ -d "$root/.git" ]]; then
    git -C "$root" fetch origin
    git -C "$root" merge --ff-only '@{upstream}'
else
    checkout=$(mktemp -d)
    trap 'rm -rf "$checkout"' EXIT
    git clone -- "$repo" "$checkout"
    cp -a "$checkout/." "$root/"
    rm -rf "$checkout"
    trap - EXIT
fi
mkdir -p "$HOME/.local/bin"
ln -sfn "$root/bin/stationdesk" "$HOME/.local/bin/stationdesk"
ln -sfn "$root/bin/stationdesk-card" "$HOME/.local/bin/stationdesk-card"
failed=()
bash "$root/agents/install.sh" || failed+=(agents)
apps=(hub devterm fileview publish dev-monitor)
$paseo && apps+=(paseo)
for app in "${apps[@]}"; do
    directory="$root/apps/$app"
    [[ "$app" == hub ]] && directory="$root/hub"
    [[ -f "$directory/app.toml" ]] || continue
    "$root/bin/stationdesk" add "$app" || failed+=("$app")
done
"$root/bin/stationdesk" project
if ((${#failed[@]})); then
    printf 'Installation failed for: %s\n' "${failed[*]}" >&2
    exit 1
fi
printf 'StationDesk: https://%s/\n' "${identity:-localhost}"
printf 'CLI: %s/bin/stationdesk (also linked in ~/.local/bin)\n' "$root"
