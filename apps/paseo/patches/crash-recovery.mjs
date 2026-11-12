// SPDX-License-Identifier: AGPL-3.0-only
// Patch the guarded-0.8.0 integration points; never modify input files.
import fs from "node:fs";
const [storage, manager, bootstrap, commands] = process.argv.slice(2);
if (!commands) throw new Error("usage: crash-recovery.mjs <agent-storage.js> <agent-manager.js> <bootstrap.js> <lifecycle-command.js>");
const marker = "[paseo-crash-recovery]";
const targets = [storage, manager, bootstrap, commands].map((path) => ({ path, source: fs.readFileSync(path, "utf8") }));
const patched = targets.filter(({ source }) => source.includes(marker)).length;
if (patched === targets.length) { console.log("ALREADY"); process.exit(10); }
if (patched) { console.error("SKIP: mixed recovery integration"); process.exit(20); }
const edits = [
    [
        ['    lastStatus: AgentStatusSchema.default("closed"),', `    // ${marker} Intent survives provider loading and uncertain delivery.\n    startupResume: z.object({ messageId: z.string(), prompt: z.string() }).optional(),\n    restartRecoveryStopped: z.boolean().optional(),\n    restartRecoveryPermissionPending: z.boolean().optional(),\n    lastStatus: AgentStatusSchema.default("closed"),`],
        ['            // Preserve soft-delete/archive status across snapshot flushes.', `            record.startupResume = existing?.startupResume;\n            record.restartRecoveryStopped = existing?.restartRecoveryStopped;\n            record.restartRecoveryPermissionPending = agent.pendingPermissions\n                ? agent.pendingPermissions.size > 0 : existing?.restartRecoveryPermissionPending;\n            // Preserve soft-delete/archive status across snapshot flushes.`],
    ],
    [
        ['    async cancelAgentRun(agentId) {', `    // ${marker} Only the explicit Stop command calls this; reload/replace keep their own lifecycle.\n    async recordRestartRecoveryStop(agentId) {\n        return this.runLifecycleMutation(agentId, async () => {\n            const agent = this.agents.get(agentId);\n            if (agent) this.touchUpdatedAt(agent);\n            if (this.registry && await this.registry.get(agentId)) await this.registry.queueRecordMutation(agentId, (record) => ({\n                ...record, startupResume: undefined,\n                restartRecoveryStopped: true,\n            }));\n        });\n    }\n    async cancelAgentRun(agentId) {`],
        ['            const agent = this.agents.get(agentId);\n            if (!agent || agent.lifecycle !== "idle" || this.hasInFlightRun(agentId)) {', `            if (guard.restartRecoveryMessageId &&\n                (record.startupResume?.messageId !== guard.restartRecoveryMessageId ||\n                    record.restartRecoveryStopped || record.restartRecoveryPermissionPending ||\n                    record.attentionReason === "permission")) {\n                throw new AgentMessageSendGuardRejectedError("restart_recovery_superseded");\n            }\n            const agent = this.agents.get(agentId);\n            if (guard.restartRecoveryMessageId && agent?.pendingPermissions.size) {\n                throw new AgentMessageSendGuardRejectedError("restart_recovery_permission_pending");\n            }\n            if (!agent || agent.lifecycle !== "idle" || this.hasInFlightRun(agentId)) {`],
        ['                this.onStreamPermissionRequested(agent, event);\n                return undefined;', '                return this.onStreamPermissionRequested(agent, event);'],
        ['    onStreamPermissionRequested(agent, event) {', `    async onStreamPermissionRequested(agent, event) {\n        // Persist before exposing a permission wait through RPC or subscribers.\n        if (this.registry && await this.registry.get(agent.id)) {\n            await this.registry.queueRecordMutation(agent.id, (record) => ({\n                ...record, restartRecoveryPermissionPending: true,\n            }));\n        }`],
        ['            turnId = await this.startPendingForegroundTurn({', `            if (!options?.clientMessageId?.startsWith("paseo-restart-") && this.registry) {\n                const stored = await this.registry.get(agentId);\n                if (stored?.restartRecoveryStopped || stored?.startupResume) {\n                    await this.registry.queueRecordMutation(agentId, (record) => ({\n                        ...record, restartRecoveryStopped: undefined, startupResume: undefined,\n                    }));\n                }\n            }\n            turnId = await this.startPendingForegroundTurn({`],
    ],
    [
        ['import { attachAgentStoragePersistence } from "./persistence-hooks.js";', `// ${marker} Recover conversations through the existing guarded send/request journal.\nimport { createCrashRecovery } from "./agent/crash-recovery.js";\nimport { attachAgentStoragePersistence } from "./persistence-hooks.js";`],
        ['    await agentStorage.initialize();', `    await agentStorage.initialize();\n    const crashRecovery = createCrashRecovery({ paseoHome: config.paseoHome, storage: agentStorage,\n        agentManager, workspaceRegistry, logger });\n    await crashRecovery.prepareStartup();`],
        ['                        wsServer.beginAcceptingConnections();', `                        wsServer.beginAcceptingConnections();\n                        crashRecovery.resumePending();`],
    ],
    [
        ['export async function cancelAgentRunCommand(dependencies, agentId) {', `export async function cancelAgentRunCommand(dependencies, agentId) {\n    // ${marker} Persist a human/MCP Stop before attempting provider interruption.\n    await dependencies.agentManager.recordRestartRecoveryStop(agentId);`],
    ],
];
for (let i = 0; i < targets.length; i++) {
    for (const [before, after] of edits[i]) {
        if (targets[i].source.split(before).length !== 2) { console.error("SKIP: missing or ambiguous recovery anchor: " + targets[i].path); process.exit(20); }
        targets[i].source = targets[i].source.replace(before, after);
    }
}
for (const target of targets) fs.writeFileSync(target.path + ".paseo-new.mjs", target.source);
console.log("PATCHED");
