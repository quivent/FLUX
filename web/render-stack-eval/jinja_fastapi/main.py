"""Standalone Jinja2/FastAPI comparison service for motion-atlas.

Serves the real production web/motion-atlas directory exactly as the
production FLUX server does -- everything (pages, CSS, JS) under one shared
/motion-atlas/ prefix, since they're siblings in the same directory and
reference each other with relative paths. Side-by-side comparison harness
only, mirroring ~/Oscillihue/studio/ -- not wired into FLUX's production
server.

/events uses ONE shared 200ms asyncio task fanning out to every connection
via per-client queues, instead of each connection running its own
asyncio.sleep loop -- the same "one source, fan out to N" fix applied to
FLUX's real telemetry SSE endpoints (server.go), which had N independent
nvidia-smi subprocesses instead of one shared poller.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path("/Users/jay/FLUX/web/motion-atlas")

app = FastAPI(title="motion-atlas (Jinja2/FastAPI comparison)")

_subscribers: set[asyncio.Queue] = set()


async def _heartbeat_hub():
    seq = 0
    while True:
        await asyncio.sleep(0.2)
        seq += 1
        payload = json.dumps({"seq": seq, "ts": time.time_ns(), "event": "heartbeat"})
        for q in list(_subscribers):
            q.put_nowait(payload)


@app.on_event("startup")
async def _start_hub():
    asyncio.create_task(_heartbeat_hub())


@app.get("/")
def root():
    return RedirectResponse(url="/motion-atlas/")


app.mount("/motion-atlas", StaticFiles(directory=str(STATIC_DIR), html=True), name="motion-atlas")


@app.get("/api/health")
def api_health():
    return {"service": "jinja-fastapi-comparison", "status": "ok"}


@app.get("/events")
async def events(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subscribers.add(queue)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    return
                payload = await queue.get()
                yield f"data: {payload}\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
