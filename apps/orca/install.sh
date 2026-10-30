#!/usr/bin/env bash
# Orca ADE (https://github.com/stablyai/orca) behind the StationDesk owner gate.
#
#   browser --https--> tailscale serve :19940 --> nginx owner gate :19943
#           --> orca serve 0.0.0.0:19942 (loopback-only via stationdesk-orca-guard)
#
# This installer provides the files: Electron runtime libraries, the pinned
# AppImage (extracted, because the FUSE mount fails under systemd --user), the
# fixed pairing code and the zero-paste include. Units, nginx and the loopback
# guard live in this folder.
set -euo pipefail

version=1.4.139
case "$(uname -m)" in
    x86_64)
        asset=orca-linux.AppImage
        sha=35ab8dc3b1427544ea1fc67f8c54a337f0b9b4d315abee4eedd9306006b43fb2
        ;;
    aarch64|arm64)
        asset=orca-linux-arm64.AppImage
        sha=d3e694482900fe44e00bddb229aa3233ae1f02a3e93286c4430cc7c11fcb9d89
        ;;
    *) echo "Orca: unsupported architecture $(uname -m); use x86_64 or ARM64" >&2; exit 1 ;;
esac

# A partial copy of the patched client serves 200s and renders a blank page.
bash "$STATIONDESK_APP_DIR/bin/verify-web-bundle.sh" --quiet

# The ARM64 AppImage extractor needs the unversioned libz.so (zlib1g-dev).
# Electron needs an X display and GTK even for headless `serve`. noble renamed
# several libraries to the t64 ABI, so fall back to the old name per package.
# Capture ldconfig output first: `ldconfig -p | grep -q` takes SIGPIPE under
# pipefail and reports a present library as missing.
libraries=$(ldconfig -p 2>/dev/null || true)
if ! command -v Xvfb >/dev/null || [[ "$libraries" != *libgtk-3.so.0* || "$libraries" != *"libz.so "* ]] || ! command -v nft >/dev/null; then
    sudo apt-get update -qq || echo 'apt-get update failed; continuing with cached lists' >&2
    missing=()
    for package in zlib1g-dev xvfb libgtk-3-0t64 libgbm1 libnss3 libasound2t64 libnotify4 libxtst6 libxss1 libatspi2.0-0t64 libsecret-1-0 nftables; do
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$package" \
            || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${package%t64}" \
            || missing+=("$package")
    done
    libraries=$(ldconfig -p 2>/dev/null || true)
    if ! command -v Xvfb >/dev/null || [[ "$libraries" != *libgtk-3.so.0* ]] || ! command -v nft >/dev/null; then
        echo "Orca runtime libraries are missing (apt could not install: ${missing[*]:-none reported})" >&2
        exit 1
    fi
fi

url="https://github.com/stablyai/orca/releases/download/v$version/$asset"
runtime="$STATIONDESK_ROOT/runtime/orca"
appimage="$runtime/orca-$version.AppImage"
mkdir -p "$runtime"
if ! [[ -x "$runtime/squashfs-root/AppRun" ]] \
        || ! printf '%s  %s\n' "$sha" "$appimage" | sha256sum --status -c - 2>/dev/null; then
    work=$(mktemp -d "$runtime/download.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    if ! printf '%s  %s\n' "$sha" "$appimage" | sha256sum --status -c - 2>/dev/null; then
        curl -fLsS --retry 2 --max-time 300 -o "$work/orca.AppImage" "$url"
        printf '%s  %s\n' "$sha" "$work/orca.AppImage" | sha256sum -c -
        chmod +x "$work/orca.AppImage"
        find "$runtime" -maxdepth 1 -name 'orca-*.AppImage' -delete
        mv "$work/orca.AppImage" "$appimage"
    fi
    (cd "$work" && "$appimage" --appimage-extract >/dev/null)
    [[ -x "$work/squashfs-root/AppRun" ]] || { echo "Orca AppRun missing after extract" >&2; exit 1; }
    rm -rf "$runtime/squashfs-root"
    mv "$work/squashfs-root" "$runtime/squashfs-root"
fi

# A fixed pairing code keeps the web client URL stable across restarts. It only
# points at the runtime, which sits behind the owner gate and the loopback guard.
code="$HOME/.config/orca/stationdesk-pairing-code"
if [[ ! -s "$code" ]]; then
    mkdir -p "$(dirname "$code")"
    (umask 077; python3 -c 'import secrets; print(secrets.token_hex(16), end="")' > "$code")
fi

# nginx includes this before the runtime first prints its pairing; ready.sh fills it.
mkdir -p "$STATIONDESK_STATE/orca"
if [[ ! -e "$STATIONDESK_STATE/orca/pairing.conf" ]]; then
    # shellcheck disable=SC2016  # an nginx variable, written literally
    (umask 077; printf 'set $orca_pairing "";\n' > "$STATIONDESK_STATE/orca/pairing.conf")
fi
