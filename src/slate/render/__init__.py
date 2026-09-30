"""`slate render`: chunked, resumable renders with proven joins and splices, a renders registry, and bundle cleanup
everywhere.

    run(film_dir, *, act=None, frames=None, engine="remotion", samples=None, out=None) -> dict
    registry.entries(film_dir) -> list[dict]      registry.register(film_dir, entry) -> None
    splice.splice(film_dir, base, patch, frame_in, frame_out) -> dict

- Engines: `remotion` (the Node engine through slate.engine.bridge), `web` (the open web renderer) and `ae` (aerender
  through slate.ae.acts, whole acts only, only on a person's go, never for an autonomous film).
- Renders are ProRes 422 HQ, 10-bit, BT.709, with true motion blur: 16 sub-frames averaged in 16-bit, 4 in draft mode.
- Code-engine renders are chunked (`render.chunk_frames`), and a chunk with the right frame count and hash is never
  rendered twice. Joins and splices are proven on decoded pixels (frame hashes and PSNR) before anything is replaced.
- Each finished act render is registered in `renders/registry.json`, which locks the film's timeline.
- Long renders always go through the job queue, and every render reserves its disk first.
- Renders are local, always: never on another machine.

The heavy parts load when they're used, so `slate --help` stays quick.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

_SUBMODULES = {"registry", "splice", "plan", "chunks", "joins", "media", "engines", "clean", "stills", "runner", "deps"}


def run(film_dir: Path, *, act: int | None = None, frames: tuple[int, int] | None = None,
        engine: str | None = "remotion", samples: int | None = None, out: str | Path | None = None,
        **options: Any) -> dict:
    """Render in this process: a whole act (registered, which locks the timeline), every act, a patch spliced into the
    registered act render (`act` + `frames`, the act's own frame numbers, inclusive), or a range to `out`.

    Options: draft, go (After Effects), fresh, resume, in_queue. See slate.render.runner.
    """
    from slate.render.runner import run as _run  # noqa: PLC0415

    return _run(film_dir, act=act, frames=frames, engine=engine, samples=samples, out=out, **options)


def __getattr__(name: str):
    # `slate.render.registry` and friends, loaded on first use (PEP 562).
    if name in _SUBMODULES:
        return importlib.import_module(f"slate.render.{name}")
    raise AttributeError(f"module 'slate.render' has no attribute {name!r}")
