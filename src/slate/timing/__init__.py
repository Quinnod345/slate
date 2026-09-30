"""The film clock's data: shots, voice cues, word timings, events and the resolved timeline.

`slate timing` turns the storyboard and the real takes into frame-exact JSON that every engine reads (Remotion, the
open web renderer, After Effects, the sound engine, QA). Python resolves every frame here; the engines never re-derive
a boundary, which is what keeps picture, voice and sound on one clock.

Cross-module interface (docs/ARCHITECTURE.md §12):

- `build(film_dir) -> list[Path]`         writes timing/shots.json, vo_cues.json, vo_words.json, timeline.json, events.json
- `load_shots(film_dir) -> dict`          timing/shots.json, or a clear "run slate timing" when it isn't there
- `words.word_time(film_dir, shot, word, occ=1, which="start") -> (seconds, exact)`   never raises
- `spoken.forms(token, lang="en") -> list[list[str]]`                                  how a number or symbol is said
- `beats.problems(beats, fps=None) -> list[str]`   what t, start and dur mean in timing/visual_events (t is the beat)
"""

from __future__ import annotations

import os
from pathlib import Path


def build(film_dir: str | os.PathLike, **kw) -> list[Path]:
    """Write the film's timing files; returns their paths. Refuses (writes nothing) if the frames aren't exact."""
    from slate.timing.builder import build as _build  # noqa: PLC0415 (keep `import slate.timing` light)

    return _build(Path(film_dir), **kw)


def load_shots(film_dir: str | os.PathLike) -> dict:
    """timing/shots.json for a film."""
    from slate.timing.builder import load_shots as _load  # noqa: PLC0415

    return _load(Path(film_dir))


_SUBMODULES = {"align", "audio", "beats", "builder", "cues", "ease", "shots", "spoken", "takes", "timeline", "vowords",
               "words"}


def __getattr__(name: str):
    # `slate.timing.words.word_time(...)` works after a plain `import slate.timing` (PEP 562), without loading every
    # submodule up front.
    if name in _SUBMODULES:
        import importlib  # noqa: PLC0415

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["build", "load_shots", *sorted(_SUBMODULES)]
