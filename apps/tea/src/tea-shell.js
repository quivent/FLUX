/* One Tea chrome on every room. Keep click capture — some pages swallow nav. */
(function () {
  var NAV =
    '<a class="tea-brand" href="/"><span class="tea-seal">茶</span><span class="tea-word">Tea</span></a>' +
    '<nav class="tea-nav" aria-label="Tea">' +
      '<a href="/jury">Jury</a>' +
      '<a href="/judge">Judge</a>' +
      '<a href="/gallery/">Gallery</a>' +
      '<a href="/garden">Garden</a>' +
      '<a href="/portraits">Portraits</a>' +
      '<a href="/collections">Collections</a>' +
      '<a href="/exhibition">Exhibition</a>' +
      '<a href="/movement">Movement</a>' +
      '<a href="/atlas/">Worlds</a>' +
      '<a href="/studies">Studies</a>' +
      '<a href="https://charters.apiary.vision/">Charters</a>' +
      '<a href="/engine">Engine</a>' +
      '<a href="/protocol">Protocol</a>' +
      '<a href="/domains">Domains</a>' +
      '<a href="/rig">Rig</a>' +
    "</nav>";

  function stealEnd(old) {
    if (!old) return "";
    var end = old.querySelector(".tea-chrome-end");
    if (end) return end.innerHTML;
    var bits = [];
    old.querySelectorAll(".ritual-switch, #themeSwitch, .collection-mark, .telemetry-tag, .equilibrium-seal").forEach(function (n) {
      bits.push(n.outerHTML);
    });
    return bits.join("");
  }

  function markCurrent() {
    var path = (location.pathname || "/").replace(/\/+$/, "") || "/";
    document.querySelectorAll("nav.tea-nav a").forEach(function (a) {
      var href = a.getAttribute("href") || "";
      var p = href.split("?")[0].split("#")[0].replace(/\/+$/, "") || "/";
      var on = p === path || (p !== "/" && (path === p || path.indexOf(p + "/") === 0));
      if (location.hostname === "charters.apiary.vision" && /charters\.apiary\.vision/.test(href)) on = true;
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function unify() {
    document.body.classList.add("tea-app");
    var path = (location.pathname || "/").replace(/\/+$/, "") || "/";
    if (path === "/engine" || path === "/rig") document.body.classList.add("mode-nocturnal");
    try {
      var mode = localStorage.getItem("tea-mode") || localStorage.getItem("influx_twilight_mode");
      if (mode === "nocturnal" || mode === "dark") document.body.classList.add("mode-nocturnal");
    } catch (e) {}

    var olds = Array.prototype.slice.call(
      document.querySelectorAll("body > header, header.tea-chrome, body > .top")
    );
    var endHTML = "";
    olds.forEach(function (h) {
      if (!endHTML) endHTML = stealEnd(h);
    });
    var header = document.createElement("header");
    header.className = "tea-chrome";
    header.innerHTML = NAV + '<div class="tea-chrome-end">' + endHTML + "</div>";
    olds.forEach(function (h) { h.remove(); });
    document.body.insertBefore(header, document.body.firstChild);
    markCurrent();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", unify);
  else unify();

  document.addEventListener("click", function (e) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest && e.target.closest("header.tea-chrome nav.tea-nav a[href]");
    if (!a) return;
    var href = a.href;
    if (!href || href.charAt(0) === "#") return;
    e.preventDefault();
    e.stopPropagation();
    window.location.assign(href);
  }, true);
})();
