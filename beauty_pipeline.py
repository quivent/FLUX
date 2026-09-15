#!/usr/bin/env python3
"""Latency-first adaptive image loop for the Beauty Protocol.

One resident FLUX worker renders one 512px frame at a time. The finished frame
is published immediately. Prompt steering is tightly bounded: Pixtral gets
first refusal because it sees pixels, Qwen is the vision/text fallback, and
Gemma is the text-only fallback. Exactly one advisor is called per frame, and
dead endpoints cool down instead of taxing every render.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import signal
import socket
import time
import urllib.request
import jury_evaluator


ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE = os.path.join(ROOT, ".fluxd", "protocol_stream_gpu3.json")
DEFAULT_PID = os.path.join(ROOT, ".fluxd", "beauty_pipeline.pid")
DEFAULT_PROMPT = (
    "Editorial image of astonishing contemporary beauty, tactile material, "
    "off-centre composition, reserved highlights, one vivid accent, visible "
    "surface, specific light, no generic luxury photography"
)
LOCAL_DIRECTIONS = (
    "low viewpoint, asymmetrical negative space, mineral pigment and raw silk",
    "overhead crop, translucent paper, hard morning shaft and one vermilion accent",
    "intimate profile, oxidized metal, bruised violet dusk and imperfect edges",
    "wide environmental portrait, wet stone, pale cyan haze and tiny warm signal",
    "fragmented close detail, hand-dyed fiber, raking light and restrained ground",
    "architectural silhouette, chalky fresco surface, long shadow and acid-green note",
)


def atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def socket_request(path, payload, timeout=10.0):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(path)
        conn.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty worker response")
    reply = json.loads(data.decode())
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or "worker refused request")
    return reply


def trim_prompt(value, words=72):
    return " ".join(str(value or "").replace("\n", " ").split()[:words]).strip()


def parse_object(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines.pop()
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start : end + 1])
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


class Advisor:
    def __init__(self, name, url, vision, timeout, api_key=""):
        self.name = name
        self.url = url.rstrip("/")
        self.vision = vision
        self.timeout = timeout
        self.api_key = api_key
        self.model = ""
        self.retry_at = 0.0

    def headers(self):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def probe(self):
        if not self.url or time.monotonic() < self.retry_at:
            return False
        try:
            req = urllib.request.Request(self.url + "/models", headers=self.headers())
            with urllib.request.urlopen(req, timeout=min(1.2, self.timeout)) as response:
                data = json.loads(response.read().decode())
            models = data.get("data") or []
            self.model = str(models[0].get("id") or "") if models else ""
            if not self.model:
                raise RuntimeError("no served model")
            return True
        except Exception:
            self.retry_at = time.monotonic() + 30.0
            return False

    def next_prompt(self, image_path, prompt, frame):
        if not self.model and not self.probe():
            return None
        system = (
            "You are the fast visual director in a generative art loop. Preserve the "
            "subject intent but make the next frame visibly non-substitutable. Return "
            "one compact JSON object with keys critique and next_prompt. next_prompt "
            "must be concrete, visual, under 72 words, and contain no commentary."
        )
        user_text = "Frame %d used this prompt: %s" % (frame, prompt)
        content = [{"type": "text", "text": user_text}]
        if self.vision:
            with open(image_path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + encoded},
            })
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content if self.vision else user_text},
            ],
            "temperature": 0.65,
            "max_tokens": 180,
            "stream": False,
        }
        headers = self.headers()
        headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(
                self.url + "/chat/completions",
                data=json.dumps(body).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
            raw = payload["choices"][0]["message"]["content"]
            if isinstance(raw, list):
                raw = "".join(str(part.get("text") or "") for part in raw if isinstance(part, dict))
            answer = parse_object(raw)
            next_prompt = trim_prompt(answer.get("next_prompt"))
            if not next_prompt:
                raise RuntimeError("advisor omitted next_prompt")
            return {"prompt": next_prompt, "critique": str(answer.get("critique") or "")[:500]}
        except Exception as exc:
            self.retry_at = time.monotonic() + 30.0
            return {"error": "%s: %s" % (self.name, str(exc)[:180])}


def local_prompt(base, frame):
    direction = LOCAL_DIRECTIONS[(frame - 1) % len(LOCAL_DIRECTIONS)]
    return trim_prompt(base + ", " + direction)


def wait_for_worker(path, state, state_path, stop, timeout=240.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stop[0]:
        try:
            socket_request(path, {"op": "ping"}, timeout=1.0)
            return
        except Exception as exc:
            state.update(stage="waiting_for_worker", error=str(exc)[:180], updated_at=time.time())
            atomic_json(state_path, state)
            time.sleep(1.0)
    raise RuntimeError("FLUX worker did not become ready")


def wait_for_job(path, job_id, state, state_path, stop):
    while not stop[0]:
        jobs = socket_request(path, {"op": "jobs"}).get("jobs") or []
        job = next((item for item in jobs if item.get("id") == job_id), None)
        if not job:
            raise RuntimeError("worker lost job " + job_id)
        status = job.get("status")
        state.update(
            stage="rendering", running=1, job_id=job_id,
            render_phase=job.get("phase"), step=job.get("step", 0),
            total_steps=job.get("total_steps", state.get("steps")), updated_at=time.time(),
        )
        atomic_json(state_path, state)
        if status == "done":
            return job
        if status in ("error", "cancelled"):
            raise RuntimeError(job.get("error") or "render " + status)
        time.sleep(0.35)
    raise KeyboardInterrupt()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--n", type=int, default=0, help="frame cap; 0 runs continuously")
    parser.add_argument("--steps", type=int, default=18)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--guidance", type=float, default=3.5)
    parser.add_argument("--seed", default="", help="base seed; blank chooses a random seed")
    parser.add_argument("--socket", default=os.path.join(ROOT, ".fluxd", "flux.sock"))
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--pid", default=DEFAULT_PID)
    parser.add_argument("--lane", default="fashion")
    parser.add_argument("--advisor-timeout", type=float, default=4.0)
    args = parser.parse_args()

    stop = [False]
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))
    os.makedirs(os.path.dirname(args.pid), exist_ok=True)
    with open(args.pid, "w") as handle:
        handle.write(str(os.getpid()) + "\n")

    state = {
        "schema": "beauty.pipeline.v1", "id": time.strftime("beauty-%Y%m%d-%H%M%S"),
        "status": "running", "stage": "starting", "lane": args.lane,
        "n": max(0, args.n), "steps": args.steps, "width": args.width,
        "height": args.height, "depth": 1, "submitted": 0, "done": 0,
        "running": 0, "prompt": trim_prompt(args.prompt), "advisor": "local",
        "started_at": time.time(), "updated_at": time.time(), "error": "",
    }
    atomic_json(args.state, state)
    key = os.environ.get("BEAUTY_ADVISOR_API_KEY", "")
    advisors = (
        Advisor("pixtral", os.environ.get("BEAUTY_PIXTRAL_URL", "http://127.0.0.1:8004/v1"), True, args.advisor_timeout, key),
        Advisor("qwen", os.environ.get("BEAUTY_QWEN_URL", "https://qwen.oceanica.network/v1"), True, args.advisor_timeout, key),
        Advisor("gemma", os.environ.get("BEAUTY_GEMMA_URL", "https://governor.influx.vision/v1"), False, args.advisor_timeout, key),
    )

    try:
        wait_for_worker(args.socket, state, args.state, stop)
        base_prompt = trim_prompt(args.prompt)
        prompt = base_prompt
        try:
            base_seed = int(args.seed) if str(args.seed).strip() else None
        except ValueError:
            raise RuntimeError("seed must be an integer or blank")
        while not stop[0] and (args.n <= 0 or state["done"] < args.n):
            ordinal = state["submitted"] + 1
            seed = (base_seed + ordinal - 1) % 2_147_483_647 if base_seed is not None else random.SystemRandom().randrange(1, 2_147_483_647)
            filename = "beauty-fashion-%s-%04d.png" % (state["id"], ordinal)
            state.update(stage="submitting", prompt=prompt, seed=seed, running=0, updated_at=time.time())
            atomic_json(args.state, state)
            reply = socket_request(args.socket, {
                "op": "submit", "backend": "cuda", "prompt": prompt,
                "steps": args.steps, "guidance": args.guidance,
                "width": args.width, "height": args.height,
                "seed": str(seed), "filename": filename,
            })
            job = reply.get("job") or {}
            job_id = job.get("id")
            if not job_id:
                raise RuntimeError("worker accepted no job id")
            state["submitted"] += 1
            settled = wait_for_job(args.socket, job_id, state, args.state, stop)
            image_path = str(settled.get("output") or "")
            state.update(
                stage="published", running=0, done=state["done"] + 1,
                image=image_path, render_seconds=settled.get("seconds"),
                published_at=time.time(), error="", updated_at=time.time(),
            )
            atomic_json(args.state, state)

            # --- JURY: this is what makes it a loop. Score the published frame
            # and queue it for the operator eye-gate. Without this the pipeline
            # is a render firehose; with it, every frame enters EGRL Gate 2.
            # Degrades safely: if the judges are down, the frame is still
            # published and the loop continues — the receipt just records it.
            try:
                state.update(stage="judging", updated_at=time.time())
                atomic_json(args.state, state)
                jury_job = {
                    "id": job_id,
                    "job_id": job_id,
                    "prompt": prompt,
                    "seed": seed,
                    "output": image_path,
                    "image_path": image_path,
                    "lane": "fashion",
                    "ts": time.time(),
                }
                receipt = jury_evaluator.score_frame(jury_job)
                state["last_tier"] = (receipt or {}).get("tier")
                state["last_score"] = (receipt or {}).get("curved_score")
            except Exception as jury_exc:  # never let a judge stall the loop
                state["jury_error"] = str(jury_exc)[:300]
            atomic_json(args.state, state)

            directed = None
            for advisor in advisors:
                if advisor.probe():
                    state.update(stage="directing", advisor=advisor.name, updated_at=time.time())
                    atomic_json(args.state, state)
                    directed = advisor.next_prompt(image_path, prompt, state["done"])
                    if directed and directed.get("prompt"):
                        prompt = directed["prompt"]
                        state["critique"] = directed.get("critique", "")
                        state["advisor"] = advisor.name
                    else:
                        state["advisor_error"] = (directed or {}).get("error", "advisor unavailable")
                    break
            if not directed or not directed.get("prompt"):
                prompt = local_prompt(base_prompt, state["done"] + 1)
                state["advisor"] = "local"
            state.update(stage="ready", next_prompt=prompt, updated_at=time.time())
            atomic_json(args.state, state)

        state.update(status="stopped" if stop[0] else "done", stage="idle", running=0, updated_at=time.time())
    except KeyboardInterrupt:
        state.update(status="stopped", stage="idle", running=0, updated_at=time.time())
    except Exception as exc:
        state.update(status="error", stage="error", running=0, error=str(exc)[:500], updated_at=time.time())
        raise
    finally:
        atomic_json(args.state, state)
        try:
            os.remove(args.pid)
        except OSError:
            pass


if __name__ == "__main__":
    main()
