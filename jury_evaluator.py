#!/usr/bin/env python3
"""Sovereign FLUX Visual Jury daemon.

This process tails the fluxd jobs ledger and, for every render that settles,
asks :mod:`moj_evaluator` for a verdict.  It owns persistence and routing --
``audit.jsonl``, ``jury.sqlite3``, the spectacle/masterpiece/defect feeds, and
the Cloudflare R2 streaming -- and nothing else.

What changed
------------
``score_frame()`` used to compute all four judge scores from
``hash(prompt + "pixtral") % 12``.  It never made an HTTP call and never sent an
image to a vision model; the "jury" was four deterministic string hashes wearing
a percentile curve.  Every one of those code paths is gone.  Scores now come
from :func:`moj_evaluator.evaluate`, which sends the actual PNG to actual
vision-language models and, when a judge cannot be reached, records
``tier: "unscored"`` with ``composite: None`` rather than inventing a number.

Also removed: ``calculate_novelty_bonus()``, which moved scores up or down by up
to 14 points based on keyword matches against the *prompt string*
(``EXCEPTIONAL_TRAITS`` / ``BANAL_CLICHES``).  That was a second, quieter way of
scoring an image without looking at it.  Real novelty evidence now comes from
``uniqueness_tracker``'s 128-d perceptual fingerprint and from
``sensory_gates``, both of which read pixels.

The percentile CDF curve, the tier thresholds, the SQLite schema and the JSONL
receipt shape are all unchanged, so ``internal/jury/jury.go``, the ``/jury``
surface and the R2 state sync keep working exactly as before.

Tier routing
------------
    masterpiece   percentile >= 98.0   -> masterpiece_vault.jsonl  + R2 sync
    spectacle     percentile >= 90.0   -> spectacle_genome.jsonl   + R2 sync
    standard      percentile 40.0-89.9 -> audit.jsonl only
    banal         percentile <  35.0   -> defect_blacklist.jsonl
    unscored      no judge answered    -> audit.jsonl only, NEVER any feed
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

import moj_evaluator

# --------------------------------------------------------------------------
# Logging.  Shared with moj_evaluator so the daemon and the jury render into
# one sink; falls back to plain prints when arcane_log is not installed.
# --------------------------------------------------------------------------
LOG = moj_evaluator.get_log()

# --------------------------------------------------------------------------
# Paths.  Resolved lazily through moj_evaluator (pipeline_paths -> env ->
# /root/Models/flux-output -> flux_paths -> ~), so importing this module on a
# machine with no /root neither raises nor creates anything.
# --------------------------------------------------------------------------
OUTPUT_DIR = moj_evaluator.output_dir()
AUDIT_LOG = os.path.join(OUTPUT_DIR, "audit.jsonl")
SQLITE_DB = os.path.join(OUTPUT_DIR, "jury.sqlite3")
CONFIG_JSON = os.path.join(OUTPUT_DIR, "jury_config.json")
SPECTACLE_LOG = os.path.join(OUTPUT_DIR, "spectacle_genome.jsonl")
MASTERPIECE_LOG = os.path.join(OUTPUT_DIR, "masterpiece_vault.jsonl")
DEFECT_LOG = os.path.join(OUTPUT_DIR, "defect_blacklist.jsonl")
JOBS_LEDGER = moj_evaluator.jobs_ledger_path()

POLL_INTERVAL_S = float(os.environ.get("JURY_POLL_INTERVAL_S") or 1.5)
LEDGER_TAIL = int(os.environ.get("JURY_LEDGER_TAIL") or 30)

#: Percentile below which a frame is filed as a defect, preserved from the
#: original evaluator.
DEFECT_PERCENTILE = 35.0


def _ensure_output_dir():
    """Create the output directory on first write, never at import time."""
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        return True
    except Exception as exc:
        LOG.error("cannot create output dir %s: %s" % (OUTPUT_DIR, exc))
        return False


# --------------------------------------------------------------------------
# Cloudflare R2 streaming (unchanged)
# --------------------------------------------------------------------------


def stream_image_to_r2_async(img_path):
    """Pushes every settled artwork directly to Cloudflare R2 on render completion."""
    if not img_path or not os.path.exists(img_path):
        return

    def _upload():
        try:
            fname = os.path.basename(img_path)
            r2_key = "outputs/%s" % fname
            subprocess.run(
                ["gemstone", "r2", "push", img_path, r2_key],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            LOG.info("R2 stream: artwork preserved to %s" % r2_key)
        except Exception as exc:
            LOG.warn("R2 stream failed for %s: %s" % (img_path, exc))

    threading.Thread(target=_upload, daemon=True).start()


def sync_state_to_r2_async():
    """Pushes active SQLite database & Spectacle genome to Cloudflare R2."""

    def _sync():
        for local, remote in (
            (SQLITE_DB, "state/jury.sqlite3"),
            (SPECTACLE_LOG, "outputs/spectacle_genome.jsonl"),
            (MASTERPIECE_LOG, "outputs/masterpiece_vault.jsonl"),
        ):
            try:
                if os.path.exists(local):
                    subprocess.run(
                        ["gemstone", "r2", "push", local, remote],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=20,
                    )
            except Exception:
                pass

    threading.Thread(target=_sync, daemon=True).start()


# --------------------------------------------------------------------------
# Configuration (unchanged contract with internal/jury/jury.go)
# --------------------------------------------------------------------------


def load_active_config():
    """Read the active jury config the Go server writes.

    Prefers ``jury_config.json`` (exported by ``jury.ExportConfigJSON``), falls
    back to the ``jury_config`` SQLite row, then to jury.go's ``DefaultConfig()``
    values so the two stay in step.
    """
    try:
        if os.path.exists(CONFIG_JSON):
            with open(CONFIG_JSON, "r") as handle:
                cfg = json.load(handle)
            if isinstance(cfg, dict):
                return cfg
    except Exception:
        pass

    con = None
    try:
        con = sqlite3.connect(SQLITE_DB)
        cur = con.cursor()
        cur.execute(
            "SELECT mode, order_json, weights_json, strictness_json, "
            "adversarial_mode FROM jury_config WHERE id = 'active'"
        )
        row = cur.fetchone()
        if row:
            return {
                "mode": row[0],
                "order": json.loads(row[1]),
                "weights": json.loads(row[2]),
                "strictness": json.loads(row[3])
                if row[3]
                else {"pixtral": 2.0, "qwen": 1.2, "decoder": 1.5, "governor": 2.2},
                "adversarial_mode": bool(row[4]),
            }
    except Exception:
        pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    return {
        "mode": "parallel",
        "order": ["pixtral", "qwen", "decoder", "governor"],
        "weights": {"pixtral": 0.35, "qwen": 0.35, "decoder": 0.15, "governor": 0.15},
        "strictness": {"pixtral": 2.0, "qwen": 1.2, "decoder": 1.5, "governor": 2.2},
        "adversarial_mode": True,
    }


# --------------------------------------------------------------------------
# Compatibility re-exports.  These moved into moj_evaluator; the names stay so
# nothing that imported them from here breaks.
# --------------------------------------------------------------------------
compute_percentile_and_curved_score = moj_evaluator.compute_percentile_and_curved_score
calibrate_raw_score = moj_evaluator.calibrate_raw_score


def find_image_for_job(job):
    """Locate the settled render for a job (path only, for callers that want it)."""
    path, _source = moj_evaluator.find_image_for_job(job, OUTPUT_DIR)
    return path


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def _append_jsonl(path, record):
    try:
        with open(path, "a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        return True
    except Exception as exc:
        LOG.error("cannot append to %s: %s" % (path, exc))
        return False


def _critiques_json(receipt):
    """Per-seat critique blob for the SQLite ``critiques_json`` column.

    Keyed by the legacy seat names the Go server and the /jury surface use, so
    the column keeps its shape.  A silent judge records why it was silent
    instead of a score-shaped sentence.
    """
    critiques = {}
    for judge in receipt.get("judges") or []:
        seat = judge.get("legacy_key") or judge.get("role")
        if not seat:
            continue
        if judge.get("degraded"):
            critiques[seat] = "DEGRADED (%s): %s" % (
                judge.get("model") or "?",
                judge.get("error") or "no reason recorded",
            )
        else:
            critiques[seat] = "%s %s/100 (gamma=%s) - %s" % (
                judge.get("title") or judge.get("role"),
                judge.get("score"),
                judge.get("gamma"),
                judge.get("critique") or "",
            )

    uniq = receipt.get("uniqueness") or {}
    if uniq.get("available"):
        critiques["uniqueness"] = "Novelty: %s%% (%s)" % (
            uniq.get("score"),
            uniq.get("category"),
        )
    else:
        critiques["uniqueness"] = "Novelty: unavailable"

    pct = receipt.get("percentile_rank")
    if pct is None:
        critiques["percentile"] = "UNSCORED - no judge answered; no percentile exists"
    else:
        critiques["percentile"] = "Top %.1f%% (%sth Percentile)" % (100.0 - pct, pct)

    if receipt.get("epigram"):
        critiques["epigram"] = receipt["epigram"]
    return critiques


def persist_receipt(receipt):
    """Write one verdict everywhere it belongs, then route it by tier."""
    _ensure_output_dir()

    tier = receipt.get("tier")
    percentile = receipt.get("percentile_rank")
    job_id = receipt.get("job_id")

    # 1. Real-time R2 streaming: push the image the instant it settles.
    image_path = receipt.get("image_path")
    if image_path and os.path.exists(image_path):
        stream_image_to_r2_async(image_path)

    # 2. Full audit trail. Unscored frames are recorded here too -- the absence
    #    of a verdict is itself part of the record.
    _append_jsonl(AUDIT_LOG, receipt)

    # 3. Tier routing. An unscored frame reaches NO feed: it did not earn a
    #    promotion and it did not earn a demotion, because nobody judged it.
    if tier == "unscored":
        LOG.warn(
            "job %s recorded to audit.jsonl as UNSCORED; excluded from every "
            "tier feed and from the percentile CDF" % job_id
        )
    elif tier == "masterpiece":
        _append_jsonl(MASTERPIECE_LOG, receipt)
        sync_state_to_r2_async()
    elif tier == "spectacle":
        _append_jsonl(
            SPECTACLE_LOG,
            {
                "ts": time.time(),
                "job_id": job_id,
                "prompt": receipt.get("prompt"),
                "seed": receipt.get("seed"),
                "percentile": percentile,
                "curved_score": receipt.get("curved_score"),
                "uniqueness": (receipt.get("uniqueness") or {}).get("score"),
                "epigram": receipt.get("epigram"),
                "target": "movement_towards_master",
            },
        )
        sync_state_to_r2_async()
    elif percentile is not None and percentile < DEFECT_PERCENTILE:
        worst = ""
        for judge in receipt.get("judges") or []:
            observed = (judge.get("observations") or {}).get("worst_defect")
            if observed:
                worst = str(observed)
                break
        _append_jsonl(
            DEFECT_LOG,
            {
                "ts": time.time(),
                "job_id": job_id,
                "prompt_snippet": str(receipt.get("prompt") or "")[:80],
                "reason": "Low percentile under the visual jury",
                "worst_defect": worst,
                "percentile": percentile,
                "score": receipt.get("curved_score"),
            },
        )

    # 4. SQLite. Schema untouched; an unscored frame stores NULL scores, which
    #    both GetSpectacles() and the percentile CDF query already skip.
    con = None
    try:
        con = sqlite3.connect(SQLITE_DB)
        with con:
            # The Go server owns this schema (internal/jury/jury.go:InitDB).
            # Recreating it identically here is idempotent and means the daemon
            # can come up before `flux serve` without dropping verdicts.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS jury_verdicts (
                    job_id TEXT PRIMARY KEY,
                    seed TEXT,
                    prompt TEXT,
                    composite_score REAL,
                    scores_json TEXT,
                    critiques_json TEXT,
                    mode TEXT,
                    masterpiece INTEGER,
                    created_at INTEGER NOT NULL
                );
                """
            )
            for column in ("raw_score REAL", "percentile_rank REAL"):
                try:
                    con.execute("ALTER TABLE jury_verdicts ADD COLUMN %s;" % column)
                except Exception:
                    pass
            con.execute(
                """
                INSERT OR REPLACE INTO jury_verdicts
                (job_id, seed, prompt, composite_score, raw_score, percentile_rank,
                 scores_json, critiques_json, mode, masterpiece, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(receipt.get("seed")),
                    receipt.get("prompt"),
                    receipt.get("curved_score"),
                    receipt.get("raw_composite"),
                    percentile,
                    json.dumps(receipt.get("jury_scores") or {}, default=str),
                    json.dumps(_critiques_json(receipt), default=str),
                    receipt.get("mode"),
                    1
                    if receipt.get("is_masterpiece")
                    else (2 if receipt.get("is_spectacle") else 0),
                    int(receipt.get("ts") or time.time()),
                ),
            )
    except Exception as exc:
        LOG.error("cannot persist job %s to SQLite: %s" % (job_id, exc))
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    return receipt


def score_frame(job, cfg=None):
    """Judge one settled render and persist the verdict.

    The name is preserved for compatibility; the fake-hash body is gone.  All
    scoring now happens in :func:`moj_evaluator.evaluate`.
    """
    receipt = moj_evaluator.evaluate(job, cfg if cfg is not None else load_active_config())
    return persist_receipt(receipt)


# --------------------------------------------------------------------------
# Daemon loop
# --------------------------------------------------------------------------


def _read_ledger(tail=None):
    """Parse the last ``tail`` records of the jobs ledger.  Never raises."""
    jobs = []
    try:
        if not os.path.exists(JOBS_LEDGER):
            return jobs
        with open(JOBS_LEDGER, "r") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        if tail:
            lines = lines[-tail:]
        for line in lines:
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict):
                jobs.append(record)
    except Exception as exc:
        LOG.warn("cannot read jobs ledger %s: %s" % (JOBS_LEDGER, exc))
    return jobs


def _banner():
    LOG.info("Sovereign Visual Jury online - real VLM jury, no fallback scores")
    LOG.info("  ledger     : %s" % JOBS_LEDGER)
    LOG.info("  output dir : %s" % OUTPUT_DIR)
    LOG.info("  evaluator  : %s %s" % (moj_evaluator.EVALUATOR_NAME, moj_evaluator.EVALUATOR_VERSION))
    runtime = moj_evaluator.load_runtime_config(load_active_config())
    for spec in moj_evaluator.JUDGES:
        entry = moj_evaluator.endpoint_for(spec, runtime)
        LOG.info(
            "  judge %-10s %-16s %-40s %s"
            % (spec.role, entry.get("model"), entry.get("hf_model"), entry.get("base_url"))
        )
    LOG.event(
        "jury.daemon.start",
        ledger=JOBS_LEDGER,
        output_dir=OUTPUT_DIR,
        evaluator_version=moj_evaluator.EVALUATOR_VERSION,
        judges=[s.role for s in moj_evaluator.JUDGES],
    )


def main():
    _banner()

    # Everything already in the ledger at startup is treated as seen, so a
    # restart does not re-judge history.
    seen = set()
    for record in _read_ledger():
        if record.get("id"):
            seen.add(record["id"])
    LOG.info("watching for new settled renders (%d already in the ledger)" % len(seen))

    while True:
        try:
            cfg = load_active_config()
            for job in _read_ledger(LEDGER_TAIL):
                if job.get("status") != "done":
                    continue
                job_id = job.get("id")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                try:
                    score_frame(job, cfg)
                except Exception as exc:
                    # A single bad frame must never take the daemon down.
                    LOG.error("job %s failed to evaluate: %r" % (job_id, exc))
                    LOG.event("jury.error", job_id=job_id, error=repr(exc))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            LOG.error("jury loop iteration failed: %r" % (exc,))
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOG.info("jury evaluator stopped")
        sys.exit(0)
