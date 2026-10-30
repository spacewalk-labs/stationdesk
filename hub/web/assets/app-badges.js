/* Optional link counts. Only visible home tiles poll; endpoints serve snapshots. */
(function (root) {
  'use strict';
  const t = (en, ko) => root.StationDeskI18n ? root.StationDeskI18n.t(en, ko) : en;
  let stop = function () {};
  const remembered = new Map();
  root.StationDeskAppBadges = {mount: function (home) {
    stop();
    let alive = true, timer = null;
    const entries = Array.from(home.querySelectorAll('[data-badge-url]')).map(function (tile) {
      let url;
      try {
        url = new URL(tile.dataset.badgeUrl);
        if (url.protocol !== 'https:' || url.origin !== new URL(tile.href).origin || url.username || url.password) return null;
      } catch (_) { return null; }
      const badge = document.createElement('span');
      badge.className = 'app-unread'; badge.hidden = true; badge.setAttribute('aria-hidden', 'true');
      tile.appendChild(badge);
      return {tile: tile, badge: badge, url: url.href, label: tile.dataset.appLabel || '', controller: null};
    }).filter(Boolean);
    function paint(entry, value, stale) {
      if (value !== undefined) {
        entry.badge.textContent = value > 99 ? '99+' : String(value);
        entry.badge.hidden = value === 0;
        entry.tile.setAttribute('aria-label', entry.label + t(', unread ', ', 안 읽음 ') + value + t('', '건') + (stale ? t(', stale information', ', 오래된 정보') : ''));
      }
      entry.tile.title = stale ? t('Could not refresh the unread count. Showing previous information.', '안 읽은 건수를 갱신하지 못했습니다. 이전 정보입니다.') : '';
    }
    entries.forEach(function (entry) { const old = remembered.get(entry.url); if (old) paint(entry, old.count, true); });
    async function update(entry) {
      if (!alive || document.hidden || entry.tile.hidden) return;
      if (entry.controller) {
        // A tab can become visible before its cancelled fetch has settled.
        if (entry.controller.signal.aborted) entry.resume = true;
        return;
      }
      const controller = new AbortController(); entry.controller = controller;
      const timeout = root.setTimeout(function () { controller.abort(); }, 10000);
      try {
        const response = await root.fetch(entry.url, {method: 'GET', credentials: 'omit', cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw new Error('status');
        const value = await response.json();
        if (!Number.isSafeInteger(value.unread_count) || value.unread_count < 0 || typeof value.stale !== 'boolean') throw new Error('count');
        if (!alive || document.hidden) return;
        // A stale snapshot cannot replace a count already seen in this page.
        const old = remembered.get(entry.url);
        if (!value.stale || !old) remembered.set(entry.url, {count: value.unread_count});
        paint(entry, remembered.get(entry.url).count, value.stale);
      } catch (_) {
        if (alive) { const old = remembered.get(entry.url); paint(entry, old && old.count, true); }
      } finally {
        root.clearTimeout(timeout);
        if (entry.controller === controller) entry.controller = null;
        if (entry.resume) { entry.resume = false; update(entry); }
      }
    }
    function refresh() {
      if (timer !== null) root.clearTimeout(timer);
      timer = null;
      if (!alive || document.hidden) { entries.forEach(function (e) { if (e.controller) e.controller.abort(); }); return; }
      entries.forEach(update);
      if (entries.length) timer = root.setTimeout(refresh, 60000);
    }
    document.addEventListener('visibilitychange', refresh);
    root.addEventListener('focus', refresh);
    stop = function () {
      alive = false; if (timer !== null) root.clearTimeout(timer);
      document.removeEventListener('visibilitychange', refresh); root.removeEventListener('focus', refresh);
      entries.forEach(function (e) { if (e.controller) e.controller.abort(); e.badge.remove(); });
    };
    refresh();
  }};
})(window);
