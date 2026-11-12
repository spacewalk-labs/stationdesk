// SPDX-License-Identifier: AGPL-3.0-only
// Detach emitted deltas from the assembler's cumulative text backing strings.
// Baked into the bundle; 0 = candidate, 10 = already applied, 20 = anchor drift.
import fs from "node:fs";
const target = process.argv[2];
if (!target) {
    console.error("usage: claude-stream-retention.mjs <claude/agent.js>");
    process.exit(1);
}
const marker = "[paseo-claude-stream-retention]";
let source = fs.readFileSync(target, "utf8");
if (source.includes(marker)) {
    console.log("ALREADY");
    process.exit(10);
}
const edits = [
    ['            items.push({ type: "assistant_message", text: nextAssistantText, messageId: state.id });',
        `            // ${marker} A slice otherwise retains every prior cumulative body.
            items.push({ type: "assistant_message", text: structuredClone(nextAssistantText), messageId: state.id });`],
    ['            items.push({ type: "reasoning", text: nextReasoningText });',
        '            items.push({ type: "reasoning", text: structuredClone(nextReasoningText) });'],
];
for (const [before, after] of edits) {
    if (source.split(before).length !== 2) {
        console.error(`SKIP: missing or duplicated anchor: ${before}`);
        process.exit(20);
    }
    source = source.replace(before, () => after);
}
fs.writeFileSync(`${target}.paseo-new.mjs`, source);
console.log("PATCHED");
