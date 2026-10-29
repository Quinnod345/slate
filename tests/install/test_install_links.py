"""Skill links and CLI shims: planned, applied, backed up, idempotent, and never in ~/.codex/skills."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from install_helpers import REPO, add_skill, make_repo, seed_skill, snapshot
from slate.errors import UsageError
from slate.install import layout, links
from slate.install import manifest as manifest_mod

ALL = ["claude", "codex", "openclaw"]


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_dry_run_plan_in_a_temp_home_writes_nothing(home, repo):
    p = links.plan(home, ALL, repo=repo)
    assert p.layout.sandbox and p.layout.home == home
    targets = {str(a.target) for a in p.actions}
    assert targets == {
        str(home / ".claude/skills/slate"), str(home / ".claude/skills/slate-story"),
        str(home / ".agents/skills/slate"), str(home / ".agents/skills/slate-story"),
        str(home / ".local/bin/slate"), str(home / "opt/homebrew/bin/slate"),
    }
    assert all(a.state == links.CREATE for a in p.actions)
    report = links.apply(p, dry_run=True)
    assert report["dry_run"] and report["changed"] == 6
    assert all(a["did"].startswith("would") for a in report["actions"])
    assert snapshot(home) == {}, "a dry run must not create anything"


def test_apply_links_every_skill_for_every_agent_folder(home, repo):
    report = links.apply(links.plan(home, ALL, repo=repo))
    assert report["changed"] == 6 and not report["errors"]
    for root in (".claude/skills", ".agents/skills"):
        for skill in ("slate", "slate-story"):
            link = home / root / skill
            assert link.is_symlink() and os.readlink(link) == str(repo / "skills" / skill)
            assert (link / "SKILL.md").is_file()


def test_shims_carry_the_marker_and_run_this_checkout(home, repo):
    links.apply(links.plan(home, ["claude"], repo=repo))
    for shim in (home / ".local/bin/slate", home / "opt/homebrew/bin/slate"):
        text = shim.read_text()
        assert links.SHIM_MARKER in text and str(repo) in text and "BROWSER=none" in text
        assert os.access(shim, os.X_OK)
        assert links.is_slate_shim(shim)


def test_shim_without_an_environment_says_what_to_do(home, repo):
    links.apply(links.plan(home, ["claude"], repo=repo))
    proc = subprocess.run([str(home / ".local/bin/slate"), "--version"], capture_output=True, text=True)
    assert proc.returncode == 3
    assert "install.sh" in proc.stderr and "missing" in proc.stderr


def test_shim_runs_the_real_checkout(home):
    links.apply(links.plan(home, ["claude"], repo=REPO, higgsfield=False))
    proc = subprocess.run([str(home / ".local/bin/slate"), "--version"], capture_output=True, text=True,
                          env={**os.environ, "HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("slate ")


def test_never_links_into_codex_skills(home, repo):
    (home / ".codex/skills").mkdir(parents=True)
    report = links.apply(links.plan(home, ["codex", "openclaw"], repo=repo))
    assert all(not a["target"].startswith(str(home / ".codex")) for a in report["actions"])
    assert list((home / ".codex/skills").iterdir()) == []
    lay = layout.resolve(home, repo=repo)
    bad = links.Action(links.SKILL, home / ".codex/skills/slate", repo / "skills/slate", "codex-skills")
    with pytest.raises(UsageError):
        links._guard(bad, lay)


def test_claude_only_and_codex_only_choose_their_own_folders(home, repo):
    links.apply(links.plan(home, ["claude"], repo=repo))
    assert (home / ".claude/skills/slate").is_symlink() and not (home / ".agents/skills").exists()
    other = home.parent / "home2"
    other.mkdir()
    links.apply(links.plan(other, ["codex"], repo=repo))
    assert (other / ".agents/skills/slate").is_symlink() and not (other / ".claude/skills").exists()


def test_a_real_folder_in_the_way_is_moved_aside_not_deleted(home, repo):
    old = seed_skill(home / ".agents/skills", "slate", "an old hand-made copy")
    before = (old / "SKILL.md").read_text()
    report = links.apply(links.plan(home, ALL, repo=repo))
    assert (home / ".agents/skills/slate").is_symlink()
    assert len(report["backups"]) == 1
    backup = Path(report["backups"][0]["backup"])
    assert (backup / "SKILL.md").read_text() == before
    assert backup.is_relative_to(layout.resolve(home, repo=repo).backups_dir)
    assert "skills" not in backup.relative_to(home).parts[:2], "backups never live inside a skill folder"
    recorded = manifest_mod.load(layout.resolve(home, repo=repo))["backups"]
    assert recorded[0]["original"] == str(home / ".agents/skills/slate") and recorded[0]["backup"] == str(backup)


def test_someone_elses_symlink_is_moved_aside_and_its_target_kept(home, repo, tmp_path):
    elsewhere = seed_skill(tmp_path / "elsewhere", "slate", "a different project")
    (home / ".claude/skills").mkdir(parents=True)
    (home / ".claude/skills/slate").symlink_to(elsewhere)
    report = links.apply(links.plan(home, ["claude"], repo=repo))
    backup = Path(report["backups"][0]["backup"])
    assert backup.is_symlink() and os.readlink(backup) == str(elsewhere)
    assert (elsewhere / "SKILL.md").is_file(), "the link was moved; what it pointed to is untouched"
    assert os.readlink(home / ".claude/skills/slate") == str(repo / "skills/slate")


def test_a_second_run_changes_nothing(home, repo):
    seed_skill(home / ".agents/skills", "slate", "old copy")
    links.apply(links.plan(home, ALL, repo=repo))
    before = snapshot(home, skip=("Library",))
    second = links.apply(links.plan(home, ALL, repo=repo))
    assert second["changed"] == 0 and second["backups"] == [] and second["errors"] == []
    assert second["kept"] == len(second["actions"])
    assert snapshot(home, skip=("Library",)) == before


def test_every_other_skill_stays_exactly_as_it_is(home, repo, tmp_path):
    seed_skill(home / ".claude/skills", "visionary")
    seed_skill(home / ".agents/skills", "ponder")
    (home / ".claude/skills/linked-elsewhere").symlink_to(seed_skill(tmp_path / "x", "linked-elsewhere"))
    (home / ".claude/skills/.DS_Store").write_bytes(b"\x00\x01")
    seed_skill(home / ".codex/skills", "codex-only")
    watched = {root: snapshot(home / root) for root in (".claude/skills", ".agents/skills", ".codex/skills")}
    links.apply(links.plan(home, ALL, repo=repo))
    after = {root: snapshot(home / root) for root in (".claude/skills", ".agents/skills", ".codex/skills")}
    for root, before in watched.items():
        for rel, value in before.items():
            assert after[root].get(rel) == value, f"{root}/{rel} changed"
    assert after[".codex/skills"] == watched[".codex/skills"]


def test_higgsfield_skills_are_shared_with_codex_and_openclaw(home, repo):
    seed_skill(home / ".claude/skills", "higgsfield-generate")
    seed_skill(home / ".claude/skills", "higgsfield-video-explainer")
    report = links.apply(links.plan(home, ["claude", "codex"], repo=repo))
    for name in ("higgsfield-generate", "higgsfield-video-explainer"):
        link = home / ".agents/skills" / name
        assert link.is_symlink() and os.readlink(link) == str(home / ".claude/skills" / name)
    assert not (home / ".claude/skills/higgsfield-generate").is_symlink(), "they stay global for Claude Code"
    kinds = {a["kind"] for a in report["actions"]}
    assert links.HIGGSFIELD in kinds
    only_claude = links.plan(home.parent / "h3", ["claude"], repo=repo)
    assert not any(a.kind == links.HIGGSFIELD for a in only_claude.actions)
    skipped = links.plan(home, ["codex"], repo=repo, higgsfield=False)
    assert not any(a.kind == links.HIGGSFIELD for a in skipped.actions)


def test_a_skills_cli_canonical_copy_isnt_looped(home, repo):
    canonical = seed_skill(home / ".agents/skills", "higgsfield-brandkit")
    (home / ".claude/skills").mkdir(parents=True)
    (home / ".claude/skills/higgsfield-brandkit").symlink_to(canonical)
    p = links.plan(home, ["codex"], repo=repo)
    [action] = [a for a in p.actions if a.kind == links.HIGGSFIELD]
    assert action.state == links.OK
    links.apply(p)
    assert (home / ".agents/skills/higgsfield-brandkit").is_dir()
    assert not (home / ".agents/skills/higgsfield-brandkit").is_symlink()


def test_no_higgsfield_skills_means_a_next_step(home, repo):
    p = links.plan(home, ["codex"], repo=repo)
    assert any("npx skills add higgsfield-ai/skills" in n for n in p.notes)


def test_a_stale_slate_link_is_tidied_and_live_ones_elsewhere_kept(home, repo):
    links.apply(links.plan(home, ALL, repo=repo))
    (home / ".claude/skills/slate-old").symlink_to(repo / "skills/slate-old")  # a skill renamed upstream
    report = links.apply(links.plan(home, ["claude"], repo=repo))
    assert not (home / ".claude/skills/slate-old").exists() and not (home / ".claude/skills/slate-old").is_symlink()
    assert any(a["kind"] == links.PRUNE and a["did"] == "removed" for a in report["actions"])
    assert (home / ".agents/skills/slate").is_symlink(), "a --agents claude run leaves the Codex links alone"


def test_a_removed_upstream_skill_is_unlinked(home, repo):
    links.apply(links.plan(home, ALL, repo=repo))
    import shutil

    shutil.rmtree(repo / "skills/slate-story")
    links.apply(links.plan(home, ALL, repo=repo))
    assert not (home / ".claude/skills/slate-story").is_symlink()
    assert not (home / ".agents/skills/slate-story").is_symlink()
    add_skill(repo, "slate-sound")
    links.apply(links.plan(home, ALL, repo=repo))
    assert (home / ".agents/skills/slate-sound").is_symlink()


def test_nothing_is_pruned_while_the_skills_folder_is_missing(home, repo):
    links.apply(links.plan(home, ALL, repo=repo))
    (repo / "skills").rename(repo / "skills-moved")
    p = links.plan(home, ALL, repo=repo)
    assert not any(a.kind == links.PRUNE for a in p.actions)


def test_an_older_shim_is_replaced_and_a_stranger_at_the_shim_path_moved_aside(home, repo):
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/slate").write_text(f"#!/bin/sh\n# {links.SHIM_MARKER}: old\nexec /old/checkout \"$@\"\n")
    (home / "opt/homebrew/bin").mkdir(parents=True)
    (home / "opt/homebrew/bin/slate").write_text("#!/bin/sh\necho another tool called slate\n")
    report = links.apply(links.plan(home, ["claude"], repo=repo))
    states = {a["target"]: a["state"] for a in report["actions"] if a["kind"] == links.SHIM}
    assert states[str(home / ".local/bin/slate")] == links.REPLACE
    assert states[str(home / "opt/homebrew/bin/slate")] == links.BACKUP
    backup = Path(next(b["backup"] for b in report["backups"]))
    assert "another tool" in backup.read_text()


def test_a_plan_gone_stale_never_clobbers(home, repo):
    p = links.plan(home, ["claude"], repo=repo)
    assert all(a.state == links.CREATE for a in p.actions)
    seed_skill(home / ".claude/skills", "slate", "made after the plan")
    report = links.apply(p)
    assert any(b["original"] == str(home / ".claude/skills/slate") for b in report["backups"])
    assert os.readlink(home / ".claude/skills/slate") == str(repo / "skills/slate")


def test_a_sandbox_never_reaches_the_real_homebrew_folder(home, repo):
    lay = layout.resolve(home, repo=repo)
    assert lay.sandbox and lay.homebrew_bin == home / "opt/homebrew/bin"
    with pytest.raises(UsageError):
        layout.resolve(home, repo=repo, root="/")


def test_the_real_home_is_not_a_sandbox_even_when_HOME_points_elsewhere():
    real = layout.account_home()
    lay = layout.resolve(real)
    assert not lay.sandbox and lay.homebrew_bin == Path("/opt/homebrew/bin")
    assert layout.resolve().sandbox, "HOME is a temp folder in these tests, so the default home is a sandbox"


def test_unknown_agent_is_a_usage_error():
    with pytest.raises(UsageError) as info:
        layout.parse_agents("claude,cursor")
    assert info.value.exit_code == 2
    assert layout.parse_agents("claude-code, Codex ,oneiro") == ["claude", "codex", "openclaw"]


def test_a_mac_with_no_agent_yet_still_gets_the_command_line(home, repo, monkeypatch):
    """Detection finding no agent is a choice already made, not an empty --agents (a fresh CI runner has none)."""
    monkeypatch.setattr(links, "detect_agents", lambda lay, search_path=None: [])
    p = links.plan(home, repo=repo)
    assert p.agents == []
    assert {str(a.target) for a in p.actions} == {str(home / ".local/bin/slate"), str(home / "opt/homebrew/bin/slate")}
    with pytest.raises(UsageError):
        links.plan(home, "", repo=repo)  # a person who types an empty --agents is still told to name one


def test_agents_are_detected_from_their_folders_and_clis(home, repo, tmp_path):
    lay = layout.resolve(home, repo=repo)
    assert links.detect_agents(lay, search_path="") == []
    (home / ".codex").mkdir()
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text("#!/bin/sh\n")
    (fake / "claude").chmod(0o755)
    assert links.detect_agents(lay, search_path=str(fake)) == ["claude", "codex"]


def test_audit_spots_skills_codex_would_list_twice(home, repo):
    links.apply(links.plan(home, ALL, repo=repo))
    seed_skill(home / ".codex/skills", "ponder")
    seed_skill(home / ".agents/skills", "ponder")
    (home / ".codex/skills/slate").symlink_to(repo / "skills/slate")
    res = links.audit(home, ALL, repo=repo)
    assert "ponder" in res["listed_twice_by_codex"] and "slate" in res["listed_twice_by_codex"]
    assert res["slate_links_in_codex_skills"] == ["slate"]
    assert not res["ok"] and any("~/.codex/skills/slate" in p for p in res["problems"])


def test_audit_is_clean_after_an_install(home, repo):
    links.apply(links.plan(home, ALL, repo=repo))
    res = links.audit(home, ALL, repo=repo)
    assert res["ok"], res["problems"]
    assert res["listed_twice_by_codex"] == []
