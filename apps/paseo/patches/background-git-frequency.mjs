// Lower automatic refresh frequency without changing explicit requests or admission policy.
// argv: workspace-git-service.js workspace-reconciliation-service.js github-service.js
// 0: .paseo-new.mjs candidates; 10: already applied; 20: upstream drift (no candidates).
import fs from "node:fs";

const files = process.argv.slice(2);
if (files.length !== 3) {
    console.error("usage: background-git-frequency.mjs <workspace-git-service.js> <workspace-reconciliation-service.js> <github-service.js>");
    process.exit(1);
}
const marker = "[paseo-background-git-frequency]";
const replacements = [
    [
        ["WORKSPACE_GIT_WATCH_DEBOUNCE_MS", "1000", "10000"],
        ["BACKGROUND_GIT_FETCH_INTERVAL_MS", "180000", "1800000"],
        ["WORKSPACE_GIT_OBSERVATION_REENSURE_INTERVAL_MS", "60000", "300000"],
        ["FORGE_PR_STATUS_POLL_FAST_INTERVAL_MS", "20000", "120000"],
        ["FORGE_PR_STATUS_POLL_SLOW_INTERVAL_MS", "120000", "600000"],
        ["FORGE_PR_STATUS_POLL_ERROR_BACKOFF_CAP_MS", "300000", "1800000"],
        ["DEGRADED_GIT_POLL_INTERVAL_MS", "5000", "60000"],
        ["DEGRADED_GIT_POLL_MAX_INTERVAL_MS", "60000", "300000"],
    ],
    [
        ["DEFAULT_RESCAN_INTERVAL_MS", "5 * 60000", "30 * 60000"],
        ["DEFAULT_DEBOUNCE_MS", "100", "30000"],
    ],
    [
        ["GITHUB_POLL_FAST_INTERVAL_MS", "20000", "120000"],
        ["GITHUB_POLL_SLOW_INTERVAL_MS", "120000", "600000"],
        ["GITHUB_POLL_ERROR_BACKOFF_CAP_MS", "300000", "1800000"],
    ],
];
const sources = files.map(file => fs.readFileSync(file, "utf8"));
const candidates = [];
for (let index = 0; index < files.length; index++) {
    let source = sources[index];
    // Validate applied values too: a stale marker must not conceal partial or conflicting changes.
    const applied = source.includes(marker);
    for (const [name, before, after] of replacements[index]) {
        const anchor = `const ${name} = ${applied ? after : before};`;
        if (source.split(anchor).length !== 2) {
            console.log(`SKIP:NO_UNIQUE_ANCHOR:${name}`);
            process.exit(20);
        }
        source = source.replace(anchor, `const ${name} = ${after};`);
    }
    candidates.push(applied ? source : `// ${marker} Automatic refresh intervals; explicit requests remain immediate.\n${source}`);
}
if (sources.every(source => source.includes(marker))) {
    console.log("ALREADY");
    process.exit(10);
}
for (let index = 0; index < files.length; index++) {
    fs.writeFileSync(files[index] + ".paseo-new.mjs", candidates[index]);
}
console.log("PATCHED");
