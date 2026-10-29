"""Skill links and CLI shims: plan them, then apply the plan.

    p = links.plan(home, agents)        # what should exist, and what's at each path now
    report = links.apply(p)             # make it so; links.apply(p, dry_run=True) only says what it would do

Slate owns, and so may create, replace or remove, exactly these:

- symlinks in a skill root that point into this checkout's `skills/` (Slate's own skills, one checkout, no copies);
- symlinks in `~/.agents/skills` to the Higgsfield skills installed for Claude Code (so Codex and OpenClaw get them
  too), recorded in the install manifest;
- the two CLI shims, which carry a marker line.

Anything else sitting at one of those paths (a real folder, an old copy, someone else's symlink) is moved to a
timestamped folder under Slate's data folder, reported, and never deleted. Nothing else in a skill root is touched,
and nothing is ever linked into `~/.codex/skills` (both Codex binaries read it, so a skill there and in
`~/.agents/skills` would be listed twice).
"""

from __future__ import annotations

import os
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from slate.errors import UsageError
from slate.install import layout as layout_mod
from slate.install import manifest as manifest_mod
from slate.install.layout import Layout

SHIM_MARKER = "slate-shim v1"
HIGGSFIELD_PREFIX = "higgsfield-"

# Kinds of action and what each means.
SKILL, HIGGSFIELD, SHIM, PRUNE = "skill", "higgsfield", "shim", "prune"
# States, as found on disk right before acting.
OK, CREATE, REPLACE, BACKUP, REMOVE = "ok", "create", "replace", "backup", "remove"


@dataclass
class Action:
    kind: str
    target: Path
    source: Path | None
    root: str  # which folder it lives in, as a short label: claude-skills, agents-skills, local-bin, homebrew-bin
    state: str = ""
    detail: str = ""

    def to_dict(self, lay: Layout | None = None) -> dict:
        out = {"kind": self.kind, "target": str(self.target), "source": str(self.source) if self.source else None,
               "root": self.root, "state": self.state}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class Plan:
    layout: Layout
    agents: list[str]
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # things a person should know or do next

    def to_dict(self) -> dict:
        return {"layout": self.layout.to_dict(), "agents": list(self.agents),
                "actions": [a.to_dict(self.layout) for a in self.actions], "notes": list(self.notes),
                "counts": counts(self.actions)}

    def pending(self) -> list[Action]:
        return [a for a in self.actions if a.state != OK]


def counts(actions: list[Action]) -> dict:
    out: dict[str, int] = {}
    for a in actions:
        out[a.state] = out.get(a.state, 0) + 1
    return out


# ---------------------------------------------------------------------------------------------------------------
# What's there


def slate_skills(lay: Layout) -> list[str]:
    """Skill folders in the checkout: each has a SKILL.md of its own."""
    src = lay.skills_src
    if not src.is_dir():
        return []
    return sorted(d.name for d in src.iterdir()
                  if d.is_dir() and not d.name.startswith((".", "_")) and (d / "SKILL.md").is_file())


def higgsfield_skills(lay: Layout) -> list[tuple[str, Path]]:
    """Higgsfield's skills as installed for Claude Code (they stay global there, decision 11)."""
    root = lay.claude_skills
    if not root.is_dir():
        return []
    return [(d.name, d) for d in sorted(root.iterdir())
            if d.name.startswith(HIGGSFIELD_PREFIX) and (d / "SKILL.md").is_file()]


def _readlink(path: Path) -> Path | None:
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    target = Path(raw)
    return target if target.is_absolute() else (path.parent / target)


def _inside(path: Path, folder: Path) -> bool:
    try:
        Path(os.path.normpath(path)).relative_to(os.path.normpath(folder))
        return True
    except ValueError:
        return False


def _real(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def is_slate_link(path: Path, lay: Layout) -> bool:
    """A symlink into this checkout's skills folder."""
    if not path.is_symlink():
        return False
    target = _readlink(path)
    return target is not None and _inside(target, lay.skills_src)


def is_slate_shim(path: Path, lay: Layout | None = None) -> bool:
    """One of Slate's shims (by its marker), or an older direct link into a Slate checkout's environment."""
    if path.is_symlink():
        target = _readlink(path)
        return bool(lay and target is not None and _inside(target, lay.repo))
    if not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(512)
    except OSError:
        return False
    return SHIM_MARKER in head


def shim_text(lay: Layout) -> str:
    """The CLI shim: a few lines of sh that run this checkout's Python, so every agent runs the same code."""
    checkout = shlex.quote(str(lay.repo))
    return (
        "#!/bin/sh\n"
        f"# {SHIM_MARKER}: Slate's command-line tool, written by Slate's installer; `slate uninstall` removes it.\n"
        "# It runs the checkout below with that checkout's own Python environment, so Claude Code, Codex and OpenClaw\n"
        "# all run the same code, and `slate update` reaches every one of them at once.\n"
        f"SLATE_CHECKOUT={checkout}\n"
        "export BROWSER=none\n"
        "# python.org's framework Python ships no CA bundle; macOS's own keeps live vendor calls verifiable.\n"
        'if [ -z "${SSL_CERT_FILE:-}" ] && [ -f /etc/ssl/cert.pem ]; then export SSL_CERT_FILE=/etc/ssl/cert.pem; fi\n'
        'if [ ! -x "$SLATE_CHECKOUT/.venv/bin/python" ]; then\n'
        '  echo "slate: Slate\'s Python environment is missing from $SLATE_CHECKOUT/.venv." >&2\n'
        '  echo "  next: run $SLATE_CHECKOUT/install.sh to rebuild it." >&2\n'
        "  exit 3\n"
        "fi\n"
        'exec "$SLATE_CHECKOUT/.venv/bin/python" -m slate "$@"\n'
    )


def _manifest_owns(data: dict, target: Path) -> bool:
    """The manifest says Slate made this link, and it still points where Slate pointed it."""
    entry = manifest_mod.link_entry(data, target)
    if not entry or not target.is_symlink():
        return False
    current = _readlink(target)
    return current is not None and entry.get("source") and os.path.normpath(current) == os.path.normpath(
        entry["source"])


def inspect(action: Action, lay: Layout, data: dict | None = None) -> tuple[str, str]:
    """The state of one action's target right now: (state, why)."""
    data = data if data is not None else manifest_mod.load(lay)
    target = action.target
    exists = target.exists() or target.is_symlink()
    if action.kind == PRUNE:
        return (REMOVE, action.detail) if exists else (OK, "already gone")
    if action.kind == SHIM:
        if not exists:
            return CREATE, "not there yet"
        if target.is_file() and not target.is_symlink():
            try:
                same = target.read_text(encoding="utf-8") == shim_text(lay)
            except OSError:
                same = False
            if same and os.access(target, os.X_OK):
                return OK, "already this checkout's shim"
        if is_slate_shim(target, lay):
            return REPLACE, "an older Slate shim"
        return BACKUP, "something that isn't Slate's is there"
    # A link (Slate skill or Higgsfield skill).
    assert action.source is not None
    if not exists:
        return CREATE, "not there yet"
    if target.is_symlink():
        current = _readlink(target)
        if current is not None and os.path.normpath(current) == os.path.normpath(action.source):
            return OK, "already linked"
        real_target, real_source = _real(target), _real(action.source)
        if real_target is not None and real_target == real_source:
            return OK, "already reaches the same folder"
        owned = is_slate_link(target, lay) if action.kind == SKILL else _manifest_owns(data, target)
        if owned:
            return REPLACE, "Slate's link, pointing somewhere else"
        return BACKUP, "someone else's symlink is there"
    # A real folder or file.
    real_source = _real(action.source)
    if real_source is not None and _real(target) == real_source:
        # The source is itself a link to this very folder (e.g. a skills-CLI canonical copy): it's already there.
        return OK, "already the same folder"
    return BACKUP, "a real folder or file that isn't Slate's is there"


# ---------------------------------------------------------------------------------------------------------------
# Plan


def _search_path(lay: Layout) -> str:
    if lay.sandbox:
        return os.environ.get("PATH", "")
    from slate import procs  # noqa: PLC0415

    return procs.child_path()


def detect_agents(lay: Layout, search_path: str | None = None) -> list[str]:
    """Agents that are on this machine: their CLI is on PATH, or their settings folder exists in the home."""
    found = []
    path = _search_path(lay) if search_path is None else search_path
    for agent in layout_mod.AGENTS:
        if lay.config_dir(agent).is_dir() or shutil.which(agent, path=path):
            found.append(agent)
    return found


def chosen_agents(agents: str | list[str] | tuple[str, ...] | None, lay: Layout) -> list[str]:
    """The agents to wire: the ones on this machine when none are named. A list already chosen stays as it is, empty
    included (a Mac with no agent yet still gets Slate's command line); an empty --agents a person typed is refused."""
    if agents is None:
        return detect_agents(lay)
    if isinstance(agents, (list, tuple)) and not agents:
        return []
    return layout_mod.parse_agents(agents)


def _roots_for(agents: list[str], lay: Layout) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    if "claude" in agents:
        roots.append(("claude-skills", lay.claude_skills))
    if "codex" in agents or "openclaw" in agents:
        roots.append(("agents-skills", lay.agents_skills))
    return roots


def _guard(action: Action, lay: Layout) -> None:
    if _inside(action.target, lay.codex_skills):
        raise UsageError(f"Refusing to write {action.target}: nothing goes into ~/.codex/skills (Codex would list it "
                         "twice).")


def _stale_links(lay: Layout, skills: list[str], data: dict, planned: set[str]) -> list[Action]:
    """Slate's own links whose skill is gone from the checkout (renamed or removed upstream), and manifest-recorded
    Higgsfield links whose skill was uninstalled. Both are Slate's to tidy.

    A target this same plan already fixes is left to that action, and nothing is pruned while the checkout has no
    skills folder at all (a half-finished clone or pull shouldn't unlink every agent).
    """
    out: list[Action] = []
    if not lay.skills_src.is_dir():
        return out
    live = set(skills)
    for label, root in (("claude-skills", lay.claude_skills), ("agents-skills", lay.agents_skills)):
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if str(entry) in planned or not is_slate_link(entry, lay):
                continue
            if entry.name not in live or not entry.exists():
                out.append(Action(PRUNE, entry, _readlink(entry), label, detail="Slate's link to a skill that's gone"))
    for rec in data.get("links", []):
        target = Path(rec.get("target", ""))
        if str(target) in planned or rec.get("kind") != HIGGSFIELD:
            continue
        if target.is_symlink() and not target.exists() and _manifest_owns(data, target):
            out.append(Action(PRUNE, target, _readlink(target), "agents-skills",
                              detail="link to a Higgsfield skill that's no longer installed"))
    return out


def plan(home: str | os.PathLike | None = None, agents: list[str] | str | None = None, *,
         repo: str | os.PathLike | None = None, root: str | os.PathLike | None = None, higgsfield: bool = True,
         shims: bool = True, lay: Layout | None = None) -> Plan:
    """Everything the installer would link for these agents (default: the ones on this machine), with each target's
    current state. Reads only; `apply` does the work."""
    lay = lay or layout_mod.resolve(home, repo=repo, root=root)
    chosen = chosen_agents(agents, lay)
    data = manifest_mod.load(lay)
    p = Plan(layout=lay, agents=list(chosen))
    skills = slate_skills(lay)
    if not skills:
        p.notes.append(f"This checkout has no skills yet ({lay.skills_src} has no SKILL.md folders), so there are "
                       "no skill links to make.")
    if not chosen:
        p.notes.append("No agents found here (no claude, codex or openclaw on PATH, and no ~/.claude, ~/.codex or "
                       "~/.openclaw). Pass --agents to choose them.")
    for label, folder in _roots_for(chosen, lay):
        for name in skills:
            p.actions.append(Action(SKILL, folder / name, lay.skills_src / name, label))
    if higgsfield and ("codex" in chosen or "openclaw" in chosen):
        found = higgsfield_skills(lay)
        if found:
            for name, src in found:
                p.actions.append(Action(HIGGSFIELD, lay.agents_skills / name, src, "agents-skills"))
        else:
            p.notes.append(
                "Higgsfield's skills aren't installed for Claude Code here, so there's nothing to share with Codex "
                "and OpenClaw yet. To add them: `DISABLE_TELEMETRY=1 npx skills add higgsfield-ai/skills -g -a "
                "claude-code`, then run `slate install` again.")
    if shims:
        p.actions.append(Action(SHIM, lay.local_bin / layout_mod.CLI_NAME, lay.repo, "local-bin"))
        if lay.sandbox or lay.homebrew_bin.is_dir():
            p.actions.append(Action(SHIM, lay.homebrew_bin / layout_mod.CLI_NAME, lay.repo, "homebrew-bin"))
        else:
            p.notes.append(f"There's no {lay.homebrew_bin} on this Mac, so the second CLI link (for the OpenClaw "
                           "gateway, whose PATH lacks ~/.local/bin) is skipped.")
    p.actions.extend(_stale_links(lay, skills, data, {str(a.target) for a in p.actions}))
    for action in p.actions:
        _guard(action, lay)
        action.state, why = inspect(action, lay, data)
        if not action.detail:
            action.detail = why
    return p


# ---------------------------------------------------------------------------------------------------------------
# Apply


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def backup_path(lay: Layout, stamp: str, root_label: str, name: str) -> Path:
    base = lay.backups_dir / stamp / root_label
    candidate = base / name
    n = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = base / f"{name}.{n}"
        n += 1
    return candidate


def move_aside(path: Path, dest: Path) -> Path:
    """Move a file, folder or symlink (the link itself, never what it points to) to `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def _remove_own(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():  # pragma: no cover - inspect() never calls a real folder Slate's own
        raise UsageError(f"{path} is a folder; Slate only ever removes its own links and shims.")


def _create(action: Action, lay: Layout) -> None:
    action.target.parent.mkdir(parents=True, exist_ok=True)
    if action.kind == SHIM:
        from slate import jsonio  # noqa: PLC0415

        jsonio.write_text_atomic(action.target, shim_text(lay))
        os.chmod(action.target, 0o755)
    else:
        assert action.source is not None
        os.symlink(str(action.source), str(action.target))


def _claim_when_ok(action: Action, data: dict) -> bool:
    """Record an already-correct target in the manifest only when it's certainly Slate's.

    A Slate skill link points into this checkout and a shim carries the marker, so both are Slate's by definition. A
    correct Higgsfield link is Slate's only if the manifest already says so: an identical link a person made, or the
    skills CLI's own folder, must never be claimed, or uninstall would take it away.
    """
    if action.kind in (SKILL, SHIM):
        return True
    return action.kind == HIGGSFIELD and manifest_mod.link_entry(data, action.target) is not None


VERBS = {CREATE: ("created", "would create"), REPLACE: ("replaced", "would replace"),
         BACKUP: ("moved aside and linked", "would move aside and link"), REMOVE: ("removed", "would remove"),
         OK: ("kept", "kept")}


def apply(p: Plan, *, dry_run: bool = False) -> dict:
    """Carry out a plan. Each target is inspected again right before acting, so a plan that went stale since it was
    made can't clobber anything. Returns what happened, with every backup listed."""
    lay = p.layout
    data = manifest_mod.load(lay)
    stamp = _stamp()
    done: list[dict] = []
    backups: list[dict] = []
    errors: list[dict] = []
    for action in p.actions:
        _guard(action, lay)
        state, why = inspect(action, lay, data)
        entry = action.to_dict(lay)
        entry["state"] = state
        entry["why"] = why
        entry["did"] = VERBS[state][1 if dry_run else 0]
        if state == BACKUP:
            entry["backup"] = str(backup_path(lay, stamp, action.root, action.target.name))
        if dry_run or state == OK:
            done.append(entry)
            if state == BACKUP:
                backups.append({"original": str(action.target), "backup": entry["backup"], "dry_run": True})
            if state == OK and not dry_run and _claim_when_ok(action, data):
                manifest_mod.put_link(data, target=action.target, source=action.source, kind=action.kind)
            continue
        try:
            if state == BACKUP:
                dest = move_aside(action.target, Path(entry["backup"]))
                manifest_mod.add_backup(data, original=action.target, backup=dest, kind=action.kind)
                backups.append({"original": str(action.target), "backup": str(dest)})
            elif state in (REPLACE, REMOVE):
                _remove_own(action.target)
            if action.kind == PRUNE:
                manifest_mod.drop_link(data, action.target)
            else:
                _create(action, lay)
                manifest_mod.put_link(data, target=action.target, source=action.source, kind=action.kind)
        except OSError as exc:
            entry["did"] = "failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(entry)
        done.append(entry)
    if not dry_run:
        data["agents"] = sorted(set(data.get("agents", [])) | set(p.agents))
        manifest_mod.save(lay, data)
    changed = [d for d in done if d["state"] != OK and d.get("did") != "failed"]
    return {"dry_run": dry_run, "actions": done, "backups": backups, "errors": errors,
            "changed": len(changed), "kept": sum(1 for d in done if d["state"] == OK), "notes": list(p.notes)}


# ---------------------------------------------------------------------------------------------------------------
# Audit (read-only): for `slate install --check` and doctor


def audit(home: str | os.PathLike | None = None, agents: list[str] | str | None = None, *,
          repo: str | os.PathLike | None = None, lay: Layout | None = None) -> dict:
    """Is Slate wired into each agent? Reads only.

    Reports each Slate skill in each agent's folder, the shims, stale links, and any skill Codex would list twice
    (the same name in `~/.agents/skills` and `~/.codex/skills`: both Codex binaries read both folders).
    """
    lay = lay or layout_mod.resolve(home, repo=repo)
    chosen = chosen_agents(agents, lay)
    p = plan(lay=lay, agents=chosen, higgsfield=True)
    problems: list[str] = []
    rows = []
    for a in p.actions:
        rows.append({"kind": a.kind, "target": str(a.target), "state": a.state, "detail": a.detail})
        if a.state != OK:
            problems.append(f"{lay.label(a.target)}: {a.detail}")
    twice: list[str] = []
    if lay.agents_skills.is_dir() and lay.codex_skills.is_dir():
        agents_names = {e.name for e in lay.agents_skills.iterdir() if not e.name.startswith(".")}
        codex_names = {e.name for e in lay.codex_skills.iterdir() if not e.name.startswith(".")}
        twice = sorted(agents_names & codex_names)
    slate_in_codex = []
    if lay.codex_skills.is_dir():
        slate_in_codex = sorted(e.name for e in lay.codex_skills.iterdir() if is_slate_link(e, lay))
    for name in slate_in_codex:
        problems.append(f"~/.codex/skills/{name} is a Slate link: Codex would list it twice. Remove that link.")
    slate_names = set(slate_skills(lay))
    for name in twice:
        if name in slate_names:
            problems.append(f"{name} is in both ~/.agents/skills and ~/.codex/skills, so Codex lists it twice.")
    return {"layout": lay.to_dict(), "agents": chosen, "entries": rows, "listed_twice_by_codex": twice,
            "slate_links_in_codex_skills": slate_in_codex, "problems": problems, "ok": not problems,
            "notes": p.notes}
