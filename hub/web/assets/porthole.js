/* Porthole ported from Airlock hub/assets/airlock-return.js.
 * Floating/corner/native placement, pointer drag, persisted position, menu and Peek
 * retain the source behavior. StationDesk supplies its O3 mark and API routes.
 */
(function () {
  "use strict";
  function t(en, ko) {
    if (window.StationDeskI18n) return window.StationDeskI18n.t(en, ko);
    try { return localStorage.getItem("stationdesk-language") === "ko" ? ko : en; }
    catch (_) { return en; }
  }
  var ID = "stationdesk-porthole";
  if (window.top !== window.self) return;          // hidden inside iframes/previews
  if (document.getElementById(ID)) return;
  var mode = "floating";
  var targetSelector = "#stationdesk-slot";
  var anchor = "bottom-left";
  var wantMenu = true;
  var accountBase = "";
  var secretBase = "";
  function baseUrl(v) { return String(v).replace(/\/+$/, "") + "/"; }
  var wantBadge = true;
  var peekNormalMs = 15000, peekUrgentMs = 90000;
  try {
    var self = document.currentScript ||
      document.querySelector('script[src$="/porthole.js"], script[src="/stationdesk-porthole.js"]');
    if (self && self.dataset) {
      if (self.dataset.mode === "corner" || self.dataset.mode === "native") mode = self.dataset.mode;
      if (self.dataset.target) targetSelector = self.dataset.target;
      if (self.dataset.anchor === "bottom-right") anchor = "bottom-right";
      if (self.dataset.menu === "1") wantMenu = true;
      if (self.dataset.accountPanel) accountBase = baseUrl(self.dataset.accountPanel);
      if (self.dataset.secretPanel) secretBase = baseUrl(self.dataset.secretPanel);
      if (self.dataset.badge === "0") wantBadge = false;
      peekNormalMs = stationdeskPeekDuration(self.dataset.peekNormalMs, 15000);
      peekUrgentMs = stationdeskPeekDuration(self.dataset.peekUrgentMs, 90000, 90000);
    }
  } catch (e) {}
  var useMenu = wantMenu;
  var HUB = baseUrl(self && self.dataset.hubUrl || location.origin);
  accountBase = accountBase || HUB + "accounts/";
  secretBase = secretBase || HUB + "devterm/?secret=1";
  var UNREAD_URL = "/monitor/api/owner/messages/preview";
  var ALERT_URL = wantBadge ? "/accounts/status.json" : "";
  var POLL_MS = 30000;
  var POS_KEY = "stationdesk:btn-pos-v1";
  function stationdeskResolveSlot(doc, selector) {
    try { return doc.querySelector(selector); } catch (_) { return null; }
  }
  function stationdeskIsCloseMessage(data) { return data === "stationdesk-panel-close"; }
  function stationdeskPeekDuration(value, fallback, cap) {
    var ms = Number(value);
    return Number.isFinite(ms) && ms > 0 ? Math.min(ms, cap || Infinity) : fallback;
  }
  function stationdeskPeekCompare(a, b) {
    if (a.last_at !== b.last_at) return a.last_at < b.last_at ? -1 : 1;
    return a.card_id === b.card_id ? 0 : a.card_id < b.card_id ? -1 : 1;
  }
  function stationdeskSelectPeeks(messages, seen, pending) {
    if (!seen) {
      seen = { last_at: "", card_id: "" };
      messages.forEach(function (card) {
        if (stationdeskPeekCompare(card, seen) > 0) seen = { last_at: card.last_at, card_id: card.card_id };
      });
      return { seen: seen, cards: [] };
    }
    var cards = pending.slice();
    messages.forEach(function (card) {
      if (card.peek !== true || card.read_at || stationdeskPeekCompare(card, seen) <= 0) return;
      if (!cards.some(function (queued) { return stationdeskPeekCompare(card, queued) === 0; })) cards.push(card);
    });
    cards.sort(function (a, b) {
      return Number(b.level === "urgent") - Number(a.level === "urgent") || stationdeskPeekCompare(a, b);
    });
    return { seen: seen, cards: cards };
  }
  var FLOAT_SIZE = 44, CORNER_SIZE = 40, NATIVE_SIZE = 30, EDGE = 8, DRAG = 6;
  var BADGE_OVERHANG = 5;
  var SIZE = mode === "corner" ? CORNER_SIZE : mode === "native" ? NATIVE_SIZE : FLOAT_SIZE;

  var btn = document.createElement("button");
  btn.id = ID;
  btn.type = "button";
  btn.title = t("Porthole — messages and tools", "포트홀 — 메시지와 도구");
  btn.setAttribute("aria-label", t("Porthole — messages and tools", "포트홀 — 메시지와 도구"));
  var S = btn.style;                                 // inline styles = isolated from app CSS
  S.position = "fixed";
  S.zIndex = "2147483000";
  S.width = S.height = SIZE + "px";
  S.margin = S.padding = "0";
  S.border = "none";
  S.cursor = "pointer";
  S.display = "flex";
  S.alignItems = S.justifyContent = "center";
  S.boxShadow = "0 2px 10px rgba(0,0,0,.35), 0 0 0 1px rgba(255,255,255,.14)";
  S.opacity = "0.9";
  S.touchAction = "none";
  S.webkitTapHighlightColor = "transparent";
  S.webkitUserSelect = S.userSelect = "none";
  S.transition = "opacity .15s ease";
  S.background = "#1F2937";
  S.color = "#F5F0E8";

  if (mode === "native") {
    S.borderRadius = "8px";
    S.position = "relative";
    S.zIndex = "auto";
    S.boxShadow = "none";
  } else if (mode === "corner") {
    S.borderRadius = "11px";
  } else {
    S.borderRadius = "12px";
  }
  var lg = document.createElement("span");
  lg.setAttribute("aria-hidden", "true");
  lg.style.cssText = "display:block;width:32px;height:32px;--bg:#1F2937;--accent:#E4572E";
  lg.innerHTML = `<svg viewBox="0 0 64 64" aria-hidden="true"><defs><clipPath id="stationdesk-porthole-front"><rect x="-10" y="32" width="84" height="40"/></clipPath></defs>
  <g transform="rotate(-14 32 32)"><ellipse cx="32" cy="32" rx="30" ry="9" fill="none" stroke="currentColor" stroke-width="3"/><circle class="moving" r="3.6" fill="var(--accent)"/></g>
  <rect x="11" y="15" width="42" height="34" rx="6" fill="var(--bg)" stroke="currentColor" stroke-width="4"/><rect x="17" y="21" width="16" height="22" rx="2" fill="currentColor"/><path d="M20.5 27.5 L25 32 L20.5 36.5" fill="none" stroke="var(--bg)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><rect x="37" y="21" width="10" height="9.5" rx="2" fill="currentColor"/><rect x="37" y="33.5" width="10" height="9.5" rx="2" fill="currentColor"/>
  <g transform="rotate(-14 32 32)"><path d="M2 32 A30 9 0 0 0 62 32" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><circle class="still" cx="58.2" cy="36.2" r="4.2" fill="var(--accent)" stroke="var(--bg)" stroke-width="2" paint-order="stroke"/><g clip-path="url(#stationdesk-porthole-front)"><circle class="moving" r="3.6" fill="var(--accent)" stroke="var(--bg)" stroke-width="2" paint-order="stroke"/></g></g></svg>`;
  lg.querySelector('svg').style.cssText = "width:100%;height:100%;overflow:visible";
  var movingDots = lg.querySelectorAll('.moving'), stillDot = lg.querySelector('.still');
  movingDots.forEach(function(dot) {
    dot.style.cssText = "display:none;offset-path:path('M2 32 A30 9 0 0 0 62 32 A30 9 0 0 0 2 32')";
  });
  var orbitAnimations = [], orbitDuration = 0;
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  function motion() {
    var duration = reducedMotion.matches || !lg.animate ? 0 : alertLevel === 'crit' ? 2000 : (alertLevel === 'warn' || peekEl) ? 6000 : 0;
    if (duration === orbitDuration) return;
    orbitDuration = duration;
    orbitAnimations.forEach(function(animation) { animation.cancel(); });
    orbitAnimations = [];
    stillDot.style.display = duration ? 'none' : '';
    movingDots.forEach(function(dot) {
      dot.style.display = duration ? '' : 'none';
      if (duration && dot.animate) orbitAnimations.push(dot.animate([{offsetDistance:'0%'},{offsetDistance:'100%'}], {duration:duration,iterations:Infinity}));
    });
  }
  reducedMotion.addEventListener('change', motion);
  btn.appendChild(lg);

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  var safeArea = { top: 0, right: 0, bottom: 0, left: 0 };
  function readSafeArea() {
    var probe = document.createElement("div");
    probe.style.cssText = "position:fixed;inset:0;visibility:hidden;pointer-events:none;" +
      "padding-top:env(safe-area-inset-top,0px);" +
      "padding-right:env(safe-area-inset-right,0px);" +
      "padding-bottom:env(safe-area-inset-bottom,0px);" +
      "padding-left:env(safe-area-inset-left,0px);";
    var root = document.body || document.documentElement;
    if (!root) return;
    try {
      root.appendChild(probe);
      var cs = window.getComputedStyle(probe);
      safeArea.top = parseFloat(cs.paddingTop) || 0;
      safeArea.right = parseFloat(cs.paddingRight) || 0;
      safeArea.bottom = parseFloat(cs.paddingBottom) || 0;
      safeArea.left = parseFloat(cs.paddingLeft) || 0;
    } catch (e) {
      safeArea = { top: 0, right: 0, bottom: 0, left: 0 };
    } finally {
      if (probe.parent) probe.remove();
      else if (probe.parentNode) probe.parentNode.removeChild(probe);
    }
  }
  function applyXY(x, y) {
    var minX = safeArea.left + EDGE;
    var minY = safeArea.top + EDGE + BADGE_OVERHANG;
    var maxX = window.innerWidth - safeArea.right - SIZE - EDGE - BADGE_OVERHANG;
    var maxY = window.innerHeight - safeArea.bottom - SIZE - EDGE;
    S.left = clamp(x, minX, maxX) + "px";
    S.top = clamp(y, minY, maxY) + "px";
    S.right = S.bottom = "auto";
    positionPeek();
  }
  function anchorCorner() {                            // corner = top-left (respect safe-area)
    S.left = "calc(env(safe-area-inset-left, 0px) + 12px)";
    S.top = "calc(env(safe-area-inset-top, 0px) + 12px)";
    S.right = S.bottom = "auto";
  }
  function anchorFloatDefault() {                      // floating with no saved pos
    if (anchor === "bottom-right") {
      S.right = "calc(env(safe-area-inset-right, 0px) + 14px)";
      S.left = "auto";
    } else {
      S.left = "calc(env(safe-area-inset-left, 0px) + 14px)";
      S.right = "auto";
    }
    S.bottom = "calc(env(safe-area-inset-bottom, 0px) + 20px)";
    S.top = "auto";
  }
  readSafeArea();
  var placed = false;                                // positioned via top/left (dragged/restored)
  if (mode === "corner") {
    anchorCorner();
  } else if (mode === "floating") {
    (function restore() {
      var p = null;
      try {
        var raw = localStorage.getItem(POS_KEY);
        p = JSON.parse(raw || "null");
      } catch (e) {}
      if (p && typeof p.x === "number" && typeof p.y === "number") { applyXY(p.x, p.y); placed = true; }
      else anchorFloatDefault();
    })();
  }
  var badge = document.createElement("span");
  var B = badge.style;
  B.position = "absolute";
  B.top = "-5px";
  B.right = "-5px";
  B.minWidth = "18px";
  B.height = "18px";
  B.boxSizing = "border-box";
  B.padding = "0 5px";
  B.borderRadius = "9px";
  B.background = "#6b7280";                           // grey circle (mark is light on green)
  B.color = "#fff";
  B.font = "700 11px/18px -apple-system, system-ui, sans-serif";
  B.textAlign = "center";
  B.letterSpacing = "0";
  B.boxShadow = "0 0 0 2px rgba(0,0,0,.28)";
  B.pointerEvents = "none";
  B.display = "none";                                 // hidden by default (0 / non-owner / error)
  if (mode === "native") {
    B.top = B.right = "-4px";
    B.minWidth = B.height = "15px";
    B.padding = "0 4px";
    B.borderRadius = "8px";
    B.font = "700 9.5px/15px -apple-system, system-ui, sans-serif";
  }
  badge.setAttribute("aria-hidden", "true");
  btn.appendChild(badge);

  var unreadCount = 0;
  function setUnread(n) {
    unreadCount = Math.max(0, Number(n) || 0);
    n = unreadCount;
    if (n > 0) {
      badge.textContent = n > 99 ? "99+" : String(n);
      badge.style.display = "block";
      btn.setAttribute("aria-label", t("Porthole, " + n + " needs action", "포트홀, 확인할 메시지 " + n + "개"));
    } else {
      badge.style.display = "none";
      btn.setAttribute("aria-label", t("Porthole — messages and tools", "포트홀 — 메시지와 도구"));
    }
  }
  function pollUnread() {
    fetch(UNREAD_URL, { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403 || r.status === 404) { setUnread(0); return null; }
        if (!r.ok) return undefined;                  // unexpected status — see below
        return r.json();
      })
      .then(function (d) {
        if (d === null) return;                       // handled above (403/404)
        if (d === undefined) return;                  // unexpected status — keep last known
        setUnread(d && (d.counts ? d.counts.unread : d.unread_count) || 0);
        window.dispatchEvent(new CustomEvent("stationdesk:messages", {detail:d}));
        if (d && Array.isArray(d.messages)) receivePeeks(d.messages);
      })
      .catch(function () {});                          // network / bad JSON — keep last known
  }
  var PEEK_KEY = "stationdesk:peek-seen-v1";
  var PEEK_WIDTH = 420;                               // desktop width; max-width shrinks it on phones
  var peekSeen = null, peekQueue = [], peekEl = null, peekTimer = null;
  try {
    var savedPeek = JSON.parse(localStorage.getItem(PEEK_KEY));
    if (savedPeek && typeof savedPeek.last_at === "string" && typeof savedPeek.card_id === "string") peekSeen = savedPeek;
  } catch (e) {}
  function savePeekSeen(seen) {
    peekSeen = { last_at: seen.last_at, card_id: seen.card_id };
    try { localStorage.setItem(PEEK_KEY, JSON.stringify(peekSeen)); } catch (e) {}
  }
  function receivePeeks(messages) {
    var next = stationdeskSelectPeeks(messages, peekSeen, peekQueue);
    if (!peekSeen) savePeekSeen(next.seen);
    peekQueue = next.cards;
    showNextPeek();
  }
  function positionPeek() {
    if (!peekEl) return;
    var r = btn.getBoundingClientRect();
    var minX = safeArea.left + EDGE, minY = safeArea.top + EDGE;
    var maxX = window.innerWidth - safeArea.right - EDGE - peekEl.offsetWidth;
    var maxY = window.innerHeight - safeArea.bottom - EDGE - peekEl.offsetHeight;
    var top = r.top - peekEl.offsetHeight - EDGE;
    if (top < minY) top = r.bottom + EDGE;
    peekEl.style.left = clamp(r.left, minX, maxX) + "px";
    peekEl.style.top = clamp(top, minY, maxY) + "px";
  }
  function closePeek() {
    clearTimeout(peekTimer);
    peekTimer = null;
    if (peekEl) { peekEl.remove(); peekEl = null; }
    motion();
    showNextPeek();
  }
  function showNextPeek() {
    if (peekEl || !peekQueue.length) return;
    var card = peekQueue.shift(), urgent = card.level === "urgent";
    var duration = urgent ? peekUrgentMs : peekNormalMs;
    var bubble = document.createElement("div");
    bubble.setAttribute("aria-label", urgent ? "Urgent Peek" : "Peek");
    bubble.style.cssText = "position:fixed;z-index:2147483050;display:flex;align-items:center;" +
      "box-sizing:border-box;width:" + PEEK_WIDTH + "px;max-width:calc(100vw - 16px - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px));" +
      "background:#202431;border:1px solid #3a4254;border-left:4px solid #E4572E;border-radius:10px;overflow:hidden;" +
      "box-shadow:0 8px 24px rgba(0,0,0,.4);" + (urgent ? "border-left:4px solid #e05a5a;" : "");
    var content = menuRow(String(card.title || "").split(/\r?\n/)[0],
      String(card.body || "").replace(/\s+/g, " ").trim(), function () { closePeek(); openInbox(); });
    content.style.flex = "1";
    content.style.minWidth = "0";
    content.style.width = "auto";
    if (content.children[0]) {
      content.children[0].style.cssText += ";white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
    }
    if (content.children[1]) {
      content.children[1].style.cssText += ";color:#c3c9d4;display:-webkit-box;-webkit-box-orient:vertical;" +
        "-webkit-line-clamp:2;line-clamp:2;overflow:hidden;";
    }
    var x = document.createElement("button");
    x.type = "button"; x.textContent = "✕"; x.setAttribute("aria-label", t("Close Peek", "미리보기 닫기"));
    x.style.cssText = "flex:0 0 36px;height:36px;margin:4px;border:0;border-radius:8px;" +
      "background:#3a4254;color:#fff;font:16px/1 system-ui,sans-serif;cursor:pointer;";
    x.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); closePeek(); });
    bubble.appendChild(content); bubble.appendChild(x);
    var progress = null;
    if (urgent) {
      progress = document.createElement("div");
      progress.setAttribute("aria-hidden", "true");
      progress.style.cssText = "position:absolute;bottom:0;left:0;right:0;height:3px;background:#e05a5a;transform-origin:left;";
      bubble.appendChild(progress);
    }
    (document.body || document.documentElement).appendChild(bubble);
    peekEl = bubble;
    motion();
    positionPeek();
    if (stationdeskPeekCompare(card, peekSeen) > 0) savePeekSeen(card);
    peekTimer = setTimeout(closePeek, duration);
    if (progress && progress.animate) progress.animate([{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }],
      { duration: duration, fill: "forwards" });
  }
  window.addEventListener("resize", function () { readSafeArea(); positionPeek(); });
  window.addEventListener("scroll", positionPeek, true);
  var RING_BASE = S.boxShadow;
  var alertLevel = "", alertInfo = null, blinkTimer = null, blinkOn = false;
  function paintRing() {
    if (blinkTimer) { clearInterval(blinkTimer); blinkTimer = null; }
    if (alertLevel === "warn") {
      S.boxShadow = RING_BASE + ", 0 0 0 3px #e6b34d, 0 0 12px rgba(230,179,77,.65)";
    } else if (alertLevel === "crit") {
      blinkOn = true;
      var flip = function () {
        S.boxShadow = blinkOn
          ? RING_BASE + ", 0 0 0 3px #e05a5a, 0 0 14px rgba(224,90,90,.8)"
          : RING_BASE + ", 0 0 0 3px rgba(224,90,90,.35)";
        blinkOn = !blinkOn;
      };
      flip();
      blinkTimer = setInterval(flip, 700);
    } else {
      S.boxShadow = RING_BASE;
    }
    motion();
  }
  function pollAlert() {
    if (!ALERT_URL) return;
    fetch(ALERT_URL, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (a) {
        alertInfo = a || null;
        alertLevel = a && a.status === "warning" ? "warn" : a && a.status === "critical" ? "crit" : "";
        var login = document.getElementById("agent-login");
        if (login) login.hidden = a.needs_login !== true;
        paintRing();
      })
      .catch(function () { alertInfo = null; alertLevel = ""; paintRing(); });
  }
  var menuEl = null, modalEl = null;
  function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
  function closeModal() { if (modalEl) { modalEl.remove(); modalEl = null; } }
  function menuRow(label, sub, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.style.cssText = "display:block;width:100%;text-align:left;border:none;background:transparent;" +
      "color:#e8ecf4;font:13.5px/1.35 -apple-system,system-ui,sans-serif;padding:11px 14px;cursor:pointer;";
    var t = document.createElement("div"); t.textContent = label; b.appendChild(t);
    if (sub) {
      var sEl = document.createElement("div");
      sEl.textContent = sub;
      sEl.style.cssText = "margin-top:2px;font-size:11.5px;color:#e6b34d;";
      b.appendChild(sEl);
    }
    b.addEventListener("mouseenter", function () { b.style.background = "rgba(255,255,255,.07)"; });
    b.addEventListener("mouseleave", function () { b.style.background = "transparent"; });
    b.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); onClick(); });
    return b;
  }
  function openPanel(which, title, base) {
    closeMenu();
    closeModal();
    var ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;z-index:2147483100;background:rgba(0,0,0,.6);" +
      "display:flex;align-items:center;justify-content:center;" + safePad(16);
    var box = document.createElement("div");
    box.style.cssText = "background:#1b1f29;border:1px solid #3a4254;border-radius:12px;" +
      "box-shadow:0 18px 48px rgba(0,0,0,.5);width:min(420px,100%);height:min(560px,92vh);" +
      "box-sizing:border-box;max-height:100%;display:flex;flex-direction:column;overflow:hidden;";
    var head = document.createElement("div");
    head.style.cssText = "flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:10px 12px;" +
      "border-bottom:1px solid #2c3240;color:#f2f5fa;font:600 14px -apple-system,system-ui,sans-serif;";
    var ttl = document.createElement("span"); ttl.textContent = title; ttl.style.flex = "1";
    var x = document.createElement("button");
    x.type = "button"; x.textContent = "✕";
    x.setAttribute("aria-label", t("Close", "닫기"));
    x.style.cssText = "width:34px;height:30px;border:none;border-radius:8px;background:#3a4254;color:#fff;" +
      "font-size:17px;line-height:1;cursor:pointer;";
    x.addEventListener("click", function (e) { e.preventDefault(); closeModal(); });
    head.appendChild(ttl); head.appendChild(x);
    var frame = document.createElement("iframe");
    frame.src = base;
    frame.setAttribute("allow", "clipboard-write");
    frame.style.cssText = "flex:1 1 auto;width:100%;border:none;background:#1b1f29;";
    var note = document.createElement("div");
    note.style.cssText = "display:none;padding:14px;color:#c3c9d4;font:12.5px/1.6 -apple-system,system-ui,sans-serif;";
    box.appendChild(head); box.appendChild(frame); box.appendChild(note);
    ov.appendChild(box);
    ov.addEventListener("click", function (e) { if (e.target === ov) closeModal(); });
    (document.body || document.documentElement).appendChild(ov);
    modalEl = ov;
    var loaded = false;
    frame.addEventListener("load", function () { loaded = true; });
    setTimeout(function () {
      if (loaded || modalEl !== ov) return;
      frame.style.display = "none";
      note.style.display = "block";
      note.textContent = t("The panel did not load within 6s: ", "6초 안에 패널을 불러오지 못했습니다: ") + frame.src +
        t(" — check that its surface is installed and reachable on that address.", " — 해당 주소에 앱이 설치되어 있고 접속 가능한지 확인하세요.");
    }, 6000);
  }
  function safePad(px) {
    return "padding:max(" + px + "px, env(safe-area-inset-top, 0px)) max(" + px + "px, env(safe-area-inset-right, 0px)) " +
      "max(" + px + "px, env(safe-area-inset-bottom, 0px)) max(" + px + "px, env(safe-area-inset-left, 0px));";
  }
  function openInbox() {
    closeMenu();
    closeModal();
    var ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;z-index:2147483100;background:rgba(0,0,0,.6);" +
      "display:flex;align-items:center;justify-content:center;" + safePad(8);
    var box = document.createElement("div");
    box.style.cssText = "background:#1b1f29;border:1px solid #3a4254;border-radius:12px;" +
      "box-shadow:0 18px 48px rgba(0,0,0,.5);height:88vh;width:min(clamp(360px,44vh,480px),63vh,calc(100vw - 16px));" +
      "box-sizing:border-box;max-height:100%;display:flex;flex-direction:column;overflow:hidden;";
    var head = document.createElement("div");
    head.style.cssText = "flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:10px 12px;" +
      "border-bottom:1px solid #2c3240;color:#f2f5fa;font:600 14px -apple-system,system-ui,sans-serif;";
    var title = document.createElement("span"); title.textContent = t("Inbox", "받은 메시지"); title.style.flex = "1";
    var x = document.createElement("button");
    x.type = "button"; x.textContent = "✕"; x.setAttribute("aria-label", t("Close", "닫기"));
    x.style.cssText = "width:34px;height:30px;border:none;border-radius:8px;background:#3a4254;color:#fff;font-size:17px;line-height:1;cursor:pointer;";
    x.addEventListener("click", function (e) { e.preventDefault(); closeModal(); });
    head.appendChild(title); head.appendChild(x);
    var frame = document.createElement("iframe");
    frame.src = HUB + "hub/inbox.html";
    frame.title = t("Inbox", "받은 메시지");
    frame.style.cssText = "flex:1 1 auto;width:100%;border:none;background:#1b1f29;";
    box.appendChild(head); box.appendChild(frame); ov.appendChild(box);
    ov.addEventListener("click", function (e) { if (e.target === ov) closeModal(); });
    (document.body || document.documentElement).appendChild(ov);
    modalEl = ov;
  }
  function openMenu() {
    if (menuEl) { closeMenu(); return; }
    var m = document.createElement("div");
    m.style.cssText = "position:fixed;z-index:2147483100;min-width:236px;background:#202431;" +
      "border:1px solid #3a4254;border-radius:10px;box-shadow:0 14px 36px rgba(0,0,0,.45);" +
      "padding:5px 0;overflow:hidden;";
    var cap = document.createElement("div");
    cap.textContent = "Porthole";
    cap.style.cssText = "padding:8px 14px 6px;color:#8a93a6;font:600 11px/1.2 -apple-system,system-ui,sans-serif;" +
      "letter-spacing:.06em;text-transform:uppercase;border-bottom:1px solid #2c3240;margin-bottom:4px;";
    m.appendChild(cap);
    m.appendChild(menuRow(t("Go to StationDesk", "StationDesk 홈"), "", function () { closeMenu(); go(); }));
    m.appendChild(menuRow(t("Inbox · " + unreadCount + " unread", "받은 메시지 · 안 읽음 " + unreadCount), "", openInbox));
    if (accountBase) {
      m.appendChild(menuRow(t("Agent accounts", "에이전트 계정"), "", function () {
        openPanel("accounts", t("Agent accounts", "에이전트 계정"), accountBase);
      }));
    }
    if (secretBase) {
      m.appendChild(menuRow(t("Secret drop", "시크릿 전달"), t("value stays on the box · path only", "값은 박스에 보관 · 경로만 전달"), function () {
        var localSecret = document.getElementById("secret-open");
        if (localSecret) { closeMenu(); localSecret.click(); }
        else openPanel("secret", t("Secret drop", "시크릿 전달"), secretBase);
      }));
    }
    (document.body || document.documentElement).appendChild(m);
    var r = btn.getBoundingClientRect(), mh = m.offsetHeight, mw = m.offsetWidth;
    var top = r.bottom + 8, left = r.left;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 8);
    if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - mw - 8);
    m.style.top = top + "px";
    m.style.left = left + "px";
    menuEl = m;
    setTimeout(function () {              // not in this same click
      document.addEventListener("pointerdown", onDocDown, true);
    }, 0);
  }
  function onDocDown(e) {
    if (menuEl && !menuEl.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
      closeMenu();
      document.removeEventListener("pointerdown", onDocDown, true);
    }
  }
  window.addEventListener("message", function (e) {
    if (stationdeskIsCloseMessage(e.data)) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (modalEl) closeModal();
    else if (menuEl) closeMenu();
  });
  var suppressClick = false, navigated = false;
  function go() {
    if (navigated) return;
    navigated = true;
    window.location.href = HUB;
  }
  btn.addEventListener("click", function (e) {
    e.preventDefault();
    if (suppressClick) { suppressClick = false; e.stopPropagation(); return; }
    if (useMenu) { openMenu(); return; }
    go();
  });
  btn.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (useMenu) openMenu(); else go(); }
  });
  if (mode === "floating") {
    var start = null, dragging = false;
    var onMove = function (e) {
      if (!start) return;
      if (!dragging && Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) < DRAG) return;
      dragging = true;
      S.opacity = "1";
      applyXY(e.clientX - start.ox, e.clientY - start.oy);
      placed = true;
      e.preventDefault();
    };
    var onUp = function () {
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onUp, true);
      S.opacity = "0.9";
      if (dragging) {
        suppressClick = true;                          // ignore the one click after this drag
        setTimeout(function () { suppressClick = false; }, 350);
        var r = btn.getBoundingClientRect();
        try { localStorage.setItem(POS_KEY, JSON.stringify({ x: r.left, y: r.top })); } catch (_) {}
      }
      start = null; dragging = false;
    };
    btn.addEventListener("pointerdown", function (e) {
      btn.setPointerCapture(e.pointerId);
      var r = btn.getBoundingClientRect();
      start = { x: e.clientX, y: e.clientY, ox: e.clientX - r.left, oy: e.clientY - r.top };
      dragging = false;
      window.addEventListener("pointermove", onMove, true);
      window.addEventListener("pointerup", onUp, true);
      window.addEventListener("pointercancel", onUp, true);
    });
    window.addEventListener("resize", function () {     // keep it on-screen on rotate/resize
      readSafeArea();
      if (!placed) return;
      var r = btn.getBoundingClientRect();
      applyXY(r.left, r.top);
    });
  }

  function mount() {
    if (mode === "native") {
      var slot = stationdeskResolveSlot(document, targetSelector, null);
      if (!slot) {
        if (window.console && console.debug) console.debug("stationdesk-return: native target not found");
        return;
      }
      slot.appendChild(btn);
    } else {
      (document.body || document.documentElement).appendChild(btn);
    }
    if (wantBadge) {
      pollUnread();
      setInterval(pollUnread, POLL_MS);
    }
    if (ALERT_URL) {
      pollAlert();
      setInterval(pollAlert, POLL_MS);
    }
  }
  window.addEventListener("stationdesk:messages-changed", pollUnread);
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
