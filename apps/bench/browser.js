// Keep the upstream canvas handlers, while allowing the browser's two-finger zoom.
(function () {
  if (window.top === window.self && matchMedia('(pointer:coarse)').matches) {
    const app = location.pathname.split('/').filter(Boolean)[0];
    if (app) {
      location.replace('/stationdesk-bench.html?app=' + encodeURIComponent(app));
      return;
    }
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
