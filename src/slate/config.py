"""Slate's settings and model choices.

Settings merge in this order, later winning: built-in defaults, then `DATA/slate.toml`, then `SLATE_*` environment
variables. Model choices come from `registry.json`, which lists the best current model for each role with the date it
was verified. Anyone can override a role:

- per person, in slate.toml:   [models]  tts = "eleven_v4"
- per shell, in the environment: SLATE_MODEL_TTS=eleven_v4
- per film, in film.json:       "models": {"tts": "eleven_v4"}

A pinned film always uses its pin ("pins" in film.json), whatever the registry says now. That is what makes a re-voice
in week three match week one; re-pinning is explicit.
"""

from __future__ import annotations

import copy
import json
import os
import tomllib
from typing import Any

from slate import paths
from slate.errors import UsageError

DEFAULTS: dict[str, Any] = {
    "agent": {
        # The recommended agent. Codex and OpenClaw keep their own model settings; this is advice, not a switch.
        "recommended_model": "claude-opus-5-5",
        "recommended_effort": "max",
    },
    "render": {
        "slots": 1,  # concurrent Remotion/web renders on this Mac (the load once hit 293 on 14 cores)
        "motion_blur_samples": 16,  # render_mb sub-frames averaged in 16-bit
        "draft_samples": 4,  # draft mode and the tracer bullet
        "chunk_frames": 600,
    },
    "jobs": {
        "disk_reserve_gb": 5.0,  # never start a job that would leave less than this free
        "poll_seconds": 2.0,
    },
    "films": {"git_init": True},
    "ae": {"app": None, "aerender": None},
    "models": {},
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _from_toml() -> dict:
    path = paths.config_file()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"{path} isn't valid TOML: {exc}") from exc


def _from_env() -> dict:
    out: dict[str, Any] = {}
    env = os.environ
    if env.get("SLATE_RENDER_SLOTS"):
        out.setdefault("render", {})["slots"] = int(env["SLATE_RENDER_SLOTS"])
    if env.get("SLATE_MB_SAMPLES"):
        out.setdefault("render", {})["motion_blur_samples"] = int(env["SLATE_MB_SAMPLES"])
    if env.get("SLATE_DISK_RESERVE_GB"):
        out.setdefault("jobs", {})["disk_reserve_gb"] = float(env["SLATE_DISK_RESERVE_GB"])
    models = {k[len("SLATE_MODEL_"):].lower(): v for k, v in env.items() if k.startswith("SLATE_MODEL_") and v}
    if models:
        out["models"] = models
    return out


def load() -> dict:
    """The merged settings (defaults < slate.toml < environment). Cheap enough to call per command."""
    return _deep_merge(_deep_merge(DEFAULTS, _from_toml()), _from_env())


_registry_cache: dict[str, Any] = {}


def registry(refresh: bool = False) -> dict:
    """registry.json: roles, models (ids, prices, limits), tool versions, each with verified_on."""
    if refresh or "data" not in _registry_cache:
        if not paths.REGISTRY_FILE.exists():
            raise UsageError(f"registry.json is missing from {paths.REPO}; reinstall Slate.")
        _registry_cache["data"] = json.loads(paths.REGISTRY_FILE.read_text(encoding="utf-8"))
    return _registry_cache["data"]


def roles() -> list[str]:
    return sorted(registry().get("roles", {}))


def _film_json(film: Any) -> dict:
    if film is None:
        return {}
    if isinstance(film, dict):
        return film
    path = paths.film_dir(film) / "film.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _pinned_model(pin: Any) -> str | None:
    if isinstance(pin, str):
        return pin
    if isinstance(pin, dict):
        return pin.get("model") or pin.get("model_id")
    return None


def model(role: str, film: Any = None) -> str:
    """The model id to use for a role. Precedence: film pin > film override > env > slate.toml > registry default."""
    reg_roles = registry().get("roles", {})
    if role not in reg_roles:
        raise UsageError(f"Unknown model role {role!r}. Known roles: {', '.join(sorted(reg_roles))}.")
    fj = _film_json(film)
    pinned = _pinned_model((fj.get("pins") or {}).get(role))
    if pinned:
        return pinned
    if (fj.get("models") or {}).get(role):
        return fj["models"][role]
    user = load().get("models", {})
    if user.get(role):
        return user[role]
    return reg_roles[role]["default"]


def model_info(model_id: str) -> dict:
    """Registry facts about a model id (provider, kind, prices, limits, verified_on). Empty if unknown."""
    return registry().get("models", {}).get(model_id, {})


def tool(name: str) -> dict:
    """Registry facts about a tool or engine (pinned versions, e.g. remotion 4.0.521)."""
    entry = registry().get("tools", {}).get(name)
    if entry is None:
        raise UsageError(f"registry.json has no tool called {name!r}.")
    return entry


def engine_version() -> str:
    """The Remotion version the engine is pinned to (exactly; never 5.0 unplanned)."""
    return str(tool("remotion")["version"])
