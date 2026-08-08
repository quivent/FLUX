#!/home/dev/venv/bin/python
"""Koyomi's two themes, in one place.

The suite has two rooms, and both are worth keeping:

    FOREST   the render feed's warm sepia -- brass on near-black, hairline
             rules, square corners, no shadows. Ground #17150f, ink #efe6d6,
             brass #b99b5e, borders #3a3324, wells #1d1a12.
    SAKURA   the atelier's white room with pink walls of light -- white panels
             on a pink-lit near-white ground, soft pink rules, pill buttons,
             flat cel shadows, red rationed to hot states only.

Neither replaces the other. They are siblings, and every page in the suite can
wear either one.

The rule that makes that possible: **nothing but a token ever differs.** Every
stylesheet in the suite consumes `var(--token)` and never a literal colour,
radius, shadow, font or easing. There is exactly one template per page, its
markup and JS are theme-agnostic, and there is not a single theme-conditional
CSS rule anywhere. Flip `document.documentElement.dataset.theme` between
"forest" and "sakura" in a live console and the whole surface re-themes, with
no reload, no refetch and no JS running.

This file is the single source of truth for both apps:

    gallery.py   imports it and inlines css() at serve time, and republishes it
                 at GET /theme.css
    control.html carries a *generated* copy between marker comments, because
                 control.py serves control.html as a static file and has no
                 route that could serve a stylesheet (and control.py is not
                 ours to edit). It is stamped, never hand-edited:

                     python3 theme.py --stamp control.html --default sakura
                     python3 theme.py --check control.html   # drift -> exit 1

    python3 theme.py --emit      the CSS
    python3 theme.py --diff      the two token blocks, side by side
"""
import argparse
import pathlib
import re
import sys

KEY = "koyomi-theme"          # one localStorage key across the whole suite
NAMES = ("forest", "sakura")

# --------------------------------------------------------------------------
# The token table.  (name, forest, sakura) -- read it as the two rooms in
# parallel.  Names the control panel's script already paints with are kept
# exactly as they were; only their values move.
# --------------------------------------------------------------------------
TOKENS = [
    ("--scheme", "dark", "light"),

    # ---- the artwork's own materials (collection.py CREAM / INK) ----------
    ("--cream", "#f7f0e2", "#f7f0e2"),
    ("--cream-dim", "#ece2d0", "#ece2d0"),

    # ---- ink -------------------------------------------------------------
    ("--ink", "#d8c9a8", "#191320"),          # display ink, headings, <b>
    ("--text", "#efe6d6", "#1c1622"),         # body copy
    ("--muted", "#8f8570", "#6d6577"),
    ("--faint", "#6b634f", "#aaa0b1"),
    ("--dim", "#00000000", "#00000000"),

    # ---- the room --------------------------------------------------------
    ("--room", "#17150f", "#fffafc"),
    ("--page-bg", "var(--room)",
     "radial-gradient(720px 520px at 6% -8%,rgba(255,92,158,.20),transparent 60%),"
     "radial-gradient(560px 420px at 98% 2%,rgba(246,48,74,.10),transparent 58%),"
     "radial-gradient(900px 640px at 82% 92%,rgba(255,92,158,.13),transparent 62%),"
     "var(--room)"),
    ("--panel", "#201d14", "#ffffff"),
    ("--panel2", "#201d14", "#ffffff"),
    ("--wall", "#17150f", "#fffafc"),
    ("--wall-lit", "#1d1a12", "#fff6fa"),
    ("--sink", "#1d1a12", "#fdf3f7"),         # recessed: inputs, wells, logs
    ("--sink-hi", "#231f15", "#fff6fa"),      # a well with something over it
    ("--well", "#241f16", "#fdf3f7"),
    ("--line", "#302b1e", "#f7e7ef"),         # soft rules, never hard hairlines
    ("--line2", "#3a3324", "#f0d5e3"),
    ("--dash", "#4a4231", "#f0d5e3"),
    ("--edge-hover", "#6d6244", "#ff5c9e"),
    ("--plate", "#0e0d09", "var(--cream-dim)"),   # the ground images sit on:
                                                  # dark paper / cream. Never black.

    # ---- the accent carries the room -------------------------------------
    ("--pink", "#b99b5e", "#ff5c9e"),
    ("--pink-ink", "#b99b5e", "#d81b7a"),
    ("--pink-soft", "#241f16", "#ffe9f2"),
    ("--pink-line", "#3a3324", "#ffcbe0"),
    ("--accent-hi", "#d8c9a8", "#ff77ae"),
    ("--accent-body", "#d8c9a8", "#6a4459"),
    ("--on-accent", "#17150f", "#ffffff"),
    # legacy names the control script paints with -- remapped, never renamed
    ("--accent", "var(--pink-ink)", "var(--pink-ink)"),
    ("--accent-soft", "var(--pink)", "var(--pink)"),

    # ---- hot / negative states, rationed ---------------------------------
    ("--red", "#d08a72", "#f6304a"),
    ("--red-ink", "#d08a72", "#d9102a"),
    ("--red-soft", "#2b1c14", "#ffe4e7"),
    ("--red-line", "#5b3a2e", "#ffc9ce"),
    ("--red-track", "#5b3a2e", "#ffcdd3"),
    ("--red-cel", "#5b3a2e", "#ffd3d8"),

    # ---- meaning-bearing status (legacy names) ---------------------------
    ("--good", "#8fbf7f", "var(--pink-ink)"),
    ("--good-line", "#3c5236", "var(--pink-line)"),
    ("--good-bg", "#241f16", "var(--pink-soft)"),
    ("--warn", "#b99b5e", "#f6304a"),
    ("--bad", "#d08a72", "#d9102a"),
    ("--bad-line", "#5b3a2e", "var(--red)"),
    ("--bad-bg", "#241f16", "var(--red-soft)"),

    # ---- the live indicator ----------------------------------------------
    ("--live", "#4b7f4b", "#ff5c9e"),
    ("--live-off", "#6b4a3a", "#f6304a"),
    ("--live-ring", "rgba(75,127,75,.7)", "rgba(255,92,158,.55)"),
    ("--live-ring-0", "rgba(75,127,75,0)", "rgba(255,92,158,0)"),

    # ---- the seed tag on a studio variant --------------------------------
    ("--tag-bg", "rgba(20,18,14,.85)", "var(--pink-soft)"),
    ("--tag-ink", "#b99b5e", "#d81b7a"),

    # ---- rgb triples, so alpha washes flip with the room -----------------
    ("--accent-rgb", "185,155,94", "255,92,158"),
    ("--glow-rgb", "0,0,0", "216,27,122"),
    ("--hot-rgb", "208,138,114", "246,48,74"),
    ("--hot-ink-rgb", "208,138,114", "217,16,42"),
    ("--ink-rgb", "239,230,214", "25,19,32"),   # the hairline on a photo edge
    ("--shade-rgb", "0,0,0", "0,0,0"),
    ("--panel-rgb", "32,29,20", "255,255,255"),

    # ---- type ------------------------------------------------------------
    ("--display", '"Iowan Old Style", Palatino, Georgia, serif',
     "'Zen Maru Gothic',ui-rounded,'Hiragino Maru Gothic ProN','M PLUS Rounded 1c',system-ui,sans-serif"),
    ("--serif", "var(--display)", "var(--display)"),
    ("--sans", '"Iowan Old Style", Palatino, Georgia, serif',
     "'M PLUS Rounded 1c',ui-rounded,'Hiragino Maru Gothic ProN',system-ui,-apple-system,'Segoe UI',sans-serif"),
    ("--mono", "ui-monospace,SFMono-Regular,Menlo,monospace",
     "'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace"),
    ("--num", "var(--serif)", "var(--mono)"),   # numerals on chips and tags

    # ---- shape: forest squares off, sakura rounds everything -------------
    ("--r-pill", "2px", "999px"),
    ("--r-xl", "0", "30px"),
    ("--r-lg", "0", "28px"),
    ("--r-plate", "0", "26px"),
    ("--r", "0", "24px"),
    ("--r-box", "0", "22px"),
    ("--r-card", "0", "20px"),
    ("--r-md", "0", "18px"),
    ("--r-out", "0", "16px"),
    ("--r-th", "0", "14px"),
    ("--r-sm", "0", "13px"),
    ("--r-kv", "0", "12px"),
    ("--r-xs", "0", "9px"),
    ("--r-kbd", "0", "7px"),
    ("--r-xxs", "0", "6px"),

    # ---- motion ----------------------------------------------------------
    ("--spring", "ease", "cubic-bezier(.34,1.46,.64,1)"),
    ("--ease", "ease", "ease"),                # opacity fades, shared on purpose

    # ---- depth: sakura's flat cel drops, forest's flatness ---------------
    ("--soft", "none", "0 12px 26px rgba(var(--glow-rgb),.06)"),
    ("--cel", "none", "0 9px 0 -4px var(--pink-line)"),
    ("--sh-bar", "none", "0 9px 0 -5px var(--pink-line),var(--soft)"),
    ("--sh-go", "none", "0 5px 0 -1px var(--pink-ink)"),
    ("--sh-go-hover", "none", "0 7px 0 -1px var(--pink-ink)"),
    ("--sh-go-active", "none", "0 2px 0 -1px var(--pink-ink)"),
    ("--sh-halt-hover", "none", "0 6px 0 -1px var(--red-ink)"),
    ("--sh-focus", "0 0 0 4px var(--pink-soft)", "0 0 0 4px var(--pink-soft)"),
    ("--sh-plate", "none",
     "0 14px 0 -7px var(--pink-line),0 22px 40px rgba(var(--glow-rgb),.10)"),
    ("--sh-plate-keep", "0 0 0 3.5px var(--pink)",
     "0 0 0 3.5px var(--pink),0 16px 0 -7px var(--pink-line),"
     "0 22px 44px rgba(var(--glow-rgb),.20)"),
    ("--sh-plate-retire", "0 0 0 3px var(--red-line)",
     "0 0 0 3px var(--red-soft),0 8px 18px rgba(var(--shade-rgb),.05)"),
    ("--sh-tile", "none",
     "0 7px 0 -4px var(--pink-line),0 10px 20px rgba(var(--glow-rgb),.07)"),
    ("--sh-tile-hover", "none",
     "0 11px 0 -4px var(--pink-line),0 16px 26px rgba(var(--glow-rgb),.14)"),
    ("--sh-tile-keep", "0 0 0 3px var(--pink)",
     "0 0 0 3px var(--pink),0 9px 0 -4px var(--pink-line),"
     "0 14px 24px rgba(var(--glow-rgb),.18)"),
    ("--sh-tile-retire", "0 0 0 2.5px var(--red-line)", "0 0 0 2.5px var(--red-soft)"),
    ("--sh-chip-on", "none", "0 3px 8px rgba(var(--glow-rgb),.28)"),
    ("--sh-seg-on", "none", "0 3px 8px rgba(var(--glow-rgb),.30)"),
    ("--sh-sync-ok", "none", "0 3px 8px rgba(var(--glow-rgb),.26)"),
    ("--sh-sync-stale", "none", "0 3px 8px rgba(var(--hot-rgb),.30)"),
    ("--sh-sdot", "none", "0 0 0 4px rgba(var(--shade-rgb),.03)"),
    ("--sh-sdot-run", "0 0 0 5px rgba(var(--accent-rgb),.22)",
     "0 0 0 5px rgba(var(--accent-rgb),.22)"),
    ("--sh-sdot-pause", "0 0 0 5px rgba(var(--hot-rgb),.16)",
     "0 0 0 5px rgba(var(--hot-rgb),.16)"),
    ("--sh-evd", "0 0 0 4px rgba(var(--accent-rgb),.20)",
     "0 0 0 4px rgba(var(--accent-rgb),.20)"),
    ("--sh-evd-err", "0 0 0 4px rgba(var(--hot-rgb),.16)",
     "0 0 0 4px rgba(var(--hot-rgb),.16)"),
    ("--sh-tog", "none", "0 1px 3px rgba(var(--shade-rgb),.18)"),
    ("--sh-toast", "none",
     "0 10px 0 -5px var(--pink-line),0 18px 40px rgba(var(--glow-rgb),.18)"),
    ("--sh-toast-bad", "none",
     "0 10px 0 -5px var(--red-cel),0 18px 40px rgba(var(--hot-ink-rgb),.18)"),
    ("--sh-ov", "none",
     "0 18px 0 -9px var(--pink-line),0 30px 70px rgba(var(--glow-rgb),.22)"),
    ("--sh-lin", "none", "0 5px 0 -3px var(--pink-line)"),
    ("--sh-jd", "none", "0 10px 0 -6px var(--pink-line)"),
    ("--sh-jd-hover", "0 0 0 3px var(--pink)",
     "0 0 0 3px var(--pink),0 12px 0 -7px var(--pink-line)"),
    ("--sh-jd-hit", "0 0 0 5px var(--pink)", "0 0 0 5px var(--pink)"),
    # the live pulse: one ring, three frames. Shape is animation, not theme;
    # the colour underneath it is what flips.
    ("--sh-live", "0 0 0 0 var(--live-ring)", "0 0 0 0 var(--live-ring)"),
    ("--sh-live-mid", "0 0 0 9px var(--live-ring-0)", "0 0 0 9px var(--live-ring-0)"),
    ("--sh-live-end", "0 0 0 0 var(--live-ring-0)", "0 0 0 0 var(--live-ring-0)"),
]

FOREST = {n: f for n, f, _ in TOKENS}
SAKURA = {n: s for n, _, s in TOKENS}


# --------------------------------------------------------------------------
# emitters
# --------------------------------------------------------------------------

def _block(selector, idx):
    lines = [f"  {selector} {{"]
    for row in TOKENS:
        name, value = row[0], row[idx]
        lines.append(f"    {name}: {value};")
    lines.append("    color-scheme: var(--scheme);")
    lines.append("  }")
    return "\n".join(lines)


#: Component CSS shared by every page in the suite: the flip control itself.
#: It is theme-agnostic -- it reads like any other surface, in whichever room
#: it happens to be standing.
SHARED_CSS = """
  ::selection { background: var(--pink); color: var(--on-accent); }

  .themetog { font-family: var(--sans); font-size: 11px; font-weight: 700;
              letter-spacing: .16em; text-transform: uppercase; cursor: pointer;
              padding: 5px 13px; margin-left: 12px; vertical-align: middle;
              line-height: 1.5; white-space: nowrap;
              background: var(--panel); color: var(--muted);
              border: 1px solid var(--line2); border-radius: var(--r-pill);
              box-shadow: none;
              transition: border-color .18s var(--spring), color .18s var(--spring),
                          transform .18s var(--spring); }
  .themetog:hover { border-color: var(--pink); color: var(--pink-ink);
                    background: var(--panel); transform: translateY(-1px); }
"""

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=M+PLUS+Rounded+1c:'
    "wght@400;500;700;800&family=Zen+Maru+Gothic:wght@500;700;900&"
    'family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">'
)


def css():
    """The whole theme layer: two token blocks and the shared flip control."""
    return "\n".join([
        "  /* ==================================================================",
        "     koyomi -- two rooms, one vocabulary.  Generated by theme.py;",
        "     do not hand-edit.  Every rule below this block consumes var(--x)",
        "     and never a literal, so the only difference between the themes is",
        "     the values here.  Theme-conditional rules: zero.",
        "     ================================================================== */",
        _block(':root, [data-theme="forest"]', 1),
        "",
        _block('[data-theme="sakura"]', 2),
        "  /* koyomi:tokens:end */",
        SHARED_CSS.rstrip(),
    ])


def head_boot(default="forest"):
    """Fonts, plus the pre-paint stamp.

    Runs in <head> before the first byte of the stylesheet matters, so the page
    never paints one room and then snaps to the other.
    """
    if default not in NAMES:
        raise ValueError(f"default must be one of {NAMES}")
    return f"""{FONT_LINKS}
<script>
  (function () {{
    var NAMES = ['forest', 'sakura'], DEF = '{default}', t = null;
    try {{ t = localStorage.getItem('{KEY}'); }} catch (e) {{ t = null; }}
    if (NAMES.indexOf(t) < 0) {{
      // Never chosen. The operating system gets a say; failing that, the page
      // keeps the identity it was built with.
      var mq = window.matchMedia;
      t = mq && mq('(prefers-color-scheme: dark)').matches ? 'forest'
        : mq && mq('(prefers-color-scheme: light)').matches ? 'sakura'
        : DEF;
    }}
    document.documentElement.setAttribute('data-theme', t);
  }})();
</script>"""


def toggle_js():
    """Wires any element with id="themetog". Same key on every page, so a choice
    made in the feed is already in force when the panel loads."""
    return f"""<script>
  (function () {{
    var root = document.documentElement, btn = document.getElementById('themetog');
    if (!btn) return;
    var other = function () {{
      return root.getAttribute('data-theme') === 'sakura' ? 'forest' : 'sakura';
    }};
    var label = function () {{
      var n = other();
      btn.textContent = n === 'sakura' ? '○ sakura' : '● forest';
      btn.title = 'Switch to the ' + n + ' theme';
      btn.setAttribute('aria-label', btn.title);
    }};
    btn.addEventListener('click', function () {{
      var n = other();
      root.setAttribute('data-theme', n);
      try {{ localStorage.setItem('{KEY}', n); }} catch (e) {{}}
      label();
    }});
    label();
  }})();
</script>"""


# --------------------------------------------------------------------------
# stamping a static page (control.html) from this one source
# --------------------------------------------------------------------------

REGIONS = {
    "boot": ("<!-- koyomi:boot:begin -->", "<!-- koyomi:boot:end -->"),
    "theme": ("/* koyomi:theme:begin */", "/* koyomi:theme:end */"),
    "toggle": ("<!-- koyomi:toggle:begin -->", "<!-- koyomi:toggle:end -->"),
}


def _bodies(default):
    return {
        "boot": head_boot(default),
        "theme": css(),
        "toggle": toggle_js(),
    }


def render(text, default):
    """Return `text` with every marker region refilled from this file."""
    out = text
    for key, (a, b) in REGIONS.items():
        pat = re.compile(re.escape(a) + r".*?" + re.escape(b), re.S)
        if not pat.search(out):
            raise SystemExit(f"marker region {key!r} not found ({a} … {b})")
        out = pat.sub(lambda _m, a=a, b=b, k=key: f"{a}\n{_bodies(default)[k]}\n{b}",
                      out, count=1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emit", action="store_true", help="print the theme CSS")
    ap.add_argument("--diff", action="store_true", help="the two blocks, side by side")
    ap.add_argument("--stamp", metavar="FILE", help="refill FILE's marker regions")
    ap.add_argument("--check", metavar="FILE", help="exit 1 if FILE has drifted")
    ap.add_argument("--default", default="forest", choices=list(NAMES))
    a = ap.parse_args(argv)

    if a.emit:
        print(css())
    if a.diff:
        w = max(len(n) for n, _, _ in TOKENS)
        print(f"{'token'.ljust(w)}  {'forest'.ljust(46)}  sakura")
        print(f"{'-' * w}  {'-' * 46}  {'-' * 46}")
        for n, f, s in TOKENS:
            print(f"{n.ljust(w)}  {f[:46].ljust(46)}  {s[:46]}")
        print(f"\n{len(TOKENS)} tokens x 2 themes")
    if a.stamp:
        p = pathlib.Path(a.stamp)
        new = render(p.read_text(), a.default)
        if new != p.read_text():
            p.write_text(new)
            print(f"stamped {p}")
        else:
            print(f"{p} already current")
    if a.check:
        p = pathlib.Path(a.check)
        cur = p.read_text()
        if render(cur, a.default) != cur:
            print(f"DRIFT: {p} does not match theme.py", file=sys.stderr)
            return 1
        print(f"ok: {p} matches theme.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
