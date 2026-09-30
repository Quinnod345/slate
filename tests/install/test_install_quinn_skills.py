"""scripts/setup_quinn_skills.sh: the owner's two skills in their own repo, linked for every personal agent, backed up
never deleted, never in ~/.codex/skills, and company agents kept out."""

from __future__ import annotations

import json
import os

import pytest

from install_helpers import REPO, client_calls, git, install_fake_clients, make_repo, openclaw_state, run_script, \
    seed_skill, set_openclaw_state, snapshot
from slate.install import core, layout, quinn_skills

SCRIPT = REPO / "scripts" / "setup_quinn_skills.sh"
NAMES = ("visionary", "goal-fidelity")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    for name in NAMES:
        seed_skill(h / ".claude/skills", name, f"the original {name}")
    seed_skill(h / ".claude/skills", "peer-files", "another personal skill")
    seed_skill(h / ".agents/skills", "ponder", "a skill Codex already has")
    return h


def _env(tmp_path, openclaw=""):
    return {"SLATE_DATA": str(tmp_path / "data"), "OPENCLAW_BIN": openclaw}


def _others(home):
    return {"peer": snapshot(home / ".claude/skills/peer-files"), "ponder": snapshot(home / ".agents/skills/ponder")}


def test_without_the_owners_yes_nothing_changes(home, tmp_path):
    before = snapshot(home)
    proc = run_script(SCRIPT, home=home, env=_env(tmp_path))
    assert proc.returncode == 1
    assert "owner's yes" in proc.stderr and "Nothing changed" in proc.stderr
    assert snapshot(home) == before


def test_dry_run_lists_every_step_and_changes_nothing(home, tmp_path):
    before = snapshot(home)
    proc = run_script(SCRIPT, "--dry-run", home=home, env=_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "would: create the git repo" in proc.stdout
    assert proc.stdout.count("would: link") == 4 and "backed up, not deleted" in proc.stdout
    assert snapshot(home) == before


def test_yes_makes_the_repo_links_them_and_backs_up_the_originals(home, tmp_path):
    originals = {n: (home / ".claude/skills" / n / "SKILL.md").read_text() for n in NAMES}
    others = _others(home)
    proc = run_script(SCRIPT, "--yes", home=home, env=_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    repo = home / "code/quinn-skills"
    assert (repo / ".git").is_dir()
    log = git(repo, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 1 and "visionary and goal-fidelity" in log[0]
    assert git(repo, "status", "--porcelain").stdout == ""
    assert (repo / "README.md").is_file() and "Claude maintains" in (repo / "README.md").read_text()
    assert (repo / "AGENTS.md").is_file()
    for name in NAMES:
        assert (repo / "skills" / name / "SKILL.md").read_text() == originals[name]
        for root in (".claude/skills", ".agents/skills"):
            link = home / root / name
            assert link.is_symlink() and os.readlink(link) == str(repo / "skills" / name)
    backups = list((tmp_path / "data/backups").glob("*-quinn-skills/claude-skills/*"))
    assert sorted(b.name for b in backups) == sorted(NAMES)
    for b in backups:
        assert (b / "SKILL.md").read_text() == originals[b.name], "the original is kept, byte for byte"
    assert not (home / ".codex").exists(), "nothing goes into ~/.codex/skills"
    assert _others(home) == others, "every other skill stays exactly as it is"


def test_a_second_run_changes_nothing(home, tmp_path):
    run_script(SCRIPT, "--yes", home=home, env=_env(tmp_path))
    before = snapshot(home)
    proc = run_script(SCRIPT, "--yes", home=home, env=_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("kept") == 4 and "source of truth" in proc.stdout
    assert snapshot(home) == before
    assert len(git(home / "code/quinn-skills", "log", "--oneline").stdout.splitlines()) == 1


def test_a_copy_in_codex_skills_is_moved_aside_so_codex_lists_it_once(home, tmp_path):
    seed_skill(home / ".codex/skills", "visionary", "an old Codex copy")
    proc = run_script(SCRIPT, "--yes", home=home, env=_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".codex/skills/visionary").exists()
    [moved] = list((tmp_path / "data/backups").glob("*-quinn-skills/codex-skills/visionary"))
    assert "old Codex copy" in (moved / "SKILL.md").read_text()


def test_several_openclaw_agents_wait_for_a_decision(home, tmp_path):
    fake = install_fake_clients(tmp_path / "bin", ("openclaw",))
    set_openclaw_state(home, agents={"main": {}, "work": {}})
    proc = run_script(SCRIPT, "--yes", home=home, env=_env(tmp_path, str(fake / "openclaw")))
    assert proc.returncode == 0, proc.stderr
    assert (home / ".claude/skills/visionary").is_symlink(), "Claude Code gets them straight away"
    assert not (home / ".agents/skills/visionary").exists(), "~/.agents/skills waits: it reaches every OpenClaw agent"
    assert "--exclude-agent" in proc.stdout and "main" in proc.stdout and "work" in proc.stdout


def test_an_excluded_agent_is_pinned_to_what_it_sees_today_and_checked(home, tmp_path):
    fake = install_fake_clients(tmp_path / "bin", ("openclaw",))
    set_openclaw_state(home, agents={"main": {}, "work": {}}, bundled=["apple-notes", "summarize"])
    proc = run_script(SCRIPT, "--yes", "--exclude-agent", "work", home=home,
                      env=_env(tmp_path, str(fake / "openclaw")))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = openclaw_state(home)
    assert state["agents"]["work"]["skills"] == ["apple-notes", "ponder", "summarize"], "exactly what it saw"
    assert "skills" not in state["agents"]["main"], "the personal agent isn't restricted"
    assert (home / ".agents/skills/visionary").is_symlink()
    assert "doesn't see them" in proc.stdout
    sets = [c for c in client_calls(home) if c["argv"][:2] == ["config", "set"]]
    assert sets and "--expect-current-absent" in sets[0]["argv"]


def test_an_agent_with_its_own_allowlist_is_left_as_it_is(home, tmp_path):
    fake = install_fake_clients(tmp_path / "bin", ("openclaw",))
    set_openclaw_state(home, agents={"main": {}, "work": {"skills": ["summarize"]}})
    proc = run_script(SCRIPT, "--yes", "--exclude-agent", "work", home=home,
                      env=_env(tmp_path, str(fake / "openclaw")))
    assert proc.returncode == 0, proc.stderr
    assert openclaw_state(home)["agents"]["work"]["skills"] == ["summarize"]
    assert not [c for c in client_calls(home) if c["argv"][:2] == ["config", "set"]]


def test_an_allowlist_that_names_them_is_refused(home, tmp_path):
    fake = install_fake_clients(tmp_path / "bin", ("openclaw",))
    set_openclaw_state(home, agents={"main": {}, "work": {"skills": ["visionary"]}})
    before = snapshot(home, skip=("client-calls.jsonl",))
    proc = run_script(SCRIPT, "--yes", "--exclude-agent", "work", home=home,
                      env=_env(tmp_path, str(fake / "openclaw")))
    assert proc.returncode == 1 and "Take them out" in proc.stderr
    assert snapshot(home, skip=("client-calls.jsonl",)) == before, "refused before changing anything"


def test_an_unknown_agent_is_a_usage_error(home, tmp_path):
    fake = install_fake_clients(tmp_path / "bin", ("openclaw",))
    set_openclaw_state(home, agents={"main": {}, "work": {}})
    before = snapshot(home, skip=("client-calls.jsonl",))
    proc = run_script(SCRIPT, "--yes", "--exclude-agent", "nobody", home=home,
                      env=_env(tmp_path, str(fake / "openclaw")))
    assert proc.returncode == 2 and "no agent called 'nobody'" in proc.stderr
    assert snapshot(home, skip=("client-calls.jsonl",)) == before, "a usage error changes nothing, not even the repo"


def test_no_exclusions_links_for_every_agent(home, tmp_path):
    fake = install_fake_clients(tmp_path / "bin", ("openclaw",))
    set_openclaw_state(home, agents={"main": {}, "work": {}})
    proc = run_script(SCRIPT, "--yes", "--no-exclusions", home=home, env=_env(tmp_path, str(fake / "openclaw")))
    assert proc.returncode == 0, proc.stderr
    assert (home / ".agents/skills/goal-fidelity").is_symlink()
    assert "skills" not in openclaw_state(home)["agents"]["work"]


def test_a_missing_skill_is_a_missing_prerequisite(tmp_path):
    bare = tmp_path / "bare-home"
    bare.mkdir()
    proc = run_script(SCRIPT, "--yes", home=bare, env=_env(tmp_path))
    assert proc.returncode == 3 and "can't find visionary" in proc.stderr


def test_the_installer_offers_it_only_when_the_skills_are_here(tmp_path):
    empty = tmp_path / "empty-home"
    empty.mkdir()
    res = core.quinn_step(layout.resolve(empty), dry_run=False, yes=False, interactive=False, exclude_agents=())
    assert res["did"] == "not offered"


def test_the_installer_waits_for_the_owners_yes_then_runs_the_script(home):
    lay = layout.resolve(home)
    res = core.quinn_step(lay, dry_run=False, yes=False, interactive=True, exclude_agents=())
    assert res["did"] == "waiting for the owner's yes" and "setup_quinn_skills.sh --yes" in res["next"]
    assert not (home / "code/quinn-skills").exists()
    res = core.quinn_step(lay, dry_run=False, yes=True, interactive=False, exclude_agents=())
    assert res["did"] == "set up" and res["set_up"], res.get("output")
    assert quinn_skills.status(lay)["set_up"]
    again = core.quinn_step(lay, dry_run=False, yes=True, interactive=False, exclude_agents=())
    assert again["did"] == "already set up"


def test_slate_install_never_links_them_itself(home, tmp_path):
    """Slate's own install leaves them alone: they're the owner's, set up only through the script."""
    repo = make_repo(tmp_path)
    before = {n: snapshot(home / ".claude/skills" / n) for n in NAMES}
    core.install(home=home, repo=repo, agents=["claude", "codex"], jobs_agent=False, doctor=False, mcp=False)
    assert {n: snapshot(home / ".claude/skills" / n) for n in NAMES} == before
    assert not (home / ".agents/skills/visionary").exists()
    data = json.loads(layout.resolve(home, repo=repo).manifest_file.read_text())
    assert not any("visionary" in link["target"] for link in data["links"])
