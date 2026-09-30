"""The launchd agent (rendered and dry-run only: no test ever loads an agent on this Mac) and every command's help."""

from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path

import pytest

from slate import procs
from slate.cli import run_cli
from slate.errors import Refused
from slate.jobs import agent


@pytest.fixture(autouse=True)
def never_launchctl(monkeypatch, tmp_path):
    """Any launchctl call fails the test, and the LaunchAgents folder is a temp one."""
    def refuse(*args):
        raise AssertionError(f"a test tried to run launchctl {args}")

    monkeypatch.setattr(agent, "_launchctl", refuse)
    real_run = procs.run

    def guarded_run(argv, **kw):
        if argv and "launchctl" in str(argv[0]):
            raise AssertionError("a test tried to run launchctl through procs.run")
        return real_run(argv, **kw)

    monkeypatch.setattr(procs, "run", guarded_run)
    monkeypatch.setenv("SLATE_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))


def test_plist_content(slate_home):
    plist = plistlib.loads(agent.render_bytes())
    assert plist["Label"] == "dev.slate.jobs"
    assert plist["ProgramArguments"] == [sys.executable, "-m", "slate", "jobs", "serve"]
    env = plist["EnvironmentVariables"]
    path_dirs = env["PATH"].split(":")
    assert "/opt/homebrew/bin" in path_dirs and str(Path.home() / ".local" / "bin") in path_dirs
    assert "/usr/bin" in path_dirs and "/bin" in path_dirs
    assert env["BROWSER"] == "none"
    assert env["SLATE_DATA"] == str(slate_home["data"])  # a custom Slate location is kept
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["AbandonProcessGroup"] is True  # reloading the agent never kills a running job
    log = str(slate_home["data"] / "logs" / "jobs" / "runner.launchd.log")
    assert plist["StandardOutPath"] == log and plist["StandardErrorPath"] == log
    assert agent.plist_path().name == "dev.slate.jobs.plist"


def test_plist_label_is_configurable(slate_home):
    (slate_home["data"] / "slate.toml").write_text('[jobs]\nagent_label = "com.example.slate-jobs"\n')
    assert plistlib.loads(agent.render_bytes())["Label"] == "com.example.slate-jobs"
    assert agent.service() == f"gui/{os.getuid()}/com.example.slate-jobs"


def test_install_dry_run_touches_nothing(slate_home):
    out = agent.install(dry_run=True)
    assert out["dry_run"] is True and not Path(out["plist"]).exists()
    uid = os.getuid()
    assert f"/bin/launchctl bootstrap gui/{uid} {out['plist']}" in out["commands"]
    assert f"/bin/launchctl bootout gui/{uid}/dev.slate.jobs" in out["commands"]
    assert "<key>KeepAlive</key>" in out["plist_xml"]
    gone = agent.uninstall(dry_run=True)
    assert gone["commands"] == [f"/bin/launchctl bootout gui/{uid}/dev.slate.jobs"]


def test_install_waits_for_a_persons_yes_under_an_agent(slate_home, monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    with pytest.raises(Refused) as exc:
        agent.install()
    assert "--yes" in exc.value.hint and "login item" in exc.value.message
    with pytest.raises(Refused):
        agent.uninstall()
    assert not agent.plist_path().exists()


def test_agent_cli_plist_and_dry_runs(slate_home, monkeypatch):
    code, out, _ = run_cli(["--json", "jobs", "agent", "plist"])
    assert code == 0 and json.loads(out)["content"]["EnvironmentVariables"]["BROWSER"] == "none"
    code, out, _ = run_cli(["jobs", "agent", "plist"])
    assert code == 0 and out.startswith("<?xml")
    code, out, _ = run_cli(["--json", "--dry-run", "jobs", "agent", "install"])
    assert code == 0 and json.loads(out)["dry_run"] is True
    code, out, _ = run_cli(["--dry-run", "jobs", "agent", "uninstall"])
    assert code == 0 and "bootout" in out
    monkeypatch.setenv("SLATE_CONTEXT", "codex")
    code, _, err = run_cli(["jobs", "agent", "install"])
    assert code == 1 and "--yes" in err


def test_agent_status_reads_launchctl(slate_home, monkeypatch):
    monkeypatch.setattr(agent, "_launchctl", lambda *a: (0, "state = running\n\tpid = 4242\n\tlast exit code = 0\n"))
    info = agent.status()
    assert info["loaded"] and info["pid"] == 4242 and info["state"] == "running"
    monkeypatch.setattr(agent, "_launchctl", lambda *a: (113, "Could not find service"))
    info = agent.status()
    assert info["loaded"] is False and info["installed"] is False


COMMANDS = [
    ["job"], ["job", "submit"], ["job", "status"], ["job", "wait"], ["job", "log"], ["job", "cancel"],
    ["job", "retry"], ["job", "list"],
    ["jobs"], ["jobs", "serve"], ["jobs", "status"], ["jobs", "locks"], ["jobs", "disk"], ["jobs", "prune"],
    ["jobs", "agent"], ["jobs", "agent", "install"], ["jobs", "agent", "uninstall"], ["jobs", "agent", "status"],
    ["jobs", "agent", "plist"],
    ["budget"], ["budget", "show"], ["budget", "set"], ["budget", "film"],
    ["ledger"], ["ledger", "list"], ["ledger", "inflight"], ["ledger", "reconcile"], ["ledger", "snapshot"],
]


@pytest.mark.parametrize("words", COMMANDS, ids=lambda w: " ".join(w))
def test_every_command_has_help(slate_home, words):
    code, out, _ = run_cli([*words, "--help"])
    assert code == 0 and "usage: slate" in out
    for tool in ("Bash(", "TodoWrite", "Task tool", "Claude Code's"):
        assert tool not in out  # agent-facing text never names one agent's own tools
