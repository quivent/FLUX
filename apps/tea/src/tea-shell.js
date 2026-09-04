/* Shared Tea chrome: mark the current room and restore parchment/nocturnal mode. */
(function () {
  document.body.classList.add("tea-app");

  try {
    var mode = localStorage.getItem("tea-mode") || localStorage.getItem("influx_twilight_mode");
    if (mode === "nocturnal" || mode === "dark") {
      document.body.classList.add("mode-nocturnal");
    }
  } catch (_) {}

  var path = (location.pathname || "/").replace(/\/+$/, "") || "/";
  document.querySelectorAll("nav.tea-nav a").forEach(function (a) {
    var href = a.getAttribute("href") || "";
    var p = href.split("?")[0].split("#")[0].replace(/\/+$/, "") || "/";
    var on =
      p === path ||
      (p !== "/" && (path === p || path.indexOf(p + "/") === 0));
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
})();
