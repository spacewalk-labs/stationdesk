#!/usr/bin/env bash
# The two root-owned pieces Orca needs, held for as long as
# stationdesk-orca-guard.service is active. Stopping the unit (app removal too)
# takes both away again, so nothing stays behind in /etc.
#
# - nft: `orca serve` has no --host flag and binds 0.0.0.0. Without this table the
#   backend is reachable over the tailnet, bypassing the owner gate.
# - AppArmor: Ubuntu 24.04 sets kernel.apparmor_restrict_unprivileged_userns=1,
#   which denies the user namespace Chromium's sandbox needs. The setuid fallback
#   cannot help under NoNewPrivileges=yes, so Electron SIGTRAPs on every start.
#   The profile grants userns to this AppImage's two binaries only. `network` is
#   needed as well: with the label attached, socket() answered EACCES for every
#   family until it was allowed (measured in Airlock, 2026-09-03..05).
set -euo pipefail

squashfs="$STATIONDESK_ROOT/runtime/orca/squashfs-root"
profile() {
    cat <<PROFILE
abi <abi/4.0>,
include <tunables/global>

profile stationdesk-orca $squashfs/{AppRun,orca-ide} flags=(unconfined) {
  userns,
  network,
  include if exists <local/stationdesk-orca>
}
PROFILE
}
apparmor() { [[ -d /sys/kernel/security/apparmor ]] && command -v apparmor_parser >/dev/null; }

case "${1:-}" in
up)
    sudo -n true || { echo 'Orca needs passwordless sudo for its loopback firewall and AppArmor profile' >&2; exit 1; }
    sudo -n nft delete table inet stationdesk_orca 2>/dev/null || true
    sudo -n nft -f - <<NFT
table inet stationdesk_orca {
    chain input {
        type filter hook input priority -10; policy accept;
        iif != "lo" tcp dport $STATIONDESK_PORT drop
    }
}
NFT
    apparmor || exit 0
    if ! profile | sudo -n apparmor_parser -r; then
        # Required only when the kernel denies userns; otherwise Orca runs without it.
        [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" != 1 ]] || exit 1
        echo 'AppArmor rejected the Orca profile; this kernel allows userns, continuing' >&2
    fi
    ;;
down)
    sudo -n nft delete table inet stationdesk_orca 2>/dev/null || true
    if apparmor; then profile | sudo -n apparmor_parser -R 2>/dev/null || true; fi
    ;;
*)
    echo 'Usage: guard.sh up|down' >&2
    exit 2
    ;;
esac
