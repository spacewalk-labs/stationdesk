// SPDX-License-Identifier: AGPL-3.0-only
// Run against an extracted guarded-0.8.0 server, never an installed prefix.
import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { appendFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
const packageRoot = resolve(process.argv[2]);
const load = (file) => import(pathToFileURL(join(packageRoot, "dist/server/server/agent", file)));
const { AgentStorage } = await load("agent-storage.js");
const { AgentManager } = await load("agent-manager.js");
const { createCrashRecovery } = await load("crash-recovery.js");
const { cancelAgentRunCommand } = await load("lifecycle-command.js");
const noop = () => {};
const logger = { child: () => logger, info: noop, warn: noop, error: (...args) => { if (process.env.PASEO_RECOVERY_TEST_DEBUG) console.error(...args); }, debug: noop, trace: noop };
const home = process.env.PASEO_RECOVERY_FIXTURE_HOME ?? await mkdtemp(join(tmpdir(), "paseo-crash-recovery-"));
let count = 0;
const id = (name) => "11111111-2222-4333-8444-" + createHash("sha256").update(name).digest("hex").slice(0, 12);
const record = (name, extra = {}) => ({ id: name.startsWith("11111111-") ? name : id(name), provider: "claude", cwd: home, labels: {},
    createdAt: "2026-11-06T00:00:00.000Z", updatedAt: "2026-11-06T00:01:00.000Z",
    lastUserMessageAt: "2026-11-06T00:01:00.000Z", lastStatus: "running", restartRecoveryPermissionPending: false,
    persistence: { provider: "claude", sessionId: "existing-session-" + name }, ...extra });
function live(r) {
    return { ...r, lifecycle: "idle", config: { provider: "claude", cwd: home },
        createdAt: new Date(r.createdAt), updatedAt: new Date(r.updatedAt),
        lastUserMessageAt: r.lastUserMessageAt ? new Date(r.lastUserMessageAt) : null,
        pendingPermissions: new Map(), bufferedPermissionResolutions: new Map(), inFlightPermissionResponses: new Set(),
        activeForegroundTurnId: null, activeTurnId: null, activeTurnStartedAt: null,
        foregroundTurnWaiters: new Set(), finalizedForegroundTurnIds: new Set(),
        session: { describePersistence: () => r.persistence }, attention: { requiresAttention: false } };
}
async function setup(label, records) {
    const root = join(home, label);
    let storage = new AgentStorage(join(root, "agents"), logger);
    await storage.initialize();
    if (records) for (const r of records) await storage.upsert(r);
    // Reopen from disk so schema loss cannot masquerade as recovery success.
    storage = new AgentStorage(join(root, "agents"), logger);
    await storage.initialize();
    const manager = new AgentManager({ logger, registry: storage, idleAgentUnloadMinutes: 0 });
    const sends = [];
    for (const r of await storage.list()) manager.agents.set(r.id, live(r));
    manager.streamAgent = async function* (id, prompt, options) {
        sends.push({ id, prompt, options });
        appendFileSync(join(root, "provider-calls.jsonl"), JSON.stringify({ id, messageId: options.clientMessageId }) + "\n");
        manager.agents.get(id).lifecycle = "running";
        yield { type: "fixture_provider_started" };
    };
    const recovery = createCrashRecovery({ paseoHome: root, storage, agentManager: manager,
        workspaceRegistry: { get: async (id) => ({ archivedAt: id === "archived" ? "yes" : null }) }, logger });
    return { root, storage, manager, sends, recovery };
}
async function run(name, fn) { await fn(); console.log("PASS " + name); count++; }
try {
    if (process.argv[3] === "--crash-phase") {
        const phase = process.argv[4];
        const s = await setup("worker-" + phase, [record("worker")]);
        await s.recovery.prepareStartup();
        if (phase === "intent") process.kill(process.pid, "SIGKILL");
        if (phase === "pending") s.manager.waitForAgentRunStart = async () => process.kill(process.pid, "SIGKILL");
        await s.recovery.resumePending();
        process.kill(process.pid, "SIGKILL");
    }
    await run("actual worker death preserves intent, uncertain send, and accepted running obligation", async () => {
        for (const phase of ["intent", "pending", "accepted"]) {
            const child = spawnSync(process.execPath, [process.argv[1], packageRoot, "--crash-phase", phase], {
                env: { ...process.env, PASEO_RECOVERY_FIXTURE_HOME: home }, encoding: "utf8", timeout: 30000 });
            assert.equal(child.signal, "SIGKILL", child.stderr);
            const next = await setup("worker-" + phase);
            await next.recovery.prepareStartup(); await next.recovery.resumePending();
            assert.equal(next.sends.length, phase === "pending" ? 0 : 1);
            const effects = (await readFile(join(next.root, "provider-calls.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
            assert.equal(effects.length, phase === "accepted" ? 2 : 1);
            if (phase === "accepted") assert.notEqual(effects[0].messageId, effects[1].messageId);
            if (phase === "pending") assert.match((await next.storage.get(id("worker"))).lastError, /outcome_unknown/);
        }
    });
    await run("interrupted five seats restore existing conversations once; no poller", async () => {
        const s = await setup("five", Array.from({ length: 5 }, (_, i) => record("seat-" + i)));
        await s.recovery.prepareStartup();
        for (const r of await s.storage.list()) assert.ok(r.startupResume);
        await s.recovery.resumePending();
        assert.equal(s.sends.length, 5);
        assert.equal(new Set(s.sends.map(x => x.id)).size, 5);
        for (const send of s.sends) {
            assert.match(send.prompt, /^<paseo-system>/);
            assert.match(send.prompt, /never repeat an uncertain write/);
            assert.match(send.options.clientMessageId, /^paseo-restart-/);
            assert.equal((await s.storage.get(send.id)).startupResume, undefined);
        }
        await s.recovery.resumePending();
        assert.equal(s.sends.length, 5);
    });
    await run("idle, closed, Stop, permission, archived and owned work stay stopped", async () => {
        const records = [record("idle", { lastStatus: "idle" }), record("closed", { lastStatus: "closed" }),
            record("stop", { restartRecoveryStopped: true }),
            record("permission", { attentionReason: "permission", requiresAttention: true }),
            record("archive", { archivedAt: "yes" }), record("workspace", { workspaceId: "archived" }),
            record("internal", { internal: true }), record("owned", { owner: { kind: "daemon", daemonId: "scheduled", executionId: "existing" } }), record("missing-session", { persistence: null })];
        // Owner schema is strict; use the real daemon-owned shape.
        const s = await setup("excluded", records);
        await s.recovery.prepareStartup(); await s.recovery.resumePending();
        assert.equal(s.sends.length, 0);
    });
    await run("markerless legacy running is unknown; historical idle never becomes a recovery task", async () => {
        const s = await setup("legacy", [record("legacy-running", { restartRecoveryPermissionPending: undefined }),
            record("legacy-idle", { lastStatus: "idle", restartRecoveryPermissionPending: undefined })]);
        await s.recovery.prepareStartup(); await s.recovery.resumePending();
        assert.equal(s.sends.length, 0);
        assert.equal((await s.storage.get(id("legacy-running"))).lastStatus, "closed");
        assert.equal((await s.storage.get(id("legacy-running"))).startupResume, undefined);
        assert.equal((await s.storage.get(id("legacy-idle"))).lastStatus, "idle");
    });
    await run("intent survives disk restart and snapshot projection before dispatch", async () => {
        const first = await setup(id("intent"), [record(id("intent"))]);
        await first.recovery.prepareStartup();
        const intent = (await first.storage.get(id("intent"))).startupResume;
        await first.storage.applySnapshot(first.manager.getAgent(id("intent")));
        assert.deepEqual((await first.storage.get(id("intent"))).startupResume, intent);
        const second = await setup(id("intent"));
        assert.deepEqual((await second.storage.get(id("intent"))).startupResume, intent);
        await second.recovery.prepareStartup(); await second.recovery.resumePending();
        assert.equal(second.sends.length, 1);
        assert.equal(second.sends[0].options.clientMessageId, intent.messageId);
    });
    await run("explicit Stop between capture and dispatch is durable even when idle", async () => {
        const s = await setup(id("stop-race"), [record(id("stop-race"))]);
        await s.recovery.prepareStartup();
        await cancelAgentRunCommand({ agentManager: s.manager, logger }, id("stop-race"));
        await s.recovery.resumePending(); assert.equal(s.sends.length, 0);
        const next = await setup(id("stop-race"));
        await next.recovery.prepareStartup(); await next.recovery.resumePending();
        assert.equal(next.sends.length, 0);
        assert.equal((await next.storage.get(id("stop-race"))).restartRecoveryStopped, true);
    });
    await run("Stop persistence and recovery guard share the existing lifecycle lane", async () => {
        const s = await setup("stop-lane", [record("stop-lane")]);
        const agentId = id("stop-lane");
        await s.recovery.prepareStartup();
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const write = s.storage.queueRecordMutation.bind(s.storage);
        s.storage.queueRecordMutation = async (...args) => { await gate; return write(...args); };
        const stopping = cancelAgentRunCommand({ agentManager: s.manager, logger }, agentId);
        const resuming = s.recovery.resumePending();
        let barrierReturned = false;
        const barrier = s.manager.waitForAgentClose(agentId).then(() => { barrierReturned = true; });
        await new Promise(resolve => setImmediate(resolve));
        try {
            assert.equal(barrierReturned, false);
            assert.equal(s.sends.length, 0);
        } finally { release(); }
        await stopping; await resuming; await barrier;
        assert.equal(s.sends.length, 0);
        assert.equal((await s.storage.get(agentId)).restartRecoveryStopped, true);
    });
    await run("permission is durable before RPC-visible pending state", async () => {
        const s = await setup("permission-boundary", [record("permission-boundary")]);
        const agentId = id("permission-boundary");
        const agent = s.manager.agents.get(agentId);
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const write = s.storage.queueRecordMutation.bind(s.storage);
        s.storage.queueRecordMutation = async (...args) => { await gate; return write(...args); };
        const requested = s.manager.onStreamPermissionRequested(agent, { request: { id: "approval" } });
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(agent.pendingPermissions.size, 0);
        release(); await requested;
        assert.equal(agent.pendingPermissions.size, 1);
        const disk = new AgentStorage(join(s.root, "agents"), logger); await disk.initialize();
        assert.equal((await disk.get(agentId)).restartRecoveryPermissionPending, true);
    });
    await run("permission Map is durable even when legacy attentionReason is empty", async () => {
        const s = await setup("durable-permission", [record("durable-permission")]);
        const agentId = id("durable-permission");
        const agent = s.manager.agents.get(agentId);
        agent.lifecycle = "running";
        agent.pendingPermissions.set("approval", {});
        await s.storage.applySnapshot(agent);
        const next = await setup("durable-permission");
        assert.equal((await next.storage.get(agentId)).restartRecoveryPermissionPending, true);
        assert.notEqual((await next.storage.get(agentId)).attentionReason, "permission");
        await next.recovery.prepareStartup(); await next.recovery.resumePending();
        assert.equal(next.sends.length, 0);
    });
    await run("resolved permission clears the marker; refused cancellation still keeps explicit Stop", async () => {
        const s = await setup("resolved-permission", [record("resolved-permission")]);
        const agentId = id("resolved-permission");
        const agent = s.manager.agents.get(agentId);
        agent.lifecycle = "running";
        agent.pendingPermissions.set("approval", {});
        await s.storage.applySnapshot(agent);
        assert.equal((await s.storage.get(agentId)).restartRecoveryPermissionPending, true);
        agent.pendingPermissions.clear();
        await s.storage.applySnapshot(agent);
        assert.equal((await s.storage.get(agentId)).restartRecoveryPermissionPending, false);
        const next = await setup("resolved-permission");
        await next.recovery.prepareStartup(); await next.recovery.resumePending();
        assert.equal(next.sends.length, 1);
        await assert.rejects(cancelAgentRunCommand({ agentManager: next.manager, logger }, agentId), /not acknowledged/);
        next.manager.agents.get(agentId).pendingPermissions.clear();
        await next.storage.applySnapshot(next.manager.getAgent(agentId));
        const stopped = await setup("resolved-permission");
        await stopped.recovery.prepareStartup(); await stopped.recovery.resumePending();
        assert.equal(stopped.sends.length, 0);
        assert.equal((await stopped.storage.get(agentId)).restartRecoveryPermissionPending, false);
        assert.equal((await stopped.storage.get(agentId)).restartRecoveryStopped, true);
    });
    await run("late provider echo cannot clear Stop; a later normal send clears it durably", async () => {
        const s = await setup("late-echo", [record("late-echo")]);
        const agentId = id("late-echo");
        await cancelAgentRunCommand({ agentManager: s.manager, logger }, agentId);
        const agent = s.manager.agents.get(agentId);
        agent.lastUserMessageAt = new Date("2026-11-06T00:02:00.000Z");
        agent.lifecycle = "running";
        await s.storage.applySnapshot(agent);
        assert.equal((await s.storage.get(agentId)).restartRecoveryStopped, true);
        await s.recovery.prepareStartup(); await s.recovery.resumePending();
        assert.equal(s.sends.length, 0);
        agent.lifecycle = "idle";
        let started = false;
        agent.session.startTurn = async () => {
            assert.equal((await s.storage.get(agentId)).restartRecoveryStopped, undefined);
            started = true;
            throw Error("fixture stops after observing durable normal-send boundary");
        };
        const iterator = AgentManager.prototype.streamAgent.call(s.manager, agentId, "person: continue", { clientMessageId: "user-new" });
        await iterator.next().catch(() => {});
        assert.equal(started, true);
    });
    await run("live pending permission cannot be cleared by recovery", async () => {
        const s = await setup(id("permission-race"), [record(id("permission-race"))]);
        await s.recovery.prepareStartup();
        s.manager.agents.get(id("permission-race")).pendingPermissions.set("approval", {});
        await s.recovery.resumePending();
        assert.equal(s.sends.length, 0);
        assert.equal(s.manager.agents.get(id("permission-race")).pendingPermissions.size, 1);
        assert.ok((await s.storage.get(id("permission-race"))).startupResume);
    });
    await run("uncertain provider acceptance is retained and never replayed after restart", async () => {
        const first = await setup(id("uncertain"), [record(id("uncertain"))]);
        await first.recovery.prepareStartup();
        first.manager.waitForAgentRunStart = async () => { throw Error("fixture worker dies before acknowledgement"); };
        await first.recovery.resumePending(); assert.equal(first.sends.length, 1);
        assert.ok((await first.storage.get(id("uncertain"))).startupResume);
        const next = await setup(id("uncertain"));
        await next.recovery.prepareStartup(); await next.recovery.resumePending();
        assert.equal(next.sends.length, 0);
        assert.match((await next.storage.get(id("uncertain"))).lastError, /agent_request_outcome_unknown/);
        assert.ok((await next.storage.get(id("uncertain"))).startupResume);
    });
    await run("accepted running continuation gets a fresh identity after another crash", async () => {
        const first = await setup(id("again"), [record(id("again"))]);
        await first.recovery.prepareStartup(); await first.recovery.resumePending();
        const accepted = first.sends[0].options.clientMessageId;
        const next = await setup(id("again"));
        await next.recovery.prepareStartup(); await next.recovery.resumePending();
        assert.equal(next.sends.length, 1);
        assert.notEqual(next.sends[0].options.clientMessageId, accepted);
    });
    await run("one failed provider does not block other interrupted seats", async () => {
        const s = await setup("independent", [record(id("bad")), record(id("good"))]);
        s.manager.agents.delete(id("bad")); // no registered provider => loading fails before send is claimed
        await s.recovery.prepareStartup(); await s.recovery.resumePending();
        assert.deepEqual(s.sends.map(x => x.id), [id("good")]);
        assert.ok((await s.storage.get(id("bad"))).startupResume);
        assert.equal((await s.storage.get(id("bad"))).requiresAttention, true);
    });
    console.log(`PASS ${count} recovery scenarios (real 0.8 storage, guarded send, request journal)`);
} finally { await rm(home, { recursive: true, force: true }); }
