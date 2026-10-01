"""The `slate` command: one dispatcher, every stage a subcommand.

Each module exposes `register(sub)` and adds its commands with `add_command` (a leaf command) or `add_group` (a
command with stages, like `slate voice tts`). Every command accepts the global flags, before or after its name:

    --json      one JSON object on stdout; logs on stderr
    --film X    the film (id under ~/Movies/Slate, or a path); default: the film folder you're in
    --live      real vendor calls even when SLATE_FAKE_VENDORS=1 (they spend)
    --dry-run   say what would happen, change nothing
    --quiet     no progress lines

Exit codes: 0 ok · 1 gate failed or refused · 2 usage · 3 missing prerequisite · 4 spend refused · 5 busy ·
75 try later (a rate limit or an unavailable service; the job queue tries again by itself).

With --json, every outcome is one JSON object on stdout, usage errors included ({"error": "usage", ...}).

A command stopped with SIGTERM or SIGHUP stops what it started too: the signal becomes SystemExit inside the command,
so slate.procs.run's cleanup stops each child's whole process group before Slate exits (143 or 129).

A module that fails to import doesn't take the CLI down: its commands are missing, `slate doctor` names it, and
`slate.cli.BROKEN` says why (the tests hold it empty).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable

from slate import __version__, log, paths
from slate.errors import SlateError, UsageError

# (module, the commands it adds, exactly). Order is the order commands appear in `slate --help`. Each command is
# registered by one module, once; tests/test_cli_surface.py holds this list to what each module really adds.
MODULES: list[tuple[str, str]] = [
    ("slate.doctor", "doctor, connect"),
    ("slate.guide", "guide"),
    ("slate.demo", "demo"),
    ("slate.state.cli", "new, status, make, approve, film, variant, revise"),
    ("slate.story.cli", "script"),
    ("slate.timing.cli", "timing"),
    ("slate.voice.cli", "voice"),
    ("slate.sound.cli", "score, mix, verify, sfx, sound"),
    ("slate.gen.cli", "gen, plates"),
    ("slate.capture.cli", "capture"),
    ("slate.engine.cli", "engine, emit-events, check"),
    ("slate.render.cli", "render"),
    ("slate.qa.cli", "qa"),
    ("slate.ae.cli", "ae"),
    ("slate.finish.cli", "master, deliver, review, notes, approved"),
    ("slate.analyze.cli", "analyze"),
    ("slate.formats", "formats"),
    ("slate.bench.cli", "bench"),
    ("slate.brand.cli", "brand"),
    ("slate.jobs.cli", "job, jobs, budget, ledger"),
    ("slate.mcp.cli", "mcp, mcpb"),
    ("slate.skills_build", "skills"),
    ("slate.knowledge", "knowledge"),
    ("slate.evals.cli", "evals"),
    ("slate.update", "update"),
    ("slate.install.cli", "install, uninstall"),
]

BROKEN: dict[str, str] = {}
REGISTERED: dict[str, str] = {}  # command -> the module that registered it (filled by build_parser)

# `slate <command> <name> …` served by another module's own register(sub), because <command> is a leaf that takes
# names (`slate qa loudness sync` runs gates) and this name has actions and flags of its own: `slate qa stability
# baseline --act 3 --by NAME …`. Plain `slate qa stability` runs the same gate either way. When the module isn't
# installed (or fails to import) the words go to the leaf command as usual.
SUBROUTES: dict[tuple[str, str], str] = {("qa", "stability"): "slate.qa.stability"}
# Commands that start without building the whole parser (importing every module costs a second or more on a busy
# Mac): `slate mcp` answers a client's initialize quickly, and builds the full CLI on its first tool call instead.
FAST_COMMANDS: dict[str, str] = {"mcp": "slate.mcp.cli"}
GLOBAL_VALUE_FLAGS = ("--film",)

_state = {"json": False, "stopping": None, "caller_env": {}}
# The variables the global flags set for the command's own run (--live sets SLATE_LIVE=1, --film sets SLATE_FILM),
# as the caller had them before that: `caller_env` reads them.
FLAG_ENV = ("SLATE_LIVE", "SLATE_FILM")


class Parser(argparse.ArgumentParser):
    """argparse, except that a usage error under --json is one JSON object on stdout, like every other outcome:
    an agent reading stdout never has to parse prose."""

    def error(self, message: str):  # argparse calls this for every usage error, in whichever subparser found it
        if _state["json"]:
            print(json.dumps({"error": "usage", "message": message, "exit_code": 2,
                              "usage": self.format_usage().strip(), "hint": f"{self.prog} --help"},
                             ensure_ascii=False), flush=True)
            print(f"{self.prog}: error: {message}", file=sys.stderr)
            self.exit(2)
        super().error(message)


def _json_requested(argv: list[str]) -> bool:
    for word in argv:
        if word == "--":
            return False
        if word == "--json":
            return True
    return False


class Terminated(SystemExit):
    """SIGTERM or SIGHUP inside a command. A SystemExit, so it unwinds through every `finally` and
    `except BaseException` on the way out: slate.procs.run stops its child's whole process group, temp files are
    removed, locks are released."""

    def __init__(self, signum: int):
        super().__init__(128 + signum)
        self.signum = signum
        self.name = signal.Signals(signum).name


def _on_signal(signum, _frame) -> None:
    if _state["stopping"] is not None:
        return  # already unwinding from the first signal; the cleanup it started finishes first
    _state["stopping"] = signum
    raise Terminated(signum)


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def _install_signal_handlers() -> dict | None:
    """Make SIGTERM and SIGHUP unwind the command (Python's default for them exits without running any cleanup, which
    orphaned the tools procs.run started). Only from the main thread (an MCP server's worker threads can't set
    handlers), and never over a handler someone chose to ignore the signal with (nohup)."""
    if threading.current_thread() is not threading.main_thread():
        return None
    saved = {}
    for sig in STOP_SIGNALS:
        try:
            current = signal.getsignal(sig)
            if current is signal.SIG_IGN:
                continue
            saved[sig] = current
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):  # pragma: no cover - an embedded interpreter without signals
            continue
    return saved


def _restore_signal_handlers(saved: dict | None) -> None:
    for sig, handler in (saved or {}).items():
        try:
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)
        except (ValueError, OSError):  # pragma: no cover
            pass


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
        if log.json_mode():
            stages = sorted(getattr(stages_action, "choices", {}) or {})
            log.emit({"error": "usage", "message": f"`{parser.prog}` needs a stage: {', '.join(stages)}.",
                      "exit_code": 2, "stages": stages, "hint": f"{parser.prog} --help"})
        parser.print_help(sys.stderr)
        return 2

    parser.set_defaults(handler=show_help)
    stages_action = parser.add_subparsers(dest=f"{name.replace('-', '_')}_stage", metavar="<stage>")
    return stages_action


def caller_env(name: str) -> str | None:
    """An environment variable as the caller set it, before this command's own flags changed it. `--live` sets
    SLATE_LIVE=1 for the command, so a command that must tell a person's explicit SLATE_LIVE=1 from its own flag
    (`slate demo --live` under an agent) reads it here."""
    saved = _state.get("caller_env") or {}
    return saved[name] if name in saved else os.environ.get(name)


def flag(args: argparse.Namespace, name: str, default=None):
    """Read a global flag that may be absent (the common flags use SUPPRESS defaults)."""
    return getattr(args, name, default)


def film(args: argparse.Namespace) -> Path:
    """The film this command acts on (from --film, SLATE_FILM, or the folder you're in)."""
    return paths.film_dir(flag(args, "film"))


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="slate", parents=[COMMON], formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Slate: a film studio any agent can run. Every stage of a film is a command here.\n"
                    "Start with `slate doctor`, then `slate new`, then follow `slate status`.",
        epilog="Exit codes: 0 ok, 1 gate failed or refused, 2 usage, 3 missing prerequisite, 4 spend refused, "
               "5 busy, 75 try later (the queue retries by itself).\nGuides: `slate guide` lists the reference topics.",
    )
    parser.add_argument("--version", action="version", version=f"slate {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    BROKEN.clear()
    REGISTERED.clear()
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
        before = set(sub.choices)
        try:
            register(sub)
        except Exception as exc:  # noqa: BLE001 - argparse refuses a command registered twice: that's a bug to see
            BROKEN[module_name] = f"register failed: {type(exc).__name__}: {exc}"
        for name in sub.choices:
            if name not in before:
                REGISTERED[name] = module_name
    return parser


def _report_error(exc: SlateError) -> None:
    if log.json_mode():
        print(json.dumps(exc.to_dict(), ensure_ascii=False, default=str))
    else:
        print(f"slate: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  next: {exc.hint}", file=sys.stderr)


def _note_try_later(exc: SlateError) -> None:
    """Tell the job queue how long a vendor asked us to wait, when this command runs as a queued job (best effort:
    the queue backs off anyway)."""
    job_id = os.environ.get("SLATE_JOB_ID")
    if exc.exit_code != 75 or not job_id:
        return
    try:
        from slate.jobs import client  # noqa: PLC0415

        client.note_try_later(job_id, retry_after_s=getattr(exc, "retry_after_s", None), reason=exc.message)
    except Exception:  # noqa: BLE001 - the exit code alone already sends the job back to the queue
        pass


def main(argv: list[str] | None = None) -> int:
    """Run one command. Returns its exit code (argparse's own exits, like --help, raise SystemExit)."""
    words = list(sys.argv[1:] if argv is None else argv)
    saved_json, saved_stopping, saved_env = _state["json"], _state["stopping"], _state.get("caller_env") or {}
    _state["json"], _state["stopping"], _state["caller_env"] = _json_requested(words), None, {}
    handlers = _install_signal_handlers()
    try:
        code = _run(words)
        if _state["json"]:
            log.seal_stdout()  # the one JSON object is out: late native writes (at exit) go to stderr
        return code
    except Terminated as exc:
        print(f"slate: stopped by {exc.name}; everything it started was stopped too.", file=sys.stderr)
        return int(exc.code)
    finally:
        _restore_signal_handlers(handlers)
        _state["json"], _state["stopping"], _state["caller_env"] = saved_json, saved_stopping, saved_env


def _positionals(words: list[str]) -> list[tuple[int, str]]:
    """(index, word) for the words that aren't flags or a global flag's value, up to `--`."""
    out, skip = [], False
    for i, word in enumerate(words):
        if skip:
            skip = False
            continue
        if word == "--":
            break
        if word.startswith("-"):
            skip = word in GLOBAL_VALUE_FLAGS
            continue
        out.append((i, word))
    return out


def _subroute(words: list[str]) -> tuple[argparse.ArgumentParser, list[str]] | None:
    """A parser for `slate <command> <name> …` when SUBROUTES sends it to another module (see SUBROUTES)."""
    pos = _positionals(words)
    if len(pos) < 2 or (pos[0][1], pos[1][1]) not in SUBROUTES:
        return None
    module_name = SUBROUTES[(pos[0][1], pos[1][1])]
    try:
        module = importlib.import_module(module_name)
        register = getattr(module, "register")
        parser = Parser(prog=f"slate {pos[0][1]}", parents=[COMMON],
                        formatter_class=argparse.RawDescriptionHelpFormatter)
        register(parser.add_subparsers(dest="route", metavar="<name>"))
    except Exception as exc:  # noqa: BLE001 - not installed or broken: the leaf command answers instead
        BROKEN.setdefault(module_name, f"its `slate {pos[0][1]} {pos[1][1]}` route failed: {type(exc).__name__}: "
                                       f"{exc}")
        return None
    return parser, words[:pos[0][0]] + words[pos[0][0] + 1:]


def _fast_parser(words: list[str]) -> argparse.ArgumentParser | None:
    """A parser holding only one command's module, for FAST_COMMANDS (None: build the whole CLI)."""
    pos = _positionals(words)
    if not pos or pos[0][1] not in FAST_COMMANDS or "--help" in words or "-h" in words:
        return None
    try:
        module = importlib.import_module(FAST_COMMANDS[pos[0][1]])
        parser = Parser(prog="slate", parents=[COMMON], formatter_class=argparse.RawDescriptionHelpFormatter)
        module.register(parser.add_subparsers(dest="command", metavar="<command>"))
    except Exception:  # noqa: BLE001 - the whole CLI answers instead, and reports what's broken
        return None
    return parser


def _run(words: list[str]) -> int:
    routed = _subroute(words)
    if routed is not None:
        parser, words = routed
    else:
        parser = _fast_parser(words) or build_parser()
    args = parser.parse_args(words)
    _state["caller_env"] = {k: os.environ.get(k) for k in FLAG_ENV}
    log.set_json(bool(flag(args, "json", False)))
    log.set_quiet(bool(flag(args, "quiet", False)))
    if flag(args, "live", False):
        os.environ["SLATE_LIVE"] = "1"
    if flag(args, "film"):
        os.environ["SLATE_FILM"] = str(flag(args, "film"))
    handler = getattr(args, "handler", None)
    if handler is None:
        if log.json_mode():
            log.emit({"error": "usage", "message": "no command given", "exit_code": 2, "hint": "slate --help"})
        parser.print_help(sys.stderr)
        return 2
    try:
        code = handler(args)
        return int(code or 0)
    except SlateError as exc:
        _report_error(exc)
        _note_try_later(exc)
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
