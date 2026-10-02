"""Scratch homes for agent clients: a throwaway OpenClaw home, sealed.

Slate makes a scratch OpenClaw home to check a client behaviour hands-on (scripts/doctor_spike.py --client-check)
without touching the owner's own OpenClaw. OPENCLAW_HOME, OPENCLAW_STATE_DIR and OPENCLAW_CONFIG_PATH alone don't seal
one: OpenClaw's memory plugin finds the Codex CLI's sign-in by itself (the Keychain, or CODEX_HOME's auth.json) and
calls OpenAI's embeddings with it, which on 2026-10-01 came back 429 from an account with no credits. So every scratch
home made here:

- puts OpenClaw's home, state, config and workspace inside the scratch folder, with a gateway port of its own, so it
  never reads or writes ~/.openclaw and never reaches the running Gateway;
- points CODEX_HOME at an empty folder of its own, so nothing finds the Codex CLI's sign-in there;
- turns memory off in its config (`plugins.slots.memory = "none"` and `memory.search.enabled = false`, OpenClaw's own
  switches), so nothing reaches for the Keychain's sign-in or calls an embeddings API;
- drops the model and provider keys from its environment.

`problems()` says what isn't sealed; the spike refuses to run a home that has any.
"""

from __future__ import annotations

import copy
import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

# Keys a scratch OpenClaw home must never inherit: they'd let it reach a real provider (OpenClaw's own list is longer;
# these are the ones on this Mac's agents' paths).
PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY",
                 "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY", "XI_API_KEY", "HF_KEY", "HF_API_KEY",
                 "HF_API_SECRET", "OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_GATEWAY_PASSWORD", "OPENCLAW_GATEWAY_URL",
                 "OPENCLAW_PROFILE")
MEMORY_OFF = {"plugins": {"slots": {"memory": "none"}}, "memory": {"search": {"enabled": False}}}


@dataclass
class OpenClawHome:
    root: Path
    env: dict[str, str]
    config_path: Path
    config: dict = field(default_factory=dict)

    @property
    def codex_home(self) -> Path:
        return Path(self.env["CODEX_HOME"])


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def openclaw_home(root: Path, *, port: int | None = None, config: dict | None = None,
                  base_env: dict | None = None) -> OpenClawHome:
    """Make a sealed scratch OpenClaw home under `root` and return it with the environment to run `openclaw` in."""
    root = Path(root)
    home = root / "home"
    state = home / ".openclaw"
    codex_home = root / "codex-home"
    for folder in (state / "workspace", codex_home):
        folder.mkdir(parents=True, exist_ok=True)
    port = port or free_port()
    cfg = _merge({"gateway": {"mode": "local", "port": port, "bind": "loopback"}}, config or {})
    cfg = _merge(cfg, MEMORY_OFF)  # last, so nothing a caller passes turns memory back on
    config_path = state / "openclaw.json"
    config_path.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")
    env = {k: v for k, v in dict(os.environ if base_env is None else base_env).items() if k not in PROVIDER_KEYS}
    env.update({"OPENCLAW_HOME": str(home), "OPENCLAW_STATE_DIR": str(state), "OPENCLAW_CONFIG_PATH": str(config_path),
                "OPENCLAW_WORKSPACE_DIR": str(state / "workspace"), "OPENCLAW_GATEWAY_PORT": str(port),
                "CODEX_HOME": str(codex_home), "BROWSER": "none"})
    return OpenClawHome(root=root, env=env, config_path=config_path, config=cfg)


def problems(h: OpenClawHome, real_home: Path | None = None) -> list[str]:
    """What keeps a scratch home from being sealed (empty when it is)."""
    real_home = Path(real_home or Path.home())
    root = h.root.resolve()
    out = []
    for key in ("OPENCLAW_HOME", "OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH", "OPENCLAW_WORKSPACE_DIR", "CODEX_HOME"):
        value = h.env.get(key)
        if not value or root not in Path(value).resolve().parents:
            out.append(f"{key} isn't inside the scratch folder ({value})")
    for real in (real_home / ".openclaw", real_home / ".codex"):
        if any(Path(h.env.get(k, "")).resolve() == real.resolve() for k in ("OPENCLAW_STATE_DIR", "CODEX_HOME")):
            out.append(f"it points at the real {real}")
    codex = h.codex_home
    if codex.exists() and any(codex.iterdir()):
        out.append(f"CODEX_HOME ({codex}) isn't empty: a sign-in there would be found")
    try:
        cfg = json.loads(h.config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return out + [f"its config can't be read: {exc}"]
    if (cfg.get("plugins") or {}).get("slots", {}).get("memory") != "none":
        out.append("its config leaves a memory plugin on (plugins.slots.memory isn't \"none\")")
    if ((cfg.get("memory") or {}).get("search") or {}).get("enabled") is not False:
        out.append("its config leaves memory search on (memory.search.enabled isn't false)")
    for key in PROVIDER_KEYS:
        if h.env.get(key):
            out.append(f"{key} is in its environment")
    return out
