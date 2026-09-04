/* One Tea chrome on every room. Keep click capture — some pages swallow nav. */
(function () {
  var NAV =
    '<a class="tea-brand" href="/"><span class="tea-seal">茶</span><span class="tea-word">Tea</span></a>' +
    '<nav class="tea-nav" aria-label="Tea">' +
      '<a href="/gallery/">Gallery</a>' +
      '<a href="/protocol">Protocol</a>' +
      '<a href="/evening">Evening</a>' +
      '<a href="/jury">Jury</a>' +
      '<a href="/judge">Judge</a>' +
      '<a href="/desk">Desk</a>' +
      '<a href="/hive">Hive</a>' +
      '<a href="/research">Research</a>' +
      '<a href="/ledger">Ledger</a>' +
      '<a href="/governor">Governor</a>' +
      '<a href="/train">Train</a>' +
      '<a href="/discourse">Discourse</a>' +
      '<a href="/daemons">Daemons</a>' +
      '<a href="/scores">Scores</a>' +
      '<a href="/movement">Movement</a>' +
      '<a href="/studies">Studies</a>' +
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
    var host = location.hostname || "";
    document.querySelectorAll("nav.tea-nav a").forEach(function (a) {
      var href = a.getAttribute("href") || "";
      var p = href.split("?")[0].split("#")[0].replace(/\/+$/, "") || "/";
      var on = p === path || (p !== "/" && (path === p || path.indexOf(p + "/") === 0));
      if (host === "charters.apiary.vision" && (p === "/charters" || /charters\.apiary\.vision/.test(href))) on = true;
      if (host === "hive.apiary.vision" && p === "/hive") on = true;
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function unify() {
    document.body.classList.add("tea-app");
    var path = (location.pathname || "/").replace(/\/+$/, "") || "/";
    var onDiscourse = document.body.classList.contains("discourse-app") ||
      path === "/discourse" || path.indexOf("/discourse/") === 0;
    if (path === "/engine" || path === "/rig") document.body.classList.add("mode-nocturnal");
    try {
      var mode = localStorage.getItem("tea-mode") || localStorage.getItem("influx_twilight_mode");
      if (mode === "nocturnal" || mode === "dark") document.body.classList.add("mode-nocturnal");
    } catch (e) {}

    if (onDiscourse) {
      document.body.classList.add("discourse-app");
      if (!document.querySelector("header.tea-chrome")) {
        var teaHeader = document.createElement("header");
        teaHeader.className = "tea-chrome";
        teaHeader.innerHTML = NAV + '<div class="tea-chrome-end"></div>';
        document.body.insertBefore(teaHeader, document.body.firstChild);
      }
      markCurrent();
      return;
    }

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

  function watchSentinel() {
    fetch("/api/tea/daemons", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var s = d.summary || {};
        var alerts = s.alerts || 0;
        var up = s.up || 0;
        var header = document.querySelector("header.tea-chrome");
        if (!header) return;
        var end = header.querySelector(".tea-chrome-end");
        if (!end) {
          end = document.createElement("div");
          end.className = "tea-chrome-end";
          header.appendChild(end);
        }
        var mark = document.getElementById("tea-sentinel-mark");
        if (!mark) {
          mark = document.createElement("a");
          mark.id = "tea-sentinel-mark";
          mark.className = "tea-sentinel-mark";
          mark.href = "/daemons";
          end.insertBefore(mark, end.firstChild);
        }
        mark.textContent = alerts ? alerts + " silent" : up + " up";
        if (alerts) mark.setAttribute("data-alert", "1");
        else mark.removeAttribute("data-alert");
      })
      .catch(function () {});
    setTimeout(watchSentinel, 8000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", unify);
  else unify();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchSentinel);
  else watchSentinel();

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
