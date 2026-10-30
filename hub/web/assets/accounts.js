const t = (en, ko) => window.StationDeskI18n ? window.StationDeskI18n.t(en, ko) : en;
const labels = {claude:'Claude Code',codex:'Codex',opencode:'OpenCode',agy:'agy'};
const states = {logged_in:t('Logged in', '로그인됨'),logged_out:t('Logged out', '로그아웃됨'),unknown:t('Could not check status', '상태 확인 실패'),unsupported:t('Check status in agy', '확인 불가(agy 안에서 확인)'),not_installed:t('Not installed', '설치되지 않음')};
const container = document.querySelector('#accounts');
const message = document.querySelector('#message');
for (const [id, label] of Object.entries(labels)) {
  const row = document.createElement('div'); row.className = 'row'; row.dataset.agent = id;
  const title = document.createElement('strong'); title.textContent = label; row.append(title);
  const state = document.createElement('span'); state.className = 'state'; state.textContent = t('Checking…', '확인 중…'); row.append(state);
  for (const [action, text] of [['login',t('Log in', '로그인')],['logout',t('Log out', '로그아웃')]]) {
    const button = document.createElement('button'); button.textContent = text; button.dataset.action = action;
    button.addEventListener('click', async () => {
      // Reserve the tab during the click so browsers allow the terminal to open.
      const terminal = action === 'login' || id === 'opencode' || id === 'agy' ? window.open('about:blank','_blank') : null;
      button.disabled = true; message.textContent = '';
      try {
        const response = await fetch('/accounts/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:id,action})});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || t('Action failed', '실행 실패'));
        if (data.url) { if (terminal) terminal.location.href = data.url; else location.href = data.url; }
        else if (terminal) terminal.close();
        message.textContent = id === 'agy' && action === 'logout' ? t('Enter /logout in the open agy terminal.', '열린 agy 터미널에서 /logout 을 입력해 주세요.') : data.url ? t('Continue in the open terminal.', '열린 터미널에서 진행해 주세요.') : t('Log out command completed.', '로그아웃 명령을 완료했습니다.');
        await refresh();
      } catch (error) { if (terminal) terminal.close(); message.textContent = error.message; }
      finally { button.disabled = false; }
    }); row.append(button);
  }
  container.append(row);
}
async function refresh() {
  try {
    const response = await fetch('/accounts/status.json',{cache:'no-store'});
    if (!response.ok) throw new Error(t('Could not load account status.', '상태를 불러올 수 없습니다.'));
    for (const agent of (await response.json()).agents) {
      const row = container.querySelector(`[data-agent="${agent.id}"]`); if (!row) continue;
      row.querySelector('.state').textContent = states[agent.status] || states.unknown;
      for (const button of row.querySelectorAll('button')) button.disabled = !agent.installed;
    }
  } catch (error) { message.textContent = error.message; }
}
refresh(); setInterval(refresh,30000); window.addEventListener('focus',refresh);
