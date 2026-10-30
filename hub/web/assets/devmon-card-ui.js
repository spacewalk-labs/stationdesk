(function (root) {
  'use strict';

  function t(en, ko) { return root.StationDeskI18n ? root.StationDeskI18n.t(en, ko) : en; }

  var EXAMPLE_CHIPS = [t('Fix it', '고쳐 줘'), t('Explain the cause', '원인만 알려 줘'), t('Run it again', '다시 돌려 줘'), t('Turn off this timer', '이 타이머 꺼 줘')];

  function create(options) {
    options = options || {};
    var doc = options.document || root.document;
    var overlay = null;
    var current = null;

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.className = className;
      if (text != null) node.textContent = String(text);
      return node;
    }
    function action(label, className, fn) {
      var node = el('button', 'dmc-button' + (className ? ' ' + className : ''), label);
      node.type = 'button';
      node.addEventListener('click', function (event) {
        if (event && event.stopPropagation) event.stopPropagation();
        Promise.resolve(fn()).catch(report);
      });
      return node;
    }
    function report(error) {
      var message = error && error.message ? error.message : String(error || 'Action failed.');
      if (options.toast) options.toast(message);
    }
    function copyText(value) {
      if (!root.navigator || !root.navigator.clipboard || !root.navigator.clipboard.writeText) {
        throw new Error(t('Could not copy the link. Open it in a new tab and copy the address.', '링크를 복사할 수 없습니다. 새 탭에서 주소를 복사해 주세요.'));
      }
      return root.navigator.clipboard.writeText(value);
    }
    function hasDoc(card) {
      return card && typeof card.link === 'string' && /^https?:\/\//i.test(card.link);
    }
    function close() {
      if (!overlay) return;
      overlay.remove();
      overlay = null;
    }
    function frame(kind, card, wide) {
      close();
      current = card;
      overlay = el('div', 'dmc-overlay' + (wide ? ' dmc-wide' : ''));
      var modal = el('section', 'dmc-modal' + (wide ? ' dmc-modal-wide' : ''));
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', kind + ': ' + card.title);
      var head = el('header', 'dmc-head');
      head.appendChild(el('span', 'dmc-kind', kind));
      head.appendChild(el('h2', '', card.title));
      var closeButton = action(t('← Inbox', '← 받은 메시지'), 'dmc-close', close);
      closeButton.setAttribute('aria-label', t('Close and return to Inbox', '닫고 받은 메시지로 돌아가기'));
      head.appendChild(closeButton);
      modal.appendChild(head);
      overlay.appendChild(modal);
      overlay.addEventListener('click', function (event) { if (event.target === overlay) close(); });
      doc.body.appendChild(overlay);
      return modal;
    }
    function footer() {
      return el('footer', 'dmc-footer');
    }
    function markRead(card) {
      if (card.read_at) return Promise.resolve();
      card.read_at = new Date().toISOString();
      if (options.changed) options.changed(card, 'read');
      return Promise.resolve(options.read ? options.read(card) : null).catch(function (error) {
        card.read_at = null;
        if (options.changed) options.changed(card, 'read-failed');
        throw error;
      });
    }
    function archive(card) {
      return Promise.resolve(options.archive ? options.archive(card) : null).then(function () {
        if (options.removed) options.removed(card);
        close();
      });
    }
    function appendMessageActions(target, card, includeArchive) {
      if (card.run) {
        if (card.ran_at && card.ran_window) {
          target.appendChild(action(t('▶ Ran · View', '▶ 실행됨 · 보기'), 'dmc-primary', function () { return openWindow(card); }));
        } else {
          target.appendChild(action(t('Run', '실행'), 'dmc-primary', function () { return openRun(card); }));
        }
      }
      if (includeArchive) target.appendChild(action(t('Archive', '보관'), '', function () { return archive(card); }));
    }
    function createActions(card, includeArchive) {
      var actions = el('div', 'dmc-actions');
      appendMessageActions(actions, card, includeArchive !== false);
      return actions;
    }
    function runDetails(card) {
      var details = el('details', 'dmc-runbox dmc-run-details');
      var summary = el('summary', '', t('Run details', '실행용 카드 내용'));
      summary.appendChild(el('span', '', t('Context passed to the agent', '자동으로 전달되는 기계적 정보')));
      details.appendChild(summary);
      details.appendChild(el('code', 'dmc-cwd', 'cwd  ' + card.run.cwd));
      details.appendChild(el('pre', 'dmc-prompt', card.run.prompt));
      return details;
    }
    function appendDetail(target, card) {
      if (typeof card.detail !== 'string' || !card.detail) return;
      var details = el('details', 'dmc-runbox dmc-run-details');
      details.appendChild(el('summary', '', t('Original message', '원천 메시지')));
      details.appendChild(el('pre', 'dmc-prompt', card.detail));
      target.appendChild(details);
    }
    function openTitle(card) {
      return hasDoc(card) ? openDoc(card) : openMessage(card);
    }
    function openMessage(card) {
      markRead(card).catch(report);
      var modal = frame(card.level === 'urgent' ? t('URGENT', '긴급') : t('MESSAGE', '메시지'), card, false);
      var meta = el('div', 'dmc-meta');
      [card.source, card.count > 1 ? '×' + card.count : '', humanWhen(card.last_at),
       t('Marked as read', '읽음 표시됨'), t('Stays in place', '자리 그대로')]
        .filter(Boolean).forEach(function (text) { meta.appendChild(el('span', '', text)); });
      modal.appendChild(meta);
      if (card.about) {
        var about = el('div', 'dmc-about');
        about.appendChild(el('b', '', t('What this job is', '작업 설명')));
        about.appendChild(el('div', '', card.about));
        modal.appendChild(about);
      }
      if (card.body) {
        var body = el('div', 'dmc-body');
        var parts = String(card.body).split(' · ');
        body.appendChild(el('p', '', parts.shift()));
        if (parts.length) {
          var list = el('ul');
          parts.forEach(function (part) { list.appendChild(el('li', '', part)); });
          body.appendChild(list);
        }
        modal.appendChild(body);
      }
      appendDetail(modal, card);
      if (card.run) {
        modal.appendChild(runDetails(card));
      }
      var foot = footer();
      if (card.ran_at) foot.appendChild(el('span', 'dmc-status', t('▶ Ran ', '▶ 실행됨 ') + card.ran_at));
      appendMessageActions(foot, card, true);
      modal.appendChild(foot);
      return modal;
    }
    function humanWhen(value) {
      var date = new Date(value);
      if (isNaN(date.getTime())) return '';
      function pad(n) { return n < 10 ? '0' + n : String(n); }
      return (date.getMonth() + 1) + '/' + date.getDate() + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }
    function openDoc(card) {
      // Opening the document is reading the card, same as opening the plain card —
      // without this a card that carries a link could never leave the unread count.
      markRead(card).catch(report);
      var modal = frame(t('DOC', '문서'), card, true);
      var tools = el('div', 'dmc-doc-tools');
      var address = el('code', 'dmc-doc-url', card.link);
      address.title = card.link;
      tools.appendChild(address);
      var copyLink = action(t('Copy link', '링크 복사'), '', function () {
        return copyText(card.link).then(function () { copyLink.textContent = t('Copied ✓', '복사됨 ✓'); });
      });
      tools.appendChild(copyLink);
      var external = el('a', 'dmc-button', t('Open in new tab ↗', '새 탭에서 열기 ↗'));
      external.href = card.link;
      external.target = '_blank';
      external.rel = 'noopener noreferrer';
      tools.appendChild(external);
      if (card.run) tools.appendChild(action(t('Run with this report', '이 보고서로 실행'), 'dmc-primary', function () { openRun(card); }));
      modal.appendChild(tools);
      appendDetail(modal, card);
      var iframe = el('iframe', 'dmc-iframe');
      iframe.src = card.link;
      iframe.title = card.title;
      modal.appendChild(iframe);
      return modal;
    }
    function openRun(card) {
      var modal = frame(t('RUN', '실행'), card, false);
      var intro = el('section', 'dmc-runintro');
      intro.appendChild(el('b', '', t('Where does this run?', '어디서 실행되나요?')));
      intro.appendChild(el('div', '', t('Runs in a new Terminal session on this box. The session opens here after you start it.', '이 박스의 Terminal 새 작업창에서 실행합니다. 실행 뒤에는 그 작업창을 바로 보여 드립니다.')));
      modal.appendChild(intro);
      modal.appendChild(runDetails(card));
      var inputs = {};
      if (card.run.params && card.run.params.length) {
        var params = el('section', 'dmc-runbox');
        params.appendChild(el('div', 'dmc-label', t('PARAMS', '매개변수')));
        card.run.params.forEach(function (param) {
          var row = el('label', 'dmc-param');
          row.appendChild(el('span', '', param.label));
          var input;
          if (param.choices) {
            input = el('select');
            param.choices.forEach(function (value) {
              var option = el('option', '', value); option.value = value;
              input.appendChild(option);
            });
          } else {
            input = el('input'); input.type = 'text'; input.maxLength = 200;
          }
          if (Object.prototype.hasOwnProperty.call(param, 'default')) input.value = param.default;
          inputs[param.key] = input;
          row.appendChild(input);
          params.appendChild(row);
        });
        modal.appendChild(params);
      }
      var notes = el('section', 'dmc-runbox dmc-note');
      notes.appendChild(el('div', 'dmc-label', t('YOUR DECISIONS / NOTES', '지시 / 메모')));
      var textarea = el('textarea');
      textarea.maxLength = 8000;
      textarea.placeholder = t('Paste the Markdown from the report, or type freely.', '보고서의 Markdown을 붙여 넣거나 자유롭게 입력하세요.');
      textarea.value = typeof card.run.default_note === 'string' ? card.run.default_note : '';
      notes.appendChild(textarea);
      if (!card.run.params || !card.run.params.length) {
        var examples = el('div', 'dmc-examples');
        var exampleChips = Array.isArray(card.run.examples) ? card.run.examples : EXAMPLE_CHIPS;
        exampleChips.forEach(function (text) {
          examples.appendChild(action(text, 'dmc-chip', function () {
            textarea.value = (textarea.value ? textarea.value + '\n' : '') + text;
            if (textarea.focus) textarea.focus();
          }));
        });
        notes.appendChild(examples);
      }
      notes.appendChild(action(t('Paste', '붙여넣기'), '', function () {
        if (!root.navigator || !root.navigator.clipboard || !root.navigator.clipboard.readText) {
          throw new Error(t('Clipboard unavailable — paste manually.', '클립보드를 사용할 수 없습니다. 직접 붙여 넣으세요.'));
        }
        return root.navigator.clipboard.readText().then(function (text) { textarea.value = text; });
      }));
      modal.appendChild(notes);
      var foot = footer();
      foot.appendChild(action(t('← Message', '← 메시지'), '', function () { openMessage(card); }));
      var submitting = false;
      var submitted = false;
      var runButton = action(t('▶ Run', '▶ 실행'), 'dmc-primary', function () {
        if (submitting || submitted) return;
        submitting = true;
        runButton.disabled = true;
        var values = {};
        Object.keys(inputs).forEach(function (key) { values[key] = inputs[key].value; });
        return Promise.resolve().then(function () {
          return options.run(card, values, textarea.value);
        }).then(function (result) {
          submitted = true;
          card.ran_at = result.ran_at;
          card.ran_window = result.ran_window || result.window;
          card.run_session = result.session;
          if (options.changed) options.changed(card, 'ran');
          return openWindow(card);
        }).catch(function (error) {
          if (!submitted) {
            submitting = false;
            runButton.disabled = false;
          }
          throw error;
        });
      });
      foot.appendChild(runButton);
      modal.appendChild(foot);
      return modal;
    }
    function openWindow(card) {
      return Promise.resolve(options.selectWindow(card)).then(function (result) {
        var modal = frame(t('RUN', '실행'), card, true);
        var back = footer();
        back.appendChild(action(t('← Message', '← 메시지'), 'dmc-primary', function () { openMessage(card); }));
        if (!result || result.state !== 'active') {
          modal.appendChild(el('div', 'dmc-ended', t('This run has ended.', '이 실행은 종료되었습니다.')));
          modal.appendChild(back);
          return modal;
        }
        var session = result.session || card.run_session;
        if (session && typeof session === 'object') session = session.name || session.id;
        return Promise.resolve(options.devtermUrl(session)).then(function (url) {
          if (!url) throw new Error(t('Terminal is not available on this box.', '이 박스에서는 Terminal을 사용할 수 없습니다.'));
          var iframe = el('iframe', 'dmc-iframe dmc-terminal');
          iframe.src = url;
          iframe.title = 'Terminal ' + session;
          modal.appendChild(iframe);
          var external = el('a', 'dmc-button', t('Open in Terminal ↗', 'Terminal에서 열기 ↗'));
          external.href = url;
          external.target = '_blank';
          external.rel = 'noopener noreferrer';
          back.insertBefore(external, back.firstChild);
          modal.appendChild(back);
          return modal;
        });
      });
    }
    function onKey(event) { if (event.key === 'Escape') close(); }
    if (doc.addEventListener) doc.addEventListener('keydown', onKey);
    return { createActions: createActions, openMessage: openMessage, openDoc: openDoc,
             openTitle: openTitle, openRun: openRun, openWindow: openWindow, close: close,
             current: function () { return current; } };
  }

  root.DevmonCardUI = { create: create };
})(typeof window === 'undefined' ? globalThis : window);
