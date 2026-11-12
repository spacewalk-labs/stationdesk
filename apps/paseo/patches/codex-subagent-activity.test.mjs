// Run against an isolated candidate tree; this test never writes beside the candidate:
// node --expose-gc codex-subagent-activity.test.mjs <providers/codex-app-server-agent.js>
// Synthetic notifications exercise the installed parser, provider, coalescer and timeline stores.
import assert from 'node:assert/strict';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const candidate = process.argv[2];
assert.ok(candidate, 'usage: codex-subagent-activity.test.mjs <candidate codex-app-server-agent.js>');
assert.equal(typeof global.gc, 'function', 'run this memory regression with --expose-gc');
const providerDir = dirname(resolve(candidate));
const agentDir = resolve(providerDir, '..');
const load = (path) => import(pathToFileURL(path).href);
const { CodexAppServerAgentSession } = await load(resolve(candidate));
const { InMemoryAgentTimelineStore } = await load(resolve(agentDir, 'agent-timeline-store.js'));
const { AgentStreamCoalescer } = await load(resolve(agentDir, 'agent-stream-coalescer.js'));
const { ProviderSubagentStore } = await load(resolve(agentDir, 'provider-subagents/store.js'));
const { limitAgentTimelineItemContent } = await load(resolve(agentDir, 'agent-timeline-content.js'));
const noop = () => {};
const logger = { child: () => logger, trace: noop, debug: noop, info: noop, warn: noop, error: noop };
const ROOT = 'synthetic-root';
const CHILD = 'synthetic-child';
const CALL = 'synthetic-spawn';

function fixture() {
    const session = new CodexAppServerAgentSession(
        { provider: 'codex', cwd: '/synthetic-workspace' }, undefined, logger,
        () => { throw new Error('the synthetic fixture must never spawn a provider'); },
    );
    session.currentThreadId = ROOT;
    const timeline = new InMemoryAgentTimelineStore();
    timeline.initialize(ROOT);
    const children = new ProviderSubagentStore();
    let now = 0;
    const append = (item) => timeline.append(ROOT, limitAgentTimelineItemContent(item));
    const coalescer = new AgentStreamCoalescer({
        windowMs: 60, now: () => now, timers: { setTimeout, clearTimeout },
        onFlush: ({ item }) => append(item),
    });
    const receive = (event) => {
        if (event.type === 'provider_subagent') children.apply(ROOT, event.provider, event.event);
        if (event.type === 'timeline' && !coalescer.handle(ROOT, event)) append(event.item);
    };
    const unsubscribe = session.subscribe(receive);
    const feed = (method, params) => {
        // A real stream can keep emitting across coalescing windows. Advancing the clock avoids
        // wall-clock sleeps without hiding the retained snapshots behind a single burst flush.
        now += 61;
        session.handleNotification(method, params);
    };
    const spawnItem = (id = CALL, child = CHILD, status = 'running') => ({
        type: 'collabAgentToolCall', id, tool: 'spawnAgent', status,
        prompt: 'Synthetic delegated work', receiverThreadIds: [child],
    });
    const spawn = (parent = ROOT, id = CALL, child = CHILD) => feed('item/started', {
        threadId: parent, turnId: 'synthetic-turn', item: spawnItem(id, child),
    });
    const delta = (text, threadId = CHILD, itemId = 'synthetic-message') => feed('item/agentMessage/delta', {
        threadId, itemId, delta: text,
    });
    const finish = (status, threadId = CHILD) => feed('turn/completed', {
        threadId, turn: { id: 'synthetic-child-turn', status, error: status === 'failed' ? { message: 'synthetic failure' } : null },
    });
    const rows = () => timeline.getRows(ROOT);
    const childRows = (id = CHILD) => children.fetchTimeline(ROOT, id, { limit: 0 }).rows;
    return {
        session, timeline, children, receive, feed, spawn, spawnItem, delta, finish, rows, childRows,
        dispose() {
            unsubscribe();
            coalescer.flushAndDiscard(ROOT);
            timeline.delete(ROOT);
            children.deleteParent(ROOT);
            session.subAgentCallsByCallId.clear();
            session.subAgentCallIdByChildThreadId.clear();
            session.pendingAgentMessages.clear();
            session.pendingReasoning.clear();
        },
    };
}

let failures = 0;
async function check(name, body) {
    const f = fixture();
    try {
        await body(f);
        console.log('PASS: ' + name);
    } catch (error) {
        failures++;
        console.error('FAIL: ' + name + '\n' + error.stack);
    } finally {
        f.dispose();
        global.gc();
    }
}

await check('600 streamed deltas retain one child body and no cumulative parent logs', (f) => {
    f.spawn();
    global.gc();
    const before = process.memoryUsage().heapUsed;
    const chunk = 'x'.repeat(1024);
    for (let i = 0; i < 600; i++) f.delta(chunk);
    global.gc();
    const growth = process.memoryUsage().heapUsed - before;
    const body = f.childRows().map((row) => row.item.text ?? '').join('');
    console.log(JSON.stringify({ deltas: 600, parentRows: f.rows().length, childRows: f.childRows().length, childChars: body.length, heapGrowthBytes: growth }));
    assert.equal(body, chunk.repeat(600), 'the dedicated child timeline lost streamed text');
    // This is an allocation regression budget for a fixed 600 KiB fixture, not a runtime limit.
    assert.ok(growth < 16 * 1024 * 1024, `600 KiB child stream retained ${growth} bytes`);
    assert.ok(f.rows().length <= 2, `unchanged launch marker emitted ${f.rows().length} parent rows`);
    assert.ok(f.rows().every((row) => row.item.detail?.log === ''), 'parent rows contain duplicated child logs');
    f.finish('completed');
    assert.equal(f.rows().at(-1).item.status, 'completed');
    assert.ok(f.rows().length <= 2, 'completion should add only a status marker');
});

for (const [wireStatus, expected] of [['completed', 'completed'], ['failed', 'failed'], ['interrupted', 'canceled']]) {
    await check(`${expected} markers preserve terminal state until an explicit reopen`, (f) => {
        f.spawn();
        f.finish(wireStatus);
        const terminal = f.rows().at(-1).item;
        assert.equal(terminal.status, expected);
        assert.equal(f.children.get(ROOT, CHILD).status, expected);
        if (expected === 'failed') assert.ok(terminal.error, 'failed marker lost its error');
        else assert.equal(terminal.error, null);
        const count = f.rows().length;
        f.delta('late child text');
        assert.equal(f.rows().length, count, 'late output resent an unchanged terminal marker');
        assert.equal(f.session.subAgentCallsByCallId.get(CALL).toolCall.status, expected);
        assert.equal(f.children.get(ROOT, CHILD).status, expected);
        assert.equal(f.childRows().at(-1).item.text, 'late child text');
        f.feed('thread/started', { thread: { id: CHILD } });
        assert.equal(f.rows().at(-1).item.status, 'running', 'explicit child restart did not reopen the marker');
        assert.equal(f.rows().length, count + 1);
        assert.equal(f.children.get(ROOT, CHILD).status, 'running');
    });
}

await check('a child label change is dispatched once without a log snapshot', (f) => {
    f.spawn();
    const count = f.rows().length;
    const activity = {
        type: 'subAgentActivity', kind: 'interacted', agentThreadId: CHILD,
        agentPath: '/root/renamed_reviewer', id: 'synthetic-label-event',
    };
    f.feed('item/started', { threadId: ROOT, item: activity });
    assert.equal(f.rows().length, count + 1, 'label changed without updating the parent marker');
    assert.equal(f.rows().at(-1).item.detail.subAgentType, 'Renamed reviewer');
    assert.equal(f.rows().at(-1).item.detail.log, '');
    assert.equal(f.children.get(ROOT, CHILD).title, 'Renamed reviewer');
    f.feed('item/started', { threadId: ROOT, item: activity });
    assert.equal(f.rows().length, count + 1, 'duplicate label event resent the marker');
});

await check('nested child activity stays in its own timeline without recursive parent snapshots', (f) => {
    f.spawn();
    const rootCount = f.rows().length;
    f.spawn(CHILD, 'synthetic-grandchild-call', 'synthetic-grandchild');
    assert.equal(f.children.get(ROOT, 'synthetic-grandchild').parentSubagentId, CHILD);
    assert.ok(f.childRows().some((row) => row.item.callId === 'synthetic-grandchild-call'));
    for (let i = 0; i < 50; i++) f.delta('grandchild text ', 'synthetic-grandchild');
    assert.equal(f.rows().length, rootCount, 'grandchild output recursively resent the root marker');
    assert.equal(f.childRows('synthetic-grandchild').map((row) => row.item.text ?? '').join(''), 'grandchild text '.repeat(50));
    assert.ok(f.childRows().every((row) => row.item.detail?.type !== 'sub_agent' || row.item.detail.log === ''));
    f.finish('completed', 'synthetic-grandchild');
    assert.equal(f.children.get(ROOT, 'synthetic-grandchild').status, 'completed');
    assert.equal(f.rows().length, rootCount, 'grandchild completion resent the unchanged root marker');
});

await check('history hydration restores child and nested child timelines independently', async (f) => {
    const histories = new Map([
        [ROOT, [f.spawnItem(CALL, CHILD, 'completed')]],
        [CHILD, [{ type: 'agentMessage', id: 'historic-child-message', text: 'historic child answer' }, f.spawnItem('historic-grandchild-call', 'historic-grandchild', 'completed')]],
        ['historic-grandchild', [{ type: 'agentMessage', id: 'historic-grandchild-message', text: 'historic nested answer' }]],
    ]);
    f.session.client = { request: async (method, { threadId }) => {
        assert.equal(method, 'thread/read');
        assert.ok(histories.has(threadId), 'hydration read an unexpected thread');
        return { thread: { turns: [{ id: 'historic-turn', items: histories.get(threadId) }] } };
    } };
    await f.session.loadPersistedHistory();
    for await (const event of f.session.streamHistory()) f.receive(event);
    assert.equal(f.children.get(ROOT, CHILD).status, 'completed');
    assert.equal(f.children.get(ROOT, 'historic-grandchild').parentSubagentId, CHILD);
    assert.ok(f.childRows().some((row) => row.item.text === 'historic child answer'));
    assert.ok(f.childRows('historic-grandchild').some((row) => row.item.text === 'historic nested answer'));
    assert.ok(f.rows().every((row) => row.item.detail?.type !== 'sub_agent' || row.item.detail.log === ''));
});

if (failures) process.exitCode = 1;
else console.log('OK: Codex subagent activity remains separate from parent launch markers');
