#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Stage the original browser host. The app installer owns config, units and ports.
set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${STATIONDESK_ROOT:?required}/runtime/paseo-browse-host"
log() { echo "[browse-host] $*"; }
mkdir -p "$INSTALL_DIR/test"
dependencies_changed=false
if ! cmp -s "$SELF_DIR/package.json" "$INSTALL_DIR/package.json"; then
  dependencies_changed=true
fi
for directory in src bin web; do
  if ! diff -qr "$SELF_DIR/$directory" "$INSTALL_DIR/$directory" >/dev/null 2>&1; then
    rm -rf "${INSTALL_DIR:?}/$directory"
    cp -R "$SELF_DIR/$directory" "$INSTALL_DIR/"
  fi
done
if ! cmp -s "$SELF_DIR/test/smoke.cjs" "$INSTALL_DIR/test/smoke.cjs"; then
  cp -f "$SELF_DIR/test/smoke.cjs" "$INSTALL_DIR/test/smoke.cjs"
fi
if $dependencies_changed || [[ ! -d "$INSTALL_DIR/node_modules/playwright" || ! -d "$INSTALL_DIR/node_modules/ws" ]]; then
  work=$(mktemp -d "$STATIONDESK_ROOT/runtime/.browse-deps.XXXXXX")
  trap 'rm -rf "$work"' EXIT
  cp "$SELF_DIR/package.json" "$work/package.json"
  (cd "$work" && npm install --omit=dev --no-audit --no-fund --loglevel=error)
  rm -rf "${INSTALL_DIR:?}/node_modules"
  mv "$work/node_modules" "$INSTALL_DIR/node_modules"
  cp "$work/package.json" "$INSTALL_DIR/package.json"
  if [[ -f "$work/package-lock.json" ]]; then
    cp "$work/package-lock.json" "$INSTALL_DIR/package-lock.json"
  fi
  rm -rf "$work"
  trap - EXIT
fi
# A successful download does not prove Chromium has its OS libraries. Probe the
# same executable the sidecar will use, and install missing libraries on LXD.
chromium_launch() {
  (cd "$INSTALL_DIR" && node -e '''
    const opts = {headless:true,args:["--no-sandbox"]};
    if (process.env.PASEO_BROWSE_CHROMIUM_PATH) opts.executablePath=process.env.PASEO_BROWSE_CHROMIUM_PATH;
    require("playwright").chromium.launch(opts).then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)});
  ''')
}
if ! chromium_launch; then
  (cd "$INSTALL_DIR" && node node_modules/playwright/cli.js install chromium)
fi
if ! chromium_launch; then
  (cd "$INSTALL_DIR" && sudo -n "$(command -v node)" node_modules/playwright/cli.js install-deps chromium)
  chromium_launch
fi
node "$SELF_DIR/bin/patch-web-ui.js" --browse "${PASEO_WEBUI_DIR:?required}" "$SELF_DIR/web/browse-view-client.js"
log "staged browser host and live browser panel"
