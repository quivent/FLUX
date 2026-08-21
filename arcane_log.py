#!/usr/bin/env python3
"""One presentation layer for the Arcane pipeline, in Python, matching the Go CLI.

WHY THIS EXISTS
`internal/ui/ui.go` already decides what this system looks like: violet headers
over an indigo rule, `⟐` for a step, `●` for a state, `█░` for a bar, keys
uppercased in ink-dim and padded to a fixed column. Every Go surface obeys it.
The Python daemons did not -- they emitted `print(f"[JURY VERDICT] ...")` -- so
`flux arcane provision` and `tail -f jury_evaluator.log` looked like two
different products written by two different teams, and the second one looked
like a debug trace someone forgot to remove.

This module is that same vocabulary in Python. Same glyphs, same palette, same
column widths, same ANSI-aware padding. The rule is simple and it is the whole
design constraint: a reader must not be able to tell which language printed a
line.

THE TWO SINKS
Every call emits twice, and the two sinks answer different questions.

  the terminal   is for a human watching a 65,536-cell atlas job crawl for
                 three days. It is allowed to be beautiful: bars, badges, a
                 crown on a masterpiece. It is throttled, width-aware, and it
                 drops every escape code the moment stdout stops being a TTY,
                 because that is exactly when it is a log file.

  the JSONL      is for the studio web surfaces that tail it. One object per
                 line, appended under an advisory lock so five daemons on one
                 card cannot interleave a record, with a schema that is allowed
                 to grow but not to change shape. Documented in
                 docs/ARCANE_LOGGING.md; treat it as an interface, not a dump.

HONESTY IS A RENDERING CONCERN
`degraded()` is a first-class method and `verdict()` refuses to dress up an
unscored receipt, because the failure mode this pipeline is being rebuilt to
escape is a fabricated number that LOOKS like a measurement. A receipt whose
jury lost quorum renders dimmed, struck, and captioned with the reason -- never
with a bar, never with a tier badge, never with a composite. If you find
yourself wanting these renderers to make a degraded run look healthy, the bug
is upstream, not here.

    from arcane_log import get_logger
    log = get_logger("jury")
    log.header("ARCANE · JURY", "moj 3.0.0")
    log.verdict(receipt)

stdlib only, on purpose: these run inside a minimal venv on a GPU node where
`pip install rich` is not a thing that is going to happen.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:  # POSIX only; the lock degrades to thread-safety alone without it.
    import fcntl
except Exception:  # pragma: no cover - Windows
    fcntl = None

__all__ = ["get_logger", "ArcaneLogger", "Style", "SCHEMA_VERSION",
           "visible_len", "pad_visible", "truncate", "truncate_ansi"]

SCHEMA_VERSION = 1

# ---------------------------------------------------------------- palette
# Lifted verbatim from internal/ui/ui.go. Do not "improve" these numbers in one
# language without moving them in the other; the whole point is that they match.

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

VIOLET = "\033[38;5;141m"
LILAC = "\033[38;5;183m"
INDIGO = "\033[38;5;99m"
TEAL = "\033[38;5;73m"
MINT = "\033[38;5;121m"
GOLD = "\033[38;5;220m"
ROSE = "\033[38;5;204m"
AMBER = "\033[38;5;214m"
RED = "\033[31m"
LINE = "\033[38;5;238m"
INKDIM = "\033[38;5;246m"

# Realm palettes. Arcane's two cities, as the conformance scorer means them.
# Truecolor first, xterm-256 cube fallback second (48 = spring green, 45 = deep
# sky blue -- the nearest cube entries to the two hexes).
ZAUN_RGB = (0x00, 0xFF, 0x88)      # chemtech emerald
PILTOVER_RGB = (0x00, 0xD2, 0xFF)  # hextech cyan
ZAUN_256 = 48
PILTOVER_256 = 45

NAMED = {
    "violet": VIOLET, "lilac": LILAC, "indigo": INDIGO, "teal": TEAL,
    "mint": MINT, "gold": GOLD, "rose": ROSE, "amber": AMBER,
    "red": RED, "line": LINE, "ink": INKDIM, "inkdim": INKDIM,
}

# ---------------------------------------------------------------- glyphs
# `ASCII` is the fallback for terminals that mangle box drawing. It is not the
# default anywhere; set ARCANE_LOG_ASCII=1 if you are stuck on one.

GLYPH = {
    "step": "⟐", "dot": "●", "cross": "✕", "diamond": "◆", "wedge": "▸",
    "tee": "├─", "ell": "└─", "pipe": "│  ", "bar": "│", "rule": "━",
    "thin": "─", "full": "█", "empty": "░", "over": "▓", "tri": "▲",
    "into": "↳", "mid": "·", "crown": "👑", "spark": "✨", "ellipsis": "…",
    "lend": "├", "rend": "┤", "mark": "●", "arrow": "→",
}
GLYPH_ASCII = {
    "step": "*", "dot": "o", "cross": "x", "diamond": "#", "wedge": ">",
    "tee": "+-", "ell": "`-", "pipe": "|  ", "bar": "|", "rule": "=",
    "thin": "-", "full": "#", "empty": ".", "over": "!", "tri": "!",
    "into": "->", "mid": ".", "crown": "[*]", "spark": "[+]", "ellipsis": "...",
    "lend": "[", "rend": "]", "mark": "o", "arrow": "->",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Truncation marker, decided once so ARCANE_LOG_ASCII=1 stays byte-clean.
_CUT = "..." if os.environ.get("ARCANE_LOG_ASCII", "").strip().lower() \
    not in ("", "0", "false", "no", "off") else "…"
_ZERO_WIDTH = {"️", "︎", "‍", "​"}

LEVELS = {"debug": 10, "soft": 20, "info": 20, "ok": 20, "warn": 30, "error": 40}
_THRESHOLD = {"debug": 10, "soft": 15, "info": 20, "warn": 30, "error": 40,
              "quiet": 30, "silent": 100}


# ============================================================ text mechanics


def visible_len(value: str) -> int:
    """Printed cell width, ignoring escape codes and counting 👑 as two.

    The Go side gets away with `len(strip(s))` because nothing it prints is
    wide. This side prints a crown, so it cannot.
    """
    plain = _ANSI_RE.sub("", value)
    width = 0
    for ch in plain:
        if ch in _ZERO_WIDTH or unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad_visible(value: str, width: int) -> str:
    """Left-align to `width` printed cells. The ANSI-aware `%-Ns`."""
    return value + " " * max(0, width - visible_len(value))


def rpad_visible(value: str, width: int) -> str:
    """Right-align to `width` printed cells."""
    return " " * max(0, width - visible_len(value)) + value


def cpad_visible(value: str, width: int) -> str:
    """Centre within `width` printed cells."""
    slack = max(0, width - visible_len(value))
    left = slack // 2
    return " " * left + value + " " * (slack - left)


def truncate(value: str, width: int, tail: str = "") -> str:
    """Cut plain text to `width` cells, marking the cut. Not ANSI-aware: pass
    unpainted text, then paint the result."""
    tail = tail or _CUT
    if visible_len(value) <= width:
        return value
    keep = max(0, width - visible_len(tail))
    out = []
    used = 0
    for ch in value:
        w = 0 if ch in _ZERO_WIDTH or unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if used + w > keep:
            break
        out.append(ch)
        used += w
    return "".join(out) + tail


def truncate_ansi(value: str, width: int, tail: str = "") -> str:
    """`truncate` for a string that already carries escape codes: colour runs
    are copied through at zero width, and the result is closed with a reset so
    a cut in the middle of a painted run cannot bleed into the next column."""
    tail = tail or _CUT
    if visible_len(value) <= width:
        return value
    keep = max(0, width - visible_len(tail))
    out, used, index, painted = [], 0, 0, False
    while index < len(value):
        match = _ANSI_RE.match(value, index)
        if match:
            out.append(match.group(0))
            painted = True
            index = match.end()
            continue
        ch = value[index]
        step = 0 if ch in _ZERO_WIDTH or unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if used + step > keep:
            break
        out.append(ch)
        used += step
        index += 1
    out.append(tail)
    if painted:
        out.append(RESET)
    return "".join(out)


def wrap(value: str, width: int):
    """Greedy word wrap on plain text. Long tokens are hard-split, never
    allowed to blow the column."""
    words = str(value).split()
    if not words:
        return [""]
    lines, cur = [], ""
    for word in words:
        while visible_len(word) > width:
            head = truncate(word, width, "")
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(head)
            word = word[len(head):]
        candidate = word if not cur else cur + " " + word
        if visible_len(candidate) > width:
            lines.append(cur)
            cur = word
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines


# ============================================================ style


class Style:
    """Colour capability for one stream, decided once at construction.

    Precedence, and it is the conventional one: NO_COLOR wins over everything,
    then the FORCE_COLOR family, then TERM=dumb, then isatty(). FLUX_NO_COLOR
    and FLUX_FORCE_COLOR are honoured so a shell that has already configured
    the Go CLI configures this too.
    """

    __slots__ = ("enabled", "depth", "tty", "ascii", "width", "glyphs")

    def __init__(self, stream=None, width: int = 0):
        stream = stream if stream is not None else sys.stdout
        try:
            self.tty = bool(stream.isatty())
        except Exception:
            self.tty = False
        self.enabled = self._decide(self.tty)
        self.depth = self._depth() if self.enabled else "none"
        self.ascii = _flag("ARCANE_LOG_ASCII", False)
        self.glyphs = GLYPH_ASCII if self.ascii else GLYPH
        self.width = width or self._width()

    @staticmethod
    def _decide(tty: bool) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FLUX_NO_COLOR"):
            return False
        for key in ("FORCE_COLOR", "FLUX_FORCE_COLOR", "ATELIER_FORCE_COLOR",
                    "CLICOLOR_FORCE"):
            value = os.environ.get(key)
            if value not in (None, "", "0"):
                return True
        if os.environ.get("TERM", "") == "dumb":
            return False
        return tty

    @staticmethod
    def _depth() -> str:
        ct = os.environ.get("COLORTERM", "").lower()
        if "truecolor" in ct or "24bit" in ct:
            return "truecolor"
        return "256"

    @staticmethod
    def _width() -> int:
        override = os.environ.get("ARCANE_LOG_WIDTH", "")
        if override.isdigit():
            return max(60, min(200, int(override)))
        try:
            cols = shutil.get_terminal_size((100, 24)).columns
        except Exception:
            cols = 100
        return max(72, min(120, cols))

    # -- painting ---------------------------------------------------------

    def paint(self, code: str, text: str) -> str:
        if not self.enabled or not code:
            return text
        return code + text + RESET

    def bold(self, text: str) -> str:
        return self.paint(BOLD, text)

    def dim(self, text: str) -> str:
        return self.paint(DIM, text)

    def rgb(self, rgb, fallback256: int) -> str:
        """Escape for a specific colour, degrading truecolor -> 256 -> nothing."""
        if not self.enabled:
            return ""
        if self.depth == "truecolor":
            return "\033[38;2;%d;%d;%dm" % rgb
        return "\033[38;5;%dm" % fallback256

    def zaun(self) -> str:
        return self.rgb(ZAUN_RGB, ZAUN_256)

    def piltover(self) -> str:
        return self.rgb(PILTOVER_RGB, PILTOVER_256)

    def g(self, name: str) -> str:
        return self.glyphs.get(name, GLYPH.get(name, "?"))

    def sep(self) -> str:
        """The inline separator, so ARCANE_LOG_ASCII=1 is genuinely ASCII."""
        return " %s " % self.g("mid")


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


# ============================================================ semantic ink


def state_ink(st: Style, value) -> str:
    """`ui.State` -- the same buckets, the same colours, the same surprises
    (a `resident` model is rose, because a hot model is holding your VRAM)."""
    text = str(value)
    key = text.strip().lower()
    if key in ("ok", "ready", "true", "online", "active", "validated",
               "complete", "done", "present", "pass", "passed", "fits",
               "conformant", "healthy"):
        return st.paint(BOLD, st.paint(MINT, text))
    if key in ("unknown", "pending", "queued", "running", "partial", "warm",
               "starting", "planned", "degraded", "provisional", "cold"):
        return st.paint(BOLD, st.paint(AMBER, text))
    if key in ("fail", "failed", "false", "missing", "blocked", "stale",
               "down", "error", "reject", "rejected", "unscored", "overcommit"):
        return st.paint(BOLD, st.paint(ROSE, text))
    if key in ("resident", "loaded", "hot"):
        return st.paint(BOLD, st.paint(ROSE, text))
    return st.paint(TEAL, text)


def score_ink(st: Style, fraction: float) -> str:
    """Bar colour as a function of how good the number is. Gold is reserved
    for the top of the range so a masterpiece bar reads as gold at a glance."""
    if fraction >= 0.95:
        return GOLD
    if fraction >= 0.80:
        return MINT
    if fraction >= 0.60:
        return TEAL
    if fraction >= 0.40:
        return AMBER
    return ROSE


def bar(st: Style, fraction, width: int, code: str = "", empty_code: str = "") -> str:
    """`ui.Progress`'s bar, reusable. `fraction` of None renders as all-empty,
    which is what an unmeasurable metric should look like."""
    if fraction is None:
        return st.paint(empty_code or LINE, st.g("empty") * width)
    frac = max(0.0, min(1.0, float(fraction)))
    filled = int(round(frac * width))
    filled = max(0, min(width, filled))
    if frac > 0 and filled == 0:
        filled = 1
    ink = code or score_ink(st, frac)
    return (st.paint(ink, st.g("full") * filled)
            + st.paint(empty_code or LINE, st.g("empty") * (width - filled)))


def _trim(line: str) -> str:
    """Strip trailing padding. A table's last column is padded to its width and
    those spaces are invisible on screen but real in a log file."""
    return line.rstrip(" ")


def _first(*values):
    """First value that is not None. `or` is wrong here: 0.0 GiB of VRAM is a
    real, meaningful reading (the governor, when it is remote)."""
    for value in values:
        if value is not None:
            return value
    return None


def _num(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fraction(value, hi: float = 100.0):
    """Normalise a score to 0..1, tolerating both 0..1 and 0..100 conventions."""
    number = _num(value)
    if number is None:
        return None
    if hi <= 0:
        return None
    return max(0.0, min(1.0, number / hi))


def _dur(seconds) -> str:
    value = _num(seconds)
    if value is None:
        return "—"
    if value < 1.0:
        return "%dms" % round(value * 1000)
    if value < 60:
        return "%.1fs" % value
    minutes, secs = divmod(int(value), 60)
    if minutes < 60:
        return "%dm %02ds" % (minutes, secs)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%dh %02dm" % (hours, minutes)
    days, hours = divmod(hours, 24)
    return "%dd %02dh" % (days, hours)


def _gib(value) -> str:
    number = _num(value)
    return "—" if number is None else "%.2f" % number


def _short(value, limit: int = 48) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return truncate(text, limit)


# ============================================================ the JSONL sink


class _Sink:
    """One append-only JSONL file, shared by every logger in this process that
    resolved to the same path, and safe against the other four daemons.

    Multi-process safety is two things and it needs both: O_APPEND so the
    kernel places the write at the true end of file even after another process
    grew it, and one `os.write` of one complete line so a record is one
    syscall. `flock` on top of that closes the residual window where a line is
    long enough to be split. `flock` is advisory and POSIX-only; the O_APPEND
    single-write property is what actually carries the guarantee.
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        self.broken = False

    @classmethod
    def acquire(cls, path):
        path = Path(path)
        key = str(path.resolve()) if path.exists() else str(path)
        with cls._registry_lock:
            sink = cls._registry.get(key)
            if sink is None:
                sink = cls(path)
                cls._registry[key] = sink
            return sink

    def write(self, record: dict) -> None:
        if self.broken:
            return
        try:
            payload = (json.dumps(record, default=str, ensure_ascii=False)
                       + "\n").encode("utf-8")
        except Exception:
            return
        with self.lock:
            try:
                if fcntl is not None:
                    fcntl.flock(self.fd, fcntl.LOCK_EX)
                try:
                    os.write(self.fd, payload)
                finally:
                    if fcntl is not None:
                        fcntl.flock(self.fd, fcntl.LOCK_UN)
            except Exception:
                # A dead sink must never take a daemon down with it.
                self.broken = True

    def close(self) -> None:
        with self.lock:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.broken = True

    @classmethod
    def close_all(cls) -> None:
        with cls._registry_lock:
            for sink in cls._registry.values():
                sink.close()
            cls._registry.clear()


atexit.register(_Sink.close_all)


def _candidate_dirs():
    """Where the JSONL goes, best first.

    `pipeline_paths` is imported defensively because it is being written in the
    next chair over; everything here works whether or not it lands. Candidates
    3 and 4 require their PARENT to already exist -- a dev laptop should not
    have `~/Models/flux-output/` conjured into being just because it imported a
    logger. Only `logs/` is ever created.
    """
    seen, out = set(), []

    def add(base, require_parent=True, suffix="logs"):
        if not base:
            return
        path = Path(str(base)).expanduser()
        if suffix:
            path = path / suffix
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        out.append((path, require_parent))

    add(os.environ.get("ARCANE_LOG_DIR", "").rstrip("/") or None, False)

    try:
        import pipeline_paths  # type: ignore
        # LOG_DIR is already the log directory; OUT_DIR is the settled-output
        # root and needs the logs/ segment. Take whichever agent 5 exposes.
        for attr, suffix in (("LOG_DIR", ""), ("OUT_DIR", "logs"),
                             ("OUTPUT_DIR", "logs"), ("out_dir", "logs"),
                             ("output_dir", "logs")):
            value = getattr(pipeline_paths, attr, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue
            if value:
                add(value, suffix=suffix)
                break
    except Exception:
        pass

    flux_home = os.environ.get("FLUX_HOME") or str(Path(__file__).resolve().parent)
    add(Path(flux_home) / "outputs")

    try:
        import flux_paths  # type: ignore
        add(flux_paths.default_out_dir())
    except Exception:
        pass

    add(Path(tempfile.gettempdir()) / "arcane-logs", False, suffix="")
    return out


def _resolve_sink(component: str, explicit=None):
    """Return (path, fell_back). Never raises; the worst case is a temp dir."""
    if explicit:
        path = Path(str(explicit)).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path, False
        except Exception:
            pass
    name = "arcane-%s.jsonl" % re.sub(r"[^A-Za-z0-9._-]+", "-", component or "pipeline")
    candidates = _candidate_dirs()
    for index, (directory, require_parent) in enumerate(candidates):
        try:
            if require_parent and not directory.parent.is_dir():
                continue
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / name
            with open(probe, "a"):
                pass
            return probe, index > 0
        except Exception:
            continue
    return Path(tempfile.gettempdir()) / name, True


# ============================================================ run identity

_RUN_ID_LOCK = threading.Lock()
_RUN_ID = None


def _run_id() -> str:
    """One id per process, or one id per fleet launch if ARCANE_RUN_ID is
    exported by the supervisor -- which is what you want, so the studio can
    stitch five daemons into one run."""
    global _RUN_ID
    with _RUN_ID_LOCK:
        if _RUN_ID is None:
            forced = os.environ.get("ARCANE_RUN_ID", "").strip()
            if forced:
                _RUN_ID = forced
            else:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                _RUN_ID = "arc-%s-%04x" % (stamp, os.getpid() & 0xFFFF)
        return _RUN_ID


# ============================================================ the logger

_STDOUT_LOCK = threading.RLock()
_LOGGERS = {}
_LOGGERS_LOCK = threading.Lock()


class ArcaneLogger:
    """The one object the daemons hold. Construct it with `get_logger`."""

    # -- construction -----------------------------------------------------

    def __init__(self, component: str, jsonl=None, level: str = "info",
                 stream=None):
        self.component = component or "pipeline"
        self.stream = stream if stream is not None else sys.stdout
        self.st = Style(self.stream)
        self.level = level if level in _THRESHOLD else "info"
        self.threshold = _THRESHOLD[self.level]
        self.sink_path, self.sink_fallback = _resolve_sink(self.component, jsonl)
        try:
            self.sink = _Sink.acquire(self.sink_path)
        except Exception:
            self.sink = None
        self.stamp = _flag("ARCANE_LOG_STAMP", False)
        self._rid = _run_id()
        self._progress_open = False
        self._progress = {}
        self._interval = float(os.environ.get("ARCANE_PROGRESS_INTERVAL", "30") or 30)

    def run_id(self) -> str:
        return self._rid

    # -- the two sinks ----------------------------------------------------

    def _record(self, level: str, kind: str, message: str, fields: dict) -> None:
        if self.sink is None:
            return
        now = time.time()
        self.sink.write({
            "v": SCHEMA_VERSION,
            "ts": datetime.fromtimestamp(now, timezone.utc)
                          .isoformat(timespec="milliseconds")
                          .replace("+00:00", "Z"),
            "epoch": round(now, 6),
            "run_id": self._rid,
            "component": self.component,
            "level": level,
            "kind": kind,
            "message": message,
            "fields": fields or {},
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
        })

    def _prefix(self) -> str:
        if not self.stamp:
            return ""
        return "%s %s " % (time.strftime("%H:%M:%S"),
                           pad_visible(self.component, 9))

    def _emit(self, lines, newline: bool = True) -> None:
        """One write per call. A panel is one syscall, so two daemons sharing a
        nohup log can never split one apart."""
        if isinstance(lines, str):
            lines = [lines]
        prefix = self._prefix()
        lines = [_trim(line) for line in lines]
        text = "".join((_trim(prefix + line) + "\n") for line in lines)
        if not newline:
            text = "".join(lines)
        with _STDOUT_LOCK:
            if self._progress_open and newline:
                text = "\n" + text
                self._progress_open = False
            try:
                self.stream.write(text)
                self.stream.flush()
            except Exception:
                pass

    def _say(self, level: str, kind: str, message: str, fields: dict, lines) -> None:
        if LEVELS.get(level, 20) >= self.threshold:
            self._emit(lines)
        self._record(level, kind, message, fields)

    @property
    def width(self) -> int:
        return self.st.width

    # ================================================== structure

    def header(self, title: str, subtitle: str = "") -> None:
        """`ui.Header`. Bold violet title, dim subtitle, indigo rule."""
        st = self.st
        line = st.paint(BOLD, st.paint(VIOLET, str(title)))
        if subtitle:
            line += st.paint(DIM, "  " + str(subtitle))
        self._say("info", "header", str(title),
                  {"title": title, "subtitle": subtitle},
                  ["", line, self._rule_text()])

    def _rule_text(self, label: str = "") -> str:
        st = self.st
        width = min(self.width, 96)
        if not label:
            return st.paint(INDIGO, st.g("rule") * width)
        tag = " %s " % str(label).upper()
        fill = max(3, width - visible_len(tag) - 3)
        return (st.paint(INDIGO, st.g("rule") * 3)
                + st.paint(BOLD, st.paint(INDIGO, tag))
                + st.paint(INDIGO, st.g("rule") * fill))

    def rule(self, label: str = "") -> None:
        """`ui.Rule`, with an optional inline caption."""
        self._say("info", "rule", str(label), {"label": label},
                  [self._rule_text(label)])

    def kv(self, key=None, value=None, *, state=None, **fields) -> None:
        """`ui.KV`. Key uppercased in ink-dim, padded to 18 cells, value after.

        Two call shapes, because both read naturally at a call site:

            log.kv("profile", "blackwell-96", state="active")
            log.kv(tier="full", backend="siglip+dinov2", device="cuda:0")

        The second emits one aligned row per keyword, in the order given.
        """
        if key is None and fields:
            for name, item in fields.items():
                self.kv(name, item)
            return
        st = self.st
        rendered = (state_ink(st, value) if state is None
                    else "%s  %s" % (str(value), state_ink(st, state)))
        line = "  %s %s" % (pad_visible(st.paint(INKDIM, str(key).upper()), 18),
                            rendered)
        self._say("info", "kv", "%s=%s" % (key, value),
                  {"key": key, "value": value, "state": state}, [line])

    def table(self, headers=None, rows=None, *, title: str = "", aligns=None,
              **kwargs) -> None:
        """A plain column table with ANSI-aware widths. Right-align anything
        numeric via `aligns=['l','l','r']`; the eye needs the decimal points
        stacked or a VRAM column is just noise."""
        st = self.st
        # Tolerate the `table(rows, headers=[...])` shape some callers use: if
        # the first positional is a list of rows, the arguments are swapped.
        if "headers" in kwargs and rows is None:
            headers, rows = kwargs["headers"], headers
        if headers and rows is None:
            first = headers[0] if headers else None
            if isinstance(first, (list, tuple)):
                headers, rows = kwargs.get("headers") or [], headers
        headers = [str(h) for h in (headers or [])]
        rows = rows or []
        if not headers and rows:
            headers = [""] * len(rows[0])
        body = [[("" if c is None else str(c)) for c in row] for row in rows]
        count = len(headers)
        aligns = list(aligns or ["l"] * count)
        aligns += ["l"] * (count - len(aligns))
        widths = [visible_len(h) for h in headers]
        for row in body:
            for i in range(min(count, len(row))):
                widths[i] = max(widths[i], visible_len(row[i]))
        # Shrink the widest column until the table fits the terminal. A table
        # that overflows soft-wraps and stops being a table, so losing the tail
        # of one long model id is the cheaper trade.
        avail = max(20, self.width - 2 - 2 * (count - 1))
        guard = 0
        while sum(widths) > avail and guard < 4096:
            widest = widths.index(max(widths))
            if widths[widest] <= 8:
                break
            widths[widest] -= 1
            guard += 1
        lines = []
        if title:
            lines.append("  " + st.paint(BOLD, st.paint(TEAL, str(title))))
        head = []
        for i, name in enumerate(headers):
            cell = st.paint(INKDIM, name.upper())
            head.append(rpad_visible(cell, widths[i]) if aligns[i] == "r"
                        else pad_visible(cell, widths[i]))
        lines.append("  " + "  ".join(head))
        lines.append("  " + st.paint(LINE, st.g("thin") * (sum(widths) + 2 * (count - 1))))
        for row in body:
            cells = []
            for i in range(count):
                raw = row[i] if i < len(row) else ""
                if visible_len(raw) > widths[i]:
                    raw = truncate_ansi(raw, widths[i])
                if aligns[i] == "r":
                    cells.append(rpad_visible(raw, widths[i]))
                elif aligns[i] == "c":
                    cells.append(cpad_visible(raw, widths[i]))
                else:
                    cells.append(pad_visible(raw, widths[i]))
            lines.append("  " + "  ".join(cells))
        self._say("info", "table", title or "table",
                  {"title": title, "headers": headers, "rows": rows}, lines)

    def panel(self, title: str, body: str, *, meta: str = "", color: str = "teal") -> None:
        """`ui.Capsule` with a gutter. The `│` is the same one `ui.Tree` uses,
        so a panel reads as a branch of the same drawing."""
        st = self.st
        code = NAMED.get(str(color).lower(), TEAL)
        lines = ["%s %s%s" % (st.paint(code, st.g("diamond")),
                              st.paint(BOLD, st.paint(code, str(title))),
                              st.paint(DIM, "  " + meta) if meta else "")]
        inner = max(20, min(self.width, 96) - 4)
        for para in str(body).splitlines() or [""]:
            for line in wrap(para, inner):
                lines.append("%s %s" % (st.paint(code, st.g("bar")), line))
        self._say("info", "panel", str(title),
                  {"title": title, "meta": meta, "body": body}, lines)

    def tree(self, title: str, groups) -> None:
        """`ui.Tree`. `groups` is {group name: [(left, right), ...]} and the
        group colours cycle through the same five in the same order."""
        st = self.st
        palette = [VIOLET, LILAC, INDIGO, TEAL, GOLD]
        lines = ["", st.paint(BOLD, st.paint(VIOLET, str(title))), self._rule_text()]
        names = list(groups.keys())
        for gi, name in enumerate(names):
            code = palette[gi % len(palette)]
            last_group = gi == len(names) - 1
            branch = st.g("ell") if last_group else st.g("tee")
            child_prefix = "   " if last_group else st.g("pipe")
            lines.append("%s %s" % (st.paint(code, branch),
                                    st.paint(BOLD, st.paint(code, str(name)))))
            children = list(groups[name])
            for ci, child in enumerate(children):
                left, right = (list(child) + ["", ""])[:2]
                cbranch = st.g("ell") if ci == len(children) - 1 else st.g("tee")
                lines.append("%s%s %s %s" % (
                    st.paint(LINE, child_prefix),
                    st.paint(code, cbranch),
                    pad_visible(st.paint(code, str(left)), 30),
                    st.paint(DIM, str(right)),
                ))
        self._say("info", "tree", str(title),
                  {"title": title, "groups": {k: list(v) for k, v in groups.items()}},
                  lines)

    # ================================================== events

    def _fields_tail(self, fields: dict) -> str:
        if not fields:
            return ""
        st = self.st
        parts = []
        for key, value in fields.items():
            parts.append("%s%s" % (st.paint(INKDIM, str(key) + "="),
                                   st.paint(TEAL, _short(value))))
        return "  " + " ".join(parts)

    def _event(self, level: str, glyph: str, code: str, msg, fields,
               kind: str = "") -> None:
        st = self.st
        text = str(msg)
        head = "  %s %s" % (st.paint(code, glyph), text)
        line = head + self._fields_tail(fields)
        if visible_len(line) > self.width and fields:
            lines = [head, "    " + self._fields_tail(fields).strip()]
        else:
            lines = [line]
        self._say(level, kind or level, text, fields, lines)

    def step(self, label: str) -> None:
        """`ui.Step`. The gold `⟐` is the Go CLI's "I am doing a thing" mark."""
        self._event("info", self.st.g("step"), GOLD, label, {}, kind="step")

    def ok(self, msg, **fields) -> None:
        self._event("ok", self.st.g("dot"), MINT, msg, fields, kind="ok")

    def info(self, msg, **fields) -> None:
        self._event("info", self.st.g("mid"), TEAL, msg, fields, kind="info")

    def warn(self, msg, **fields) -> None:
        self._event("warn", self.st.g("dot"), AMBER, msg, fields, kind="warn")

    def error(self, msg, **fields) -> None:
        self._event("error", self.st.g("cross"), ROSE, msg, fields, kind="error")

    def soft(self, msg, **fields) -> None:
        st = self.st
        line = "    " + st.paint(DIM, str(msg)) + self._fields_tail(fields)
        self._say("soft", "soft", str(msg), fields, [line])

    def degraded(self, what: str, why: str, **fields) -> None:
        """The most important event in this module.

        A degraded run is not an error -- the pipeline keeps going -- but it is
        also NOT a healthy run, and the difference has to survive being skimmed
        at 3am. Amber triangle, the subsystem in bold, the reason on its own
        indented line, and `degraded=true` in the JSONL so the studio can count
        them without parsing prose.
        """
        st = self.st
        head = "  %s %s  %s" % (
            st.paint(AMBER, st.g("tri")),
            st.paint(BOLD, st.paint(AMBER, "DEGRADED")),
            st.paint(BOLD, str(what)),
        )
        lines = [head]
        indent = "      %s " % st.paint(LINE, st.g("into"))
        for chunk in wrap(str(why), max(20, min(self.width, 96) - 10)):
            lines.append(indent + st.paint(DIM, chunk))
            indent = "        "
        tail = self._fields_tail(fields)
        if tail:
            lines.append("       " + tail.strip())
        payload = {"what": what, "why": why, "degraded": True}
        payload.update(fields)
        self._say("warn", "degraded", "%s: %s" % (what, why), payload, lines)

    def event(self, kind: str, **fields) -> None:
        """Structured only. Nothing reaches the terminal; this is for the
        machine-readable trail the web surfaces tail."""
        self._record("info", str(kind), str(kind), fields)

    # ================================================== progress

    def progress(self, current: int, total: int, *, label: str = "",
                 detail: str = "", rate=None, cache_hit=None) -> None:
        """`ui.Progress`, taught to survive a three-day job and a log file.

        On a TTY this is one repainted line, throttled to 20fps. Redirected --
        which is how `run_pipeline_daemons.sh` actually runs these -- carriage
        returns would produce a single unreadable megabyte-long line, so it
        emits a whole line instead, and only when it has something new to say:
        the first cell, the last cell, every crossed decile, or every
        ARCANE_PROGRESS_INTERVAL seconds (default 30).

        `rate` overrides the measured items/second. `cache_hit` is a 0..1
        fraction and is rendered because on an atlas job it is the number that
        explains the rate.
        """
        st = self.st
        total = max(1, int(total or 0))
        current = max(0, min(total, int(current or 0)))
        now = time.time()
        state = self._progress
        key = label or "progress"
        if state.get("key") != key or current < state.get("current", 0):
            state = self._progress = {
                "key": key, "t0": now, "c0": current, "current": current,
                "last_paint": 0.0, "last_decile": -1,
            }
        state["current"] = current

        elapsed = now - state["t0"]
        done_here = current - state["c0"]
        measured = (done_here / elapsed) if (elapsed > 0.5 and done_here > 0) else None
        per_sec = _num(rate) if rate is not None else measured
        per_item = (1.0 / per_sec) if per_sec else None
        eta = ((total - current) / per_sec) if per_sec else None
        pct = 100.0 * current / total
        decile = int(pct // 10)

        first = state["last_paint"] == 0.0
        last = current >= total
        if st.tty:
            if not (first or last) and (now - state["last_paint"]) < 0.05:
                return
        else:
            due = (now - state["last_paint"]) >= self._interval
            if not (first or last or due or decile > state["last_decile"]):
                return
        state["last_paint"] = now
        state["last_decile"] = max(decile, state["last_decile"])

        width = 30 if self.width >= 110 else 22
        pieces = [
            st.paint(GOLD, st.g("step")),
            pad_visible(state_ink(st, "done" if last else "running"), 9),
            bar(st, current / total, width, TEAL),
            rpad_visible("%d%%" % int(pct), 4),
            "%s%s" % (st.paint(BOLD, "{:,}".format(current)),
                      st.paint(DIM, "/{:,}".format(total))),
        ]
        meta = []
        if per_item is not None:
            meta.append("%.2fs/cell" % per_item if per_item >= 0.01
                        else "%.0fms/cell" % (per_item * 1000))
        if cache_hit is not None:
            frac = _fraction(cache_hit, 1.0 if _num(cache_hit) <= 1 else 100.0)
            meta.append("cache %d%%" % round((frac or 0) * 100))
        if eta is not None:
            meta.append("eta %s" % _dur(eta))
        if detail:
            meta.append(str(detail))
        line = "  " + " ".join(pieces)
        if label:
            line = "  %s %s %s" % (st.paint(GOLD, st.g("step")),
                                   pad_visible(st.paint(TEAL, truncate(label, 16)), 16),
                                   " ".join(pieces[1:]))
        if meta:
            line += "  " + st.paint(INKDIM, (" %s " % st.g("mid")).join(meta))

        self._record("info", "progress", label or "progress", {
            "current": current, "total": total, "pct": round(pct, 2),
            "per_item_s": round(per_item, 4) if per_item else None,
            "eta_s": round(eta, 1) if eta else None,
            "cache_hit": cache_hit, "detail": detail, "label": label,
        })
        if LEVELS["info"] < self.threshold:
            return
        if st.tty:
            with _STDOUT_LOCK:
                try:
                    self.stream.write("\r" + line + "\033[K")
                    self.stream.flush()
                except Exception:
                    pass
                self._progress_open = True
        else:
            self._emit([line])

    def progress_done(self, summary: str = "") -> None:
        """Close the repainted line and state what actually happened. The
        summary is not decoration: on a redirected log it is the only line that
        carries the total."""
        st = self.st
        state = self._progress
        with _STDOUT_LOCK:
            if self._progress_open:
                try:
                    self.stream.write("\n")
                    self.stream.flush()
                except Exception:
                    pass
                self._progress_open = False
        elapsed = time.time() - state["t0"] if state.get("t0") else None
        done = state.get("current", 0) - state.get("c0", 0)
        bits = []
        if done:
            bits.append("%s cells" % "{:,}".format(done))
        if elapsed:
            bits.append("in %s" % _dur(elapsed))
            if done:
                bits.append("(%.2fs/cell)" % (elapsed / done))
        text = summary or " ".join(bits) or "complete"
        line = "  %s %s %s" % (st.paint(MINT, st.g("dot")),
                               st.paint(BOLD, st.paint(MINT, "COMPLETE")),
                               st.paint(DIM, text))
        self._say("ok", "progress_done", text,
                  {"summary": summary, "cells": done, "elapsed_s": elapsed}, [line])
        self._progress = {}

    @contextmanager
    def timer(self, label: str):
        """`with log.timer("warm gates"):` -- a step on the way in, an elapsed
        on the way out, and a real error line if the body raised."""
        started = time.time()
        self.step(label)
        holder = {"label": label, "started": started, "elapsed": None}
        try:
            yield holder
        except BaseException as exc:
            holder["elapsed"] = time.time() - started
            self.error("%s failed after %s" % (label, _dur(holder["elapsed"])),
                       error="%s: %s" % (type(exc).__name__, exc))
            raise
        else:
            holder["elapsed"] = time.time() - started
            st = self.st
            line = "    %s %s" % (
                st.paint(LINE, st.g("into")),
                st.paint(DIM, "%s in " % label) + st.paint(MINT, _dur(holder["elapsed"])))
            self._say("info", "timer", label,
                      {"label": label, "elapsed_s": round(holder["elapsed"], 4)},
                      [line])

    # ================================================== domain renderers

    # -- jury -------------------------------------------------------------

    _TIER = {
        "masterpiece": ("crown", "MASTERPIECE", GOLD),
        "spectacle": ("spark", "SPECTACLE", LILAC),
        "standard": ("diamond", "STANDARD", TEAL),
        "unscored": ("cross", "UNSCORED", ROSE),
    }

    def _tier_badge(self, tier: str, percentile=None) -> str:
        st = self.st
        glyph, label, code = self._TIER.get(
            str(tier).lower(), ("diamond", str(tier).upper() or "?", TEAL))
        badge = "%s %s" % (st.g(glyph), st.paint(BOLD, st.paint(code, label)))
        pct = _num(percentile)
        if pct is not None and str(tier).lower() in ("masterpiece", "spectacle"):
            badge += st.paint(DIM, "  top %.1f%%" % max(0.0, 100.0 - pct))
        return badge

    def verdict(self, receipt: dict) -> None:
        """The jury scorecard, and the one renderer that is allowed to be loud.

        An `unscored` receipt takes a completely different path: no bars, no
        composite, no badge that could be mistaken for a grade, the whole block
        dimmed, and the quorum failure printed in full. A reader skimming a
        wall of these must be able to tell a real 94 from a missing one without
        reading a word, because the previous evaluator could not and that is
        why this one exists.
        """
        st = self.st
        receipt = receipt or {}
        tier = str(receipt.get("tier") or "unscored").lower()
        unscored = tier == "unscored" or receipt.get("unscored") or receipt.get("raw_composite") is None
        job = str(receipt.get("job_id") or "?")
        seed = receipt.get("seed")
        prompt = str(receipt.get("prompt") or "")
        percentile = _num(receipt.get("percentile_rank"))
        curved = _num(receipt.get("curved_score"))
        raw = _num(receipt.get("raw_composite"))
        judges = list(receipt.get("judges") or [])
        degraded_judges = list(receipt.get("degraded_judges") or [])
        elapsed = _num(receipt.get("elapsed_ms"))
        inner = min(self.width, 96)

        code = ROSE if unscored else self._TIER.get(tier, ("", "", TEAL))[2]
        gutter = st.paint(LINE if unscored else code, st.g("bar"))

        meta = ["job %s" % job]
        if seed is not None:
            meta.append("seed %s" % seed)
        if elapsed is not None:
            meta.append(_dur(elapsed / 1000.0))
        if receipt.get("mode"):
            meta.append(str(receipt["mode"]))

        title = "VERDICT" if not unscored else "NO VERDICT"
        head_left = "%s %s%s" % (
            st.paint(code, st.g("diamond")),
            st.paint(BOLD, st.paint(code, title)),
            st.paint(DIM, "  " + st.sep().join(meta)),
        )
        badge = self._tier_badge(tier, percentile)
        pad = max(1, inner - visible_len(head_left) - visible_len(badge))
        lines = ["", head_left + " " * pad + badge]

        # prompt
        for chunk in wrap(prompt, inner - 4)[:2]:
            lines.append("%s %s" % (gutter, st.paint(DIM, chunk)))
        if prompt and len(wrap(prompt, inner - 4)) > 2:
            lines[-1] += st.paint(DIM, " " + st.g("ellipsis"))

        # judges
        if judges:
            lines.append("%s" % gutter)
            lines.append("%s %s" % (gutter, st.paint(INKDIM, "JURY")))
        for judge in judges:
            role = str(judge.get("role") or "?")
            model = str(judge.get("model") or "?")
            score = _num(judge.get("score"))
            weight = _num(judge.get("weight"))
            is_dead = bool(judge.get("degraded")) or score is None
            name = pad_visible(st.paint(BOLD if not is_dead else DIM,
                                        truncate(role, 15)), 15)
            model_cell = pad_visible(st.paint(INKDIM, truncate(model, 22)), 22)
            if is_dead:
                meter = st.paint(LINE, st.g("empty") * 18)
                value = rpad_visible(st.paint(ROSE, "—"), 5)
                mark = st.paint(ROSE, st.g("cross"))
            elif unscored:
                # A surviving judge on an unscored frame is a real reading, so
                # print the number -- but drain the colour out of it, because
                # nothing on this card is a grade.
                meter = bar(st, score / 100.0, 18, INKDIM)
                value = rpad_visible(st.paint(DIM, "%.1f" % score), 5)
                mark = st.paint(INKDIM, st.g("dot"))
            else:
                meter = bar(st, score / 100.0, 18)
                value = rpad_visible(st.paint(BOLD, "%.1f" % score), 5)
                mark = st.paint(MINT if score >= 60 else AMBER, st.g("dot"))
            wtxt = st.paint(DIM, "×%.2f" % weight) if weight else ""
            lines.append("%s   %s %s %s %s %s %s" % (
                gutter, mark, name, model_cell, meter, value, wtxt))
            critique = str(judge.get("critique") or judge.get("error") or "").strip()
            if critique:
                ink = ROSE if is_dead else LINE
                for index, chunk in enumerate(wrap(critique, inner - 12)[:2]):
                    arrow = st.paint(ink, st.g("into")) if index == 0 else " "
                    lines.append("%s        %s %s" % (
                        gutter, arrow, st.paint(DIM, chunk)))

        # composite / percentile, or the refusal
        lines.append("%s" % gutter)
        if unscored:
            reason = str(receipt.get("unscored_reason")
                         or "the jury did not reach quorum; no composite was computed")
            lines.append("%s %s %s" % (
                gutter, st.paint(BOLD, st.paint(ROSE, st.g("cross") + " NOT SCORED")),
                st.paint(DIM, "no composite exists for this frame")))
            for chunk in wrap(reason, inner - 6):
                lines.append("%s   %s" % (gutter, st.paint(ROSE, chunk)))
            if degraded_judges:
                lines.append("%s   %s %s" % (
                    gutter, st.paint(INKDIM, "silent judges"),
                    st.paint(AMBER, ", ".join(str(d) for d in degraded_judges))))
            lines.append("%s   %s" % (gutter, st.paint(DIM,
                "this frame is excluded from the percentile CDF and from every tier feed")))
        else:
            comp = "%s %s %s %s" % (
                pad_visible(st.paint(INKDIM, "COMPOSITE"), 12),
                st.paint(DIM, "raw %s" % ("%.1f" % raw if raw is not None else "—")),
                st.paint(DIM, st.g("arrow")),
                st.paint(BOLD, st.paint(score_ink(st, (curved or 0) / 100.0),
                                        "%.1f" % (curved or 0)))
                + st.paint(DIM, " / 100"))
            lines.append("%s %s" % (gutter, comp))
            if percentile is not None:
                track = 34
                pos = int(round(max(0.0, min(100.0, percentile)) / 100.0 * (track - 1)))
                drawn = (st.paint(LINE, st.g("thin") * pos)
                         + st.paint(BOLD, st.paint(score_ink(st, percentile / 100.0),
                                                   st.g("mark")))
                         + st.paint(LINE, st.g("thin") * (track - pos - 1)))
                lines.append("%s %s %s%s%s %s" % (
                    gutter,
                    pad_visible(st.paint(INKDIM, "PERCENTILE"), 12),
                    st.paint(LINE, st.g("lend")), drawn, st.paint(LINE, st.g("rend")),
                    st.paint(BOLD, "%.1f" % percentile) + st.paint(DIM, "th"),
                ))
            uniq = receipt.get("uniqueness") or {}
            if uniq:
                ustr = "%s%s" % (
                    "%s%%" % _fmt(uniq.get("score")),
                    "  " + str(uniq.get("category")) if uniq.get("category") else "")
                extra = st.paint(ROSE, "  mode collapse") if uniq.get("mode_collapse") else ""
                lines.append("%s %s %s%s" % (
                    gutter, pad_visible(st.paint(INKDIM, "NOVELTY"), 12),
                    st.paint(TEAL, ustr), extra))
            if degraded_judges:
                lines.append("%s %s %s" % (
                    gutter, pad_visible(st.paint(INKDIM, "DEGRADED"), 12),
                    st.paint(AMBER, "%s  (weights renormalised over the survivors)"
                             % ", ".join(str(d) for d in degraded_judges))))

        # attached evidence, if the receipt carries it
        gates = receipt.get("gates")
        if isinstance(gates, dict) and gates:
            gmain, gtail = self._gates_parts(gates, compact=True)
            if visible_len(gmain) + visible_len(gtail) + 4 <= self.width:
                lines.append("%s %s  %s" % (gutter, gmain, gtail))
            else:
                lines.append("%s %s" % (gutter, gmain))
                lines.append("%s %s%s" % (gutter, " " * 13, gtail))
        arcane = receipt.get("arcane")
        if isinstance(arcane, dict) and arcane:
            raw_f = _num(arcane.get("fortiche_score")) or 0.0
            unit = raw_f <= 1.0
            score = _fraction(arcane.get("fortiche_score"), 1.0 if unit else 100.0)
            lines.append("%s %s %s %s %s" % (
                gutter, pad_visible(st.paint(INKDIM, "FORTICHE"), 12),
                bar(st, score, 18),
                st.paint(BOLD, _fmt(arcane.get("fortiche_score"), 2 if unit else 1)),
                state_ink(st, arcane.get("verdict", "unknown"))))

        lines.append(st.paint(LINE, st.g("thin") * inner))
        level = "warn" if unscored else "ok"
        self._say(level, "verdict",
                  "job %s %s" % (job, tier),
                  {"job_id": job, "tier": tier, "seed": seed,
                   "percentile_rank": percentile, "curved_score": curved,
                   "raw_composite": raw, "unscored": bool(unscored),
                   "degraded_judges": degraded_judges,
                   "judges": [{"role": j.get("role"), "model": j.get("model"),
                               "score": j.get("score"),
                               "degraded": bool(j.get("degraded"))}
                              for j in judges]},
                  lines)

    # -- sensory gates ----------------------------------------------------

    _GATE_METRICS = (("aesthetic", "aes"), ("novelty", "nov"),
                     ("adherence", "adh"), ("palette_delta", "pal"))

    @staticmethod
    def _gate_split(gate: dict):
        """Separate a frame's REJECTIONS from the tier's standing disclosure.

        `sensory_gates` computes `passed == (not failures)` and then builds
        `reasons` as failures plus a persistent "DEGRADED: DINOv2 is
        mandatory..." line whenever the full tier is not loaded. That degraded
        line is true on every frame a degraded node ever triages; rendering it
        among the rejection reasons would make a passing frame look rejected
        and would train the operator to ignore the one line that matters.

        Returns (failures, banners, passed).
        """
        reasons = [str(r) for r in (gate.get("reasons") or [])]
        if "failures" in gate:
            failures = [str(f) for f in (gate.get("failures") or [])]
            banners = [r for r in reasons if r not in failures]
        else:  # older/handmade dicts: reasons ARE the failures
            failures, banners = list(reasons), []
        passed = bool(gate.get("passed")) if "passed" in gate else not failures
        return failures, banners, passed

    def _tier_chip(self, gate: dict) -> str:
        """The sensory tier, said out loud on every line.

        `emergency` on production hardware is an incident, not a mode, so it
        gets the only red chip in this module outside a VRAM overcommit.
        """
        st = self.st
        tier = str(gate.get("tier") or "").strip().lower()
        mandatory = gate.get("mandatory_satisfied")
        if not tier:
            tier = "full" if mandatory else ("degraded" if gate.get("degraded") else "")
        if not tier:
            return ""
        if tier == "emergency":
            return st.paint(BOLD, st.paint(RED, "[EMERGENCY]"))
        if tier == "full":
            chip = "[full]" if mandatory is not False else "[full!]"
            return st.paint(BOLD, st.paint(MINT, chip))
        return st.paint(BOLD, st.paint(AMBER, "[%s]" % tier))

    def _gate_cell(self, gate: dict, key: str, label: str, width: int = 0) -> str:
        """One metric. An unmeasurable metric renders as a rail and a dash --
        never as a zero and never as a bar, for the same reason an unscored
        verdict carries no composite: absent is not the same as bad."""
        st = self.st
        width = width or (8 if self.width >= 120 else 6)
        measured = gate.get("measured") or {}
        unavailable = str(measured.get(key, "")) == "unavailable"
        number = _num(gate.get(key))
        if unavailable or number is None:
            return "%s %s %s" % (st.paint(INKDIM, label),
                                 st.paint(LINE, st.g("thin") * width),
                                 rpad_visible(st.paint(DIM, "—"), 3))
        return "%s %s %s" % (st.paint(INKDIM, label),
                             bar(st, number / 100.0, width),
                             rpad_visible(st.paint(BOLD, "%.0f" % number), 3))

    def _gates_parts(self, gate: dict, compact: bool = False):
        """(verdict + metrics, provenance tail). Split so a narrow terminal can
        drop the provenance to its own line instead of soft-wrapping the bars,
        which is the one thing that makes this line unreadable."""
        st = self.st
        _, _, passed = self._gate_split(gate)
        mark = (st.paint(BOLD, st.paint(MINT, "%s PASS" % st.g("dot"))) if passed
                else st.paint(BOLD, st.paint(ROSE, "%s REJECT" % st.g("cross"))))
        chip = self._tier_chip(gate)
        cells = [self._gate_cell(gate, key, label)
                 for key, label in self._GATE_METRICS]
        tail = [st.paint(INKDIM, str(gate.get("backend") or "?"))]
        calibration = str(gate.get("calibration") or "")
        if calibration and calibration != "n/a":
            ink = AMBER if calibration in ("provisional", "heuristic") else DIM
            tail.append(st.paint(ink, "calib " + calibration))
        latency = _num(gate.get("latency_ms"))
        if latency is not None:
            tail.append(st.paint(DIM, "%.0fms" % latency))
        head = "" if compact else "  %s " % st.paint(GOLD, st.g("step"))
        label = pad_visible(st.paint(INKDIM, "GATES"), 12 if compact else 6)
        main = "%s%s %s %s  %s" % (head, label, mark, chip, "  ".join(cells))
        return main, st.paint(DIM, st.sep().join(tail))

    def _gates_line(self, gate: dict, compact: bool = False) -> str:
        main, tail = self._gates_parts(gate, compact)
        return "%s  %s" % (main, tail)

    def gates(self, gate_result: dict) -> None:
        """One line of triage, then everything it is obliged to disclose.

        Three kinds of subordinate line, and they are deliberately not
        interchangeable:

          `↳` rose    a FAILURE. This frame was rejected for this reason, and
                      `passed` is false because of it.
          `▲` amber   a TIER BANNER. The node is not running the declared
                      sensory model. Always true while the node is degraded,
                      never a statement about this frame.
          `·` dim     a NOTE. A cold start, an excluded metric, a calibration
                      disclosure. Informational.
        """
        st = self.st
        gate = gate_result or {}
        failures, banners, passed = self._gate_split(gate)
        main, tail = self._gates_parts(gate)
        if visible_len(main) + visible_len(tail) + 2 <= self.width:
            lines = ["%s  %s" % (main, tail)]
        else:
            lines = [main, "        " + tail]
        inner = min(self.width, 96)
        tier = str(gate.get("tier") or "").lower()

        for reason in failures:
            for i, chunk in enumerate(wrap(str(reason), inner - 12)):
                lines.append("        %s %s" % (
                    st.paint(ROSE, st.g("into")) if i == 0 else " ",
                    st.paint(ROSE, chunk)))
        if tier == "emergency":
            incident = ("emergency tier: no vision model of any kind is resident; "
                        "this is a pixel heuristic wearing a gate's name")
            head = "        %s %s " % (st.paint(RED, st.g("tri")),
                                       st.paint(BOLD, st.paint(RED, "INCIDENT")))
            for i, chunk in enumerate(wrap(incident, inner - 22)):
                lines.append((head if i == 0 else " " * 19)
                             + st.paint(AMBER, chunk))
        for banner in banners:
            for i, chunk in enumerate(wrap(str(banner), inner - 12)):
                lines.append("        %s %s" % (
                    st.paint(AMBER, st.g("tri")) if i == 0 else " ",
                    st.paint(AMBER, chunk)))
        for note in list(gate.get("notes") or []):
            for i, chunk in enumerate(wrap(str(note), inner - 12)):
                lines.append("        %s %s" % (
                    st.paint(LINE, st.g("mid")) if i == 0 else " ",
                    st.paint(DIM, chunk)))

        measured = gate.get("measured") or {}
        self._say("info" if passed else "warn", "gates",
                  "gates %s" % ("pass" if passed else "reject"),
                  {"passed": passed,
                   "backend": gate.get("backend"), "tier": gate.get("tier"),
                   "degraded": bool(gate.get("degraded")),
                   "mandatory_satisfied": gate.get("mandatory_satisfied"),
                   "calibration": gate.get("calibration"),
                   "aesthetic": gate.get("aesthetic"), "novelty": gate.get("novelty"),
                   "adherence": (None if str(measured.get("adherence", "")) == "unavailable"
                                 else gate.get("adherence")),
                   "palette_delta": gate.get("palette_delta"),
                   "latency_ms": gate.get("latency_ms"),
                   "measured": measured,
                   "failures": failures, "banners": banners,
                   "image_path": gate.get("image_path")},
                  lines)

    # -- Arcane conformance ----------------------------------------------

    _FORTICHE = (
        ("impasto", "impasto", "loaded paint, ridges that catch light"),
        ("planarity", "planarity", "faceted planes, not smooth gradients"),
        ("chiaroscuro", "chiaroscuro", "theatrical single-source contrast"),
        ("palette_zaun", "palette · zaun", "chemtech emerald & bruise violet"),
        ("palette_piltover", "palette · piltover", "hextech cyan & gilt brass"),
        ("anti_cgi", "anti-cgi", "absence of render-engine sheen"),
    )

    def fortiche(self, conformance: dict) -> None:
        """The six Arcane invariants as bars, in Zaun emerald and Piltover
        cyan where the invariant names a realm.

        Scores are accepted in either 0..1 or 0..100 and normalised on the
        maximum seen, because two of the three modules that will call this have
        not been written yet and guessing wrong should not produce a chart that
        is silently 100x off.
        """
        st = self.st
        conf = conformance or {}
        values = [_num(conf.get(key)) for key, _, _ in self._FORTICHE]
        composite = _num(conf.get("fortiche_score"))
        span = max([v for v in values + [composite] if v is not None] or [1.0])
        hi = 1.0 if span <= 1.0 else 100.0
        inner = min(self.width, 96)

        realm = str(conf.get("realm") or "").lower()
        head = "%s %s%s" % (
            st.paint(VIOLET, st.g("diamond")),
            st.paint(BOLD, st.paint(VIOLET, "FORTICHE CONFORMANCE")),
            st.paint(DIM, "  " + (realm or "arcane") +
                     (" · %s" % conf.get("backend") if conf.get("backend") else "")))
        lines = ["", head]
        gutter = st.paint(VIOLET, st.g("bar"))
        for key, label, note in self._FORTICHE:
            frac = _fraction(conf.get(key), hi)
            if key == "palette_zaun":
                ink = st.zaun()
            elif key == "palette_piltover":
                ink = st.piltover()
            else:
                ink = ""
            meter = bar(st, frac, 28, ink)
            value = ("—" if frac is None
                     else ("%.2f" % _num(conf.get(key)) if hi == 1.0
                           else "%.1f" % _num(conf.get(key))))
            lines.append("%s %s %s %s  %s" % (
                gutter,
                pad_visible(st.paint(BOLD if frac and frac >= 0.7 else "",
                                     label), 19),
                meter,
                rpad_visible(st.paint(BOLD, value), 6),
                st.paint(DIM, note)))
        lines.append(gutter)
        cfrac = _fraction(composite, hi)
        verdict = str(conf.get("verdict") or
                      ("conformant" if (cfrac or 0) >= 0.65 else "off-model"))
        threshold = _num(conf.get("threshold"))
        comp_text = "—" if composite is None else (
            "%.2f" % composite if hi == 1.0 else "%.1f" % composite)
        lines.append("%s %s %s %s  %s" % (
            gutter,
            pad_visible(st.paint(INKDIM, "FORTICHE SCORE"), 19),
            bar(st, cfrac, 28),
            rpad_visible(st.paint(BOLD, comp_text), 6),
            state_ink(st, verdict)))
        if threshold is not None:
            lines.append("%s %s" % (gutter, st.paint(DIM,
                "threshold %s · below this the frame is not Arcane, it is merely painterly"
                % ("%.2f" % threshold if hi == 1.0 else "%.1f" % threshold))))
        if conf.get("available") is False or conf.get("degraded"):
            lines.append("%s %s" % (gutter, st.paint(AMBER,
                "%s measured on a degraded backend -- treat as indicative"
                % st.g("tri"))))
        lines.append(st.paint(LINE, st.g("thin") * inner))
        payload = {key: conf.get(key) for key, _, _ in self._FORTICHE}
        payload.update({"fortiche_score": composite, "verdict": verdict,
                        "realm": conf.get("realm")})
        self._say("info", "fortiche", "fortiche %s" % verdict, payload, lines)

    # -- VRAM -------------------------------------------------------------

    def vram(self, budget: dict) -> None:
        """The card, honestly.

        A 0.9% margin on a 96 GiB card is arithmetically a pass and
        operationally a time bomb, so this prints the headroom next to the
        verdict and turns amber under 2 GiB. `fits: false` gets a red overflow
        bar, an explicit over-by figure, and a rule of `✕` -- there is no
        reading of that block where it looks fine.
        """
        st = self.st
        budget = budget or {}
        tenants = list(budget.get("tenants") or [])
        total = _num(budget.get("total_gib")) or _num(budget.get("vram_per_gpu_gib"))
        reserve = _num(budget.get("reserve_gib")) or 0.0
        usable = _num(budget.get("usable_gib"))
        if usable is None and total is not None:
            usable = total - reserve
        allocated = _num(budget.get("allocated_gib"))
        if allocated is None:
            allocated = sum(
                _num(_first(t.get("vram_gib"), t.get("vram_expected_gib"))) or 0.0
                for t in tenants)
        free = _num(budget.get("free_gib"))
        if free is None and total is not None:
            free = total - allocated
        headroom = _num(budget.get("headroom_gib"))
        if headroom is None and usable is not None:
            headroom = usable - allocated
        fits = budget.get("fits")
        if fits is None:
            fits = usable is None or allocated <= usable
        fits = bool(fits)
        inner = min(self.width, 96)

        profile = str(budget.get("profile") or "?")
        gpu = str(budget.get("gpu") or "")
        self.header("VRAM BUDGET", "%s%s" % (profile, "  ·  " + gpu if gpu else ""))

        rows = []
        for tenant in tenants:
            gib = _num(_first(tenant.get("vram_gib"), tenant.get("vram_expected_gib")))
            share = (gib / usable) if (gib is not None and usable) else None
            rows.append([
                str(tenant.get("name") or tenant.get("tenant") or "?"),
                truncate(str(tenant.get("model") or "?"), 40),
                str(tenant.get("precision") or "—"),
                _gib(gib),
                bar(self.st, share, 12, TEAL),
                str(tenant.get("note") or ""),
            ])
        if rows:
            self.table(["tenant", "model", "prec", "gib", "share", "note"], rows,
                       aligns=["l", "l", "l", "r", "l", "l"])

        width = min(56, inner - 26)
        cap = usable or total or 1.0
        frac = allocated / cap if cap else 0.0
        lines = [""]
        if fits:
            meter = bar(st, min(1.0, frac), width,
                        MINT if (headroom or 0) >= 2.0 else AMBER)
        else:
            over = max(0.0, allocated - cap)
            over_cells = max(1, min(width // 3,
                                    int(round(over / cap * width)) if cap else 1))
            meter = (st.paint(ROSE, st.g("full") * (width - over_cells))
                     + st.paint(BOLD, st.paint(RED, st.g("over") * over_cells)))
        lines.append("  %s %s %s" % (
            pad_visible(st.paint(INKDIM, "CAPACITY"), 12), meter,
            st.paint(BOLD, "%.1f%%" % (frac * 100.0))))
        lines.append("  %s %s %s %s" % (
            pad_visible(st.paint(INKDIM, "ALLOCATED"), 12),
            st.paint(BOLD, "%s GiB" % _gib(allocated)),
            st.paint(DIM, "of %s usable" % _gib(usable)),
            st.paint(DIM, "(%s total - %s reserved)" % (_gib(total), _gib(reserve)))))
        lines.append("  %s %s %s" % (
            pad_visible(st.paint(INKDIM, "FREE"), 12),
            st.paint(TEAL, "%s GiB" % _gib(free)),
            st.paint(DIM, "on the card")))
        if fits:
            ink = MINT if (headroom or 0) >= 2.0 else AMBER
            note = ("comfortable" if (headroom or 0) >= 8.0
                    else "workable" if (headroom or 0) >= 2.0
                    else "THIN — first thing to OOM when a KV cache grows")
            lines.append("  %s %s %s" % (
                pad_visible(st.paint(INKDIM, "HEADROOM"), 12),
                st.paint(BOLD, st.paint(ink, "%s GiB" % _gib(headroom))),
                st.paint(ink if ink is AMBER else DIM, note)))
            lines.append("  %s %s" % (
                pad_visible(st.paint(INKDIM, "VERDICT"), 12),
                st.paint(BOLD, st.paint(MINT, "%s FITS" % st.g("dot")))))
        else:
            over = allocated - (usable or 0.0)
            lines.append("  " + st.paint(BOLD, st.paint(RED, st.g("cross") * (inner - 2))))
            lines.append("  %s %s" % (
                pad_visible(st.paint(INKDIM, "VERDICT"), 12),
                st.paint(BOLD, st.paint(RED, "%s DOES NOT FIT" % st.g("cross")))))
            lines.append("  %s %s %s" % (
                pad_visible(st.paint(INKDIM, "OVER BY"), 12),
                st.paint(BOLD, st.paint(ROSE, "%s GiB" % _gib(over))),
                st.paint(DIM, "past the %s GiB usable ceiling" % _gib(usable))))
            for hint in list(budget.get("remedies") or []):
                lines.append("      %s %s" % (st.paint(ROSE, st.g("into")),
                                              st.paint(AMBER, str(hint))))
            lines.append("  " + st.paint(BOLD, st.paint(RED, st.g("cross") * (inner - 2))))
        self._say("info" if fits else "error", "vram",
                  "vram %s: %s/%s GiB" % ("fits" if fits else "OVERCOMMIT",
                                          _gib(allocated), _gib(usable)),
                  {"profile": profile, "fits": fits, "allocated_gib": allocated,
                   "usable_gib": usable, "total_gib": total,
                   "reserve_gib": reserve, "free_gib": free,
                   "headroom_gib": headroom,
                   "tenants": [{"name": t.get("name"),
                                "model": t.get("model"),
                                "precision": t.get("precision"),
                                "vram_gib": _first(t.get("vram_gib"),
                                                   t.get("vram_expected_gib"))}
                               for t in tenants]},
                  lines)

    # -- roster -----------------------------------------------------------

    def roster(self, tenants) -> None:
        """Who is on the card, what they are, and where to reach them."""
        st = self.st
        rows = []
        for tenant in list(tenants or []):
            name = str(tenant.get("name") or tenant.get("role") or "?")
            port = tenant.get("port")
            remote = bool(tenant.get("remote"))
            endpoint = (str(tenant.get("base_url")) if remote and tenant.get("base_url")
                        else (":%s" % port if port else
                              str(tenant.get("socket") or tenant.get("uds") or "in-process")))
            enabled = tenant.get("enabled", True)
            state = tenant.get("state") or (
                "online" if enabled else "planned")
            rows.append([
                st.paint(BOLD, name),
                str(tenant.get("purpose") or tenant.get("title") or "—"),
                truncate(str(tenant.get("model") or "?"), 38),
                str(tenant.get("precision") or "—"),
                st.paint(INKDIM, truncate(endpoint, 34)),
                _gib(_first(tenant.get("vram_gib"), tenant.get("vram_expected_gib"))),
                state_ink(st, state),
            ])
        self.table(["tenant", "purpose", "model", "prec", "endpoint", "gib", "state"],
                   rows, title="MODEL ROSTER",
                   aligns=["l", "l", "l", "l", "l", "r", "l"])
        self._record("info", "roster", "roster of %d tenants" % len(rows),
                     {"tenants": list(tenants or [])})


def _fmt(value, nd: int = 1) -> str:
    number = _num(value)
    if number is None:
        return "—"
    return ("%%.%df" % nd) % number


# ============================================================ entry point


def get_logger(component: str, *, jsonl=None, level: str = "info") -> ArcaneLogger:
    """The whole adoption cost of this module.

        from arcane_log import get_logger
        log = get_logger("jury")

    Cached per (component, sink, level), so importing it from four modules in
    one daemon gets you one logger, one file descriptor and one lock rather
    than four of each racing for the same line.
    """
    key = (str(component), str(jsonl) if jsonl else None, str(level))
    with _LOGGERS_LOCK:
        logger = _LOGGERS.get(key)
        if logger is None:
            logger = ArcaneLogger(component, jsonl=jsonl, level=level)
            _LOGGERS[key] = logger
        return logger


# ============================================================ demo reel


def _demo():
    """Every renderer, once, with data shaped like the real thing.

    Run it: `python3 arcane_log.py`. Pipe it: `python3 arcane_log.py | cat`.
    Both must look right, and the second one must contain no escape codes.
    """
    log = get_logger("demo")

    log.header("ARCANE · CONTINUUM",
               "sovereign visual jury · moj 3.0.0 · blackwell-96")
    log.kv("run", log.run_id())
    log.kv("node", "gpu6.aons.beauty · RTX PRO 6000 Blackwell · sm_120")
    log.kv("profile", "blackwell-96", state="active")
    log.kv("jsonl", str(log.sink_path))
    log.kv("governor", "remote", state="online")

    # ---- roster ---------------------------------------------------------
    log.rule("tenancy")
    log.roster([
        {"name": "flux", "purpose": "generator", "model": "black-forest-labs/FLUX.1-dev",
         "precision": "bf16", "socket": ".fluxd/flux-gpu0.sock", "vram_gib": 35.0,
         "state": "resident"},
        {"name": "witness", "purpose": "visual witness", "model": "unsloth/Qwen3.8-27B-NVFP4",
         "precision": "nvfp4", "port": 8001, "vram_gib": 26.88, "state": "online"},
        {"name": "governor", "purpose": "adjudicator", "model": "nvidia/Gemma-4-31B-IT-NVFP4",
         "precision": "nvfp4", "remote": True,
         "base_url": "https://governor.influx.vision/v1", "vram_gib": 0.0,
         "state": "online"},
        {"name": "pixtral", "purpose": "second critic", "model": "RedHatAI/pixtral-12b-w4a16",
         "precision": "w4a16", "port": 8002, "vram_gib": 8.64, "enabled": False,
         "state": "planned"},
        {"name": "gates", "purpose": "sensory triage",
         "model": "dinov2-giant + siglip-so400m", "precision": "fp16",
         "vram_gib": 2.5, "state": "warm"},
    ])

    # ---- VRAM, both ways ------------------------------------------------
    log.vram({
        "profile": "blackwell-96 · governor REMOTE",
        "gpu": "RTX PRO 6000 Blackwell · 96 GiB GDDR7",
        "total_gib": 96.0, "reserve_gib": 2.0, "usable_gib": 94.0,
        "tenants": [
            {"name": "flux", "model": "black-forest-labs/FLUX.1-dev",
             "precision": "bf16", "vram_gib": 35.0,
             "note": "BF16 DiT + T5-XXL + CLIP-L + VAE"},
            {"name": "witness", "model": "unsloth/Qwen3.8-27B-NVFP4",
             "precision": "nvfp4", "vram_gib": 26.88, "note": "0.28 × 96"},
            {"name": "kontext", "model": "city96/FLUX.1-Kontext-dev-gguf",
             "precision": "q4_k_s", "vram_gib": 9.0,
             "note": "marginal; text encoders shared"},
            {"name": "gates", "model": "dinov2-giant + siglip-so400m",
             "precision": "fp16", "vram_gib": 2.5, "note": "in-process"},
        ],
    })

    log.vram({
        "profile": "blackwell-96 · kontext ON · governor LOCAL",
        "gpu": "RTX PRO 6000 Blackwell · 96 GiB GDDR7",
        "total_gib": 96.0, "reserve_gib": 2.0, "usable_gib": 94.0,
        "tenants": [
            {"name": "flux", "model": "black-forest-labs/FLUX.1-dev",
             "precision": "bf16", "vram_gib": 35.0, "note": "hard-pinned"},
            {"name": "witness", "model": "unsloth/Qwen3.8-27B-NVFP4",
             "precision": "nvfp4", "vram_gib": 26.88, "note": "0.28 × 96"},
            {"name": "governor", "model": "nvidia/Gemma-4-31B-IT-NVFP4",
             "precision": "nvfp4", "vram_gib": 20.16, "note": "0.21 × 96, LOCAL"},
            {"name": "pixtral", "model": "RedHatAI/pixtral-12b-w4a16",
             "precision": "w4a16", "vram_gib": 8.64, "note": "0.09 × 96"},
            {"name": "kontext", "model": "city96/FLUX.1-Kontext-dev-gguf",
             "precision": "q4_k_s", "vram_gib": 9.0, "note": "marginal"},
            {"name": "gates", "model": "dinov2-giant + siglip-so400m",
             "precision": "fp16", "vram_gib": 2.5, "note": "in-process"},
        ],
        "remedies": [
            "ARCANE_GOVERNOR_REMOTE=1  →  frees 20.16 GiB, leaves 20.98 GiB of air",
            "ARCANE_KONTEXT=0          →  frees 9.00 GiB",
            "ARCANE_FLUX_PRECISION=q4_k_s → frees 17.00 GiB and costs impasto fidelity",
        ],
    })

    # ---- warm-up + timer ------------------------------------------------
    log.rule("warm-up")
    with log.timer("resolve continuum profile"):
        time.sleep(0.05)
    with log.timer("warm sensory gates"):
        time.sleep(0.12)
    log.ok("siglip-so400m resident", device="cuda:0", dtype="fp16", gib=0.81)
    log.ok("dinov2-giant resident", device="cuda:0", dtype="fp16", gib=1.69)
    # the keyword form of kv(), which is how sensory_gates.py announces its tier
    log.kv(tier="full", backend="siglip+dinov2", device="cuda:0",
           resident_gib=2.50, probes="atelier.aesthetic")
    log.degraded("pixtral-critic",
                 "endpoint :8002 refused the connection after 3 attempts; the jury "
                 "will run on two visual judges and renormalise their weights",
                 endpoint="http://127.0.0.1:8002/v1", attempts=3)
    log.soft("quorum is 2 of 3; two survivors still clears it")

    # ---- atlas progress -------------------------------------------------
    log.rule("atlas · latent cartography")
    log.step("rendering atlas cells for job arc-atlas-0041")
    total = 65536
    for current in (0, 4096, 12288, 24576, 40960, 56320, 65536):
        log.progress(current, total, label="atlas 0041",
                     detail="tile 12/16", cache_hit=0.63 + current / total * 0.3)
        time.sleep(0.03)
    log.progress_done("65,536 cells · 3d 04h wall · 4.21s/cell · cache 93%")

    # ---- verdicts -------------------------------------------------------
    log.rule("jury")

    log.verdict({
        "job_id": "9f2c1e04", "seed": 774411, "mode": "arcane",
        "prompt": ("a masked Venetian alchemist in draped velvet robes with "
                   "constellations embroidered in liquid silver thread, "
                   "theatrical chiaroscuro, loaded impasto, Fortiche key visual"),
        "tier": "masterpiece", "percentile_rank": 98.7, "curved_score": 96.4,
        "raw_composite": 91.2, "elapsed_ms": 3140.0,
        "jury_scores": {"harmony": 93.0, "structure": 89.5,
                        "feature_decoder": 90.1, "semantic_fidelity": 92.4},
        "judges": [
            {"role": "visual-witness", "model": "Qwen3.8-27B-NVFP4", "score": 93.0,
             "weight": 1.0, "degraded": False,
             "critique": "the impasto is real loaded paint, not a texture overlay — "
                         "ridge highlights fall where the key light actually is"},
            {"role": "pixtral-critic", "model": "pixtral-12b-w4a16", "score": 89.5,
             "weight": 0.6, "degraded": False,
             "critique": "left hand is cropped rather than solved, which is a dodge, "
                         "but the silhouette carries it"},
            {"role": "governor", "model": "gemma-4-31b-nvfp4", "score": 92.4,
             "weight": 1.2, "degraded": False,
             "critique": "this is the first frame this week I would hang; the mask "
                         "reads as an object with weight"},
        ],
        "uniqueness": {"score": 87.0, "category": "distinct", "mode_collapse": False},
        "degraded_judges": [],
        "gates": {"aesthetic": 84.0, "novelty": 87.0, "adherence": 79.0,
                  "palette_delta": 71.0, "backend": "siglip+dinov2",
                  "tier": "full", "mandatory_satisfied": True,
                  "calibration": "measured", "passed": True,
                  "latency_ms": 41.2, "failures": [], "reasons": []},
        "arcane": {"fortiche_score": 0.84, "verdict": "conformant"},
    })

    log.verdict({
        "job_id": "b71a4c88", "seed": 20260819, "mode": "arcane",
        "prompt": ("hextech foundry interior at dusk, brass armatures and cyan "
                   "arc-light, wide establishing shot"),
        "tier": "spectacle", "percentile_rank": 93.2, "curved_score": 88.1,
        "raw_composite": 83.7, "elapsed_ms": 2870.0,
        "judges": [
            {"role": "visual-witness", "model": "Qwen3.8-27B-NVFP4", "score": 86.0,
             "weight": 1.0, "degraded": False,
             "critique": "confident planar shading on the armatures; the floor "
                         "reflection is the weakest surface in the frame"},
            {"role": "pixtral-critic", "model": "pixtral-12b-w4a16", "score": None,
             "weight": 0.0, "degraded": True,
             "error": "read timeout after 45s"},
            {"role": "governor", "model": "gemma-4-31b-nvfp4", "score": 81.0,
             "weight": 1.2, "degraded": False,
             "critique": "handsome but safe — this is Piltover as a postcard, not "
                         "as a place someone works"},
        ],
        "uniqueness": {"score": 64.0, "category": "familiar", "mode_collapse": False},
        "degraded_judges": ["pixtral-critic"],
        "gates": {"aesthetic": 78.0, "novelty": 64.0, "adherence": 81.0,
                  "palette_delta": 58.0, "backend": "siglip+dinov2",
                  "tier": "full", "mandatory_satisfied": True,
                  "calibration": "provisional", "passed": True,
                  "latency_ms": 38.9, "failures": [], "reasons": []},
    })

    log.verdict({
        "job_id": "c04e77d1", "seed": 991337, "mode": "arcane",
        "prompt": "zaun undercity chem-baron parlour, verdigris and bruise violet",
        "tier": "unscored", "percentile_rank": None, "curved_score": None,
        "raw_composite": None, "elapsed_ms": 46120.0, "unscored": True,
        "unscored_reason": ("only 1 of 3 judges answered (quorum is 2); refusing to "
                            "fabricate a composite from a single critic"),
        "judges": [
            {"role": "visual-witness", "model": "Qwen3.8-27B-NVFP4", "score": None,
             "weight": 0.0, "degraded": True,
             "error": "vLLM returned 503 — engine restarting after CUDA OOM"},
            {"role": "pixtral-critic", "model": "pixtral-12b-w4a16", "score": None,
             "weight": 0.0, "degraded": True,
             "error": "endpoint disabled on this node"},
            {"role": "governor", "model": "gemma-4-31b-nvfp4", "score": 74.0,
             "weight": 1.2, "degraded": False,
             "critique": "judging from testimony alone; no visual judge survived, "
                         "so this is not a picture score"},
        ],
        "uniqueness": {"score": 55.0, "category": "familiar"},
        "degraded_judges": ["visual-witness", "pixtral-critic"],
    })

    # ---- gates ----------------------------------------------------------
    log.rule("sensory gates")

    # tier `full`: the declared model, nothing to disclose
    log.gates({
        "aesthetic": 84.0, "novelty": 87.0, "adherence": 79.0,
        "palette_delta": 71.0, "backend": "siglip+dinov2", "tier": "full",
        "mandatory_satisfied": True, "calibration": "measured",
        "passed": True, "degraded": False, "latency_ms": 41.2,
        "failures": [], "reasons": [],
        "measured": {"aesthetic": "siglip-probe-margin",
                     "novelty": "dinov2-cls-cosine",
                     "adherence": "siglip-image-text-cosine",
                     "palette_delta": "lab-histogram-hellinger"},
        "notes": [],
        "image_path": "/runs/flux-output/arc/9f2c1e04.png",
    })

    # tier `degraded`: REJECTED for two real failures, AND standing disclosure.
    # The rose `↳` lines rejected this frame. The amber `▲` line is true on
    # every frame this node triages and is not a statement about this one.
    log.gates({
        "aesthetic": 51.0, "novelty": 22.0, "adherence": 68.0,
        "palette_delta": 9.0, "backend": "clip", "tier": "degraded",
        "mandatory_satisfied": False, "calibration": "provisional",
        "passed": False, "degraded": True, "latency_ms": 63.4,
        "measured": {"aesthetic": "clip-probe-margin",
                     "novelty": "clip-image-embedding-cosine",
                     "adherence": "clip-image-text-cosine",
                     "palette_delta": "lab-histogram-hellinger"},
        "failures": [
            "novelty 22.0 is under the 45.0 floor; this frame is a near-duplicate "
            "of something already in the last 64 frames (closest cosine distance "
            "0.031)",
            "palette delta 9.0 is under the 25.0 floor; the colourway is nearly "
            "identical to a recent frame, which is what mode collapse looks like "
            "early",
        ],
        "reasons": [
            "DEGRADED: DINOv2 is mandatory by operator direction and is NOT "
            "loaded on this node; novelty and aesthetic are proxies, not the "
            "declared sensory model",
            "novelty 22.0 is under the 45.0 floor; this frame is a near-duplicate "
            "of something already in the last 64 frames (closest cosine distance "
            "0.031)",
            "palette delta 9.0 is under the 25.0 floor; the colourway is nearly "
            "identical to a recent frame, which is what mode collapse looks like "
            "early",
        ],
        "notes": ["running the CLIP fallback tier, not SigLIP/DINOv2"],
        "image_path": "/runs/flux-output/arc/d90b2f13.png",
    })

    # tier `emergency`: no vision model at all. `adherence` is UNMEASURABLE and
    # renders as a rail and a dash -- never as a zero, never as a bar.
    log.gates({
        "aesthetic": 58.0, "novelty": 50.0, "adherence": 50.0,
        "palette_delta": 41.0, "backend": "heuristic", "tier": "emergency",
        "mandatory_satisfied": False, "calibration": "heuristic",
        "passed": True, "degraded": True, "latency_ms": 4.9,
        "measured": {"aesthetic": "pixel-heuristic",
                     "novelty": "handcrafted-fp128-cosine (raw)",
                     "adherence": "unavailable",
                     "palette_delta": "lab-histogram-hellinger"},
        "failures": [],
        "reasons": [
            "DEGRADED: DINOv2 is mandatory by operator direction and is NOT "
            "loaded on this node; this is the pure-pixel heuristic tier",
        ],
        "notes": [
            "adherence was excluded from the verdict because no image-text "
            "model was available to measure it",
            "novelty is a cold-start placeholder: only 3 frame(s) of this "
            "embedding kind are in memory, fewer than the 8 needed",
        ],
        "image_path": "/runs/flux-output/arc/0a17cc52.png",
    })

    # ---- fortiche -------------------------------------------------------
    log.fortiche({
        "impasto": 0.88, "planarity": 0.79, "chiaroscuro": 0.91,
        "palette_zaun": 0.72, "palette_piltover": 0.34, "anti_cgi": 0.83,
        "fortiche_score": 0.78, "verdict": "conformant", "realm": "zaun",
        "threshold": 0.65, "backend": "siglip+dinov2", "available": True,
    })

    # ---- panels, trees, tail --------------------------------------------
    log.panel("MOVEMENT TOWARDS MASTER",
              "The masterpiece feed took 3 frames this hour. Two of them share a "
              "seed lineage with arc-atlas-0041, which is the first time the "
              "atlas has fed the tier feed rather than the other way round.",
              meta="feed · last 60m", color="gold")

    log.tree("ARCANE PIPELINE", {
        "ingest": [("perpetual_feeder.py", "prompt synthesis & anti-collapse"),
                   ("flux worker (UDS)", "bf16 render, .fluxd/flux-gpu0.sock")],
        "triage": [("sensory_gates.py", "siglip + dinov2, 4 metrics, ~40ms"),
                   ("arcane_aesthetic.py", "six Fortiche invariants")],
        "jury": [("moj_evaluator.py", "3 judges, quorum 2, percentile CDF"),
                 ("jury_continuum.toml", "tenancy, VRAM, strictness")],
        "sinks": [("audit.jsonl", "every receipt, scored or not"),
                  ("masterpiece.jsonl", "tier ≥ 98th percentile"),
                  ("arcane-*.jsonl", "this logger, tailed by the studio")],
    })

    log.rule("tail")
    log.info("3 verdicts rendered", scored=2, unscored=1)
    log.warn("pixtral-critic still down", since="14m ago")
    log.error("r2 sync failed", bucket="aons-beauty", key="masterpiece/9f2c1e04.png")
    log.event("demo_complete", renderers=11, run_id=log.run_id())
    log.kv("jsonl sink", str(log.sink_path))
    log.kv("run", log.run_id(), state="done")
    print()


if __name__ == "__main__":
    _demo()
