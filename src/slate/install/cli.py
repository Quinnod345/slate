"""`slate install` and `slate uninstall`.

    slate install [--dry-run] [--agents claude,codex,openclaw] [--home PATH] [--yes] [--check]
    slate uninstall [--dry-run] [--home PATH] [--yes] [--keep-backups]

install.sh runs `slate install` once the Python environment exists; you can run it yourself any time (it's
idempotent). Neither command ever prompts in an agent: anything that needs a person's yes comes back as a next step.
"""

from __future__ import annotations

import argparse

from slate import log
from slate.cli import add_command, flag
from slate.install import core

EPILOG = ("Exit codes: 0 ok, 1 something failed or needs attention (it's listed), 2 usage, 3 missing prerequisite.\n"
          "A --home other than yours is a sandbox: links and shims go inside it, and anything that would reach this "
          "Mac's real session (MCP registration, the launchd job runner) is shown, not done.")

INSTALL_DESCRIPTION = """Wire Slate into every agent on this Mac, from this one checkout (no copies):

  skills   Slate's skills, symlinked into ~/.claude/skills (Claude Code) and ~/.agents/skills (Codex and
           OpenClaw); Higgsfield's installed skills linked into ~/.agents/skills too. Never ~/.codex/skills:
           both Codex binaries read it, so a skill there would be listed twice.
  CLI      a small shim at ~/.local/bin/slate and /opt/homebrew/bin/slate (the OpenClaw gateway's PATH
           lacks ~/.local/bin) that runs this checkout's own environment.
  MCP      `slate mcp` registered with Claude Code, Codex and OpenClaw, where each is installed.
  jobs     the launchd job runner, a login item, loaded only with a person's yes (--yes).
  doctor   runs last and lists anything still missing.

Anything in the way that isn't Slate's is moved to a dated backup folder and reported, never deleted. Every
other skill stays exactly as it is. Run it again whenever you like: a second run changes nothing."""

UNINSTALL_DESCRIPTION = """Undo what the installer did, and only that: Slate's skill links, the Higgsfield links it made,
the CLI shims, its MCP registrations and its job runner. Anything it moved aside goes back where it was, when that
spot is free. Your other skills, this checkout, films, brand packs and Slate's data folder all stay."""


def cmd_install(args: argparse.Namespace) -> int:
    dry_run = bool(flag(args, "dry_run"))
    if args.check:
        res = core.check(home=args.home, agents=args.agents)
        lines = ["Slate's wiring: " + ("all good." if res["ok"] else f"{len(res['problems'])} to fix.")]
        lines += [f"  {p}" for p in res["problems"]]
        if res["listed_twice_by_codex"]:
            lines.append("  Codex lists these twice (in both ~/.agents/skills and ~/.codex/skills): "
                         + ", ".join(res["listed_twice_by_codex"]))
        lines += [f"  note: {n}" for n in res.get("notes", [])]
        log.result(res, "\n".join(lines))
        return 0 if res["ok"] else 1
    res = core.install(home=args.home, agents=args.agents, dry_run=dry_run, yes=args.yes,
                       interactive=args.interactive, higgsfield=not args.no_higgsfield, mcp=not args.no_mcp,
                       jobs_agent=not args.no_jobs_agent, quinn=not args.no_quinn_skills, doctor=not args.no_doctor,
                       exclude_agents=args.exclude_agent)
    if not dry_run:
        core.write_last_result(res["layout"], res)
    log.result(res, "\n".join(core.summary_lines(res)))
    return 0 if res["ok"] else 1


def cmd_uninstall(args: argparse.Namespace) -> int:
    dry_run = bool(flag(args, "dry_run"))
    res = core.uninstall(home=args.home, dry_run=dry_run, yes=args.yes, restore_backups=not args.keep_backups)
    verb = "Would remove" if dry_run else "Removed"
    lines = [f"{verb} Slate's wiring from {res['layout']['home']}:"]
    lines += [f"  {r['did']}: {r['target']}" for r in res["removed"]] or ["  (no Slate links or shims found)"]
    lines += [f"  {r['did']}: {r['original']} (from {r['backup']})" for r in res["restored"]]
    lines += [f"  left alone: {r['target']} ({r['why']})" for r in res["left_alone"]]
    for m in res.get("mcp", []):
        lines.append(f"  MCP for {m['client']}: {m['did']}" + (f" ({m['why']})" if m.get("why") else "")
                     + (f": {m['command']}" if m["did"] == "shown" else ""))
    if res.get("jobs_agent"):
        lines.append(f"  job runner: {res['jobs_agent'].get('did')}"
                     + (f". Next: {res['jobs_agent']['next']}" if res["jobs_agent"].get("next") else ""))
    lines.append("Kept: " + "; ".join(res["kept"]) + ".")
    log.result(res, "\n".join(lines))
    return 0 if res["ok"] else 1


def register(sub) -> None:
    """Add `install` and `uninstall` (slate.cli.MODULES lists this module for both)."""
    p = add_command(sub, "install", cmd_install, "wire Slate into every agent here: skills, CLI, MCP, job runner",
                    description=INSTALL_DESCRIPTION, epilog=EPILOG)
    p.add_argument("--agents", metavar="LIST", default=None,
                   help="which agents: claude,codex,openclaw (default: the ones on this Mac)")
    p.add_argument("--home", metavar="PATH", default=None,
                   help="install into this home instead of yours (a sandbox; see below)")
    p.add_argument("--yes", action="store_true",
                   help="the person said yes: load the job runner (a login item) and, when visionary and "
                        "goal-fidelity are here, set them up for every personal agent")
    p.add_argument("--exclude-agent", action="append", default=[], metavar="ID",
                   help="an OpenClaw agent that must never get visionary and goal-fidelity (repeatable)")
    p.add_argument("--check", action="store_true", help="check the wiring only; change nothing")
    p.add_argument("--no-higgsfield", action="store_true", help="don't link Higgsfield's skills for Codex/OpenClaw")
    p.add_argument("--no-mcp", action="store_true", help="don't register the MCP server")
    p.add_argument("--no-jobs-agent", action="store_true", help="leave the launchd job runner alone")
    p.add_argument("--no-quinn-skills", action="store_true", help="don't offer the visionary/goal-fidelity setup")
    p.add_argument("--no-doctor", action="store_true", help="skip the doctor run at the end")
    p.add_argument("--interactive", action="store_true", help=argparse.SUPPRESS)  # install.sh, in a real Terminal

    p = add_command(sub, "uninstall", cmd_uninstall, "undo the installer's links, shims, MCP entries and runner",
                    description=UNINSTALL_DESCRIPTION, epilog=EPILOG)
    p.add_argument("--home", metavar="PATH", default=None, help="the home to uninstall from (default: yours)")
    p.add_argument("--yes", action="store_true", help="the person said yes (removing the job runner)")
    p.add_argument("--keep-backups", action="store_true",
                   help="leave what the installer moved aside in its backup folder")
