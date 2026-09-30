"""`slate update`: bring this checkout up to date, so every agent gets the new Slate at once.

    slate update [--dry-run] [--no-doctor]

1. `git pull --ff-only` in the checkout. Never a merge or a rebase: a checkout that has diverged from its remote, or
   has local changes the pull would overwrite, is refused with the way out.
2. When the pull changed `pyproject.toml` or `uv.lock`, the environment is synced (slate.install.env).
3. The links are made again by the updated installer itself: new skills get linked, removed ones tidied away.
4. The Node engine is checked against its pin in registry.json (Remotion moves only after the parity check).
5. `slate doctor` runs last.

Every agent reaches Slate through symlinks into this checkout and one CLI shim, so there's nothing to copy and
nothing to register again: one pull updates Claude Code, Codex and OpenClaw together.

This module also brings in `slate install` and `slate uninstall` (slate.install.cli), since the CLI's module list
names `slate.update` for this part of the surface.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from slate import jsonio, log, paths, procs
from slate.cli import add_command, flag
from slate.errors import MissingPrerequisite, Refused, SlateError

ENV_FILES = ("pyproject.toml", "uv.lock")
GIT_TIMEOUT_S = 300

EPILOG = ("Exit codes: 0 ok, 1 refused (diverged, local changes in the way, engine off its pin), 2 usage, "
          "3 missing prerequisite (not a git checkout, no uv).")

UPDATE_DESCRIPTION = """Bring this checkout up to date, so every agent gets the new Slate at once:

  1. git pull --ff-only (never a merge; a diverged checkout, or local changes in the way, is refused)
  2. sync the environment when pyproject.toml or uv.lock changed
  3. re-link skills and shims with the updated installer (new skills linked, removed ones tidied)
  4. check the Node engine against its Remotion pin in registry.json
  5. run slate doctor

Agents reach Slate through symlinks into this checkout and one CLI shim, so nothing is copied and nothing is
registered again."""


def _git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_S):
    # GIT_TERMINAL_PROMPT=0: a remote that wants credentials fails at once instead of waiting on a prompt nobody sees
    # (an agent's stdin is a pipe, and Oneiro's research slices are killed after ten minutes).
    return procs.run(["git", "-C", str(repo), *args], timeout=timeout,
                     env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""})


def _out(proc) -> str:
    return (proc.stdout or "").strip()


def _tail(proc) -> str:
    from slate.secrets import redact  # noqa: PLC0415

    return redact(((proc.stderr or "") + "\n" + (proc.stdout or "")).strip())[-800:]


def git_state(repo: Path) -> dict:
    """Branch, upstream and HEAD of the checkout (reads only)."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise MissingPrerequisite(
            f"{repo} isn't a git checkout, so there's nothing to update from.",
            hint="Install Slate with its one-line installer (it clones the repo), or clone it yourself and run "
                 "install.sh from the clone.",
        )
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise MissingPrerequisite(f"{repo} has no commits yet, so there's nothing to update.", hint=_tail(head))
    branch = _out(_git(repo, "rev-parse", "--abbrev-ref", "HEAD"))
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return {"repo": str(repo), "head": _out(head), "branch": branch,
            "upstream": _out(upstream) if upstream.returncode == 0 else None}


def pull(repo: Path, *, dry_run: bool = False) -> dict:
    """Fast-forward the checkout to its upstream. Refuses rather than merging."""
    state = git_state(repo)
    if not state["upstream"]:
        raise Refused(
            f"The checkout's branch {state['branch']!r} doesn't track a remote branch, so there's nothing to pull.",
            hint=f"Point it at one, for example: git -C {repo} branch --set-upstream-to origin/main",
        )
    command = f"git -C {repo} pull --ff-only"
    if dry_run:
        return {**state, "did": "shown", "command": command}
    proc = _git(repo, "pull", "--ff-only")
    if proc.returncode != 0:
        text = _tail(proc)
        lowered = text.lower()
        if "would be overwritten" in lowered or "commit your changes" in lowered or "unstaged" in lowered:
            raise Refused("Local changes in the checkout would be overwritten by the update, so nothing was pulled.",
                          hint=f"Commit or stash them (git -C {repo} status shows them), then run `slate update` "
                               "again.", details={"git": text})
        if "fast-forward" in lowered or "diverg" in lowered:
            raise Refused("This checkout has commits its remote doesn't (it has diverged), and Slate only ever "
                          "fast-forwards.",
                          hint=f"Look at `git -C {repo} log --oneline @{{u}}..HEAD`, push or move those commits, then "
                               "run `slate update` again.", details={"git": text})
        raise SlateError(f"git pull failed: {text}", hint="Check the network and your git credentials, then try again.")
    after = _out(_git(repo, "rev-parse", "HEAD"))
    changed: list[str] = []
    if after != state["head"]:
        diff = _git(repo, "diff", "--name-only", state["head"], after)
        changed = [line for line in _out(diff).splitlines() if line]
    return {**state, "did": "updated" if after != state["head"] else "already up to date", "before": state["head"],
            "after": after, "changed_files": changed, "command": command}


def _version(package_json: Path) -> str | None:
    data = jsonio.read(package_json, default=None)
    return data.get("version") if isinstance(data, dict) else None


def engine_check(repo: Path | None = None) -> dict:
    """The engine that would run, against the Remotion pin in registry.json. Reads only.

    Mirrors slate.engine.bridge.engine_dir(): the installed engine for the pin (DATA/engine/<pin>) wins when it's
    complete, then the checkout's own engine/. The pin moves only after the render-parity check, and registry.json
    and engine/package.json must move together.
    """
    repo = Path(repo or paths.REPO)
    registry = jsonio.read(repo / "registry.json", default={}) or {}
    pin = str(((registry.get("tools") or {}).get("remotion") or {}).get("version") or "") or None
    pkg = jsonio.read(repo / "engine" / "package.json", default={}) or {}
    declared = (pkg.get("dependencies") or {}).get("remotion")
    out: dict = {"pin": pin, "declared": declared, "problems": [], "next": []}
    if not pin:
        out["status"] = "unchecked"
        out["problems"].append("registry.json has no Remotion pin (tools.remotion.version).")
        return out
    if declared != pin:
        out["problems"].append(f"registry.json pins Remotion {pin}, but engine/package.json declares {declared}. "
                               "They move together, after the render-parity check.")
    candidates = []
    if os.environ.get("SLATE_ENGINE_DIR"):
        candidates.append(("env", Path(os.environ["SLATE_ENGINE_DIR"]).expanduser()))
    candidates += [("installed", paths.engine_home(pin)), ("checkout", repo / "engine")]
    running = None
    for source, folder in candidates:
        remotion = folder / "node_modules" / "remotion" / "package.json"
        if (folder / "cli.mjs").is_file() and remotion.is_file():
            running = {"source": source, "folder": str(folder), "remotion": _version(remotion)}
            break
    out["engine"] = running
    if running is None:
        # Not a fault of the update: a new Mac has no engine until `slate doctor --install` (or this) puts one there.
        out["next"].append("No Node engine is installed yet: `slate engine install --npm-ci` (or `slate doctor "
                           "--install`, which asks first).")
    elif running["remotion"] != pin:
        out["problems"].append(f"The {running['source']} engine at {running['folder']} has Remotion "
                               f"{running['remotion']}, not the pinned {pin}.")
        out["next"].append("slate engine install --npm-ci")
    if out["problems"]:
        out["status"] = "off pin"
    else:
        out["status"] = "ok" if running else "not installed"
    return out


def _relink(repo: Path, *, dry_run: bool) -> dict:
    """Make the links again with the installer from the checkout as it is now (the updated code, not this process's
    copy of the old one), when the checkout has its environment; otherwise in this process. Uses the agents the last
    install recorded, so an update never widens or narrows what was chosen."""
    from slate.install import core, layout  # noqa: PLC0415
    from slate.install import manifest as manifest_mod  # noqa: PLC0415

    agents = manifest_mod.load(layout.resolve(repo=repo)).get("agents") or None
    python = repo / ".venv" / "bin" / "python"
    if python.exists() and Path(repo).resolve() == paths.REPO.resolve():
        args = ["--no-mcp", "--no-jobs-agent", "--no-quinn-skills", "--no-doctor"]
        args += ["--dry-run"] if dry_run else []
        args += ["--agents", ",".join(agents)] if agents else []
        proc = procs.run([str(python), "-m", "slate", "install", "--json", *args], cwd=repo, timeout=600)
        from slate.install.output import first_json  # noqa: PLC0415

        parsed = first_json(proc.stdout or "")
        return parsed if isinstance(parsed, dict) else {"exit_code": proc.returncode, "tail": _tail(proc)}
    return core.install(repo=repo, agents=agents, dry_run=dry_run, mcp=False, jobs_agent=False, quinn=False,
                        doctor=False)


def run(*, repo: Path | None = None, dry_run: bool = False, doctor: bool = True, sync_env: bool = True,
        relink: bool = True) -> dict:
    """The whole update. Returns what each step did."""
    repo = Path(repo or paths.REPO)
    out: dict = {"repo": str(repo), "dry_run": dry_run, "next_steps": []}
    out["git"] = pull(repo, dry_run=dry_run)
    changed = out["git"].get("changed_files", [])
    if sync_env:
        from slate.install import env  # noqa: PLC0415

        needs_env = any(Path(f).name in ENV_FILES for f in changed) or not env.venv_python(repo).exists()
        if dry_run:
            out["env"] = {"did": "shown", "commands": [" ".join(c) for c in env.commands(repo)],
                          "why": "only when the pull changes pyproject.toml or uv.lock"}
        elif needs_env:
            out["env"] = env.sync(repo)
            if out["env"].get("warning"):
                out["next_steps"].append(out["env"]["warning"])
        else:
            out["env"] = {"did": "unchanged", "why": "the pull didn't touch pyproject.toml or uv.lock"}
    if relink:
        out["links"] = _relink(repo, dry_run=dry_run)
        for note in (out["links"].get("next_steps") or []):
            if note not in out["next_steps"]:
                out["next_steps"].append(note)
    out["engine"] = engine_check(repo)
    out["next_steps"].extend(out["engine"]["next"])
    if doctor and not dry_run:
        from slate.install import core, layout  # noqa: PLC0415

        out["doctor"] = core.doctor_step(layout.resolve(repo=repo),
                                         python=str(repo / ".venv" / "bin" / "python")
                                         if (repo / ".venv" / "bin" / "python").exists() else sys.executable)
    link_failures = (out.get("links") or {}).get("failures")
    out["ok"] = out["engine"]["status"] in ("ok", "not installed", "unchecked") and not link_failures
    return out


def _lines(res: dict) -> list[str]:
    g = res["git"]
    lines = []
    if g["did"] == "shown":
        lines.append(f"Would run: {g['command']} (branch {g['branch']}, tracking {g['upstream']}).")
    elif g["did"] == "updated":
        lines.append(f"Updated {res['repo']} from {g['before'][:10]} to {g['after'][:10]} "
                     f"({len(g['changed_files'])} files changed).")
    else:
        lines.append(f"{res['repo']} is already up to date ({g['head'][:10]} on {g['branch']}).")
    if res.get("env"):
        lines.append(f"Environment: {res['env'].get('did')}"
                     + (f" ({res['env']['why']})" if res["env"].get("why") else ""))
    links_res = res.get("links") or {}
    if links_res.get("links"):
        lk = links_res["links"]
        lines.append(f"Links: {lk.get('changed', 0)} changed, {lk.get('kept', 0)} already right.")
    eng = res["engine"]
    running = eng.get("engine") or {}
    lines.append(f"Engine: Remotion pinned {eng.get('pin')}; "
                 + (f"{running.get('source')} engine has {running.get('remotion')}" if running else "no engine found")
                 + f" ({eng['status']}).")
    lines += [f"  {p}" for p in eng["problems"]]
    doc = res.get("doctor")
    if doc:
        lines.append("Doctor: " + (f"UNCHECKED ({doc.get('why')})" if doc.get("status") == "unchecked"
                                   else f"{doc.get('status')} ({doc.get('counts')})"))
    if res.get("next_steps"):
        lines.append("Next:")
        lines += [f"  {i}. {s}" for i, s in enumerate(res["next_steps"], 1)]
    return lines


def cmd_update(args: argparse.Namespace) -> int:
    res = run(dry_run=bool(flag(args, "dry_run")), doctor=not args.no_doctor)
    log.result(res, "\n".join(_lines(res)))
    return 0 if res["ok"] else 1


def register(sub) -> None:
    choices = getattr(sub, "choices", {}) or {}
    if "update" not in choices:
        p = add_command(sub, "update", cmd_update, "pull the latest Slate and re-link it for every agent",
                        description=UPDATE_DESCRIPTION, epilog=EPILOG)
        p.add_argument("--no-doctor", action="store_true", help="skip the doctor run at the end")
    # `slate install` and `slate uninstall` live in slate.install.cli; register them here too, since the CLI's module
    # list names this module for that part of the surface. Its register() skips names that are already taken.
    from slate.install import cli as install_cli  # noqa: PLC0415

    install_cli.register(sub)
