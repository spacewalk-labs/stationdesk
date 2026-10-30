'use strict';
// Run the shipped selection and connection functions without opening a terminal.
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../apps/devterm/web/app.js'), 'utf8');
const functions = ['ttydSearch', 'currentSession', 'mkFocus', 'connect', 'initSession'].map(name => {
  const match = source.match(new RegExp(`(?:async )?function ${name}\\([^]*?^}`, 'm'));
  assert.ok(match, `shipped ${name} function`);
  return match[0];
}).join('\n');

for (const [name, search, selected, args] of [
  ['card link', '?session=card-42', 'card-42', ['card-42']],
  ['account link', '?session=account-claude', 'account-claude', ['account-claude']],
  ['named session and working directory', '?arg=work&arg=%2Fhome%2Fowner%2Fa+b', 'work', ['work', '/home/owner/a b']],
  ['explicit ttyd args take priority', '?session=other&arg=work&arg=%2Ftmp', 'work', ['work', '/tmp']],
]) {
  test(name, async () => {
    const requests = [], sockets = [];
    let modalOpen = false, focusCount = 0;
    class Socket {
      constructor(url, protocols) { this.url = String(url); this.protocols = protocols; sockets.push(this); }
      send() {}
    }
    Socket.CONNECTING = 0; Socket.OPEN = 1;
    const context = vm.createContext({
      URL, URLSearchParams, clearTimeout, WebSocket: Socket,
      location: {search, href: `https://station.test/devterm/${search}`, protocol: 'https:'},
      fetch: async url => { requests.push(url); return {json: async () => ({token: 'test'})}; },
      connecting: false, connected: false, ws: null, connectSeq: 0, reconnectTimer: null, switching: false,
      _openingSn: null, reconnectAttempts: 0, token: '', enc: text => text, hideStatus() {},
      document: {querySelector: () => modalOpen ? {} : null},
      term: {cols: 80, rows: 24, focus() { focusCount++; }},
      showStatus() {}, scheduleReconnect() { assert.fail('unexpected reconnect'); }, onMsg() {},
    });
    vm.runInContext(functions, context);
    assert.equal(vm.runInContext('currentSession()', context), selected);
    // initSession must honor a card/account link before requesting another session.
    vm.runInContext('initSession()', context);
    await new Promise(setImmediate);
    assert.equal(requests.length, 1);
    assert.ok(requests[0].startsWith('token?'));
    assert.equal(sockets.length, 1);
    const socket = new URL(sockets[0].url);
    assert.equal(socket.pathname, '/devterm/ws');
    assert.equal(socket.protocol, 'wss:');
    assert.deepEqual(socket.searchParams.getAll('arg'), args);
    assert.deepEqual(new URL(requests[0], context.location.href).searchParams.getAll('arg'), args);
    assert.equal(socket.searchParams.has('session'), false);
    sockets[0].onopen();
    assert.equal(focusCount, 1, 'normal connection focuses terminal');
    modalOpen = true;
    sockets[0].onopen();
    assert.equal(focusCount, 1, 'reconnection preserves secret/modal input focus');
  });
}
