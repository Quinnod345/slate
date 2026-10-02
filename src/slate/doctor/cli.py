"""`slate doctor` and `slate connect`."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from typing import Iterator

from slate import log
from slate.cli import add_command, add_group, dry_stop, flag
from slate.doctor import connect, core, prereqs
from slate.doctor import rows as R

DOCTOR_DESCRIPTION = """Check everything Slate needs on this Mac, and say how to fix what's missing.

Every row is green (checked, and right), yellow (Slate works, but something should be fixed or finished) or red
(something Slate needs is missing or broken). The overall status is green only when every row is. Doctor only reads:
it never changes a setting, never launches After Effects, never opens a browser, and checks keys by presence only.

Run it from each place Slate runs, and label each run with --context: a Terminal, Claude Code's shell, a Codex research
slice, the OpenClaw gateway. The format is the same everywhere, and each labelled run is saved to
Slate's data folder as doctor/<NAME>.json."""

DOCTOR_EPILOG = """Examples:
  slate doctor
  slate doctor --json --context claude-code
  slate doctor --brand demo              also check that brand's fonts
  slate doctor --install                 in your own Terminal: set up the free prerequisites, asking before each
  slate doctor --only aftereffects --only keys

Exit codes: 0 green or yellow (1 for yellow with --strict), 3 red (something Slate needs is missing), 1 refused (not
macOS), 2 usage. `--install` from an agent prints the commands and exits 3: nothing ever prompts an agent."""

CONNECT_DESCRIPTION = """Connect Slate to Claude Code (and Codex and OpenClaw), Higgsfield, ElevenLabs and After Effects.

Keys are only ever typed by a person, into macOS's own prompt in their own Terminal, and go straight into the login
Keychain. Slate never opens a browser: the Higgsfield sign-in is printed for you to run yourself."""

EPILOG = "Exit codes: 0 ok, 1 refused or failed, 2 usage, 3 missing prerequisite (the output says what to do)."


# --- Keeping `--json` to one JSON object on stdout ---------------------------------------------------------------
#
# With Slate's ocr extra installed, Apple's Vision framework writes log lines ("Unable to find a valid E5 …") straight
# to file descriptor 1, underneath Python, some of them after the report has been printed. Under --json that breaks
# the promise of one JSON object on stdout, and every agent parsing it. So while the checks run, and after the report
# is out, fd 1 points at stderr. This only happens when stdout is the real process stdout: in-process callers
# (slate.cli.run_cli, tests) swap sys.stdout for a buffer and are left alone.


def _real_stdout() -> bool:
    try:
        return sys.stdout is sys.__stdout__ and sys.__stdout__ is not None and sys.__stdout__.fileno() == 1
    except (AttributeError, OSError, ValueError):
        return False


@contextlib.contextmanager
def _native_writes_to_stderr(active: bool) -> Iterator[None]:
    """While the checks run, anything written to fd 1 below Python goes to stderr instead."""
    if not (active and _real_stdout()):
        yield
        return
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


def _stdout_closed_after_report(active: bool) -> None:
    """The JSON object is out: point fd 1 at stderr for good, so late native writes (at exit) can't follow it."""
    if active and _real_stdout():
        sys.stdout.flush()
        os.dup2(2, 1)


def cmd_doctor(args: argparse.Namespace) -> int:
    json_out = log.json_mode()
    code = _doctor(args)
    # Only after a report was printed: a refusal raised above still reaches stdout as its own JSON error object.
    _stdout_closed_after_report(json_out)
    return code


def _doctor(args: argparse.Namespace) -> int:
    name = args.context
    if name:
        core.check_context_name(name)
    keep = name if not flag(args, "dry_run", False) else None  # a dry run saves no context report
    with _native_writes_to_stderr(log.json_mode()):
        rep = core.report(name, brand=args.brand, only=args.only)
    if rep.get("refused"):
        # Off macOS there's nothing else to check, and nothing to install.
        if keep:
            rep["saved"] = str(core.save(rep, keep))
        log.result(rep, R.format_report(rep))
        return 1
    if args.install:
        return _install(args, rep)
    if keep:
        rep["saved"] = str(core.save(rep, keep))
    log.result(rep, R.format_report(rep))
    return R.exit_code(rep["status"], strict=args.strict)


def _install(args: argparse.Namespace, rep: dict) -> int:
    env = core.make_env()
    items = prereqs.plan(rep["checks"])
    listing = "\n".join(f"  {it['what']}" + (f" ({it['size']})" if it.get("size") else "")
                        + f"\n      {prereqs.command_text(it)}" + (f"\n      can't run yet: {it['needs']}"
                                                                   if it.get("needs") else "") for it in items)
    if not items:
        rep["install"] = {"items": [], "ran": False}
        if args.context and not flag(args, "dry_run", False):
            rep["saved"] = str(core.save(rep, args.context))
        log.result(rep, R.format_report(rep) + "\n\nNothing to install: every free prerequisite doctor can set up is "
                                               "already here.")
        return R.exit_code(rep["status"], strict=args.strict)
    at_terminal, why = env.person_at_terminal()
    if flag(args, "dry_run", False) or not at_terminal:
        rep["install"] = {"items": [prereqs.public(it) for it in items], "ran": False,
                          "why": "dry run" if flag(args, "dry_run", False) else why,
                          "next": "Open Terminal and run: slate doctor --install (it asks before each one; nothing is "
                                  "installed without your yes)."}
        if args.context and not flag(args, "dry_run", False):
            rep["saved"] = str(core.save(rep, args.context))
        head = ("Dry run: nothing was installed." if flag(args, "dry_run", False) else
                f"Nothing was installed: installs are only ever run for a person at their own Terminal ({why}).")
        log.result(rep, f"{R.format_report(rep)}\n\n{head} These are the free prerequisites doctor would offer, each with "
                        f"its command:\n{listing}\n\nOpen Terminal and run: slate doctor --install (it asks before each "
                        "one).")
        return 0 if flag(args, "dry_run", False) else 3
    log.info(f"{len(items)} free prerequisite{'s' if len(items) != 1 else ''} to offer. Each one asks first.")
    results = prereqs.interactive(items, env)
    with _native_writes_to_stderr(log.json_mode()):
        after = core.report(args.context, brand=args.brand, only=args.only)
    after["install"] = {"items": [prereqs.public(it) for it in items], "ran": True, "results": results}
    if args.context:
        after["saved"] = str(core.save(after, args.context))
    failed = [r for r in results if r.get("ok") is False]
    done = [r["id"] for r in results if r.get("ok")]
    skipped = [r["id"] for r in results if r.get("skipped")]
    tail = [f"Installed: {', '.join(done) or 'nothing'}.", f"Skipped: {', '.join(skipped) or 'nothing'}."]
    tail += [f"Failed: {r['id']}: {r.get('error')}" for r in failed]
    log.result(after, R.format_report(after) + "\n\n" + "\n".join(tail))
    if failed:
        return 1
    return R.exit_code(after["status"], strict=args.strict)


def _finish(code: int, result: dict, human: str) -> int:
    log.result(result, human)
    return code


def cmd_connect_claude(args: argparse.Namespace) -> int:
    return _finish(*connect.claude(core.make_env(), yes=args.yes, dry_run=bool(flag(args, "dry_run", False))))


def cmd_connect_higgsfield(args: argparse.Namespace) -> int:
    if args.api_key and not args.check and dry_stop(
            args, "ask for the Higgsfield Cloud API key in macOS's own prompt and store it in the login Keychain, "
                  "replacing the one there (--check tests the stored key without changing it)"):
        return 0
    return _finish(*connect.higgsfield(core.make_env(), api_key=args.api_key, check=args.check))


OWNERS = {False: "your", True: "Oneiro's capped"}


def cmd_connect_elevenlabs(args: argparse.Namespace) -> int:
    if not args.check and dry_stop(args, f"ask for {OWNERS[bool(args.oneiro)]} "
                                         "ElevenLabs key in macOS's own prompt and store it in the login Keychain, "
                                         "replacing the one there (--check tests the stored key without changing it)"):
        return 0
    return _finish(*connect.elevenlabs(core.make_env(), oneiro=args.oneiro, check=args.check))


def cmd_connect_aftereffects(args: argparse.Namespace) -> int:
    return _finish(*connect.aftereffects(core.make_env(), go=args.go, brand=args.brand,
                                         dry_run=bool(flag(args, "dry_run", False))))


def register(sub) -> None:
    p = add_command(sub, "doctor", cmd_doctor, "check everything Slate needs on this Mac, and how to fix what's missing",
                    description=DOCTOR_DESCRIPTION, epilog=DOCTOR_EPILOG)
    p.add_argument("--install", action="store_true",
                   help="set up the free prerequisites that are missing, asking before each one (your own Terminal "
                        "only; from an agent it prints the commands and exits 3)")
    p.add_argument("--context", metavar="NAME",
                   help="label this run (claude-code, pursuit-slice, openclaw-gateway, …) and save it to "
                        "DATA/doctor/NAME.json")
    p.add_argument("--brand", metavar="B", help="also check brand B's fonts")
    p.add_argument("--only", action="append", choices=R.GROUPS, metavar="GROUP",
                   help=f"check only this group (repeatable): {', '.join(R.GROUPS)}")
    p.add_argument("--strict", action="store_true", help="exit 1 unless every row is green")

    group = add_group(sub, "connect", "connect Claude, Higgsfield, ElevenLabs or After Effects",
                      description=CONNECT_DESCRIPTION, epilog=EPILOG)
    c = add_command(group, "claude", cmd_connect_claude,
                    "link Slate's skills, CLI and MCP server into Claude Code (and Codex and OpenClaw when they're here)",
                    description="Link Slate's skills (symlinks, one checkout, no copies), its command-line tool and its "
                                "MCP server into Claude Code, and into Codex and OpenClaw when they're on this Mac. "
                                "It changes those agents' own settings, so from an agent it waits for --yes, passed "
                                "only on the person's say-so. --dry-run shows every step first.", epilog=EPILOG)
    c.add_argument("--yes", action="store_true", help="the person said yes (an agent passes this only on their say-so)")

    h = add_command(group, "higgsfield", cmd_connect_higgsfield,
                    "print the Higgsfield sign-in for you to run, and store the Cloud API key with --api-key",
                    description="The higgsfield CLI's sign-in opens a browser, so Slate never runs it: this prints "
                                "`higgsfield auth login` for you to run in your own Terminal. --api-key asks for your "
                                "Cloud API key (id:secret) in macOS's own prompt and stores it in the login Keychain; "
                                "a zero-cost probe then checks it.", epilog=EPILOG)
    h.add_argument("--api-key", dest="api_key", action="store_true",
                   help="store the Cloud API key (your own Terminal only)")
    h.add_argument("--check", action="store_true", help="only check what's there; ask for nothing")

    e = add_command(group, "elevenlabs", cmd_connect_elevenlabs,
                    "store your ElevenLabs key (or, with --oneiro, Oneiro's capped key) in the Keychain",
                    description="Asks for the key in macOS's own prompt, in your own Terminal, and stores it in the "
                                "login Keychain; then the free plan check (GET /v1/user/subscription) says what the "
                                "plan allows. --oneiro stores the separate, capped key autonomous films spend through, "
                                "and explains how to create it (a credit quota; text to speech, music and alignment "
                                "only).", epilog=EPILOG)
    e.add_argument("--oneiro", action="store_true", help="Oneiro's capped key, for autonomous films")
    e.add_argument("--check", action="store_true", help="only check what's there; ask for nothing")

    a = add_command(group, "aftereffects", cmd_connect_aftereffects,
                    "find AE and aerender, install the AE bridge, and render the 1-second smoke test",
                    description="Finds After Effects and aerender, reports the scripting permission and the brand's "
                                "fonts, installs Higgsfield's AE bridge at its pinned version and registers it, then "
                                "renders the 1-second smoke test. It starts After Effects: a person running it is the "
                                "go, and an agent needs --go, passed only on the person's say-so. --dry-run starts "
                                "nothing.", epilog=EPILOG)
    a.add_argument("--go", action="store_true", help="the person's go (an agent passes this only on their say-so)")
    a.add_argument("--brand", metavar="B", help="also check brand B's fonts, on this Mac and inside After Effects")
