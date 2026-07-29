// Processing tab: CPU-side continuity calculator.
// deg/frame = 360 / n_cols for pure azimuth stepping; shell_scale multiplies the
// angular step, so effective step = shell_scale * 360 / n_cols.
(function () {
  var cols = document.getElementById("pcCols");
  var scale = document.getElementById("pcScale");
  var frames = document.getElementById("pcFrames");
  var out = document.getElementById("pcOut");
  if (!cols || !scale || !frames || !out) return;

  function verdict(deg) {
    if (deg < 0.2) return ["near-identical", "good"];
    if (deg < 1.0) return ["smooth", "good"];
    if (deg < 6.0) return ["steppy", "warn"];
    return ["unrelated", "bad"];
  }

  function recompute() {
    var n = Math.max(1, Number(cols.value) || 1);
    var s = Math.max(0.01, Number(scale.value) || 0.01);
    var f = Math.max(1, Number(frames.value) || 1);
    var deg = (360 / n) * s;
    var revs = (deg * f) / 360;
    var v = verdict(deg);
    var closes = n / s;
    out.innerHTML =
      '<div class="pcRow"><span>degrees per frame</span><b>' + deg.toFixed(4) + "&deg;</b></div>" +
      '<div class="pcRow"><span>revolutions over ' + f + " frames</span><b>" + revs.toFixed(2) + "</b></div>" +
      '<div class="pcRow"><span>path closes after</span><b>' + Math.round(closes) + " frames</b></div>" +
      '<div class="pcRow"><span>verdict</span><b class="' + v[1] + '">' + v[0] + "</b></div>" +
      (revs > 1.05
        ? '<div class="pcNote">Path revisits itself ' + Math.floor(revs) +
          "x across this run. Raise n_cols or lower shell_scale for a single sweep.</div>"
        : "");
  }

  [cols, scale, frames].forEach(function (el) {
    el.addEventListener("input", recompute);
  });
  recompute();
})();
