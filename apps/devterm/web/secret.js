'use strict';
// secret.html: the secret drop form on its own page — no terminal, no ttyd connection.
(function () {
  const statusEl = document.getElementById('status');
  let hideT = null;
  const flash = (text, ms, variant) => {
    statusEl.textContent = text;
    statusEl.classList.toggle('status-error', variant === 'error');
    statusEl.hidden = false;
    clearTimeout(hideT);
    hideT = setTimeout(() => { statusEl.hidden = true; }, ms || 2000);
  };
  const postJson = (url, body) => fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then((r) => r.json());
  // In Porthole's panel the parent closes the frame; opened directly, say it worked.
  const done = window.parent !== window
    ? () => window.parent.postMessage('stationdesk-panel-close', '*')
    : () => flash('Path copied · deleted after 30 min', 2200);
  window.initSecretDrop({ flash: flash, postJson: postJson })
    .renderSecretDrop(document.getElementById('secret'), done);
})();
