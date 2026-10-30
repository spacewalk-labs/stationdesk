// SPDX-License-Identifier: AGPL-3.0-only
// [paseo-agent-storage-load-once] Do not copy the whole cache on warm load().
//
// AgentStorage callers await load() for initialization; they read the record they need from the
// cache, or explicitly call list(). Returning Array.from(cache.values()) here made every warm
// get/upsert/lookup allocate a second array of all persisted agents. listAgentPayloads reaches
// get repeatedly, so this was repeated once per live agent for every roster refresh.
//
// Contract: argv[2] = server agent/agent-storage.js.
//   exit  0 = candidate written to <target>.paseo-new.mjs
//   exit 10 = already patched (sentinel)
//   exit 20 = the expected upstream anchor is missing or duplicated; writes nothing
//   exit  1 = usage / IO / patch logic error
import fs from "node:fs";

const target = process.argv[2];
if (!target) {
    console.error("usage: agent-storage-load-once.mjs <server agent/agent-storage.js>");
    process.exit(1);
}

const SENTINEL = "[paseo-agent-storage-load-once]";
let source;
try {
    source = fs.readFileSync(target, "utf8");
}
catch (error) {
    console.error(`read failed (${target}): ${String(error)}`);
    process.exit(1);
}
if (source.includes(SENTINEL)) {
    console.log("ALREADY");
    process.exit(10);
}

const anchor = [
    "        if (this.loaded) {",
    "            return Array.from(this.cache.values());",
    "        }",
].join("\n");
const replacement = [
    "        if (this.loaded) {",
    `            // ${SENTINEL} Warm callers only need loading to be complete; list() owns enumeration.`,
    "            return;",
    "        }",
].join("\n");

const occurrences = source.split(anchor).length - 1;
if (occurrences !== 1) {
    console.error(`SKIP: loaded branch anchor missing or duplicated (count=${occurrences})`);
    process.exit(20);
}

const candidate = source.replace(anchor, replacement);
try {
    fs.writeFileSync(`${target}.paseo-new.mjs`, candidate);
}
catch (error) {
    console.error(`candidate write failed: ${String(error)}`);
    process.exit(1);
}
console.log("PATCHED");
process.exit(0);
