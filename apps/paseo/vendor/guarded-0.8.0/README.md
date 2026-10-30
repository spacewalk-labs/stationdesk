# Guarded Paseo 0.8.0 bundle

This directory is the reproducible StationDesk input for the guarded Paseo 0.8.0
deployment. The seven npm package tarballs are installed together so npm resolves
the modified `@getpaseo` packages as one version-consistent set.

- Base: upstream Paseo `v0.8.0` (`b8e24677e12b226c7c38c1c3a40649daa9f1152f`)
- License: Apache-2.0 (upstream, since `v0.7`) for the unmodified portions; our own
  overlay is licensed AGPL-3.0-only by our own choice — see `LICENSE` (our AGPL-3.0
  text) and `UPSTREAM-LICENSE` (upstream's Apache-2.0 text) beside this file, and
  [the patch inventory](../../patches/README.md).

The installer verifies `SHA256SUMS` before changing the npm prefix and records
the checksum-file digest as the installed identity. A changed digest reinstalls
the seven local tarballs even when their version remains `0.8.0`; npm replaces
the installed package bytes, including removed overlays. `INSTALLED_SHA256SUMS`
pins the overlaid files for bundle verification. The installer uses these
archives rather than a configurable registry version. Current source and rebuild
instructions are in [source/README.md](source/README.md).

## Overlay history and source

The following notes preserve the bundle's development and earlier deployment
validation records. Dated measurements and test totals describe those runs;
they are not a new verification of every historical claim.
An earlier fork commit recorded below is historical provenance, not a required
build input; the rebuild uses the pinned public upstream source and included patches.

The 0.2.5 predecessor of this bundle carried a never-upstreamed fork PR
(`6d13dd8e042de1120ff63ac19c0d690d0ad7c32e` on top of merge-base `6fc491e6220fba6543bbbe4bf1b1f58cfe59228b`) adding three protections beyond stock Paseo: an MCP
fix so archived sessions stop appearing live, an atomic guard on agent-message
send (`agentMessageSendGuard`), and exact schedule pause/resume state restore
(`scheduleStateRestore`). Comparing that fork commit against the real `v0.8.0`
tag via the GitHub compare API (718 commits apart, never merged) showed:

- The MCP archived-session fix is independently covered by upstream 0.8.0's own
  `includeArchived` filtering (`agent/tools/paseo-tools.js`). Nothing carried
  forward.
- `agentRequestReceipts` — the request-dedup journal the old fork intentionally
  left at capability `false` (it didn't cover keyed agent creation) — is now a
  complete, independent upstream implementation (`agent/requests/index.ts`,
  covering both `create` and `send`) and defaults to `true`. We reuse it as-is;
  it is not part of this overlay.
- `agentMessageSendGuard` and `scheduleStateRestore` were genuinely absent from
  `v0.8.0` (confirmed in the pinned source). They were re-derived against the
  0.8.0 lifecycle mutation and request mechanisms. The current editable
  TypeScript source is in `source/guard-source.patch`, with public upstream
  inputs, build commands, exact downstream patches and verification in
  `source/README.md`. The original build checkout was unavailable; the new
  source was reconstructed from the shipped guard output and declarations,
  then checked with a fresh strict build, AST comparison and behavior tests.

The server archive overlays the reconstructed guard build output for the eight
`@getpaseo/server` files the port actually touches (`agent/agent-manager.js`,
`agent/agent-prompt.js`, `authorization/operation-permissions.js`,
`schedule/service.js`, `schedule/store.js`,
`session/schedule/schedule-session.js`, `session.js`, `websocket-server.js`),
the protocol archive overlays `messages.js`, `schedule/rpc-schemas.js`, and the
generated validator `generated/validation/ws-outbound.aot.js`, and the client
archive overlays `daemon-client.js`.

On top of that guard-port overlay, four local StationDesk patches that have no
runtime behaviour test — just a syntactic anchor match plus `node --check`
(`depth4-search`, `image-attachments-persist`, `claude-model-prune`,
`opencode-grok-defaults`) — are baked into the tarballs too, same as the
0.2.5 predecessor baked in every patch it shipped: a stock re-download of
the same version cannot silently regress any of them, and `install.sh`'s own
patch steps correctly report "already applied" instead of doing redundant
first-install work. `opencode-grok-defaults` needed real re-derivation, not
just anchor drift: 0.8.0's thinking-default selection changed mechanism
(`defaultThinkingOptionId` now reads `thinkingOptions[0].id` off an array
that starts with a synthetic "Default" entry, not `rawVariants.map(...,
isDefault: index === 0)`), so the fix now reorders the assembled
`thinkingOptions` array instead of `rawVariants` — verified by isolating
`buildOpenCodeModelDefinition` and driving it with a mock grok-4.6 model
(`defaultThinkingOptionId` came back `"high"` as intended).

`opencode-grok-defaults` was re-baked again (2026-09-16, first revision) to put
`opencode-go/muse-spark-1.3-contributor` first in the picker (ahead of
`xai/grok-4.6`, then `xai/grok-build-0.1`) and default its thinking to `max`
via the same reorder mechanism as grok-4.6's `high`. `max` is not in the
models.dev catalog (it stops at `xhigh`) — it only exists as a variant when a
box's own `~/.config/opencode/opencode.jsonc` defines it (box-local config,
not shipped here), so this half of the patch is a no-op on every box that
hasn't opted into `opencode-go`/muse-spark. Re-baking (rather than leaving
this install-time-only) was required for the same idempotency reason as the
patches below: the sentinel text did not change, so the already-baked file
already satisfied the old patcher's `already applied` check — only replacing
the vendored bytes actually ships the new order.

`opencode-grok-defaults` was re-baked once more (2026-09-16, corrected revision): the
muse-spark thinking default is `xhigh`, not `max`. Measured on the box, the
`max` variant (a box-local `opencode.jsonc` invention — the models.dev catalog
stops at `xhigh`) is rejected by the provider
(`invalid_request_error: The request contains invalid parameters`), so the
first revision's default broke effort changes in Paseo. Same reorder mechanism, same
idempotency reason (sentinel still unchanged), same no-op property on boxes
without `opencode-go`/muse-spark.

`provider-subagent-stream-filter` is baked in too, despite having its own
runtime behaviour test (unlike the four above) — that test passes cleanly
against the 0.8.0-derived candidate, so there is no verification gap to
avoid shipping unverified. Needed real re-derivation: 0.8.0 already built
`forwardProviderSubagentUpdate` itself (a per-capability, per-source delivery
method that did not exist in 0.2.5), but it still does not filter by the
VIEWED parent agent — every source that supports the capability still
receives every provider-subagent update, the same over-broadcast the 0.2.5
patch closed. The fix adds the same `viewedTimelineAgentIds`/
`viewedTimelineAgentIdsBySource` checks `forwardAgentStream` (normal agent
streams) already uses, keyed on the update's `parentAgentId`.

`archive-consistency` is baked into `session.js` and
`session/agent-updates/agent-updates-service.js`. Upstream 0.8.0 can archive a
workspace record while leaving its agent record unarchived; the default agent
snapshot used to expose that orphan as live, and a later live/stored
`agent_update` could insert it again. The patch applies the same workspace
archive predicate to the snapshot and subscription boundaries: default updates
emit `remove`, while `includeArchived=true` keeps the diagnostic/recovery
`upsert`. Agents with no or unknown workspace identity keep their existing
snapshot behavior. Its behavior test covers both paths, idempotence, mixed-state
refusal, and anchor drift.

`workspace-remove-delivery` is baked into `session.js` (2026-10-02). A
changes-only resubscription omits unchanged cached workspaces from the session's
last-emitted map; their first archive removal must still be delivered. The
behavior test executes the Session delivery branch and checks the real
DirectorySyncService changes-only bootstrap and removal sequence. Baking this
patch and updating both checksum manifests preserves installer idempotency;
leaving it as an install-time mutation would reinstall and restart on every run.

`directory-sync-snapshot` is baked into `server/directory-sync/index.js`
(2026-10-10). Workspace and agent directory reads ignore the cursor position and
return the full list, so a device whose cursor has passed an archive still drops
the archived row on its next connect. Only that tar member differs from the
previous server tarball; both checksum manifests are updated together, and
`INSTALLED_SHA256SUMS` gains the file.

`reconcile-archives-agents` is baked into `server/bootstrap.js` (2026-10-11).
The reconciliation that archives a workspace whose directory is missing now
archives that workspace's agents too. Only that tar member differs from the
previous server tarball; `INSTALLED_SHA256SUMS` gains the file.

`mcp-auth-off-argv` is baked into `server/agent/providers/claude/query.js` and
`server/agent/agent-projections.js` (2026-10-15). The daemon's Agent MCP bearer no
longer reaches claude's argv (the inline `--mcp-config` JSON goes to a 0600 file) or
stored agent records (the daemon's own `/mcp/agents` server is dropped from
`persistence.metadata.mcpServers`). Only those two tar members differ from the
previous server tarball; `query.js` was already pinned and `INSTALLED_SHA256SUMS`
gains `agent-projections.js`. There is no install-time step: both files are baked.

`orphan-process-guard` (claude) and `orphan-process-group` (claude-agent,
claude-query, codex-transport) are baked in for the same reason as
provider-subagent-stream-filter: both have their own runtime behaviour tests
(`orphan-process-guard.test.mjs`, `orphan-process-group.test.mjs`) and both
pass cleanly against the 0.8.0-derived candidates. codex is not covered any
more: re-checked against 0.8.0, upstream independently rewrote
`CodexAppServerSession`'s `connect()`/`close()` lifecycle (a `this.closed`
gate at three points, `connectionPromise` de-duplication so overlapping
callers cannot each spawn their own app-server, and identity-checked dispose
in every failure branch) — the same fix this patch makes for claude, already
shipped upstream. `orphan-process-group`'s `claude-agent` mode chains on
`orphan-process-guard`'s own output text, so the two ship together: baking
one without the other would leave `claude/agent.js` pinned at a state
`install.sh` never actually produces standalone. Baking both was also
required for a correctness reason, not just a tidiness one: before this,
`orphan-process-guard`/`orphan-process-group` used to drift-skip against
0.8.0 (never mutating `claude/agent.js`), so `INSTALLED_SHA256SUMS`
happened to still hold after a real install. Re-deriving them to actually
apply broke that — install-time-only patches that succeed keep mutating a
pinned file on every run, so the very next idempotency check saw a checksum
mismatch and reinstalled from scratch every time. Baking removes the
mutation from the post-install steady state and restores idempotency.

This adds `session.js` (depth4, provider-subagent-stream-filter,
archive-consistency),
`session/agent-updates/agent-updates-service.js` (archive-consistency),
`agent/providers/claude/agent.js` (image persistence, orphan-process-guard,
orphan-process-group), `agent/providers/claude/query.js`
(orphan-process-group), `agent/providers/claude/model-manifest.js` (model
prune), `agent/providers/opencode-agent.js` (opencode grok defaults),
`agent/providers/codex/app-server-transport.js` (orphan-process-group), and
`agent/providers/codex-feature-definitions.js` plus
`agent/providers/codex-app-server-agent.js` (codex-model-roster), and
`schedule/service.js` (schedule-busy-pending-delivery, schedule-stale-due-run)
to the overlay.
Apart from these overlays, modification notices and added package license
texts, the package payloads match the original registry archives, whose hashes
are in `source/ORIGINAL_SHA256SUMS`. The unused intermediate
`schedule/service.js.candidate.mjs` is omitted. The web UI code remains the
original registry build; its dependency notices accompany the server archive. `INSTALLED_SHA256SUMS` pins every overlaid file.

`schedule-busy-pending-delivery` / `schedule-pending-delivery-schema` is
baked in too, for the same reason as `provider-subagent-stream-filter` and
`orphan-process-guard`/`-group` above. Its own runtime behaviour test
(steps ① busy defers without a failed run, ② repeat busy coalesces, ③ idle
delivers exactly once, ④ no duplicate delivery) drives the patched
`ScheduleService` with a stub `agentManager` — the stub was stale for
0.8.0: `executeSchedule`'s agent-target path now goes through the shared
`startAgentRun()` helper (`ensureAgentLoaded` → `tryRunOutOfBand` →
`steerOrReplaceActiveRun` → `startOrReplaceRun`, which is where busy
actually forks into `replaceAgentRun()` vs `streamAgent()`) and
`waitForAgentEvent()`, not the simple `runAgent()` the 0.2.5-era stub
satisfied, so ③④ could not run to a real verdict. Fixed and re-verified
against the real 0.8.0 dist — all four steps pass — so both halves are now
baked in together (the schema half already was; leaving the service half
install-time-only once it started actually succeeding would have repeated
the same idempotency hazard `orphan-process-guard`/`-group` hit: a
non-baked patch that succeeds keeps mutating a pinned file on every run).

`schedule-stale-due-run` is baked in on top of it, and had to be: the tick it
anchors on is the one `schedule-busy-pending-delivery` writes, so leaving it
install-time-only would mutate a pinned `schedule/service.js` on every run and
reinstall the bundle from scratch each time — exactly the idempotency hazard
described above. It closes two defects that only show once the schedule
directory is large. The daemon's tick is `setInterval(..., 1000)`
fire-and-forget, so a slow tick is re-entered; `runSchedule` then receives a due
snapshot the on-disk record has already moved past, and 0.8.0's
`appendRunningRun` appends unconditionally — so a `maxRuns: 1` one-shot gets a
second run, which is what makes a delivery receipt read `unavailable`. Measured
on a pilot box 2026-09-23: 21 of 415 delivery schedules carried two succeeded
runs. The patch revalidates inside `store.update`'s atomic section (active,
`nextRunAt === scheduledFor`, not past expiry/maxRuns), returns null on refusal
and never calls the runner. It also moves the append inside `try`: 0.8.0 leaves
it outside, so a throwing append skips `finally` and strands the id in
`runningScheduleIds` forever. Second, `tick()` called `store.list()` twice per
second and `list()` reads and parses every JSON in the directory — 2,760 files
was ~5,520 reads/sec; the second call now reuses the first snapshot, which is
only safe *because* of the revalidation above, so the two ship as one patch.
Its behaviour test drives the patched service with stub seats and the
precursor's own test is re-run against the same candidate.

`schedule-pending-batch` is baked in on top of `schedule-stale-due-run`
(2026-10-03), for the same idempotency reason. session-delivery creates one
one-shot schedule per message, so a seat that stays busy collects several
pending schedules; delivering one holds the seat for the whole turn and the
others wait a turn each. Measured on a pilot box 2026-10-03: 15 deliveries queued
for one seat, delivery lag p50 of 1.3–2.3 h in the worst hours. The patch
claims every pending schedule of an idle seat and delivers them in one turn,
each schedule still getting exactly one run (the session-delivery receipt
contract). It also puts the pending bit back when a claimed schedule's append
is refused, instead of leaving it until the next cron hour. Only
`dist/server/server/schedule/service.js` changed in the tarball.

`claude-model-prune` was re-baked (2026-09-24) to add Opus 5.5
(`claude-opus-5-5`, released 2026-09-22) at the top of the Claude picker and relabel
Opus 5 as the previous release. Same reason as the other re-bakes: the sentinel is
unchanged, so only replacing the vendored bytes ships the new roster. Only
`model-manifest.js` differs from the previous server tarball; both checksum files
are updated.

`claude-model-prune` was re-baked again (2026-09-29) to add Sonnet 5.5
(`claude-sonnet-5-5`) to the picker. Its 1M context and `high` default effort
match Anthropic's model specification; Paseo's existing Claude thinking default
already selects `high`. Sonnet 5 is labelled as the previous release. The
server tarball and both checksum files are updated together.

`codex-model-roster` is baked into the two Codex provider modules. Paseo obtains
the picker from Codex app-server's live `model/list`, so the overlay keeps that
availability boundary and filters only the retired exact ID `gpt-5.5`. The Fast
capability table adds `gpt-6-sol` and `gpt-6-luna`; the rows therefore appear
only after the signed-in ChatGPT account receives them, but arrive with the Fast
control on the first eligible refresh. Both modules and their behavior test are
applied as one unit, and both installed files are pinned below.

`workspace-git-watch-recovery` is baked into `server/workspace-git-service.js`. It is a
backport of upstream getpaseo/paseo `8ffe7c75d` ("Keep the daemon responsive when file
watching fails", #3056), working-tree path only, with upstream's constant names so the
overlay can simply be dropped when the pin moves past it. In 0.8.0 a working-tree watcher
that misses its 10s subscribe deadline falls back to a 5s poll (four git commands per
tick) and gives up recovering after three attempts, so one slow moment — typically a
daemon boot with many workspaces — becomes polling that lasts until the next restart.
Measured on a pilot box 2026-09-25: that fallback was the top git refresh source (610 per
30s window) and the git queue peaked at 552 waiting. The patch keeps retrying (backoff
capped at 300s) and doubles a quiet poll's interval up to 60s, returning to 5s as soon as
a refresh changes the snapshot fingerprint. Its behaviour test drives the two patched
methods directly and reads the delays handed to `setTimeout`.

`workspace-git-identity` is baked into `server/workspace-git-service.js`
and `utils/checkout-git.js` (2026-10-05). `getCheckout` reads checkout identity
through the existing checkout-context inspection and main-repository resolution,
without computing dirty state, diffs, ahead/behind counts or forge status.
Automatic reconciliation avoids unused full Git-status work. The automatic
timing is now set by the background-frequency overlay described below.

The temporary automatic Git and reconciliation sampling tails were removed from
their service modules. The changed bundle digest takes the normal npm
reinstallation path; the real bundle-install test models the previous identity
and both old tails in a scratch prefix, verifies their replacement, then verifies
that the next install is idempotent. Both services and the identity helper are
checksum-pinned together.

`send-keep-pending-permissions` is baked into three tar members (2026-10-05):
`@getpaseo/protocol` `dist/messages.js`, `@getpaseo/server` `session.js` and
`@getpaseo/client` `dist/daemon-client.js`. All three are checksum-pinned, so an
install-time-only version would mutate pinned files on every run (the hazard above).
The installer still calls the patcher and sees `ALREADY`. Only those three members
changed; both checksum files were regenerated. The daemon restarts once because the
bundle identity changed.

The web UI patcher's 0.8.0 port updated all ten original anchors, including
persistence through `createValidatedPersistStorage`; its recorded validation
covered both `--subagent-stream` and `--browse` against the extracted bundle.
Known current shapes are listed in `KNOWN_BUNDLE_SHAPES` and the anchor manifest.
These UI edits are applied to `@getpaseo/server/dist/server/web-ui` at install
time; the npm archives retain the original UI build.

Earlier validation notes described warning-only UI call sites. In the current
installer, the server filter and `--subagent-stream` subscription edit are
validated as a pair. `browse-host/install.sh` invokes `--browse` directly and
propagates a failure to the Paseo installer.

The original pre-vendoring validation record reported 76 new tests (guard e2e, schedule-restore e2e,
store, service) plus a 359-test regression sweep of the directly-touched files
(agent-manager, agent-prompt, session, websocket-server notifications,
wire-compat) green under the upstream repo's own `vitest`, re-run independently
(not just taking the porting agent's word for it) against the final committed
state. `npm run build` for all seven packages (and their build-order
dependencies) succeeds clean. A real `npm i -g` of these seven tarballs into a
scratch prefix lands `@getpaseo/*` as siblings (no nesting under `cli`, the
layout `install.sh` depends on), `paseo --version` reports `0.8.0`, and a real
`bash apps/paseo/install.sh` run against that tree (the actual installer, not
a fixture) applies all ten baked-in patches (depth4, image-attachments-
persist, claude-model-prune, opencode-grok-defaults,
provider-subagent-stream-filter, orphan-process-guard, orphan-process-group,
schedule-busy-pending-delivery, archive-consistency, codex-model-roster)
idempotently, restarts the
daemon, and installs successfully end to end.

## Latency fixes (2026-10-30)

These runtime fixes were carried from an earlier deployment on 2026-10-30.
That import changed five server modules: the Git service, reconciliation
service, GitHub service, AgentStorage and AgentManager. AgentManager included
the idle-unload and timeline-replacement overlays already applied by the
installer. Public-release preparation later added license/source notices and
regenerated the manifests; the current archive identities are in `SHA256SUMS`,
and the complete shipped delta is in `source/distribution.patch`.

`agent-storage-load-once` keeps the initial shared disk load, but returns directly
on warm reads/writes instead of copying the full agent cache for each lookup.
Explicit list calls still enumerate current records.

`background-git-frequency` sets full reconciliation and background fetch to
30 minutes, root-event debounce to 30 seconds, watcher debounce to 10 seconds,
observation reensure to 5 minutes, and degraded polling to 1–5 minutes.
GitHub and generic forge PR polling use 2 minutes while pending, 10 minutes while
stable, and a 30-minute error cap. Initial and explicit forced requests remain
immediate. Automatic displays may be less fresh. Root events still reconcile all
workspaces, at most once per 30-second debounce window during sustained events.

These fixes are baked into the shipped archive and checked by the installer.
They do not depend on modifying an existing development box. Existing installs
receive them on a normal StationDesk app update; this source change itself does
not deploy or restart any running box.
