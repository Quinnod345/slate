"""Review stills: PNG frames from the Remotion engine (all from one bundle, which is deleted afterwards) or from the
open web renderer (one frame of its range per still, at the film's size, then scaled).

Frames are the act's own frame numbers with `act`, otherwise film frames (Slate finds each one's act). Half size and
one sample by default, as the films' review stills were: a still is for looking at layout and type, and motion blur on
a single frame mostly just softens it. After Effects renders comps, not frames, so its stills come from its renders.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

from slate.errors import Refused, UsageError
from slate.render import deps, engines, shot_engines
from slate.render.plan import Act, acts_from_shots, find_act

DEFAULT_DIR = Path("renders") / "stills"


def _group(frames: list[int], act: int | None, acts: list[Act]) -> list[tuple[Act, list[int]]]:
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


def _routes(film_dir: Path, shots: dict, groups: list[tuple[Act, list[int]]],
            base_engine: str | None) -> dict[tuple[str, Path | None], list[tuple[Act, list[int]]]]:
    """Resolve shot pages from built ownership; explicit preview engines choose the base frames."""
    preview_engine = base_engine or "remotion"
    work: dict[tuple[str, Path | None], list[tuple[Act, list[int]]]] = {}
    for a, frames in groups:
        built = shot_engines.act_engine(film_dir, a.index) or preview_engine
        segments = shot_engines.segments(film_dir, shots, a.index, a.frame_in, built)
        mixed = shot_engines.mixed_scenes(film_dir, shots, a.index, fallback_base=built)
        visible_base = built if base_engine is None and mixed else preview_engine
        selected: dict[tuple[str, Path | None], list[int]] = {}
        for frame in frames:
            segment = shot_engines.segment_for(segments, frame, frame)
            key = (segment.engine, segment.scene) if segment is not None else (visible_base, None)
            selected.setdefault(key, []).append(frame)
        for key, frames in selected.items():
            work.setdefault(key, []).append((a, frames))
    return work


def stills(film_dir: Path, *, frames: list[int], act: int | None = None, scale: float = 0.5, samples: int = 1,
           out_dir: str | Path | None = None, engine: str | None = None) -> dict:
    """Render stills and return where they landed: {"out_dir", "stills": [{act, frame, film_frame, file}]}.

    An omitted engine follows the built base for mixed acts; pure previews keep the Remotion default. An explicit
    engine chooses the base preview without changing a declared replacement shot's own engine or page.
    """
    if engine is not None and engine not in ("remotion", "web"):
        raise Refused(f"Stills come from the Remotion engine or the open web renderer; the {engine} engine renders "
                      "whole acts.",
                      hint=f"Render the whole act (slate render --engine {engine} --act N --go); every frame is "
                           "then in its registered render (slate render list shows where).")
    if not frames:
        raise UsageError("Say which frames: --frames 0,120,240.")
    if not 0 < scale <= 4:
        raise UsageError(f"--scale must be above 0 and at most 4 (got {scale}).")
    if samples < 1:
        raise UsageError(f"--samples must be at least 1 (got {samples}).")
    film_dir = Path(film_dir).resolve()
    film = deps.load_film(film_dir)
    shots = deps.load_shots(film_dir)
    acts = acts_from_shots(shots)
    groups = _group([int(f) for f in frames], act, acts)
    work = _routes(film_dir, shots, groups, engine)
    out = Path(out_dir).expanduser() if out_dir else film_dir / DEFAULT_DIR
    out = out if out.is_absolute() else film_dir / out
    out.mkdir(parents=True, exist_ok=True)
    made: dict[tuple[int, int], dict] = {}
    with ExitStack() as stack:
        for (name, scene), requests in work.items():
            eng = engines.get(name, film_dir, film, scene=scene)
            eng.preflight()
            # Register before opening: a failed open can still hold a bundle.
            stack.callback(eng.close)
            eng.open()
            for a, fs in requests:
                result = eng.stills(a.index, fs, out, scale=scale, samples=samples)
                files = _files(result)
                for i, f in enumerate(fs):
                    item = files[i] if i < len(files) else None
                    file = item.get("file") if isinstance(item, dict) else item
                    made[a.index, f] = {"act": a.index, "frame": f, "film_frame": a.frame_in + f, "file": file}
    ordered = [made[a.index, f] for a, fs in groups for f in fs]
    return {"ok": True, "out_dir": str(out), "stills": ordered}
