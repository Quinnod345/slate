"""The one hand-edited source: script/script.json in, a gated storyboard out.

`slate script build` reads the script (acts, shots, VO, on-screen text, picture, sources, claims), the brand's truth
(banned words, approved claims, what it never claims), the format (runtime, pace) and the film's pinned sources, runs
the script gates, and only then writes script/storyboard.json and script/SCRIPT.md. Any FAIL and nothing is written.

Cross-module interface (docs/ARCHITECTURE.md §12):

- `build(film_dir) -> list[GateResult]`
- `gates.check_script(script, *, brand, fmt) -> list[GateResult]`
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from slate.gates import GateResult


def build(film_dir: str | os.PathLike, **kw) -> list[GateResult]:
    """Run the script gates and, when none fails, write the storyboard and SCRIPT.md. Returns the gate results."""
    from slate.story.builder import build as _build  # noqa: PLC0415 (keep `import slate.story` light)

    return _build(Path(film_dir), **kw)


_SUBMODULES = {"builder", "freeze", "gates", "inputs", "model", "numbers", "scriptmd"}


def __getattr__(name: str):
    # `slate.story.gates.check_script(...)` works after a plain `import slate.story` (PEP 562).
    if name in _SUBMODULES:
        import importlib  # noqa: PLC0415

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["build", *sorted(_SUBMODULES)]
