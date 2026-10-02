"""Scratch OpenClaw homes are sealed by construction: their own home, state, config, workspace and gateway port, an
empty CODEX_HOME, memory off, no provider keys. Nothing here runs OpenClaw."""

import importlib.util
import json
from pathlib import Path

from slate import paths
from slate.evals import scratch


def test_a_scratch_openclaw_home_is_sealed(tmp_path):
    base = {"PATH": "/usr/bin", "OPENAI_API_KEY": "not-a-real-key", "OPENCLAW_GATEWAY_TOKEN": "x", "HOME": "/Users/you"}
    h = scratch.openclaw_home(tmp_path / "check", base_env=base)
    assert scratch.problems(h, real_home=tmp_path / "real-home") == []
    root = (tmp_path / "check").resolve()
    for key in ("OPENCLAW_HOME", "OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH", "OPENCLAW_WORKSPACE_DIR", "CODEX_HOME"):
        assert root in Path(h.env[key]).resolve().parents, key
    assert "OPENAI_API_KEY" not in h.env and "OPENCLAW_GATEWAY_TOKEN" not in h.env and h.env["PATH"] == "/usr/bin"
    assert h.codex_home.is_dir() and not any(h.codex_home.iterdir()), "nothing there to sign in with"
    cfg = json.loads(h.config_path.read_text())
    assert cfg["plugins"]["slots"]["memory"] == "none" and cfg["memory"]["search"]["enabled"] is False
    assert cfg["gateway"]["port"] == int(h.env["OPENCLAW_GATEWAY_PORT"]) and cfg["gateway"]["bind"] == "loopback"


def test_a_callers_config_cant_turn_memory_back_on(tmp_path):
    h = scratch.openclaw_home(tmp_path / "check", base_env={},
                              config={"memory": {"search": {"enabled": True}}, "plugins": {"slots": {"memory": "x"}},
                                      "agents": {"defaults": {"model": "dead/m"}}})
    cfg = json.loads(h.config_path.read_text())
    assert cfg["memory"]["search"]["enabled"] is False and cfg["plugins"]["slots"]["memory"] == "none"
    assert cfg["agents"]["defaults"]["model"] == "dead/m"


def test_problems_name_what_isnt_sealed(tmp_path):
    real = tmp_path / "real-home"
    (real / ".codex").mkdir(parents=True)
    h = scratch.openclaw_home(tmp_path / "check", base_env={})
    (h.codex_home / "auth.json").write_text("{}")
    h.env["OPENAI_API_KEY"] = "not-a-real-key"
    cfg = json.loads(h.config_path.read_text())
    cfg["memory"]["search"]["enabled"] = True
    h.config_path.write_text(json.dumps(cfg))
    found = " | ".join(scratch.problems(h, real_home=real))
    assert "isn't empty" in found and "OPENAI_API_KEY" in found and "memory search on" in found
    h.env["CODEX_HOME"] = str(real / ".codex")
    assert any("real" in p for p in scratch.problems(h, real_home=real))


def test_the_client_check_uses_the_sealed_home_and_a_dead_provider():
    """scripts/doctor_spike.py --client-check builds its scratch home only through slate.evals.scratch, with a model
    provider on the discard port, so the check's one agent turn calls no model."""
    spec = importlib.util.spec_from_file_location("doctor_spike", paths.REPO / "scripts" / "doctor_spike.py")
    spike = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spike)
    provider = spike.DEAD_PROVIDER["models"]["providers"]["dead"]
    assert provider["baseUrl"].startswith("http://127.0.0.1:9/") and spike.DEAD_PROVIDER["agents"]["defaults"]["model"] == "dead/m"
    source = (paths.REPO / "scripts" / "doctor_spike.py").read_text()
    assert "scratch.openclaw_home(" in source and "scratch.problems(" in source
    assert "OPENCLAW_STATE_DIR" not in source.split("def client_check_bundle_placeholders", 1)[1].split("def recorded", 1)[0], \
        "the check never builds an OpenClaw environment of its own"
