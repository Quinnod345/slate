"""Helpers for the eval suite's tests.

Nothing here runs a real agent or calls a model. `fake_suite` builds a throwaway copy of the suite whose four
configurations run `fake_agent.py`, keeping everything else about each configuration (stdin, kill, transcript format,
environment) exactly as configs.json has it.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

from slate import paths

EVALS = paths.REPO / "evals"
FAKE_AGENT = Path(__file__).with_name("fake_agent.py")


@pytest.fixture(autouse=True)
def _scratch_codex_home(tmp_path, monkeypatch):
    """The runner tidies Codex's trust list and Claude Code's project folders after their runs; in tests both are
    scratch ones, never ~/.codex or ~/.claude."""
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))  # and Claude Code's project folders
    return home


@pytest.fixture
def real_configs():
    from slate.evals import suite

    return suite.load_configs(EVALS)


@pytest.fixture
def real_cases(real_configs):
    from slate.evals import suite

    return suite.load_cases(EVALS, configs=real_configs)


@pytest.fixture
def fake_suite(tmp_path, monkeypatch):
    """make(modes={config_id: mode}, collect_mode=None) -> the suite folder, with SLATE_EVALS_DIR pointing at it."""

    def make(modes: dict[str, str], *, cases: list[str] | None = None) -> Path:
        root = tmp_path / "evals"
        shutil.copytree(EVALS / "cases", root / "cases")
        shutil.copytree(EVALS / "tripwire", root / "tripwire")
        shutil.copytree(EVALS / "mcp", root / "mcp")
        shutil.copy(EVALS / "sandbox-notice.md", root / "sandbox-notice.md")
        (root / "fixtures" / "recorded").mkdir(parents=True)
        (root / "fixtures" / "synthetic").mkdir(parents=True)
        if cases is not None:
            for p in (root / "cases").glob("*.json"):
                if p.stem not in cases:
                    p.unlink()
        configs = json.loads((EVALS / "configs.json").read_text())
        for cid, cfg in configs["configurations"].items():
            mode = modes.get(cid, "stdin")
            cfg["binary"] = sys.executable
            last = "-" if cfg["stdin"] == "prompt" else "{prompt}"
            cfg["argv"] = ["{binary}", str(FAKE_AGENT), mode, last]
            cfg["transcript"] = {"format": "claude-stream-json", "source": cfg["transcript"]["source"]}
            if cfg["transcript"]["source"] == "collect":
                cfg["collect"] = [{"argv": ["{binary}", str(FAKE_AGENT), "collect", "{session_id}"],
                                   "stdout": "transcript", "needs": ["session_id"]}]
                cfg["transcript"]["format"] = "openclaw"
            cfg.pop("mcp_template", None)
            cfg.pop("deny", None)
        (root / "configs.json").write_text(json.dumps(configs, indent=1))
        monkeypatch.setenv("SLATE_EVALS_DIR", str(root))
        return root

    return make
