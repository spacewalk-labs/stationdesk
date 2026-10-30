#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
product=$(cd "$here/../.." && pwd)
out=${1:?Usage: build-deb.sh /absolute/output.deb [version]}
version=${2:-0.1.3}
[[ "$out" = /* ]] || { echo 'Output must be an absolute path.' >&2; exit 2; }
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
resources="$stage/usr/share/stationdesk-installer"
mkdir -p "$resources/icons" "$stage/usr/bin" "$stage/usr/share/applications" "$stage/usr/share/pixmaps" "$stage/DEBIAN"
install -m644 "$here/installer.py" "$resources/installer.py"
install -m644 "$product/installer/assets/stationdesk.png" "$resources/stationdesk.png"
for icon in devterm fileview publish dev-monitor paseo; do
    install -m644 "$product/hub/web/assets/app-icons/$icon.png" "$resources/icons/$icon.png"
done
install -m644 "$product/installer/assets/stationdesk.png" "$stage/usr/share/pixmaps/stationdesk-installer.png"
install -m755 "$here/../setup.sh" "$resources/setup.sh"
install -m755 "$here/../sudo" "$resources/sudo"
bash "$here/../build-seed.sh" "$product" "$resources/source.bundle"
cat > "$stage/usr/bin/stationdesk-installer" <<'LAUNCH'
#!/bin/sh
exec /usr/bin/python3 /usr/share/stationdesk-installer/installer.py "$@"
LAUNCH
chmod 755 "$stage/usr/bin/stationdesk-installer"
cat > "$stage/usr/share/applications/stationdesk-installer.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=StationDesk Setup
Name[ko]=StationDesk 설치
Comment=Install your StationDesk workspace
Exec=stationdesk-installer
Icon=stationdesk-installer
Terminal=false
Categories=System;
StartupNotify=true
DESKTOP
cat > "$stage/DEBIAN/control" <<CONTROL
Package: stationdesk-installer
Version: $version
Section: admin
Priority: optional
Architecture: all
Depends: python3, python3-tk, pkexec, sudo, bash, git, curl
Maintainer: StationDesk <support@stationdesk.app>
Description: Graphical StationDesk workspace installer
 Native Ubuntu setup interface with bundled StationDesk source.
CONTROL
mkdir -p "$(dirname "$out")"
dpkg-deb --root-owner-group --build "$stage" "$out"
