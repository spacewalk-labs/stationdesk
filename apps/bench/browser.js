// Keep the upstream canvas handlers, while allowing the browser's two-finger zoom.
(function () {
  const match = location.pathname.match(/^\/([a-z0-9-]+)\//);
  const app = match && match[1];
  if (app && window.top === window.self && matchMedia('(pointer:coarse)').matches) {
    location.replace('/stationdesk-bench.html?app=' + encodeURIComponent(app));
    return;
  }
  // Editors that declare no icon get their Bench icon; a vendor icon is kept.
  if (app && !document.querySelector('link[rel~="icon"]')) {
    const icon = document.createElement('link');
    icon.rel = 'icon';
    icon.type = 'image/png';
    icon.href = '/icons/' + app + '.png';
    document.head.appendChild(icon);
  }
  const style = document.createElement('style');
  // One finger still belongs to the editor; two fingers can zoom and pan the page.
  style.textContent = 'canvas{touch-action:pinch-zoom!important}';
  document.head.appendChild(style);

  function allowBrowserGesture(event) {
    if (event.target instanceof HTMLCanvasElement) {
      // Upstream cancels touchstart, which otherwise disables native pinch.
      // Change only this event; the app still receives its drawing events.
      event.preventDefault = function () {};
    }
  }
  document.addEventListener('touchstart', allowBrowserGesture, true);
  document.addEventListener('touchmove', allowBrowserGesture, true);
})();
