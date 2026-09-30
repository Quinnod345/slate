"""visionary and goal-fidelity: the owner's two personal skills, in a small repo of their own (PLAN decision 10).

Slate doesn't own them. The installer only offers, once and with the owner's yes, to run
`scripts/setup_quinn_skills.sh`, which moves them into `~/code/quinn-skills` (its own git repo, shareable, maintained
by Claude) and symlinks them into every personal agent. Company agents never get them, and every other skill stays
exactly as it is.

This module answers the installer's two questions: are they on this machine, and are they set up already?
"""

from __future__ import annotations

import os
from pathlib import Path

from slate import procs
from slate.install.layout import Layout

NAMES = ("visionary", "goal-fidelity")


def repo_dir(lay: Layout) -> Path:
    return lay.home / "code" / "quinn-skills"


def script(lay: Layout) -> Path:
    return lay.repo / "scripts" / "setup_quinn_skills.sh"


def _points_into(link: Path, folder: Path) -> bool:
    if not link.is_symlink():
        return False
    target = Path(os.readlink(link))
    target = target if target.is_absolute() else link.parent / target
    try:
        Path(os.path.normpath(target)).relative_to(os.path.normpath(folder))
        return True
    except ValueError:
        return False


def status(lay: Layout) -> dict:
    """Where each skill is now, and whether both agent folders already link into the quinn-skills repo."""
    repo = repo_dir(lay)
    per_skill = {}
    available = False
    set_up = True
    for name in NAMES:
        in_repo = (repo / "skills" / name / "SKILL.md").is_file()
        where = {
            "repo": in_repo,
            "claude": _points_into(lay.claude_skills / name, repo / "skills"),
            "agents": _points_into(lay.agents_skills / name, repo / "skills"),
            "copies": [str(root / name) for root in (lay.claude_skills, lay.agents_skills, lay.codex_skills)
                       if (root / name / "SKILL.md").is_file() and not (root / name).is_symlink()],
        }
        available = available or in_repo or bool(where["copies"])
        set_up = set_up and in_repo and where["claude"] and where["agents"]
        per_skill[name] = where
    return {"available": available, "set_up": available and set_up, "repo": str(repo), "skills": per_skill,
            "script": str(script(lay))}


def command(lay: Layout, *, exclude_agents: tuple[str, ...] | list[str] = (), dry_run: bool = False) -> list[str]:
    argv = ["/bin/bash", str(script(lay))]
    argv.append("--dry-run" if dry_run else "--yes")
    for agent in exclude_agents:
        argv += ["--exclude-agent", agent]
    return argv


def run(lay: Layout, *, exclude_agents: tuple[str, ...] | list[str] = (), dry_run: bool = False,
        interactive: bool = False, openclaw_bin: str | None = None) -> dict:
    """Run the setup script (with --yes, or --dry-run). Only call this after the owner said yes."""
    argv = command(lay, exclude_agents=exclude_agents, dry_run=dry_run)
    if interactive:
        argv.append("--interactive")
    env = {"HOME": str(lay.home), "SLATE_DATA": str(lay.data)}
    if lay.sandbox:
        # A sandbox never asks the real OpenClaw gateway anything; the script treats an empty value as "not here".
        env["OPENCLAW_BIN"] = openclaw_bin or ""
    elif openclaw_bin is not None:
        env["OPENCLAW_BIN"] = openclaw_bin
    proc = procs.run(argv, env=env, timeout=600, capture=not interactive)
    return {"command": " ".join(argv[1:]), "exit_code": proc.returncode,
            "output": ((proc.stdout or "") + (proc.stderr or ""))[-4000:] if not interactive else None}
