"""Who may start After Effects, and when AE has to be closed.

Adobe's enterprise terms (PSLT §1.2) bar operations a person didn't start, and until the person's own licence terms
are checked Slate treats that as the rule:

- every command that drives AE or aerender needs `--go`, which means "I, a person, am starting this now";
- a film in autonomous mode never touches AE, whatever flags are passed. Its masters come from ffmpeg and it is held
  for the person, who can start the AE finish once they approve it;
- when a step needs AE closed (masters, preference edits), Slate stops with exit 5 and asks the person to save and
  quit. It never quits AE itself and never discards changes.

Autonomous mode is recognised three ways, strongest first: `slate.state.modes.check` when the state module provides
it, the film's own `film.json` "mode", and `SLATE_MODE=autonomous` in the environment (for AE commands that aren't
tied to one film, run from inside an autonomous pursuit).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from slate import jsonio, log, procs
from slate.errors import Busy, Refused, SlateError, SpendRefused

SAVE_AND_QUIT = "save your work in After Effects and quit it, then run this again"
GO_HINT = "Add --go when you (a person) want After Effects to start. An agent passes --go only on your say-so."
PSLT = ("Autonomous films never use After Effects: Adobe's enterprise terms (PSLT §1.2) bar operations a person "
        "didn't start. The film is mastered with ffmpeg and held for you; start its AE finish yourself once you "
        "approve it (switch the film to supervised, then run the AE step with --go).")


def film_mode(film_dir: Path | str | None) -> str | None:
    """The film's mode from film.json ("supervised", "express", "draft", "autonomous"), or None without a film."""
    if not film_dir:
        return None
    fj = jsonio.read(Path(film_dir) / "film.json", default=None)
    if not isinstance(fj, dict):
        return None
    mode = fj.get("mode")
    return str(mode) if mode else None


def _state_modes_check(film_dir: Path, action: str) -> None:
    """Ask slate.state.modes whether this film may take this AE step. Its refusal wins; its absence is fine.

    Actions it knows: "ae", "ae_build", "ae_master", "ae_finish", "ae_render", "ae_project". A film.json the state
    module can't read is not this guard's verdict to give: it logs a warning and falls back to the film's mode.
    """
    try:
        from slate.state import modes  # noqa: PLC0415 - optional dependency, built in parallel
    except ImportError:
        return
    check = getattr(modes, "check", None)
    if not callable(check):
        return
    try:
        verdict: Any = check(film_dir, action)
    except TypeError:
        verdict = check(film_dir)
    except (Refused, SpendRefused):
        raise
    except SlateError as exc:
        log.warn(f"slate.state couldn't check this film's mode ({exc.message}); using film.json's mode instead.")
        return
    if verdict is None or verdict is True:
        return
    if verdict is False:
        raise Refused("This film's mode doesn't allow After Effects.", hint=PSLT)
    if isinstance(verdict, tuple) and verdict and verdict[0] is False:
        reason = verdict[1] if len(verdict) > 1 else "this film's mode doesn't allow After Effects"
        raise Refused(str(reason), hint=PSLT)
    if isinstance(verdict, dict) and verdict.get("allowed") is False:
        raise Refused(str(verdict.get("reason") or "this film's mode doesn't allow After Effects"), hint=PSLT)


def refuse_autonomous(film_dir: Path | str | None = None, action: str = "ae") -> None:
    """Raise Refused when the film (or the calling pursuit) is autonomous. Never lets AE near an autonomous film."""
    if os.environ.get("SLATE_MODE", "").strip().lower() == "autonomous":
        raise Refused("This is an autonomous run, and autonomous runs never use After Effects.", hint=PSLT)
    if film_dir:
        film_dir = Path(film_dir)
        _state_modes_check(film_dir, action)
        if (film_mode(film_dir) or "").lower() == "autonomous":
            raise Refused(f"Film {film_dir.name} is in autonomous mode, and autonomous films never use After Effects.",
                          hint=PSLT)


def require_go(go: bool, what: str) -> None:
    """Raise Refused unless the person explicitly started this AE step."""
    if not go:
        raise Refused(f"{what} starts After Effects, so it waits for a person's go.", hint=GO_HINT)


def person_start(go: bool, what: str, *, film_dir: Path | str | None = None, action: str = "ae") -> None:
    """The gate in front of every AE or aerender step: never for autonomous films, and only with --go.

    `action` is the step's name for slate.state.modes ("ae_build", "ae_master", "ae_render", or "ae").
    """
    refuse_autonomous(film_dir, action)
    require_go(go, what)


def ae_open() -> list[str]:
    """Which AE processes are running right now: "After Effects" (the app) and/or "aerender"."""
    busy = []
    if procs.ae_running():
        busy.append("After Effects")
    if procs.aerender_running():
        busy.append("aerender")
    return busy


def require_ae_closed(what: str, *, allow_aerender: bool = False) -> None:
    """Raise Busy (exit 5) when AE is open. Slate never quits it for you: quitting could lose unsaved work."""
    if procs.ae_running():
        raise Busy(f"After Effects is open, and {what} needs it closed: {SAVE_AND_QUIT}.",
                   hint="Slate never quits After Effects for you, because quitting could lose unsaved work.")
    if not allow_aerender and procs.aerender_running():
        raise Busy(f"aerender is running, and {what} needs it finished first. Let it finish (or stop it yourself), "
                   "then run this again.",
                   hint="`slate job status` shows whether one of Slate's own jobs is rendering.")
