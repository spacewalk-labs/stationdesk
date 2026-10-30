#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
product=$(cd -- "$here/../.." && pwd)
out=${1:-"$PWD/dist"}
version=${2:-0.1.3}
mkdir -p "$out"
out=$(cd "$out" && pwd)
app="$out/StationDesk Setup.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
xcrun swiftc -O -parse-as-library -target "$(uname -m)-apple-macosx13.0" "$here/StationDeskSetup.swift" -o "$app/Contents/MacOS/StationDeskSetup"
cp "$product/installer/assets/stationdesk.png" "$app/Contents/Resources/stationdesk.png"
for icon in devterm fileview publish dev-monitor paseo; do
    cp "$product/hub/web/assets/app-icons/$icon.png" "$app/Contents/Resources/$icon.png"
done
iconset="$out/StationDesk.iconset"
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$product/installer/assets/stationdesk.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    retina=$((size * 2))
    sips -z "$retina" "$retina" "$product/installer/assets/stationdesk.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/StationDesk.icns"
cp "$product/installer/setup.sh" "$app/Contents/Resources/setup.sh"
bash "$product/installer/build-seed.sh" "$product" "$app/Contents/Resources/source.bundle"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>StationDesk Setup</string>
<key>CFBundleIdentifier</key><string>tech.spacewalk.stationdesk.setup</string>
<key>CFBundleExecutable</key><string>StationDeskSetup</string>
<key>CFBundleIconFile</key><string>StationDesk</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$version</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
# This build is ad hoc signed and is not notarized; disclose it in release notes.
codesign --force --sign - "$app"
ditto -c -k --sequesterRsrc --keepParent "$app" "$out/StationDesk-Setup-mac.zip"
printf '%s\n' "$out/StationDesk-Setup-mac.zip"
