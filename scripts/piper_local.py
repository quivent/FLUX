#!/usr/bin/env python3
"""Minimal local asset broker speaking the Piper wire protocol.

FLUX expects `piper-runtime.service` to own $PIPER_SOCKET and relay rendered
cells from the workers to the HTTP server, which fans them out over
/api/assets/events so the Motion Atlas page updates without a refresh:

    worker.py --asset.publish--> piper <--asset.subscribe-- flux serve --SSE--> page

Without a broker the workers' publishes fail silently (three retries, then the
cell is simply never announced) and the page shows job progress with no assets.
This is a stand-in for local work, not a replacement for the real runtime: it
keeps no durable log, does not replay history to new subscribers, and does no
authentication.

Protocol, per scripts/motion_probe.py and internal/server/server.go:
  {"type":"health"}                          -> {"ok":true,"status":"healthy"}
  {"type":"asset.subscribe","consumer":name} -> {"ok":true,"status":"subscribed"}
                                                then newline JSON events forever
  {"type":"asset.publish","job_id":..,"asset":{..}}
                                             -> {"ok":true,"status":"published"}
Broadcast event shape must satisfy both consumers: motion_probe matches on
"asset_id", the Go hub requires event=="ASSET_READY" and an asset.access_url
under /outputs/.
"""
import json
import os
import pathlib
import queue
import signal
import socket
import sys
import threading
import time

SOCKET_PATH = os.environ.get("PIPER_SOCKET", "/tmp/piper.sock")
BACKLOG = 128

_lock = threading.Lock()
_subscribers = {}          # queue -> consumer name
_recent = []               # small replay buffer for late subscribers
_stats = {"published": 0, "subscribers": 0, "started": time.time()}


def _broadcast(event):
    with _lock:
        _recent.append(event)
        if len(_recent) > BACKLOG:
            del _recent[: len(_recent) - BACKLOG]
        targets = list(_subscribers.keys())
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            # A wedged subscriber must never stall a render.
            pass


def _serve_subscriber(conn, wfile, consumer):
    q = queue.Queue(maxsize=1024)
    with _lock:
        _subscribers[q] = consumer
        _stats["subscribers"] = len(_subscribers)
    try:
        # No backlog replay on subscribe. A subscriber's next line must be the
        # next published event: scripts/motion_probe.py subscribes, publishes,
        # then reads exactly one line and matches asset_id. Replaying history
        # here would hand it a stale event and fail the flow check. The server
        # keeps its own recent buffer (motionAssetHub) for late SSE clients,
        # which is where replay belongs.
        while True:
            event = q.get()
            wfile.write(json.dumps(event, separators=(",", ":")) + "\n")
            wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
        pass
    finally:
        with _lock:
            _subscribers.pop(q, None)
            _stats["subscribers"] = len(_subscribers)
        try:
            conn.close()
        except OSError:
            pass


def _handle(conn):
    try:
        conn.settimeout(None)
        rfile = conn.makefile("r", encoding="utf-8")
        wfile = conn.makefile("w", encoding="utf-8")
        line = rfile.readline()
        if not line.strip():
            conn.close()
            return
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            wfile.write(json.dumps({"ok": False, "error": "invalid JSON"}) + "\n")
            wfile.flush()
            conn.close()
            return

        kind = str(req.get("type") or "")

        if kind == "health":
            with _lock:
                body = {"ok": True, "status": "healthy", "published": _stats["published"],
                        "subscribers": _stats["subscribers"],
                        "uptime_seconds": round(time.time() - _stats["started"], 1)}
            wfile.write(json.dumps(body) + "\n")
            wfile.flush()
            conn.close()
            return

        if kind == "asset.subscribe":
            consumer = str(req.get("consumer") or "anonymous")
            wfile.write(json.dumps({"ok": True, "status": "subscribed", "consumer": consumer}) + "\n")
            wfile.flush()
            _serve_subscriber(conn, wfile, consumer)   # holds the connection open
            return

        if kind == "asset.publish":
            asset = req.get("asset") if isinstance(req.get("asset"), dict) else {}
            job_id = str(req.get("job_id") or asset.get("job_id") or "")
            event = {
                "event": "ASSET_READY",
                "asset_id": asset.get("id"),
                "job_id": job_id,
                "asset": asset,
                "ts": time.time(),
            }
            with _lock:
                _stats["published"] += 1
            _broadcast(event)
            wfile.write(json.dumps({"ok": True, "status": "published",
                                    "asset_id": asset.get("id")}) + "\n")
            wfile.flush()
            conn.close()
            return

        wfile.write(json.dumps({"ok": False, "error": f"unknown type {kind!r}"}) + "\n")
        wfile.flush()
        conn.close()
    except (BrokenPipeError, ConnectionResetError, OSError):
        try:
            conn.close()
        except OSError:
            pass


def main():
    path = pathlib.Path(SOCKET_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A stale socket file from a previous run would block bind().
    if path.exists():
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1)
            probe.connect(str(path))
            probe.close()
            print(f"piper already listening on {path}", file=sys.stderr)
            return 1
        except OSError:
            path.unlink()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    os.chmod(str(path), 0o660)
    srv.listen(64)

    # SIGTERM bypasses the finally block, leaving a stale socket file that
    # blocks the next bind. Handle it so restarts are clean.
    def _bye(_sig, _frm):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    print(f"piper_local listening on {path}", flush=True)
    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
