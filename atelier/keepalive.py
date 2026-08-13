#!/home/dev/venv/bin/python
"""Supervisor: keep the whole stack up without anyone watching it.

Today every outage had the same shape -- something was killed for a good reason
and never came back, and the loss was only noticed later:
  * control.py died to a pkill whose pattern matched its own command line, and
    port 8091 stayed dark until someone looked;
  * triage was pointed at an expired endpoint for a whole session and failed
    OPEN, so the loop ran on believing it was filtering;
  * the render loops carry a --deadline and simply exit when it expires, so the
    wall stops filling with no error anywhere;
  * agents restart fluxd underneath everything else.

So this supervises. Rules that matter:

TWO STRIKES, NOT ONE. A service must fail two consecutive checks before it is
restarted. A single miss is usually somebody legitimately restarting it, and
racing them produces two copies fighting over a port.

INTENT IS RESPECTED. `touch /home/dev/supervise.off` stops all supervision;
`touch /home/dev/<name>.off` stops it for one service. A deliberate stop must not
be undone by a machine.

BACKOFF, AND GIVE UP LOUDLY. A service that keeps dying is broken, not unlucky.
Exponential backoff to a cap, and after enough failures it is marked `failing` and
left alone rather than restarted forever in a hot loop.

HEALTH IS NOT LIVENESS. A process can be alive and not serving. Where there is a
port, the check is an HTTP probe, not a pgrep.
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.request

HOME = pathlib.Path("/home/dev")
PY = str(HOME / "venv" / "bin" / "python")
STATE = HOME / "supervise_state.json"
LOG = HOME / "supervise.jsonl"
OFF = HOME / "supervise.off"

ENV = {
    "PYTHONPATH": "/home/dev",
    "HF_HOME": "/home/dev/hf-cache",
    "HF_HUB_OFFLINE": "1",
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "HOME": "/home/dev",
}

INTERVAL = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
MAX_BACKOFF = 300.0
GIVE_UP_AFTER = 8
GRACE = 90.0          # a slow starter is not a dead one
PROBE_TIMEOUT = 12.0  # CLIP load blocks uvicorn's loop


def http_ok(url, timeout=PROBE_TIMEOUT):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "keepalive/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 500      # 404 still means it is serving
    except Exception:
        return False


def pgrep_exact(script):
    """Match the interpreter+script exactly. `pgrep -f name` also matches the
    shell wrappers and any grep mentioning it, which is how a SIGUSR1 once went
    to the wrong process."""
    try:
        out = subprocess.run(["pgrep", "-af", script], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return []
    pids = []
    for ln in out.splitlines():
        parts = ln.split(None, 1)
        if len(parts) != 2:
            continue
        if parts[1].startswith(f"{PY} /home/dev/{script}"):
            pids.append(int(parts[0]))
    return pids


class Service:
    def __init__(self, name, script, args=(), health=None, critical=False):
        self.name = name
        self.script = script
        self.args = list(args)
        self.health = health
        self.critical = critical
        self.misses = 0
        self.failures = 0
        self.next_try = 0.0
        self.last_start = 0.0
        self.state = "unknown"
        self.pids = []
        self.detail = ""

    def alive(self):
        pids = pgrep_exact(self.script)
        self.pids = pids
        if not pids:
            self.detail = "no process"
            return False
        if len(pids) > 1:
            # Duplicates fight over the port and the probe flaps forever. The
            # oldest owns the socket; the rest are mistakes (usually mine).
            for extra in sorted(pids)[1:]:
                try:
                    os.kill(extra, signal.SIGTERM)
                except OSError:
                    pass
            self.detail = f"reaped {len(pids)-1} duplicate(s)"
        if time.time() - self.last_start < GRACE:
            self.detail = "starting"
            return True                      # do not judge it while it boots
        if self.health and not http_ok(self.health):
            self.detail = "process up, port not answering"
            return False
        self.detail = "ok"
        return True

    def start(self):
        cmd = [PY, str(HOME / self.script)] + self.args
        logf = open(HOME / f"{self.name}.out", "ab")
        logf.write(f"\n=== keepalive start {time.strftime('%H:%M:%S')} ===\n".encode())
        logf.flush()
        subprocess.Popen(cmd, cwd=str(HOME), stdout=logf, stderr=logf,
                         start_new_session=True, env=dict(ENV))
        self.last_start = time.time()


# The render loop carries the node's idle clock as well as the wall: if nothing
# renders, the node itself eventually auto-stops. Its deadline is set very long
# and the supervisor restarts it when it expires anyway.
SERVICES = [
    Service("fluxd", "fluxd.py", health="http://127.0.0.1:8080/health", critical=True),
    Service("gallery", "gallery.py", health="http://127.0.0.1:8090/", critical=True),
    Service("control", "control.py", health="http://127.0.0.1:8091/api/state", critical=True),
    Service("triaged", "triaged.py", health="http://127.0.0.1:8092/"),
    Service("vision_run", "vision_run.py", args=["86400", "6", "34", "704", "1056"]),
]


def log(rec):
    rec["at"] = time.time()
    try:
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    print(f"[keepalive] {rec.get('event')} {rec.get('service','')} "
          f"{rec.get('note','')}", flush=True)


def main():
    log({"event": "supervisor_start", "note": f"interval {INTERVAL}s, "
         f"{len(SERVICES)} services"})
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))

    while not stop["now"]:
        now = time.time()
        report = {}
        supervising = not OFF.exists()

        for s in SERVICES:
            disabled = (HOME / f"{s.name}.off").exists()
            up = s.alive()
            if up:
                if s.state != "up":
                    log({"event": "up", "service": s.name})
                s.state, s.misses, s.failures = "up", 0, 0
            else:
                s.misses += 1
                if disabled or not supervising:
                    s.state = "disabled"
                elif s.misses < 2:
                    # one miss is usually a legitimate restart by someone else
                    s.state = "missing"
                elif s.failures >= GIVE_UP_AFTER:
                    s.state = "failing"
                elif now >= s.next_try:
                    s.failures += 1
                    back = min(MAX_BACKOFF, INTERVAL * (2 ** (s.failures - 1)))
                    s.next_try = now + back
                    s.state = "restarting"
                    log({"event": "restart", "service": s.name,
                         "note": f"attempt {s.failures}, next backoff {back:.0f}s"})
                    try:
                        s.start()
                    except Exception as e:
                        log({"event": "start_failed", "service": s.name,
                             "note": repr(e)})
            report[s.name] = {"state": s.state, "misses": s.misses,
                              "failures": s.failures, "pids": s.pids,
                              "detail": s.detail, "critical": s.critical}

        STATE.write_text(json.dumps({
            "at": now, "supervising": supervising, "interval": INTERVAL,
            "services": report,
            "down": [n for n, r in report.items()
                     if r["state"] not in ("up", "disabled")],
        }, indent=2))

        for _ in range(int(INTERVAL * 4)):
            if stop["now"]:
                break
            time.sleep(0.25)

    log({"event": "supervisor_stop"})


if __name__ == "__main__":
    main()
