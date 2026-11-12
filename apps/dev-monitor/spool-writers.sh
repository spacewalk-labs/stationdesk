#!/usr/bin/env bash
# Let other accounts on this box publish inbox cards (e.g. a service account that runs checks).
# usage: sudo spool-writers.sh <account>...      (STATIONDESK_ROOT, STATIONDESK_CARD_WRITERS optional)
# Writers join one group. The root, state/ and cards-spool/ are 0710 (traversal only), tmp/ and new/ are
# setgid+sticky 3770 (writers add files, cannot touch each other's), bad/ stays box-user only.
# Producers must make files 0644 (stationdesk-card does); the 0710/3770 directories keep others out.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'run as root: sudo spool-writers.sh <account>...' >&2; exit 1; }
(($#)) || { echo 'usage: spool-writers.sh <account>...' >&2; exit 1; }
root=${STATIONDESK_ROOT:-/opt/stationdesk}
group=${STATIONDESK_CARD_WRITERS:-stationdesk-card-writers}
state=$root/state
spool=$state/cards-spool
operator=$(stat -c %U "$state")
for path in "$root" "$state" "$spool" "$spool/tmp" "$spool/new" "$spool/bad"; do
    [ ! -L "$path" ] || { echo "refusing symlink: $path" >&2; exit 1; }
done
getent group "$group" >/dev/null || groupadd --system "$group"
for account in "$@"; do
    [ "$account" != "$operator" ] || { echo "$account is the box user; it writes without the group" >&2; exit 1; }
    id -u "$account" >/dev/null
    usermod -aG "$group" "$account"
done
# The root itself must be traversable (710): writers reach bin/stationdesk-card and the spool, nothing is listable.
install -d -m 710 -o "$operator" -g "$group" "$root" "$state" "$spool"
install -d -m 3770 -o "$operator" -g "$group" "$spool/tmp" "$spool/new"
install -d -m 700 -o "$operator" -g "$(id -gn "$operator")" "$spool/bad"
echo "card writers: $group <- $*  (a running session needs a new login to see the group)"
