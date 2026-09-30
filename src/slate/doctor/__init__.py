"""`slate doctor` and `slate connect`: is this Mac ready for Slate, and the steps that make it so.

Doctor checks the binaries and their versions (the way Slate will run them), the Node engine and its headless Chrome,
keys by presence plus the vendors' free probes, After Effects (installed, its scripting pref, the AE bridge at its pin,
the Automation permission, the smoke test), Apple's sampled instruments, every agent's skills and MCP registration,
the CLI on PATH, disk space, the default browser and anything that would open it, the job agent, OCR, and Slate's own
modules. Each check is a row: green, yellow or red, with the fix. It only reads.

Connect does the promise's connect steps: `claude`, `higgsfield`, `elevenlabs` and `aftereffects`.

    from slate import doctor
    rows = doctor.checks()                  # [{id, status: green|yellow|red, summary, fix, …}]
    rep = doctor.report("claude-code")      # what `slate doctor --json` prints
"""

from __future__ import annotations

from typing import Iterable


def checks(context: str | None = None, *, brand: str | None = None, only: Iterable[str] | None = None) -> list[dict]:
    """Every doctor row for this Mac (docs/ARCHITECTURE.md §12): [{id, status: green|yellow|red, summary, fix}]."""
    from slate.doctor import core  # noqa: PLC0415

    return core.checks(context, brand=brand, only=only)


def report(context: str | None = None, *, brand: str | None = None, only: Iterable[str] | None = None) -> dict:
    """{status, context, checks, …}: the report `slate doctor --json` prints."""
    from slate.doctor import core  # noqa: PLC0415

    return core.report(context, brand=brand, only=only)


def overall(rows: Iterable[dict]) -> str:
    """green only when every row is green; otherwise the worst status."""
    from slate.doctor import rows as R  # noqa: PLC0415

    return R.overall(rows)


def register(sub) -> None:
    """Add `slate doctor` and `slate connect` to the CLI."""
    from slate.doctor import cli  # noqa: PLC0415

    cli.register(sub)
