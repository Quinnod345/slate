"""Output conventions every command follows.

Human text goes to stdout; with `--json`, stdout carries exactly one JSON object and everything else goes to stderr.
Progress and warnings always go to stderr, so an agent parsing stdout never trips over them.
"""

from __future__ import annotations

import json
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


def result(obj: Any, human: str | None = None) -> None:
    """Print a result the right way for the current mode: JSON with --json, otherwise the human text."""
    if _state["json"]:
        emit(obj)
    elif human is not None:
        say(human)
