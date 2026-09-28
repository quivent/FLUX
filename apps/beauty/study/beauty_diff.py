"""Scoring the diff between a collection's champion and a challenger: no judge's number, a signed profile.

Measured, through FLUX's own sensory gates (sensory_gates.py loads DINOv2-giant and SigLIP and reads the frames;
this module only asks it for embeddings):
  d_prev, d_champ   DINOv2 CLS cosine distance to the previous frame and to the champion
  subject           SigLIP image-text cosine with the fixed subject, champion and challenger
  directions        SigLIP cosine of the challenger with each of the teacher's directions
Judged, by the vision jurors with both frames in one request (the PNG files are sent as they were rendered):
  Qwen scores ten aspects for B (challenger) against A (champion), -3..+3, plus one short note: one call per frame.
See beauty.influx.vision/scoring.
"""
import base64, json, os, re, sys, urllib.request

os.environ.setdefault("SENSORY_DINOV2_MODEL", "/home/ubuntu/models/facebook/dinov2-giant")
os.environ.setdefault("SENSORY_SIGLIP_MODEL", "/home/ubuntu/models/google/siglip-base-patch16-224")
sys.path.insert(0, "/home/ubuntu/FLUX")
import sensory_gates as sg  # noqa: E402

JURORS = {   # seat -> (served model, endpoint, aspects). Pixtral is not in the diff: it saw no difference in 95 of 95 comparisons
             # and cost ~8 s a frame; it stays on the wall jury.
    # Qwen scores all ten: in practice Pixtral 12B answered "no difference" on both orderings for every aspect (95 of 95),
    # so its aspects also go to Qwen; Pixtral's non-zero answers are averaged in where they point the same way.
    # What "better" means is the teacher's direction: it leads and weighs 3x (WEIGHTS); beauty next; hands and defects are the only structure checks.
    "qwen": ("ornith-judge", "http://127.0.0.1:8006/v1/chat/completions",      # the seat is Ornith's now (Qwen removed from the study)
             ["beauty", "original", "change", "anatomy and hands", "defects"]),
}


def _embeddings(path, texts):
    """(DINOv2 CLS unit vector, [SigLIP cosine with each text]) via the gates' loaded models and helpers."""
    sg.warm(require_full=True)
    torch = sg._try_module("torch")
    smodel, sproc, _, _ = sg._state["siglip"]
    dmodel, dproc, dsize, dmean, dstd = sg._state["dinov2"]
    device, dtype = sg._state["device"], sg._state["dtype"]
    ssize, smean, sstd = sg._proc_geometry(sproc, 224)
    frame = sg._load_image(path)[0]
    with sg._lock, torch.inference_mode():
        iemb = sg._unit(sg._feat(smodel.get_image_features(pixel_values=sg._prep(frame, ssize, smean, sstd, torch, device, dtype))).float())
        dout = dmodel(pixel_values=sg._prep(frame, dsize, dmean, dstd, torch, device, dtype))
        cls = getattr(dout, "pooler_output", None)
        cls = sg._unit((cls if cls is not None else dout.last_hidden_state[:, 0]).float())[0]
    sims = [float((iemb @ sg._text_embedding(sg.BACKEND_SIGLIP, t).T).mean()) for t in texts]
    return cls, sims


def measure(champ, chall, prev, subject, directions):
    texts = [subject] + list(directions)
    c_cls, c_sims = _embeddings(champ, texts[:1])
    x_cls, x_sims = _embeddings(chall, texts)
    p_cls = _embeddings(prev, [])[0] if prev else None
    return {"d_prev": round(1 - float(x_cls @ p_cls), 4) if p_cls is not None else None,
            "d_champ": round(1 - float(x_cls @ c_cls), 4),
            "subject_champ": round(c_sims[0], 4), "subject_chall": round(x_sims[0], 4),
            "directions": {d: round(s, 4) for d, s in zip(directions, x_sims[1:])}}


def _file_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _parse(raw, aspects):
    """Jurors write "+2", trailing commas, or prose around the JSON; read what they meant."""
    body = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""
    fixed = re.sub(r'(:\s*)\+(\d)', r'\1\2', body)
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    try:
        return json.loads(fixed)
    except Exception:
        out = {"aspects": {}, "b_broken": bool(re.search(r'"b_broken"\s*:\s*true', raw))}
        for a in aspects:
            m = re.search(r'"%s"\s*:\s*\{\s*"d"\s*:\s*([+-]?\d)(?:[^}]*?"why"\s*:\s*"([^"]*)")?' % re.escape(a), raw)
            if m:
                out["aspects"][a] = {"d": int(m.group(1)), "why": m.group(2) or ""}
        return out


WEIGHTS = {"teacher's direction": 3.0, "vision": 2.0, "beauty": 1.0, "change": 2.0, "original": 1.0, "anatomy and hands": 1.0, "defects": 1.0}
VISION = ""      # set by the caller: the collection's current vision
DIRECTION = ""   # set by the caller: the teacher's directions, verbatim


def _ask(seat, a_uri, b_uri, subject):
    model, url, aspects = JURORS[seat]
    keys = {a: a.split()[0] for a in aspects}           # short keys keep the answer to a few dozen tokens
    keys = {"beauty": "beauty", "original": "original", "change": "change", "anatomy and hands": "anatomy", "defects": "defects"}
    # neutral: the judge is told nothing to prefer (no direction, no vision); it judges beauty and uniqueness as it sees them
    text = ("A and B: two images. Score B against A, integers -3..3 (positive: B better): "
            "beauty = which is more beautiful; original = which is more unique, striking and unlike an ordinary image; "
            "change = 0 (measured elsewhere); anatomy = hands and body; defects = rendering faults (positive: B has fewer). "
            "Use 0 only if you truly cannot tell them apart; a small but visible improvement is 1, a small loss -1. "
            "Then name the biggest visible difference in at most 12 words. "
            'JSON only: {"d": {%s}, "note": "...", "broken": false}'
            % ", ".join('"%s": 0' % k for k in keys.values()))
    body = {"model": model, "temperature": 0, "max_tokens": 160, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "A:"}, {"type": "image_url", "image_url": {"url": a_uri}},
                {"type": "text", "text": "B:"}, {"type": "image_url", "image_url": {"url": b_uri}},
                {"type": "text", "text": text}]}]}
    if seat == "qwen":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = json.load(r)["choices"][0]["message"]["content"]
    body_txt = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else "{}"
    body_txt = re.sub(r'(:\s*)\+(\d)', r'\1\2', body_txt)
    try:
        d = json.loads(body_txt)
    except Exception:
        d = {"d": {k: int(v) for k, v in re.findall(r'"(\w+)"\s*:\s*([+-]?\d)', body_txt)}, "note": ""}
    note = str(d.get("note") or "")[:120]
    out = {}
    for a, k in keys.items():
        try:
            n = max(-3, min(3, int(round(float((d.get("d") or {}).get(k, 0))))))
        except Exception:
            n = 0
        out[a] = {"d": n, "why": note}
    return out, bool(d.get("broken"))


def judge(seat, champ, chall, subject):
    """Signed profile of the challenger against the champion: one call, champion as A, challenger as B."""
    prof, broken = _ask(seat, _file_uri(champ), _file_uri(chall), subject)
    return {a: {"d": float(v["d"]), "why": v["why"], "raw": [v["d"]]} for a, v in prof.items()}, broken
