"""`slate install`, `slate uninstall` and `slate update` on the command line, the job runner step, and prompts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from install_helpers import make_repo
from slate.cli import run_cli
from slate.install import core, links
from slate.install.layout import Layout


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.mark.parametrize("command", ["install", "uninstall", "update"])
def test_every_command_has_help(command):
    code, out, err = run_cli([command, "--help"])
    assert code == 0, err
    assert "Exit codes" in out


def test_install_help_names_every_part():
    _, out, _ = run_cli(["install", "--help"])
    for words in ("~/.claude/skills", "~/.agents/skills", "~/.codex/skills", "~/.local/bin/slate",
                  "/opt/homebrew/bin/slate", "MCP", "launchd", "doctor", "--dry-run", "--agents", "--home"):
        assert words in out, words


def test_install_dry_run_json_in_a_temp_home(home, bare_path):
    code, out, err = run_cli(["install", "--dry-run", "--json", "--home", str(home), "--agents", "claude,codex",
                              "--no-doctor"])
    assert code == 0, err
    res = json.loads(out)
    assert res["dry_run"] and res["sandbox"] and res["agents"] == ["claude", "codex"]
    assert res["links"]["changed"] >= 2 and all(a["did"].startswith("would") or a["state"] == "ok"
                                                 for a in res["links"]["actions"])
    assert [e["client"] for e in res["mcp"]] == ["claude", "codex"]
    assert res["jobs_agent"]["did"] == "shown"
    assert not any(home.iterdir()), "a dry run writes nothing"


def test_install_then_check_then_uninstall(home, bare_path):
    code, out, err = run_cli(["install", "--home", str(home), "--agents", "claude,codex", "--no-doctor"])
    assert code == 0, err
    assert "Installed Slate" in out and "links and shims" in out
    code, out, _ = run_cli(["install", "--check", "--json", "--home", str(home), "--agents", "claude,codex"])
    res = json.loads(out)
    assert code == 0 and res["ok"], res["problems"]
    assert (Path(res["layout"]["data"]) / "install" / "last.json").is_file()
    code, out, err = run_cli(["uninstall", "--home", str(home)])
    assert code == 0, err
    assert "Removed Slate's wiring" in out and "Kept:" in out
    assert not (home / ".local/bin/slate").exists()


def test_check_fails_until_installed(home, bare_path):
    code, out, _ = run_cli(["install", "--check", "--json", "--home", str(home), "--agents", "claude"])
    res = json.loads(out)
    assert code == 1 and not res["ok"]
    assert any(p.startswith("~/.local/bin/slate") and "not there yet" in p for p in res["problems"])


def test_an_unknown_agent_is_exit_2(home):
    code, out, err = run_cli(["install", "--home", str(home), "--agents", "claude,notepad", "--json"])
    assert code == 2
    assert json.loads(out)["error"] == "usage"


def test_register_twice_is_harmless():
    from slate.install import cli as install_cli
    from slate import update

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    install_cli.register(sub)
    install_cli.register(sub)
    update.register(sub)
    update.register(sub)
    assert {"install", "uninstall", "update"} <= set(sub.choices)


def test_nothing_ever_prompts_in_an_agent(monkeypatch):
    monkeypatch.delenv("SLATE_NONINTERACTIVE", raising=False)
    monkeypatch.setenv("SLATE_CONTEXT", "codex")
    assert core.can_ask(True) is False
    monkeypatch.delenv("SLATE_CONTEXT")
    monkeypatch.setenv("SLATE_NONINTERACTIVE", "1")
    assert core.can_ask(True) is False
    assert core.can_ask(False) is False


# --- The job runner step, on a real (not sandbox) layout whose paths are all temp, with the job agent faked ----------


class FakeJobAgent:
    def __init__(self, loaded=False, plist=None):
        self.loaded = loaded
        self.plist = plist
        self.installed_with = None

    def status(self):
        return {"label": "dev.slate.jobs", "plist": str(self.plist), "installed": self.plist.exists(),
                "loaded": self.loaded}

    def render_bytes(self):
        return b"<plist>current</plist>"

    def install(self, *, dry_run=False, yes=False):
        self.installed_with = {"dry_run": dry_run, "yes": yes}
        return {"label": "dev.slate.jobs", "plist": str(self.plist), "commands": ["launchctl bootstrap ..."],
                "dry_run": dry_run, "loaded": not dry_run}


@pytest.fixture
def real_layout(tmp_path):
    return Layout(home=tmp_path / "h", sandbox=False, root=tmp_path / "root", repo=make_repo(tmp_path),
                  data=tmp_path / "data")


def _patch_agent(monkeypatch, fake):
    import slate.jobs.agent as job_agent

    for name in ("status", "render_bytes", "install"):
        monkeypatch.setattr(job_agent, name, getattr(fake, name))


def test_job_runner_waits_for_a_yes(monkeypatch, real_layout, tmp_path):
    fake = FakeJobAgent(plist=tmp_path / "dev.slate.jobs.plist")
    _patch_agent(monkeypatch, fake)
    res = core.jobs_agent_step(real_layout, dry_run=False, yes=False, interactive=True)
    assert res["did"] == "waiting for a yes" and "--yes" in res["next"]
    assert fake.installed_with is None


def test_job_runner_loads_with_a_yes(monkeypatch, real_layout, tmp_path):
    fake = FakeJobAgent(plist=tmp_path / "dev.slate.jobs.plist")
    _patch_agent(monkeypatch, fake)
    res = core.jobs_agent_step(real_layout, dry_run=False, yes=True, interactive=False)
    assert res["did"] == "loaded" and fake.installed_with == {"dry_run": False, "yes": True}


def test_job_runner_already_loaded_is_left_alone(monkeypatch, real_layout, tmp_path):
    plist = tmp_path / "dev.slate.jobs.plist"
    plist.write_bytes(b"<plist>current</plist>")
    fake = FakeJobAgent(loaded=True, plist=plist)
    _patch_agent(monkeypatch, fake)
    res = core.jobs_agent_step(real_layout, dry_run=False, yes=True, interactive=False)
    assert res["did"] == "already loaded" and fake.installed_with is None


def test_job_runner_in_a_sandbox_is_only_shown(home):
    res = core.jobs_agent_step(core.layout_mod.resolve(home), dry_run=False, yes=True, interactive=False)
    assert res["did"] == "shown" and "real login session" in res["why"]
    assert res["plist"].startswith(str(home)), "the shown plist path is inside the sandbox"
    assert not (home / "Library/LaunchAgents").exists()


def test_doctor_not_wired_is_unchecked_never_passed(home, monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "slate.doctor" else real(name, *a))
    res = core.doctor_step(core.layout_mod.resolve(home))
    assert res["status"] == "unchecked"


def test_json_output_survives_native_noise_around_it():
    """Regression: with the ocr extra in, Apple's Vision printed 'Unable to find a valid E5 ...' after doctor's JSON."""
    from slate.install.output import first_json

    noise = "Unable to find a valid E5 in provided path /System/Library/.../x.bundle/. Found bundles : { }."
    assert first_json('{"checks": [1]}\n' + noise + "\n" + noise) == {"checks": [1]}
    assert first_json("warming up [cache\n" + '{"ok": true}') == {"ok": True}, "a stray bracket before it is skipped"
    assert first_json("no JSON here at all") is None and first_json("") is None


def test_doctor_results_are_summarised(home, monkeypatch):
    import importlib.util
    import subprocess

    from slate import procs

    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object())
    report = {"status": "red", "checks": [
        {"id": "keys.elevenlabs", "status": "red", "summary": "no key", "fix": "slate connect elevenlabs"},
        {"id": "ae.prefs", "status": "yellow", "summary": "no prefs", "fix": None},
        {"id": "tools.ffmpeg", "status": "green", "summary": "9.0.2", "fix": None}]}
    monkeypatch.setattr(procs, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, json.dumps(report), ""))
    res = core.doctor_step(core.layout_mod.resolve(home))
    assert res["status"] == "red" and res["counts"] == {"red": 1, "yellow": 1, "green": 1}
    assert res["red"] == [{"id": "keys.elevenlabs", "summary": "no key", "fix": "slate connect elevenlabs"}]
