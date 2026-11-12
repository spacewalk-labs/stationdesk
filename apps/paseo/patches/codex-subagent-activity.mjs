// SPDX-License-Identifier: AGPL-3.0-only
// Backport the Codex parent-card portion of upstream 184ab66cdea7680018f06c490c3f056cc194f8a2.
// Child activity already streams to ProviderSubagentStore; retaining a fresh cumulative log
// in every parent row makes retained text quadratic in the number of streamed deltas.
// This is baked into the guarded bundle. No installer-time mutation is needed.
// 0 = candidate written, 10 = already applied, 20 = anchor drift (no write).
import fs from "node:fs";

const target = process.argv[2];
if (!target) {
    console.error("usage: codex-subagent-activity.mjs <codex-app-server-agent.js>");
    process.exit(1);
}
const marker = "[paseo-codex-subagent-activity]";
let source = fs.readFileSync(target, "utf8");
if (source.includes(marker)) {
    console.log("ALREADY");
    process.exit(10);
}
const edits = [
    ['import { curateAgentActivity } from "../activity-curator.js";',
        'import { isDeepStrictEqual } from "node:util";'],
    [`            state.toolCall = {
                ...state.toolCall,
                detail: {
                    ...state.toolCall.detail,
                    subAgentType: activityToolCall.detail.subAgentType,
                },
            };`,
        `            subAgentType = activityToolCall.detail.subAgentType;`],
    [`        if (activityToolCall?.detail.type === "sub_agent" &&`,
        `        let subAgentType;
        if (activityToolCall?.detail.type === "sub_agent" &&`],
    [`        this.emitSubAgentActivityUpdate(callId, nextStatus);`,
        `        this.emitSubAgentActivityUpdate(callId, nextStatus, { subAgentType });`],
    [`    getSubAgentChildTimeline(state) {
        return state.childItemOrder
            .map((itemId) => state.childItems.get(itemId))
            .filter((item) => Boolean(item));
    }
`, ""],
    [`        const childTimeline = this.getSubAgentChildTimeline(state);
        const log = childTimeline.length > 0
            ? curateAgentActivity(childTimeline, { labelAssistantMessages: true })
            : "";
`, ""],
    [`        for (const childThreadId of state.childThreadIds) {
            this.emitProviderSubagentUpsert(childThreadId, state, resolvedStatus);
        }
        const baseToolCall = {`, `        const baseToolCall = {`],
    [`        const baseToolCall = {
            ...state.toolCall,
            detail: {
                ...state.toolCall.detail,
                log,
            },
        };`,
        `        // ${marker} The child owns its activity; the parent card carries status and label.
        const baseToolCall = {
            ...state.toolCall,
            detail: {
                ...state.toolCall.detail,
                ...(options?.subAgentType ? { subAgentType: options.subAgentType } : {}),
                log: "",
            },
        };`],
    [`        state.toolCall = nextToolCall;
        if (state.parentCallId && state.parentCallId !== callId) {
            this.upsertSubAgentChildItem(state.parentCallId, state.callId, nextToolCall);
            this.emitSubAgentActivityUpdate(state.parentCallId);
            return;
        }`,
        `        const cardChanged = !isDeepStrictEqual(nextToolCall, state.toolCall);
        state.toolCall = nextToolCall;
        for (const childThreadId of state.childThreadIds) {
            this.emitProviderSubagentUpsert(childThreadId, state, resolvedStatus);
        }
        if (!cardChanged || (state.parentCallId && state.parentCallId !== callId)) {
            return;
        }`],
];
for (const [before, after] of edits) {
    if (source.split(before).length !== 2) {
        console.error(`SKIP: missing or duplicated anchor: ${before.split("\n")[0]}`);
        process.exit(20);
    }
    source = source.replace(before, () => after);
}
fs.writeFileSync(`${target}.paseo-new.mjs`, source);
console.log("PATCHED");
