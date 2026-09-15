(function () {
  const nav = [
    ['/gallery/', 'Gallery'],
    ['/collections', 'Collections'],
    ['/protocol', 'Protocol'],
    ['/overview', 'Overview'],
    ['/profiles', 'Profiles'],
    ['/setup', 'Setup'],
    ['/jury', 'Jury'],
    ['/control', 'Controls']
  ];

  const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  function current(path, href) {
    const clean = (value) => value.replace(/\/+$/, '') || '/';
    path = clean(path);
    href = clean(href);
    return path === href || (href !== '/' && path.startsWith(href + '/'));
  }

  function mount() {
    const old = document.querySelector('.beauty-chrome');
    if (old) old.remove();
    const header = document.createElement('header');
    header.className = 'beauty-chrome';
    header.innerHTML =
      '<a class="beauty-brand" href="/gallery/"><span class="beauty-word">Beauty</span><small>working archive</small></a>' +
      '<nav class="beauty-nav" aria-label="Beauty">' + nav.map(([href, label]) =>
        '<a href="' + href + '"' + (current(location.pathname, href) ? ' aria-current="page"' : '') + '>' + label + '</a>'
      ).join('') + '</nav>' +
      '<div class="beauty-system"><i class="live-dot" id="beauty-live-dot"></i><span id="beauty-live-label">checking</span></div>';
    document.body.insertBefore(header, document.body.firstChild);
  }

  async function status() {
    const dot = document.getElementById('beauty-live-dot');
    const label = document.getElementById('beauty-live-label');
    if (!dot || !label) return;
    try {
      const response = await fetch('/api/protocol?lane=fashion', { cache: 'no-store' });
      if (!response.ok) throw new Error('protocol ' + response.status);
      const data = await response.json();
      const stream = data.stream || {};
      const running = stream.status === 'running' || stream.running === true;
      dot.className = 'live-dot ' + (running ? 'on' : '');
      label.textContent = running ? 'making' : ((data.flux && data.flux.loaded) ? 'ready' : 'idle');
    } catch (_) {
      dot.className = 'live-dot warn';
      label.textContent = 'offline';
    }
  }

  window.Beauty = { esc };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', status);
  else status();
  setInterval(status, 5000);
})();
