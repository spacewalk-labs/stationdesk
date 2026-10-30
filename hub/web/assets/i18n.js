/* English is the default. Korean is an explicit, persisted preference. */
(function () {
  "use strict";
  var selected = "en";
  try {
    if (localStorage.getItem("stationdesk-language") === "ko") selected = "ko";
  } catch (_) {}
  document.documentElement.lang = selected;

  function apply(root) {
    if (selected !== "ko") return;
    root = root || document;
    var attributes = ["title", "aria-label", "placeholder"];
    var selector = "[data-i18n-ko]," + attributes.map(function (name) {
      return "[data-i18n-ko-" + name + "]";
    }).join(",");
    var nodes = Array.from(root.querySelectorAll(selector));
    if (root.matches && root.matches(selector)) nodes.unshift(root);
    nodes.forEach(function (node) {
      if (node.hasAttribute("data-i18n-ko")) node.textContent = node.getAttribute("data-i18n-ko");
      attributes.forEach(function (name) {
        var value = node.getAttribute("data-i18n-ko-" + name);
        if (value !== null) node.setAttribute(name, value);
      });
    });
  }

  window.StationDeskI18n = {
    t: function (en, ko) { return selected === "ko" ? ko : en; },
    language: function () { return selected; },
    setLanguage: function (language) {
      selected = language === "ko" ? "ko" : "en";
      try { localStorage.setItem("stationdesk-language", selected); } catch (_) {}
      document.documentElement.lang = selected;
    },
    apply: apply
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { apply(); });
  else apply();
})();
