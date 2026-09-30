"""Review stills: PNG frames from the Remotion engine, all from one bundle, which is deleted afterwards.

Frames are the act's own frame numbers with `act`, otherwise film frames (Slate finds each one's act). Half size and
one sample by default, as the films' review stills were: a still is for looking at layout and type, and motion blur on
a single frame mostly just softens it.
"""

from __future__ import annotations

from pathlib import Path

from slate.errors import Refused, UsageError
from slate.render import deps, engines
from slate.render.plan import acts_from_shots, find_act

DEFAULT_DIR = Path("renders") / "stills"


def _group(frames: list[int], act: int | None, acts) -> list[tuple[object, list[int]]]:
    if act is not None:
        a = find_act(acts, act)
        bad = [f for f in frames if not 0 <= f < a.frames]
        if bad:
            raise UsageError(f"Act {act} has frames 0–{a.frames - 1}; {', '.join(map(str, bad))} isn't inside it.")
        return [(a, sorted(set(frames)))]
    groups: dict[int, list[int]] = {}
    last = acts[-1].frame_out - 1
    for f in frames:
        if not 0 <= f <= last:
            raise UsageError(f"The film has frames 0–{last}; {f} isn't one of them.")
        a = next(x for x in acts if x.frame_in <= f < x.frame_out)
        groups.setdefault(a.index, []).append(f - a.frame_in)
    return [(find_act(acts, i), sorted(set(fs))) for i, fs in sorted(groups.items())]


def _files(result: dict) -> list:
    for key in ("stills", "files", "paths", "outputs"):
        if isinstance(result.get(key), list):
            return result[key]
    return []


def stills(film_dir: Path, *, frames: list[int], act: int | None = None, scale: float = 0.5, samples: int = 1,
           out_dir: str | Path | None = None, engine: str = "remotion") -> dict:
    """Render stills and return where they landed: {"out_dir", "stills": [{act, frame, film_frame, file}]}."""
    if engine != "remotion":
        raise Refused(f"Stills come from the Remotion engine; the {engine} engine renders ranges.",
                      hint=f"For one frame from {engine}: slate render --engine {engine} --act N --from F --to F "
                           "--out still.mov")
    if not frames:
        raise UsageError("Say which frames: --frames 0,120,240.")
    if not 0 < scale <= 4:
        raise UsageError(f"--scale must be above 0 and at most 4 (got {scale}).")
    if samples < 1:
        raise UsageError(f"--samples must be at least 1 (got {samples}).")
    film_dir = Path(film_dir).resolve()
    film = deps.load_film(film_dir)
    acts = acts_from_shots(deps.load_shots(film_dir))
    groups = _group([int(f) for f in frames], act, acts)
    out = Path(out_dir).expanduser() if out_dir else film_dir / DEFAULT_DIR
    out = out if out.is_absolute() else film_dir / out
    out.mkdir(parents=True, exist_ok=True)
    eng = engines.RemotionEngine(film_dir, film)
    eng.preflight()
    made = []
    try:
        eng.open()
        for a, fs in groups:
            result = eng.stills(a.index, fs, out, scale=scale, samples=samples)
            files = _files(result)
            for i, f in enumerate(fs):
                item = files[i] if i < len(files) else None
                file = item.get("file") if isinstance(item, dict) else item
                made.append({"act": a.index, "frame": f, "film_frame": a.frame_in + f, "file": file})
    finally:
        eng.close()
    return {"ok": True, "out_dir": str(out), "stills": made}
