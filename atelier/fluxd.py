#!/home/dev/venv/bin/python
"""FLUX inference daemon: keeps the pipeline resident so a render costs only its
own steps, not a fresh ~60 s model load.

  POST /generate  {"prompt": "...", "steps": 28, "num": 1, ...}
                  -> {"images": [{path, seed, seconds, url}, ...]}
  POST /generate?return=png  -> the PNG bytes of the first image
  GET  /image/<name>.png     -> a rendered PNG
  GET  /health               -> {"ready": true, "model": ..., "vram_gib": ...}

One GPU, so generation is serialized behind a lock; requests queue rather than
thrashing VRAM.
"""
import asyncio
import pathlib

import torch
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import fluxlib

app = FastAPI(title="fluxd")
_lock = asyncio.Lock()


class EditReq(BaseModel):
    image: str                       # path on the node, or gen/key
    instruction: str
    steps: int = 28
    guidance: float = 2.5
    seed: int | None = None
    stem: str | None = None
    width: int | None = None
    height: int | None = None


class BatchItem(BaseModel):
    prompt: str
    seed: int | None = None
    stem: str | None = None


class BatchReq(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=16)
    steps: int | None = None
    guidance: float | None = None
    width: int = 1024
    height: int = 1024
    max_sequence_length: int = 512


class Req(BaseModel):
    prompt: str
    steps: int | None = None
    guidance: float | None = None
    width: int = 1024
    height: int = 1024
    seed: int | None = None
    num: int = Field(default=1, ge=1, le=16)
    negative: str | None = None
    stem: str | None = None
    max_sequence_length: int = 512


@app.on_event("startup")
def _warm():
    fluxlib.load()


@app.get("/health")
def health():
    return {
        "ready": fluxlib._pipe is not None,
        "model": fluxlib.MODEL_ID,
        "defaults": fluxlib.defaults(),
        "kontext_ready": fluxlib._kontext is not None,
        "vram_gib": round(torch.cuda.memory_allocated() / 2**30, 2),
        "vram_reserved_gib": round(torch.cuda.memory_reserved() / 2**30, 2),
        "vram_free_gib": round(torch.cuda.mem_get_info()[0] / 2**30, 2),
        "out_dir": str(fluxlib.OUT_DIR),
    }


@app.post("/generate")
async def generate(req: Req, return_: str = Query("json", alias="return")):
    async with _lock:
        results = await asyncio.to_thread(
            fluxlib.generate,
            req.prompt,
            steps=req.steps,
            guidance=req.guidance,
            width=req.width,
            height=req.height,
            seed=req.seed,
            num=req.num,
            negative_prompt=req.negative,
            max_sequence_length=req.max_sequence_length,
            stem=req.stem,
        )
    if return_ == "png":
        return FileResponse(results[0]["path"], media_type="image/png")
    for r in results:
        r["url"] = f"/image/{pathlib.Path(r['path']).name}"
    return JSONResponse({"images": results})


@app.post("/batch")
async def batch(req: BatchReq):
    """One forward pass for every item -- the whole collection in a single call."""
    async with _lock:
        results = await asyncio.to_thread(
            fluxlib.generate_batch,
            [i.model_dump() for i in req.items],
            steps=req.steps,
            guidance=req.guidance,
            width=req.width,
            height=req.height,
            max_sequence_length=req.max_sequence_length,
        )
    for r in results:
        r["url"] = f"/image/{pathlib.Path(r['path']).name}"
    return JSONResponse({"images": results})


@app.post("/edit")
async def edit(req: EditReq):
    """Kontext: change one thing about an existing image, keep the rest."""
    src = pathlib.Path(req.image)
    if not src.is_file():
        raise HTTPException(404, f"no such image: {req.image}")
    async with _lock:
        result = await asyncio.to_thread(
            fluxlib.edit,
            str(src),
            req.instruction,
            steps=req.steps,
            guidance=req.guidance,
            seed=req.seed,
            stem=req.stem,
            width=req.width,
            height=req.height,
        )
    result["url"] = f"/image/{pathlib.Path(result['path']).name}"
    return JSONResponse(result)


@app.get("/image/{name}")
def image(name: str):
    # Resolve inside OUT_DIR so a crafted name cannot walk out of it.
    path = (fluxlib.OUT_DIR / name).resolve()
    if fluxlib.OUT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "no such image")
    return FileResponse(path, media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
