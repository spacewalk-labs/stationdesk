'use strict';
/*
 * Secret drop: store the value on this box, copy only its path token, close.
 * The owner-gated API stores values under ~/.config/stationdesk/secrets and deletes
 * them after 30 minutes. ui.js supplies the modal and clipboard helpers; the page
 * supplies flash, postJson and, in the terminal, the remote-session path adapter.
 */
window.initSecretDrop = function initSecretDrop(deps) {
  const flash = deps.flash, postJson = deps.postJson;
  const tokenTarget = deps.tokenTarget || function (path) { return path; };
  const refocus = typeof deps.refocus === 'function' ? deps.refocus : function () {};

  // The token is not the value, it is where the value is — the markdown link shape
  // devterm already uses for uploads.
  const secretToken = (name) =>
    '[secret:' + name + '](' + tokenTarget('~/.config/stationdesk/secrets/' + name + '.txt') + ') ';

  // close: dismiss without saving (null when the embedder owns closing).
  // done: called after the value is stored and the token copied.
  function renderSecretUI(host, close, done) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:10px;';
    host.appendChild(wrap);

    const hd = document.createElement('div'); hd.style.cssText = 'display:flex;align-items:center;gap:10px;';
    // In a panel the embedder already titles it.
    const title = close ? uiTitle('Secret drop') : document.createElement('div');
    title.style.flex = '1'; hd.appendChild(title);
    const ttl = document.createElement('span'); ttl.textContent = 'Deleted after 30 min';
    ttl.style.cssText = 'color:#8a92a6;font:12px system-ui;'; hd.appendChild(ttl);
    if (close) hd.appendChild(mkCloseBtn(close));
    wrap.appendChild(hd);

    const field = (label, el) => {
      const d = document.createElement('div'); d.textContent = label;
      d.style.cssText = 'color:#c3c9d4;font:600 12px system-ui;margin-bottom:-5px;';
      wrap.appendChild(d); wrap.appendChild(el);
    };
    const quiet = (el) => { el.autocomplete = 'off'; el.autocapitalize = 'off'; el.spellcheck = false; el.setAttribute('autocorrect', 'off'); };
    const nameIn = document.createElement('input');
    nameIn.type = 'text'; nameIn.placeholder = 'GH_TOKEN'; quiet(nameIn);
    nameIn.style.cssText = 'width:100%;box-sizing:border-box;height:38px;padding:0 12px;' + UI_FIELD + 'font:14px ui-monospace,monospace;';
    const valueIn = document.createElement('textarea');
    valueIn.rows = 4; quiet(valueIn);
    valueIn.style.cssText = 'width:100%;box-sizing:border-box;min-height:80px;resize:vertical;padding:10px 12px;' + UI_FIELD + 'font:13px ui-monospace,monospace;line-height:1.45;';
    field('Name', nameIn);
    field('Value', valueIn);

    const err = document.createElement('div');
    err.style.cssText = 'display:none;color:#ffb4ad;font:12px/1.4 system-ui;';
    wrap.appendChild(err);

    const btn = uiBtn('Save & copy path', 'primary');
    btn.style.alignSelf = 'flex-start';
    wrap.appendChild(btn);

    btn.onclick = async () => {
      const name = nameIn.value, value = valueIn.value;
      btn.disabled = true; err.style.display = 'none';
      // Copy first: the token depends only on the name, and a copy started after the
      // save round-trip has lost the click (Safari refuses it).
      const [copied, saved] = await Promise.all([
        copyText(secretToken(name)),
        postJson('secret-put', { name: name, value: value }).catch(() => null),
      ]);
      btn.disabled = false;
      const problem = !saved || saved.error || saved.ok === false
        ? 'Not saved' + (saved && saved.error ? ': ' + saved.error : '')
        : copied ? '' : 'Saved, but the clipboard refused the copy — press again';
      if (problem) { err.textContent = problem; err.style.display = 'block'; return; }
      done();
    };

    nameIn.focus();
  }

  // Terminal: a modal (Esc, backdrop click or ✕ closes it).
  function openSecretDrop() {
    const { ov, box } = makeModal(24, 'padding:16px;width:100%;max-width:440px;max-height:92vh;overflow:auto;');
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      document.removeEventListener('keydown', onKey);
      try { document.body.removeChild(ov); } catch (e) {}
      refocus();
    };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(); });
    ov.appendChild(box); document.body.appendChild(ov);
    renderSecretUI(box, close, () => { close(); flash('Path copied · deleted after 30 min', 2200); });
  }

  // secret.html: the form is the page; the embedder closes it.
  function renderSecretDrop(host, done) { renderSecretUI(host, null, done); }

  return { openSecretDrop: openSecretDrop, renderSecretDrop: renderSecretDrop };
};
