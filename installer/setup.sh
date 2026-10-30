#!/usr/bin/env bash
# GUI adapter: use the same installer and engine as the terminal entry point.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
action=${1:-check}
shift || true
case "$action" in check|prepare|login|install) ;; *) echo "Unknown action: $action" >&2; exit 2 ;; esac
for argument in "$@"; do
    [[ "$argument" == --no-paseo ]] || { echo "Unknown option: $argument" >&2; exit 2; }
done
status() { printf 'SD_STATUS\t%s\t%s\n' "$1" "$2"; }

if [[ $(uname -s) == Darwin ]]; then
    orb_bin=''
    for candidate in "$HOME/.orbstack/bin/orb" /usr/local/bin/orb /opt/homebrew/bin/orb; do
        if [[ -x "$candidate" ]]; then orb_bin=$candidate; break; fi
    done
    if [[ -z "$orb_bin" ]]; then
        status runtime_missing OrbStack
        exit 0
    fi
    if ! "$orb_bin" info stationdesk >/dev/null 2>&1; then
        if [[ "$action" != prepare ]]; then status needs_prepare true; exit 0; fi
        echo 'Creating the StationDesk Ubuntu machine…'
        "$orb_bin" create ubuntu:24.04 stationdesk
    fi
    exec "$orb_bin" -m stationdesk bash "/mnt/mac$here/setup.sh" "$action" "$@"
fi

# On a desktop, pkexec uses the OS authentication dialog. On OrbStack and an
# explicitly configured test owner, ordinary passwordless sudo already works.
if ! sudo -n true 2>/dev/null && [[ -x "$here/sudo" ]]; then
    export PATH="$here:$PATH"
fi
check_identity() {
    if ! command -v tailscale >/dev/null || ! command -v python3 >/dev/null; then
        status needs_prepare true
        return
    fi
    local identity
    identity=$(tailscale status --json 2>/dev/null | python3 -c '
import json, sys
try:
    status = json.load(sys.stdin)
    me = status.get("Self") or {}
    owner = (status.get("User", {}).get(str(me.get("UserID")), {}) or {}).get("LoginName", "") or me.get("LoginName", "")
    if status.get("BackendState") == "Running" and owner and me.get("DNSName"):
        print(owner)
except (ValueError, TypeError):
    pass
' || true)
    if [[ -n "$identity" ]]; then status signed_in "$identity"; else status needs_login true; fi
}
case "$action" in
    check) check_identity ;;
    prepare)
        # These are host facts, not guessed from which GUI package was opened.
        # shellcheck disable=SC1091
        source /etc/os-release
        [[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || {
            echo 'StationDesk requires Ubuntu 24.04.' >&2; exit 1;
        }
        systemctl --user show-environment >/dev/null || {
            echo 'Open the installer in your Ubuntu desktop user session.' >&2; exit 1;
        }
        echo 'Preparing Git, curl, sudo, and Tailscale…'
        sudo bash -s -- "$(id -un)" <<'ROOT'
set -euo pipefail
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates sudo
if ! command -v tailscale >/dev/null; then
    script=$(mktemp)
    trap 'rm -f "$script"' EXIT
    curl -fsSL https://tailscale.com/install.sh -o "$script"
    sh "$script"
fi
systemctl enable --now tailscaled
tailscale set --operator="$1"
ROOT
        status prepared true
        check_identity
        ;;
    login)
        echo 'Opening Tailscale sign-in…'
        # tailscale up emits the actual login URL; expiry is a pending sign-in,
        # never a successful installation. The UI offers Check sign-in afterward.
        login_output=$(mktemp)
        trap 'rm -f "$login_output"' EXIT
        login_result=0
        tailscale up --timeout=30s >"$login_output" 2>&1 || login_result=$?
        cat "$login_output"
        login_url=$(grep -Eo 'https://login\.tailscale\.com/[^[:space:]]+' "$login_output" | head -1 || true)
        if [[ -n "$login_url" ]]; then status login_url "$login_url"; fi
        check_identity
        if [[ "$login_result" -ne 0 && -z "$login_url" ]]; then exit "$login_result"; fi
        ;;
    install)
        bundle="$here/source.bundle"
        [[ -f "$bundle" ]] || { echo 'The installer source bundle is missing. Download the installer again.' >&2; exit 1; }
        # The bundle supplies the entry point; the installed checkout follows
        # the public repository, so ordinary fast-forward updates keep working.
        source_dir=$(mktemp -d)
        trap 'rm -rf "$source_dir"' EXIT
        git clone --quiet -- "$bundle" "$source_dir"
        echo 'Installing StationDesk…'
        # Vendor installers may traverse their working directory; a desktop launcher
        # or remote test can start in a directory the installing owner cannot read.
        cd "$source_dir"
        bash "$source_dir/install.sh" "$@"
        echo 'Checking StationDesk…'
        /opt/stationdesk/bin/stationdesk doctor
        fqdn=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
        [[ -n "$fqdn" ]] || { echo 'Tailscale did not report a hostname.' >&2; exit 1; }
        curl --fail --silent --show-error --output /dev/null "https://$fqdn/"
        status ready_url "https://$fqdn/"
        ;;
esac
