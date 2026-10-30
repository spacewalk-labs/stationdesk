#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build/install the agy-acp fork (StationDesk's patched fork of
# sibbl/google-antigravity-acp, MIT — see README.md) and register it with
# Paseo as the `agy` provider. Called by apps/paseo/install.sh, config-gated
# (agy = true under [apps.paseo]) and warn-only — same discipline as its
# neighbor browse-host/install.sh: a failure here must never break the paseo
# daemon or the rest of the StationDesk install.
#
# Does NOT touch Paseo's own dist tree or apps/paseo/patches/ — registration
# is a config.json entry (agents.providers.agy, "extends": "acp"), which
# Paseo's existing GenericACPAgentClient already understands. Does NOT invoke
# `npm install -g` — the fork is staged and built under $HOME/.local/share,
# then referenced by absolute path from config.json, same pattern as
# ../browse-host/install.sh.
set -euo pipefail

# A dry run never writes (LIVE_BOX_ISOLATION; C2.5 F2, 2026-09-15).
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  echo "[dry] agy-acp: skipped (no build, no config.json/skills.json write)"
  exit 0
fi

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATIONDESK_PASEO_AGY="${STATIONDESK_PASEO_AGY:-false}"

log() { echo "[agy-acp] $*"; }
fatal() { echo "[FATAL] agy-acp: $*" >&2; exit 1; }

if [ "$STATIONDESK_PASEO_AGY" != true ]; then
  log "disabled (set agy = true under [apps.paseo] in the StationDesk app configuration to enable)"
  exit 0
fi

command -v node >/dev/null 2>&1 || fatal "node not found (>= 20 required)"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
[ "${NODE_MAJOR:-0}" -ge 20 ] || fatal "node >= 20 required (found $(node -v 2>/dev/null))"
NODE_BIN="$(readlink -f "$(command -v node)")"
NPM_BIN="$(command -v npm)" || fatal "npm not found"

# --- stage source into a stable install dir (survives repo/redeploy churn) ---
# LICENSE ships alongside the code it covers — an installed fork with no
# license text next to it is a distribution gap, not just an inconvenience.
INSTALL_DIR="${STATIONDESK_ROOT:?required}/runtime/agy-acp"
mkdir -p "$INSTALL_DIR"
source_changed=false
for file in package.json package-lock.json tsconfig.json LICENSE README.md; do
  if ! cmp -s "$SELF_DIR/$file" "$INSTALL_DIR/$file"; then
    source_changed=true
  fi
done
if ! diff -qr "$SELF_DIR/src" "$INSTALL_DIR/src" >/dev/null 2>&1; then
  source_changed=true
fi

if $source_changed || [[ ! -f "$INSTALL_DIR/dist/cli.js" || ! -d "$INSTALL_DIR/node_modules" ]]; then
  work=$(mktemp -d "$STATIONDESK_ROOT/runtime/.agy-acp.XXXXXX")
  trap 'rm -rf "$work"' EXIT
  mkdir "$work/new"
  cp -R "$SELF_DIR/"{package.json,package-lock.json,tsconfig.json,LICENSE,README.md,src} "$work/new/"
  log "npm install + build -> $INSTALL_DIR"
  ( cd "$work/new" && "$NPM_BIN" ci --no-audit --no-fund --loglevel=error && "$NPM_BIN" run build ) \
    || fatal "npm install/build failed"
  [ -f "$work/new/dist/cli.js" ] || fatal "build did not produce dist/cli.js"
  mv "$INSTALL_DIR" "$work/old"
  if ! mv "$work/new" "$INSTALL_DIR"; then
    mv "$work/old" "$INSTALL_DIR"
    fatal "could not install completed build"
  fi
  rm -rf "$work"
  trap - EXIT
fi
[ -f "$INSTALL_DIR/dist/cli.js" ] || fatal "build did not produce dist/cli.js"
log "built OK"

# --- register the `agy` Paseo provider + ~/.gemini/config/skills.json -------
# agy resolution (real binary path, download-if-missing) happens inside the
# fork's own cli.js (src/binary.ts resolveAgy()) — this command intentionally
# carries no `-b`/`-m` flags and no static `models` list; see
# configure-agy-acp.py's module docstring for why. Reached via SELF_DIR, not
# a platform-internal install/ path: the D5 app ABI only contracts
# install/lib.sh and gate/*, so this package-owned script lives here.
PASEO_HOME_DIR="${PASEO_HOME:-$HOME/.paseo}"
configure_out="$(python3 "$SELF_DIR/configure-agy-acp.py" \
  --home "$HOME" \
  --paseo-home "$PASEO_HOME_DIR" \
  --node-bin "$NODE_BIN" \
  --cli-js "$INSTALL_DIR/dist/cli.js")" \
  || fatal "provider/skills.json configuration failed"
printf '%s\n' "$configure_out" | sed 's/^/[agy-acp] /'

# Exit 2 (distinct from 0/1) tells the caller a config file actually changed,
# so it can fold this into its own restart decision instead of restarting
# unconditionally (which would drop the owner's live agent sessions on every
# idempotent re-run) or not at all (which would leave a newly written
# provider unread until some unrelated later restart).
if grep -q ': written' <<<"$configure_out"; then
  log "agy provider registered — restart needed to pick it up"
  exit 2
fi
log "agy provider already registered and up to date — no restart needed"
