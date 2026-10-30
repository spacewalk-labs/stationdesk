// [paseo-steer-forward] idempotent, all-or-nothing patcher.
//
//   node paseo-steer-forward.mjs <.../agent/provider-registry.js>
//
// Target: @getpaseo/server .../agent/provider-registry.js
//
// Problem: an automatic message (session-delivery, activeTurnBehavior "steer") to a seat that is
// running a tool kills that tool call. wrapSessionProvider copies the session methods one by one
// and leaves out `steerActiveTurn`, so AgentManager sees `agent.session.steerActiveTurn ===
// undefined`, takes the "unavailable" branch and replaces the running turn (interrupt). The seat
// reads the tool result "The user doesn't want to proceed… STOP" as a human refusal and stops
// (taskboard#616). With the method forwarded, the steer is accepted and the tool finishes.
// Upstream already forwards it (packages/server/src/server/agent/provider-registry.ts) — drop this
// patch when the daemon moves to a build that has the line.
//
// Contract: argv[2] = target file. One stdout line + an exit code.
//   exit 10 = already patched (sentinel) -> skip
//   exit 20 = anchor missing/duplicated (upstream drift) -> writes nothing
//   exit  0 = candidate written to <target>.paseo-new.mjs (install.sh runs node --check and the
//             behaviour test, then moves it)
//   exit  1 = usage / IO error
import fs from "node:fs";

const F = process.argv[2];
if (!F) {
    console.error("usage: paseo-steer-forward.mjs <provider-registry.js>");
    process.exit(1);
}

const SENTINEL = "[paseo-steer-forward]";
let src;
try { src = fs.readFileSync(F, "utf8"); }
catch (err) { console.error("read failed: " + String(err)); process.exit(1); }

if (src.includes(SENTINEL)) { console.log("ALREADY"); process.exit(10); }

const L = (...lines) => lines.join("\n");

// The closing `};` keeps the anchor inside wrapSessionProvider (wrapClientProvider ends differently).
const OLD = L(
    "        tryHandleOutOfBand: inner.tryHandleOutOfBand?.bind(inner),",
    "    };",
    "}",
);
const NEW = L(
    "        tryHandleOutOfBand: inner.tryHandleOutOfBand?.bind(inner),",
    `        // ${SENTINEL} Without this AgentManager sees no steering and replaces the running turn.`,
    "        steerActiveTurn: inner.steerActiveTurn?.bind(inner),",
    "    };",
    "}",
);

if (src.split(OLD).length - 1 !== 1) {
    console.error("SKIP: anchor missing or not unique (upstream drift?)");
    process.exit(20);
}
const out = src.replace(OLD, NEW);
if (!out.includes(SENTINEL)) { console.error("sentinel absent after patching -- logic error"); process.exit(1); }

try { fs.writeFileSync(F + ".paseo-new.mjs", out); }
catch (err) { console.error("tmp write failed: " + String(err)); process.exit(1); }
console.log("PATCHED");
process.exit(0);
