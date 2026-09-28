"""Stream the Ornith sessions to R2 as they change, flushed at most once per PERIOD.

Sources (R2 under s3://models/j-a-a-a-y/sessions/<name>/, additive: nothing is deleted remotely):
  ornith-chat   ~/ornith-eval/live_concept_capture   the original chat: transcript, ratings, residuals
  studio        ~/ornith-session                     the working session: journal, edits, steers, captures, anchor
  lab           ~/ornith-lab                         thermometer replays, signals, calibration

Event-driven: `inotifywait -m -r` marks a source dirty; a flush runs PERIOD seconds after the previous one, only when
something changed. Each flush syncs the dirty sources, then counts files local vs R2.

Credentials never touch disk: the daemon blocks on the FIFO /run/sessions-sync/creds until R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY and R2_ENDPOINT arrive as KEY=VALUE lines (sent from the Mac over ssh stdin), and keeps them in
memory. After a restart it waits again; status says so.
Status: ~/tracker/sessions_sync.json.
"""
import json, os, stat, subprocess, threading, time
from pathlib import Path

H = Path("/home/ubuntu")
DEST = "s3://models/j-a-a-a-y/sessions"
SOURCES = {
    "ornith-chat": H / "ornith-eval" / "live_concept_capture",
    "studio": H / "ornith-session",
    "lab": H / "ornith-lab",
    "beauty-collections": H / "Models" / "flux-output" / "collections",
    "influx-pictures": H / "Models" / "flux-output" / "influx-outputs",
}
DESTS = {"beauty-collections": "s3://models/j-a-a-a-y/beauty/collections",
         "influx-pictures": "s3://governor/outputs"}          # influx.pictures lists governor/outputs/ (album from the protocol-<album>-stream- name)   # everything else goes under DEST/<name>
EXCLUDE = ["*/__pycache__/*", "*.sock", "*.tmp", "access_token", "*/access_token", "home/*", "pydeps/*"]
SKIP_DIRS = {"home", "pydeps", "__pycache__"}
PERIOD = 300                                  # collections change every frame: back up every 5 minutes, not every minute
FIFO = Path("/run/sessions-sync/creds")
STATUS = H / "tracker" / "sessions_sync.json"
AWS = "/home/ubuntu/.local/bin/aws"

lock = threading.Condition()
dirty, env = set(SOURCES), None     # everything is dirty at start: the first flush is a full sync
state = {"armed": False, "period_s": PERIOD, "dest": DEST, "last_flush": None, "sources": {}, "pending": []}


def write_status():
    state["pending"] = sorted(dirty)
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.chown(tmp, 1000, 1000)
    os.replace(tmp, STATUS)


def arm():
    """Block until the credentials arrive on the FIFO; keep them only in this process."""
    global env
    FIFO.parent.mkdir(mode=0o700, exist_ok=True)
    if FIFO.exists() and not stat.S_ISFIFO(FIFO.stat().st_mode):
        FIFO.unlink()
    if not FIFO.exists():
        os.mkfifo(FIFO, 0o600)
    while True:
        with open(FIFO) as f:
            kv = dict(l.strip().split("=", 1) for l in f if "=" in l)
        kv = {k.removeprefix("export ").strip(): v.strip().strip('"').strip("'") for k, v in kv.items()}
        if all(kv.get(k) for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT")):
            env = {**os.environ, "AWS_ACCESS_KEY_ID": kv["R2_ACCESS_KEY_ID"], "AWS_SECRET_ACCESS_KEY": kv["R2_SECRET_ACCESS_KEY"],
                   "AWS_DEFAULT_REGION": "auto", "R2_ENDPOINT": kv["R2_ENDPOINT"],
                   "PYTHONPATH": next(str(p) for p in (H / ".local" / "lib").glob("python3*/site-packages"))}
            return


def local_count(src):
    n = b = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        if rel.parts and rel.parts[0] in SKIP_DIRS or "__pycache__" in rel.parts:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS or rel.parts]
        for f in files:
            if f == "access_token" or f.endswith((".sock", ".tmp")):
                continue
            p = Path(root) / f
            if p.is_file() and not p.is_symlink():
                n += 1
                b += p.stat().st_size
    return n, b


def remote_count(name):
    out = subprocess.run([AWS, "s3", "ls", DESTS.get(name, f"{DEST}/{name}") + "/", "--recursive", "--endpoint-url", env["R2_ENDPOINT"]],
                         env=env, capture_output=True, text=True, timeout=300).stdout
    rows = [l.split(None, 3) for l in out.splitlines() if l.strip()]
    return len(rows), sum(int(r[2]) for r in rows if len(r) >= 3 and r[2].isdigit())


def flush(names):
    for name in names:
        src = SOURCES[name]
        t0 = time.time()
        args = [AWS, "s3", "sync", str(src), DESTS.get(name, f"{DEST}/{name}"), "--endpoint-url", env["R2_ENDPOINT"], "--only-show-errors"]
        for e in EXCLUDE:
            args += ["--exclude", e]
        r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3600)
        ln, lb = local_count(src)
        rn, rb = remote_count(name)
        state["sources"][name] = {"path": str(src), "local_files": ln, "local_bytes": lb, "r2_files": rn, "r2_bytes": rb,
                                  "ok": r.returncode == 0 and rn >= ln, "seconds": round(time.time() - t0, 1),
                                  "error": r.stderr.strip()[-400:] or None, "at": time.time()}
    state["last_flush"] = time.time()


def watcher():
    dirs = " ".join(str(p) for p in SOURCES.values() if p.exists())
    cmd = f"inotifywait -m -r -q -e close_write,moved_to,create,delete --exclude '(/ornith-session/(home|pydeps)/|__pycache__|\\.sock$)' --format '%w%f' {dirs}"
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, text=True)
    for line in proc.stdout:
        path = line.strip()
        for name, src in SOURCES.items():
            if path.startswith(str(src) + "/") or path == str(src):
                with lock:
                    if name not in dirty:
                        dirty.add(name)
                        write_status()
                    lock.notify()
    raise SystemExit("inotifywait ended")


def main():
    write_status()
    arm()
    state["armed"] = True
    write_status()
    threading.Thread(target=watcher, daemon=True).start()
    last = 0.0
    while True:
        with lock:
            while not dirty:
                lock.wait()                          # sleeps until a file event; no polling
            wait = last + PERIOD - time.time()
        if wait > 0:
            time.sleep(wait)                         # periodicity: at most one flush per PERIOD
        with lock:
            names = sorted(dirty)
            dirty.clear()
        try:
            flush(names)
        except Exception as e:                       # a failed flush stays pending
            with lock:
                dirty.update(names)
            state["error"] = f"{type(e).__name__}: {e}"[-400:]
        else:
            state.pop("error", None)
        last = time.time()
        with lock:
            write_status()


if __name__ == "__main__":
    main()
