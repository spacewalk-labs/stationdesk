// [paseo-agent-storage-load-once] Exercise the real AgentStorage class source without starting
// Paseo or importing the installed package. Input may be an upstream source, a patch candidate,
// or an already-baked module extracted from the public bundle.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path, { dirname, join } from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const input = process.argv[2];
if (!input) {
    console.error("usage: agent-storage-load-once.test.mjs <agent-storage.js|candidate|baked module>");
    process.exit(1);
}

const patcher = join(dirname(fileURLToPath(import.meta.url)), "agent-storage-load-once.mjs");
const temp = mkdtempSync(join(tmpdir(), "paseo-agent-storage-load-once-"));
const sentinel = "[paseo-agent-storage-load-once]";

function runPatcher(target) {
    return spawnSync(process.execPath, [patcher, target], { encoding: "utf8" });
}

function extractAgentStorage(source) {
    const start = source.indexOf("export class AgentStorage {");
    assert.notEqual(start, -1, "AgentStorage class was not found in input");
    const end = source.indexOf("\n}\nfunction projectDirNameFromCwd", start);
    assert.notEqual(end, -1, "AgentStorage class end anchor was not found");
    return source.slice(start, end + 2).replace(/^export class/m, "class");
}

function loadClass(source) {
    const classSource = extractAgentStorage(source);
    const context = {
        path,
        fs: { unlink: async () => {} },
        writeJsonFileAtomic: async () => {},
        projectDirNameFromCwd: () => "fixture",
    };
    return vm.runInNewContext(`${classSource}\nAgentStorage;`, context);
}

// AgentStorage's exercised methods only need path joining and an atomic writer. Disk scan is
// replaced below to count the one initial scan deterministically.

function makeRecord(id, cwd) {
    const now = new Date().toISOString();
    return { id, provider: "claude", cwd, createdAt: now, updatedAt: now, labels: {}, lastStatus: "idle" };
}

async function checkBehavior(source) {
    const AgentStorage = loadClass(source);
    const logger = { child: () => logger, error() {}, warn() {} };
    const storage = new AgentStorage(join(temp, "records"), logger);
    let scans = 0;
    let releaseScan;
    const scanGate = new Promise((resolveScan) => { releaseScan = resolveScan; });
    storage.scanDisk = async () => {
        scans += 1;
        await scanGate;
        return [];
    };

    const first = makeRecord("agent-one", "/workspace/one");
    const loadA = storage.load();
    const loadB = storage.load();
    const getDuringLoad = storage.get("missing");
    const upsertDuringLoad = storage.upsert(first);
    await Promise.resolve();
    assert.equal(scans, 1, "concurrent first callers started more than one disk scan");
    releaseScan();
    const [resultA, resultB, missing] = await Promise.all([loadA, loadB, getDuringLoad, upsertDuringLoad]);
    assert.ok(Array.isArray(resultA), "first load stopped returning the initial records array");
    assert.equal(resultA, resultB, "concurrent first loads did not share the doLoad result");
    assert.equal(missing, null);
    assert.equal(scans, 1, "initial load scanned disk more than once");
    assert.equal(await storage.load(), undefined, "warm load should only signal completion");

    // Warm reads and writes await load() but must not enumerate the cache. Explicit list() still
    // enumerates and sees additions made after the initial disk snapshot.
    let enumerations = 0;
    const mapValues = storage.cache.values;
    storage.cache.values = function* () {
        enumerations += 1;
        yield* mapValues.call(this);
    };
    assert.equal(await storage.get(first.id), first);
    assert.equal(enumerations, 0, "get enumerated the full cache after load completed");
    const second = makeRecord("agent-two", "/workspace/two");
    await storage.upsert(second);
    assert.equal(enumerations, 0, "upsert enumerated the full cache after load completed");
    const listed = await storage.list();
    assert.deepEqual(new Set(listed.map((record) => record.id)), new Set([first.id, second.id]));
    assert.equal(enumerations, 1, "list() did not own exactly one explicit cache enumeration");
}

try {
    const inputSource = readFileSync(input, "utf8");
    let patchedSource = inputSource;
    const suppliedCandidate = input.endsWith(".paseo-new.mjs");

    // On a source or candidate input, prove the patcher writes the same candidate from a copy.
    // A baked bundle already contains the sentinel and is exercised directly below.
    if (suppliedCandidate) {
        const sourceInput = input.slice(0, -".paseo-new.mjs".length);
        const source = readFileSync(sourceInput, "utf8");
        const fixture = join(temp, "agent-storage.js");
        writeFileSync(fixture, source);
        const applied = runPatcher(fixture);
        assert.equal(applied.status, 0, applied.stdout + applied.stderr);
        const freshCandidate = `${fixture}.paseo-new.mjs`;
        patchedSource = readFileSync(freshCandidate, "utf8");
        assert.equal(patchedSource, inputSource, "candidate differs from fresh patch output");
        assert.equal(spawnSync(process.execPath, ["--check", freshCandidate], { encoding: "utf8" }).status, 0);

    }
    else if (inputSource.includes(sentinel)) {
        patchedSource = inputSource;
    }
    else {
        const fixture = join(temp, "agent-storage.js");
        writeFileSync(fixture, inputSource);
        const applied = runPatcher(fixture);
        assert.equal(applied.status, 0, applied.stdout + applied.stderr);
        patchedSource = readFileSync(`${fixture}.paseo-new.mjs`, "utf8");
        assert.equal(readFileSync(fixture, "utf8"), inputSource, "patcher changed its input");
        assert.equal(spawnSync(process.execPath, ["--check", `${fixture}.paseo-new.mjs`], { encoding: "utf8" }).status, 0);
    }

    const already = join(temp, "already.js");
    writeFileSync(already, patchedSource);
    const repeated = runPatcher(already);
    assert.equal(repeated.status, 10, repeated.stdout + repeated.stderr);

    const drift = join(temp, "drift.js");
    writeFileSync(drift, "export class AgentStorage {}\n");
    const skipped = runPatcher(drift);
    assert.equal(skipped.status, 20, skipped.stdout + skipped.stderr);
    assert.equal(existsSync(`${drift}.paseo-new.mjs`), false, "drift wrote a candidate");

    await checkBehavior(patchedSource);
    console.log("PASS: one initial scan; warm get/upsert do not enumerate; list sees later records");
}
finally {
    rmSync(temp, { recursive: true, force: true });
}
