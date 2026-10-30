#!/usr/bin/env bash
set -euo pipefail
runtime="$STATIONDESK_ROOT/runtime/fileview"
database="$STATIONDESK_STATE/fileview/fb.db"
version=2.63.18
mkdir -p "$runtime" "$(dirname "$database")"
if ! [ -x "$runtime/filebrowser" ] || ! "$runtime/filebrowser" version | grep -q "$version"; then
    case "$(uname -m)" in
        x86_64) asset=linux-amd64-filebrowser.tar.gz; sha=cd599c34afad0e8e61c577d1061c820bccb7feaa3c5a4477a12db586a1cd93ff ;;
        aarch64) asset=linux-arm64-filebrowser.tar.gz; sha=29b3935c222d91522874e98dfa33195ee7d2acdac5dfbf37c1361a73704a28de ;;
        *) echo "Unsupported filebrowser architecture: $(uname -m)" >&2; exit 1 ;;
    esac
    download=$(mktemp -d "$runtime/download.XXXXXX")
    trap 'rm -rf "$download"' EXIT
    curl -fsSL --max-time 90 -o "$download/filebrowser.tar.gz" "https://github.com/filebrowser/filebrowser/releases/download/v$version/$asset"
    printf '%s  %s\n' "$sha" "$download/filebrowser.tar.gz" | sha256sum --check
    tar -xzf "$download/filebrowser.tar.gz" -C "$download" filebrowser
    install -m755 "$download/filebrowser" "$runtime/filebrowser"
    rm -rf "$download"
    trap - EXIT
fi
# Configure only a new database; re-add leaves a running service and its DB alone.
if ! [ -f "$database" ]; then
    "$runtime/filebrowser" --database "$database" --root "$HOME" --address 127.0.0.1 --port "$STATIONDESK_PORT" --noauth --followExternalSymlinks=false --disableTypeDetectionByHeader >/dev/null 2>&1 &
    setup_pid=$!
    trap 'kill "$setup_pid" 2>/dev/null || true; wait "$setup_pid" 2>/dev/null || true' EXIT
    sleep 2
    kill "$setup_pid" 2>/dev/null || true
    wait "$setup_pid" 2>/dev/null || true
    trap - EXIT
    "$runtime/filebrowser" config set --database "$database" --baseURL /fileview --root "$HOME" --hideDotfiles=false --perm.share=false --disableTypeDetectionByHeader=true >/dev/null
fi
mkdir -p "$STATIONDESK_ROOT/web/fileview"
if ! diff -qr "$STATIONDESK_APP_DIR/static" "$STATIONDESK_ROOT/web/fileview/static" >/dev/null 2>&1; then
    cp -R "$STATIONDESK_APP_DIR/static" "$STATIONDESK_ROOT/web/fileview/"
fi
python3 - <<'PY'
import html, os
from pathlib import Path
page = Path(os.environ['STATIONDESK_ROOT']) / 'web/fileview/viewer.html'
source = Path(os.environ['STATIONDESK_APP_DIR']) / 'static/viewer.html'
content = source.read_text().replace('<meta name="fileview-home" content="">',
    '<meta name="fileview-home" content="' + html.escape(os.environ['HOME'], quote=True) + '">')
if not page.exists() or page.read_text() != content:
    page.write_text(content)
PY
