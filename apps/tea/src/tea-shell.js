/* Tea app shell.
   The top window owns the parchment chrome and the URL. Rooms load in
   #tea-frame so their scripts, sockets, and timers stay isolated while the
   header never unmounts. Direct loads still work; the first in-app click
   promotes that document into the host. */
(function () {
  var PREFIXES = [
    "/gallery", "/portraits", "/garden", "/tea", "/jury", "/moj", "/consult",
    "/movement", "/motion-work", "/exhibition", "/studies", "/stallion",
    "/engine", "/engine-room", "/protocol", "/spec", "/arcane", "/sentinel"
  ];

  function isTeaPath(href) {
    var u;
    try { u = new URL(href, location.href); } catch (e) { return false; }
    if (u.origin !== location.origin) return false;
    var p = u.pathname.replace(/\/+$/, "") || "/";
    if (p === "/") return true;
    for (var i = 0; i < PREFIXES.length; i++) {
      var pre = PREFIXES[i];
      if (p === pre || p.indexOf(pre + "/") === 0) return true;
    }
    return false;
  }

  function isEmbedded() {
    try {
      return window.parent !== window && !!(window.parent.__teaApp);
    } catch (e) {
      return false;
    }
  }

  // Run in <head> so an embedded room can hide its duplicate chrome before paint.
  if (isEmbedded()) document.documentElement.classList.add("tea-embed");

  function restoreMode() {
    try {
      var mode = localStorage.getItem("tea-mode") || localStorage.getItem("influx_twilight_mode");
      if (mode === "nocturnal" || mode === "dark") document.body.classList.add("mode-nocturnal");
    } catch (e) {}
  }

  function ensureConsultLink() {
    document.querySelectorAll("nav.tea-nav").forEach(function (nav) {
      if (nav.querySelector('a[href="/consult"]')) return;
      var jury = nav.querySelector('a[href="/jury"]');
      var a = document.createElement("a");
      a.href = "/consult";
      a.textContent = "Direction";
      if (jury && jury.parentNode === nav) jury.insertAdjacentElement("afterend", a);
      else nav.appendChild(a);
    });
  }

  function markCurrent(pathname) {
    ensureConsultLink();
    var path = (pathname || "/").replace(/\/+$/, "") || "/";
    document.querySelectorAll("nav.tea-nav a").forEach(function (a) {
      var href = a.getAttribute("href") || "";
      var p = href.split("?")[0].split("#")[0].replace(/\/+$/, "") || "/";
      var on = p === path || (p !== "/" && (path === p || path.indexOf(p + "/") === 0));
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function teaClick(e) {
    if (e.defaultPrevented || e.button !== 0) return null;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return null;
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!a) return null;
    if (a.target && a.target !== "_self") return null;
    var raw = a.getAttribute("href") || "";
    if (!raw || raw.charAt(0) === "#") return null;
    return a.href;
  }

  function bootEmbed() {
    document.documentElement.classList.add("tea-embed");
    document.body.classList.add("tea-app");
    restoreMode();
    ensureConsultLink();
    var chrome = document.querySelector("header.tea-chrome");
    if (chrome) chrome.setAttribute("hidden", "");
    document.addEventListener("click", function (e) {
      var href = teaClick(e);
      if (!href) return;
      if (isTeaPath(href)) {
        e.preventDefault();
        window.parent.__teaApp.navigate(href);
        return;
      }
      e.preventDefault();
      window.top.location.href = href;
    }, true);
  }

  var nativeRAF = window.requestAnimationFrame.bind(window);
  var nativeCAF = window.cancelAnimationFrame.bind(window);
  var nativeSetInterval = window.setInterval.bind(window);
  var nativeSetTimeout = window.setTimeout.bind(window);
  var nativeClearInterval = window.clearInterval.bind(window);
  var nativeClearTimeout = window.clearTimeout.bind(window);
  var NativeWS = window.WebSocket;
  var NativeES = window.EventSource;
  var gen = 1;
  var sockets = [];
  var sources = [];
  var patched = false;

  function installHostPatches() {
    if (patched) return;
    patched = true;
    window.requestAnimationFrame = function (cb) {
      var g = gen;
      return nativeRAF(function (t) { if (g === gen) cb(t); });
    };
    window.setInterval = function (cb, ms) {
      var g = gen;
      return nativeSetInterval(function () { if (g === gen) cb.apply(this, arguments); }, ms);
    };
    window.setTimeout = function (cb, ms) {
      var g = gen;
      if (typeof cb !== "function") return nativeSetTimeout(cb, ms);
      return nativeSetTimeout(function () { if (g === gen) cb.apply(this, arguments); }, ms);
    };
    window.WebSocket = function (url, protocols) {
      var ws = protocols !== undefined ? new NativeWS(url, protocols) : new NativeWS(url);
      ws.__teaGen = gen;
      sockets.push(ws);
      return ws;
    };
    window.WebSocket.prototype = NativeWS.prototype;
    ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) {
      window.WebSocket[k] = NativeWS[k];
    });
    window.EventSource = function (url, config) {
      var es = config ? new NativeES(url, config) : new NativeES(url);
      es.__teaGen = gen;
      sources.push(es);
      return es;
    };
    window.EventSource.prototype = NativeES.prototype;
    window.EventSource.CONNECTING = NativeES.CONNECTING;
    window.EventSource.OPEN = NativeES.OPEN;
    window.EventSource.CLOSED = NativeES.CLOSED;
  }

  function teardownHostPage() {
    gen += 1;
    sockets.splice(0).forEach(function (ws) { try { ws.close(); } catch (e) {} });
    sources.splice(0).forEach(function (es) { try { es.close(); } catch (e) {} });
  }

  var frame = null;
  var bar = null;
  var chrome = null;

  function ensureChrome() {
    chrome = document.querySelector("header.tea-chrome");
    if (!chrome) return;
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "tea-progress";
      bar.setAttribute("aria-hidden", "true");
      chrome.insertAdjacentElement("afterend", bar);
    }
    if (window.ResizeObserver) {
      new ResizeObserver(syncChromeHeight).observe(chrome);
    }
    syncChromeHeight();
    window.addEventListener("resize", syncChromeHeight);
  }

  function syncChromeHeight() {
    if (!chrome) return;
    document.documentElement.style.setProperty(
      "--tea-chrome-actual",
      chrome.getBoundingClientRect().height + "px"
    );
  }

  function ensureFrame() {
    if (frame && frame.isConnected) return frame;
    document.documentElement.classList.add("tea-host");
    teardownHostPage();
    var stage = document.getElementById("tea-stage");
    if (!stage) {
      stage = document.createElement("div");
      stage.id = "tea-stage";
    }
    Array.prototype.slice.call(document.body.children).forEach(function (el) {
      if (el === chrome || el === bar || el === stage) return;
      el.remove();
    });
    if (!stage.isConnected) {
      if (bar && bar.isConnected) bar.insertAdjacentElement("afterend", stage);
      else if (chrome) chrome.insertAdjacentElement("afterend", stage);
      else document.body.prepend(stage);
    }
    frame = document.createElement("iframe");
    frame.id = "tea-frame";
    frame.title = "Tea";
    frame.setAttribute("allow", "autoplay");
    stage.appendChild(frame);
    return frame;
  }

  function showFrame(href, push) {
    var u = new URL(href, location.href);
    var next = u.pathname + u.search + u.hash;
    var here = location.pathname + location.search + location.hash;
    if (push && next !== here) history.pushState({ tea: 1 }, "", next);
    markCurrent(u.pathname);
    ensureChrome();
    ensureFrame();
    if (bar) bar.classList.add("on");
    frame.onload = function () {
      if (bar) bar.classList.remove("on");
      try {
        var d = frame.contentDocument;
        if (d && d.title) document.title = d.title;
        if (frame.contentWindow) frame.contentWindow.focus();
      } catch (e) {}
    };
    var dest = u.pathname + u.search + u.hash;
    if (frame.getAttribute("src")) {
      try {
        frame.contentWindow.location.assign(u.href);
        return;
      } catch (e) {}
    }
    frame.src = dest;
  }

  function navigate(href) {
    var u = new URL(href, location.href);
    showFrame(u.href, true);
  }

  function bootHost() {
    document.body.classList.add("tea-app");
    restoreMode();
    ensureChrome();
    ensureConsultLink();
    markCurrent(location.pathname);
    history.replaceState({ tea: 1 }, "", location.href);
    window.__teaApp = { navigate: navigate };
    document.addEventListener("click", function (e) {
      var href = teaClick(e);
      if (!href || !isTeaPath(href)) return;
      e.preventDefault();
      navigate(href);
    }, true);
    window.addEventListener("popstate", function () {
      if (!isTeaPath(location.href)) return;
      showFrame(location.href, false);
    });
  }

  if (!isEmbedded()) installHostPatches();

  function boot() {
    if (window.__teaShellBooted) return;
    window.__teaShellBooted = true;
    if (isEmbedded()) bootEmbed();
    else bootHost();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
