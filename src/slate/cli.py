"""The `slate` command: one dispatcher, every stage a subcommand.

Each module exposes `register(sub)` and adds its commands with `add_command` (a leaf command) or `add_group` (a
command with stages, like `slate voice tts`). Every command accepts the global flags, before or after its name:

    --json      one JSON object on stdout; logs on stderr
    --film X    the film (id under ~/Movies/Slate, or a path); default: the film folder you're in
    --live      real vendor calls even when SLATE_FAKE_VENDORS=1 (they spend)
    --dry-run   say what would happen, change nothing
    --quiet     no progress lines

Exit codes: 0 ok · 1 gate failed or refused · 2 usage · 3 missing prerequisite · 4 spend refused · 5 busy.

A module that fails to import doesn't take the CLI down: its commands are missing, and `slate doctor` names it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

from slate import __version__, log, paths
from slate.errors import SlateError, UsageError

# (module, what it adds). Order is the order commands appear in `slate --help`.
MODULES: list[tuple[str, str]] = [
    ("slate.doctor", "doctor, connect"),
    ("slate.guide", "guide"),
    ("slate.state.cli", "new, status, make, approve, film"),
    ("slate.story.cli", "script"),
    ("slate.timing.cli", "timing"),
    ("slate.voice.cli", "voice"),
    ("slate.sound.cli", "score, mix, verify, sfx"),
    ("slate.gen.cli", "gen, plates"),
    ("slate.capture.cli", "capture"),
    ("slate.engine.cli", "engine, emit-events, check"),
    ("slate.render.cli", "render"),
    ("slate.qa.cli", "qa"),
    ("slate.ae.cli", "ae"),
    ("slate.finish.cli", "master, deliver, review, notes, variant, formats"),
    ("slate.analyze.cli", "analyze"),
    ("slate.brand.cli", "brand"),
    ("slate.jobs.cli", "job, jobs, budget, ledger"),
    ("slate.mcp.cli", "mcp"),
    ("slate.skills_build", "skills"),
    ("slate.knowledge", "knowledge"),
    ("slate.evals.cli", "evals"),
    ("slate.demo", "demo"),
    ("slate.update", "update"),
]

BROKEN: dict[str, str] = {}


def _common_flags() -> argparse.ArgumentParser:
    # SUPPRESS defaults: a flag given before the subcommand isn't overwritten by the subparser's default.
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="machine output: one JSON object on stdout")
    p.add_argument("--film", default=argparse.SUPPRESS, metavar="ID|PATH",
                   help="the film (default: the film folder you're in)")
    p.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                   help="real vendor calls (spends), even in replay mode")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=argparse.SUPPRESS,
                   help="say what would happen; change nothing")
    p.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS, help="no progress lines")
    return p


COMMON = _common_flags()


def add_command(subparsers, name: str, handler: Callable[[argparse.Namespace], int], help: str,
                **kw) -> argparse.ArgumentParser:
    """Add a leaf command. `handler(args) -> exit code`."""
    parser = subparsers.add_parser(name, help=help, description=kw.pop("description", help), parents=[COMMON],
                                   formatter_class=argparse.RawDescriptionHelpFormatter, **kw)
    parser.set_defaults(handler=handler)
    return parser


def add_group(subparsers, name: str, help: str, **kw):
    """Add a command with stages (`slate voice tts`). Returns the group's subparsers for `add_command`."""
    parser = subparsers.add_parser(name, help=help, description=kw.pop("description", help), parents=[COMMON],
                                   formatter_class=argparse.RawDescriptionHelpFormatter, **kw)

    def show_help(_args: argparse.Namespace) -> int:
        parser.print_help(sys.stderr)
        return 2

    parser.set_defaults(handler=show_help)
    return parser.add_subparsers(dest=f"{name.replace('-', '_')}_stage", metavar="<stage>")


def flag(args: argparse.Namespace, name: str, default=None):
    """Read a global flag that may be absent (the common flags use SUPPRESS defaults)."""
    return getattr(args, name, default)


def film(args: argparse.Namespace) -> Path:
    """The film this command acts on (from --film, SLATE_FILM, or the folder you're in)."""
    return paths.film_dir(flag(args, "film"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slate", parents=[COMMON], formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Slate: a film studio any agent can run. Every stage of a film is a command here.\n"
                    "Start with `slate doctor`, then `slate new`, then follow `slate status`.",
        epilog="Exit codes: 0 ok, 1 gate failed or refused, 2 usage, 3 missing prerequisite, 4 spend refused, "
               "5 busy.\nGuides: `slate guide` lists the reference topics.",
    )
    parser.add_argument("--version", action="version", version=f"slate {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    BROKEN.clear()
    for module_name, _what in MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name or module_name.startswith(str(exc.name)):
                BROKEN[module_name] = "not built yet"
                continue
            BROKEN[module_name] = f"{type(exc).__name__}: {exc}"
            continue
        except Exception as exc:  # noqa: BLE001 - one broken module must not take the CLI down
            BROKEN[module_name] = f"{type(exc).__name__}: {exc}"
            continue
        register = getattr(module, "register", None)
        if register is None:
            BROKEN[module_name] = "has no register(sub)"
            continue
        try:
            register(sub)
        except Exception as exc:  # noqa: BLE001
            BROKEN[module_name] = f"register failed: {type(exc).__name__}: {exc}"
    return parser


def _report_error(exc: SlateError) -> None:
    if log.json_mode():
        print(json.dumps(exc.to_dict(), ensure_ascii=False, default=str))
    else:
        print(f"slate: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  next: {exc.hint}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log.set_json(bool(flag(args, "json", False)))
    log.set_quiet(bool(flag(args, "quiet", False)))
    if flag(args, "live", False):
        os.environ["SLATE_LIVE"] = "1"
    if flag(args, "film"):
        os.environ["SLATE_FILM"] = str(flag(args, "film"))
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        code = handler(args)
        return int(code or 0)
    except SlateError as exc:
        _report_error(exc)
        return exc.exit_code
    except KeyboardInterrupt:
        print("slate: stopped.", file=sys.stderr)
        return 130
    except BrokenPipeError:  # pragma: no cover - `slate … | head`
        return 0


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process and capture (code, stdout, stderr). Used by tests and the MCP server."""
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415

    out, err = io.StringIO(), io.StringIO()
    saved = {k: os.environ.get(k) for k in ("SLATE_LIVE", "SLATE_FILM")}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:  # argparse usage errors and --help
                code = int(exc.code) if isinstance(exc.code, int) else 2
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        log.set_json(False)
        log.set_quiet(False)
    return code, out.getvalue(), err.getvalue()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
