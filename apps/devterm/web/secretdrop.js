'use strict';
/*
 * Terminal secret drop: store the value on this box and deliver only a path token.
 * The owner-gated API stores values under ~/.config/stationdesk/secrets.
 * ui.js supplies the page's modal and clipboard helpers. app.js supplies delivery,
 * status messages and the remote-session path adapter.
 */
window.initSecretDrop = function initSecretDrop(deps) {
  const flash = deps.flash, postJson = deps.postJson;
  const sendInput = deps.sendInput;
  const tokenTarget = deps.tokenTarget || function (path) { return path; };
  const readCmd = deps.readCmd || function (path) { return 'cat ' + path; };
  const refocus = typeof deps.refocus === 'function' ? deps.refocus : function () {};

  const secretPath = (name) => '~/.config/stationdesk/secrets/' + name + '.txt';
  // The agent-facing token is not the value, it is where the value is — same markdown
  // link shape devterm already uses for uploads.
  const secretTokenStr = (name) => '[secret:' + name + '](' + tokenTarget(secretPath(name)) + ') ';
  // The shell-facing form reads the file into an env var. For a remote session readCmd
  // reaches the right box.
  const secretExportStr = (name) => 'export ' + name + '=$(' + readCmd(secretPath(name)) + ')';

  const remainText = (sec) => {
    const n = Math.max(0, Number(sec) || 0);
    return n >= 60 ? Math.ceil(n / 60) + ' min left' : n + 's left';
  };

  // Draw the modal body; closing stops in-flight responses from updating it.
  function renderSecretUI(host, close) {
    let closed = false, busy = false;
    const deliveryBtns = [], deleteBtns = [];
    const markClosed = () => { closed = true; };

    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:14px;';
    host.appendChild(wrap);
    const group = (gap) => {
      const g = document.createElement('div');
      g.style.cssText = 'display:flex;flex-direction:column;gap:' + (gap || 5) + 'px;';
      wrap.appendChild(g); return g;
    };
    const mkLabel = (text) => {
      const d = document.createElement('div'); d.textContent = text;
      d.style.cssText = 'color:#c3c9d4;font:600 12px system-ui;letter-spacing:.2px;';
      return d;
    };

    const hd = document.createElement('div'); hd.style.cssText = 'display:flex;align-items:center;gap:10px;';
    hd.appendChild(uiTitle('Secret drop'));
    const sub = document.createElement('div');
    sub.textContent = 'The value stays in this modal; the terminal gets only a path';
    sub.style.cssText = 'flex:1;color:#8a92a6;font:12.5px/1.45 system-ui;'; hd.appendChild(sub);
    hd.appendChild(mkCloseBtn(close));
    wrap.appendChild(hd);

    const nameIn = document.createElement('input');
    nameIn.type = 'text'; nameIn.placeholder = 'GH_TOKEN';
    nameIn.autocomplete = 'off'; nameIn.autocorrect = 'off'; nameIn.autocapitalize = 'off'; nameIn.spellcheck = false;
    nameIn.style.cssText = 'width:100%;box-sizing:border-box;height:38px;padding:0 12px;' + UI_FIELD + 'font:14px ui-monospace,monospace;';
    const valueIn = document.createElement('textarea');
    valueIn.rows = 6; valueIn.autocomplete = 'off'; valueIn.autocorrect = 'off'; valueIn.autocapitalize = 'off'; valueIn.spellcheck = false;
    valueIn.style.cssText = 'width:100%;box-sizing:border-box;min-height:104px;resize:vertical;padding:10px 12px;' + UI_FIELD + 'font:13px ui-monospace,monospace;line-height:1.45;';
    const nameGroup = group(); nameGroup.appendChild(mkLabel('Name')); nameGroup.appendChild(nameIn);
    const valueGroup = group(); valueGroup.appendChild(mkLabel('Value')); valueGroup.appendChild(valueIn);
    const note = document.createElement('div');
    note.textContent = 'Stored only in ~/.config/stationdesk/secrets/ on this box and deleted automatically ' +
                       'when it expires. The value is stored as entered.';
    note.style.cssText = 'margin-top:-7px;color:#8a92a6;font:11.5px/1.5 system-ui;';
    valueGroup.appendChild(note);

    // Shown only when delivery failed (terminal disconnected / clipboard refused): keep
    // the token visible so it can be copied again. Failing quietly would leave the user
    // believing the secret was delivered.
    const fallback = document.createElement('div');
    fallback.style.cssText = 'display:none;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 10px;border:1px solid #8a4b4b;border-radius:7px;background:#2c2025;';
    const fallbackMsg = document.createElement('span'); fallbackMsg.style.cssText = 'flex:1;min-width:220px;color:#ffb4ad;font:12px/1.4 system-ui;';
    const fallbackTok = document.createElement('input');
    fallbackTok.type = 'text'; fallbackTok.readOnly = true; fallbackTok.spellcheck = false;
    fallbackTok.style.cssText = 'flex:1 1 100%;box-sizing:border-box;height:32px;padding:0 10px;' + UI_FIELD + 'font:12px ui-monospace,monospace;';
    const fallbackCopy = uiBtn('Copy path token', 'ghost');
    fallback.appendChild(fallbackMsg); fallback.appendChild(fallbackTok); fallback.appendChild(fallbackCopy);

    const listEl = document.createElement('div'); listEl.style.cssText = 'display:flex;flex-direction:column;gap:6px;min-height:28px;';

    let exportBtn;
    const exportHint = document.createElement('div'); exportHint.style.cssText = 'color:#e2c37b;font:11px/1.4 system-ui;';
    const setBusy = (on) => {
      busy = on;
      deliveryBtns.forEach((b) => { b.disabled = on; });
      deleteBtns.forEach((b) => { b.disabled = on; });
      if (exportBtn) updateExportGuard();
    };
    const updateExportGuard = () => {
      const valid = /^[A-Za-z_][A-Za-z0-9_]*$/.test(nameIn.value);
      exportBtn.disabled = busy || !valid;
      // An EMPTY name is guidance, not a warning — nothing is wrong yet, so it stays
      // grey. Amber only once something was typed that a shell cannot accept.
      const empty = !nameIn.value;
      exportHint.style.color = empty || valid ? '#8a92a6' : '#e2c37b';
      exportHint.textContent = empty
        ? 'An export statement needs a shell variable name (A-Z, 0-9, _).'
        : valid
          ? 'An exported value outlives the file — it stays in this shell and its children.'
          : 'Cannot export — use a shell variable name (A-Z, 0-9, _).';
    };
    const showFallback = (msg, token) => {
      fallback.style.display = 'flex';
      fallbackMsg.textContent = msg;
      fallbackTok.value = token;
      fallbackCopy.onclick = () => copyText(token).then((ok) => flash(
        ok ? 'Path token copied' : 'Copy failed — select the token above and copy it', 3200, ok ? undefined : 'error'));
    };
    const saveAndDeliver = async (kind) => {
      const name = nameIn.value, value = valueIn.value;
      setBusy(true);
      let j = null;
      try { j = await postJson('secret-put', { name: name, value: value }); } catch (e) {}
      if (closed) return;
      if (!j || j.error || j.ok === false) {
        setBusy(false);
        // Do not swallow the reason: a bad name, an oversized value and a storage failure
        // each call for a different action.
        flash('Could not store the secret' + (j && j.error ? ': ' + j.error : ''), 3000, 'error');
        return;
      }
      const token = kind === 'export' ? secretExportStr(name) : secretTokenStr(name);
      const sent = sendInput(token);
      if (!sent) {
        showFallback('Terminal disconnected — reconnect and deliver again.', token);
        copyText(token).then((ok) => flash(
          ok ? 'Disconnected — the path token was copied to the clipboard'
             : 'Disconnected — use the copy button below', 5200, 'error'));
      } else {
        if (valueIn.value === value) valueIn.value = '';
        // Copy it as well: the same token often has to go into another window too.
        // The clipboard gets the PATH TOKEN, never the secret itself.
        const base = kind === 'export' ? 'Sent the export to the shell' : 'Sent the path to the agent';
        copyText(token).then((ok) => flash(
          base + (ok ? ' · also copied' : ' (clipboard copy failed)'),
          ok ? 2200 : 3200, ok ? undefined : 'error'));
      }
      setBusy(false);
      loadList();
    };

    // Buttons are only as wide as their label. With the export button on flex:1 it took
    // half the row and outranked the primary action.
    const actionGroup = group(8);
    const actions = document.createElement('div'); actions.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;';
    const agentBtn = uiBtn('Send to agent', 'primary');
    agentBtn.onclick = () => saveAndDeliver('agent'); deliveryBtns.push(agentBtn);
    exportBtn = uiBtn('Export in shell', 'ghost');
    exportBtn.onclick = () => saveAndDeliver('export'); deliveryBtns.push(exportBtn);
    actions.appendChild(agentBtn); actions.appendChild(exportBtn);
    const cancelBtn = uiBtn('Close', 'ghost'); cancelBtn.onclick = close; actions.appendChild(cancelBtn);
    actionGroup.appendChild(actions); actionGroup.appendChild(exportHint);
    wrap.appendChild(fallback);

    // The stored list is a different concern from the input above it, so a rule separates
    // them — spacing alone read as one continuous form.
    const listSec = group(7);
    const rule = document.createElement('div');
    rule.style.cssText = 'height:1px;background:rgba(255,255,255,.09);margin:2px 0 1px;';
    listSec.appendChild(rule);
    listSec.appendChild(mkLabel('Live secrets'));
    listSec.appendChild(listEl);
    nameIn.addEventListener('input', updateExportGuard); updateExportGuard();

    const loadList = async () => {
      deleteBtns.length = 0;
      listEl.textContent = 'Loading…';
      try {
        const j = await postJson('secret-list', {});
        if (closed) return;
        if (!j || j.error || j.ok === false) throw new Error('list');
        listEl.textContent = '';
        if (!Array.isArray(j.secrets) || !j.secrets.length) {
          const empty = document.createElement('div');
          empty.textContent = 'No stored secrets';
          empty.style.cssText = 'color:#7f8798;font:12px system-ui;padding:4px 0;';
          listEl.appendChild(empty);
          return;
        }
        for (const item of j.secrets) {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid rgba(255,255,255,.08);border-radius:7px;background:#191d27;';
          const info = document.createElement('div'); info.style.cssText = 'flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;';
          const nm = document.createElement('div'); nm.textContent = item.name;
          nm.style.cssText = 'color:#e6e6e6;font:13px ui-monospace,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
          const meta = document.createElement('div');
          meta.textContent = (item.bytes === undefined ? '' : String(item.bytes) + 'B · ') + remainText(item.remain_sec === undefined ? Math.ceil(item.expires_at - Date.now() / 1000) : item.remain_sec);
          meta.style.cssText = 'color:#8a92a6;font:11px system-ui;';
          info.appendChild(nm); info.appendChild(meta);
          // Re-fetch the path of an already-stored secret (to paste it somewhere else) —
          // the token only, never the value.
          const again = uiBtn('Copy path', 'ghost'); again.style.cssText += 'height:30px;padding:0 10px;font-size:12px;';
          again.onclick = () => copyText(secretTokenStr(item.name)).then((ok) => flash(
            ok ? 'Path token copied' : 'Copy failed', ok ? 1800 : 3000, ok ? undefined : 'error'));
          const del = uiBtn('Delete', 'danger'); del.style.cssText += 'height:30px;padding:0 10px;font-size:12px;'; del.disabled = busy;
          del.onclick = async () => {
            setBusy(true);
            let d = null;
            try { d = await postJson('secret-del', { name: item.name }); } catch (e) {}
            if (closed) return;
            if (!d || d.error || d.ok === false) flash('Could not delete the secret', 3000, 'error');
            else flash('Secret deleted', 1500);
            await loadList();
            setBusy(false);
          };
          deleteBtns.push(del); row.appendChild(info); row.appendChild(again); row.appendChild(del);
          listEl.appendChild(row);
        }
      } catch (e) {
        if (closed) return;
        listEl.textContent = 'Could not load the list';
        listEl.style.cssText += 'color:#e06a5a;font:12px system-ui;';
      }
    };

    nameIn.focus();
    loadList();
    return { markClosed: markClosed };
  }

  // devterm — a modal (overlay + Esc + backdrop click).
  function openSecretDrop() {
    const { ov, box } = makeModal(24, 'padding:16px;width:100%;max-width:520px;display:flex;flex-direction:column;gap:10px;max-height:92vh;overflow:auto;');
    let ui = null, closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      if (ui) ui.markClosed();
      document.removeEventListener('keydown', onKey);
      try { document.body.removeChild(ov); } catch (e) {}
      refocus();
    };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(); });
    ov.appendChild(box); document.body.appendChild(ov);
    ui = renderSecretUI(box, close);
  }

  return { openSecretDrop: openSecretDrop };
};
