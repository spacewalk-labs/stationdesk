// SPDX-License-Identifier: AGPL-3.0-only
const assert = require("node:assert/strict");
const { isTerminalCloseCode, applyRosterMessage, closeAction } = require("../web/browse-view-client.js");

assert.equal(isTerminalCloseCode(4001), true);
assert.equal(isTerminalCloseCode(1000), false);
assert.equal(isTerminalCloseCode(1006), false);
assert.equal(closeAction(4001, false), "terminal");
assert.equal(closeAction(1006, false), "reconnect");
assert.equal(closeAction(1000, false), "reconnect");
assert.equal(closeAction(4001, true), "none");
assert.equal(closeAction(1006, true), "none");

const first = { browserId: "b1", workspaceId: "wks_one", url: "https://one" };
const second = { browserId: "b2", workspaceId: "wks_two", url: "https://two" };
const tabs = new Map([["old", { browserId: "old" }]]);

applyRosterMessage(tabs, { type: "snapshot", tabs: [first, second] });
assert.deepEqual([...tabs.keys()], ["b1", "b2"]);

const updated = { ...first, url: "https://updated" };
applyRosterMessage(tabs, { type: "upsert", tab: updated });
assert.equal(tabs.get("b1").url, "https://updated");

applyRosterMessage(tabs, { type: "remove", browserId: "b2" });
assert.equal(tabs.has("b2"), false);

applyRosterMessage(tabs, { type: "reset" });
assert.equal(tabs.size, 0);

tabs.set("keep", { browserId: "keep" });
applyRosterMessage(tabs, null);
applyRosterMessage(tabs, "invalid");
applyRosterMessage(tabs, { type: "unknown" });
assert.equal(tabs.size, 1);

console.log("companion unit OK");

// Run the shipped handlers: entering a URL while the new tab is connecting must
// preserve the latest request and send it once the host has created the page.
{
  const fs = require("node:fs");
  const vm = require("node:vm");
  const source = fs.readFileSync(require.resolve("../web/browse-view-client.js"), "utf8");
  const handlers = ["navigate", "onText"].map(name => {
    const match = source.match(new RegExp("LivePanel\\.prototype\\." + name + " = function \\([^]*?^  };", "m"));
    assert.ok(match, "shipped " + name);
    return match[0];
  }).join("\n");
  const sent = [];
  const context = {LivePanel: function () {}, document: {}, JSON};
  vm.runInNewContext(handlers, context);
  const panel = new context.LivePanel();
  Object.assign(panel, {ready: false, ws: null, send: msg => sent.push(JSON.parse(JSON.stringify(msg))), setStatus() {}});
  panel.navigate("first.example");
  panel.navigate("https://latest.example/path");
  assert.equal(sent.length, 0);
  assert.equal(panel.pendingNavigation, "https://latest.example/path");
  panel.ws = {readyState: 1};
  panel.onText(JSON.stringify({type: "ready"}));
  assert.deepEqual(sent, [{type: "nav", action: "navigate", url: "https://latest.example/path"}]);
  assert.equal(panel.pendingNavigation, null);
  panel.onText(JSON.stringify({type: "ready"}));
  assert.equal(sent.length, 1, "a duplicate ready must not navigate twice");
  panel.navigate("next.example");
  assert.equal(sent[1].url, "https://next.example");
  panel.ws.readyState = 3;
  panel.navigate("reconnect.example");
  assert.equal(sent.length, 2, "a closing socket must retain the requested URL");
  panel.ws = {readyState: 1};
  panel.onText(JSON.stringify({type: "ready"}));
  assert.equal(sent[2].url, "https://reconnect.example");
}
console.log("companion connecting navigation regression OK");
