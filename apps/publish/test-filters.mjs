import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('./web/index.html', import.meta.url), 'utf8');
const start = html.indexOf('// TESTABLE:FILTERS_START');
const end = html.indexOf('// TESTABLE:FILTERS_END');
assert.ok(start >= 0 && end > start, 'filter markers missing');

const now = Math.floor(Date.now() / 1000), D = 86400;
const ctx = {
  state: { view: 'all', publicEnabled: true, publicListState: 'ready', pubBySrc: {}, sel: { type: {}, status: {}, project: {}, age: {}, views: {} } },
  search: '',
  keys: Object.keys,
  esc: (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  snapAt: (p) => p.snapshot_at || p.created || 0,
  $: () => ({ get value() { return ctx.search; } }),
  Date,
};
vm.createContext(ctx);
vm.runInContext(html.slice(start, end), ctx);
const doc = (name, title, project, ageD, extra = {}) => ({
  name, title, type: 'html', isLink: true, isDir: false, isBroken: false,
  target_dir: `/home/u/workspace/${project}/docs`, mtime_epoch: now - ageD * D, ...extra });
const items = [
  doc('a.html', 'Alpha plan', 'airlock', 1),
  doc('b.html', 'Beta notes', 'airlock', 20),
  doc('c.html', 'Gamma <b>', 'wiki', 50, { isBroken: true }),
  doc('d.html', '', 'wiki', 200, { isLink: false }),
];
ctx.state.pubBySrc = {
  'a.html': { expiry: now + 2 * D, snapshot_at: now - 5 * D, mode: 'open' },
  'b.html': { expiry: now + 9 * D, snapshot_at: now - 10 * D, mode: 'gated' },
};
const names = () => items.filter((it) => ctx.matchesFilters(it)).map((it) => it.name[0]).join('');
const pick = (f, ...v) => { ctx.state.sel[f] = Object.fromEntries(v.map((x) => [x, true])); };

assert.equal(ctx.projectOf(items[0]), 'airlock');
assert.equal(ctx.projectOf(items[3]), '(direct)');
assert.equal(names(), 'abcd');

// OR within a row
pick('project', 'airlock', 'wiki'); assert.equal(names(), 'abc');
pick('project', 'airlock'); assert.equal(names(), 'ab');
// AND across rows
pick('status', 'public'); assert.equal(names(), 'ab');
pick('status', 'expiring'); assert.equal(names(), 'a');
pick('status', 'password', 'expiring'); assert.equal(names(), 'ab');
pick('project'); pick('status'); pick('age', '7d'); assert.equal(names(), 'a');
pick('age', '30d'); assert.equal(names(), 'ab');   // cumulative: yesterday is also within 30 days
pick('age', '90d'); assert.equal(names(), 'abc');
pick('age', 'old'); assert.equal(names(), 'd');
pick('age', '7d', 'old'); assert.equal(names(), 'ad');
// Source changed: a's snapshot is newer than its file? a mtime=1d ago, snapshot 5d ago -> changed
pick('project'); pick('age'); pick('status', 'changed'); assert.equal(names(), 'a');
pick('status', 'broken'); assert.equal(names(), 'c');
pick('status', 'direct'); assert.equal(names(), 'd');
pick('status', 'private'); assert.equal(names(), 'cd');
pick('status');

// Views row: buckets by recorded count; a document with none recorded is "0 views"
items[0].views = 12; items[1].views = 2; items[2].views = 3;   // d has none
pick('views', 'v0'); assert.equal(names(), 'd');
pick('views', 'lt3'); assert.equal(names(), 'bd');   // "< 3" includes the unviewed
pick('views', 'ge10'); assert.equal(names(), 'a');
pick('views', 'v0', 'ge10'); assert.equal(names(), 'ad');
pick('views', 'ge10'); pick('age', '30d'); assert.equal(names(), 'a');   // AND with other rows
pick('age'); pick('views');

// Search: title, file name, project; every word must match
ctx.search = 'beta'; assert.equal(names(), 'b');
ctx.search = 'D.HTML'; assert.equal(names(), 'd');
ctx.search = 'wiki'; assert.equal(names(), 'c');
ctx.search = 'airlock plan'; assert.equal(names(), 'a');
ctx.search = 'nothing'; assert.equal(names(), '');

// Highlight escapes first and never breaks markup or entities
ctx.search = 'b amp';
assert.equal(ctx.hl('Gamma <b> & more'), 'Gamma &lt;<mark>b</mark>&gt; &amp; more');
ctx.search = '(';
assert.equal(ctx.hl('f(x)'), 'f<mark>(</mark>x)');
ctx.search = '';

// Unknown public state must not hide anything by public statuses
ctx.state.publicListState = 'unknown';
pick('status', 'private'); assert.equal(names(), 'abcd');

// Pagination
assert.equal(ctx.PAGE_SIZE, 50);
assert.equal([0, 1, 50, 51, 100, 101].map(ctx.pageCount).join(), '1,1,1,2,2,3');
assert.equal([[0, 5], [1, 5], [9, 120], [3, 120], [-2, 120], [2, 0]].map(([p, n]) => ctx.clampPage(p, n)).join(), '1,1,3,3,1,1');
const rows = Array.from({ length: 120 }, (_, i) => i);
assert.deepEqual([1, 2, 3].map((p) => ctx.pageSlice(rows, p).length), [50, 50, 20]);
assert.equal(ctx.pageSlice(rows, 3)[0], 100);
assert.equal(ctx.pageSlice(rows, 9)[0], 100);   // past the end clamps to the last page
assert.equal(ctx.pageSlice([], 4).length, 0);
const pi = (p, n) => ctx.pagerItems(p, n).map((x) => x || '…').join(' ');
assert.equal(pi(1, 1), '1');
assert.equal(pi(1, 5), '1 2 3 4 5');
assert.equal(pi(1, 10), '1 2 3 … 10');
assert.equal(pi(5, 10), '1 2 3 4 5 6 7 … 10');
assert.equal(pi(6, 10), '1 … 4 5 6 7 8 9 10');
assert.equal(pi(5, 12), '1 2 3 4 5 6 7 … 12');
assert.equal(pi(6, 12), '1 … 4 5 6 7 8 … 12');
assert.equal(pi(4, 10), '1 2 3 4 5 6 … 10');   // a one-page gap shows the page, not an ellipsis
assert.equal(pi(10, 10), '1 … 8 9 10');
console.log('publish filters: ok');
