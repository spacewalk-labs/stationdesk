// Agent accounts: one implementation, three hosts — the hub settings sheet, /accounts/
// and the Porthole panel (/accounts/?agent=<id>&do=login|logout). The backend runs each
// vendor's official login out of sight; this sheet shows only its link, its device code
// or a field for what it asks back. Row wording comes from porthole.js
// (window.StationDeskAccountText), which every host loads.
(() => {
  const t = (en, ko) => window.StationDeskI18n ? window.StationDeskI18n.t(en, ko) : en;
  const text = () => window.StationDeskAccountText;
  // [id, name, tile letters] from porthole.js, read when drawn (the hosts load it later).
  const agent = (id) => text().agents.find((entry) => entry[0] === id);
  const PROVIDERS = [['anthropic', 'Anthropic'], ['openai', 'OpenAI'], ['google', 'Google'], ['openrouter', 'OpenRouter']];
  const POLL_MS = 2500;
  let last = null;           // the latest /accounts/status.json
  const lists = new Set();

  const el = (tag, cls, content) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (content != null) node.textContent = content;
    return node;
  };
  const button = (label, cls, onClick) => {
    const node = el('button', 'acc-btn' + (cls ? ' ' + cls : ''), label);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  };
  const tile = (id) => el('div', 'acc-tile', agent(id)[2]);

  async function load() {
    const response = await fetch('/accounts/status.json', {cache: 'no-store'});
    if (!response.ok) throw new Error(t('Could not load account status.', '계정 상태를 불러오지 못했습니다.'));
    last = await response.json();
    for (const list of lists) list.render();
    return last;
  }
  const row = (id) => (last && last.agents.find((agent) => agent.id === id)) || {id, status: 'unknown'};

  async function act(agent, action, value) {
    const response = await fetch('/accounts/action', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(value === undefined ? {agent, action} : {agent, action, value})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || t('Action failed', '실행하지 못했습니다'));
    return data;
  }

  // A list of the four agents. `summary` receives a short count for a section header.
  function mountList(container, {summary} = {}) {
    let confirming = null;
    const ul = el('ul', 'acc-list');
    const message = el('p', 'acc-msg');
    message.setAttribute('role', 'status');
    container.replaceChildren(ul, message);
    const list = {render() {
      if (!last) return;
      ul.replaceChildren(...text().agents.map(([id, name]) => {
        const r = row(id);
        const d = text().describe(r, last.known);
        const li = el('li', 'acc-row');
        li.dataset.agent = id;
        li.dataset.tone = d.tone;
        if (r.status === 'not_installed') li.dataset.off = '';
        const body = el('div', 'acc-txt');
        body.append(el('div', 'acc-name', name),
          el('div', 'acc-sub', confirming === id ? t('Signs out on this box only', '이 박스에서만 로그아웃합니다') : d.text));
        const acts = el('div', 'acc-acts');
        if (confirming === id) {
          li.dataset.confirm = '';
          acts.append(button(t('Cancel', '취소'), 'acc-ghost', () => { confirming = null; list.render(); }),
            button(t('Log out', '로그아웃'), 'acc-danger', () => { confirming = null; logout(id, message); }));
        } else {
          // agy cannot report its state, so it offers both.
          if (r.installed && r.status !== 'logged_in') acts.append(button(t('Log in', '로그인'), 'acc-go', () => openSheet(id, 'login')));
          if (r.status === 'logged_in' || r.status === 'unsupported') acts.append(button(t('Log out', '로그아웃'), '', () => { confirming = id; list.render(); }));
        }
        li.append(tile(id), body, acts);
        return li;
      }));
      if (summary) {
        const sum = text().summary(last);
        summary.textContent = sum.text;
        summary.dataset.alert = String(sum.alert);
      }
    }};
    lists.add(list);
    if (last) list.render();
    else load().catch((error) => { message.textContent = error.message; });
    return list;
  }

  // Signing out after the row's own confirmation. OpenCode keeps one credential per
  // provider, so more than one opens the sheet to pick which.
  async function logout(id, message) {
    message.textContent = '';
    const providers = row(id).providers || [];
    if (id === 'opencode' && providers.length !== 1) return openSheet(id, 'logout');
    try {
      await act(id, 'logout', id === 'opencode' ? providers[0] : undefined);
      await load();
    } catch (error) { message.textContent = error.message; }
  }

  // The sheet is modal: the rest of the page is inert while it is open, and focus
  // returns to the row that opened it.
  let sheet = null;
  function closeSheet(replacing) {
    if (!sheet) return;
    clearInterval(sheet.timer);
    if (sheet.live) act(sheet.id, 'cancel').catch(() => {});   // stop an unfinished sign-in
    sheet.node.remove();
    for (const node of sheet.inert) node.inert = false;
    dispatchEvent(new Event('stationdesk:accounts-changed'));
    if (replacing !== true) {
      const back = sheet.opener && sheet.opener.isConnected ? sheet.opener
        : document.querySelector(`.acc-row[data-agent="${sheet.id}"] button`);
      if (back) back.focus();
      if (document.documentElement.classList.contains('acc-embedded')) parent.postMessage('stationdesk-panel-close', '*');
    }
    sheet = null;
  }

  function openSheet(id, operation) {
    if (!agent(id) || (operation !== 'login' && operation !== 'logout')) return;
    const opener = document.activeElement;
    closeSheet(true);
    const name = agent(id)[1];
    const veil = el('div', 'acc-veil');
    const box = el('section', 'acc-sheet');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    const head = el('div', 'acc-head');
    const title = el('h2', '', operation === 'login' ? t('Sign in to ' + name, name + ' 로그인') : t('Sign out of ' + name, name + ' 로그아웃'));
    title.id = 'acc-sheet-title';
    box.setAttribute('aria-labelledby', title.id);
    const x = el('button', 'acc-x', '✕');
    x.type = 'button';
    x.setAttribute('aria-label', t('Close', '닫기'));
    x.addEventListener('click', () => closeSheet());
    head.append(tile(id), title, x);
    const body = el('div', 'acc-body');
    box.append(head, body);
    veil.append(box);
    veil.addEventListener('click', (event) => { if (event.target === veil) closeSheet(); });
    const inert = [...document.body.children].filter((node) => !node.inert);
    for (const node of inert) node.inert = true;
    document.body.append(veil);
    sheet = {node: veil, timer: null, inert, opener, id, live: false};
    const current = sheet;
    const alive = () => sheet === current;
    x.focus();

    const show = (...nodes) => { if (alive()) body.replaceChildren(...nodes); };
    const foot = (...nodes) => {
      const node = el('div', 'acc-foot');
      node.append(el('span', ''), button(t('Cancel', '취소'), 'acc-ghost', () => closeSheet()), ...nodes);
      return node;
    };
    const busy = (note) => {
      const node = el('div', 'acc-foot');
      node.append(el('div', 'acc-spin'), el('span', '', note), button(t('Cancel', '취소'), 'acc-ghost', () => closeSheet()));
      return node;
    };
    const step = (n, label) => {
      const node = el('div', 'acc-step');
      node.append(el('b', '', String(n)), el('span', '', label));
      return node;
    };
    const fail = (error, retry) => {
      current.live = false;
      clearInterval(current.timer);
      act(id, 'cancel').catch(() => {});
      show(el('p', 'acc-error', error.message), foot(button(t('Try again', '다시 시도'), 'acc-go', retry)));
    };
    const done = (headline, detail) => {
      current.live = false;
      clearInterval(current.timer);
      const ok = el('div', 'acc-ok');
      ok.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
      const wrap = el('div', 'acc-done');
      const finish = button(t('Done', '완료'), 'acc-go', () => closeSheet());
      wrap.append(ok, el('h3', '', headline), el('p', '', detail || ''), finish);
      show(wrap);
      if (alive()) finish.focus();
    };
    // Poll until `check` says the sign-in finished; it throws when the sign-in gave up.
    const watch = (check) => {
      clearInterval(current.timer);
      current.timer = setInterval(async () => {
        try {
          if (alive() && await check()) done(t('Signed in', '로그인되었습니다'), id === 'agy' ? 'agy' : text().identity(row(id)));
        } catch (error) { if (alive()) fail(error, () => openSheet(id, 'login')); }
      }, POLL_MS);
    };
    // The vendor's status agrees. OpenCode instead finishes when its command has ended
    // and the chosen provider is stored, which covers replacing a key as well as adding one.
    const statusIn = (provider) => async () => {
      // Ask whether the command still runs before reading status, so an ended command
      // is always judged on the status it left behind.
      const running = (await act(id, 'check')).running;
      await load();
      const r = row(id);
      if (provider == null ? r.status === 'logged_in'
        : !running && (r.providers || []).some((stored) => stored.toLowerCase() === provider.toLowerCase())) return true;
      if (!running)
        throw new Error(t('The sign-in ended without signing in. Check the code and try again.', '로그인되지 않은 채 끝났습니다. 코드를 확인하고 다시 시도하세요.'));
      return false;
    };
    // agy has no status command: signed in once it stays running past the code prompt.
    const agyIn = async () => {
      const state = await act(id, 'check');
      if (!state.running) throw new Error(t('agy stopped without signing in. Check the code and try again.', 'agy 가 로그인하지 못하고 종료됐습니다. 코드를 확인하고 다시 시도하세요.'));
      return !state.waiting;
    };
    const link = (url, label) => {
      const node = el('a', 'acc-btn acc-go acc-open', label);
      node.href = url;
      node.target = '_blank';
      node.rel = 'noopener noreferrer';
      return node;
    };
    const field = (placeholder, type) => {
      const input = el('input', 'acc-input');
      input.type = type || 'text';
      input.placeholder = placeholder;
      input.autocomplete = 'off';
      input.spellcheck = false;
      return input;
    };
    const onEnter = (input, send) => input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); send(); }
    });

    // A host that has not read the status yet (Dev Monitor) must not mistake that for none.
    const route = () => {
      if (!alive()) return;
      if (operation === 'logout') showLogout();
      else if (id === 'opencode') showKeyForm();
      else start();
    };
    if (last) route();
    else {
      show(busy(t('Checking accounts…', '계정을 확인하는 중…')));
      load().then(route, (error) => fail(error, () => openSheet(id, operation)));
    }

    // Claude and agy: open the approval page, paste back the code it shows. Codex: show
    // its device code; approving it in the browser finishes the sign-in by itself.
    function start() {
      show(busy(t('Preparing the sign-in link…', '로그인 링크를 준비하는 중…')));
      current.live = true;
      act(id, 'login').then((flow) => {
        if (!alive()) return;
        if (id === 'codex') showDeviceCode(flow);
        else showPasteCode(flow);
      }).catch((error) => fail(error, start));
    }
    function showDeviceCode(flow) {
      const code = el('button', 'acc-code', flow.code);
      code.type = 'button';
      code.title = t('Copy', '복사');
      code.addEventListener('click', () => {
        if (navigator.clipboard) navigator.clipboard.writeText(flow.code).then(() => { code.dataset.copied = ''; }, () => {});
      });
      show(step(1, t('Open the OpenAI page and sign in.', 'OpenAI 페이지를 열어 로그인하세요.')),
        link(flow.url, t('Open OpenAI page', 'OpenAI 페이지 열기')),
        step(2, t('Enter this code there.', '이 코드를 입력하세요.')), code,
        busy(t('Finishes on its own once approved', '승인하면 자동으로 완료됩니다')));
      watch(statusIn(null));
    }
    function showPasteCode(flow) {
      const site = id === 'agy' ? 'Google' : 'Claude';
      const input = field(t('Paste the code', '코드 붙여 넣기'));
      const entry = el('div', 'acc-entry');
      entry.append(input, button(t('Sign in', '로그인'), 'acc-go', send));
      onEnter(input, send);
      show(step(1, t('Open the ' + site + ' page and approve.', site + ' 페이지를 열어 승인하세요.')),
        link(flow.url, t('Open ' + site + ' page', site + ' 페이지 열기')),
        step(2, t('Paste the code it shows.', '화면에 나온 코드를 붙여 넣으세요.')), entry, foot());
      function send() {
        const value = input.value.trim();
        if (!value) return input.focus();
        show(busy(t('Checking…', '확인하는 중…')));
        act(id, 'submit', value).then(() => {
          // agy has no status command: it is done once it stops asking for a code.
          watch(id === 'agy' ? agyIn : statusIn(null));
        }).catch((error) => fail(error, start));
      }
    }
    // OpenCode: the provider's API key, typed into `opencode auth login` by the backend.
    function showKeyForm() {
      const select = el('select', 'acc-input');
      for (const [value, label] of PROVIDERS) select.append(Object.assign(el('option', '', label), {value}));
      const key = field(t('API key', 'API 키'), 'password');
      onEnter(key, send);
      show(step(1, t('Choose the provider.', '제공자를 고르세요.')), select,
        step(2, t('Paste its API key.', 'API 키를 붙여 넣으세요.')), key, foot(button(t('Save', '저장'), 'acc-go', send)));
      select.focus();
      function send() {
        const value = key.value.trim();
        if (!value) return key.focus();
        const provider = select.selectedOptions[0].textContent;
        show(busy(t('Saving…', '저장하는 중…')));
        current.live = true;
        act(id, 'login', select.value).then(() => act(id, 'submit', value))
          .then(() => watch(statusIn(provider))).catch((error) => fail(error, showKeyForm));
      }
    }
    // Signing out always takes a click here, even when the Porthole opened this page.
    function showLogout() {
      const providers = id === 'opencode' ? row(id).providers || [] : [null];
      if (!providers.length) return show(el('p', '', t('No stored API keys.', '저장된 API 키가 없습니다.')), foot());
      const go = (provider) => () => {
        show(busy(t('Signing out…', '로그아웃하는 중…')));
        act(id, 'logout', provider == null ? undefined : provider).then(load).then(() => {
          if ((id === 'claude' || id === 'codex') && row(id).status !== 'logged_out')
            throw new Error(t(name + ' still reports a signed-in account.', name + ' 이 아직 로그인 상태로 보고합니다.'));
          done(t('Signed out', '로그아웃되었습니다'), provider || name);
        }).catch((error) => fail(error, showLogout));
      };
      const choices = el('div', 'acc-choices');
      for (const provider of providers) {
        choices.append(button(provider ? t('Remove ' + provider, provider + ' 키 삭제') : t('Log out', '로그아웃'), 'acc-danger', go(provider)));
      }
      show(el('p', '', id === 'opencode' ? t('Remove a stored API key from this box.', '이 박스에 저장된 API 키를 삭제합니다.')
        : t('Signs out of ' + name + ' on this box only.', '이 박스에서만 ' + name + ' 로그아웃합니다.')), choices, foot());
    }
  }
  // Escape closes only the sheet, not the settings sheet or Porthole menu under it.
  addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sheet) { event.stopPropagation(); closeSheet(); }
  }, true);

  setInterval(() => { if (lists.size && !sheet && !document.hidden) load().catch(() => {}); }, 30000);
  addEventListener('focus', () => { if (lists.size) load().catch(() => {}); });

  window.StationDeskAccounts = {mountList, openSheet, load};
})();
