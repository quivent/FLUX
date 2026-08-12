#!/usr/bin/env python3
"""Minimal local orchestration authority speaking the Nexus wire protocol.

FLUX gates every atlas submission on a verified Nexus receipt: server.go's
submitNexusReceipt dials NEXUS_ADDR (default 127.0.0.1:9999) and
nexusReceiptVerified refuses the job unless the reply carries
    ok && accepted && verified && status == "accepted"
    && job_id == <the job id> && receipt_id != ""
With nothing listening, /api/atlas answers 502 "NEXUS REJECTED JOB" and the web
UI's launch button cannot dispatch at all.

This is a stand-in for local work, not a replacement for nexus-runtime.service:
receipts live in memory only, nothing is durable, and no authority is verified.

Protocol:
  {"type":"health"}              -> {"ok":true,"status":"healthy",...}
  {"type":"submit","job":{...}}  -> verified receipt for job["id"]/["job_id"]
"""
import json
import os
import socket
import socketserver
import sys
import threading
import time
import uuid

HOST, PORT = "127.0.0.1", int(os.environ.get("NEXUS_PORT", "9999"))

_lock = threading.Lock()
_receipts = {}
_stats = {"submitted": 0, "started": time.time()}


class Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self):
        try:
            line = self.rfile.readline()
        except (OSError, socket.timeout):
            return
        if not line or not line.strip():
            return
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            self._send({"ok": False, "error": "invalid JSON"})
            return
        kind = str(req.get("type") or "")

        if kind == "health":
            with _lock:
                self._send({"ok": True, "status": "healthy",
                            "submitted": _stats["submitted"],
                            "receipts": len(_receipts),
                            "uptime_seconds": round(time.time() - _stats["started"], 1)})
            return

        if kind == "submit":
            job = req.get("job") if isinstance(req.get("job"), dict) else {}
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            if not job_id:
                self._send({"ok": False, "accepted": False, "verified": False,
                            "status": "rejected", "error": "job id required"})
                return
            with _lock:
                # Submission is idempotent by durable job identity. Recovery
                # paths may ask again, but Nexus must not mint a second
                # authority receipt for the same production.
                receipt = _receipts.get(job_id)
                if receipt is None:
                    receipt = {
                        "ok": True, "accepted": True, "verified": True,
                        "status": "accepted", "job_id": job_id,
                        "receipt_id": "rcpt_" + uuid.uuid4().hex[:16],
                        "kind": str(job.get("kind") or "flux.motion_atlas"),
                        "execution_owner": str(job.get("execution_owner") or "flux"),
                        "issued_at": time.time(),
                    }
                    _receipts[job_id] = receipt
                    _stats["submitted"] += 1
            self._send(receipt)
            return

        self._send({"ok": False, "error": f"unknown type {kind!r}"})

    def _send(self, body):
        try:
            self.wfile.write((json.dumps(body) + "\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    try:
        srv = Server((HOST, PORT), Handler)
    except OSError as exc:
        print(f"cannot bind {HOST}:{PORT}: {exc}", file=sys.stderr)
        return 1
    print(f"nexus_local listening on {HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
