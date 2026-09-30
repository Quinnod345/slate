"""Uninstall reverses the installer, and only the installer."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from install_helpers import REPO, make_repo, run_script, seed_skill, snapshot
from slate.install import core, links


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def _install(home, repo):
    return core.install(home=home, repo=repo, agents=["claude", "codex", "openclaw"], jobs_agent=False, quinn=False,
                        doctor=False, mcp=False)


def test_uninstall_reverses_only_slates_links(home, repo, tmp_path):
    seed_skill(home / ".claude/skills", "visionary")
    seed_skill(home / ".agents/skills", "ponder")
    seed_skill(home / ".claude/skills", "higgsfield-generate")
    (home / ".claude/skills/elsewhere").symlink_to(seed_skill(tmp_path / "x", "elsewhere"))
    old = seed_skill(home / ".agents/skills", "slate", "an old copy")
    old_text = (old / "SKILL.md").read_text()
    others_before = {r: snapshot(home / r) for r in (".claude/skills", ".agents/skills")}
    _install(home, repo)
    assert (home / ".agents/skills/higgsfield-generate").is_symlink()
    res = core.uninstall(home=home, repo=repo, mcp=False, jobs_agent=False)
    assert res["ok"]
    removed = {Path(r["target"]).relative_to(home).as_posix() for r in res["removed"]}
    assert removed == {".claude/skills/slate", ".claude/skills/slate-story", ".agents/skills/slate",
                       ".agents/skills/slate-story", ".agents/skills/higgsfield-generate", ".local/bin/slate",
                       "opt/homebrew/bin/slate"}
    assert (home / ".agents/skills/slate/SKILL.md").read_text() == old_text, "the old copy is put back"
    assert [r["did"] for r in res["restored"]] == ["put back"]
    for root, before in others_before.items():
        assert snapshot(home / root) == before, f"{root} isn't as it was before the install"
    assert not (home / ".agents/skills/higgsfield-generate").exists()
    assert (home / ".claude/skills/higgsfield-generate/SKILL.md").is_file(), "Higgsfield's own install stays"


def test_uninstall_dry_run_changes_nothing(home, repo):
    seed_skill(home / ".agents/skills", "slate", "an old copy")
    _install(home, repo)
    before = snapshot(home)
    res = core.uninstall(home=home, repo=repo, dry_run=True, mcp=False, jobs_agent=False)
    assert all(r["did"] == "would remove" for r in res["removed"]) and len(res["removed"]) == 6
    assert [r["did"] for r in res["restored"]] == ["would put back"]
    assert snapshot(home) == before


def test_a_link_the_person_replaced_is_left_alone(home, repo, tmp_path):
    seed_skill(home / ".claude/skills", "higgsfield-soul-id")
    _install(home, repo)
    mine = seed_skill(tmp_path / "mine", "higgsfield-soul-id")
    (home / ".agents/skills/higgsfield-soul-id").unlink()
    (home / ".agents/skills/higgsfield-soul-id").symlink_to(mine)
    res = core.uninstall(home=home, repo=repo, mcp=False, jobs_agent=False)
    assert (home / ".agents/skills/higgsfield-soul-id").is_symlink()
    assert os.readlink(home / ".agents/skills/higgsfield-soul-id") == str(mine)
    assert any("isn't the link Slate made" in r["why"] for r in res["left_alone"])


def test_a_stranger_at_the_shim_path_is_left_alone(home, repo):
    _install(home, repo)
    (home / ".local/bin/slate").unlink()
    (home / ".local/bin/slate").write_text("#!/bin/sh\necho mine\n")
    res = core.uninstall(home=home, repo=repo, mcp=False, jobs_agent=False)
    assert (home / ".local/bin/slate").read_text() == "#!/bin/sh\necho mine\n"
    assert any(r["target"] == str(home / ".local/bin/slate") for r in res["left_alone"])


def test_keep_backups_leaves_them_where_they_are(home, repo):
    seed_skill(home / ".agents/skills", "slate", "an old copy")
    installed = _install(home, repo)
    backup = Path(installed["links"]["backups"][0]["backup"])
    res = core.uninstall(home=home, repo=repo, restore_backups=False, mcp=False, jobs_agent=False)
    assert res["restored"] == [] and backup.is_dir()
    assert not (home / ".agents/skills/slate").exists()


def test_a_backup_isnt_restored_over_something_new(home, repo):
    seed_skill(home / ".agents/skills", "slate", "an old copy")
    installed = _install(home, repo)
    (home / ".agents/skills/slate").unlink()
    seed_skill(home / ".agents/skills", "slate", "a newer copy the person made")
    res = core.uninstall(home=home, repo=repo, mcp=False, jobs_agent=False)
    assert "newer copy" in (home / ".agents/skills/slate/SKILL.md").read_text()
    assert res["restored"][0]["did"] == "kept in backups"
    assert Path(installed["links"]["backups"][0]["backup"]).is_dir()


def test_uninstall_sh_fallback_without_an_environment(tmp_path, home):
    """With no .venv, uninstall.sh still removes Slate's links and marked shims, and nothing else."""
    checkout = make_repo(tmp_path, name="bare-checkout")
    shutil.copy(REPO / "uninstall.sh", checkout / "uninstall.sh")
    core.install(home=home, repo=checkout, agents=["claude", "codex"], jobs_agent=False, quinn=False,
                 doctor=False, mcp=False)
    seed_skill(home / ".claude/skills", "visionary")
    others = snapshot(home / ".claude/skills/visionary")
    proc = run_script(checkout / "uninstall.sh", "--dry-run", home=home)
    assert proc.returncode == 0, proc.stderr
    assert "would remove" in proc.stdout and (home / ".claude/skills/slate").is_symlink()
    proc = run_script(checkout / "uninstall.sh", home=home)
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".claude/skills/slate").is_symlink() and not (home / ".agents/skills/slate").is_symlink()
    assert not (home / ".local/bin/slate").exists() and not (home / "opt/homebrew/bin/slate").exists()
    assert snapshot(home / ".claude/skills/visionary") == others
    assert "claude mcp remove --scope user slate" in proc.stdout


def test_uninstall_sh_uses_the_environment_when_it_is_there(home):
    proc = run_script(REPO / "uninstall.sh", "--dry-run", "--json", "--home", str(home), home=home)
    assert proc.returncode == 0, proc.stderr
    assert '"removed"' in proc.stdout and '"dry_run": true' in proc.stdout
