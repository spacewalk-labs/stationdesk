// SPDX-License-Identifier: AGPL-3.0-only
// Exercise the rebuilt guard methods, including durable crash-recovery boundaries.
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const build = resolve(process.argv[2]);
const { ScheduleStore } = await import(pathToFileURL(join(build, 'packages/server/dist/server/server/schedule/store.js')));
const { AgentManager, AgentMessageSendGuardRejectedError } = await import(pathToFileURL(join(build, 'packages/server/dist/server/server/agent/agent-manager.js')));
const scratch = await mkdtemp(join(tmpdir(), 'paseo-guards-'));
const schedule = () => ({
  name: 'source verification', prompt: 'check', cadence: { type: 'every', everyMs: 60000 },
  target: { type: 'new-agent', config: { provider: 'claude', cwd: scratch } },
  status: 'active', createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z',
  nextRunAt: '2026-01-01T00:01:00.000Z', lastRunAt: null, pausedAt: null,
  expiresAt: null, maxRuns: null, runs: [],
});
const pause = s => ({ ...s, status: 'paused', pausedAt: '2026-01-01T00:00:30.000Z', nextRunAt: null });
const restore = (s, state) => ({ ...s, ...state });
try {
  const store = new ScheduleStore(join(scratch, 'exact'));
  const first = await store.create(schedule());
  const operation = { operationId: 'exact', scheduleId: first.id, targetStatus: 'paused' };
  assert.equal((await store.transitionState(operation, pause)).replayed, false);
  assert.equal((await store.transitionState(operation, pause)).replayed, true);
  const recovered = await new ScheduleStore(join(scratch, 'exact')).restoreState(operation, restore);
  assert.equal(recovered.schedule.status, first.status);
  assert.equal(recovered.schedule.nextRunAt, first.nextRunAt);
  assert.equal(recovered.schedule.pausedAt, first.pausedAt);
  assert.equal((await store.restoreState(operation, restore)).replayed, true);
  await assert.rejects(store.transitionState(operation, pause), /already restored/);

  const second = await store.create(schedule());
  const changed = { operationId: 'changed', scheduleId: second.id, targetStatus: 'paused' };
  await store.transitionState(changed, pause);
  await store.update(second.id, s => ({ ...s, name: 'another managed write' }));
  await assert.rejects(store.restoreState(changed, restore), /changed after operation/);
  await assert.rejects(store.transitionState({ ...changed, targetStatus: 'active' }, s => s), /already used/);
  await assert.rejects(store.restoreState({ ...changed, scheduleId: first.id }, restore), /another schedule/);

  for (const hook of ['afterOperationPrepared', 'afterScheduleWritten', 'afterRestorePrepared', 'afterRestoreScheduleWritten']) {
    const dir = join(scratch, hook);
    const normal = new ScheduleStore(dir);
    const original = await normal.create(schedule());
    const input = { operationId: hook, scheduleId: original.id, targetStatus: 'paused' };
    const crashing = new ScheduleStore(dir, { [hook]: () => { throw new Error('simulated exit'); } });
    if (hook.startsWith('afterRestore')) {
      await normal.transitionState(input, pause);
      await assert.rejects(crashing.restoreState(input, restore), /simulated exit/);
    } else {
      await assert.rejects(crashing.transitionState(input, pause), /simulated exit/);
      await new ScheduleStore(dir).transitionState(input, pause);
    }
    const result = await new ScheduleStore(dir).restoreState(input, restore);
    assert.equal(result.schedule.status, original.status);
    assert.equal(result.schedule.nextRunAt, original.nextRunAt);
  }

  const agentId = '00000000-0000-4000-8000-000000000001';
  const stamp = new Date('2026-01-01T00:00:00.000Z');
  const guard = { expectedAgentId: agentId, expectedUpdatedAt: stamp.toISOString() };
  let called = 0, lane = false;
  const agent = { lifecycle: 'idle', updatedAt: stamp };
  let record = {}, busy = false;
  const manager = {
    agents: new Map([[agentId, agent]]),
    requireRegistry: () => ({ get: async () => record }),
    hasInFlightRun: () => busy,
    runLifecycleMutation: async (id, fn) => { assert.equal(id, agentId); lane = true; try { return await fn(); } finally { lane = false; } },
  };
  const send = async () => { assert.equal(lane, true); return ++called; };
  const dispatch = input => AgentManager.prototype.runGuardedAgentMessageSend.call(manager, agentId, input, send);
  assert.equal(await dispatch(guard), 1);
  const reject = async (input, reason) => {
    await assert.rejects(dispatch(input), error => error instanceof AgentMessageSendGuardRejectedError && error.reason === reason);
    assert.equal(called, 1);
  };
  await reject({ ...guard, expectedAgentId: 'another' }, 'agent_id_mismatch');
  record = null; await reject(guard, 'agent_id_mismatch');
  record = { archivedAt: stamp.toISOString() }; await reject(guard, 'archived');
  record = {}; busy = true; await reject(guard, 'not_idle');
  busy = false; agent.lifecycle = 'running'; await reject(guard, 'not_idle');
  agent.lifecycle = 'idle'; await reject({ ...guard, expectedUpdatedAt: 'stale' }, 'updated_at_mismatch');
  console.log('PASS: exact restore/replay, ownership and mutation checks, four crash boundaries, atomic send and all rejection reasons');
} finally {
  await rm(scratch, { recursive: true, force: true });
}
