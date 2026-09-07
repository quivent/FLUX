#!/usr/bin/env python3
"""Turn jury evidence into the next render instruction for Relative Beauty."""
import json
import os
import secrets
import socket
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("BEAUTY_OUTPUT", "/home/dev/models/flux-output"))
COLLECTION = os.environ.get("BEAUTY_COLLECTION", "relative-beauty")
COLLECTION_DIR = ROOT / "collections" / COLLECTION
JURY_STATE = COLLECTION_DIR / "jury-runtime.json"
CONFIG = COLLECTION_DIR / "control.json"
STATE = COLLECTION_DIR / "worker-runtime.json"
HISTORY = COLLECTION_DIR / "evolution.jsonl"
SOCKET = os.environ.get("BEAUTY_FLUX_SOCKET", "/home/dev/FLUX/.fluxd/flux-gpu0.sock")
WORKER_URL = os.environ.get("BEAUTY_WORKER_URL", "http://127.0.0.1:8107/v1").rstrip("/")
WORKER_MODEL = os.environ.get("BEAUTY_WORKER_MODEL", "qwen38-worker-2")
ANCHOR = (
    "Create a singular image of beauty that feels genuinely new—as though this particular vision has never "
    "before been seen or imagined—yet whose beauty is immediate, coherent, and unmistakable. Let the image "
    "discover its own subject, form, material, scale, and visual language rather than relying on a predetermined "
    "symbol of beauty. It should reward sustained attention, reveal an internally necessary order, and remain "
    "identifiable as beautiful even while expanding what beauty might be."
)
DEFAULT_CONFIG = {
    "running": True,
    "prompt": ANCHOR,
    "width": 512,
    "height": 512,
    "steps": 18,
    "guidance": 3.5,
    "seed_policy": "random",
    "seed": 0,
    "worker_temperature": 0.35,
    "worker_max_tokens": 700,
}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def load(path, default):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else dict(default)
    except Exception:
        return dict(default)


def socket_request(payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(SOCKET)
        client.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty response from resident FLUX")
    return json.loads(data)


def worker_instruction(receipt, config, recent):
    uniqueness = receipt.get("uniqueness") or {}
    collection_direction = (
        "The collection is concentrated in a visually redundant region. Propose a materially different "
        "conception of beauty—new subject ontology, composition, and visual structure—while learning from "
        "the successful craft evidence in this receipt."
        if uniqueness.get("mode_collapse") else
        "Continue exploring from this result, balancing visual quality with a distinct contribution to the collection."
    )
    evidence = {
        "operator_anchor": config.get("prompt") or ANCHOR,
        "previous_render_instruction": receipt.get("prompt") or "",
        "jury_receipt": receipt,
        "recent_evolution": recent[-4:],
        "collection_direction": collection_direction,
    }
    system = (
        "You are the instruction worker in a relative-beauty evolution loop. "
        "Create the next concrete render instruction in pursuit of the operator's request for relative beauty. "
        "Use the actual jury evidence to retain what succeeded, improve what failed, and explore one clear "
        "visual change. Return strict JSON with keys revised_prompt, changed_axis, preserved, removed, "
        "rationale. revised_prompt must be a complete FLUX render instruction under 70 words. The operator "
        "anchor expresses the goal rather than prescribing a literal subject. Explain how the proposed image "
        "responds to the evidence while allowing genuinely new and surprising directions. Treat the supplied "
        "collection_direction as an affirmative search objective. Distinguish beauty of this frame from whether "
        "it contributes a new conception to the collection."
    )
    body = json.dumps({
        "model": WORKER_MODEL,
        "temperature": float(config.get("worker_temperature", .35)),
        "max_tokens": int(config.get("worker_max_tokens", 700)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ],
    }).encode()
    req = urllib.request.Request(
        WORKER_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        text = json.loads(response.read())["choices"][0]["message"]["content"]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("worker did not return a JSON directive")
    result = json.loads(text[start:end + 1])
    if not str(result.get("revised_prompt") or "").strip():
        raise RuntimeError("worker returned an empty revised_prompt")
    return result


def main():
    COLLECTION_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        atomic_json(CONFIG, DEFAULT_CONFIG)
    state = load(STATE, {"status": "starting", "source_verdict": ""})
    recent = []
    if HISTORY.exists():
        for line in HISTORY.read_text().splitlines()[-8:]:
            try:
                recent.append(json.loads(line))
            except Exception:
                pass
    while True:
        try:
            config = load(CONFIG, DEFAULT_CONFIG)
            if not config.get("running", True):
                state.update({"status": "paused", "updated_at": time.time()})
                atomic_json(STATE, state)
                time.sleep(1)
                continue
            jury = load(JURY_STATE, {})
            source = jury.get("last_job_id")
            receipt = jury.get("last_receipt") or {}
            if not source or source == state.get("source_verdict"):
                state.update({"status": "waiting-for-verdict", "updated_at": time.time()})
                atomic_json(STATE, state)
                time.sleep(.5)
                continue
            active = [j for j in socket_request({"op": "jobs"}).get("jobs", [])
                      if j.get("status") in ("queued", "running")]
            if active:
                time.sleep(.25)
                continue
            state.update({"status": "revising-instruction", "source_verdict": source, "updated_at": time.time()})
            atomic_json(STATE, state)
            directive = worker_instruction(receipt, config, recent)
            seed = int(config.get("seed") or 0) if config.get("seed_policy") == "fixed" else secrets.randbelow(2**31 - 1)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            payload = {
                "op": "submit", "backend": "cuda", "prompt": directive["revised_prompt"],
                "width": int(config["width"]), "height": int(config["height"]),
                "steps": int(config["steps"]), "guidance": float(config["guidance"]),
                "seed": str(seed), "filename": f"collections/{COLLECTION}/evolution-{stamp}-seed-{seed}.png",
            }
            job = socket_request(payload).get("job") or {}
            event = {"ts": time.time(), "source_verdict": source, "directive": directive, "render": job}
            with HISTORY.open("a") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            recent.append(event)
            state.update({"status": "rendering", "last_job_id": job.get("id"), "last_directive": directive,
                          "last_parameters": payload, "updated_at": time.time(), "error": ""})
            atomic_json(STATE, state)
        except Exception as exc:
            state.update({"status": "error", "error": repr(exc), "updated_at": time.time()})
            atomic_json(STATE, state)
            time.sleep(2)
        time.sleep(.5)


if __name__ == "__main__":
    main()
