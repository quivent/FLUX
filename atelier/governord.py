#!/home/dev/venv/bin/python
"""governord: the Governor's own clock, so his voice stops needing a courier.

protocol.py gave him a wire and estate.py gave him a throttle, but both are
pulled by something else -- an operator running a command, or perpetual.py
reaching its consult_every cycle. So he speaks only when someone else decides he
should, and he is silent in exactly the situations where his judgement matters
most: the loop stalled, the operator asleep, the fitness curve flat for an hour.
This daemon is the missing clock. Every interval it assembles what he needs to
know, asks him, and carries out what he answers.

What it steers is deliberately wider than protocol.consult_and_apply:

  * protocol.VERBS        the pipeline's directives -- mutation vectors,
                          anchor policy, champions.
  * estate.ESTATE_VERBS   the throttle -- tempo, stall, ignite, VRAM.
  * tunables / direction  the live knobs, engine and creative. These landed
                          after protocol.py was written, and until now only the
                          control panel has ever turned them. Published from
                          here they reach the running worker in ~10ms over
                          SIGUSR1, so his instruction changes the next picture
                          rather than the next restart.

The reply channel is protocol.consult(), unchanged -- his system prompt there
lists only the pipeline verbs, so the extra contract rides in the packet itself
and the extra parsing happens here, on the raw text. Reinventing the wire to
widen the vocabulary would have given him two endpoints with two prompts and no
single audit trail.

Three rules keep an autonomous voice from being a dangerous one:

  * NOTHING ENDS THE ESTATE. Cosign-gated verbs (COLD_STORAGE, WAKE_UP) are
    recorded as proposed and skipped -- never executed, because a model on a
    180-second timer must not be the thing that stops the node. This daemon
    also never deletes a file; every write is an append or a whole-file
    replace of something it owns.
  * SILENCE IS NOT A SIGNAL. His endpoint 524s, 403s, and stalls for minutes.
    A failed tick is a logged fact, not an exception: nothing here raises past
    the tick boundary, and consecutive failures widen the interval up to a cap
    so an outage does not become a retry storm.
  * EVERY DECISION IS ON THE RECORD. governord.jsonl takes one line per tick --
    what he was told, what he said verbatim, what was done, and what was
    refused. The panel reads that file; it is the only claim this daemon makes
    about itself.

Deliberately NOT wired: estate.note_unreachable() and estate.due_cosigns().
The Dead Man's Switch counts the LOOP's unreachable cycles, and a second clock
incrementing the same counter would stall the pipeline on a schedule nobody
chose; due_cosigns() executes node-level actions, which is the one thing this
process is not allowed to do.
"""
import argparse
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

sys.path.insert(0, "/home/dev")

import direction as DIR  # noqa: E402
import estate as E  # noqa: E402
import protocol as P  # noqa: E402
import tunables as TU  # noqa: E402

HOME = pathlib.Path("/home/dev")
GOV = HOME / "governor"
LOG = GOV / "governord.jsonl"          # the audit trail the panel reads
STATE = GOV / "governord_state.json"   # the heartbeat, one whole-file replace per tick
PERPETUAL_STATE = HOME / "perpetual_state.json"
TASTE_STORE = HOME / "taste" / "anchors.json"

# The worker's identity, as argv rather than as a substring. See worker_pids().
WORKER_ARGV = ["/home/dev/venv/bin/python", "/home/dev/perpetual.py"]

RAW_LIMIT = 4000            # how much of his reply the audit line keeps
CONSULT_TIMEOUT = 300       # he has taken 120s on a good day; a short timeout
                            # would report him unreachable while he was thinking
BACKOFF_CAP = 900           # 15 minutes; long enough to ride out an outage,
                            # short enough that his return is noticed quickly

# Any cosign-gated estate verb is refused here, not just the two named ones:
# the gate is the property that matters, so a verb added later inherits it.
BLOCKED_VERBS = ({v for v, spec in E.ESTATE_VERBS.items() if spec.get("cosign")}
                 | {"COLD_STORAGE", "WAKE_UP"})

_STOP = False


def _stop(signum, frame):
    global _STOP
    _STOP = True


def _read(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return default


# ---------------------------------------------------------------- the worker


def _cmdline(pid):
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a for a in raw.decode("utf-8", "replace").split("\0") if a]


def worker_pids():
    """PIDs of the live Perpetual Sieve, decided by argv and not by text match.

    pgrep -f matches any command line CONTAINING the pattern, which includes the
    shell that was handed this same string, and any editor or grep sitting on the
    filename. A SIGUSR1 delivered to one of those is silently wrong, so pgrep
    only nominates candidates and /proc/<pid>/cmdline casts the vote.
    """
    try:
        out = subprocess.run(["pgrep", "-f", " ".join(WORKER_ARGV)],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = []
    for tok in out.split():
        if not tok.isdigit():
            continue
        pid = int(tok)
        if pid == os.getpid():
            continue
        if _cmdline(pid)[:2] == WORKER_ARGV:
            pids.append(pid)
    return pids


# ---------------------------------------------------------------- the packet


def taste_store():
    """The fitness store, read as JSON instead of through taste.load().

    taste.py imports torch at module scope and brings a CLIP tower with it. This
    daemon never embeds anything; paying for the model stack in a supervisor
    process would cost VRAM the worker is actually using.
    """
    return _read(TASTE_STORE, {"anchors": [], "repulsors": [], "updated": 0})


def build_packet():
    """What he is told each tick: the pipeline packet, the estate telemetry, and
    the live control surface -- he cannot ask for a knob he has not been shown.
    """
    ps = _read(PERPETUAL_STATE, {})
    store = taste_store()

    packet = P.state_packet(ps, store)
    packet["telemetry"] = E.telemetry(ps, store)

    tun, tun_ack = TU.desired(), TU.ack()
    dirn, dir_ack = DIR.desired(), DIR.ack()
    pids = worker_pids()
    packet["live_controls"] = {
        "worker_pids": pids,
        "worker_running": bool(pids),
        "tunables": {"rev": tun["rev"], "values": tun["values"],
                     "adopted_rev": tun_ack.get("rev")},
        "direction": {"rev": dirn["rev"], "adopted_rev": dir_ack.get("rev"),
                      "in_force": dir_ack.get("in_force")},
    }

    # The system prompt in protocol.py predates these layers and lists only the
    # pipeline verbs. Widening what he may say therefore has to travel in the
    # packet, where it is also a factual statement of what this daemon accepts.
    packet["reply_contract_extension"] = {
        "note": "Reply with ONE JSON object. Besides the pipeline verbs in the "
                "system prompt, this object may carry an estate verb and/or "
                "live knob edits, in any combination.",
        "estate_verbs": sorted(v for v in E.ESTATE_VERBS if v not in BLOCKED_VERBS),
        "refused_verbs": {"verbs": sorted(BLOCKED_VERBS),
                          "why": "node-level and cosign-gated; recorded as "
                                 "proposed and skipped, never auto-applied"},
        "tunables_fields": {k: [lo, hi] for k, (_c, lo, hi) in TU.FIELDS.items()},
        "direction_fields": sorted(DIRECTION_FIELDS),
        "adoption": "tunables/direction edits are signalled to the running "
                    "worker immediately; they take effect within ~10ms",
        "example": {"verb": "SHIFT_GEAR", "args": {"tempo": "SLOW"},
                    "tunables": {"steps": 24},
                    "direction": {"pin_vector": "golden"},
                    "rationale": "one or two sentences"},
    }
    return packet


def packet_summary(packet):
    """The compact shape of the packet that goes in the audit line. The full
    packet is already reconstructable from protocol's own trail; what matters
    here is the handful of numbers his answer was a response to."""
    t = packet.get("telemetry") or {}
    live = packet.get("live_controls") or {}
    return {
        "cycle": packet.get("cycle"),
        "champion_fitness": (packet.get("champion") or {}).get("fitness"),
        "fitness_trend": packet.get("fitness_trend_over_window"),
        "stagnation_cycles": t.get("stagnation_cycles"),
        "human_keeps": t.get("human_keeps_total"),
        "anchors_provisional": t.get("anchors_provisional"),
        "tempo": t.get("tempo"),
        "stalled": t.get("stalled"),
        "vram_pressure": (t.get("vram") or {}).get("pressure"),
        "burn_usd_per_hr": t.get("burn_rate_usd_per_hr"),
        "tunables_rev": (live.get("tunables") or {}).get("rev"),
        "direction_rev": (live.get("direction") or {}).get("rev"),
        "worker_pids": live.get("worker_pids"),
    }


# ---------------------------------------------------------------- his answer

DIRECTION_FIELDS = ("axes", "style", "vectors", "pin_vector", "pin_slot",
                    "kontext_steps", "kontext_guidance", "negative")


def extract_json(text):
    """First balanced JSON object in his reply.

    protocol.parse_directive does the same scan but discards anything whose verb
    is not a pipeline verb -- which is precisely the class of reply this daemon
    exists to act on, so the scan is repeated here without that filter.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return d if isinstance(d, dict) else None
    return None


def _clean_tunables(raw):
    """Keep only fields tunables.py owns and values it can cast. publish() clamps
    the range; what it cannot survive is a string where a number belongs."""
    out = {}
    for k, v in (raw or {}).items():
        if k not in TU.FIELDS or v is None or isinstance(v, bool):
            continue
        try:
            out[k] = TU.clamp(k, v)
        except (TypeError, ValueError):
            continue
    return out


def _clean_direction(raw):
    """direction.py has no bounds table of its own, so the typing happens here.
    A malformed axes dict pushed onto the live collection module would poison
    every prompt until someone noticed."""
    out = {}
    for k, v in (raw or {}).items():
        if k not in DIRECTION_FIELDS:
            continue
        if k == "axes" and isinstance(v, dict):
            axes = {str(a): str(b) for a, b in v.items() if isinstance(b, str)}
            if axes:
                out["axes"] = axes
        elif k in ("style", "negative") and isinstance(v, str):
            out[k] = v
        elif k in ("pin_vector", "pin_slot") and (v is None or isinstance(v, str)):
            out[k] = v or None          # empty string means "release the pin"
        elif k == "kontext_steps":
            try:
                out[k] = max(1, min(80, int(v)))
            except (TypeError, ValueError):
                pass
        elif k == "kontext_guidance":
            try:
                out[k] = max(0.0, min(20.0, float(v)))
            except (TypeError, ValueError):
                pass
        elif k == "vectors" and isinstance(v, list):
            vecs = [{"name": str(x["name"]), "text": str(x["text"]),
                     "enabled": bool(x.get("enabled", True))}
                    for x in v
                    if isinstance(x, dict) and x.get("name") and x.get("text")]
            if vecs:
                out["vectors"] = vecs
    return out


def interpret(reply):
    """Turn his raw text into the action this daemon will take.

    He is asked for one object; he is not required to use every channel in it,
    and he sometimes puts a knob at the top level rather than in its section, so
    both placements are read. Anything unrecognised is commentary and changes
    nothing -- protocol.py's closed-verb rule, applied to the wider surface.
    """
    d = extract_json(reply)
    if d is None:
        return None
    tun = _clean_tunables(d.get("tunables") if isinstance(d.get("tunables"), dict) else {})
    dirn = _clean_direction(d.get("direction") if isinstance(d.get("direction"), dict) else {})
    top_tun = _clean_tunables({k: v for k, v in d.items() if k in TU.FIELDS})
    top_dir = _clean_direction({k: v for k, v in d.items() if k in DIRECTION_FIELDS})
    verb = d.get("verb")
    return {
        "verb": verb if isinstance(verb, str) else None,
        "args": d.get("args") if isinstance(d.get("args"), dict) else {},
        "tunables": {**top_tun, **tun},        # the explicit section wins
        "direction": {**top_dir, **dirn},
        "rationale": str(d.get("rationale", ""))[:600],
    }


# ---------------------------------------------------------------- execution


def _apply_verb(action, dry_run):
    verb, args = action["verb"], action["args"]
    if verb in BLOCKED_VERBS:
        return False, (f"{verb} PROPOSED AND SKIPPED: cosign-gated node action, "
                       f"never auto-applied by governord (args={args})")
    if verb in E.ESTATE_VERBS:
        # HALT is claimed by both layers. estate's is the one the running loop
        # actually reads (E.stalled()), so the throttle wins the name.
        if dry_run:
            return False, f"dry-run: would estate.execute({verb}, {args})"
        return E.execute(verb, args, source="governord")
    if verb in P.VERBS:
        if dry_run:
            return False, f"dry-run: would protocol.apply_directive({verb}, {args})"
        return P.apply_directive({"verb": verb, "args": args,
                                  "rationale": action["rationale"]})
    return False, f"unknown verb {verb!r}: recorded as commentary, nothing changed"


def apply_action(action, dry_run=False):
    """Carry out one interpreted reply. Returns (applied, note).

    Partial success is normal and is reported as such: he can move a knob in the
    same breath as proposing a verb this daemon refuses.
    """
    notes, applied = [], False

    if action["verb"]:
        ok, note = _apply_verb(action, dry_run)
        applied = applied or bool(ok)
        notes.append(note)

    published = False
    if action["tunables"]:
        if dry_run:
            notes.append(f"dry-run: would publish tunables {action['tunables']}")
        else:
            res = TU.publish(action["tunables"], by="governord")
            applied, published = True, True
            notes.append(f"tunables rev {res['rev']}: {', '.join(res['changed'])}")

    if action["direction"]:
        if dry_run:
            notes.append(f"dry-run: would publish direction {sorted(action['direction'])}")
        elif not DIR.desired()["rev"]:
            # publish() falls back to direction.defaults(), which imports
            # perpetual and collection -- the whole diffusers stack -- into this
            # process. The panel establishes the baseline; the daemon edits it.
            notes.append("direction skipped: no published baseline rev to merge onto")
        else:
            res = DIR.publish(action["direction"], by="governord")
            applied, published = True, True
            notes.append(f"direction rev {res['rev']}: {', '.join(res['changed'])}")

    if published and not dry_run:
        # One signal covers both channels: the worker's SIGUSR1 handler adopts
        # direction and tunables together.
        poked = TU.notify(worker_pids())
        notes.append(f"SIGUSR1 -> {poked or 'no live worker to notify'}")

    if not notes:
        notes.append("reply carried no verb and no knob; nothing to do")
    return applied, "; ".join(notes)


# ---------------------------------------------------------------- the record


def record(entry):
    """One line per tick, appended. This file is the daemon's only claim about
    itself, so it is written even when everything else failed."""
    try:
        GOV.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"[governord] could not write audit line: {e!r}", flush=True)


def heartbeat(state):
    """Whole-file replace through a temp file: the panel may be reading this at
    the moment we write it, and half a JSON object reads as a dead daemon."""
    try:
        GOV.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, STATE)
    except OSError as e:
        print(f"[governord] could not write heartbeat: {e!r}", flush=True)


# ---------------------------------------------------------------- the tick


def tick(state, dry_run=False):
    """One consultation. Never raises: a tick that blows up is a logged fact and
    the next one still happens."""
    entry = {"at": time.time(), "tick": state["ticks"] + 1, "packet_summary": None,
             "raw": None, "action": None, "applied": False, "note": "", "error": None}
    packet = None
    try:
        packet = build_packet()
        entry["packet_summary"] = packet_summary(packet)
    except Exception as e:
        entry["error"] = f"packet build failed: {type(e).__name__}: {e}"

    if packet is not None:
        try:
            d, raw, err = P.consult(packet, timeout=CONSULT_TIMEOUT)
            entry["raw"] = (raw or "")[:RAW_LIMIT] if raw else None
            if err:
                entry["error"] = err
                entry["note"] = "governor unreachable; nothing changed"
            else:
                action = interpret(raw)
                entry["action"] = action
                if action is None:
                    entry["note"] = "no JSON object in reply; treated as commentary"
                else:
                    entry["applied"], entry["note"] = apply_action(action, dry_run)
        except Exception as e:
            # Anything from the wire, the parser or an executor lands here rather
            # than killing the daemon. A supervisor that dies on his bad day is
            # worse than no supervisor.
            entry["error"] = f"{type(e).__name__}: {e}"
            entry["note"] = "tick aborted after exception; state unchanged"

    record(entry)

    # Mirror applied decisions into the estate trail so the existing panel and
    # protocol.audit readers see them without knowing this file exists.
    if entry["applied"]:
        try:
            E.audit("governord", {"action": entry["action"], "note": entry["note"]})
        except Exception:
            pass

    reachable = entry["error"] is None or entry["raw"] is not None
    state["ticks"] += 1
    state["last_tick"] = entry["at"]
    state["last_action"] = {
        "at": entry["at"],
        "verb": (entry["action"] or {}).get("verb"),
        "applied": entry["applied"],
        "note": entry["note"],
        "error": entry["error"],
    }
    if reachable and entry["error"] is None:
        state["last_success"] = entry["at"]
        state["consecutive_failures"] = 0
    else:
        state["consecutive_failures"] += 1
    heartbeat(state)
    return entry


def sleep_for(state, interval):
    """Back off while he is down, but never past the cap: the point of the cap is
    that his return is noticed in minutes, not hours."""
    fails = state["consecutive_failures"]
    if not fails:
        return interval
    return min(interval * (2 ** min(fails, 10)), BACKOFF_CAP)


def _wait(seconds):
    """Interruptible sleep, so SIGTERM ends the daemon now and not in 15 minutes."""
    deadline = time.time() + seconds
    while not _STOP and time.time() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.time())))


def main():
    ap = argparse.ArgumentParser(description="The Governor's own clock.")
    ap.add_argument("--interval", type=float, default=180.0,
                    help="seconds between consultations (default 180)")
    ap.add_argument("--once", action="store_true",
                    help="run a single tick and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="consult and log, but change nothing")
    a = ap.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    state = {"alive_since": time.time(), "last_tick": None, "last_success": None,
             "consecutive_failures": 0, "ticks": 0, "last_action": None,
             "pid": os.getpid(), "interval": a.interval, "dry_run": a.dry_run}
    heartbeat(state)

    while not _STOP:
        entry = tick(state, dry_run=a.dry_run)
        verb = (entry["action"] or {}).get("verb") or "-"
        print(f"[governord] tick {entry['tick']} verb={verb} "
              f"applied={entry['applied']} note={entry['note'] or '-'} "
              f"error={entry['error'] or '-'}", flush=True)
        if a.once:
            break
        wait = sleep_for(state, a.interval)
        if state["consecutive_failures"]:
            print(f"[governord] backing off {wait:.0f}s after "
                  f"{state['consecutive_failures']} failed tick(s)", flush=True)
        _wait(wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
