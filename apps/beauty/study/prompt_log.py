"""Every finished render's prompt, kept forever: ~/Models/flux-output/collections/prompts.jsonl (streams to R2 with the collections).
Event-driven: inotify on the FLUX jobs ledger; appends each job once, when it finishes."""
import json, subprocess
from pathlib import Path

LEDGER = Path("/home/ubuntu/FLUX/.fluxd/jobs.jsonl")
LOG = Path("/home/ubuntu/Models/flux-output/collections/prompts.jsonl")


def logged():
    try:
        return {json.loads(l)["id"] for l in LOG.read_text().splitlines() if l.strip()}
    except Exception:
        return set()


def sweep(done_ids):
    latest = {}
    for line in LEDGER.read_text(errors="ignore").splitlines():
        try:
            j = json.loads(line)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("id"):
            latest[j["id"]] = {**latest.get(j["id"], {}), **j}
    new = [j for j in latest.values() if j.get("status") == "done" and j["id"] not in done_ids]
    with open(LOG, "a") as f:
        for j in sorted(new, key=lambda j: j.get("finished") or 0):
            f.write(json.dumps({k: j.get(k) for k in ("id", "output", "filename", "prompt", "seed", "width", "height", "steps",
                                                        "guidance", "created", "finished")}) + "\n")
            done_ids.add(j["id"])


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    done = logged()
    sweep(done)                                   # everything already in the ledger, once
    w = subprocess.Popen(["inotifywait", "-m", "-q", "-e", "close_write,moved_to", "--format", "%f", str(LEDGER.parent)],
                         stdout=subprocess.PIPE, text=True)
    for name in w.stdout:
        if name.strip() == LEDGER.name:
            sweep(done)


if __name__ == "__main__":
    main()
