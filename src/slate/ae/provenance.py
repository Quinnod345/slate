"""What made each file the After Effects lane writes, noted in film.lock.json through slate.state.lock.

`slate status` calls a derived file stale when anything it was made from changes, and a person can lock it once it's
approved; both need the note. It's bookkeeping, so a failure here warns and never undoes the work that was done.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from slate import log


def note(film_dir: str | Path, made: Iterable[str | Path], *, producer: str, inputs: Iterable[str | Path]) -> list[str]:
    """Record each file in `made` (film-relative or absolute) as made by `producer` from `inputs` (only those that
    exist are hashed). Returns the film-relative paths it recorded."""
    film_dir = Path(film_dir)
    try:
        from slate.state import lock  # noqa: PLC0415 - optional: the state module is built alongside this one
    except ImportError:
        return []
    have = [str(p) for p in inputs if (film_dir / p if not Path(p).is_absolute() else Path(p)).is_file()]
    done = []
    for rel in made:
        try:
            lock.record(film_dir, str(rel), producer=producer, inputs=have)
            done.append(str(rel))
        except Exception as exc:  # noqa: BLE001 - the file stands; the lock note is bookkeeping
            log.warn(f"couldn't note {rel} in film.lock.json: {getattr(exc, 'message', exc)}")
    return done
