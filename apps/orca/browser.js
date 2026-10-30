// StationDesk chrome around the unmodified Orca client.
(() => {
  const mobile = window.matchMedia('(max-width: 640px)');
  const initialized = new Set();
  const restore = new Set();
  const selectors = {
    left: 'button.sidebar-toggle:has(svg.lucide-panel-left)',
    right: 'button.sidebar-toggle:has(svg.lucide-panel-right)',
  };
  const openPanel = {
    left: () => document.querySelector('[data-sidebar-resize-handle]'),
    right: () => document.querySelector('.right-sidebar-header-drag button.sidebar-toggle:has(svg.lucide-panel-right)'),
  };

  // A manual choice wins over automatic restoration when returning to desktop.
  document.addEventListener('click', event => {
    if (!event.isTrusted) return;
    for (const side of ['left', 'right']) {
      if (event.target.closest(selectors[side])) {
        restore.delete(side);
        if (mobile.matches) initialized.add(side);
      }
    }
  }, true);

  function update() {
    const title = document.querySelector('.titlebar-app-name-main');
    if (title && !title.querySelector('img')) {
      const icon = document.createElement('img');
      icon.src = '/stationdesk-app-icon.png';
      icon.alt = '';
      icon.width = icon.height = 20;
      icon.style.cssText = 'display:inline-block;vertical-align:middle;margin-right:6px;flex-shrink:0';
      title.prepend(icon);
    }
    if (!mobile.matches) return;
    for (const side of ['left', 'right']) {
      const button = document.querySelector(selectors[side]);
      if (initialized.has(side) || !button || !openPanel[side]()) continue;
      initialized.add(side);
      // data-state belongs to Orca's tooltip, not to the panel. The mounted
      // resize handle / internal header toggle identifies an open panel.
      restore.add(side);
      button.click();
    }
  }
  mobile.addEventListener('change', () => {
    if (mobile.matches) initialized.clear();
    if (!mobile.matches) {
      for (const side of restore) {
        if (!openPanel[side]()) document.querySelector(selectors[side])?.click();
      }
      restore.clear();
    }
    update();
  });
  new MutationObserver(update).observe(document.getElementById('root') || document.body, {childList:true, subtree:true});
  update();
})();
