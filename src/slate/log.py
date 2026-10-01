"""Output conventions every command follows.

Human text goes to stdout; with `--json`, stdout carries exactly one JSON object and everything else goes to stderr.
Progress and warnings always go to stderr, so an agent parsing stdout never trips over them.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

_state = {"json": False, "quiet": False}


def set_json(on: bool) -> None:
    _state["json"] = bool(on)


def json_mode() -> bool:
    return _state["json"]


def set_quiet(on: bool) -> None:
    _state["quiet"] = bool(on)


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def info(msg: str) -> None:
    """Progress for people. Goes to stderr so it never mixes with machine output."""
    if not _state["quiet"]:
        print(f"{_stamp()} {msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    print(f"{_stamp()} warning: {msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    print(f"{_stamp()} error: {msg}", file=sys.stderr, flush=True)


def say(text: str) -> None:
    """A command's human result (stdout). Suppressed in --json mode, where `emit` is the result."""
    if not _state["json"]:
        print(text, flush=True)


def emit(obj: Any) -> None:
    """A command's machine result: one JSON object on stdout (only printed in --json mode)."""
    if _state["json"]:
        print(json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def _real_stdout() -> bool:
    try:
        return sys.stdout is sys.__stdout__ and sys.__stdout__ is not None and sys.__stdout__.fileno() == 1
    except (AttributeError, OSError, ValueError):
        return False


def seal_stdout() -> bool:
    """After a --json command's one object is out: send file descriptor 1 to stderr for the rest of the process.

    Native code (Apple Vision's "Unable to find a valid E5 …" lines) writes below Python, some of it buffered in C stdio
    and flushed at exit, after the JSON. Sealing fd 1 sends whatever comes later (that buffered text included: it's
    deliberately not flushed first) to stderr, where logs go, so stdout stays exactly one object. Only the real
    process stdout is sealed: in-process callers (slate.cli.run_cli, the MCP server, tests) capture sys.stdout in a
    buffer and are left alone. True when it sealed."""
    if not _real_stdout():
        return False
    try:
        sys.stdout.flush()
        os.dup2(2, 1)
    except OSError:  # pragma: no cover - no fd 2 to point at
        return False
    return True


def result(obj: Any, human: str | None = None) -> None:
    """Print a result the right way for the current mode: JSON with --json, otherwise the human text."""
    if _state["json"]:
        emit(obj)
    elif human is not None:
        say(human)
