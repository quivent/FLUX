import os
import pathlib


SYSTEM_MODEL_CANDIDATES = (
    "/models/flux1",
    "/models/FLUX.1-dev",
    "/models/flux/FLUX.1-dev",
)


def _home():
    return pathlib.Path(os.path.expanduser("~"))


def _first_env(*keys):
    for key in keys:
        value = os.environ.get(key, "")
        if value:
            return value
    return ""


def valid_model_dir(path):
    if not path:
        return False
    root = pathlib.Path(path).expanduser()
    return all((root / name).exists() for name in ("model_index.json", "transformer", "vae"))


def default_model_dir():
    env_model = _first_env("MODEL_DIR", "FLUX_MODEL_DIR")
    home = _home()
    candidates = [
        env_model,
        *SYSTEM_MODEL_CANDIDATES,
        home / "Models" / "flux1",
        home / "models" / "flux1",
        home / "models" / "FLUX.1-dev",
        home / "models" / "flux" / "FLUX.1-dev",
    ]
    for candidate in candidates:
        if valid_model_dir(candidate):
            return str(candidate)
    if env_model:
        return env_model
    # Keep this fallback chain identical to internal/config/config.go:resolveModelDir
    # and core/paths.py:flux_model_dir() in Council-of-Gemmas.
    if pathlib.Path("/models").exists():
        return "/models/flux1"
    if (home / "models").exists():
        return str(home / "models" / "flux1")
    return str(home / "Models" / "flux1")


def default_out_dir():
    output = _first_env("OUT_DIR", "FLUX_OUTPUT_DIR")
    if output:
        return output
    if pathlib.Path("/runs").exists():
        return "/runs/flux-output"
    return str(_home() / "Models" / "flux-output")


def default_direct_ane_dir():
    return str(pathlib.Path(default_model_dir()) / "ane" / "direct")
