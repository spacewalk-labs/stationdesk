#!/usr/bin/env bash
# Install the exact guarded Airlock 0.8.0 build in the StationDesk user runtime.
# The engine owns all unit starts, nginx projection, and artifact removal.
# Existing patch validation deliberately uses A && B || die.
# shellcheck disable=SC2015
set -euo pipefail
# shellcheck disable=SC1091
source "$STATIONDESK_APP_DIR/VERSION"
node="$STATIONDESK_ROOT/runtime/node"
prefix="$STATIONDESK_ROOT/runtime/paseo"
case "$(uname -m)" in
    x86_64) arch=x64; sha=$NODE_X64_SHA256 ;;
    aarch64) arch=arm64; sha=$NODE_ARM64_SHA256 ;;
    *) echo "Unsupported Node architecture: $(uname -m)" >&2; exit 1 ;;
esac
mkdir -p "$node" "$prefix"
if ! [ -x "$node/bin/node" ] || [[ $("$node/bin/node" --version) != "v$NODE_VERSION" ]]; then
    download=$(mktemp -d "$STATIONDESK_ROOT/runtime/node-download.XXXXXX")
    trap 'rm -rf "$download"' EXIT
    asset="node-v$NODE_VERSION-linux-$arch.tar.xz"
    curl -fsSL --max-time 180 -o "$download/node.tar.xz" "https://nodejs.org/dist/v$NODE_VERSION/$asset"
    printf '%s  %s\n' "$sha" "$download/node.tar.xz" | sha256sum --check
    tar -xJf "$download/node.tar.xz" -C "$node" --strip-components=1
    rm -rf "$download"
    trap - EXIT
fi
export PATH="$node/bin:$prefix/bin:$HOME/.local/bin:$HOME/.opencode/bin:/usr/local/bin:/usr/bin:/bin"

HERE="$STATIONDESK_APP_DIR"
NPM_ROOT="$prefix/lib/node_modules"
PASEO_PKG=@getpaseo/cli
PASEO_BUNDLE_DIR="$HERE/vendor/guarded-0.8.0"
PASEO_BUNDLE_SUMS="$PASEO_BUNDLE_DIR/SHA256SUMS"
PASEO_INSTALL_ID_FILE="$NPM_ROOT/@getpaseo/.stationdesk-install-id"
PY="$(command -v python3)"
log() { printf '[paseo] %s\n' "$*"; }
die() { printf '[paseo] %s\n' "$*" >&2; exit 1; }
paseo_server_dir() { printf '%s/@getpaseo/server\n' "$NPM_ROOT"; }
# All seven guarded tarballs are installed together so registry packages cannot
# shadow the patched server. The original manifest and tarball hashes stay intact.
(cd "$PASEO_BUNDLE_DIR" && sha256sum --check SHA256SUMS)
PASEO_INSTALL_ID="bundle:$(sha256sum "$PASEO_BUNDLE_SUMS" | cut -d ' ' -f1)"
if ! [ -f "$PASEO_INSTALL_ID_FILE" ] || [ "$(cat "$PASEO_INSTALL_ID_FILE")" != "$PASEO_INSTALL_ID" ]; then
    mapfile -t paseo_packages < <(awk -v dir="$PASEO_BUNDLE_DIR" '{print dir "/" $2}' "$PASEO_BUNDLE_SUMS")
    npm install -g --prefix "$prefix" "${paseo_packages[@]}" --no-audit --no-fund
    printf '%s\n' "$PASEO_INSTALL_ID" > "$PASEO_INSTALL_ID_FILE"
fi
for package in server client protocol; do
    rm -rf "$NPM_ROOT/@getpaseo/cli/node_modules/@getpaseo/$package"
done
PATCH_SKIP_APPROVED="$HERE/patches/skip-approved.txt"
patch_skipped() {
    local reason
    reason="$(awk -v n="$1" '$1 == n { sub(/^[^ \t]+[ \t]+/, ""); print; exit }' "$PATCH_SKIP_APPROVED")"
    [ -n "$reason" ] || die "$1 does not fit bundled Paseo: ${2:-}"
    log "$1 skipped (approved: $reason)"
}
# --- 2. depth4 search patch (idempotent) ---
# paseo's add-project name search full-scans $HOME; on a large home it times out.
# Cap it to maxDepth 4 (workspace @files search is untouched). This edits paseo's
# own bundle, so it is an AGPL-3.0 derivative — see patches/README.md. The .patch
# in patches/ is the reference/re-derivation copy; we apply it via idempotent sed
# so a paseo version bump that moved the anchor warns loudly instead of silently
# skipping (fail-visible, not fail-silent).
# Resolved once, after step 1 has settled the tree; every patch target below hangs
# off it. (Dry run: whatever is on disk, or the bundle layout if nothing is.)
PASEO_SERVER_DIR="$(paseo_server_dir)"
if [ "${STATIONDESK_DRY_RUN:-0}" != 1 ]; then
  "$PY" "$HERE/normalize-native-links.py" "$PASEO_SERVER_DIR" \
    || die "Paseo native files cannot be materialized for rollback checkpoints"
fi

# Codex model roster: app-server remains the authority for which models this
# ChatGPT account can actually use. The overlay only retires GPT-5.5 from the
# picker and enables Fast for GPT-6 Sol/Luna when model/list begins returning
# them. Apply both files as one verified unit so the catalog never imports a
# helper the feature module does not yet export.
CODEX_ROSTER_PATCHER="$HERE/patches/codex-model-roster.mjs"
CODEX_ROSTER_TEST="$HERE/patches/codex-model-roster.test.mjs"
CODEX_FEATURES="$PASEO_SERVER_DIR/dist/server/server/agent/providers/codex-feature-definitions.js"
CODEX_CATALOG="$PASEO_SERVER_DIR/dist/server/server/agent/providers/codex-app-server-agent.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] align Paseo Codex picker with the StationDesk model roster"
elif [ ! -f "$CODEX_FEATURES" ] || [ ! -f "$CODEX_CATALOG" ]; then
  log "warning: Codex provider files not found under $PASEO_SERVER_DIR (paseo dist layout changed?) — model roster skipped"
elif [ ! -f "$CODEX_ROSTER_PATCHER" ] || [ ! -f "$CODEX_ROSTER_TEST" ]; then
  log "warning: Codex model-roster patcher or behaviour test missing under $HERE — skipped"
else
  feature_rc=0 catalog_rc=0
  feature_out="$(node "$CODEX_ROSTER_PATCHER" feature "$CODEX_FEATURES" 2>&1)" || feature_rc=$?
  catalog_out="$(node "$CODEX_ROSTER_PATCHER" catalog "$CODEX_CATALOG" 2>&1)" || catalog_rc=$?
  if { [ "$feature_rc" -eq 0 ] || [ "$feature_rc" -eq 10 ]; } \
    && { [ "$catalog_rc" -eq 0 ] || [ "$catalog_rc" -eq 10 ]; }; then
    feature_check="$CODEX_FEATURES"
    catalog_check="$CODEX_CATALOG"
    [ "$feature_rc" -eq 0 ] && feature_check="$CODEX_FEATURES.paseo-new.mjs"
    [ "$catalog_rc" -eq 0 ] && catalog_check="$CODEX_CATALOG.paseo-new.mjs"
    node --check "$feature_check" || die "Codex model-roster feature candidate is invalid JS"
    node --check "$catalog_check" || die "Codex model-roster catalog candidate is invalid JS"
    node "$CODEX_ROSTER_TEST" "$feature_check" "$catalog_check" \
      || die "Codex model-roster candidate failed its behaviour check"
    if [ "$feature_rc" -eq 0 ]; then mv "$feature_check" "$CODEX_FEATURES"; :; fi
    if [ "$catalog_rc" -eq 0 ]; then mv "$catalog_check" "$CODEX_CATALOG"; :; fi
    log "Codex model roster verified (GPT-6 Sol/Luna ready; GPT-5.5 retired)"
  elif [ "$feature_rc" -eq 20 ] || [ "$catalog_rc" -eq 20 ]; then
    rm -f "$CODEX_FEATURES.paseo-new.mjs" "$CODEX_CATALOG.paseo-new.mjs"
    patch_skipped "codex-model-roster" "feature=$feature_out catalog=$catalog_out"
  else
    rm -f "$CODEX_FEATURES.paseo-new.mjs" "$CODEX_CATALOG.paseo-new.mjs"
    log "warning: Codex model-roster patcher error (feature_rc=$feature_rc catalog_rc=$catalog_rc) — pair skipped: feature=$feature_out catalog=$catalog_out"
  fi
fi

# Keep automatic cleanup reversible. Only the CLI is guarded; browser/direct RPC
# deletion remains upstream behavior. Changing a CLI module needs no daemon restart.
HISTORY_GUARD_CLI="$NPM_ROOT/$PASEO_PKG/dist/commands/agent/delete.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] refuse CLI permanent agent deletion before connecting/cancelling"
else
  rm -f "$HISTORY_GUARD_CLI.paseo-new.mjs"
  history_guard_rc=0
  node "$HERE/patches/agent-history-delete-guard.mjs" cli "$HISTORY_GUARD_CLI" \
    || history_guard_rc=$?
  case "$history_guard_rc" in
    0)
      node --check "$HISTORY_GUARD_CLI.paseo-new.mjs" \
        || die "CLI deletion guard candidate is invalid"
      node "$HERE/patches/agent-history-delete-guard.test.mjs" "$HISTORY_GUARD_CLI.paseo-new.mjs" \
        || die "CLI deletion guard behaviour check failed"
      mv "$HISTORY_GUARD_CLI.paseo-new.mjs" "$HISTORY_GUARD_CLI" \
        || die "CLI deletion guard replacement failed"
      ;;
    10) ;;
    *) die "CLI deletion guard could not be applied (rc=$history_guard_rc)" ;;
  esac
  log "CLI permanent deletion disabled; use archive or manual browser deletion"
fi
# archive/detach/reload resolve their target by scanning the includeArchived list, which
# the server caps at 200 — an old seat sorts past it and the command says "Agent not
# found". CLI modules only, so no daemon restart. See patches/README.md.
RESOLVE_BYID_PATCHER="$HERE/patches/agent-resolve-by-id.mjs"
RESOLVE_BYID_DIR="$NPM_ROOT/$PASEO_PKG/dist/commands/agent"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] resolve archive/detach/reload targets by exact id (bypass 200-cap list)"
elif [ ! -f "$RESOLVE_BYID_PATCHER" ]; then
  log "warning: agent-resolve-by-id patcher missing under $HERE — skipped"
else
  for rb in archive detach reload; do
    rb_target="$RESOLVE_BYID_DIR/$rb.js"
    [ -f "$rb_target" ] || { log "warning: agent-resolve-by-id target not found ($rb_target) — skipped"; continue; }
    rm -f "$rb_target.paseo-new.mjs"
    rb_rc=0; rb_out="$(node "$RESOLVE_BYID_PATCHER" "$rb_target")" || rb_rc=$?
    case "$rb_rc" in
      0)
        if node --check "$rb_target.paseo-new.mjs"; then
          mv "$rb_target.paseo-new.mjs" "$rb_target" || die "agent-resolve-by-id $rb mv failed"
          log "agent $rb resolves exact ids directly (no 200-cap list)"
        else
          rm -f "$rb_target.paseo-new.mjs"
          log "warning: agent-resolve-by-id $rb candidate invalid — skipped"
        fi ;;
      10) ;;
      20) patch_skipped "agent-resolve-by-id" "$rb: $rb_out" ;;
      *)  log "warning: agent-resolve-by-id $rb patcher error (rc=$rb_rc): $rb_out — skipped" ;;
    esac
  done
fi
SESSION_JS="$PASEO_SERVER_DIR/dist/server/server/session.js"
PATCH_LINE='                maxDepth: searchesWorkspace ? undefined : 4,'
PATCH_ANCHOR='confidentResultScanThreshold: searchesWorkspace ? undefined : 5000,'
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply depth4 search patch to $SESSION_JS (idempotent sed after anchor)"
elif [ ! -f "$SESSION_JS" ]; then
  log "warning: session.js not found ($SESSION_JS) — depth4 patch skipped"
elif grep -qF 'maxDepth: searchesWorkspace ? undefined : 4' "$SESSION_JS"; then
  log "depth4 search patch already applied"
elif grep -qF "$PATCH_ANCHOR" "$SESSION_JS"; then
  # Insert the maxDepth line right after the anchor (indentation preserved).
  sed -i "/$(printf '%s' "$PATCH_ANCHOR" | sed 's/[.[\*^$]/\\&/g')/a\\${PATCH_LINE}" "$SESSION_JS" \
    || die "depth4 patch sed failed"
  grep -qF 'maxDepth: searchesWorkspace ? undefined : 4' "$SESSION_JS" \
    || die "depth4 patch verify failed (not inserted after anchor)"
  node --check "$SESSION_JS" || die "depth4 patch produced invalid JS"
  :   # bundle changed -> restart so the daemon loads the patched code
  log "depth4 search patch applied (add-project name search maxDepth 4)"
else
  log "warning: depth4 anchor not found (paseo version drift?) — search may be slow; see patches/depth4-search.patch"
fi

# --- 2a. archive/workspace consistency at the server read + update boundaries ---
# A workspace archive can leave an otherwise-live agent record behind. Keep the
# stored records untouched, but hide agents whose workspace registry record is
# archived from the default list and remove them from default live subscriptions.
# Agents with no/unknown workspace stay visible in the snapshot; includeArchived=true
# remains the upstream diagnostic/recovery view. The two server files are one patch:
# a read-only snapshot fix without the update-path fix would make the next upsert revive it.
ARCHIVE_CONSISTENCY_PATCHER="$HERE/patches/archive-consistency.mjs"
ARCHIVE_CONSISTENCY_TEST="$HERE/patches/archive-consistency.test.mjs"
ARCHIVE_CONSISTENCY_UPDATES="$PASEO_SERVER_DIR/dist/server/server/session/agent-updates/agent-updates-service.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply archive/workspace consistency patch to $SESSION_JS and $ARCHIVE_CONSISTENCY_UPDATES"
elif [ ! -f "$SESSION_JS" ] || [ ! -f "$ARCHIVE_CONSISTENCY_UPDATES" ]; then
  log "warning: archive-consistency server target missing ($SESSION_JS or $ARCHIVE_CONSISTENCY_UPDATES) — skipped"
elif [ ! -f "$ARCHIVE_CONSISTENCY_PATCHER" ] || [ ! -f "$ARCHIVE_CONSISTENCY_TEST" ]; then
  log "warning: archive/workspace consistency patcher or behaviour test missing under $HERE — skipped"
else
  ac_session_tmp="${SESSION_JS}.paseo-new.mjs"
  ac_updates_tmp="${ARCHIVE_CONSISTENCY_UPDATES}.paseo-new.mjs"
  rm -f "$ac_session_tmp" "$ac_updates_tmp"
  ac_rc=0
  ac_out="$(node "$ARCHIVE_CONSISTENCY_PATCHER" "$SESSION_JS" "$ARCHIVE_CONSISTENCY_UPDATES")" || ac_rc=$?
  case "$ac_rc" in
    10) log "archive/workspace consistency already applied" ;;
    20) patch_skipped "archive-consistency" "$ac_out" ;;
    0)
      if [ ! -f "$ac_session_tmp" ] || [ ! -f "$ac_updates_tmp" ]; then
        rm -f "$ac_session_tmp" "$ac_updates_tmp"
        log "warning: archive/workspace consistency did not produce both candidates — skipped"
      elif ! node --check "$ac_session_tmp" || ! node --check "$ac_updates_tmp"; then
        rm -f "$ac_session_tmp" "$ac_updates_tmp"
        log "warning: archive/workspace consistency candidate is invalid JS — not applied"
      elif ! node "$ARCHIVE_CONSISTENCY_TEST" "$ac_session_tmp" "$ac_updates_tmp" >/dev/null 2>&1; then
        rm -f "$ac_session_tmp" "$ac_updates_tmp"
        log "warning: archive/workspace consistency candidates failed their behaviour check — not applied"
      else
        mv "$ac_session_tmp" "$SESSION_JS" || die "archive/workspace consistency session.js mv failed"
        mv "$ac_updates_tmp" "$ARCHIVE_CONSISTENCY_UPDATES" || die "archive/workspace consistency update-service mv failed"
        :
        log "archive/workspace consistency applied"
      fi
      ;;
    *) log "warning: archive/workspace consistency patcher error (rc=$ac_rc): $ac_out — skipped" ;;
  esac
  # The sentinel is only a comment; verify the actual installed bytes before
  # allowing the daemon restart transaction to proceed.
  if grep -qF 'paseo-archive-consistency' "$SESSION_JS" \
    || grep -qF 'paseo-archive-consistency' "$ARCHIVE_CONSISTENCY_UPDATES"; then
    grep -qF 'paseo-archive-consistency' "$SESSION_JS" \
      && grep -qF 'paseo-archive-consistency' "$ARCHIVE_CONSISTENCY_UPDATES" \
      || die "installed archive/workspace consistency is partially applied"
    node "$ARCHIVE_CONSISTENCY_TEST" "$SESSION_JS" "$ARCHIVE_CONSISTENCY_UPDATES" >/dev/null 2>&1 \
      || die "installed archive/workspace consistency behavior check failed"
    log "archive/workspace consistency behaviour check passed"
  fi
fi

# --- 2b. send_agent_message may keep the receiving seat's pending permissions ---
# Upstream pins clearPendingPermissions=true, so an automatic steer message (session-delivery)
# denies the permission request the seat was waiting on. Optional boolean on the request; absent
# = true, so human/UI sends are unchanged. Schema (protocol), handler (server) and sender (client)
# are one patch: schema-only strips nothing but is inert, server-only never sees the field, and a
# client sending the field to an unpatched schema has it stripped. The server file changes -> restart.
SENDKEEP_PATCHER="$HERE/patches/send-keep-pending-permissions.mjs"
SENDKEEP_TEST="$HERE/patches/send-keep-pending-permissions.test.mjs"
SENDKEEP_MESSAGES="$NPM_ROOT/@getpaseo/protocol/dist/messages.js"
SENDKEEP_CLIENT="$NPM_ROOT/@getpaseo/client/dist/daemon-client.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply send-keep-pending-permissions patch to $SENDKEEP_MESSAGES, $SESSION_JS and $SENDKEEP_CLIENT"
elif [ ! -f "$SENDKEEP_MESSAGES" ] || [ ! -f "$SESSION_JS" ] || [ ! -f "$SENDKEEP_CLIENT" ]; then
  log "warning: send-keep-pending-permissions target missing — skipped"
elif [ ! -f "$SENDKEEP_PATCHER" ] || [ ! -f "$SENDKEEP_TEST" ]; then
  log "warning: send-keep-pending-permissions patcher or behaviour test missing under $HERE — skipped"
else
  sk_files=("$SENDKEEP_MESSAGES" "$SESSION_JS" "$SENDKEEP_CLIENT")
  sk_clean() { rm -f "${sk_files[0]}.paseo-new.mjs" "${sk_files[1]}.paseo-new.mjs" "${sk_files[2]}.paseo-new.mjs"; }
  sk_clean
  sk_rc=0
  sk_out="$(node "$SENDKEEP_PATCHER" "${sk_files[@]}" 2>&1)" || sk_rc=$?
  case "$sk_rc" in
    10) log "send-keep-pending-permissions already applied" ;;
    20) patch_skipped "send-keep-pending-permissions" "$sk_out" ;;
    0)
      if [ ! -f "${sk_files[0]}.paseo-new.mjs" ] || [ ! -f "${sk_files[1]}.paseo-new.mjs" ] || [ ! -f "${sk_files[2]}.paseo-new.mjs" ]; then
        sk_clean
        log "warning: send-keep-pending-permissions did not produce all three candidates — skipped"
      elif ! node --check "${sk_files[0]}.paseo-new.mjs" || ! node --check "${sk_files[1]}.paseo-new.mjs" \
        || ! node --check "${sk_files[2]}.paseo-new.mjs"; then
        sk_clean
        log "warning: send-keep-pending-permissions candidate is invalid JS — not applied"
      elif ! node "$SENDKEEP_TEST" "${sk_files[0]}.paseo-new.mjs" "${sk_files[1]}.paseo-new.mjs" "${sk_files[2]}.paseo-new.mjs" >/dev/null 2>&1; then
        sk_clean
        log "warning: send-keep-pending-permissions candidates failed their behaviour check — not applied"
      else
        mv "${sk_files[0]}.paseo-new.mjs" "${sk_files[0]}" || die "send-keep-pending-permissions protocol mv failed"
        mv "${sk_files[1]}.paseo-new.mjs" "${sk_files[1]}" || die "send-keep-pending-permissions session.js mv failed"
        mv "${sk_files[2]}.paseo-new.mjs" "${sk_files[2]}" || die "send-keep-pending-permissions client mv failed"
        :
        log "send-keep-pending-permissions applied"
      fi
      ;;
    *) log "warning: send-keep-pending-permissions patcher error (rc=$sk_rc): $sk_out — skipped" ;;
  esac
fi

# --- 2c. idle seats leave daemon memory ---
# Upstream keeps every loaded seat and every timeline it ever loaded in memory; the V8 heap hit its
# ceiling and the daemon died three times in four days on a busy box (2026-10-16..20). A seat idle
# for PASEO_IDLE_UNLOAD_MINUTES (default 30, 0 = off; render.sh sets 360) is unloaded without touching its stored
# record, and a closed seat drops its timeline; both resume from provider history on next touch.
IDLE_UNLOAD_PATCHER="$HERE/patches/idle-agent-unload.mjs"
IDLE_UNLOAD_TEST="$HERE/patches/idle-agent-unload.test.mjs"
AGENT_MANAGER_JS="$PASEO_SERVER_DIR/dist/server/server/agent/agent-manager.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply idle-agent-unload patch to $AGENT_MANAGER_JS"
elif [ ! -f "$AGENT_MANAGER_JS" ]; then
  log "warning: idle-agent-unload target missing ($AGENT_MANAGER_JS) — skipped"
elif [ ! -f "$IDLE_UNLOAD_PATCHER" ] || [ ! -f "$IDLE_UNLOAD_TEST" ]; then
  log "warning: idle-agent-unload patcher or behaviour test missing under $HERE — skipped"
else
  iu_tmp="${AGENT_MANAGER_JS}.paseo-new.mjs"
  rm -f "$iu_tmp"
  iu_rc=0
  iu_out="$(node "$IDLE_UNLOAD_PATCHER" "$AGENT_MANAGER_JS" 2>&1)" || iu_rc=$?
  case "$iu_rc" in
    10) log "idle-agent-unload already applied" ;;
    20) patch_skipped "idle-agent-unload" "$iu_out" ;;
    0)
      if [ ! -f "$iu_tmp" ]; then
        log "warning: idle-agent-unload did not produce a candidate — skipped"
      elif ! node --check "$iu_tmp"; then
        rm -f "$iu_tmp"
        log "warning: idle-agent-unload candidate is invalid JS — not applied"
      elif ! node "$IDLE_UNLOAD_TEST" "$iu_tmp" >/dev/null 2>&1; then
        rm -f "$iu_tmp"
        log "warning: idle-agent-unload candidate failed its behaviour check — not applied"
      else
        mv "$iu_tmp" "$AGENT_MANAGER_JS" || die "idle-agent-unload agent-manager.js mv failed"
        :
        log "idle-agent-unload applied"
      fi
      ;;
    *) log "warning: idle-agent-unload patcher error (rc=$iu_rc): $iu_out — skipped" ;;
  esac
fi

# --- 2d. a reloaded seat tells open chats to refetch ---
# A seat loaded again rebuilds its timeline under a fresh epoch. Upstream only reloads at daemon
# start, when clients refetch anyway; with 2c clients stay connected, keep the old epoch and drop
# every new row, so an old seat answered but the open chat showed nothing (2026-10-21). The first
# hydration after a load now dispatches timeline_replacement, the event rewind already uses.
RELOAD_REPLACE_PATCHER="$HERE/patches/reload-timeline-replacement.mjs"
RELOAD_REPLACE_TEST="$HERE/patches/reload-timeline-replacement.test.mjs"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply reload-timeline-replacement patch to $AGENT_MANAGER_JS"
elif [ ! -f "$AGENT_MANAGER_JS" ]; then
  log "warning: reload-timeline-replacement target missing ($AGENT_MANAGER_JS) — skipped"
elif [ ! -f "$RELOAD_REPLACE_PATCHER" ] || [ ! -f "$RELOAD_REPLACE_TEST" ]; then
  log "warning: reload-timeline-replacement patcher or behaviour test missing under $HERE — skipped"
else
  rr_tmp="${AGENT_MANAGER_JS}.paseo-new.mjs"
  rm -f "$rr_tmp"
  rr_rc=0
  rr_out="$(node "$RELOAD_REPLACE_PATCHER" "$AGENT_MANAGER_JS" 2>&1)" || rr_rc=$?
  case "$rr_rc" in
    10) log "reload-timeline-replacement already applied" ;;
    20) patch_skipped "reload-timeline-replacement" "$rr_out" ;;
    0)
      if [ ! -f "$rr_tmp" ]; then
        log "warning: reload-timeline-replacement did not produce a candidate — skipped"
      elif ! node --check "$rr_tmp"; then
        rm -f "$rr_tmp"
        log "warning: reload-timeline-replacement candidate is invalid JS — not applied"
      elif ! node "$RELOAD_REPLACE_TEST" "$rr_tmp" >/dev/null 2>&1; then
        rm -f "$rr_tmp"
        log "warning: reload-timeline-replacement candidate failed its behaviour check — not applied"
      else
        mv "$rr_tmp" "$AGENT_MANAGER_JS" || die "reload-timeline-replacement agent-manager.js mv failed"
        :
        log "reload-timeline-replacement applied"
      fi
      ;;
    *) log "warning: reload-timeline-replacement patcher error (rc=$rr_rc): $rr_out — skipped" ;;
  esac
fi

# Removal of a cached row must survive an empty changes-only subscription bootstrap.
WORKSPACE_REMOVE_PATCHER="$HERE/patches/workspace-remove-delivery.mjs"
WORKSPACE_REMOVE_TEST="$HERE/patches/workspace-remove-delivery.test.mjs"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply cached workspace removal delivery patch to $SESSION_JS"
else
  wr_rc=0
  node "$WORKSPACE_REMOVE_PATCHER" "$SESSION_JS" || wr_rc=$?
  case "$wr_rc" in
    0)
      node --check "$SESSION_JS.paseo-new.mjs" \
        && node "$WORKSPACE_REMOVE_TEST" "$SESSION_JS.paseo-new.mjs" "$PASEO_SERVER_DIR/dist/server/server/directory-sync/index.js" \
        || die "workspace removal candidate failed verification"
      mv "$SESSION_JS.paseo-new.mjs" "$SESSION_JS" || die "workspace removal patch install failed"
      :
      ;;
    10) node "$WORKSPACE_REMOVE_TEST" "$SESSION_JS" "$PASEO_SERVER_DIR/dist/server/server/directory-sync/index.js" || die "workspace removal installed behavior failed" ;;
    20) patch_skipped "workspace-remove-delivery" ;;
    *) die "workspace removal patcher failed (rc=$wr_rc)" ;;
  esac
fi

# A device whose cursor has passed a removal keeps the archived row until the daemon
# restarts; workspace and agent directory reads answer with the full list instead.
DIRECTORY_SYNC_JS="$PASEO_SERVER_DIR/dist/server/server/directory-sync/index.js"
DIRECTORY_SYNC_PATCHER="$HERE/patches/directory-sync-snapshot.mjs"
DIRECTORY_SYNC_TEST="$HERE/patches/directory-sync-snapshot.test.mjs"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply directory sync snapshot patch to $DIRECTORY_SYNC_JS"
else
  ds_rc=0
  node "$DIRECTORY_SYNC_PATCHER" "$DIRECTORY_SYNC_JS" || ds_rc=$?
  case "$ds_rc" in
    0)
      node --check "$DIRECTORY_SYNC_JS.paseo-new.mjs" \
        && node "$DIRECTORY_SYNC_TEST" "$DIRECTORY_SYNC_JS.paseo-new.mjs" \
        || die "directory sync snapshot candidate failed verification"
      mv "$DIRECTORY_SYNC_JS.paseo-new.mjs" "$DIRECTORY_SYNC_JS" || die "directory sync snapshot patch install failed"
      :
      ;;
    10) node "$DIRECTORY_SYNC_TEST" "$DIRECTORY_SYNC_JS" || die "directory sync snapshot installed behavior failed" ;;
    20) patch_skipped "directory-sync-snapshot" ;;
    *) die "directory sync snapshot patcher failed (rc=$ds_rc)" ;;
  esac
fi

# Reconciliation archives a workspace whose directory is gone; its agents must be
# archived with it, or they stay listed as live under an archived workspace.
BOOTSTRAP_JS="$PASEO_SERVER_DIR/dist/server/server/bootstrap.js"
RECONCILE_AGENTS_PATCHER="$HERE/patches/reconcile-archives-agents.mjs"
RECONCILE_AGENTS_TEST="$HERE/patches/reconcile-archives-agents.test.mjs"
RECONCILE_AGENTS_SERVICE="$PASEO_SERVER_DIR/dist/server/server/workspace-archive-service.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply reconcile-archives-agents patch to $BOOTSTRAP_JS"
else
  ra_rc=0
  node "$RECONCILE_AGENTS_PATCHER" "$BOOTSTRAP_JS" || ra_rc=$?
  case "$ra_rc" in
    0)
      node --check "$BOOTSTRAP_JS.paseo-new.mjs" \
        && node "$RECONCILE_AGENTS_TEST" "$BOOTSTRAP_JS.paseo-new.mjs" "$RECONCILE_AGENTS_SERVICE" \
        || die "reconcile-archives-agents candidate failed verification"
      mv "$BOOTSTRAP_JS.paseo-new.mjs" "$BOOTSTRAP_JS" || die "reconcile-archives-agents patch install failed"
      :
      ;;
    10) node "$RECONCILE_AGENTS_TEST" "$BOOTSTRAP_JS" "$RECONCILE_AGENTS_SERVICE" || die "reconcile-archives-agents installed behavior failed" ;;
    20) patch_skipped "reconcile-archives-agents" ;;
    *) die "reconcile-archives-agents patcher failed (rc=$ra_rc)" ;;
  esac
fi

# --- 2b. provider-subagent selective delivery (server + always-on web UI) ---
# Upstream sends every provider-owned subagent update to every browser socket that
# advertises the feature, even when that socket is viewing another agent. The normal
# agent stream is already source/subscription filtered. This patch gives provider
# subagents the same parent-agent filter. The web half is correctness-critical: when a
# provider-subagent tab is the only visible tab, it must subscribe its parentAgentId or
# a server-only filter would cut off that tab's live updates.
#
# Keep the server edit as a checked candidate until the UI edit succeeds. Serving the
# new UI against the old broadcast server is harmless; loading the new server against
# the old UI is not. The daemon restart below therefore cannot expose a half-patched
# pair.
#
# What is fatal here, and what is not: the guarded bundle already carries the server
# half (the patcher answers ALREADY), and only the web half is applied at install time.
# A target that is not where this script expects it, an anchor that moved, or a patcher
# that cannot run all leave the checksum-verified bundle serving exactly what upstream
# shipped — a feature missing, not an install broken — so they warn and skip. Only a
# served tree that is actually wrong is fatal: invalid JS in a file the daemon serves,
# a rename that did not happen, or installed bytes that carry a patch's sentinel and
# fail that patch's behaviour check (the sentinel is a comment; the check is the proof).
# The 2026-09-12 install was rolled back, platform-wide, by the `die` that used to
# stand where the first warning below stands now.
SUBAGENT_FILTER_PATCHER="$HERE/patches/provider-subagent-stream-filter.mjs"
SUBAGENT_FILTER_TEST="$HERE/patches/provider-subagent-stream-filter.test.mjs"
WEBUI_PATCHER="$HERE/browse-host/bin/patch-web-ui.js"
WEBUI_DIR="$PASEO_SERVER_DIR/dist/server/web-ui"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply provider-subagent source filter to $SESSION_JS"
  log "[dry] apply always-on provider-subagent parent subscription to $WEBUI_DIR"
elif [ ! -f "$SESSION_JS" ] || [ ! -d "$WEBUI_DIR" ]; then
  log "warning: session.js or web-ui not found under $PASEO_SERVER_DIR (paseo dist layout changed?) — provider-subagent selective delivery skipped"
elif [ ! -f "$SUBAGENT_FILTER_PATCHER" ] || [ ! -f "$SUBAGENT_FILTER_TEST" ] || [ ! -f "$WEBUI_PATCHER" ]; then
  log "warning: provider-subagent patcher, its behaviour test or the web-ui patcher is missing under $HERE — skipped"
else
  sf_tmp="${SESSION_JS}.paseo-new.mjs"
  rm -f "$sf_tmp"
  sf_rc=0
  sf_out="$(node "$SUBAGENT_FILTER_PATCHER" "$SESSION_JS")" || sf_rc=$?
  sf_candidate=0
  case "$sf_rc" in
    10) log "provider-subagent server filter already applied" ;;
    20) patch_skipped "provider-subagent-stream-filter" "$sf_out" ;;
    0)
      if [ ! -f "$sf_tmp" ]; then
        log "warning: provider-subagent server filter produced no candidate — server half skipped"
      elif ! node --check "$sf_tmp"; then
        rm -f "$sf_tmp"
        log "warning: provider-subagent server filter candidate is invalid JS — not applied"
      elif ! node "$SUBAGENT_FILTER_TEST" "$sf_tmp" >/dev/null 2>&1; then
        rm -f "$sf_tmp"
        log "warning: provider-subagent server filter candidate failed its behaviour check — not applied"
      else
        sf_candidate=1
      fi
      ;;
    *) log "warning: provider-subagent server filter patcher error (rc=$sf_rc): $sf_out — server half skipped" ;;
  esac

  # This mode changes only the general provider-subagent subscription anchor. It
  # shares cache-busting/index.html repointing with the optional --browse group.
  # The patcher writes the new bundle before it repoints index.html, so a failure
  # leaves the old UI served intact (apps/paseo/browse-host/bin/patch-web-ui.js).
  if node "$WEBUI_PATCHER" --subagent-stream "$WEBUI_DIR"; then
    webui_bundle="$(find "$WEBUI_DIR/_expo/static/js/web" -maxdepth 1 -type f -name 'index-*.js' -print -quit)"
    { [ -n "$webui_bundle" ] && [ -f "$webui_bundle" ]; } \
      || die "served paseo web-ui bundle not found after provider-subagent patch"
    node --check "$webui_bundle" || die "provider-subagent web-ui patch produced invalid JS"
    if [ "$sf_candidate" = 1 ]; then
      mv "$sf_tmp" "$SESSION_JS" || die "provider-subagent server filter mv failed"
      :
      log "provider-subagent server filter applied"
    fi
  else
    rm -f "$sf_tmp"
    log "warning: provider-subagent web-ui subscription patch failed — pair not installed, web UI serves as shipped"
  fi
  # The sentinel the patcher keys on is a comment; this check on the INSTALLED bytes is
  # the only evidence that the code under it does what the sentinel claims. A file
  # that carries the sentinel and fails the check is a wrong tree — fatal, not a warning.
  if grep -qF 'paseo-provider-subagent-stream-filter' "$SESSION_JS"; then
    node "$SUBAGENT_FILTER_TEST" "$SESSION_JS" >/dev/null 2>&1 \
      || die "installed provider-subagent server filter failed its behaviour check — the served session.js claims the filter and does not behave like it"
    log "provider-subagent selective delivery pair verified"
  fi
fi

# The pinned manifest — the file the prune step below edits. Declared here rather
# than beside its only user because it has already outlived one: an Opus 5 backport
# step owned this declaration until the pin reached a version whose manifest ships
# Opus 5, and under `set -u` removing that step would have taken the prune with it.
CLAUDE_MANIFEST_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/claude/model-manifest.js"

# --- 2c. prune superseded models (idempotent) ---
# The pinned manifest still lists Opus 4.7/4.6 and Sonnet 4.6; drop them so the
# picker is the handful people actually choose. Picker-only: the manifest is not
# on the execution path, so sessions pinned to a removed model keep running.
# No CLI version gate here — removing an entry cannot break a spawn.
PRUNE_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/claude-model-prune.mjs"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] prune superseded models from $CLAUDE_MANIFEST_JS"
elif [ ! -f "$CLAUDE_MANIFEST_JS" ]; then
  # This used to be a bare `:` because the Opus 5 step above warned for the same
  # condition. That step is gone, and a missing manifest is the signal that upstream
  # moved its dist layout — the last thing to swallow.
  log "warning: model-manifest.js not found ($CLAUDE_MANIFEST_JS) — model prune skipped (paseo dist layout changed?)"
elif [ ! -f "$PRUNE_PATCHER" ]; then
  log "warning: model prune patcher not found ($PRUNE_PATCHER) — skipped"
else
  pr_rc=0
  pr_out="$(node "$PRUNE_PATCHER" "$CLAUDE_MANIFEST_JS")" || pr_rc=$?
  case "$pr_rc" in
    10) log "model prune already applied" ;;
    20) patch_skipped "claude-model-prune" "$pr_out" ;;
    0)
      PR_TMP="${CLAUDE_MANIFEST_JS}.paseo-new.mjs"
      if node --check "$PR_TMP"; then
        mv "$PR_TMP" "$CLAUDE_MANIFEST_JS" || die "model prune mv failed"
        :   # bundle changed -> restart so the daemon serves the new list
        log "model prune applied ($pr_out)"
      else
        rm -f "$PR_TMP"
        log "warning: model prune produced invalid JS — not applied"
      fi
      ;;
     *) log "warning: model prune patcher error (rc=$pr_rc): $pr_out — skipped" ;;
   esac
fi

# --- 2c2. OpenCode picker: grok-4.6 first, grok-build next, thinking default high ---
# OpenCode's catalog order and first-variant default put grok-4.6 last at low.
# Picker/create-form only: an already-running agent's model string is untouched.
OPENCODE_AGENT_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/opencode-agent.js"
OPENCODE_GROK_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/opencode-grok-defaults.mjs"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply OpenCode grok picker defaults to $OPENCODE_AGENT_JS"
elif [ ! -f "$OPENCODE_AGENT_JS" ]; then
  log "warning: opencode-agent.js not found ($OPENCODE_AGENT_JS) — OpenCode grok defaults skipped (paseo dist layout changed?)"
elif [ ! -f "$OPENCODE_GROK_PATCHER" ]; then
  log "warning: OpenCode grok defaults patcher not found ($OPENCODE_GROK_PATCHER) — skipped"
else
  og_rc=0
  og_out="$(node "$OPENCODE_GROK_PATCHER" "$OPENCODE_AGENT_JS")" || og_rc=$?
  case "$og_rc" in
    10) log "OpenCode grok picker defaults already applied" ;;
    20) patch_skipped "opencode-grok-defaults" "$og_out" ;;
    0)
      OG_TMP="${OPENCODE_AGENT_JS}.paseo-new.mjs"
      if node --check "$OG_TMP"; then
        mv "$OG_TMP" "$OPENCODE_AGENT_JS" || die "OpenCode grok defaults mv failed"
        :
        log "OpenCode grok picker defaults applied ($og_out)"
      else
        rm -f "$OG_TMP"
        log "warning: OpenCode grok defaults produced invalid JS — not applied"
      fi
      ;;
    *) log "warning: OpenCode grok defaults patcher error (rc=$og_rc): $og_out — skipped" ;;
  esac
fi

# --- 2d. persist pasted images (idempotent) ---
# An image pasted into the web UI reaches the model only as an inline base64 vision block:
# the model can see it, but there is no file, so the agent's Read tool has no path to open
# and "look at this screenshot, then fix the file" dead-ends. The patch keeps the inline
# block and additionally writes the bytes under the session cwd, naming the path in a
# sibling text block. No CLI version gate: this changes what the provider sends, not which
# model runs.
IMGPERSIST_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/image-attachments-persist.mjs"
CLAUDE_AGENT_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/claude/agent.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply pasted-image persistence to $CLAUDE_AGENT_JS"
elif [ ! -f "$CLAUDE_AGENT_JS" ]; then
  log "warning: claude agent.js not found ($CLAUDE_AGENT_JS) — pasted-image persistence skipped"
elif [ ! -f "$IMGPERSIST_PATCHER" ]; then
  log "warning: pasted-image patcher not found ($IMGPERSIST_PATCHER) — skipped"
else
  ip_rc=0
  ip_out="$(node "$IMGPERSIST_PATCHER" "$CLAUDE_AGENT_JS")" || ip_rc=$?
  case "$ip_rc" in
    10) log "pasted-image persistence already applied" ;;
    20) patch_skipped "image-attachments-persist" ;;
    0)
      IP_TMP="${CLAUDE_AGENT_JS}.paseo-new.mjs"
      if node --check "$IP_TMP"; then
        mv "$IP_TMP" "$CLAUDE_AGENT_JS" || die "pasted-image persistence mv failed"
        :   # bundle changed -> restart so the daemon runs the patched provider
        log "pasted-image persistence applied (saves under <cwd>/.paseo-attachments/)"
      else
        rm -f "$IP_TMP"
        log "warning: pasted-image persistence produced invalid JS — not applied"
      fi
      ;;
    *) log "warning: pasted-image patcher error (rc=$ip_rc): $ip_out — skipped" ;;
  esac
fi

# --- 2e. orphan process guard (idempotent; claude provider only) ---
# paseo leaks the agent processes it spawns. The claude provider tracks exactly one
# live child (`this.childProcess`) and kills it behind an `if (handle)` with no else
# branch, and does not honour the closed flag on its spawn entry point (ensureQuery
# -- only startTurn()/startQueryPump() do, confirmed against 0.8.0 source). So a
# control-plane call landing during or after close — setMode, setModel, listCommands,
# revertFiles, or just the in-flight spawn finishing late — starts a REPLACEMENT
# process on a session nothing will ever close again. It then runs until the box is
# rebooted, and close() reports success because at that instant there genuinely was
# nothing to kill. Measured on Pilot box 2026-08-05: 18 orphans, 2.9G RSS + 1.9G swap.
# The patch makes ownership a Set (so a replaced handle is still terminated), gates
# the spawn entry point on the closed flag, terminates late arrivals on the spot,
# and warns at level 40 — the surrounding session_close lines are logger.trace, which
# the daemon's info-level logger never emits, so this class of leak was unobservable.
# codex is not covered here: re-checked against 0.8.0, upstream independently
# rewrote CodexAppServerSession's connect()/close() lifecycle (closed-flag gate at
# three points, connectionPromise de-duplication, identity-checked dispose in every
# failure branch) — the same fix this patch makes for claude, already shipped
# upstream. See the patcher's own header for the full comparison.
ORPHANGUARD_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/orphan-process-guard.mjs"
ORPHANGUARD_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/orphan-process-guard.test.mjs"
apply_orphan_guard() {  # <mode> <target-js>
  local mode="$1" target="$2" og_rc=0 og_out og_tmp
  if [ ! -f "$target" ]; then
    log "warning: $mode provider not found ($target) — orphan guard skipped"
    return 0
  fi
  og_out="$(node "$ORPHANGUARD_PATCHER" "$mode" "$target")" || og_rc=$?
  case "$og_rc" in
    10) log "orphan guard already applied ($mode)" ;;
    20) patch_skipped "orphan-process-guard" "$mode" ;;
    0)
      og_tmp="${target}.paseo-new.mjs"
      if node --check "$og_tmp"; then
        mv "$og_tmp" "$target" || die "orphan guard mv failed ($mode)"
        :   # bundle changed -> restart so the daemon runs the patched provider
        log "orphan guard applied ($mode)"
      else
        rm -f "$og_tmp"
        log "warning: orphan guard produced invalid JS ($mode) — not applied"
      fi
      ;;
    *) log "warning: orphan guard patcher error ($mode rc=$og_rc): $og_out — skipped" ;;
  esac
}
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply orphan process guard to $CLAUDE_AGENT_JS"
elif [ ! -f "$ORPHANGUARD_PATCHER" ]; then
  log "warning: orphan guard patcher not found ($ORPHANGUARD_PATCHER) — skipped"
else
  apply_orphan_guard claude "$CLAUDE_AGENT_JS"
  # Syntax-valid is not the same as behaving. This check slices the guard methods
  # back out of the installed bundle and drives them against fake children, so a patch
  # that applied but reassembled wrongly fails the install instead of shipping quietly.
  if [ -f "$ORPHANGUARD_TEST" ] && grep -q 'paseo-orphan-guard' "$CLAUDE_AGENT_JS" 2>/dev/null; then
    node "$ORPHANGUARD_TEST" "$CLAUDE_AGENT_JS" >/dev/null 2>&1 \
      || die "orphan guard behaviour check failed — the installed provider carries the guard's sentinel and still leaks children"
    log "orphan guard behaviour check passed"
  fi
fi

# --- 2e-2. schedule busy -> pending delivery (coalesce one tick, do not lose it) ---
# When an agent-target schedule fires while that seat is running a turn, upstream records the
# run as FAILED and advances to the next cadence: the tick is gone, the failed run eats maxRuns,
# and — worst — masters learn "to receive my clock I must be idle at that instant" and end turns
# with work still in hand. Measured on a pilot box 2026-09-13: 5 of 17 agent-target runs were exactly
# `already has an active run`; one master wrote, verbatim, "the clock refuses a wake while a turn
# is running, so I am returning the turn to receive the 08:55 wake".
# The patch coalesces busy into ONE pending bit (not a queue): no failed run, no maxRuns burn,
# and the next tick delivers exactly once as soon as the seat is idle — due or not.
# Two halves, independent on purpose: the protocol schema keeps the bit across restarts (zod
# strips unknown keys), the server implements it. Schema-only = the field is always false;
# server-only = pending works until the daemon restarts. Schema goes first so the behaviour
# check below can prove persistence.
SCHEDPEND_SCHEMA_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-pending-delivery-schema.mjs"
SCHEDPEND_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-busy-pending-delivery.mjs"
SCHEDPEND_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-busy-pending-delivery.test.mjs"
# 🔴 Third half, and it must go LAST: its anchors are the tick the busy-pending patch writes.
# Re-entrant ticks (setInterval fire-and-forget) hand runSchedule a due snapshot that disk has
# already moved past, so maxRuns=1 gets a second run -- which makes session-delivery's receipt
# read "unavailable". Measured on a pilot box, 2026-09-23: 21 of 415 patrol-deliver schedules had
# two succeeded runs. The same patch stops reading the whole schedule directory twice per tick.
SCHEDSTALE_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-stale-due-run.mjs"
SCHEDSTALE_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-stale-due-run.test.mjs"
# 🔴 Fourth, after stale-due: its anchors are the pending loop busy-pending writes and it reuses
# stale-due's revalidating append. session-delivery makes one one-shot schedule per message, so a
# seat that was busy collects N pending schedules; delivering one holds the seat for a whole turn and
# the rest wait a turn each. Measured on a pilot box 2026-10-03: 15 waiting for one seat, hours of lag.
# This delivers a seat's pending schedules in ONE turn (each still gets exactly one run) and puts the
# bit back if a claimed schedule's append is refused, instead of stranding it until the next cron hour.
SCHEDBATCH_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-pending-batch.mjs"
SCHEDBATCH_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/schedule-pending-batch.test.mjs"
SCHEDULE_SERVICE_JS="$PASEO_SERVER_DIR/dist/server/server/schedule/service.js"
SCHEDULE_TYPES_JS="$(cd "$PASEO_SERVER_DIR/../protocol/dist" 2>/dev/null && pwd || true)/schedule/types.js"
apply_schedpend() {  # <label> <patcher> <target-js> [behaviour-test] [phrase]
  # `phrase` names the patch family in the log. test-paseo-bundle-install.sh asserts
  # these exact lines -- every baked patch must report "already applied" against the
  # pinned bundle -- so the wording is a contract, not decoration.
  local label="$1" patcher="$2" target="$3" test_js="${4:-}" phrase="${5:-busy-pending patch}"
  local sp_rc=0 sp_out sp_tmp
  if [ ! -f "$target" ]; then
    log "warning: schedule $label target not found ($target) — $phrase skipped"
    return 0
  fi
  sp_out="$(node "$patcher" "$target")" || sp_rc=$?
  case "$sp_rc" in
    10) log "schedule $label $phrase already applied" ;;
    20) patch_skipped "$(basename "$patcher" .mjs)" "$sp_out" ;;
    0)
      sp_tmp="${target}.paseo-new.mjs"
      if ! node --check "$sp_tmp"; then
        rm -f "$sp_tmp"
        log "warning: schedule $label patch produced invalid JS — not applied"
      elif [ -n "$test_js" ] && ! node "$test_js" "$sp_tmp" >/dev/null 2>&1; then
        # Syntax-valid is not behaving. The check drives the candidate with stub seats:
        # busy -> pending bit and no failed run, then exactly one delivery once idle.
        rm -f "$sp_tmp"
        log "warning: schedule $label $phrase failed its behaviour check — not applied"
      else
        mv "$sp_tmp" "$target" || die "schedule $label patch mv failed"
        :
        log "schedule $label $phrase applied"
      fi
      ;;
    *) log "warning: schedule $label patcher error (rc=$sp_rc): $sp_out — skipped" ;;
  esac
}
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply schedule busy-pending delivery to $SCHEDULE_TYPES_JS and $SCHEDULE_SERVICE_JS"
elif [ ! -f "$SCHEDPEND_PATCHER" ] || [ ! -f "$SCHEDPEND_SCHEMA_PATCHER" ] || [ ! -f "$SCHEDPEND_TEST" ]; then
  log "warning: schedule busy-pending patchers or behaviour check missing under $HERE — skipped"
else
  apply_schedpend schema "$SCHEDPEND_SCHEMA_PATCHER" "$SCHEDULE_TYPES_JS"
  apply_schedpend service "$SCHEDPEND_PATCHER" "$SCHEDULE_SERVICE_JS" "$SCHEDPEND_TEST"
  # Order is load-bearing: stale-due anchors on text the service patch introduces, so if that
  # one was skipped this exits 20 and skips too -- never half a fix. Its own behaviour check
  # also re-runs the busy-pending scenarios, so a regression there blocks this patch as well.
  if [ ! -f "$SCHEDSTALE_PATCHER" ] || [ ! -f "$SCHEDSTALE_TEST" ]; then
    log "warning: schedule stale-due patcher or behaviour check missing under $HERE — skipped"
  else
    apply_schedpend stale-due "$SCHEDSTALE_PATCHER" "$SCHEDULE_SERVICE_JS" "$SCHEDSTALE_TEST" \
      "patch"
  fi
  if [ ! -f "$SCHEDBATCH_PATCHER" ] || [ ! -f "$SCHEDBATCH_TEST" ]; then
    log "warning: schedule pending-batch patcher or behaviour check missing under $HERE — skipped"
  else
    apply_schedpend pending-batch "$SCHEDBATCH_PATCHER" "$SCHEDULE_SERVICE_JS" "$SCHEDBATCH_TEST" \
      "patch"
  fi
fi

# --- 2e-w. working-tree watch recovery (upstream #3056 backport; idempotent) ---
# A watcher that misses its 10s subscribe deadline drops to a 5s poll (4 git commands a tick),
# and 0.8.0 stops trying to recover after 3 attempts -- so one slow moment (a daemon boot with
# many workspaces) turns into polling that lasts until the next restart. Measured on a pilot
# box 2026-09-25: the fallback poll was the top git refresh source (610 per 30s window) and the
# git queue peaked at 552 waiting. Upstream keeps retrying (capped at 300s) and slows a quiet
# poll (doubling, capped at 60s); this carries exactly that, working-tree path only.
WATCHREC_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/workspace-git-watch-recovery.mjs"
WATCHREC_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/workspace-git-watch-recovery.test.mjs"
WORKSPACE_GIT_SERVICE_JS="$PASEO_SERVER_DIR/dist/server/server/workspace-git-service.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply working-tree watch recovery to $WORKSPACE_GIT_SERVICE_JS"
elif [ ! -f "$WATCHREC_PATCHER" ] || [ ! -f "$WATCHREC_TEST" ]; then
  log "warning: watch recovery patcher or behaviour check missing under $HERE — skipped"
elif [ ! -f "$WORKSPACE_GIT_SERVICE_JS" ]; then
  log "warning: watch recovery target not found ($WORKSPACE_GIT_SERVICE_JS) — skipped"
else
  wr_rc=0; wr_out="$(node "$WATCHREC_PATCHER" "$WORKSPACE_GIT_SERVICE_JS")" || wr_rc=$?
  case "$wr_rc" in
    10) log "watch recovery patch already applied" ;;
    20) patch_skipped "workspace-git-watch-recovery" "$wr_out" ;;
    0)
      wr_tmp="${WORKSPACE_GIT_SERVICE_JS}.paseo-new.mjs"
      if ! node --check "$wr_tmp"; then
        rm -f "$wr_tmp"; log "warning: watch recovery patch produced invalid JS — not applied"
      elif ! node "$WATCHREC_TEST" "$wr_tmp" >/dev/null 2>&1; then
        rm -f "$wr_tmp"; log "warning: watch recovery patch failed its behaviour check — not applied"
      else
        mv "$wr_tmp" "$WORKSPACE_GIT_SERVICE_JS" || die "watch recovery patch mv failed"
        :
        log "watch recovery patch applied"
      fi
      ;;
    *) log "warning: watch recovery patcher error (rc=$wr_rc): $wr_out — skipped" ;;
  esac
fi

# --- 2e-x. fresh identity-only checkout reads for reconciliation (0.8.0 backport) ---
IDENTITY_PATCHER="$HERE/patches/workspace-git-identity.mjs"
IDENTITY_TEST="$HERE/patches/workspace-git-identity.test.mjs"
CHECKOUT_GIT_JS="$PASEO_SERVER_DIR/dist/server/utils/checkout-git.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply checkout identity to $WORKSPACE_GIT_SERVICE_JS and $CHECKOUT_GIT_JS"
elif [ ! -f "$IDENTITY_PATCHER" ] || [ ! -f "$IDENTITY_TEST" ] || [ ! -f "$WORKSPACE_GIT_SERVICE_JS" ] || [ ! -f "$CHECKOUT_GIT_JS" ]; then
  log "warning: checkout identity patcher, behaviour check or target missing — skipped"
else
  identity_rc=0
  identity_out="$(node "$IDENTITY_PATCHER" "$WORKSPACE_GIT_SERVICE_JS" "$CHECKOUT_GIT_JS")" || identity_rc=$?
  case "$identity_rc" in
    10) log "checkout identity patch already applied" ;;
    20) patch_skipped "workspace-git-identity" "$identity_out" ;;
    0)
      identity_service="$WORKSPACE_GIT_SERVICE_JS.paseo-new.mjs"
      identity_checkout="$CHECKOUT_GIT_JS.paseo-new.mjs"
      if node --check "$identity_service" && node --check "$identity_checkout" && \
          node "$IDENTITY_TEST" "$identity_service" "$identity_checkout"; then
        mv "$identity_checkout" "$CHECKOUT_GIT_JS" || die "checkout identity utility mv failed"
        mv "$identity_service" "$WORKSPACE_GIT_SERVICE_JS" || die "checkout identity service mv failed"
        :
        log "checkout identity patch applied"
      else
        rm -f "$identity_service" "$identity_checkout"
        log "warning: checkout identity candidate failed verification — skipped"
      fi ;;
    *) log "warning: checkout identity patcher error (rc=$identity_rc): $identity_out — skipped" ;;
  esac
fi

# --- 2e-y. lower automatic Git/forge frequency; explicit requests stay immediate ---
FREQUENCY_PATCHER="$HERE/patches/background-git-frequency.mjs"
FREQUENCY_TEST="$HERE/patches/background-git-frequency.test.mjs"
RECONCILIATION_JS="$PASEO_SERVER_DIR/dist/server/server/workspace-reconciliation-service.js"
GITHUB_SERVICE_JS="$PASEO_SERVER_DIR/dist/server/services/github-service.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] lower automatic Git, reconciliation and forge refresh frequency"
elif [ ! -f "$FREQUENCY_PATCHER" ] || [ ! -f "$FREQUENCY_TEST" ] || [ ! -f "$WORKSPACE_GIT_SERVICE_JS" ] || [ ! -f "$RECONCILIATION_JS" ] || [ ! -f "$GITHUB_SERVICE_JS" ]; then
  log "warning: automatic Git frequency patcher, behaviour check or target missing — skipped"
else
  frequency_rc=0
  frequency_out="$(node "$FREQUENCY_PATCHER" "$WORKSPACE_GIT_SERVICE_JS" "$RECONCILIATION_JS" "$GITHUB_SERVICE_JS")" || frequency_rc=$?
  case "$frequency_rc" in
    10) log "automatic Git frequency patch already applied" ;;
    20) patch_skipped "background-git-frequency" "$frequency_out" ;;
    0)
      frequency_git="$WORKSPACE_GIT_SERVICE_JS.paseo-new.mjs"
      frequency_reconciliation="$RECONCILIATION_JS.paseo-new.mjs"
      frequency_github="$GITHUB_SERVICE_JS.paseo-new.mjs"
      if node --check "$frequency_git" && node --check "$frequency_reconciliation" && node --check "$frequency_github" && \
          node "$FREQUENCY_TEST" "$frequency_git" "$frequency_reconciliation" "$frequency_github"; then
        mv "$frequency_git" "$WORKSPACE_GIT_SERVICE_JS" || die "automatic Git frequency service mv failed"
        mv "$frequency_reconciliation" "$RECONCILIATION_JS" || die "automatic Git frequency reconciliation mv failed"
        mv "$frequency_github" "$GITHUB_SERVICE_JS" || die "automatic Git frequency forge mv failed"
        log "automatic Git frequency patch applied"
      else
        rm -f "$frequency_git" "$frequency_reconciliation" "$frequency_github"
        log "warning: automatic Git frequency candidate failed verification — skipped"
      fi ;;
    *) log "warning: automatic Git frequency patcher error (rc=$frequency_rc): $frequency_out — skipped" ;;
  esac
fi

# --- 2e-z. avoid enumerating all AgentStorage records on every warm load ---
STORAGE_LOAD_PATCHER="$HERE/patches/agent-storage-load-once.mjs"
STORAGE_LOAD_TEST="$HERE/patches/agent-storage-load-once.test.mjs"
AGENT_STORAGE_JS="$PASEO_SERVER_DIR/dist/server/server/agent/agent-storage.js"
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply AgentStorage load-once to $AGENT_STORAGE_JS"
elif [ ! -f "$STORAGE_LOAD_PATCHER" ] || [ ! -f "$STORAGE_LOAD_TEST" ] || [ ! -f "$AGENT_STORAGE_JS" ]; then
  log "warning: AgentStorage load-once patcher, behaviour check or target missing — skipped"
else
  storage_load_rc=0
  storage_load_out="$(node "$STORAGE_LOAD_PATCHER" "$AGENT_STORAGE_JS")" || storage_load_rc=$?
  case "$storage_load_rc" in
    10) log "AgentStorage load-once patch already applied" ;;
    20) patch_skipped "agent-storage-load-once" "$storage_load_out" ;;
    0)
      storage_load_candidate="$AGENT_STORAGE_JS.paseo-new.mjs"
      if node --check "$storage_load_candidate" && node "$STORAGE_LOAD_TEST" "$storage_load_candidate"; then
        mv "$storage_load_candidate" "$AGENT_STORAGE_JS" || die "AgentStorage load-once mv failed"
        log "AgentStorage load-once patch applied"
      else
        rm -f "$storage_load_candidate"
        log "warning: AgentStorage load-once candidate failed verification — skipped"
      fi ;;
    *) log "warning: AgentStorage load-once patcher error (rc=$storage_load_rc): $storage_load_out — skipped" ;;
  esac
fi

# --- 2f. process-group sweep (idempotent; layers on 2e — order matters) ---
# The one leak 2e deliberately left open: when the agent LEADER exits before we
# terminate it, terminateWithTreeKill returns "already-exited" and stops — and by then
# the leader's MCP children have been reparented, so a ppid-walking tree-kill can no
# longer find them. They survive as orphans.
# A process group outlives its leader, so killing the GROUP reaches them. Controlled
# experiment (pilot box, 2026-08-06): detached=false -> grandchild orphaned and
# kill(-pid) returns ESRCH (harmless); detached=true -> kill(-pgid) kills it. In both
# cases the child stays in stationdesk-paseo.service's cgroup, so KillMode=control-group still
# sweeps everything on restart. codex already spawns its app-server detached upstream
# and merely never killed the group; claude needed both halves.
# 🔴 This must run AFTER 2e: its claude-agent anchors are text 2e introduces. If 2e was
# skipped, this exits 20 and skips too — never half a fix.
PGROUP_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/orphan-process-group.mjs"
PGROUP_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/orphan-process-group.test.mjs"
CLAUDE_QUERY_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/claude/query.js"
CODEX_TRANSPORT_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/codex/app-server-transport.js"
apply_pgroup() {  # <mode> <target-js>
  local mode="$1" target="$2" pg_rc=0 pg_out pg_tmp
  if [ ! -f "$target" ]; then
    log "warning: $mode target not found ($target) — process-group sweep skipped"
    return 0
  fi
  pg_out="$(node "$PGROUP_PATCHER" "$mode" "$target")" || pg_rc=$?
  case "$pg_rc" in
    10) log "process-group sweep already applied ($mode)" ;;
    20) patch_skipped "orphan-process-group" "$mode" ;;
    0)
      pg_tmp="${target}.paseo-new.mjs"
      if node --check "$pg_tmp"; then
        mv "$pg_tmp" "$target" || die "process-group sweep mv failed ($mode)"
        :
        log "process-group sweep applied ($mode)"
      else
        rm -f "$pg_tmp"
        log "warning: process-group sweep produced invalid JS ($mode) — not applied"
      fi
      ;;
    *) log "warning: process-group patcher error ($mode rc=$pg_rc): $pg_out — skipped" ;;
  esac
}
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply process-group sweep to claude agent/query and codex transport"
elif [ ! -f "$PGROUP_PATCHER" ]; then
  log "warning: process-group patcher not found ($PGROUP_PATCHER) — skipped"
else
  apply_pgroup claude-agent    "$CLAUDE_AGENT_JS"
  apply_pgroup claude-query    "$CLAUDE_QUERY_JS"
  apply_pgroup codex-transport "$CODEX_TRANSPORT_JS"
  # Drives the shipped sweep against REAL detached processes: spawns a leader with a
  # child, kills only the leader, and asserts the sweep reaps the survivor. A syntax
  # check cannot tell us that, and this is the half of the fix that signals other
  # processes — it should never ship unverified.
  if [ -f "$PGROUP_TEST" ] && grep -q 'paseo-process-group' "$CLAUDE_AGENT_JS" 2>/dev/null; then
    node "$PGROUP_TEST" "$CLAUDE_AGENT_JS" >/dev/null 2>&1 \
      || die "process-group behaviour check failed — the installed provider carries the sweep's sentinel and does not reap descendants"
    log "process-group behaviour check passed"
  fi
fi

# --- 2g. agy (generic ACP) gaps: context gauge + cross-provider mode default ---
# agy runs over Paseo's generic ACP provider. Against 0.8.0, usage_update is parsed
# and dropped (no context gauge for any ACP provider), a config-only update (model
# or thinking only) blanks a mode list that came from session/new rather than a
# config option, an ACP agent's own advertised unattended mode never reaches
# anything downstream, and — once a generic ACP agent advertises modes — an
# ATTENDED agent of a different provider running `paseo run --provider agy` with
# no --mode is refused ("cannot inherit mode"). And a new workspace re-probes agy's
# model catalogue (~10s: agy models + a real agy start) because only claude/codex
# share one host-wide catalogue; the registry patch gives agy the same key.
ACPGAUGE_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-context-gauge.mjs"
ACPGAUGE_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-context-gauge.test.mjs"
ACP_AGENT_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/acp-agent.js"
ACPMODE_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-cross-provider-mode-default.mjs"
ACPMODE_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-cross-provider-mode-default.test.mjs"
GENERIC_ACP_AGENT_JS="$PASEO_SERVER_DIR/dist/server/server/agent/providers/generic-acp-agent.js"
ACPMODEL_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-model-rejection.mjs"
ACPMODEL_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-model-rejection.test.mjs"
ACPCATALOG_PATCHER="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-agy-shared-catalog.mjs"
ACPCATALOG_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/patches" 2>/dev/null && pwd || true)/acp-agy-shared-catalog.test.mjs"
PROVIDER_REGISTRY_JS="$PASEO_SERVER_DIR/dist/server/server/agent/provider-registry.js"
apply_acp_gap() {  # <label> <patcher> <target-js> <behaviour-test>
  local label="$1" patcher="$2" target="$3" test_js="$4" ag_rc=0 ag_out ag_tmp
  if [ ! -f "$target" ]; then
    log "warning: $label target not found ($target) — skipped"
    return 0
  fi
  ag_out="$(node "$patcher" "$target")" || ag_rc=$?
  case "$ag_rc" in
    10) log "$label already applied" ;;
    20) patch_skipped "$(basename "$patcher" .mjs)" "$label" ;;
    0)
      ag_tmp="${target}.paseo-new.mjs"
      if ! node --check "$ag_tmp"; then
        rm -f "$ag_tmp"
        log "warning: $label produced invalid JS — not applied"
      elif [ -n "$test_js" ] && ! node "$test_js" "$ag_tmp" >/dev/null 2>&1; then
        rm -f "$ag_tmp"
        log "warning: $label failed its behaviour check — not applied"
      else
        mv "$ag_tmp" "$target" || die "$label mv failed"
        :
        log "$label applied"
      fi
      ;;
    *) log "warning: $label patcher error (rc=$ag_rc): $ag_out — skipped" ;;
  esac
}
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] apply agy ACP gaps to $ACP_AGENT_JS, $GENERIC_ACP_AGENT_JS and $PROVIDER_REGISTRY_JS"
elif [ ! -f "$ACPGAUGE_PATCHER" ] || [ ! -f "$ACPMODE_PATCHER" ] || [ ! -f "$ACPMODEL_PATCHER" ] \
  || [ ! -f "$ACPCATALOG_PATCHER" ]; then
  log "warning: agy ACP gap patchers not found — skipped"
else
  apply_acp_gap "ACP context gauge" "$ACPGAUGE_PATCHER" "$ACP_AGENT_JS" "$ACPGAUGE_TEST"
  apply_acp_gap "ACP cross-provider mode default" "$ACPMODE_PATCHER" "$GENERIC_ACP_AGENT_JS" "$ACPMODE_TEST"
  apply_acp_gap "ACP invalid model rejection" "$ACPMODEL_PATCHER" "$ACP_AGENT_JS" "$ACPMODEL_TEST"
  apply_acp_gap "agy shared model catalogue" "$ACPCATALOG_PATCHER" "$PROVIDER_REGISTRY_JS" "$ACPCATALOG_TEST"
fi
# wrapSessionProvider leaves steerActiveTurn out, so AgentManager treats every steer as
# "unavailable" and interrupts the running turn: an automatic message kills the seat's in-flight
# tool call and the seat reads it as a human refusal (Airlock #987). See patches/README.md.
if [ "${STATIONDESK_DRY_RUN:-0}" = 1 ]; then
  log "[dry] forward steerActiveTurn in $PROVIDER_REGISTRY_JS"
else
  apply_acp_gap "session steer forwarding" "$HERE/patches/paseo-steer-forward.mjs" "$PROVIDER_REGISTRY_JS" "$HERE/patches/paseo-steer-forward.test.mjs"
fi


# Preserve the daemon's ~/.paseo storage format and config, including provider IDs.
export PASEO_HOME="$HOME/.paseo"
mkdir -p "$PASEO_HOME"
node - "$PASEO_HOME/config.json" "$STATIONDESK_PORT" <<'NODE'
const fs = require('fs');
const path = process.argv[2];
const config = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, 'utf8')) : {};
config.daemon ??= {};
config.daemon.listen = `127.0.0.1:${process.argv[3]}`;
config.daemon.browserTools ??= {};
config.daemon.browserTools.enabled = true;
config.daemon.mcp ??= {};
config.daemon.mcp.injectIntoAgents = true;
const content = JSON.stringify(config, null, 2) + '\n';
if (!fs.existsSync(path) || fs.readFileSync(path, 'utf8') !== content) fs.writeFileSync(path, content);
NODE
agy_rc=0
STATIONDESK_PASEO_AGY=true bash "$HERE/agy-acp/install.sh" || agy_rc=$?
case "$agy_rc" in 0|2) ;; *) die "agy-acp build/config failed (rc=$agy_rc)" ;; esac
"$PY" "$HERE/configure-claude-default.py" --paseo-home "$PASEO_HOME" \
    --model "${STATIONDESK_PASEO_CLAUDE_DEFAULT_MODEL:-}" || [ "$?" = 2 ]
PASEO_WEBUI_DIR="$WEBUI_DIR" PASEO_BROWSE_FQDN="$STATIONDESK_FQDN" \
    PASEO_BACKEND_PORT="$STATIONDESK_PORT" PASEO_BROWSE_STREAM_PORT=19955 \
    PASEO_HTTPS_PORT=19950 bash "$HERE/browse-host/install.sh"

# Preserve the current 15/16 hard ceiling and 14/16 throttle band. cgroup RAM
# takes precedence inside containers. Drop-in belongs to this app's ledger.
case "${STATIONDESK_PASEO_MEM_CAP_BYTES:-0}" in
  *[!0-9]*) die "STATIONDESK_PASEO_MEM_CAP_BYTES must be a plain byte count, got: ${STATIONDESK_PASEO_MEM_CAP_BYTES}" ;;
esac
_cap="${STATIONDESK_PASEO_MEM_CAP_BYTES:-$(cat /sys/fs/cgroup/memory.max 2>/dev/null || true)}"
case "$_cap" in ''|max|*[!0-9]*) _cap=$(( $(awk '/^MemTotal:/{print $2}' /proc/meminfo) * 1024 )) ;; esac
_cap_gib=$(( _cap / 1073741824 ))
if [ $(( _cap % 1073741824 )) -ge 536870912 ]; then _cap_gib=$(( _cap_gib + 1 )); fi
[ "$_cap_gib" -ge 1 ] || _cap_gib=1
_memmax_mib=$(( _cap_gib * 1024 * 15 / 16 ))
_memhigh_mib=$(( _cap_gib * 1024 * 14 / 16 ))
_cap_mib=$(( _cap / 1048576 ))
if [ "$_memmax_mib" -gt "$_cap_mib" ]; then
  _memmax_mib=$(( _cap_mib * 15 / 16 ))
  _memhigh_mib=$(( _cap_mib * 14 / 16 ))
fi
if [ "$_memmax_mib" -lt 3 ]; then _memmax_mib=2; _memhigh_mib=1; fi
PASEO_TASKSMAX="${STATIONDESK_PASEO_TASKS_MAX:-infinity}"
if [ "${STATIONDESK_PASEO_ALLOW_UNBACKED_MEM:-0}" = 1 ]; then
  log "WARNING: paseo memory backstop disabled by explicit override STATIONDESK_PASEO_ALLOW_UNBACKED_MEM=1 \
— this unit gets no memory limit at all: cap=${_cap} bytes (~${_cap_gib} GiB); rendering \
MemoryMax=infinity and MemoryHigh=infinity. TasksMax=${PASEO_TASKSMAX} — with the default that \
leaves the enclosing user slice as the only limit of any kind on this unit."
  PASEO_MEMMAX=infinity
  PASEO_MEMHIGH=infinity
else
  PASEO_MEMMAX="${_memmax_mib}M"
  PASEO_MEMHIGH="${_memhigh_mib}M"
  log "paseo memory share: cap=${_cap} bytes (~${_cap_gib} GiB) -> MemoryMax=${PASEO_MEMMAX} \
(15/16) MemoryHigh=${PASEO_MEMHIGH} (14/16)"
fi

dropin="$HOME/.config/systemd/user/stationdesk-paseo.service.d"
mkdir -p "$dropin"
resources=$(printf '[Service]\nMemoryMax=%s\nMemoryHigh=%s\nTasksMax=%s\n' \
    "$PASEO_MEMMAX" "$PASEO_MEMHIGH" "$PASEO_TASKSMAX")
if ! printf '%s\n' "$resources" | cmp -s - "$dropin/resources.conf"; then
  printf '%s\n' "$resources" > "$dropin/resources.conf"
fi
if command -v claude >/dev/null 2>&1 && ! claude mcp get paseo >/dev/null 2>&1; then
    claude mcp add --transport http --scope user paseo "http://127.0.0.1:$STATIONDESK_PORT/mcp/agents" \
        || log "Claude MCP registration failed; daemon remains available"
fi
log "guarded Paseo $PASEO_VERSION installed (same ~/.paseo format; Node $(node --version))"
