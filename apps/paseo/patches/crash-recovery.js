// SPDX-License-Identifier: AGPL-3.0-only
// One startup pass over interrupted work; the existing request journal owns uncertain sends.
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { AgentRequests } from "./requests/index.js";
import { ensureAgentLoaded } from "./agent-loading.js";
import { formatSystemNotificationPrompt, sendPromptToAgent } from "./agent-prompt.js";

const prompt = formatSystemNotificationPrompt("The Paseo daemon worker crashed. Continue the already authorized task in this conversation. First inspect the current workspace and durable execution receipts. Reconcile any in-flight external effects before issuing commands; never repeat an uncertain write, apply, deployment or message. Keep all existing approval and permission boundaries. A human Stop, pending permission or login still applies. Continue the remaining authorized work; do not treat the worker crash as a request to stop.");

export function createCrashRecovery({ paseoHome, storage, agentManager, workspaceRegistry, logger }) {
    const requests = new AgentRequests(join(paseoHome, "agent-requests"));
    const candidates = new Set();
    const stopped = (record) => record?.restartRecoveryStopped === true;
    const eligible = async (record) => record && !record.archivedAt && !record.internal && !record.owner &&
        record.persistence?.sessionId &&
        !stopped(record) && record.restartRecoveryPermissionPending === false && record.attentionReason !== "permission" &&
        (!record.workspaceId || !(await workspaceRegistry?.get(record.workspaceId))?.archivedAt);

    async function resume(id) {
        const record = await storage.get(id);
        const intent = record?.startupResume;
        if (!intent || !(await eligible(record))) return;
        try {
            await requests.send({
                agentId: id,
                messageId: intent.messageId,
                request: { prompt: intent.prompt },
                // Opening a provider is safe to retry. Claim the send only after that succeeds.
                prepare: async () => {
                    await ensureAgentLoaded(id, { agentManager, agentStorage: storage, logger });
                    const current = await storage.get(id);
                    if (!(await eligible(current))) throw new Error("restart_recovery_no_longer_eligible");
                },
                send: async () => {
                    const snapshot = agentManager.getAgent(id);
                    await sendPromptToAgent({
                        agentId: id, prompt: intent.prompt, agentManager, agentStorage: storage,
                        messageId: intent.messageId,
                        logger, unarchive: false, clearPendingPermissions: false,
                        guard: { expectedAgentId: id, expectedUpdatedAt: snapshot.updatedAt.toISOString(),
                            restartRecoveryMessageId: intent.messageId },
                    });
                    await agentManager.waitForAgentRunStart(id);
                    // Retire the intent before the journal becomes completed. A crash before this
                    // write leaves a pending receipt (unknown outcome); after it, a later crash
                    // captures the new running turn as a new continuation, never replays this send.
                    await storage.queueRecordMutation(id, (current) =>
                        current?.startupResume?.messageId === intent.messageId
                            ? { ...current, startupResume: undefined,
                                lastStatus: agentManager.getAgent(id)?.lifecycle ?? current.lastStatus }
                            : current);
                },
            });
            logger.info({ agentId: id, messageId: intent.messageId }, "Interrupted agent continuation accepted");
        } catch (error) {
            // A pending receipt is deliberately not retried: the provider may already have acted.
            await storage.queueRecordMutation(id, (current) => {
                if (!current || stopped(current) || current.restartRecoveryPermissionPending || current.attentionReason === "permission") return current;
                return { ...current, requiresAttention: true, attentionReason: "error",
                    attentionTimestamp: new Date().toISOString(), lastError: "Restart recovery: " + String(error) };
            });
            logger.error({ agentId: id, messageId: intent.messageId, err: error }, "Interrupted agent continuation retained; inspect outcome before resending");
        }
    }

    return {
        async prepareStartup() {
            for (const record of await storage.list()) {
                if (!record.startupResume && record.lastStatus !== "running") continue;
                if (!(await eligible(record))) {
                    // The old worker is gone even when its work must wait for a person.
                    if (record.lastStatus === "running") await storage.upsert({ ...record, lastStatus: "closed" });
                    continue;
                }
                const startupResume = record.startupResume ?? { messageId: "paseo-restart-" + randomUUID(), prompt };
                await storage.upsert({ ...record, startupResume, lastStatus: "closed" });
                candidates.add(record.id);
            }
        },
        resumePending() {
            const pending = [...candidates];
            candidates.clear();
            return Promise.allSettled(pending.map((id) => resume(id).catch((err) =>
                logger.error({ agentId: id, err }, "Failed to persist restart recovery outcome"))));
        },
    };
}
