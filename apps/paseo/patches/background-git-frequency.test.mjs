// Exercise real copied module methods with fake timers; never starts a daemon or Git process.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { spawnSync } from "node:child_process";

const files = process.argv.slice(2);
if (files.length !== 3) throw new Error("usage: background-git-frequency.test.mjs <service candidate> <reconciliation candidate> <github candidate>");
const sources = files.map(file => fs.readFileSync(file, "utf8"));
function clock() {
    let now = 0, next = 0;
    const timers = new Map();
    return {
        timers, now: () => now,
        setTimeout(fn, delay) { const id = ++next; timers.set(id, { fn, due: now + delay, delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        setInterval(fn, delay) { const id = ++next; timers.set(id, { fn, due: now + delay, delay, interval: true }); return id; },
        clearInterval(id) { timers.delete(id); },
        async advance(ms) {
            const until = now + ms;
            while (true) {
                const entry = [...timers].filter(([, t]) => t.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
                if (!entry) break;
                const [id, timer] = entry;
                now = timer.due;
                if (timer.interval) timer.due += timer.delay; else timers.delete(id);
                await timer.fn();
                for (let i = 0; i < 12; i++) await Promise.resolve();
            }
            now = until;
        },
    };
}
const logger = { child() { return this; }, warn() {}, debug() {} };
function load(source, name, timer) {
    const context = vm.createContext({ ...timer, console, Set, Map, Promise, queueMicrotask, resolve: value => value });
    vm.runInContext(source.replace(/^import[\s\S]*?;\n/gm, "").replace(/\bexport /g, "") + `\nglobalThis.Result = ${name};`, context);
    return context.Result;
}
const timer = clock();
const Reconciliation = load(sources[1], "WorkspaceReconciliationService", timer);
let full = 0, metadata = 0;
const reconciliation = new Reconciliation({ serverId: "test", projectRegistry: {}, workspaceRegistry: {}, logger, clock: timer });
reconciliation.syncProjectRootWatches = async () => {};
reconciliation.runOnce = async () => { full++; return { changesApplied: [] }; };
reconciliation.reconcileGitMetadata = async () => { metadata++; return { changesApplied: [] }; };
await reconciliation.start();
await reconciliation.reconcileNow();
assert.equal(full, 1, "boot/manual reconciliation remains immediate");
await timer.advance(300000);
assert.equal(full, 1, "old five-minute rescan no longer runs");
await timer.advance(1500000);
assert.equal(full, 2, "full rescan runs after thirty minutes");
for (let second = 0; second < 90; second++) {
    reconciliation.scheduleObservedReconciliation();
    await timer.advance(1000);
}
assert.equal(metadata, 3, "sustained root events still fan out once every thirty seconds");
await reconciliation.reconcileNow();
assert.equal(full, 3, "manual reconciliation bypasses event debounce");
reconciliation.dispose();

const gitTimer = clock();
const Git = load(sources[0], "WorkspaceGitServiceImpl", gitTimer);
const service = Object.create(Git.prototype);
service.logger = logger;
const target = { cwd: "/fixture", closed: false, pendingDebounceRequest: null, debounceTimer: null };
service.workspaceTargets = new Map([[target.cwd, target]]);
service.mergeRefreshRequests = (_pending, request) => request;
let snapshots = 0;
service.refreshWorkspaceTarget = async () => { snapshots++; };
service.scheduleWorkspaceRefresh(target);
await gitTimer.advance(9999);
assert.equal(snapshots, 0);
await gitTimer.advance(1);
assert.equal(snapshots, 1, "watch refresh uses ten-second debounce");
service.isActiveObservedWorkspaceTarget = () => true;
service.deps = { getWorkspaceGitSelfHealPhaseMs: () => 0, now: () => new Date(gitTimer.now()) };
service.scheduleWorkspaceObservationSetup = () => {};
service.updateForgePrStatusPollForTarget = () => {};
service.startWorkspaceSubscriptionTimers(target);
await gitTimer.advance(0);
assert.ok([...gitTimer.timers.values()].some(t => t.delay === 300000), "observation reensure uses five minutes");
gitTimer.timers.clear();
// Explicit forced requests retain the original bypass of the unchanged two-second throttle.
service.runWorkspaceRefreshLoop = async () => { snapshots++; return { fresh: true }; };
target.refreshState = { status: "idle" };
target.latestSnapshot = { cached: true };
target.lastShellOutAtMs = gitTimer.now();
assert.equal(service.shouldThrottleNonForcedRefresh(target), true);
const explicit = service.normalizeRefreshRequest({ force: true, reason: "manual" }, "getSnapshot", true);
assert.equal((await service.requestWorkspaceSnapshot(target, explicit)).fresh, true);
await gitTimer.advance(2000);
assert.equal(service.shouldThrottleNonForcedRefresh(target), false, "internal min gap remains two seconds");

gitTimer.timers.clear();
service.repoTargets = new Map();
service.startRepoMetadataObservation = async () => {};
service.deps.hasOriginRemote = async () => true;
let fetches = 0;
service.runRepoFetch = async () => { fetches++; };
target.repoGitRoot = "/repo";
await service.ensureRepoTarget(target);
assert.equal(fetches, 1, "first repository fetch remains immediate");
await gitTimer.advance(180000);
assert.equal(fetches, 1, "old three-minute fetch no longer runs");
await gitTimer.advance(1620000);
assert.equal(fetches, 2, "background fetch runs every thirty minutes");
gitTimer.timers.clear();
const fallback = { cwd: "/fixture", repoRoot: "/fixture", closed: false, subscription: null, workspaceKeys: new Set([target.cwd]) };
service.workingTreeWatchTargets = new Map([[fallback.cwd, fallback]]);
service.notifyWorkingTreeConsumers = () => {};
service.refreshWorkspaceTarget = async () => {};
service.startWorkingTreeWatchFallback(fallback, "test");
assert.equal([...gitTimer.timers.values()][0].delay, 60000);
await gitTimer.advance(60000);
assert.equal([...gitTimer.timers.values()][0].delay, 120000);
await gitTimer.advance(120000);
assert.equal([...gitTimer.timers.values()][0].delay, 240000);
await gitTimer.advance(240000);
assert.equal([...gitTimer.timers.values()][0].delay, 300000);
service.refreshWorkspaceTarget = async () => { target.latestFingerprint = "changed"; };
await gitTimer.advance(300000);
assert.equal([...gitTimer.timers.values()][0].delay, 60000, "changed fallback returns to one minute");
gitTimer.timers.clear();
const repo = { repoGitRoot: "/repo", workspaceKeys: new Set(), closed: false, subscription: null };
service.repoTargets = new Map([[repo.repoGitRoot, repo]]);
service.startRepoMetadataFallback(repo);
assert.equal([...gitTimer.timers.values()][0].delay, 60000, "metadata fallback shares one-minute base");

function topFunction(source, name) {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1);
    return source.slice(start, source.indexOf("\n}", start) + 2);
}
for (const [source, prefix, fn, extra] of [
    [sources[0], "FORGE_PR_STATUS_POLL_", "computeGenericForgeNextInterval", ""],
    [sources[2], "GITHUB_POLL_", "computeGithubNextInterval", topFunction(sources[2], "isGitHubStatusPending")],
]) {
    const declarations = source.split("\n").filter(line => line.includes(`const ${prefix}`)).join("\n").replace(/export /g, "");
    const calculate = vm.runInNewContext(`${declarations}\n${extra}\n${topFunction(source, fn)}\n${fn}`);
    assert.equal(calculate({ checksStatus: "pending" }, 0), 120000);
    assert.equal(calculate(null, 0), 600000);
    assert.equal(calculate(null, 9), 1800000);
}
// Candidate protocol: originals remain untouched; applied copies are idempotent; drift yields no output.
const patcher = path.join(import.meta.dirname, "background-git-frequency.mjs");
assert.equal(spawnSync(process.execPath, [patcher, ...files], { encoding: "utf8" }).status, 10);
const originals = files.map(file => file + ".original");
const baselineValues = [
    { WORKSPACE_GIT_WATCH_DEBOUNCE_MS: "1000", BACKGROUND_GIT_FETCH_INTERVAL_MS: "180000", WORKSPACE_GIT_OBSERVATION_REENSURE_INTERVAL_MS: "60000", FORGE_PR_STATUS_POLL_FAST_INTERVAL_MS: "20000", FORGE_PR_STATUS_POLL_SLOW_INTERVAL_MS: "120000", FORGE_PR_STATUS_POLL_ERROR_BACKOFF_CAP_MS: "300000", DEGRADED_GIT_POLL_INTERVAL_MS: "5000", DEGRADED_GIT_POLL_MAX_INTERVAL_MS: "60000" },
    { DEFAULT_RESCAN_INTERVAL_MS: "5 * 60000", DEFAULT_DEBOUNCE_MS: "100" },
    { GITHUB_POLL_FAST_INTERVAL_MS: "20000", GITHUB_POLL_SLOW_INTERVAL_MS: "120000", GITHUB_POLL_ERROR_BACKOFF_CAP_MS: "300000" },
];
try {
    for (let index = 0; index < files.length; index++) {
        let baseline = sources[index].replace(/^.*\[paseo-background-git-frequency\].*\n/m, "");
        for (const [name, value] of Object.entries(baselineValues[index])) {
            baseline = baseline.replace(new RegExp(`const ${name} = [^;]+;`), `const ${name} = ${value};`);
        }
        fs.writeFileSync(originals[index], baseline);
    }
    const snapshots = originals.map(file => fs.readFileSync(file, "utf8"));
    assert.equal(spawnSync(process.execPath, [patcher, ...originals], { encoding: "utf8" }).status, 0, "unpatched baseline creates all candidates");
    originals.forEach((file, index) => {
        assert.equal(fs.readFileSync(file, "utf8"), snapshots[index], "patcher does not modify its input");
        assert.equal(fs.readFileSync(file + ".paseo-new.mjs", "utf8"), sources[index], "baseline converges to the tested policy");
    });
} finally {
    for (const file of originals) { fs.rmSync(file, { force: true }); fs.rmSync(file + ".paseo-new.mjs", { force: true }); }
}
const drift = files.map(file => file + ".drift");
try {
    drift.forEach((file, index) => fs.writeFileSync(file, sources[index]));
    fs.writeFileSync(drift[2], sources[2].replace("GITHUB_POLL_FAST_INTERVAL_MS = 120000", "GITHUB_POLL_FAST_INTERVAL_MS = 123"));
    assert.equal(spawnSync(process.execPath, [patcher, ...drift], { encoding: "utf8" }).status, 20);
    for (const file of drift) assert.equal(fs.existsSync(file + ".paseo-new.mjs"), false);
} finally { for (const file of drift) fs.rmSync(file, { force: true }); }
console.log("PASS background frequency: rescan, sustained events, watch debounce, reensure, explicit force, both fallback paths, both PR pollers, idempotence/drift");
