# Paseo patches

StationDesk's Paseo modifications in this directory are **AGPL-3.0-only**.
The pinned Paseo 0.8.0 base is **Apache-2.0**; upstream portions retain that
license. The complete texts are [LICENSE](LICENSE) and
[UPSTREAM-LICENSE](UPSTREAM-LICENSE). See the repository [NOTICE](../../../NOTICE)
for component licenses.

The guarded archives and their editable TypeScript source, downstream
JavaScript delta and build instructions are documented in
[the bundle README](../vendor/guarded-0.8.0/README.md) and
[the source README](../vendor/guarded-0.8.0/source/README.md).
Descriptions below include dated observations from the patches' development;
they do not report a new test run on the reader's installation.

## What is here

- **`agent-history-delete-guard.mjs`** — refuses CLI permanent deletion before
  connecting or cancelling agents.

- **`workspace-git-identity.mjs`** (+ `.test.mjs`) — `getCheckout` reads fresh
  checkout identity with the existing checkout inspector and common-dir resolver,
  without dirty state, comparisons, upstream tracking or pull-request lookup.
  The six fields preserve full-status normalization, including Paseo ownership's
  base-ref condition. The pair is baked into the guarded bundle; install fallback
  checks both candidate modules and behavior before replacing them. Automatic
  reconciliation remains unsampled; automatic timing is set by `background-git-frequency.mjs`.

- **`background-git-frequency.mjs`** (+ `.test.mjs`) — lowers automatic work:
  full reconciliation and background fetch every 30 minutes, root-event debounce
  30 seconds, workspace-event debounce 10 seconds, observation reensure 5 minutes,
  degraded polling 1 minute (quiet working trees back off to 5 minutes), and forge
  PR polling 2 minutes while pending or 10 minutes when stable (errors cap at
  30 minutes). First/manual reconciliation, first fetch and explicit forced Git
  requests keep their existing behavior; the internal 2-second minimum gap stays.
  Sustained root events can still reconcile all projects every 30 seconds.
  All three candidate modules are checked before replacement.

- **`agent-storage-load-once.mjs`** (+ `.test.mjs`) — removes the full cache-array
  allocation from an already loaded storage object's `load()` path. Warm lookups
  and writes need only await initialization; explicit `list()` still enumerates
  records. The existing shared initial disk scan and write/archive methods stay
  intact, with no history or unload policy changes.

- **`schedule-busy-pending-delivery.mjs`** (+ `.patch`, `.test.mjs`) and its schema half
  **`schedule-pending-delivery-schema.mjs`** (+ `.patch`) — when an agent-target schedule fires while
  that seat is mid-turn, upstream records a FAILED run and jumps to the next cadence: the tick is
  lost, failed runs eat `maxRuns`, and masters learn to end turns early so they are idle when their
  clock ticks (pilot box 2026-09-13: 5 of 17 agent-target runs were exactly `already has an active
  run`). The patch folds busy into one `pendingAgentDelivery` bit — no failed run, no queue — and
  the next tick delivers exactly once as soon as the seat is idle. The schema half keeps the bit
  across restarts (zod strips unknown keys); the two halves are safe in either order and alone.
  Same discipline as the others: sentinel, all-or-nothing anchors, `node --check`, plus a behaviour
  check that drives the candidate with stub seats before it is installed.

- **`idle-agent-unload.mjs`** (+ `.patch`, `.test.mjs`) — upstream keeps every loaded seat and
  the full timeline of every seat it ever loaded in daemon memory: nothing unloads an idle seat,
  and a plain close keeps the timeline (only archive drops it). On a box creating ~275 seats a day
  the heap followed the timeline row count to the ~4GB V8 ceiling and the daemon died of "heap out
  of memory" three times in four days (2026-10-16, 10-17, 10-20). The patch unloads a seat idle for
  `PASEO_IDLE_UNLOAD_MINUTES` (default 30, `0` = off;
  `../units/stationdesk-paseo.service` sets 360) — session closed, timeline dropped, stored
  record untouched so it stays `idle`, the same state as a seat nobody touched since a restart —
  and makes closeAgentRuntime drop the timeline too. The next send, fetch or schedule tick resumes
  the seat through `ensureAgentLoaded` and re-reads provider history. The unload runs in Paseo's
  per-seat lifecycle queue, so a concurrent close or load waits for it; only the seat's id is kept,
  so an explicit close of an unloaded seat still records `closed` on the current stored record.
  Memory then follows the seats at work, and the sweep's 24-hour close rule is not involved. The
  behaviour test drives the real AgentManager with stub seats over the real AgentStorage, including
  a close racing the unload and a label written after it; the two earlier designs of this patch
  and deliberate mutants each fail it.

- **`reload-timeline-replacement.mjs`** (+ `.patch`, `.test.mjs`) — a seat loaded again rebuilds
  its timeline from provider history under a fresh epoch (the durable timeline store is not wired).
  Upstream only reloads at daemon start, when every client reconnects and refetches. With
  `idle-agent-unload` clients stay connected through the reload, keep the old epoch, and the app's
  sequencing gate drops every new row whose seq is not 1 (`drop_epoch`): the seat answered, the
  open chat showed nothing (2026-10-21, "오래된 세션이 말해도 반응이 없다"; the daemon had
  accepted the sends). The patch dispatches `timeline_replacement` — the event rewind already uses — after
  the first hydration of a load, before the caller's prompt, so viewers refetch the tail and adopt
  the new epoch. Keyed on the first prime rather than the unload id set, so close → reopen is
  covered and the unload sweep cannot race it. It applies alone or on top of `idle-agent-unload`;
  a separate sentinel lets already-patched boxes take it without a reinstall.

- **`depth4-search.patch`** — caps the add-project name search to `maxDepth: 4`
  (paseo's default full-scans `$HOME` and times out on a large home). This is the
  reference / re-derivation copy of the edit; `../install.sh` applies it via an
  idempotent `sed` against the installed bundle.

- **`image-attachments-persist.mjs`** (+ `image-attachments-persist.patch`, the
  reference copy) — an image pasted into paseo's web UI reaches the model only as an
  inline base64 vision block: the model sees it, but no file exists, so the agent's
  `Read` tool has no path and "look at this screenshot, then edit the file" dead-ends.
  The patch keeps the inline block and *also* writes the bytes under the session cwd
  (`<cwd>/.paseo-attachments/`, self-ignored via its own `.gitignore`, name
  content-addressed so a re-paste dedups), then appends a sibling text block naming the
  absolute path. Same discipline as the others: sentinel (idempotent), all-or-nothing
  (three anchors or none), `node --check` before it replaces the file, exit 20 on
  upstream drift so the install continues without the feature.

- **`claude-model-prune.mjs`** (+ `claude-model-prune.patch`, the reference copy)
  — removes superseded entries (Opus 4.7/4.6, Sonnet 4.6) from paseo's
  `CLAUDE_MODEL_MANIFEST` so the picker is the handful people actually choose. Picker-only: the manifest
  is not on the execution path, so an agent already pinned to a removed model
  keeps running (it just loses the known context-window maximum in the gauge).
  Decomposes the array into entry blocks and refuses to write unless they
  reassemble byte-for-byte, so an upstream format change skips instead of
  mangling the file. Edit `PRUNE_IDS` to change which models are hidden.
  It also adds models released after the pin (`ADD_BLOCKS`; Opus 5.5 and
  Sonnet 5.5, each placed before its predecessor). Added entries
  advertise only what was measured — the CLI accepts the id — so thinking-off and
  fast mode stay off until someone runs them. A manifest pruned by the previous
  version (sentinel present, entry absent) still gains the addition.

- **`opencode-grok-defaults.mjs`** (+ `opencode-grok-defaults.patch`, the reference copy)
  — OpenCode's catalog order treats the first variant key as the thinking default
  (`low`) and lists models in provider-catalog order, not StationDesk's preference. The
  patch sorts the picker so `opencode-go/muse-spark-1.3-contributor` is first,
  `xai/grok-4.6` second, and `xai/grok-build-0.1` third, and moves each of their
  thinking defaults (`xhigh` for muse-spark, `high` for grok-4.6) to the front so new
  sessions start there. muse-spark's `xhigh` is the top of the models.dev catalog
  (which stops at `xhigh`); the earlier box-local `max` variant was rejected by the
  provider, so no box-local variant is needed. Picker/create-form only: an already-running
  agent's model and thinking stay on the session record. Both anchors or none.

- **`provider-subagent-stream-filter.mjs`** (+ reference patch and behavior test)
  — filters provider-child updates by the viewed parent-agent subscription for
  each WebSocket source. Legacy clients retain the broadcast fallback. The
  original patch targeted 0.2.5; the current patch targets 0.8.0's
  `forwardProviderSubagentUpdate` method. It is paired with the
  `--subagent-stream` group in `../browse-host/bin/patch-web-ui.js`, which
  includes a child tab's `parentAgentId` in the subscription set. The installer
  validates both halves before replacing a server candidate. An installed
  filter that fails its final behavior check stops installation; other warning
  and drift paths are described at the end of this document.

- **`orphan-process-guard.mjs`** (+ `orphan-process-guard-{claude,codex}.patch`, the
  reference copies, and `orphan-process-guard.test.mjs`, a behaviour check) — paseo
  leaks the agent processes it spawns. Both providers track exactly one live child
  (`this.childProcess` / `this.client`) and terminate it behind an `if (handle)` with
  no else branch, and neither honours the closed flag on its spawn entry point
  (`ensureQuery()` / `connect()`). A control-plane call landing during or after close —
  `setMode`, `setModel`, `listCommands`, `revertFiles`, a codex reconnect, or the
  in-flight spawn simply finishing late — starts a **replacement** process on a session
  nothing will ever close again; it runs until the box is rebooted. close() reports
  success because at that instant there was genuinely nothing to kill, and the
  surrounding `session_close.start/complete` lines are `logger.trace`, which the
  daemon's info-level logger never emits — so the whole class of leak was unobservable.
  Measured on the pilot box 2026-08-05: 18 orphans, 2.9G RSS + 1.9G swap.
  The patch makes ownership a `Set` (a replaced handle is still terminated), gates both
  spawn entry points, terminates a late arrival on the spot instead of storing it, and
  `logger.warn`s every branch that used to be silent. Two independent targets, one
  invocation each (`claude` / `codex`) so a drift in one does not disable the other;
  anchors must be present **and unique** or it exits 20. Deliberately out of scope:
  `detached: true` + process-group kill, which would also cover MCP children orphaned
  when the leader exits first — that changes the signal/session semantics of the
  provider spawn and needs its own change and observation window; this patch logs that
  case loudly instead. Verify after install:
  `node orphan-process-guard.test.mjs <installed>/providers/claude/agent.js`.

- **`orphan-process-group.mjs`** (+ `orphan-process-group.test.mjs`) — closes the one
  leak the guard above deliberately left open. When the agent **leader** exits before we
  terminate it, `terminateWithTreeKill` returns `"already-exited"` and stops — and by then
  the leader's MCP children have been reparented, so a ppid-walking tree-kill can no longer
  find them; they survive as orphans. A **process group outlives its leader**, so killing
  the group reaches them. Controlled experiment (pilot box, 2026-08-06):

  | spawn | pgid | leader kill | `kill(-pid)` |
  |---|---|---|---|
  | `detached: false` | ≠ pid | grandchild orphaned | **ESRCH** (no such group — harmless) |
  | `detached: true` | = pid | grandchild orphaned | grandchild **dies** ✅ |

  In both cases the child stays in `stationdesk-paseo.service`'s cgroup, so `KillMode=control-group`
  still sweeps everything on daemon restart — `detached` does not escape the cgroup. That
  ESRCH result is what makes the sweep safe if the spawn edit ever fails to apply: a group id
  *is* its leader's pid, so `kill(-pid)` can only reach the group led by that same process.
  codex already spawned its app-server detached upstream and merely never killed the group;
  claude needed both halves. 🔴 Apply **after** `orphan-process-guard.mjs` — the
  `claude-agent` anchors are text that patch introduces (otherwise exit 20 = skip, not a
  half-fix). The behaviour check spawns real detached processes and asserts the shipped
  sweep reaps a survivor, because this is the half that signals other processes.

- **`acp-context-gauge.mjs`** (+ `.test.mjs`) — agy runs over Paseo's generic ACP
  provider. `usage_update` is parsed and dropped (`handleUsageUpdate` was `void
  update;`) — no context-window gauge for any ACP provider. `handlePromptResponse`
  then OVERWRITES `currentTurnUsage` at turn end instead of merging, so even a turn
  that did see a mid-turn `usage_update` loses those fields the moment it ends. A
  `config_option_update` that only touches model or thinking reassigns
  `availableModes` from a mode-state-less `deriveModesFromACP` call, blanking any
  mode list that came from `session/new` rather than a config option. And an ACP
  agent's own advertised unattended mode (`_meta.paseo.isUnattended`) never reaches
  the mode list or the catalog probe's `defaultModeId`. Five edit sites, one file,
  same discipline as the others: sentinel, all-or-nothing anchors, `node --check`,
  plus a behaviour test that drives the extracted methods directly (merge-not-
  overwrite, mode-list survival, event shape).

- **`acp-cross-provider-mode-default.mjs`** (+ `.test.mjs`) — once a generic ACP
  agent (agy) advertises modes, `paseo run --provider agy` with no `--mode` from an
  **attended** agent of a *different* provider is refused ("cannot inherit mode").
  The base `ACPAgentClient` already bypasses this, but only for an unattended create
  or an unattended parent (`resolveACPCreateConfig`, `acp-agent.js`) — attended
  cross-provider callers still hit the throw. A plain method override on
  `GenericACPAgentClient` would be dead code against 0.8.0: the base constructor
  assigns `this.resolveCreateConfig` as an *own instance property*, which always
  shadows a subclass's same-named prototype method. The patch instead captures that
  already-assigned function in the subclass constructor and wraps it — bypass for
  the attended-cross-provider-no-mode case, fall through to the captured base
  function (preserving its auto-accept feature-value injection) otherwise. Same
  codebase pattern the base class itself already uses for provider dispatch.

- **`acp-model-rejection.mjs`** (+ `.test.mjs`) — rejects unavailable legacy
  models and model config choices with an error after the existing warning.
  This prevents AgentManager from recording a requested model that the ACP
  consumer did not select. Tests drive the real bundled selection helpers,
  session methods, and manager method; invalid choices preserve previous state,
  while valid choices reach the ACP connection. The ACP module is not pinned in
  `INSTALLED_SHA256SUMS`, so this follows the existing install-time ACP overlays
  without rebaking the vendor bundle.

- **`acp-agy-shared-catalog.mjs`** (+ `.test.mjs`) — a new workspace listed agy's
  models ~10s after claude/codex. Providers already load in parallel; the gap was the
  cache. claude/codex return catalogue key `"host"`, so every workspace shares one
  catalogue, while a generic ACP client has no key and was re-probed per workspace cwd
  (spawn agy-acp, `agy models` ~3s, then a real agy start for `session/new` ~7s). The
  patch edits `agent/provider-registry.js`: `wrapClientProvider` now forwards
  `getCatalogCacheKey` (agy is always wrapped: inner provider `acp`, id `agy`), and the
  provider `agy` gets `"host"`. Other generic ACP providers stay per-cwd. The shared
  catalogue is refreshed the same way claude/codex's is (provider refresh in the UI).

- **`paseo-steer-forward.mjs`** (+ `.test.mjs`) — an automatic message (session-delivery,
  `activeTurnBehavior: "steer"`) to a seat running a tool killed that tool call, and the seat
  read the result "The user doesn't want to proceed… STOP" as a human refusal (observed during inter-session delivery).
  `agent/provider-registry.js` `wrapSessionProvider` copied the session methods one by one and left
  out `steerActiveTurn`, so `AgentManager.steerOrReplaceActiveTurn` saw it as missing,
  answered "unavailable" and replaced (interrupted) the running turn. The patch forwards the method;
  the steer is accepted and the tool finishes. Server module, so the daemon restarts to pick it up.
  **Remove when** the daemon moves to a build whose `provider-registry` already forwards it
  (upstream `packages/server/src/server/agent/provider-registry.ts`).

- **`codex-model-roster.mjs`** (+ `.patch`, `.test.mjs`) — keeps the Codex picker
  driven by the live app-server catalog while retiring the exact `gpt-5.5` row.
  It also marks `gpt-6-sol` and `gpt-6-luna` as Fast-capable, so those controls
  appear as soon as the signed-in ChatGPT account's `model/list` exposes the new
  family. It does not synthesize unavailable rows: on 2026-09-23 the public API
  catalog listed both models while this box's ChatGPT-backed Codex rejected both,
  so availability stays with Codex and the two-file overlay is verified and
  installed as one unit. By 2026-09-30 `gpt-6-sol` was accepted here, and
  `gpt-6.1-sol` was accepted only after Codex CLI 0.156.1 → 0.159.2 (0.159.1 added
  it to the bundled catalog); `paseo provider models codex` then lists it first.
  `gpt-6.1-sol` is not in the Fast list above: whether it takes the existing Fast
  mode was not measured, so no row is added on assumption.

- **`agent-resolve-by-id.mjs`** (+ `.test.mjs`) — `paseo agent archive`, `detach` and `reload`
  resolve their target by scanning the `includeArchived` agent list, which the server caps at
  200 (`session.js` `limit ?? 200`; the CLI passes no `--page`). On a box with thousands of
  archived agents an old or stopped seat sorts past the cap and the command answers `Agent not
  found` — precisely the seat you are trying to clean up or reload (a 24h sweep on 2026-09-21
  hit 17/17 failures). The sibling commands `stop`/`delete` already resolve a single id straight
  from the daemon with `fetchAgent({agentId})`; this patch gives the three list-scanning commands
  the same fast path, leaving prefix/name on the list (the daemon cannot resolve those directly).
  CLI modules only, so **no daemon restart** — same class as `agent-history-delete-guard`.
  **Remove this overlay when upstream resolves single ids in the CLI** (a `--page` loop or an id
  lookup); the anchors disappear with the fix, so the patcher then skips with exit 20.

- **`archive-consistency.mjs`** (+ `.patch`, `.test.mjs`) — keeps the upstream self-archive
  filter and additionally hides an otherwise-live agent only when its `workspaceId` matches a
  workspace-registry record whose `archivedAt` is set. It applies atomically to `session.js` and
  `agent-updates-service.js`: the initial live-plus-persisted snapshot and later stored/live
  `agent_update` subscriptions agree, so an archived-workspace agent cannot reappear on the
  next upsert. Agents with no workspace ID and agents pointing at an unknown workspace retain
  their existing behavior, while `includeArchived=true` remains the diagnostic/recovery view.
  The behavior check verifies active, archived, null, undefined, unknown, and self-archived
  records across both paths. This server read/update patch requires a daemon restart when newly
  applied.

- **`send-keep-pending-permissions.mjs`** (+ `.test.mjs`) — the daemon's `send_agent_message_request`
  handler hard-codes `clearPendingPermissions: true`, so an automatic inter-session message
  (session-delivery, `activeTurnBehavior: "steer"`) denies the permission request the receiving seat
  was waiting on. The request gains an optional boolean `clearPendingPermissions`; the handler uses
  `msg.clearPendingPermissions ?? true`, so a sender that omits it (the browser UI, a human) keeps
  today's behaviour, and the client's `sendAgentMessage` forwards the option only when it is a
  boolean. Three files, one patch id, all-or-nothing: `@getpaseo/protocol` `dist/messages.js` (zod
  strips unknown keys, so the schema must learn the field), `@getpaseo/server` `session.js` and
  `@getpaseo/client` `dist/daemon-client.js`; every anchor must be present and unique or exit 20,
  a mixed state also exits 20, and nothing is written. The `Session.sendText` site (a human typing
  or speaking) is untouched. The behaviour test drives the real zod schema and the shipped client and
  server methods with stubs: absent -> `true`, `false` -> `false`. Baked into the three vendored
  tarballs (all three files are checksum-pinned; see `../vendor/guarded-0.8.0/README.md`), so the
  installer normally sees `ALREADY`. Server file changes, so a newly applied patch needs a daemon restart. Senders opt in by passing `clearPendingPermissions: false`
  to `DaemonClient.sendAgentMessage`; this patch alone changes no sender.

- **`anchor-manifest.json`** — records Paseo 0.8.0, the pristine web UI hash,
  known patched bundle shapes, representative patch anchors and the paired
  server/UI dependency. Its `web_ui` version and hash match
  `patch-web-ui.js`'s `PINNED_VERSION`, `PINNED_SHA` and
  `KNOWN_BUNDLE_SHAPES`. The manifest records data used by drift checks; it
  does not include the upstream browser bundle itself.

The browser-host web UI patcher lives outside this directory:
**`../browse-host/bin/patch-web-ui.js`** (`SPDX-License-Identifier:
AGPL-3.0-only`). Its always-on `--subagent-stream` group fixes the provider-child
subscription boundary, sets the fresh-install font-size defaults (ui 18 / code 14
instead of upstream's 16 / 12 — a device that already saved settings keeps its own),
shares the sidebar order across devices, and makes touch devices usable (tooltips do
not park over the composer; a coarse pointer gets the project row's `+` without first
manufacturing a hover; one tap on a sidebar row navigates, instead of being eaten by
the long-press/drag machinery web never arms), and keeps an archive request waiting
for the daemon's answer instead of timing out at 60 s and putting the archived row
back; its optional `--browse` group contains
the three live-panel edits. Both share fail-loud state classification, syntax gating,
and content-hash cache busting. The remaining browser-host service runs separately from the compiled Paseo packages.

## Additional server patches

- `workspace-remove-delivery.mjs` fixes deleted workspaces remaining in the sidebar
  after reconnect. A sequenced changes-only bootstrap does not seed unchanged
  cached rows into `lastEmittedByWorkspaceId`; their archive must still emit a
  removal. Repeated removals remain deduplicated. The behavior test executes the
  installed Session delivery method. A daemon restart activates this server patch.
- `directory-sync-snapshot.mjs` (+ `.test.mjs`) stops an archived workspace or agent from
  staying on a device that was not watching when it was archived. A changes read returns
  only what is newer than the device's cursor, so a cursor that has passed the removal
  never receives it and the row survives every reload until the daemon restarts (measured
  2026-10-10: moving a cached cursor 34 steps past a removal kept the archived row
  through two reloads). Workspace and agent directory reads now answer with the full
  list, which the client already uses to replace its own; project reads keep their
  cursor. The behavior test executes the installed `DirectorySyncService`. A daemon
  restart activates this server patch.
- `reconcile-archives-agents.mjs` (+ `.test.mjs`) closes the path that leaves agents
  unarchived under an archived workspace. When a worktree directory is removed outside
  Paseo, automatic reconciliation archives the workspace record but not its
  agents (measured 2026-10-11: 58 such agents, six from the last five days, all with a
  missing directory). The reconciliation hook now calls the same `archiveWorkspaceContents`
  a UI archive uses. A daemon restart activates this server patch.
- `mcp-auth-off-argv.mjs` (+ `.test.mjs`) keeps the daemon's Agent MCP bearer
  (`Authorization: Bearer <per-run capability token>`, injected into every seat's MCP
  config) out of places anything on the box can read. The Agent SDK passed it to claude
  as inline `--mcp-config` JSON, so it sat in argv (`ps`); claude now gets the same JSON
  as a 0600 file in a fresh 0700 temp directory, removed when claude exits. Providers
  also stored it in `persistence.metadata.mcpServers` of every agent record (measured
  2026-10-15: 9,098 of 10,119 records); the daemon's own server is now dropped there —
  it is re-injected on every launch and resume — and user MCP servers stay. Baked into
  the bundle (both targets); the patcher re-derives it on a new Paseo build and the
  behavior test is `mcp-auth-off-argv.test.mjs`.
  A daemon restart activates it.

## License review record

The original release checklist recorded an open review of
patch/anchor excerpts, with a preference for install-time derivation where
feasible. That review has not been recorded as complete here.

For network deployments, see AGPL section 13 in [LICENSE](LICENSE).

## When a patch no longer fits

Patchers generally use exit 20 for an unrecognized anchor. Where `../install.sh`
passes that result to `patch_skipped`, installation stops unless
[`skip-approved.txt`](skip-approved.txt) records the patch name, approver and
reason. Other missing-target, invalid-candidate or optional-feature paths can
warn and continue; the installer is the authority for each patch's handling.
Before 2026-10-11, skipped anchors only produced warnings; that earlier behavior
allowed a fix to be lost during the 0.8.0 upgrade. Port a drifting patch or record
an intentional skip alongside the bundle change.
