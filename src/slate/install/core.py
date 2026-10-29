"""Installing, checking and uninstalling Slate for every agent on this Mac.

`install()` is what install.sh, `slate install` and `slate update` all run after the environment exists:

1. skill links and CLI shims (links.plan + links.apply): Slate's skills into `~/.claude/skills` and
   `~/.agents/skills`, Higgsfield's installed skills into `~/.agents/skills`, the shim into `~/.local/bin` and
   `/opt/homebrew/bin`;
2. the MCP server, registered with each agent that's here;
3. the launchd job agent (a login item, so it waits for a person's yes);
4. an offer to set up visionary and goal-fidelity (the owner's yes, once);
5. `slate doctor`.

It never prompts unless the caller asked for it (install.sh does, in a real Terminal) and never in an agent: a question
becomes a printed next step. A sandbox home (see layout.py) shows the steps that would reach this Mac's real session.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Iterator

from slate import jsonio, procs
from slate.install import layout as layout_mod
from slate.install import links, mcp_register, quinn_skills
from slate.install.output import first_json
from slate.install import manifest as manifest_mod
from slate.install.layout import Layout

JOB_AGENT_QUESTION = ("Load Slate's job runner as a login item? It keeps long renders going after the agent that "
                      "started them has gone, and you can remove it any time. [Y/n] ")
QUINN_QUESTION = ("Set up visionary and goal-fidelity in every personal agent (their own small repo in "
                  "~/code/quinn-skills, symlinked; the originals are backed up, never deleted)? [y/N] ")


# ---------------------------------------------------------------------------------------------------------------
# Small helpers


def can_ask(interactive: bool) -> bool:
    """Ask only when the caller wants questions, a person has a terminal, and no agent is running us."""
    if not interactive or procs.agent_context() or os.environ.get("SLATE_NONINTERACTIVE"):
        return False
    try:
        with open("/dev/tty", "r+"):
            return True
    except OSError:
        return False


def ask(question: str, *, default: bool = False) -> bool:
    """A yes/no question on the terminal itself (never stdin: under `curl | bash`, stdin is the script)."""
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(question)
            tty.flush()
            answer = tty.readline().strip().lower()
    except OSError:
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


@contextlib.contextmanager
def _env(**values: str | None) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _path_has(folder: Path) -> bool:
    return str(folder) in os.environ.get("PATH", "").split(os.pathsep)


# ---------------------------------------------------------------------------------------------------------------
# Steps


def jobs_agent_step(lay: Layout, *, dry_run: bool, yes: bool, interactive: bool) -> dict:
    """Load the launchd job agent through `slate jobs agent install` (slate.jobs.agent), with a person's yes."""
    try:
        from slate.jobs import agent as job_agent  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - a broken jobs module mustn't stop the install
        return {"did": "skipped", "why": f"the job queue module didn't load ({type(exc).__name__}: {exc})"}
    if lay.sandbox or dry_run:
        redirect = str(lay.launch_agents) if lay.sandbox else None
        with _env(SLATE_LAUNCH_AGENTS=redirect):
            info = job_agent.install(dry_run=True)
        info.pop("plist_xml", None)
        info["did"] = "shown"
        info["why"] = ("sandbox home: launchd would load it into this Mac's real login session"
                       if lay.sandbox else "dry run")
        return info
    status = job_agent.status()
    if status.get("loaded") and Path(status["plist"]).is_file() \
            and Path(status["plist"]).read_bytes() == job_agent.render_bytes():
        return {"did": "already loaded", **status}
    if not yes and can_ask(interactive):
        yes = ask(JOB_AGENT_QUESTION, default=True)
    if not yes:
        return {"did": "waiting for a yes", **status,
                "next": "Loading the job runner adds a login item, so it waits for a person's yes. Ask, then run "
                        "`slate jobs agent install --yes` (or `slate install --yes`). Until then the queue still "
                        "works: `slate job submit` starts a one-shot runner itself."}
    info = job_agent.install(yes=True)
    info["did"] = "loaded" if info.get("loaded") else "installed, not loaded"
    return info


def quinn_step(lay: Layout, *, dry_run: bool, yes: bool, interactive: bool,
               exclude_agents: tuple[str, ...] | list[str]) -> dict:
    """Offer the owner's visionary and goal-fidelity setup, once; run it only after the owner's yes."""
    st = quinn_skills.status(lay)
    if not st["available"]:
        return {**st, "did": "not offered", "why": "visionary and goal-fidelity aren't on this machine"}
    if st["set_up"]:
        return {**st, "did": "already set up"}
    base = " ".join(quinn_skills.command(lay, exclude_agents=exclude_agents)[1:])
    if dry_run:
        res = quinn_skills.run(lay, exclude_agents=exclude_agents, dry_run=True)
        return {**st, "did": "shown", "command": base, "output": res.get("output")}
    if not yes and can_ask(interactive):
        yes = ask(QUINN_QUESTION, default=False)
    if not yes:
        return {**st, "did": "waiting for the owner's yes",
                "next": "visionary and goal-fidelity can live in their own small repo, linked into every personal "
                        f"agent. With the owner's yes, run: {base} (add --exclude-agent <id> for each company "
                        "OpenClaw agent that must never get them)."}
    res = quinn_skills.run(lay, exclude_agents=exclude_agents, interactive=can_ask(interactive))
    after = quinn_skills.status(lay)
    return {**after, "did": "set up" if res["exit_code"] == 0 else "needs attention", "command": res["command"],
            "exit_code": res["exit_code"], "output": res.get("output")}


def _checks_from(obj) -> list[dict]:
    if isinstance(obj, list):
        return [c for c in obj if isinstance(c, dict)]
    if isinstance(obj, dict):
        for key in ("checks", "results", "items"):
            if isinstance(obj.get(key), list):
                return [c for c in obj[key] if isinstance(c, dict)]
    return []


def doctor_step(lay: Layout, *, python: str | None = None, timeout: float = 600) -> dict:
    """Run `slate doctor --json` for this home and summarize it. UNCHECKED, never passed, when doctor isn't there."""
    if importlib.util.find_spec("slate.doctor") is None:
        return {"status": "unchecked", "why": "the doctor module isn't in this checkout yet, so nothing was checked",
                "command": "slate doctor"}
    argv = [python or sys.executable, "-m", "slate", "doctor", "--json"]
    env = {"HOME": str(lay.home)}
    if lay.sandbox:
        env["SLATE_DATA"] = str(lay.data)
    try:
        proc = procs.run(argv, env=env, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the install
        return {"status": "unchecked", "why": f"doctor didn't finish: {exc}", "command": "slate doctor"}
    if proc.returncode == 2 and "invalid choice: 'doctor'" in (proc.stderr or ""):
        # The module is there but its command isn't wired into the CLI yet (a half-built checkout).
        return {"status": "unchecked", "why": "`slate doctor` isn't a command in this checkout yet, so nothing was "
                                              "checked", "command": "slate doctor"}
    text = proc.stdout or ""
    parsed = first_json(text)  # Vision can print after the report when the ocr extra is in; see output.py
    checks = _checks_from(parsed)
    if not checks:
        return {"status": "unchecked", "why": "doctor didn't return checks", "exit_code": proc.returncode,
                "tail": (proc.stderr or text)[-800:], "command": "slate doctor"}
    tally: dict[str, int] = {}
    for c in checks:
        tally[str(c.get("status"))] = tally.get(str(c.get("status")), 0) + 1
    reds = [{k: c.get(k) for k in ("id", "summary", "fix")} for c in checks if c.get("status") == "red"]
    yellows = [{k: c.get(k) for k in ("id", "summary", "fix")} for c in checks if c.get("status") == "yellow"]
    return {"status": "red" if reds else ("yellow" if yellows else "green"), "counts": tally, "red": reds,
            "yellow": yellows, "exit_code": proc.returncode, "command": "slate doctor", "checks": checks}


# ---------------------------------------------------------------------------------------------------------------
# The three entry points


def install(*, home: str | os.PathLike | None = None, agents: list[str] | str | None = None, dry_run: bool = False,
            yes: bool = False, interactive: bool = False, higgsfield: bool = True, mcp: bool = True,
            jobs_agent: bool = True, quinn: bool = True, doctor: bool = True,
            exclude_agents: tuple[str, ...] | list[str] = (), repo: str | os.PathLike | None = None,
            root: str | os.PathLike | None = None, client_path: str | None = None,
            run_clients: bool | None = None) -> dict:
    """Wire Slate into every agent here. Idempotent: a second run changes nothing and says so.

    `client_path` and `run_clients` exist for tests (fake agent CLIs in a temp folder); leave them alone.
    """
    lay = layout_mod.resolve(home, repo=repo, root=root)
    chosen = links.chosen_agents(agents, lay)
    out: dict = {"layout": lay.to_dict(), "agents": chosen, "dry_run": dry_run, "sandbox": lay.sandbox,
                 "next_steps": []}
    p = links.plan(lay=lay, agents=chosen, higgsfield=higgsfield)
    out["links"] = links.apply(p, dry_run=dry_run)
    out["next_steps"].extend(p.notes)
    if not lay.sandbox and not _path_has(lay.local_bin):
        out["next_steps"].append(f"{lay.local_bin} isn't on your PATH, so a Terminal won't find `slate` there. Add "
                                 "`export PATH=\"$HOME/.local/bin:$PATH\"` to your ~/.zprofile.")
    if mcp:
        run = (not dry_run and not lay.sandbox) if run_clients is None else (run_clients and not dry_run)
        entries = mcp_register.plan(chosen, lay, path=client_path, may_run=not lay.sandbox or bool(run_clients))
        out["mcp"] = mcp_register.apply(entries, lay, run=run)
        for e in out["mcp"]:
            if e.get("next"):
                out["next_steps"].append(e["next"])
            if e.get("did") == "shown" and lay.sandbox:
                e["why"] = "sandbox home: the agents' own CLIs would change this Mac's real settings"
    if jobs_agent:
        out["jobs_agent"] = jobs_agent_step(lay, dry_run=dry_run, yes=yes, interactive=interactive)
        if out["jobs_agent"].get("next"):
            out["next_steps"].append(out["jobs_agent"]["next"])
    if quinn:
        out["quinn_skills"] = quinn_step(lay, dry_run=dry_run, yes=yes, interactive=interactive,
                                         exclude_agents=exclude_agents)
        if out["quinn_skills"].get("next"):
            out["next_steps"].append(out["quinn_skills"]["next"])
    if doctor and not dry_run:
        out["doctor"] = doctor_step(lay)
        if out["doctor"].get("status") in ("red", "yellow"):
            out["next_steps"].append("Doctor listed what's still missing. In your own Terminal, `slate doctor "
                                     "--install` installs the free tools (it asks before each one); keys go in with "
                                     "`slate connect elevenlabs` and `slate connect higgsfield --api-key`.")
    failures = list(out["links"]["errors"]) + [e for e in out.get("mcp", []) if e.get("did") == "failed"]
    out["ok"] = not failures
    out["failures"] = failures
    return out


def wire(agents: list[str] | str, *, home: str | os.PathLike | None = None, dry_run: bool = False) -> dict:
    """Links, shims and MCP for the named agents only: what `slate connect claude` needs. No job agent, no doctor."""
    return install(home=home, agents=agents, dry_run=dry_run, jobs_agent=False, quinn=False, doctor=False)


def check(*, home: str | os.PathLike | None = None, agents: list[str] | str | None = None,
          repo: str | os.PathLike | None = None, client_path: str | None = None) -> dict:
    """Is Slate wired into every agent? Reads only; runs no agent CLI in a sandbox."""
    lay = layout_mod.resolve(home, repo=repo)
    res = links.audit(lay=lay, agents=agents)
    chosen = res["agents"]
    res["mcp"] = mcp_register.plan(chosen, lay, path=client_path, may_run=not lay.sandbox)
    words = {mcp_register.ADD: "Slate's MCP server isn't registered yet (`slate install` does it)",
             mcp_register.UPDATE: "Slate's MCP server is registered with an older command (`slate install` updates it)"}
    for e in res["mcp"]:
        if e["state"] in words:
            res["problems"].append(f"{e['client']}: {words[e['state']]}.")
        elif e["state"] == mcp_register.CONFLICT:
            res["problems"].append(f"{e['client']}: {e['next']}")
        elif e["state"] == mcp_register.NO_CLIENT:
            res["notes"].append(f"{e['client']} has settings here but its CLI isn't on PATH, so its MCP entry wasn't "
                                "checked.")
        elif e["state"] == mcp_register.UNKNOWN:
            why = (e.get("current") or {}).get("unknown") or "its settings couldn't be read"
            res["notes"].append(f"{e['client']}: its MCP entry wasn't checked ({why}). Unchecked isn't the same as "
                                "registered: `slate doctor` asks each agent from the real home.")
    res["quinn_skills"] = quinn_skills.status(lay)
    res["ok"] = not res["problems"]
    return res


def uninstall(*, home: str | os.PathLike | None = None, dry_run: bool = False, yes: bool = False,
              restore_backups: bool = True, mcp: bool = True, jobs_agent: bool = True,
              repo: str | os.PathLike | None = None, root: str | os.PathLike | None = None,
              client_path: str | None = None, run_clients: bool | None = None) -> dict:
    """Reverse the installer, and only the installer: Slate's links and shims, the Higgsfield links it made, its MCP
    registrations and its job agent. Backups it made go back where they came from when that spot is free again.

    Other skills, the checkout and its environment, films, brand packs and Slate's data folder all stay.
    """
    lay = layout_mod.resolve(home, repo=repo, root=root)
    data = manifest_mod.load(lay)
    mcp_results = None
    if mcp:
        run = (not dry_run and not lay.sandbox) if run_clients is None else (run_clients and not dry_run)
        # Check registration ownership while its launcher still exists, before removing Slate's own shims.
        mcp_results = mcp_register.unregister(list(mcp_register.CLIENTS), lay, run=run, path=client_path)
        # unregister saves its own snapshot; retain successful removals in the link/backup snapshot too.
        for entry in mcp_results:
            if entry["did"] == "removed":
                manifest_mod.drop_mcp(data, entry["client"])
    removed: list[dict] = []
    left: list[dict] = []
    targets: list[tuple[Path, str]] = []
    for label, folder in (("claude-skills", lay.claude_skills), ("agents-skills", lay.agents_skills)):
        if folder.is_dir():
            targets += [(entry, "skill") for entry in sorted(folder.iterdir()) if links.is_slate_link(entry, lay)]
    for rec in data.get("links", []):
        t = Path(rec.get("target", ""))
        if rec.get("kind") == links.HIGGSFIELD and all(t != x for x, _ in targets):
            if links._manifest_owns(data, t):
                targets.append((t, "higgsfield"))
            elif t.exists() or t.is_symlink():
                left.append({"target": str(t), "why": "it isn't the link Slate made any more"})
    for shim in {lay.local_bin / layout_mod.CLI_NAME, lay.homebrew_bin / layout_mod.CLI_NAME,
                 *(Path(r["target"]) for r in data.get("links", []) if r.get("kind") == links.SHIM)}:
        if links.is_slate_shim(shim, lay):
            targets.append((shim, "shim"))
        elif shim.exists() or shim.is_symlink():
            left.append({"target": str(shim), "why": "it isn't a Slate shim"})
    for path, kind in targets:
        entry = {"target": str(path), "kind": kind, "did": "would remove" if dry_run else "removed"}
        if not dry_run:
            try:
                path.unlink()
                manifest_mod.drop_link(data, path)
            except OSError as exc:
                entry["did"] = "failed"
                entry["error"] = str(exc)
        removed.append(entry)
    restored: list[dict] = []
    if restore_backups:
        for rec in reversed(data.get("backups", [])):
            if rec.get("restored"):
                continue
            original, backup = Path(rec["original"]), Path(rec["backup"])
            if not (backup.exists() or backup.is_symlink()):
                continue
            occupied = original.exists() or original.is_symlink()
            if occupied and dry_run:
                # In a dry run the link is still there; it counts as free only if this uninstall would remove it.
                occupied = not any(str(original) == r["target"] and r["did"] == "would remove" for r in removed)
            if occupied:
                restored.append({"original": str(original), "backup": str(backup), "did": "kept in backups",
                                 "why": "something new is at the original spot"})
                continue
            item = {"original": str(original), "backup": str(backup),
                    "did": "would put back" if dry_run else "put back"}
            if not dry_run:
                links.move_aside(backup, original)
                rec["restored"] = True
            restored.append(item)
    out: dict = {"layout": lay.to_dict(), "dry_run": dry_run, "removed": removed, "left_alone": left,
                 "restored": restored}
    if mcp_results is not None:
        out["mcp"] = mcp_results
    if jobs_agent:
        out["jobs_agent"] = _jobs_agent_uninstall(lay, dry_run=dry_run, yes=yes)
    if not dry_run:
        data["agents"] = []
        manifest_mod.save(lay, data)
    out["kept"] = ["your other skills", f"the checkout ({lay.repo}) and its environment", "films", "brand packs",
                   f"Slate's data folder ({lay.data})"]
    out["ok"] = not any(r.get("did") == "failed" for r in removed) and not any(
        m.get("did") == "failed" for m in out.get("mcp", []))
    return out


def _jobs_agent_uninstall(lay: Layout, *, dry_run: bool, yes: bool) -> dict:
    try:
        from slate.jobs import agent as job_agent  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"did": "skipped", "why": f"the job queue module didn't load ({type(exc).__name__})"}
    if lay.sandbox or dry_run:
        with _env(SLATE_LAUNCH_AGENTS=str(lay.launch_agents) if lay.sandbox else None):
            info = job_agent.uninstall(dry_run=True)
        info["did"] = "shown"
        return info
    status = job_agent.status()
    if not status.get("installed") and not status.get("loaded"):
        return {"did": "not installed", **status}
    if procs.agent_context() and not yes:
        return {"did": "waiting for a yes", **status,
                "next": "Removing the job runner changes a login item: ask, then run `slate jobs agent uninstall "
                        "--yes`."}
    info = job_agent.uninstall(yes=True)
    info["did"] = "removed"
    return info


def summary_lines(result: dict) -> list[str]:
    """Plain words for `slate install`'s result (the CLI prints these without --json)."""
    lay = result["layout"]
    home = lay["home"].rstrip("/")

    def short(path: str | None) -> str:
        if not path:
            return ""
        return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path

    lines = []
    where = "the sandbox home " + lay["home"] if lay["sandbox"] else "your home"
    verb = "Would install" if result.get("dry_run") else "Installed"
    lines.append(f"{verb} Slate from {short(lay['checkout'])} into {where}"
                 + (f" for {', '.join(result['agents'])}." if result.get("agents") else " (no agents found)."))
    link = result["links"]
    for a in link["actions"]:
        if a["state"] == links.OK:
            continue
        if a["kind"] == links.SHIM:
            line = f"  {a['did']}: {short(a['target'])} (a shim that runs {short(a['source'])})"
        elif a["kind"] == links.PRUNE:
            line = f"  {a['did']}: {short(a['target'])} ({a.get('detail', 'stale')})"
        else:
            line = f"  {a['did']}: {short(a['target'])} -> {short(a['source'])}"
        if a.get("backup"):
            line += (f"\n      what was there {'would move' if result.get('dry_run') else 'moved'} to "
                     f"{short(a['backup'])} (kept, not deleted)")
        lines.append(line)
    lines.append(f"  links and shims: {link['changed']} {'to change' if result.get('dry_run') else 'changed'}, "
                 f"{link['kept']} already right"
                 + (f", {len(link['backups'])} moved aside" if link["backups"] else "") + ".")
    for e in result.get("mcp", []):
        if e["did"] == "shown":
            # Exact paths: each client saves the command as given, and a `~` isn't expanded inside quotes, so a copied
            # command must be literal.
            cmds = e["commands"] or [mcp_register.show(e["add"])]
            lines.append(f"  MCP for {e['client']}: shown, not run: " + " && ".join(cmds))
        elif e["did"] == "skipped":
            lines.append(f"  MCP for {e['client']}: skipped ({e['client']} isn't on PATH here)")
        else:
            lines.append(f"  MCP for {e['client']}: {e['did']}")
    ja = result.get("jobs_agent")
    if ja:
        extra = ""
        if ja.get("did") == "shown":
            extra = f" ({ja.get('why')}): " + " && ".join(ja.get("commands", []))
        elif ja.get("why"):
            extra = f" ({ja['why']})"
        lines.append(f"  job runner: {ja.get('did')}{extra}")
    qs = result.get("quinn_skills")
    if qs:
        lines.append(f"  visionary and goal-fidelity: {qs.get('did')}" + (f" ({qs['why']})" if qs.get("why") else ""))
    doc = result.get("doctor")
    if doc:
        if doc.get("status") == "unchecked":
            lines.append(f"  doctor: UNCHECKED ({doc.get('why')})")
        else:
            counts = ", ".join(f"{v} {k}" for k, v in sorted(doc.get("counts", {}).items()))
            lines.append(f"  doctor: {doc['status']} ({counts})")
            for r in doc.get("red", []):
                lines.append(f"    red: {r.get('id')}: {r.get('summary')}" + (f" (fix: {r['fix']})" if r.get("fix")
                                                                               else ""))
    for f in result.get("failures", []):
        lines.append(f"  FAILED: {f.get('target') or f.get('client')}: {f.get('error')}")
    if result.get("next_steps"):
        lines.append("Next:")
        lines += [f"  {i}. {s}" for i, s in enumerate(result["next_steps"], 1)]
    return lines


def write_last_result(lay_dict: dict, result: dict) -> None:
    """Keep the last install result next to the manifest, for doctor and for anyone asking "what did it do?"."""
    try:
        path = Path(lay_dict["data"]) / "install" / "last.json"
        slim = {k: v for k, v in result.items() if k != "doctor"}
        if result.get("doctor"):
            slim["doctor"] = {k: v for k, v in result["doctor"].items() if k != "checks"}
        jsonio.write_atomic(path, slim)
    except OSError:
        pass
