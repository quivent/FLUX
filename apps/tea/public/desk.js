window.TeaDesk = (function () {
  var t = 0, lane = "microgreens";
  function $(id) { return document.getElementById(id); }
  function status(msg, cls) {
    var el = $("desk-status");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "desk-status" + (cls ? " " + cls : "");
  }
  function q(path) {
    return path + (path.indexOf("?") >= 0 ? "&" : "?") + "lane=" + encodeURIComponent(lane);
  }
  async function load() {
    const r = await fetch(q("/api/tea/desk"), { cache: "no-store" });
    const d = await r.json();
    if (d.desk && d.desk.lane) lane = d.desk.lane;
    return d;
  }
  async function save(patch) {
    patch.lane = lane;
    const r = await fetch(q("/api/tea/desk"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    const d = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      status((d.error || ("HTTP " + r.status)), "bad");
      throw new Error(d.error || "save failed");
    }
    status("live · saved", "ok");
    return d;
  }
  function debounce(fn, ms) {
    return function () {
      var args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(null, args); }, ms || 180);
    };
  }
  function setLane(next, after) {
    lane = next;
    document.querySelectorAll(".desk-lanes button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-lane") === lane);
    });
    after && after();
  }
  function bindLanes(after) {
    document.querySelectorAll(".desk-lanes button").forEach(function (b) {
      b.onclick = function () { setLane(b.getAttribute("data-lane"), after); };
    });
  }
  return { load: load, save: save, debounce: debounce, setLane: setLane, bindLanes: bindLanes, getLane: function () { return lane; }, status: status, $: $ };
})();
