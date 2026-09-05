#!/usr/bin/env python3
"""Provision and verify the Arcane studio web surfaces.

``flux arcane provision`` calls this. It does two things and refuses to invent
anything while doing either:

1. **Verify** every studio surface that ``internal/server/server.go`` promises --
   the file is present, non-empty, parses as HTML, and the route that claims to
   serve it actually resolves through the mux. Local asset references are
   resolved against ``apps/tea/public/``; external CDN and font URLs are
   reported but never fetched.

2. **Generate** the Arcane surface manifest that ``arcane.html`` reads. The
   dashboard is served from a read-only public listener, so a *static JSON
   artifact* is the only honest mechanism -- a new API route would be blocked by
   ``readOnlyPaths`` and adding one would widen a security allowlist for a
   cosmetic reason.

Everything here is stdlib-only and import-clean on Python 3.9. Sibling modules
written by other agents (``pipeline_paths``, ``arcane_log``, ``arcane_pipeline``,
``flux_paths``) are imported defensively: their absence is reported in
``warnings``, never faked, and never fatal.

Nothing in this file writes a measured number it did not measure. The protocol
spec's "94.1% hit rate / 1.50 s per cell" travels through the manifest inside
``spec_claims_unverified`` and is labelled as a claim, so the dashboard cannot
accidentally render it as an observation.

Usage
-----
    python3 provision_surfaces.py                 # verify + write the manifest
    python3 provision_surfaces.py --check         # verify only, exit 1 on failure
    python3 provision_surfaces.py --dry-run       # do everything, write nothing
    python3 provision_surfaces.py --json          # machine-readable report
    python3 provision_surfaces.py --profile b300  # pin the hardware profile
"""

from __future__ import annotations

import argparse
import datetime
import html.parser
import importlib
import importlib.util
import json
import os
import pathlib
import re
import sys
import time

__version__ = "1.0.0"

MANIFEST_KIND = "arcane_surface_manifest"
MANIFEST_VERSION = 1
MANIFEST_NAME = "arcane-manifest.json"

OK = "ok"
FAIL = "FAIL"
WARN = "warn"

# Claims made by docs/ARCANE_LATENT_CARTOGRAPHY_PROTOCOL_SPEC.md section 2.2.
# They are carried as claims. arcane_pipeline.py holds the same two constants
# for the same reason.
SPEC_CLAIM_HIT_RATE = 0.941
SPEC_CLAIM_SECONDS_PER_CELL = 1.50

# Protocol spec section 3, in the six-invariant form the dashboard renders.
# ``pipeline_key`` maps each one onto arcane_pipeline.FORTICHE_INVARIANTS so the
# conformance readout can key off whichever vocabulary a cell record used.
FORTICHE_INVARIANTS = [
    {
        "key": "impasto",
        "pipeline_key": "brush",
        "label": "Brush Texture & Impasto",
        "glyph": "\U0001F3A8",
        "constraint": "Visible oil/gouache paint layering, dry-brush breaks",
    },
    {
        "key": "planarity",
        "pipeline_key": "silhouette",
        "label": "Silhouette & Planes",
        "glyph": "\U0001F4D0",
        "constraint": "Sharp angular facial geometry, planar cheekbones",
    },
    {
        "key": "chiaroscuro",
        "pipeline_key": "lighting",
        "label": "Lighting & Chiaroscuro",
        "glyph": "\U0001F4A1",
        "constraint": "Dual-source: high-contrast ambient + graphic rim light",
    },
    {
        "key": "palette_zaun",
        "pipeline_key": "palette_zaun",
        "label": "Zaun Undercity Palette",
        "glyph": "\U0001F9EA",
        "constraint": "Toxic chemtech emerald (#00ff88), rusted iron, violet",
    },
    {
        "key": "palette_piltover",
        "pipeline_key": "palette_piltover",
        "label": "Piltover Apex Palette",
        "glyph": "☀️",
        "constraint": "Gilded brass, white marble, hextech cyan (#00d2ff)",
    },
    {
        "key": "anti_cgi",
        "pipeline_key": "anti_plastic",
        "label": "Anti-Plastic CGI Filter",
        "glyph": "\U0001F6AB",
        "constraint": "Hard rejection of smooth skin or flat photographic CGI",
    },
]

# Live endpoints the dashboard consumes. Every one of these already exists in
# internal/server/server.go. This provisioner invents none of them; it only
# records which ones the page is allowed to reach.
SURFACE_ENDPOINTS = {
    "atlas_events": "/api/atlas/events/{job_id}",
    "gallery_events": "/api/gallery/events/{collection}",
    "recent_images": "/api/recent-images",
    "sentinel": "/api/sentinel",
    "sentinel_events": "/api/sentinel/events",
    "health": "/api/health",
    "outputs": "/outputs/",
    "run_index": "/outputs/arcane/index.json",
    "run_manifest": "/outputs/arcane/{job_id}.json",
    "surface_manifest": "/arcane/" + MANIFEST_NAME,
}

# Prefixes whose targets are produced at runtime, not checked out. A reference
# to one of these is correct even though no file backs it on this machine.
RUNTIME_PREFIXES = ("/api/", "/outputs/", "/staged/")

ASSET_SUFFIXES = (
    ".css", ".js", ".mjs", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", ".avif", ".mp4", ".webm", ".woff", ".woff2", ".ttf", ".otf",
    ".ico", ".map", ".txt",
)


# ---------------------------------------------------------------------------
# Defensive imports -- agents 5 and 7 may not have landed yet
# ---------------------------------------------------------------------------


def load_module(name):
    """Import a sibling module, tolerating every way it can be missing."""
    try:
        if importlib.util.find_spec(name) is None:
            return None
    except (ImportError, ValueError, AttributeError):
        return None
    try:
        return importlib.import_module(name)
    except Exception:  # a half-written sibling must not take this script down
        return None


class Log:
    """House logging via ``arcane_log`` when it exists, plain prints otherwise.

    ``arcane_log`` is being written by another agent right now, so every call
    into it is probed and guarded. A signature mismatch degrades to ``print``;
    it never raises.
    """

    _FN_NAMES = ("emit", "log", "line", "info", "say", "note", "write")

    def __init__(self, module=None, quiet=False):
        self.quiet = quiet
        self.backend = "print"
        self._fn = None
        if module is not None:
            for name in self._FN_NAMES:
                fn = getattr(module, name, None)
                if callable(fn):
                    self._fn = fn
                    self.backend = "arcane_log.%s" % name
                    break
            if self._fn is None:
                getter = getattr(module, "get_logger", None)
                if callable(getter):
                    try:
                        logger = getter("provision_surfaces")
                    except Exception:
                        logger = None
                    fn = getattr(logger, "info", None)
                    if callable(fn):
                        self._fn = fn
                        self.backend = "arcane_log.get_logger().info"

    def __call__(self, text="", level="INFO"):
        if self.quiet:
            return
        if self._fn is not None:
            try:
                self._fn(text, level)
                return
            except TypeError:
                pass
            except Exception:
                self._fn = None
                self.backend = "print (arcane_log raised)"
            if self._fn is not None:
                try:
                    self._fn(text)
                    return
                except Exception:
                    self._fn = None
                    self.backend = "print (arcane_log raised)"
        print(text)

    def rule(self, char="-", width=100):
        self(char * width)


def _short(exc, limit=140):
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] if text else exc.__class__.__name__


def _first_env(*keys):
    for key in keys:
        value = os.environ.get(key, "")
        if value.strip():
            return value.strip()
    return ""


def _as_path(value):
    if value in (None, ""):
        return None
    try:
        return pathlib.Path(str(value)).expanduser()
    except Exception:
        return None


def human_bytes(n):
    if n is None:
        return "-"
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / (1024.0 * 1024.0))


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------


class Paths:
    """Order of authority: ``pipeline_paths`` -> environment -> repo-relative."""

    def __init__(self):
        self.source = "repo-relative defaults"
        self.home = pathlib.Path(__file__).resolve().parent
        self.out_dir = None
        self.atlas_dir = None
        self.continuum = {}      # pipeline_paths' resolved view, when available
        self.raw_continuum = {}  # jury_continuum.toml as written
        self.warnings = []

    @classmethod
    def resolve(cls):
        self = cls()

        pp = load_module("pipeline_paths")
        if pp is not None:
            self.source = "pipeline_paths"
            self.home = _as_path(getattr(pp, "FLUX_HOME", None)) or self.home
            self.out_dir = _as_path(getattr(pp, "OUT_DIR", None))
            self.atlas_dir = _as_path(getattr(pp, "ATLAS_DIR", None))
            loader = getattr(pp, "load_continuum", None)
            if callable(loader):
                try:
                    loaded = loader()
                    if isinstance(loaded, dict) and loaded:
                        self.continuum = loaded
                except Exception as exc:
                    self.warnings.append(
                        "pipeline_paths.load_continuum() failed: %s" % _short(exc))
        else:
            self.warnings.append(
                "pipeline_paths not importable; using env + repo-relative fallbacks")

        env_home = _first_env("FLUX_HOME", "ARCANE_FLUX_HOME")
        if env_home:
            self.home = pathlib.Path(env_home).expanduser()

        if self.out_dir is None:
            env_out = _first_env("OUT_DIR", "FLUX_OUTPUT_DIR", "ARCANE_OUT_DIR")
            if env_out:
                self.out_dir = pathlib.Path(env_out).expanduser()
            else:
                fp = load_module("flux_paths")
                if fp is not None and hasattr(fp, "default_out_dir"):
                    try:
                        self.out_dir = pathlib.Path(fp.default_out_dir()).expanduser()
                    except Exception:
                        self.out_dir = None
                if self.out_dir is None:
                    if pathlib.Path("/runs").exists():
                        self.out_dir = pathlib.Path("/runs/flux-output")
                    else:
                        self.out_dir = pathlib.Path.home() / "Models" / "flux-output"

        if self.atlas_dir is None:
            self.atlas_dir = self.out_dir / "atlas"

        # The raw file is read either way: it is the only place the full list of
        # available profiles lives, and it is the fallback when pipeline_paths
        # cannot import (Python 3.9 has no tomllib, and that module needs it).
        self.raw_continuum = read_continuum(self.home / "jury_continuum.toml", self.warnings)

        return self

    # -- derived ------------------------------------------------------------

    @property
    def public_dir(self):
        return self.home / "apps" / "tea" / "public"

    @property
    def assets_dir(self):
        return self.public_dir / "assets"

    @property
    def drafts_dir(self):
        return self.home / "atlas_drafts"

    @property
    def server_go(self):
        return self.home / "internal" / "server" / "server.go"

    @property
    def surface_dir(self):
        """Where arcane_pipeline.py publishes its per-run manifests."""
        return self.out_dir / "arcane"

    @property
    def genome_path(self):
        return self.surface_dir / "crowned_genome.jsonl"

    def as_dict(self):
        return {
            "source": self.source,
            "FLUX_HOME": str(self.home),
            "OUT_DIR": str(self.out_dir),
            "ATLAS_DIR": str(self.atlas_dir),
            "PUBLIC_DIR": str(self.public_dir),
            "DRAFTS_DIR": str(self.drafts_dir),
            "out_dir_present": self.out_dir.is_dir(),
        }


# ---------------------------------------------------------------------------
# jury_continuum.toml -- agent 5 owns the file, we only read it
# ---------------------------------------------------------------------------


def read_continuum(path, warnings=None):
    path = pathlib.Path(path)
    if not path.exists():
        if warnings is not None:
            warnings.append("jury_continuum.toml not found at %s" % path)
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        if warnings is not None:
            warnings.append("could not read %s: %s" % (path, _short(exc)))
        return {}
    try:
        import tomllib  # Python 3.11+

        return tomllib.loads(text)
    except ImportError:
        pass
    except Exception as exc:
        if warnings is not None:
            warnings.append("tomllib rejected jury_continuum.toml: %s" % _short(exc))
    try:
        return _mini_toml(text)
    except Exception as exc:
        if warnings is not None:
            warnings.append("continuum parse failed (%s); continuing without it" % _short(exc))
        return {}


def _mini_toml(text):
    """Enough TOML for [tables] of scalars, arrays and inline tables.

    Python 3.9 has no tomllib and this repo is stdlib-only, so the fallback has
    to exist. It mirrors arcane_pipeline._mini_toml deliberately: two parsers
    that disagree about the same file would be worse than one that is limited.
    """
    root = {}
    table = root
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().strip("[]").strip()
            table = root
            for part in [p.strip().strip('"') for p in name.split(".") if p.strip()]:
                node = table.get(part)
                if not isinstance(node, dict):
                    node = {}
                    table[part] = node
                table = node
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip('"')
        parsed = _mini_toml_value(value.strip())
        if "." in key:  # dotted key such as `variants.bf16 = { ... }`
            head, _, tail = key.partition(".")
            node = table.get(head)
            if not isinstance(node, dict):
                node = {}
                table[head] = node
            node[tail] = parsed
        else:
            table[key] = parsed
    return root


def _split_top(text):
    out, depth, quote, buf = [], 0, "", []
    for ch in text:
        if quote:
            if ch == quote:
                quote = ""
            buf.append(ch)
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [p for p in (s.strip() for s in out) if p]


def _mini_toml_value(text):
    text = text.split(" #")[0].strip()
    if not text:
        return ""
    if text[0] in "\"'":
        return text.strip("\"'")
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_mini_toml_value(p) for p in _split_top(inner)] if inner else []
    if text.startswith("{") and text.endswith("}"):
        out = {}
        for part in _split_top(text[1:-1].strip()):
            k, _, v = part.partition("=")
            if _:
                out[k.strip().strip('"')] = _mini_toml_value(v.strip())
        return out
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


# ---------------------------------------------------------------------------
# server.go -- routes are read out of the source, never guessed
# ---------------------------------------------------------------------------

_RE_HANDLEFUNC = re.compile(r'mux\.HandleFunc\(\s*"([^"]+)"\s*,\s*s\.(\w+)\s*\)')
_RE_FUNC = re.compile(r'^func \(s \*?Server\) (\w+)\(', re.MULTILINE)
_RE_PUBLIC_FILE = re.compile(
    r'filepath\.Join\(\s*s\.cfg\.Root\s*,\s*"apps"\s*,\s*"tea"\s*,\s*"public"\s*,\s*"([^"]+\.html)"')
_RE_DELEGATE = re.compile(r's\.(\w+)\(w, r\)')
_RE_READONLY_BLOCK = re.compile(r'var readOnlyPaths = \[\]string\{(.*?)\n\}', re.DOTALL)
_RE_STRING = re.compile(r'"([^"]*)"')


class ServerRoutes:
    """What internal/server/server.go actually promises to serve."""

    def __init__(self):
        self.available = False
        self.routes = []           # [(pattern, handler)]
        self.handler_files = {}    # handler -> [html file, ...]
        self.readonly_prefixes = []
        self.patterns = set()
        self.warnings = []

    @classmethod
    def parse(cls, *go_files):
        self = cls()
        text_parts = []
        for path in go_files:
            path = pathlib.Path(path)
            if not path.exists():
                self.warnings.append("%s not found; surface table falls back to static defaults"
                                     % path)
                continue
            try:
                text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                self.warnings.append("could not read %s: %s" % (path, _short(exc)))
        if not text_parts:
            return self
        text = "\n".join(text_parts)
        self.available = True

        for pattern, handler in _RE_HANDLEFUNC.findall(text):
            self.routes.append((pattern, handler))
            self.patterns.add(pattern)

        # Split the source into function bodies so a ServeFile literal is
        # attributed to the handler that actually contains it.
        bounds = [(m.group(1), m.start()) for m in _RE_FUNC.finditer(text)]
        for i, (name, start) in enumerate(bounds):
            end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
            files = []
            for fname in _RE_PUBLIC_FILE.findall(text[start:end]):
                if fname not in files:
                    files.append(fname)
            if files:
                self.handler_files[name] = files

        # A handler that hands off to another (`s.gallery` -> `s.galleryFlux`)
        # serves whatever the delegate serves. Without this the file lands under
        # the wrong route, or under none at all.
        delegates = {}
        for i, (name, start) in enumerate(bounds):
            end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
            body = text[start:end]
            found = set(_RE_DELEGATE.findall(body)) - {name}
            if found:
                delegates[name] = found
        for _ in range(3):  # transitive, but bounded -- delegation is shallow here
            changed = False
            for name, targets in delegates.items():
                files = list(self.handler_files.get(name, []))
                for target in targets:
                    for fname in self.handler_files.get(target, []):
                        if fname not in files:
                            files.append(fname)
                            changed = True
                if files:
                    self.handler_files[name] = files
            if not changed:
                break

        block = _RE_READONLY_BLOCK.search(text)
        if block:
            body = "\n".join(
                line.split("//")[0] for line in block.group(1).splitlines())
            self.readonly_prefixes = _RE_STRING.findall(body)
        else:
            self.warnings.append("readOnlyPaths block not found in server.go")
        return self

    # -- queries ------------------------------------------------------------

    def surfaces(self):
        """handler -> {name, routes, files} for every handler serving a page."""
        grouped = {}
        for pattern, handler in self.routes:
            grouped.setdefault(handler, []).append(pattern)
        out = []
        for handler, files in sorted(self.handler_files.items()):
            routes = sorted(set(grouped.get(handler, [])))
            if not routes:
                continue
            for fname in files:
                out.append({
                    "surface": fname[:-len(".html")] if fname.endswith(".html") else fname,
                    "handler": handler,
                    "routes": routes,
                    "file": fname,
                })
        # Collapse duplicates (index.html is reached by several handlers).
        merged = {}
        for row in out:
            key = row["file"]
            if key in merged:
                for route in row["routes"]:
                    if route not in merged[key]["routes"]:
                        merged[key]["routes"].append(route)
                if row["handler"] not in merged[key]["handlers"]:
                    merged[key]["handlers"].append(row["handler"])
            else:
                merged[key] = {
                    "surface": row["surface"],
                    "handlers": [row["handler"]],
                    "routes": list(row["routes"]),
                    "file": key,
                }
        for row in merged.values():
            row["routes"].sort()
            row["handlers"].sort()
        return [merged[k] for k in sorted(merged)]

    def resolves(self, path):
        """Mirror http.ServeMux: longest registered pattern wins."""
        if not self.available:
            return None
        best = None
        for pattern in self.patterns:
            if pattern == path:
                return pattern
            if pattern.endswith("/") and path.startswith(pattern):
                if best is None or len(pattern) > len(best):
                    best = pattern
        return best

    def routed(self, path):
        """True when some pattern other than the "/" catch-all claims this path.

        The catch-all is ``s.home``, which 404s everything except "/" itself, so
        falling through to it is the same as having no route.
        """
        if not self.available:
            return None
        match = self.resolves(path)
        if match is None:
            return False
        if match == "/":
            return path == "/"
        return True

    def public_readonly(self, path):
        if not self.readonly_prefixes:
            return None
        for prefix in self.readonly_prefixes:
            if path == prefix or path.startswith(prefix):
                return True
        return path in ("/", "/favicon.ico")


# Used only when server.go cannot be read at all.
FALLBACK_SURFACES = [
    {"surface": "arcane", "file": "arcane.html", "routes": ["/arcane", "/arcane/"], "handlers": ["arcanePage"]},
    {"surface": "jury", "file": "jury.html", "routes": ["/jury", "/jury/", "/moj", "/moj/"], "handlers": ["juryPage"]},
    {"surface": "consult", "file": "consult.html", "routes": ["/consult", "/consult/"], "handlers": ["consultPage"]},
    {"surface": "engine", "file": "engine.html", "routes": ["/engine", "/engine/", "/engine-room", "/engine-room/"], "handlers": ["enginePage"]},
    {"surface": "exhibition", "file": "exhibition.html", "routes": ["/exhibition", "/exhibition/"], "handlers": ["exhibition"]},
    {"surface": "gallery", "file": "gallery.html", "routes": ["/gallery", "/gallery/"], "handlers": ["gallery"]},
    {"surface": "index", "file": "index.html", "routes": ["/", "/garden", "/tea"], "handlers": ["home"]},
    {"surface": "protocol", "file": "protocol.html", "routes": ["/protocol", "/spec"], "handlers": ["protocolPage"]},
    {"surface": "movement", "file": "movement.html", "routes": ["/movement", "/motion-work"], "handlers": ["movement"]},
    {"surface": "studies", "file": "studies.html", "routes": ["/studies", "/studies/"], "handlers": ["teaStudiesPage"]},
    {"surface": "sentinel", "file": "sentinel.html", "routes": ["/sentinel", "/sentinel/"], "handlers": ["sentinelPage"]},
    {"surface": "stallion", "file": "stallion.html", "routes": ["/exhibition/stallion"], "handlers": ["exhibition"]},
    {"surface": "stallion-lab", "file": "stallion-lab.html", "routes": ["/studies/stallion"], "handlers": ["stallionMotionLab"]},
]


# ---------------------------------------------------------------------------
# HTML verification
# ---------------------------------------------------------------------------

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_STRUCTURAL = ("html", "head", "body", "div", "section", "main", "header",
               "footer", "article", "nav", "script", "style", "table")
_REF_ATTRS = ("src", "href", "poster", "data-src", "data-href")


class SurfaceParser(html.parser.HTMLParser):
    """Collects tag balance and every local reference an attribute names."""

    def __init__(self):
        html.parser.HTMLParser.__init__(self, convert_charrefs=True)
        self.opened = {}
        self.closed = {}
        self.refs = []
        self.has_doctype = False
        self.title = ""
        self._in_title = False

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.has_doctype = True

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.opened[tag] = self.opened.get(tag, 0) + 1
        if tag == "title":
            self._in_title = True
        table = dict(attrs)
        for attr in _REF_ATTRS:
            value = table.get(attr)
            if value:
                self.refs.append((tag, attr, value.strip()))
        srcset = table.get("srcset")
        if srcset:
            for part in srcset.split(","):
                candidate = part.strip().split(" ")[0]
                if candidate:
                    self.refs.append((tag, "srcset", candidate))

    def handle_startendtag(self, tag, attrs):
        saved = self.opened.get(tag)
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            if saved is None:
                self.opened.pop(tag, None)
            else:
                self.opened[tag] = saved

    def handle_endtag(self, tag):
        if tag not in _VOID:
            self.closed[tag] = self.closed.get(tag, 0) + 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()


def classify_ref(url):
    """external | anchor | runtime | route | file"""
    if not url:
        return "anchor"
    low = url.lower()
    if low.startswith(("http://", "https://", "//", "data:", "mailto:",
                       "tel:", "javascript:", "blob:", "ws://", "wss://")):
        return "external"
    if url.startswith("#"):
        return "anchor"
    if url.startswith("{") or "${" in url:
        return "runtime"
    for prefix in RUNTIME_PREFIXES:
        if url.startswith(prefix):
            return "runtime"
    stem = url.split("?")[0].split("#")[0]
    if stem.lower().endswith(ASSET_SUFFIXES):
        return "file"
    return "route"


def _add(bucket, entry):
    if entry not in bucket:
        bucket.append(entry)


def check_surface(paths, routes, row, seen_files):
    """Verify one surface file and everything it points at locally."""
    fname = row["file"]
    target = paths.public_dir / fname
    result = {
        "surface": row["surface"],
        "file": fname,
        "path": str(target),
        "routes": list(row["routes"]),
        "handlers": list(row.get("handlers", [])),
        "bytes": None,
        "status": OK,
        "title": "",
        "problems": [],
        "notes": [],
        "external_refs": [],
        "broken_refs": [],
        "unrouted_refs": [],
        "public_readonly": None,
    }
    seen_files.add(fname)

    if not target.exists():
        result["status"] = FAIL
        result["problems"].append("file missing")
        return result
    if not target.is_file():
        result["status"] = FAIL
        result["problems"].append("not a regular file")
        return result

    size = target.stat().st_size
    result["bytes"] = size
    if size == 0:
        result["status"] = FAIL
        result["problems"].append("file is empty")
        return result

    try:
        text = target.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        result["status"] = FAIL
        result["problems"].append("unreadable: %s" % _short(exc))
        return result

    parser = SurfaceParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        result["status"] = FAIL
        result["problems"].append("HTML parse error: %s" % _short(exc))
        return result

    result["title"] = parser.title
    if not parser.has_doctype:
        result["notes"].append("no <!doctype>")
    # <html>, <head> and <body> are optional in HTML5 and several house pages
    # legitimately omit them, so their absence is a note, not a failure.
    for tag in ("html", "head", "body"):
        if not parser.opened.get(tag):
            result["notes"].append("no explicit <%s> (implied; legal HTML5)" % tag)
    if not parser.opened and not parser.closed:
        result["status"] = FAIL
        result["problems"].append("no HTML tags at all")
    for tag in _STRUCTURAL:
        opened = parser.opened.get(tag, 0)
        closed = parser.closed.get(tag, 0)
        if opened and opened != closed:
            result["notes"].append("<%s> %d open / %d close" % (tag, opened, closed))

    # -- local references -------------------------------------------------
    for tag, attr, url in parser.refs:
        kind = classify_ref(url)
        if kind == "external":
            if url not in result["external_refs"]:
                result["external_refs"].append(url)
            continue
        if kind in ("anchor", "runtime"):
            continue
        stem = url.split("?")[0].split("#")[0]
        routed = routes.routed(stem) if (routes.available and stem.startswith("/")) else None

        if kind == "route":
            if routed is False:
                _add(result["unrouted_refs"],
                     "%s (<%s %s>) has no mux route" % (url, tag, attr))
            continue

        # A local asset resolves if a file backs it, or if a handler claims the
        # path and serves it from somewhere else (/exhibition/*.mp4 is served out
        # of apps/tea/public/assets/ by an allowlist inside the handler).
        if stem.startswith("/"):
            candidates = [paths.public_dir / stem.lstrip("/"),
                          paths.assets_dir / pathlib.PurePosixPath(stem).name]
        else:
            candidates = [(target.parent / stem)]
        backed = next((c for c in candidates if c.exists()), None)

        if backed is None and routed is not True:
            _add(result["broken_refs"], "%s -> %s" % (url, candidates[0]))
        elif backed is None:
            _add(result["unrouted_refs"],
                 "%s (route claims it; no file under public/ or public/assets/)" % url)
        elif routed is False:
            _add(result["unrouted_refs"], "%s (file present, no mux route)" % url)

    if result["broken_refs"]:
        result["status"] = FAIL
        result["problems"].append("%d broken local reference(s)" % len(result["broken_refs"]))

    # -- read-only listener reachability ----------------------------------
    if routes.readonly_prefixes:
        reachable = [r for r in result["routes"] if routes.public_readonly(r)]
        result["public_readonly"] = bool(reachable)

    if result["status"] == OK and (result["notes"] or result["unrouted_refs"]):
        result["status"] = WARN
    return result


# ---------------------------------------------------------------------------
# Manifest generation
# ---------------------------------------------------------------------------


class Continuum:
    """The active hardware profile, model roster and VRAM budget.

    Two shapes are accepted, in this order of authority:

    1. ``pipeline_paths.load_continuum()`` -- agent 5's *resolved* view. It has
       already applied the per-tenant precision knobs, the toggles actually in
       force, and derived ``vram_expected_gib`` rather than trusting the literal
       in the file. When it is available it wins, because re-deriving any of
       that here would mean two provisioners disagreeing about one GPU.
    2. The raw ``jury_continuum.toml`` tables, parsed directly. This is the path
       on Python 3.9, which has no ``tomllib`` and where ``pipeline_paths``
       therefore cannot import at all.
    """

    _ORDER = {"flux": 0, "witness": 1, "governor": 2, "pixtral": 3, "gates": 4, "kontext": 5}

    def __init__(self):
        self.source = "unavailable"
        self.profile_name = None
        self.origin = None
        self.hardware = {}
        self.roster = []
        self.budget = {}
        self.verdict = {}
        self.ports = {}
        self.endpoints = {}
        self.available_profiles = []
        self.cadence_seconds = None

    # -- construction -------------------------------------------------------

    @classmethod
    def resolve(cls, paths, requested, warnings):
        self = cls()
        raw = paths.raw_continuum if isinstance(paths.raw_continuum, dict) else {}
        profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
        self.available_profiles = sorted(profiles)
        cont = raw.get("continuum") if isinstance(raw.get("continuum"), dict) else {}
        default = str(cont.get("default_profile") or "")
        if isinstance(cont.get("cadence_seconds"), (int, float)):
            self.cadence_seconds = float(cont["cadence_seconds"])

        if requested and profiles and requested not in profiles:
            warnings.append("profile %r (--profile) is not in jury_continuum.toml; "
                            "available: %s" % (requested, ", ".join(self.available_profiles) or "none"))
            requested = ""

        resolved = paths.continuum if isinstance(paths.continuum, dict) else {}
        if cls._is_resolved_view(resolved):
            self._from_pipeline_paths(resolved, warnings)
            self.origin = ("--profile" if requested else
                           ("env ARCANE_PROFILE" if _first_env("ARCANE_PROFILE")
                            else "continuum.default_profile"))
            if not self.available_profiles:
                self.available_profiles = [self.profile_name] if self.profile_name else []
            return self

        if not profiles:
            warnings.append("no [profiles.*] available; profile, roster and VRAM budget "
                            "are absent from the manifest rather than guessed")
            return self

        name, origin = "", ""
        for candidate, label in ((requested, "--profile"),
                                 (_first_env("ARCANE_PROFILE", "JURY_PROFILE"), "env"),
                                 (default, "continuum.default_profile")):
            if candidate and candidate in profiles:
                name, origin = candidate, label
                break
            if candidate and label != "--profile":
                warnings.append("profile %r (%s) not in jury_continuum.toml; ignored"
                                % (candidate, label))
        if not name:
            name, origin = self.available_profiles[0], "first available"
            warnings.append("falling back to first profile %r" % name)
        self._from_raw(profiles[name], name, origin, raw)
        return self

    @staticmethod
    def _is_resolved_view(body):
        return (isinstance(body.get("profile"), str)
                and isinstance(body.get("tenants"), dict)
                and body.get("tenants"))

    def _from_pipeline_paths(self, body, warnings):
        self.source = "pipeline_paths.load_continuum()"
        self.profile_name = body.get("profile")
        self.hardware = dict(body.get("hardware") or {})
        self.verdict = dict(body.get("verdict") or {})
        self.ports = dict(body.get("ports") or {})
        self.endpoints = dict(body.get("endpoints") or {})
        cont = body.get("continuum")
        if isinstance(cont, dict) and isinstance(cont.get("cadence_seconds"), (int, float)):
            self.cadence_seconds = float(cont["cadence_seconds"])

        tenants = body.get("tenants") or {}
        roster = []
        for name in tenants:
            tenant = tenants[name]
            if not isinstance(tenant, dict):
                continue
            roster.append(self._row(name, tenant))
        self.roster = self._sorted(roster)

        vram = body.get("vram") if isinstance(body.get("vram"), dict) else {}
        capacity = vram.get("total_gib", self.hardware.get("vram_gib"))
        self.budget = {
            "capacity_gib": capacity,
            "reserve_gib": vram.get("reserve_gib", self.hardware.get("reserve_gib")),
            "usable_gib": vram.get("usable_gib"),
            "allocated_gib": vram.get("allocated_gib"),
            "weights_gib": vram.get("weights_gib"),
            "free_gib": vram.get("free_gib"),
            "headroom_gib": vram.get("headroom_gib"),
            "fits": vram.get("fits"),
            "over_budget": (vram.get("fits") is False),
            "enabled_tenants": [t["tenant"] for t in self.roster if t["enabled"]],
            "disabled_tenants": [t["tenant"] for t in self.roster if not t["enabled"]],
            "source": self.source,
            "note": "Declared reservation for the toggle state in force. Not a live "
                    "nvidia-smi reading -- the dashboard must not present it as one.",
        }
        for text in body.get("warnings") or []:
            warnings.append("pipeline_paths: %s" % text)

    def _from_raw(self, profile_body, name, origin, raw):
        self.source = "jury_continuum.toml"
        self.profile_name = name
        self.origin = origin
        self.hardware = {
            key: profile_body.get(key)
            for key in ("description", "gpu", "sm", "vram_gib", "gpu_count",
                        "vram_per_gpu_gib", "reserve_gib", "vllm_min_version",
                        "prebuilt_wheel_available", "notes")
        }
        tenants = profile_body.get("tenants") if isinstance(profile_body.get("tenants"), dict) else {}
        self.roster = self._sorted([self._row(k, v) for k, v in tenants.items()
                                    if isinstance(v, dict)])
        self.verdict = dict(raw.get("verdict") or {})
        self.ports = dict(raw.get("ports") or {})

        def num(value):
            return float(value) if isinstance(value, (int, float)) else 0.0

        capacity = num(profile_body.get("vram_gib"))
        reserve = num(profile_body.get("reserve_gib"))
        allocated = sum(num(t["vram_expected_gib"]) for t in self.roster if t["enabled"])
        weights = sum(num(t["weights_gib"]) for t in self.roster if t["enabled"])
        free = capacity - reserve - allocated
        self.budget = {
            "capacity_gib": round(capacity, 2) if capacity else None,
            "reserve_gib": round(reserve, 2) if reserve else None,
            "usable_gib": round(capacity - reserve, 2) if capacity else None,
            "allocated_gib": round(allocated, 2),
            "weights_gib": round(weights, 2),
            "free_gib": round(free, 2) if capacity else None,
            "headroom_gib": round(free, 2) if capacity else None,
            "fits": (free >= 0) if capacity else None,
            "over_budget": bool(capacity and free < 0),
            "enabled_tenants": [t["tenant"] for t in self.roster if t["enabled"]],
            "disabled_tenants": [t["tenant"] for t in self.roster if not t["enabled"]],
            "source": self.source,
            "note": "vram_expected_gib summed straight from jury_continuum.toml "
                    "(tomllib unavailable, so precision knobs were not re-applied).",
        }

    # -- helpers ------------------------------------------------------------

    @classmethod
    def _row(cls, name, tenant):
        return {
            "tenant": tenant.get("name") or name,
            "role": tenant.get("role"),
            "kind": tenant.get("kind"),
            "model": tenant.get("model"),
            "precision": tenant.get("precision"),
            "port": tenant.get("port"),
            "socket": tenant.get("socket"),
            "served_name": tenant.get("served_name"),
            "vram_expected_gib": tenant.get("vram_expected_gib"),
            "weights_gib": tenant.get("weights_gib"),
            "gpu_memory_utilization": tenant.get("gpu_memory_utilization"),
            "enabled": bool(tenant.get("enabled", False)),
            "mandatory": bool(tenant.get("mandatory", (tenant.get("name") or name) != "kontext")),
            "toggleable": bool(tenant.get("toggleable", (tenant.get("name") or name) == "kontext")),
            "remote": tenant.get("remote"),
            "degrades_generator": bool(tenant.get("degrades_generator", False)),
        }

    @classmethod
    def _sorted(cls, roster):
        roster.sort(key=lambda t: (cls._ORDER.get(t["tenant"], 99), t["tenant"]))
        return roster


def collect_drafts(paths, warnings):
    drafts = []
    directory = paths.drafts_dir
    if not directory.is_dir():
        warnings.append("atlas_drafts/ not found at %s" % directory)
        return drafts
    for path in sorted(directory.glob("arcane_*.json")):
        entry = {
            "file": path.name,
            "path": str(path),
            "bytes": path.stat().st_size,
            "id": None, "label": None, "subject": None, "prompt": None,
            "mode": None, "traversal": None,
            "n_rows": None, "n_cols": None, "n_latent": None,
            "size": None, "steps": None, "guidance": None,
            "valid": False,
        }
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            entry["error"] = _short(exc)
            warnings.append("draft %s unreadable: %s" % (path.name, _short(exc)))
            drafts.append(entry)
            continue
        if not isinstance(body, dict):
            entry["error"] = "not a JSON object"
            drafts.append(entry)
            continue
        entry["valid"] = True
        for key in ("id", "label", "subject", "prompt", "mode", "traversal",
                    "n_rows", "n_cols", "n_latent", "size", "steps", "guidance"):
            if key in body:
                entry[key] = body[key]
        if isinstance(entry["prompt"], str) and len(entry["prompt"]) > 400:
            entry["prompt"] = entry["prompt"][:397] + "..."
        drafts.append(entry)
    if not drafts:
        warnings.append("no atlas_drafts/arcane_*.json drafts found")
    return drafts


def collect_runs(paths, warnings):
    """Run index published by arcane_pipeline.py, if OUT_DIR is reachable."""
    index_path = paths.surface_dir / "index.json"
    if not index_path.exists():
        return [], "%s absent (no pipeline run has published a surface index here)" % index_path
    try:
        body = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append("run index unreadable: %s" % _short(exc))
        return [], "unreadable: %s" % _short(exc)
    runs = body.get("runs") if isinstance(body, dict) else None
    if not isinstance(runs, list):
        return [], "run index has no runs[]"
    out = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        job_id = run.get("job_id")
        if not job_id:
            continue
        out.append({
            "job_id": job_id,
            "label": run.get("label"),
            "status": run.get("status"),
            "updated": run.get("updated"),
            "cells": run.get("cells"),
            "crowned": run.get("crowned"),
            "manifest_url": "/outputs/arcane/%s.json" % job_id,
            "atlas_events_url": "/api/atlas/events/%s" % job_id,
        })
    return out, str(index_path)


def collect_crowned(paths, limit, warnings):
    """Recent crowned frames from the crowned genome ledger."""
    path = paths.genome_path
    if not path.exists():
        return [], "%s absent (nothing crowned on this machine yet)" % path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        warnings.append("crowned genome unreadable: %s" % _short(exc))
        return [], "unreadable: %s" % _short(exc)
    out = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        url = ""
        disk = entry.get("path") or ""
        if disk:
            try:
                rel = pathlib.Path(disk).relative_to(paths.out_dir)
                url = "/outputs/" + str(rel).replace(os.sep, "/")
            except ValueError:
                url = ""
        out.append({
            "job_id": entry.get("job_id"),
            "cell": entry.get("cell"),
            "score": entry.get("score"),
            "epigram": entry.get("epigram", ""),
            "prompt": entry.get("prompt", ""),
            "ts": entry.get("ts"),
            "url": url,
            "path": disk,
        })
        if len(out) >= limit:
            break
    return out, str(path)


def build_manifest(paths, routes, surfaces, continuum, warnings, crowned_limit):
    roster = continuum.roster
    budget = continuum.budget
    runs, runs_source = collect_runs(paths, warnings)
    crowned, crowned_source = collect_crowned(paths, crowned_limit, warnings)
    drafts = collect_drafts(paths, warnings)

    verdict = continuum.verdict if isinstance(continuum.verdict, dict) else {}
    hardware = continuum.hardware if isinstance(continuum.hardware, dict) else {}
    now = time.time()

    return {
        "kind": MANIFEST_KIND,
        "version": MANIFEST_VERSION,
        "generated_by": "provision_surfaces.py %s" % __version__,
        "generated": now,
        "generated_iso": datetime.datetime.fromtimestamp(
            now, datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "paths": paths.as_dict(),
        "surfaces": [
            {
                "surface": s["surface"],
                "file": s["file"],
                "routes": s["routes"],
                "bytes": s["bytes"],
                "status": s["status"],
                "title": s["title"],
                "public_readonly": s["public_readonly"],
            }
            for s in surfaces
        ],
        "endpoints": dict(SURFACE_ENDPOINTS),
        "profile": {
            "name": continuum.profile_name,
            "origin": continuum.origin,
            "description": hardware.get("description"),
            "gpu": hardware.get("gpu"),
            "sm": hardware.get("sm"),
            "vram_gib": hardware.get("vram_gib"),
            "gpu_count": hardware.get("gpu_count"),
            "vram_per_gpu_gib": hardware.get("vram_per_gpu_gib"),
            "reserve_gib": hardware.get("reserve_gib"),
            "notes": hardware.get("notes"),
            "available_profiles": continuum.available_profiles,
            "cadence_seconds": continuum.cadence_seconds,
            "ports": continuum.ports,
            "source": continuum.source,
        },
        "roster": roster,
        "vram_budget": budget,
        "verdict": {
            "masterpiece_threshold": verdict.get("masterpiece_threshold"),
            "weights": verdict.get("weights"),
            "tiers": verdict.get("tiers"),
            "jurors": verdict.get("jurors"),
            "tier_badges": {
                "crowned": {"glyph": "\U0001F451", "label": "masterpiece"},
                "kept": {"glyph": "\u2728", "label": "spectacle"},
                "drift": {"glyph": "\u2013", "label": "drift"},
                "unscored": {"glyph": "\u2298", "label": "unscored"},
            },
            "source": "%s [verdict]" % continuum.source,
        },
        "fortiche": {
            "invariants": FORTICHE_INVARIANTS,
            "source": "docs/ARCANE_LATENT_CARTOGRAPHY_PROTOCOL_SPEC.md section 3",
        },
        "drafts": drafts,
        "runs": runs,
        "crowned": crowned,
        "sources": {
            "runs": runs_source,
            "crowned": crowned_source,
            "continuum": str(paths.home / "jury_continuum.toml"),
            "server_go": str(paths.server_go),
        },
        "data_available": {
            "out_dir": paths.out_dir.is_dir(),
            "profile": bool(continuum.profile_name),
            "runs": bool(runs),
            "crowned": bool(crowned),
            "drafts": bool([d for d in drafts if d.get("valid")]),
            "roster": bool(roster),
        },
        "spec_claims_unverified": {
            "cache_hit_rate": SPEC_CLAIM_HIT_RATE,
            "seconds_per_cell": SPEC_CLAIM_SECONDS_PER_CELL,
            "source": "docs/ARCANE_LATENT_CARTOGRAPHY_PROTOCOL_SPEC.md section 2.2",
            "note": "Aspirational claims from the spec. The dashboard must render "
                    "measured values from the atlas stream and never substitute these.",
        },
        "warnings": list(warnings),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_table(log, surfaces):
    headers = ("SURFACE", "ROUTE", "FILE", "SIZE", "PUBLIC", "STATUS")
    rows = []
    for s in surfaces:
        public = "-" if s["public_readonly"] is None else ("yes" if s["public_readonly"] else "no")
        rows.append((
            s["surface"],
            ", ".join(s["routes"]) or "(none)",
            s["file"],
            human_bytes(s["bytes"]),
            public,
            s["status"],
        ))
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    # Keep the route column from running away on wide terminals.
    widths[1] = min(widths[1], 52)

    def line(cells):
        out = []
        for i, cell in enumerate(cells):
            text = cell if len(cell) <= widths[i] else cell[:widths[i] - 1] + "…"
            out.append(text.ljust(widths[i]))
        return "  ".join(out).rstrip()

    log(line(headers))
    log("  ".join("-" * w for w in widths))
    for row in rows:
        log(line(row))


def report(log, paths, routes, surfaces, manifest, warnings, orphans):
    log()
    log("ARCANE SURFACE PROVISIONER · v%s" % __version__)
    log("  FLUX_HOME   %s" % paths.home)
    log("  PUBLIC_DIR  %s" % paths.public_dir)
    log("  OUT_DIR     %s%s" % (paths.out_dir, "" if paths.out_dir.is_dir() else "  (absent here)"))
    log("  paths from  %s" % paths.source)
    log("  routes from %s" % (paths.server_go if routes.available else "static fallback table"))
    log()
    render_table(log, surfaces)
    log()

    failures = [s for s in surfaces if s["status"] == FAIL]
    warned = [s for s in surfaces if s["status"] == WARN]
    log("  %d surface(s) · %d ok · %d warn · %d FAIL"
        % (len(surfaces), len(surfaces) - len(failures) - len(warned), len(warned), len(failures)))

    for s in surfaces:
        if not (s["problems"] or s["notes"] or s["broken_refs"] or s["unrouted_refs"]):
            continue
        log()
        log("  %s (%s)" % (s["surface"], s["file"]))
        for problem in s["problems"]:
            log("    FAIL  %s" % problem)
        for ref in s["broken_refs"]:
            log("    FAIL  broken local reference: %s" % ref)
        for ref in s["unrouted_refs"]:
            log("    warn  %s" % ref)
        for note in s["notes"]:
            log("    note  %s" % note)

    external = {}
    for s in surfaces:
        for url in s["external_refs"]:
            external.setdefault(url, []).append(s["file"])
    if external:
        log()
        log("  External references (reported, never fetched):")
        for url in sorted(external):
            log("    %-72s %s" % (url[:72], ", ".join(sorted(external[url]))))

    if orphans:
        log()
        log("  Page files in apps/tea/public/ with no server route:")
        for name in orphans:
            log("    %s" % name)

    if manifest is not None:
        log()
        log("  Manifest")
        profile = manifest["profile"]
        if profile["name"]:
            log("    profile        %s (via %s, from %s) · %s"
                % (profile["name"], profile["origin"], profile["source"],
                   profile.get("gpu") or "gpu unknown"))
        else:
            log("    profile        unavailable -- omitted rather than guessed")
        budget = manifest["vram_budget"]
        if budget.get("capacity_gib"):
            log("    vram budget    %s / %s GiB allocated · %s GiB free · reserve %s GiB%s"
                % (budget["allocated_gib"], budget["capacity_gib"], budget["free_gib"],
                   budget["reserve_gib"], "  OVER BUDGET" if budget["over_budget"] else ""))
        else:
            log("    vram budget    unavailable")
        log("    roster         %d tenant(s): %s"
            % (len(manifest["roster"]),
               ", ".join("%s%s" % (t["tenant"], "" if t["enabled"] else " (off)")
                         for t in manifest["roster"]) or "none"))
        log("    atlas drafts   %d" % len([d for d in manifest["drafts"] if d.get("valid")]))
        log("    pipeline runs  %d  (%s)" % (len(manifest["runs"]), manifest["sources"]["runs"]))
        log("    crowned frames %d  (%s)" % (len(manifest["crowned"]), manifest["sources"]["crowned"]))
        log("    invariants     %s"
            % ", ".join(i["key"] for i in manifest["fortiche"]["invariants"]))

    if warnings:
        log()
        log("  Warnings")
        for text in warnings:
            log("    - %s" % text)
    log()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def write_json(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="provision_surfaces.py",
        description="Verify the Arcane studio web surfaces and publish their manifest.")
    parser.add_argument("--check", action="store_true",
                        help="verify only; write nothing and exit non-zero on failure")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except write files")
    parser.add_argument("--json", action="store_true",
                        help="emit the full report as JSON on stdout")
    parser.add_argument("--profile", default="",
                        help="hardware profile from jury_continuum.toml (default: its default_profile)")
    parser.add_argument("--out", default="",
                        help="manifest path (default: apps/tea/public/%s)" % MANIFEST_NAME)
    parser.add_argument("--crowned-limit", type=int, default=24,
                        help="how many recent crowned frames to carry (default 24)")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures")
    opts = parser.parse_args(argv)

    # pipeline_paths.load_continuum() reads ARCANE_PROFILE at import/call time,
    # so the override has to be in the environment before Paths.resolve() runs.
    requested = opts.profile.strip()
    if requested:
        os.environ["ARCANE_PROFILE"] = requested

    log = Log(load_module("arcane_log"), quiet=opts.json)

    paths = Paths.resolve()
    warnings = list(paths.warnings)

    routes = ServerRoutes.parse(paths.server_go,
                                paths.home / "internal" / "server" / "studies.go",
                                paths.home / "internal" / "server" / "stallion_motion.go")
    warnings.extend(routes.warnings)

    rows = routes.surfaces() if routes.available else list(FALLBACK_SURFACES)
    if not rows:
        warnings.append("no page-serving handlers found in server.go; using static fallback table")
        rows = list(FALLBACK_SURFACES)

    seen_files = set()
    surfaces = [check_surface(paths, routes, row, seen_files) for row in rows]
    surfaces.sort(key=lambda s: s["surface"])

    orphans = []
    if paths.public_dir.is_dir():
        for path in sorted(paths.public_dir.glob("*.html")):
            if path.name not in seen_files:
                orphans.append(path.name)
                warnings.append("%s is checked in but no server route serves it" % path.name)

    continuum = Continuum.resolve(paths, requested, warnings)
    manifest = build_manifest(paths, routes, surfaces, continuum,
                              warnings, max(1, opts.crowned_limit))

    failures = [s for s in surfaces if s["status"] == FAIL]
    warned = [s for s in surfaces if s["status"] == WARN]

    written = []
    would_write = [str(pathlib.Path(opts.out).expanduser()) if opts.out
                   else str(paths.public_dir / MANIFEST_NAME)]
    if not opts.out and paths.out_dir.is_dir():
        would_write.append(str(paths.surface_dir / "surface_manifest.json"))

    if not opts.check and not opts.dry_run:
        for target in would_write:
            try:
                write_json(pathlib.Path(target), manifest)
                written.append(target)
            except OSError as exc:
                warnings.append("could not write %s: %s" % (target, _short(exc)))
        manifest["warnings"] = list(warnings)

    mode = "check" if opts.check else ("dry-run" if opts.dry_run else "provision")
    ok = not failures and (not opts.strict or not warned)

    if opts.json:
        json.dump({
            "kind": "arcane_surface_report",
            "version": 1,
            "generated_by": "provision_surfaces.py %s" % __version__,
            "mode": mode,
            "ok": ok,
            "log_backend": log.backend,
            "counts": {
                "surfaces": len(surfaces),
                "ok": len(surfaces) - len(failures) - len(warned),
                "warn": len(warned),
                "fail": len(failures),
                "orphans": len(orphans),
            },
            "surfaces": surfaces,
            "orphan_pages": orphans,
            "manifest_written": written,
            "manifest_would_write": would_write if not written else [],
            "manifest": manifest,
            "warnings": warnings,
        }, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        report(log, paths, routes, surfaces, manifest, warnings, orphans)
        if written:
            for target in written:
                log("  wrote %s (%s)" % (target, human_bytes(pathlib.Path(target).stat().st_size)))
        elif opts.check:
            log("  --check: nothing written.")
        elif opts.dry_run:
            for target in would_write:
                log("  --dry-run: would write %s" % target)
        log()
        log("  %s: %s" % (mode, "PASS" if ok else "FAIL"))
        log()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
