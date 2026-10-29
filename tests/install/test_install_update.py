"""`slate update` in a temp clone: fast-forward only, re-link, engine against its pin, and every refusal."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from install_helpers import add_skill, git, make_repo
from slate import update
from slate.cli import run_cli
from slate.errors import MissingPrerequisite, Refused
from slate.install import core

PIN = "4.0.521"


def _engine_files(repo: Path, declared: str = PIN, pin: str = PIN) -> None:
    (repo / "registry.json").write_text(json.dumps({"tools": {"remotion": {"version": pin}}}))
    (repo / "engine").mkdir(exist_ok=True)
    (repo / "engine" / "package.json").write_text(json.dumps({"dependencies": {"remotion": declared}}))


@pytest.fixture
def origin(tmp_path):
    """A stand-in for the GitHub repo, with one commit."""
    repo = make_repo(tmp_path, skills=("slate", "slate-story"), name="origin")
    _engine_files(repo)
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "first")
    return repo


@pytest.fixture
def clone(tmp_path, origin):
    dest = tmp_path / "checkout"
    git(tmp_path, "clone", "--quiet", f"file://{origin}", str(dest))
    return dest


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def _commit(origin: Path, message: str) -> None:
    git(origin, "add", "-A")
    git(origin, "commit", "--quiet", "-m", message)


def test_update_fast_forwards_and_relinks(clone, origin, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    core.install(repo=clone, agents=["claude", "codex"], jobs_agent=False, quinn=False, doctor=False, mcp=False)
    assert (home / ".claude/skills/slate-story").is_symlink()
    add_skill(origin, "slate-sound")
    import shutil

    shutil.rmtree(origin / "skills/slate-story")
    _commit(origin, "add slate-sound, retire slate-story")
    res = update.run(repo=clone, doctor=False, sync_env=False)
    assert res["git"]["did"] == "updated" and res["git"]["before"] != res["git"]["after"]
    assert "skills/slate-sound/SKILL.md" in res["git"]["changed_files"]
    assert (home / ".claude/skills/slate-sound").is_symlink() and (home / ".agents/skills/slate-sound").is_symlink()
    assert not (home / ".claude/skills/slate-story").is_symlink(), "the retired skill's link is tidied away"
    assert res["ok"]


def test_update_when_already_current(clone, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    res = update.run(repo=clone, doctor=False, sync_env=False)
    assert res["git"]["did"] == "already up to date" and res["git"]["changed_files"] == []


def test_update_refuses_a_diverged_checkout(clone, origin, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    (clone / "local.txt").write_text("a local commit\n")
    git(clone, "add", "-A")
    git(clone, "commit", "--quiet", "-m", "local work")
    (origin / "upstream.txt").write_text("upstream\n")
    _commit(origin, "upstream work")
    head = git(clone, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(Refused) as info:
        update.run(repo=clone, doctor=False, sync_env=False)
    assert info.value.exit_code == 1 and "diverged" in info.value.message
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == head, "nothing moved"


def test_update_refuses_when_local_changes_are_in_the_way(clone, origin, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    (origin / "skills/slate/SKILL.md").write_text("---\nname: slate\ndescription: upstream edit\n---\n")
    _commit(origin, "edit slate")
    (clone / "skills/slate/SKILL.md").write_text("---\nname: slate\ndescription: my unsaved edit\n---\n")
    with pytest.raises(Refused) as info:
        update.run(repo=clone, doctor=False, sync_env=False)
    assert "Local changes" in info.value.message and "stash" in info.value.hint
    assert "my unsaved edit" in (clone / "skills/slate/SKILL.md").read_text()


def test_update_refuses_without_an_upstream(tmp_path, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    repo = make_repo(tmp_path, name="lonely")
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "only")
    with pytest.raises(Refused) as info:
        update.run(repo=repo, doctor=False, sync_env=False)
    assert "doesn't track a remote branch" in info.value.message and "set-upstream-to" in info.value.hint


def test_update_needs_a_git_checkout(tmp_path):
    with pytest.raises(MissingPrerequisite) as info:
        update.run(repo=make_repo(tmp_path, name="not-git"), doctor=False, sync_env=False)
    assert info.value.exit_code == 3 and "isn't a git checkout" in info.value.message


def test_update_dry_run_changes_nothing(clone, origin, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    add_skill(origin, "slate-sound")
    _commit(origin, "add slate-sound")
    head = git(clone, "rev-parse", "HEAD").stdout.strip()
    res = update.run(repo=clone, dry_run=True, doctor=False)
    assert res["git"]["did"] == "shown" and "pull --ff-only" in res["git"]["command"]
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == head
    assert res["env"]["did"] == "shown"
    assert not (home / ".claude").exists()


def test_env_sync_runs_only_when_dependencies_changed(clone, origin, home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    calls = []
    from slate.install import env as env_mod

    monkeypatch.setattr(env_mod, "sync", lambda repo, dry_run=False: calls.append(repo) or {"did": "synced"})
    (clone / ".venv/bin").mkdir(parents=True)
    (clone / ".venv/bin/python").write_text("")
    (origin / "README.md").write_text("docs only\n")
    _commit(origin, "docs")
    assert update.run(repo=clone, doctor=False)["env"]["did"] == "unchanged" and calls == []
    (origin / "pyproject.toml").write_text("[project]\nname = 'slate'\nversion = '0.0.1'\n")
    _commit(origin, "bump")
    assert update.run(repo=clone, doctor=False)["env"]["did"] == "synced" and calls == [clone]


def test_env_commands_install_the_ocr_extra(tmp_path):
    from slate.install import env as env_mod

    repo = make_repo(tmp_path)
    cmds = env_mod.commands(repo)
    assert cmds[0][:2] == ["uv", "venv"] and ">=3.11" in cmds[0]
    assert cmds[-1] == ["uv", "pip", "install", "--python", str(repo / ".venv/bin/python"), "-e", f"{repo}[ocr]"]
    assert env_mod.sync(repo, dry_run=True)["did"] == "shown"


def _fake_engine(folder: Path, version: str) -> None:
    (folder / "node_modules/remotion").mkdir(parents=True)
    (folder / "node_modules/remotion/package.json").write_text(json.dumps({"version": version}))
    (folder / "cli.mjs").write_text("// engine\n")


def test_engine_on_its_pin(tmp_path, slate_home):
    repo = make_repo(tmp_path)
    _engine_files(repo)
    _fake_engine(repo / "engine", PIN)
    res = update.engine_check(repo)
    assert res["status"] == "ok" and res["engine"]["source"] == "checkout" and res["engine"]["remotion"] == PIN


def test_engine_off_its_pin_or_missing(tmp_path, slate_home):
    repo = make_repo(tmp_path)
    _engine_files(repo)
    missing = update.engine_check(repo)
    assert missing["status"] == "not installed" and missing["engine"] is None and missing["problems"] == []
    assert any("slate engine install --npm-ci" in n for n in missing["next"])
    _fake_engine(repo / "engine", "4.0.529")
    drifted = update.engine_check(repo)
    assert drifted["status"] == "off pin" and "not the pinned 4.0.521" in drifted["problems"][0]
    installed = slate_home["data"] / "engine" / PIN
    _fake_engine(installed, PIN)
    assert update.engine_check(repo)["engine"]["source"] == "installed", "the installed engine for the pin wins"
    _engine_files(repo, declared="4.0.530")
    assert any("move together" in p for p in update.engine_check(repo)["problems"])


@pytest.mark.parametrize("missing", ["cli.mjs", "node_modules/remotion/package.json"])
def test_engine_check_does_not_fall_back_from_broken_explicit_override(tmp_path, slate_home, monkeypatch, missing):
    repo = make_repo(tmp_path)
    _engine_files(repo)
    _fake_engine(repo / "engine", PIN)
    override = tmp_path / "selected-engine"
    _fake_engine(override, PIN)
    (override / missing).unlink()
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(override))

    checked = update.engine_check(repo)

    assert checked["status"] == "off pin"
    assert checked["engine"] is None
    assert any("SLATE_ENGINE_DIR" in problem and missing in problem for problem in checked["problems"])
    assert any("Unset SLATE_ENGINE_DIR" in step for step in checked["next"])


def test_engine_check_refuses_override_with_interrupted_dependencies(tmp_path, slate_home, monkeypatch):
    from slate import jsonio
    from slate.engine import install

    repo = make_repo(tmp_path)
    _engine_files(repo)
    _fake_engine(repo / "engine", PIN)
    override = tmp_path / "partial-engine"
    _fake_engine(override, PIN)
    jsonio.write_atomic(override / install.MARKER, {"packages_ready": False})
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(override))
    checked = update.engine_check(repo)
    assert checked["status"] == "off pin" and checked["engine"] is None
    assert any("did not finish" in problem for problem in checked["problems"])


def test_update_command_refuses_on_this_checkout_without_an_upstream():
    """The real checkout has no remote yet (phase 7 creates the GitHub repo), so update says so, and moves nothing."""
    from slate import paths

    if not (paths.REPO / ".git").exists():
        pytest.skip("not a git checkout")
    upstream = git(paths.REPO, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if upstream.returncode == 0:
        pytest.skip("this checkout tracks a remote now")
    code, out, err = run_cli(["update", "--dry-run", "--json"])
    assert code == 1
    payload = json.loads(out)
    assert payload["error"] == "refused" and "set-upstream-to" in payload["hint"]


def test_update_help():
    code, out, _ = run_cli(["update", "--help"])
    assert code == 0
    assert "--ff-only" in out and "re-link" in out and "Remotion pin" in out


def test_update_refreshes_engine_sources_even_without_a_remotion_bump(clone, origin, slate_home):
    from slate.engine import install

    (origin / "engine/cli.mjs").write_text("// first engine\n")
    (origin / "engine/package-lock.json").write_text('{}\n')
    (origin / "engine/remotion").mkdir()
    (origin / "engine/remotion/scene.mjs").write_text("// old scene\n")
    _commit(origin, "add engine sources")
    git(clone, "pull", "--ff-only")
    dest = slate_home["data"] / "engine" / PIN
    _fake_engine(dest, PIN)
    # The actual installer provides the same layout and receipt as a real installation.
    install.ensure(PIN, source=clone / "engine")
    marker = json.loads((dest / install.MARKER).read_text())
    from slate import jsonio

    marker["npm_ci_lock_sha256"] = jsonio.sha256(clone / "engine/package-lock.json")
    (dest / install.MARKER).write_text(json.dumps(marker))
    (dest / "skills").mkdir()
    (dest / "skills/keep.txt").write_text("installed vendor skill\n")
    (origin / "engine/remotion/scene.mjs").write_text("// fixed scene\n")
    _commit(origin, "fix engine without changing its Remotion pin")

    res = update.run(repo=clone, doctor=False, sync_env=False, relink=False)

    assert res["ok"]
    assert (dest / "remotion/scene.mjs").read_text() == "// fixed scene\n"
    assert (dest / "skills/keep.txt").read_text() == "installed vendor skill\n"
    assert (dest / "node_modules/remotion/package.json").exists()
    assert res["engine_refresh"]["did"] == "refreshed"


def test_update_cannot_succeed_when_relink_process_failed(clone, monkeypatch):
    monkeypatch.setattr(update, "_relink", lambda *a, **k: {"exit_code": 1, "tail": "installer failed"})
    res = update.run(repo=clone, doctor=False, sync_env=False)
    assert not res["ok"]


@pytest.mark.parametrize("status,code", [("red", 1), ("unchecked", 0), ("green", 1)])
def test_update_cannot_claim_verified_success_when_doctor_failed(clone, monkeypatch, status, code):
    monkeypatch.setattr(core, "doctor_step", lambda *a, **k: {"status": status, "exit_code": code})
    res = update.run(repo=clone, sync_env=False, relink=False)
    assert not res["ok"]
