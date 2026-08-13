#!/home/dev/venv/bin/python
"""Ground truth: the operator's eye, captured one forced choice at a time.

Thousands of images exist on this node and not one of them carries a human
verdict. Every number the system currently optimises -- fitness against taste.py's
two machine-seeded anchors, novelty against a text centroid, craft against three
failure probes -- is therefore unvalidated. We do not know whether any of them
points where the operator points, and until we do a rising score is not evidence
of anything.

The scarce resource is the operator's attention, not compute, so the protocol is
the cheapest one that still yields real signal: two images, one question -- which
is less generic -- one click. A pairwise choice needs no calibration, no
remembered scale and no vocabulary; it is the same judgement whether it is the
first of the day or the hundredth. Scoring an image 1-10 is a much worse deal:
the scale drifts between sessions and most of the answer is the rater's mood.

From those choices this builds three things:

  ratings     Bradley-Terry (in its Elo parameterisation) per card, aggregated to
              a rating per CONFIG, because the actionable question is not which
              image won but which generator deserves the GPU.
  specimens   the cards that keep winning -- the Peak Specimen Library, the
              baseline everything else is measured against. A standard made of
              real images cannot drift the way a remembered standard does.
  agreement   how often each automatic metric picked the same winner as the
              human. That single number says whether the watcher is worth
              listening to. Below 0.5 a metric is not noise, it is inverted, and
              that is just as useful to know.

Pair selection is what actually decides how much a click is worth. A random pair
is usually a blowout, and a blowout teaches nothing -- the winner was already
predictable, so the answer carries almost no information. This picks pairs the
system cannot already call: close on the automatic metrics (so the human is
resolving a real tie rather than confirming an obvious one), close on current
rating, at least one card barely compared, and drawn from two configs that have
not met often, since ranking the configs is the point. Every third pair faces a
current Peak Specimen, so the ladder stays anchored to one standard instead of
fragmenting into disconnected pockets that were never compared to each other.

taste.py is the thing NOT to copy. It measured similarity to two images the
machine picked for itself and called that fitness. Nothing here is a landscape
until a human made a choice that put it there.

Automatic scores come from watch.jsonl, which watch.py owns and which is the
authority whenever it has a row. It does not always have one -- the watcher is a
separate process that can be down, behind, or (as of this writing) failing to
embed at all -- and a ground-truth layer that cannot start until another service
is healthy is not a ground-truth layer. So `duel.py score` computes the same
three metrics, from watch.py's own probe lists and its own vacancy(), into
duel_scores.jsonl as a stand-in. watch.jsonl always wins where the two overlap.

CLI:
    duel.py score [limit]   backfill automatic scores for cards watch has missed
    duel.py next            print the next pair
    duel.py standings       print ratings, agreement, specimens
    duel.py rebuild         replay duels.jsonl into duel_ratings.json
"""
import contextlib
import fcntl
import json
import math
import os
import pathlib
import random
import sys
import threading
import time

sys.path.insert(0, "/home/dev")

import watch as W          # config_of, and the probe set the metrics are defined by

HOME = pathlib.Path("/home/dev")
RUNS = HOME / "runs"
RATINGS = HOME / "duel_ratings.json"
DUELS = HOME / "duels.jsonl"
SPECIMENS = HOME / "specimens.json"
SCORES = HOME / "duel_scores.jsonl"     # stand-in for watch.jsonl, same schema
WATCH_LOG = HOME / "watch.jsonl"        # watch.py's own output: authoritative
LOCK = HOME / "duel.lock"

METRICS = ("novelty", "craft", "flat")

# Elo parameterisation of Bradley-Terry. 400 is only the scale in which the
# strengths are printed; the update is plain gradient ascent on the BT
# log-likelihood, so the choice of scale changes no ordering.
START = 1500.0
SCALE = 400.0
K_NEW = 32.0            # a card with almost no record should move fast
K_SETTLED = 16.0
SETTLE_AFTER = 10
K_CONFIG = 12.0         # a config aggregates many duels; it should move slowly

SPECIMEN_N = 12
SPECIMEN_MIN_COMPARISONS = 3    # one lucky win is not a standard

ANCHOR_EVERY = 3        # one duel in three faces the current standard-bearer
SAMPLE_PAIRS = 600      # candidate pairs considered per selection
MIN_SEPARATION = 0.05   # pooled standard deviations; below this it is one image
NEWCOMERS = 80          # never-compared cards admitted to the pool per call
CORPUS_TTL = 45.0

# A loss is weak evidence -- a good card can lose to a Peak Specimen. Only a card
# that has lost this many times and never once won is written through as a
# retire, because taste.harvest turns every retire into a permanent repulsor and
# poisoning that landscape is worse than leaving it thin.
RETIRE_AFTER_LOSSES = 3

_mem_lock = threading.Lock()
_corpus = None
_corpus_lock = threading.Lock()
_refreshing = False
_state_cache = None
_state_stamp = None


# --------------------------------------------------------------------- files


def _read_json(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return default


def _write_json(p, obj):
    """Atomic: a half-written ratings file would be indistinguishable from a
    corrupt one, and this is the only durable record of the maths."""
    p = pathlib.Path(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, p)


@contextlib.contextmanager
def _flock(path):
    """Cross-process exclusive lock. control.py and the CLI both write these
    files, and a lost update here is a judgement the operator made and we threw
    away."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def write_verdict(gen, key, verdict, note="", critic="operator"):
    """THE write path for runs/<gen>/verdicts.json.

    Lives here rather than in control.py so that /api/verdict and the duel
    write-through are literally the same code: two divergent writers of the same
    file is how taste.harvest and watch.verdict_separation end up disagreeing
    about what the operator said. Returns False when the generation is gone.
    """
    d = RUNS / gen
    if not d.is_dir():
        return False
    with _flock(d / "verdicts.lock"):
        data = _read_json(d / "verdicts.json", {}) or {}
        if verdict == "clear":
            data.pop(key, None)
        else:
            data[key] = {"score": None, "verdict": verdict,
                         "note": note, "critic": critic}
        _write_json(d / "verdicts.json", data)
    return True


# ------------------------------------------------------------ automatic scores


def _score_index():
    """card id -> {novelty, craft, flat}. watch.jsonl wins wherever it has a row;
    duel_scores.jsonl only fills the gaps it leaves."""
    idx = {}
    for src in (SCORES, WATCH_LOG):          # watch read second: it overwrites
        if not src.is_file():
            continue
        try:
            lines = src.read_text().splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            g, k = r.get("gen"), r.get("key")
            if not g or not k:
                continue
            vals = {m: r[m] for m in METRICS if isinstance(r.get(m), (int, float))}
            if vals:
                idx[f"{g}/{k}"] = vals
    return idx


# -------------------------------------------------------------------- corpus


def _stamp():
    """Cheap freshness key. New generations move the runs mtime; new score rows
    move a log's size. Anything subtler is caught by the TTL."""
    def st(p):
        try:
            s = p.stat()
            return (round(s.st_mtime, 2), s.st_size)
        except OSError:
            return (0.0, 0)
    return (st(RUNS), st(WATCH_LOG), st(SCORES))


def _build_corpus():
    scores = _score_index()
    cards = {}
    for d in sorted(RUNS.glob("gen-*")):
        run = _read_json(d / "run.json", None)
        if not isinstance(run, dict):
            continue
        label = run.get("label", "")
        cfg = W.config_of(label)
        cdir = d / "cards"
        for c in run.get("cards", []) or []:
            k = c.get("key")
            # A card whose pixels were vacuumed away cannot be shown, so it is
            # simply not in the corpus -- everything downstream inherits that.
            if not k or not (cdir / f"{k}.png").is_file():
                continue
            cid = f"{d.name}/{k}"
            cards[cid] = {"id": cid, "gen": d.name, "key": k, "config": cfg,
                          "label": label, "scores": scores.get(cid, {})}
    # Per-metric spread, so "close on the automatic metrics" means close relative
    # to how much that metric varies at all rather than close in raw units.
    sd = {}
    for m in METRICS:
        vals = [c["scores"][m] for c in cards.values() if m in c["scores"]]
        if len(vals) >= 8:
            mu = sum(vals) / len(vals)
            var = sum((v - mu) ** 2 for v in vals) / len(vals)
            sd[m] = math.sqrt(var) or 1.0
        else:
            sd[m] = 1.0
    for c in cards.values():
        c["z"] = {m: v / sd[m] for m, v in c["scores"].items() if m in sd}
    ids = list(cards)
    scored = [i for i in ids if cards[i]["scores"]]
    by_config = {}
    for cid in scored:
        by_config.setdefault(cards[cid]["config"], []).append(cid)
    return {"cards": cards, "ids": ids, "scored": scored,
            "by_config": by_config, "sd": sd,
            "at": time.time(), "stamp": _stamp()}


def _fresh(c):
    """Inside the TTL the index is fresh, full stop.

    Checking the stamp too would sound stricter and is actually worse: watch.py
    appends a score row every minute and a scoring pass appends continuously, so
    the stamp moves on almost every request and we would rebuild on almost every
    request -- background threads that then contend for the GIL with the handler
    they were supposed to unblock. The stamp's real job is the opposite one:
    once the TTL HAS expired, it says whether anything moved at all, so an idle
    node stops rebuilding entirely.
    """
    if c is None:
        return False
    if time.time() - c["at"] < CORPUS_TTL:
        return True
    if c["stamp"] == _stamp():
        c["at"] = time.time()          # nothing moved: start the window again
        return True
    return False


def _refresh_async():
    """One background rebuild at a time; extra callers are dropped."""
    global _refreshing
    with _corpus_lock:
        if _refreshing:
            return
        _refreshing = True

    def run():
        global _corpus, _refreshing
        try:
            c = _build_corpus()
            _corpus = c
        finally:
            _refreshing = False

    threading.Thread(target=run, daemon=True).start()


def corpus(force=False):
    """Cached card index, refreshed stale-while-revalidate.

    A rebuild is ~40ms idle but seconds when the disk is busy behind a render or
    a scoring pass -- and the stamp moves every time watch.py appends a row, so
    a handler that rebuilt inline would eat that stall once a minute. A stale
    index costs nothing worse than a card being a minute late into the pool, so
    the request is served from what we have and the rebuild happens behind it.
    Only a completely cold cache blocks, and warm() takes care of that at start.
    """
    global _corpus
    c = _corpus
    if not force and _fresh(c):
        return c
    if not force and c is not None:
        _refresh_async()
        return c
    with _corpus_lock:
        if not force and _fresh(_corpus):
            return _corpus
        _corpus = _build_corpus()
        return _corpus


def warm():
    """Build the corpus off the request path at process start."""
    t = threading.Thread(target=lambda: corpus(force=True), daemon=True)
    t.start()
    return t


# -------------------------------------------------------------------- ratings


def _pair_key(a, b):
    return "|".join(sorted((a, b)))


def _expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / SCALE))


def _k(n):
    return K_NEW if n < SETTLE_AFTER else K_SETTLED


def _blank_state():
    return {"cards": {}, "configs": {}, "config_pairs": {}, "pairs": [],
            "duels": 0, "skips": 0, "updated": 0.0}


def _card_row(st, cid, config):
    row = st["cards"].get(cid)
    if row is None:
        gen, _, key = cid.partition("/")
        row = {"gen": gen, "key": key, "config": config,
               "rating": START, "n": 0, "wins": 0, "losses": 0}
        st["cards"][cid] = row
    if config and config != "unknown":
        row["config"] = config
    return row


def _config_row(st, cfg):
    return st["configs"].setdefault(
        cfg, {"rating": START, "n": 0, "wins": 0, "losses": 0})


def _apply(st, e):
    """Fold one logged judgement into the ratings.

    The ONLY place a rating moves, which is what makes duels.jsonl the truth and
    duel_ratings.json a pure derivative: replaying the log reproduces the file.
    """
    a, b, w = e["a"], e["b"], e["winner"]
    pk = _pair_key(a, b)
    if pk not in st["_seen"]:
        st["_seen"].add(pk)
        st["pairs"].append(pk)
    ra_row = _card_row(st, a, e.get("a_config"))
    rb_row = _card_row(st, b, e.get("b_config"))
    if w == "skip":
        st["skips"] += 1
        return
    st["duels"] += 1

    sa = 1.0 if w == "a" else 0.0
    ea = _expected(ra_row["rating"], rb_row["rating"])
    ra_row["rating"] += _k(ra_row["n"]) * (sa - ea)
    rb_row["rating"] += _k(rb_row["n"]) * ((1.0 - sa) - (1.0 - ea))
    for row, s in ((ra_row, sa), (rb_row, 1.0 - sa)):
        row["n"] += 1
        row["wins"] += int(s == 1.0)
        row["losses"] += int(s == 0.0)

    ca, cb = ra_row["config"], rb_row["config"]
    if ca != cb:
        # Only a cross-config duel says anything about which config is better; a
        # vision-vs-vision result would just add noise to both sides equally.
        st["config_pairs"][_pair_key(ca, cb)] = \
            st["config_pairs"].get(_pair_key(ca, cb), 0) + 1
        A, B = _config_row(st, ca), _config_row(st, cb)
        ea = _expected(A["rating"], B["rating"])
        A["rating"] += K_CONFIG * (sa - ea)
        B["rating"] += K_CONFIG * ((1.0 - sa) - (1.0 - ea))
        for row, s in ((A, sa), (B, 1.0 - sa)):
            row["n"] += 1
            row["wins"] += int(s == 1.0)
            row["losses"] += int(s == 0.0)


def _log_rows():
    if not DUELS.is_file():
        return []
    out = []
    try:
        lines = DUELS.read_text().splitlines()
    except OSError:
        return []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def rebuild():
    """Replay the append-only log. Recovers a deleted or stale ratings file, and
    is the check that the update rule is the only thing moving the numbers."""
    st = _blank_state()
    st["_seen"] = set()
    for e in _log_rows():
        _apply(st, e)
    return st


def _hydrate(st):
    st.setdefault("cards", {})
    st.setdefault("configs", {})
    st.setdefault("config_pairs", {})
    st.setdefault("pairs", [])
    st.setdefault("duels", 0)
    st.setdefault("skips", 0)
    st["_seen"] = set(st["pairs"])
    return st


def _load_state():
    d = _read_json(RATINGS, None)
    if not isinstance(d, dict) or "cards" not in d:
        return rebuild()
    return _hydrate(d)


def _save_state(st):
    st["updated"] = time.time()
    _write_json(RATINGS, {k: v for k, v in st.items() if not k.startswith("_")})


def state():
    """Read-mostly view for selection and standings, reloaded when the file moves
    under us (the CLI and the server both write it)."""
    global _state_cache, _state_stamp
    try:
        s = RATINGS.stat()
        stamp = (round(s.st_mtime, 3), s.st_size)
    except OSError:
        stamp = None
    if _state_cache is None or stamp != _state_stamp:
        _state_cache = _load_state()
        _state_stamp = stamp
    return _state_cache


# ------------------------------------------------------------------ specimens


def _specimen_rows(st, co):
    rows = []
    for cid, r in st["cards"].items():
        if r["n"] < SPECIMEN_MIN_COMPARISONS:
            continue
        # Top-N alone is not enough. Early on, only a handful of cards have
        # enough comparisons to qualify, and a card that has LOST most of them
        # would be admitted purely for being one of the few that were measured.
        # A specimen has to have actually won: above the starting rating or it
        # is not a standard anything should be compared against.
        if r["rating"] <= START:
            continue
        if cid not in co["cards"]:      # pixels gone: it cannot be a baseline
            continue
        rows.append({"id": cid, "gen": r["gen"], "key": r["key"],
                     "config": r["config"], "rating": round(r["rating"], 1),
                     "comparisons": r["n"], "wins": r["wins"],
                     "losses": r["losses"],
                     "card_url": f"/img/{r['gen']}/{r['key']}",
                     "scores": co["cards"][cid]["scores"]})
    rows.sort(key=lambda x: (-x["rating"], -x["comparisons"]))
    return rows[:SPECIMEN_N]


def _save_specimens(rows):
    _write_json(SPECIMENS, {
        "updated": time.time(),
        "min_comparisons": SPECIMEN_MIN_COMPARISONS,
        "n": len(rows),
        "specimens": rows,
        "note": "the Peak Specimen Library: cards that keep winning against "
                "the operator's eye. Use these as the baseline a new image has "
                "to beat, not a machine-seeded anchor.",
    })


def specimens():
    """Public accessor so other components can take the library as a baseline
    without re-deriving it (or re-inventing the threshold)."""
    d = _read_json(SPECIMENS, None)
    if isinstance(d, dict) and isinstance(d.get("specimens"), list):
        return d["specimens"]
    return _specimen_rows(state(), corpus())


# ------------------------------------------------------------ pair selection


def _rating_of(st, cid):
    r = st["cards"].get(cid)
    return r["rating"] if r else START


def _n_of(st, cid):
    r = st["cards"].get(cid)
    return r["n"] if r else 0


def _metric_distance(x, y, co):
    """Mean absolute difference across the shared metrics, in pooled standard
    deviations. None when the two have no metric in common."""
    zx, zy = co["cards"][x]["z"], co["cards"][y]["z"]
    shared = [m for m in METRICS if m in zx and m in zy]
    if not shared:
        return None
    return sum(abs(zx[m] - zy[m]) for m in shared) / len(shared)


def _pair_value(x, y, co, st):
    """How much we expect one click on this pair to teach us.

    Four terms, all in 0..1:
      closeness  the automatic metrics barely separate these, so the human is
                 resolving a genuine tie rather than rubber-stamping a rout --
                 but not so close that it is the same picture twice, which is
                 rejected outright above.
      info       Bradley-Terry information is 4p(1-p): maximal when the current
                 ratings call it even, zero when the outcome is already known.
      unc        at least one card has barely been compared, so its rating is
                 mostly prior.
      cfg        different configs, preferring the config pairing we have the
                 least evidence about -- ranking configs is the actual goal.
    """
    cx, cy = co["cards"][x], co["cards"][y]
    d = _metric_distance(x, y, co)
    if d is not None and d < MIN_SEPARATION:
        return None                 # the same image twice: no click to be had
    closeness = 1.0 / (1.0 + (d if d is not None else 1.0))

    p = _expected(_rating_of(st, x), _rating_of(st, y))
    info = 4.0 * p * (1.0 - p)
    unc = 1.0 / (1.0 + min(_n_of(st, x), _n_of(st, y)))

    if cx["config"] != cy["config"]:
        seen = st["config_pairs"].get(_pair_key(cx["config"], cy["config"]), 0)
        cfg = 1.0 / (1.0 + seen)
    else:
        cfg = 0.0
    return 0.35 * closeness + 0.20 * info + 0.20 * unc + 0.25 * cfg


def _standard_bearer(st, co, rng):
    """A current Peak Specimen to defend the title. Before any card has enough
    comparisons to be a specimen, the best-rated card that has fought at all
    stands in -- otherwise the ladder never consolidates: every duel would go to
    two fresh cards and nothing would ever reach three comparisons."""
    rows = _specimen_rows(st, co)
    if rows:
        return rng.choice(rows[:max(3, len(rows) // 2)])["id"]
    live = [(r["rating"], cid) for cid, r in st["cards"].items()
            if r["n"] >= 1 and cid in co["cards"]]
    if not live:
        return None
    live.sort(reverse=True)
    return live[0][1]


def _pool(st, co, rng):
    """The working set. Comparing 2000 cards pairwise is not a thing a human
    does, and it is not what we want: we want the peak. So the pool is everything
    already on the ladder plus a rotating uniform sample of untouched cards.

    Newcomers are stratified by config and, within a config, sampled UNIFORMLY.

    Stratified because sampling cards uniformly would make the pool a sample of
    PRODUCTION VOLUME -- auto has rendered hundreds of cards and grid ten, so
    grid would essentially never appear and could never be ranked. Uniform
    within a config because admitting cards by novelty would seed the pool with
    the very metric we are trying to validate, and the agreement number would
    then be measuring our own sampling rather than the metric.
    """
    if len(co["scored"]) >= 40:
        groups = co["by_config"]
    else:
        groups = {}
        for cid in co["ids"]:
            groups.setdefault(co["cards"][cid]["config"], []).append(cid)
    ladder = [cid for cid in st["cards"] if cid in co["cards"]]
    # Sample within a config rather than shuffling it: the groups belong to the
    # shared index and a request has no business reordering them.
    per = max(2, NEWCOMERS // max(1, len(groups)) + 1)
    queues = []
    for g in groups.values():
        pick = g if len(g) <= per else rng.sample(g, per)
        queues.append([c for c in pick if c not in st["cards"]])
    queues.sort(key=len, reverse=True)
    fresh, i = [], 0
    while len(fresh) < NEWCOMERS and any(queues):
        q = queues[i % len(queues)]
        if q:
            fresh.append(q.pop())
        i += 1
    pool = list(dict.fromkeys(ladder + fresh))
    return [c for c in pool if c in co["cards"]]


def next_pair(rng=None):
    """The next two cards to show. None when the pool is exhausted."""
    rng = rng or random
    co = corpus()
    st = state()
    pool = _pool(st, co, rng)
    if len(pool) < 2:
        return None
    seen = st["_seen"]

    anchor = None
    if (st["duels"] + 1) % ANCHOR_EVERY == 0:
        anchor = _standard_bearer(st, co, rng)

    def sample(fixed):
        best = []
        for _ in range(SAMPLE_PAIRS):
            x = fixed or rng.choice(pool)
            y = rng.choice(pool)
            if x == y or _pair_key(x, y) in seen:
                continue
            v = _pair_value(x, y, co, st)
            if v is None:
                continue
            best.append((v, x, y))
        if not best:
            return None
        best.sort(key=lambda t: -t[0])
        # Top-k rather than argmax: a strictly greedy selector keeps proposing
        # the same neighbourhood while the operator is deciding.
        return rng.choice(best[:3])

    pick = sample(anchor) if anchor else None
    if pick is None:
        pick = sample(None)
    if pick is None:
        return None
    value, x, y = pick
    if rng.random() < 0.5:              # no positional bias for the left slot
        x, y = y, x

    def side(cid):
        c = co["cards"][cid]
        r = st["cards"].get(cid) or {}
        return {"id": cid, "gen": c["gen"], "key": c["key"],
                "config": c["config"], "label": c["label"],
                "scores": c["scores"],
                "rating": round(r.get("rating", START), 1),
                "comparisons": r.get("n", 0),
                "card_url": f"/img/{c['gen']}/{c['key']}"}

    return {"a": side(x), "b": side(y),
            "against_specimen": bool(anchor) and anchor in (x, y),
            "selection_value": round(value, 4),
            "pool": len(pool), "duels": st["duels"]}


# -------------------------------------------------------------------- verdict


def _writethrough(st, e):
    """Push the result into runs/<gen>/verdicts.json through the one writer, so
    watch.verdict_separation and taste.harvest see the operator's choices.

    Winner -> keep. Loser -> retire ONLY once it has lost repeatedly and never
    won: one loss usually means it drew a Peak Specimen, and turning that into a
    permanent repulsor would teach the landscape something false.
    """
    if e["winner"] == "skip":
        return []
    win = e["a"] if e["winner"] == "a" else e["b"]
    lose = e["b"] if e["winner"] == "a" else e["a"]
    out = []
    g, _, k = win.partition("/")
    if write_verdict(g, k, "keep", note=f"duel: chosen over {lose}"):
        out.append({"gen": g, "key": k, "verdict": "keep"})
    row = st["cards"].get(lose) or {}
    if row.get("wins", 0) == 0 and row.get("losses", 0) >= RETIRE_AFTER_LOSSES:
        g, _, k = lose.partition("/")
        if write_verdict(g, k, "retire",
                         note=f"duel: lost {row['losses']} of {row['losses']}"):
            out.append({"gen": g, "key": k, "verdict": "retire"})
    return out


def record(a, b, winner, note="", by="operator"):
    """Record one judgement: append to the log, move the ratings, refresh the
    specimen library, write the verdict through."""
    if winner not in ("a", "b", "skip"):
        raise ValueError("winner must be 'a', 'b' or 'skip'")
    if not a or not b or "/" not in a or "/" not in b:
        raise ValueError("card ids must look like 'gen-0001/key'")
    if a == b:
        raise ValueError("a card cannot be duelled against itself")

    co = corpus()
    ca, cb = co["cards"].get(a), co["cards"].get(b)
    # A card can be deleted between being served and being judged. The judgement
    # is still real, so it is recorded; only its config and scores fall back.
    e = {"at": time.time(), "a": a, "b": b, "winner": winner, "by": by,
         "note": note,
         "a_config": (ca or {}).get("config", "unknown"),
         "b_config": (cb or {}).get("config", "unknown"),
         "a_scores": (ca or {}).get("scores", {}),
         "b_scores": (cb or {}).get("scores", {}),
         "a_missing": ca is None, "b_missing": cb is None}

    with _mem_lock, _flock(LOCK):
        st = _load_state()
        e["a_rating_before"] = round(_rating_of(st, a), 1)
        e["b_rating_before"] = round(_rating_of(st, b), 1)
        with DUELS.open("a") as fh:         # the log is written first: it is the
            fh.write(json.dumps(e) + "\n")  # truth, the ratings are derived
        _apply(st, e)
        _save_state(st)
        rows = _specimen_rows(st, co)
        _save_specimens(rows)
        global _state_cache, _state_stamp
        _state_cache, _state_stamp = None, None

    wrote = _writethrough(st, e)
    return {"ok": True, "recorded": {"a": a, "b": b, "winner": winner},
            "duels": st["duels"], "skips": st["skips"],
            "a_rating": round(_rating_of(st, a), 1),
            "b_rating": round(_rating_of(st, b), 1),
            "configs": _config_standings(st),
            "specimens": rows, "verdicts_written": wrote}


# ------------------------------------------------------------------ standings


def _config_standings(st):
    co = corpus()
    means = {}
    for cid, r in st["cards"].items():
        if r["n"] >= 1:
            means.setdefault(r["config"], []).append(r["rating"])
    out = []
    for cfg, r in st["configs"].items():
        m = means.get(cfg, [])
        out.append({"config": cfg, "rating": round(r["rating"], 1),
                    "duels": r["n"], "wins": r["wins"], "losses": r["losses"],
                    "win_rate": round(r["wins"] / r["n"], 3) if r["n"] else None,
                    "cards_rated": len(m),
                    "mean_card_rating": round(sum(m) / len(m), 1) if m else None})
    # Configs that have only met their own kind never enter the cross-config
    # ladder; show them anyway so a config is never silently missing.
    for cfg, m in means.items():
        if cfg not in st["configs"]:
            out.append({"config": cfg, "rating": None, "duels": 0, "wins": 0,
                        "losses": 0, "win_rate": None, "cards_rated": len(m),
                        "mean_card_rating": round(sum(m) / len(m), 1)})
    out.sort(key=lambda x: (x["rating"] is None, -(x["rating"] or 0)))
    return out


def agreement(rows=None):
    """Per metric: how often the higher value picked the human's winner.

    Read it as a coin test, not a score. Near 0.5 the metric knows nothing about
    what the operator sees. Below 0.5 it is inverted, which is a usable finding
    (flat in particular has no agreed direction -- watch.py treats emptiness as a
    virtue, and this is the measurement that settles it).
    """
    rows = _log_rows() if rows is None else rows
    out = {}
    for m in METRICS:
        hit = tie = seen = 0
        for e in rows:
            if e.get("winner") not in ("a", "b"):
                continue
            va = (e.get("a_scores") or {}).get(m)
            vb = (e.get("b_scores") or {}).get(m)
            if va is None or vb is None:
                continue
            seen += 1
            if va == vb:
                tie += 1
                continue
            if ("a" if va > vb else "b") == e["winner"]:
                hit += 1
        decided = seen - tie
        acc = hit / decided if decided else None
        if acc is None or decided < 10:
            reading = "too few comparisons to call"
        elif acc >= 0.6:
            reading = "agrees with the operator"
        elif acc <= 0.4:
            reading = "inverted: lower values win"
        else:
            reading = "no signal: indistinguishable from a coin"
        out[m] = {"accuracy": round(acc, 4) if acc is not None else None,
                  "n": decided, "ties": tie, "agreed": hit, "reading": reading}
    return out


def standings():
    st = state()
    co = corpus()
    rows = _log_rows()
    spec = _read_json(SPECIMENS, None)
    spec = spec["specimens"] if isinstance(spec, dict) and "specimens" in spec \
        else _specimen_rows(st, co)
    top = sorted(((r["rating"], cid) for cid, r in st["cards"].items()
                  if r["n"] >= 1), reverse=True)[:10]
    return {
        "duels": st["duels"],
        "skips": st["skips"],
        "cards_rated": len(st["cards"]),
        "configs": _config_standings(st),
        "agreement": agreement(rows),
        "specimens": spec,
        "top_cards": [{"id": cid, "rating": round(r, 1),
                       "comparisons": st["cards"][cid]["n"],
                       "config": st["cards"][cid]["config"]} for r, cid in top],
        "corpus": {"cards": len(co["ids"]), "with_scores": len(co["scored"]),
                   "score_sources": {"watch.jsonl": WATCH_LOG.is_file(),
                                     "duel_scores.jsonl": SCORES.is_file()}},
        "note": "agreement is the point of the whole exercise: it says whether "
                "the automatic metrics track the operator or not.",
    }


# ------------------------------------------------------- stand-in score pass


def _as_tensor(out, fallback):
    """transformers 5.x hands back an output object from get_*_features on this
    build; watch.py assumes a bare tensor and dies on .norm(). Same defence
    taste.embed already carries."""
    import torch
    if torch.is_tensor(out):
        return out
    for attr in ("image_embeds", "text_embeds", "pooler_output"):
        v = getattr(out, attr, None)
        if torch.is_tensor(v):
            return v
    return fallback()


def _backfill_order(co):
    """Unscored cards, interleaved across configs, newest generation first.

    Straight newest-first would spend an hour on whatever the loop happened to
    render most recently and leave four configs with no scores at all -- and a
    config with no scored cards cannot enter a pair, so it never gets ranked.
    Round-robin buys breadth first, which is what the ratings are for.
    """
    by_cfg = {}
    for cid in co["ids"]:
        if co["cards"][cid]["scores"]:
            continue
        by_cfg.setdefault(co["cards"][cid]["config"], []).append(cid)
    for v in by_cfg.values():
        v.sort(reverse=True)
    order, queues = [], sorted(by_cfg.values(), key=len, reverse=True)
    i = 0
    while any(queues):
        q = queues[i % len(queues)]
        if q:
            order.append(q.pop(0))
        i += 1
    return order


def backfill(limit=None, batch=24, device=None):
    """Compute novelty / craft / flat for cards watch.jsonl has no row for.

    Deliberately the same definitions -- watch.GENERIC/GOOD/BAD and
    watch.vacancy are imported, not copied -- so a row written here is
    interchangeable with one watch.py writes.
    """
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    co = corpus(force=True)
    todo = _backfill_order(co)
    if limit:
        todo = todo[:limit]
    if not todo:
        return 0

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    proc = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
    model = CLIPModel.from_pretrained(
        "openai/clip-vit-base-patch32",
        dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev).eval()

    def text(ts):
        tk = proc(text=ts, return_tensors="pt", padding=True,
                  truncation=True).to(dev)
        with torch.no_grad():
            e = _as_tensor(model.get_text_features(**tk),
                           lambda: model.text_projection(
                               model.text_model(**tk).pooler_output)).float()
        return e / e.norm(dim=-1, keepdim=True)

    gen = text(W.GENERIC).mean(0, keepdim=True)
    gen = gen / gen.norm()
    good, bad = text(W.GOOD), text(W.BAD)

    written = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        ims, keep = [], []
        for cid in chunk:
            c = co["cards"][cid]
            try:
                ims.append(Image.open(
                    RUNS / c["gen"] / "cards" / f"{c['key']}.png").convert("RGB"))
                keep.append(cid)
            except Exception:
                continue          # deleted or truncated under us: skip it
        if not ims:
            continue
        tk = proc(images=ims, return_tensors="pt").to(dev)
        with torch.no_grad():
            e = _as_tensor(model.get_image_features(**tk),
                           lambda: model.visual_projection(
                               model.vision_model(
                                   pixel_values=tk["pixel_values"]).pooler_output))
        e = e.float()
        e = e / e.norm(dim=-1, keepdim=True)
        with SCORES.open("a") as fh:
            for j, cid in enumerate(keep):
                v = e[j:j + 1]
                novelty = 1.0 - float(v @ gen.T)
                gs = float((v @ good.T).mean())
                bs = float((v @ bad.T).mean())
                craft = 1.0 / (1.0 + math.exp(-(gs - bs) * 40))
                flat, edge = W.vacancy(ims[j])
                c = co["cards"][cid]
                fh.write(json.dumps({
                    "gen": c["gen"], "key": c["key"], "config": c["config"],
                    "label": c["label"], "novelty": round(novelty, 4),
                    "craft": round(craft, 4), "flat": round(flat, 4),
                    "edge": round(edge, 3), "at": time.time(),
                    "by": "duel.backfill"}) + "\n")
                written += 1
        for im in ims:
            im.close()
        print(f"[duel] scored {written}/{len(todo)}", flush=True)
    corpus(force=True)
    return written


# ------------------------------------------------------------------------ cli


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "standings"
    if cmd == "score":
        n = int(argv[2]) if len(argv) > 2 else None
        print(json.dumps({"scored": backfill(n)}, indent=2))
    elif cmd == "next":
        print(json.dumps(next_pair(), indent=2))
    elif cmd == "standings":
        print(json.dumps(standings(), indent=2))
    elif cmd == "rebuild":
        with _flock(LOCK):
            st = rebuild()
            _save_state(st)
            _save_specimens(_specimen_rows(st, corpus(force=True)))
        print(json.dumps({"rebuilt_from": str(DUELS), "duels": st["duels"],
                          "cards": len(st["cards"])}, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
