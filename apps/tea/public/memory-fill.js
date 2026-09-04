(function (root) {
  function mount(el, opts) {
    if (!el) return;
    opts = opts || {};
    const src = opts.src || "/tea/memory-fill.json";
    el.classList.add("mem-fill");
    el.innerHTML =
      '<div class="mem-fill-head"><span class="k">Memory</span><span class="v" data-label>—</span></div>' +
      '<div class="mem-fill-well" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">' +
      '<i data-bar></i></div>' +
      '<div class="mem-fill-sub"><span data-sub>—</span></div>';
    const bar = el.querySelector("[data-bar]");
    const lab = el.querySelector("[data-label]");
    const sub = el.querySelector("[data-sub]");
    const well = el.querySelector(".mem-fill-well");
    async function tick() {
      try {
        const r = await fetch(src, { cache: "no-store" });
        if (!r.ok) throw new Error(r.status);
        const d = await r.json();
        const w = d.working || {};
        const dur = d.durable || {};
        const pct = Math.max(0, Math.min(100, Number(w.pct) || 0));
        bar.style.width = pct + "%";
        well.setAttribute("aria-valuenow", String(Math.round(pct)));
        el.classList.toggle("hot", pct >= 85);
        el.classList.toggle("mid", pct >= 35 && pct < 85);
        const used = w.tokens_used != null ? Number(w.tokens_used).toLocaleString() : "—";
        const cap = w.tokens_cap != null ? Number(w.tokens_cap).toLocaleString() : "—";
        lab.textContent = pct.toFixed(1) + "% · " + used + " / " + cap + " tok";
        sub.textContent =
          "durable " + (dur.pct || 0).toFixed(0) + "% · " +
          (dur.semantic_keys || 0) + " cache keys · " +
          Math.round((dur.shard_bytes || 0) / 1048576) + " MiB shards";
      } catch (e) {
        lab.textContent = "memory scrape down";
        sub.textContent = String(e.message || e);
      }
    }
    tick();
    setInterval(tick, opts.interval || 1500);
  }
  root.mountMemoryFill = mount;
})(window);
