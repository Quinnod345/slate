"""Errors that map straight onto the CLI's exit codes (docs/ARCHITECTURE.md §4).

Raise one of these anywhere in Slate. `slate.cli` catches it, prints its message in plain words (or as JSON with
`--json`) and exits with its code, so every agent sees the same code for the same situation:

    0 ok · 1 gate failed or refused · 2 usage · 3 missing prerequisite · 4 spend refused · 5 busy
"""

from __future__ import annotations


class SlateError(Exception):
    """Base class. `hint` is the one next step a person or agent should take, when there is one."""

    exit_code = 1
    kind = "error"

    def __init__(self, message: str, *, hint: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        out = {"error": self.kind, "message": self.message, "exit_code": self.exit_code}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class Refused(SlateError):
    """A gate failed, or Slate refused on purpose (a rule, a lock, an approval that isn't there)."""

    exit_code = 1
    kind = "refused"


class UsageError(SlateError):
    """The command was called wrongly: a missing argument, an unknown film, a bad value."""

    exit_code = 2
    kind = "usage"


class MissingPrerequisite(SlateError):
    """Something Slate needs isn't installed, connected or approved yet. The hint names the fix."""

    exit_code = 3
    kind = "missing_prerequisite"


class SpendRefused(SlateError):
    """A paid call was refused: over budget, over the monthly cap, or waiting for a person's yes."""

    exit_code = 4
    kind = "spend_refused"


class Busy(SlateError):
    """A resource is in use: After Effects is open, a lock is held, a job is already running."""

    exit_code = 5
    kind = "busy"
