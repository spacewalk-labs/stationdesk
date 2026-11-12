// No files are written beside the candidate and no Claude process is started.
// node --expose-gc claude-stream-retention.test.mjs <providers/claude/agent.js>
import assert from 'node:assert/strict';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const candidate = process.argv[2];
assert.ok(candidate, 'usage: claude-stream-retention.test.mjs <candidate claude/agent.js>');
assert.equal(typeof global.gc, 'function', 'run the memory regression with --expose-gc');
const agentDir = resolve(dirname(resolve(candidate)), '../..');
const load = (path) => import(pathToFileURL(path).href);
const { ClaudeAgentClient } = await load(resolve(candidate));
const { InMemoryAgentTimelineStore } = await load(resolve(agentDir, 'agent-timeline-store.js'));
const { AgentStreamCoalescer } = await load(resolve(agentDir, 'agent-stream-coalescer.js'));
const noop = () => {};
const logger = { child: () => logger, trace: noop, debug: noop, info: noop, warn: noop, error: noop };
const forbidden = () => { throw new Error('this synthetic fixture must never start Claude'); };

async function fixture() {
    const client = new ClaudeAgentClient({ logger, queryFactory: forbidden, resolveBinary: forbidden });
    const session = await client.createSession({ provider: 'claude', cwd: '/synthetic-workspace' });
    const timeline = new InMemoryAgentTimelineStore();
    timeline.initialize('synthetic-agent');
    let now = 0;
    let currentId = 'synthetic-message';
    const coalescer = new AgentStreamCoalescer({
        windowMs: 60, now: () => now, timers: { setTimeout, clearTimeout },
        onFlush: ({ item }) => timeline.append('synthetic-agent', item),
    });
    const feed = async (message) => {
        // Model a sustained stream across real coalescing windows without wall-clock sleeps.
        now += 61;
        const events = await session.buildPumpedMessageEvents(message, currentId, 'synthetic-turn');
        for (const event of events) {
            if (event.type === 'timeline' && !coalescer.handle('synthetic-agent', event)) {
                timeline.append('synthetic-agent', event.item);
            }
        }
    };
    const stream = (event) => feed({ type: 'stream_event', event });
    const start = async (id = 'synthetic-message') => {
        currentId = id;
        await stream({ type: 'message_start', message: { id } });
    };
    const delta = (kind, text) => stream({
        type: 'content_block_delta',
        delta: kind === 'assistant_message'
            ? { type: 'text_delta', text }
            : { type: 'thinking_delta', thinking: text },
    });
    const drain = () => coalescer.flushFor('synthetic-agent');
    const stop = async () => { await stream({ type: 'message_stop' }); drain(); };
    const items = () => timeline.getItems('synthetic-agent');
    const text = (kind) => items().filter((item) => item.type === kind).map((item) => item.text).join('');
    return {
        session, feed, start, delta, stop, drain, items, text,
        dispose() {
            coalescer.flushAndDiscard('synthetic-agent');
            timeline.delete('synthetic-agent');
            session.timelineAssembler.messages.clear();
            session.timelineAssembler.activeMessageByRun.clear();
        },
    };
}

let failures = 0;
async function check(name, body) {
    const f = await fixture();
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

for (const kind of ['assistant_message', 'reasoning']) {
    await check(`${kind} deltas retain their own text after message completion`, async (f) => {
        await f.start();
        global.gc();
        const before = process.memoryUsage().heapUsed;
        const chunk = 'x'.repeat(1024);
        for (let i = 0; i < 600; i++) await f.delta(kind, chunk);
        await f.stop();
        assert.equal(f.session.timelineAssembler.messages.size, 0, 'completed assembler state was not released');
        global.gc();
        const growth = process.memoryUsage().heapUsed - before;
        const rows = f.items().filter((item) => item.type === kind);
        console.log(JSON.stringify({ kind, deltas: 600, rows: rows.length, visibleChars: rows.reduce((n, item) => n + item.text.length, 0), heapGrowthBytes: growth }));
        assert.equal(f.text(kind), chunk.repeat(600), 'streamed content changed');
        assert.equal(rows.length, 600, 'the sustained stream should cross 600 coalescing windows');
        // Allocation budget for this fixed 600 KiB fixture, not a runtime limit or setting.
        assert.ok(growth < 16 * 1024 * 1024, `600 KiB ${kind} stream retained ${growth} bytes`);
    });
}

await check('mixed Unicode and whitespace survive streamed chunks exactly', async (f) => {
    await f.start();
    const assistant = ['  assistant prefix ', '\ud83d', '\ude42\ntrailing whitespace  '];
    const reasoning = ['  reasoning prefix ', '\ud83e', '\uddea\ntrailing whitespace  '];
    for (let i = 0; i < assistant.length; i++) {
        await f.delta('assistant_message', assistant[i]);
        await f.delta('reasoning', reasoning[i]);
    }
    await f.stop();
    assert.equal(f.text('assistant_message'), assistant.join(''));
    assert.equal(f.text('reasoning'), reasoning.join(''));
});

await check('absolute assistant snapshots emit only unseen text and ignore finalized replays', async (f) => {
    await f.start();
    await f.delta('assistant_message', 'first assistant chunk ');
    await f.delta('reasoning', 'first reasoning chunk ');
    const assistant = 'first assistant chunk final assistant suffix';
    const reasoning = 'first reasoning chunk final reasoning suffix';
    const message = {
        type: 'assistant',
        message: { id: 'synthetic-message', content: [
            { type: 'text', text: assistant }, { type: 'thinking', thinking: reasoning },
        ] },
    };
    await f.feed(message);
    f.drain();
    const rows = f.items().length;
    await f.feed(message);
    assert.equal(f.items().length, rows, 'an unchanged absolute snapshot emitted duplicate text');
    await f.stop();
    await f.feed(message);
    assert.equal(f.items().length, rows, 'a finalized message was emitted again');
    assert.equal(f.text('assistant_message'), assistant);
    assert.equal(f.text('reasoning'), reasoning);
});

await check('a new message in the same turn has independent stream offsets', async (f) => {
    await f.start('synthetic-first');
    await f.delta('assistant_message', 'first independent message');
    await f.stop();
    await f.start('synthetic-second');
    await f.delta('assistant_message', 'second independent message');
    await f.stop();
    assert.deepEqual(f.items().filter((item) => item.type === 'assistant_message').map((item) => [item.messageId, item.text]), [
        ['synthetic-first', 'first independent message'],
        ['synthetic-second', 'second independent message'],
    ]);
});

if (failures) process.exitCode = 1;
else console.log('OK: Claude streamed text does not retain past accumulated prefixes');
