"""Before an act render replaces the registered one, it proves itself (the films' roll-out procedure, L-render-S001).

1. **The stability report against the act's accepted baseline** (slate.qa.stability: the scan, triaged against the
   act's planned beats and the flags a person accepted). A whole-pixel reversal is a FAIL nothing excuses, and the new
   render isn't installed. New flags over the limits are left for a person (REVIEW, with `slate qa stability --act N`):
   they don't stop the install, because a changed act brings new motion a person has to look at anyway.
2. **The static-frame regression against the render it replaces**, for a rendering-only change: when the act's
   scenes, timing and brand pack are what the registered render was drawn from (its content hash) and only the engine
   or the kit changed, nothing may move, go missing, double or soften (slate.qa.stability.compare_renders). A FAIL
   stops the install. A deliberate rendering change goes in with `--accept-change`, recorded as accepted.
3. **For a roll-out** (`--rollout`: a rendering-only change rolled out act by act), a one-sample preview against the
   act's reference in the old mode comes first, before the full render is spent: the same comparison where the
   reference holds still (a one-sample render has no motion blur).

A refused render is kept beside the registered one (renders/actN.rejected.mov) with a strip that shows why
(qa/stability/strips/), and its chunks are kept, so accepting it never renders it twice. A draft render skips the
checks (it's never delivered), and so does an act's first render (there's nothing to replace).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slate import gates, jsonio, log, paths
from slate.errors import Refused, SlateError
from slate.render import deps

STRIP_FRAMES = 7


def content_hash(film_dir: Path, film: dict) -> str:
    """What an act is drawn from, without the engine's own code: the film's scenes, its timing and its brand pack. Two
    renders with the same content hash differ only in how they were rendered (a kit, engine or pipeline change)."""
    from slate.render import engines  # noqa: PLC0415 (engines imports this module's neighbours)

    film_dir = Path(film_dir)
    listing: list[tuple[str, str]] = []
    for p in engines._code_files(film_dir / "scenes"):  # noqa: SLF001 - the same walk the render's own hash uses
        listing.append((p.relative_to(film_dir).as_posix(), jsonio.sha256(p)))
    timing = film_dir / "timing"
    if timing.is_dir():
        for p in sorted(timing.glob("*.json")):
            listing.append((f"timing/{p.name}", jsonio.sha256(p)))
    brand = film.get("brand")
    if brand:
        for folder in paths.brand_dirs():
            pack = Path(folder) / str(brand) / "brand.json"
            if pack.is_file():
                listing.append((f"brand/{brand}/brand.json", jsonio.sha256(pack)))
                break
    return jsonio.sha256_obj({"content": listing})


def _gate(name: str, status: str, summary: str, **extra: Any) -> dict:
    return gates.GateResult(name, status, summary, evidence=extra.pop("evidence", []),
                            engine=gates.engine("slate render install checks", "1"),
                            details={"stage": "render", **extra}).to_dict()


def _beats(film_dir: Path, stability: Any) -> list[int]:
    """Film frames of the film's planned beats (and burned-in captions coming and going), as the stability gate reads
    them; empty when QA's film context can't be read."""
    try:
        from slate.qa.film import context  # noqa: PLC0415

        ctx = context(film_dir)
        return [int(b["frame"]) for b in ctx.beats()] + list(stability.caption_changes(ctx))
    except Exception:  # noqa: BLE001 - without beats every pop is judged, which only errs toward a person looking
        return []


def _strip(stability: Any, film_dir: Path, name: str, rows: list, frame: int, box, title: str) -> str | None:
    try:
        out = Path(film_dir) / "qa" / "stability" / "strips" / name
        stability.strip(out, rows, max(0, frame - STRIP_FRAMES // 2), STRIP_FRAMES, box, title=title)
        return out.relative_to(film_dir).as_posix()
    except (SlateError, OSError, ValueError) as exc:
        log.warn(f"a strip couldn't be made ({exc})")
        return None


def check(film_dir: Path, *, act: int, frame_in: int, new: Path, old: Path | None, rendering_only: bool | None,
          accept_change: bool = False, jobs: int | None = None) -> dict:
    """The install checks for one act render. Returns {gates, refuse (why it can't be installed, or None), strips,
    notes}. `rendering_only` is True when the act's content is unchanged since the registered render, False when it
    changed, None when that isn't known (the registered render predates content hashes)."""
    film_dir = Path(film_dir)
    stability = deps.optional("slate.qa.stability")
    out: dict = {"gates": [], "refuse": None, "strips": [], "notes": []}
    base = f"render.act{act}.install"
    if stability is None:
        out["gates"].append(_gate(f"{base}.stability", "UNCHECKED", "slate.qa.stability isn't installed, so the new "
                                  "render couldn't be scanned before it replaced the registered one"))
        return out
    # 1. the stability report against the act's accepted baseline
    try:
        raw = stability.scan(new, first_film_frame=frame_in, jobs=jobs)
    except SlateError as exc:
        out["gates"].append(_gate(f"{base}.stability", "UNCHECKED", f"the new render couldn't be scanned: {exc}"))
        raw = None
    if raw is not None:
        tri = stability.triage(raw["flags"], beats=_beats(film_dir, stability),
                               baseline=stability.baseline_flags(film_dir, act))
        open_gated = [f for f in tri["open"] if f["class"] in stability.GATED]
        hard = [f for f in open_gated if f["class"] in stability.HARD]
        counts = stability.counts(open_gated)
        lim = stability.limits()
        over = [c for c in stability.GATED if c not in stability.HARD and counts[c] > lim.get(c, 0)]
        size = tuple(raw.get("size") or (0, 0))
        if hard:
            worst = max(hard, key=lambda f: f.get("severity", 0.0))
            strip = _strip(stability, film_dir, f"act{act}_install_{worst['class']}_{worst['frame']}.png",
                           [("new render", new, 0)] + ([("registered", old, 0)] if old and old.is_file() else []),
                           int(worst["local"]), stability.flag_box(worst, size),
                           f"act {act}, film frame {worst['frame']}: {worst['class']} (not installed)")
            if strip:
                out["strips"].append(strip)
            frames = sorted({int(f["frame"]) for f in hard})
            why = (f"a whole-pixel reversal near rest at film frame{'s' if len(frames) > 1 else ''} "
                   f"{', '.join(map(str, frames[:8]))} (layout rounding under a move), which no baseline excuses")
            out["gates"].append(_gate(f"{base}.stability", "FAIL", why, evidence=[strip] if strip else [],
                                      counts=counts))
            out["refuse"] = why
        elif over:
            out["gates"].append(_gate(f"{base}.stability", "REVIEW",
                                      f"new stability flags over the limits ({', '.join(f'{c} {counts[c]}' for c in over)}): "
                                      f"look at them with `slate qa stability --act {act}`, and accept the ones that "
                                      "belong", counts=counts, over=over))
        else:
            out["gates"].append(_gate(f"{base}.stability", "PASS",
                                      f"nothing new against the act's baseline ({len(tri['near_beats'])} near planned "
                                      f"beats, {len(tri['accepted_by_baseline'])} accepted before)", counts=counts))
    # 2. the static-frame regression against the render it replaces, for a rendering-only change
    if rendering_only is None:
        out["notes"].append("the registered render predates content hashes, so whether this is a rendering-only "
                            "change isn't known; the regression against it was skipped")
    elif rendering_only is False:
        out["notes"].append("the act's scenes, timing or brand changed since the registered render, so the new render "
                            "isn't expected to match it; the regression was skipped")
    elif old is None or not old.is_file():
        out["gates"].append(_gate(f"{base}.regression", "UNCHECKED", "the registered render is missing, so the new one "
                                  "couldn't be compared with it"))
    else:
        r = stability.compare_renders(old, new)
        details = r.details or {}
        if r.status == "FAIL":
            struct = details.get("structure") or []
            strip = None
            if struct:
                x, y = struct[0]["at"]
                side = 90
                strip = _strip(stability, film_dir, f"act{act}_install_regression_{frame_in + int(struct[0]['frame'])}.png",
                               [("registered", old, 0), ("new render", new, 0)], int(struct[0]["frame"]),
                               (max(0, int(x) - side // 2), max(0, int(y) - side // 2), side, side),
                               f"act {act}: a rendering-only change that changed the picture")
                if strip:
                    out["strips"].append(strip)
            if accept_change:
                out["gates"].append(_gate(f"{base}.regression", "REVIEW", f"the picture changed ({r.summary}), and the "
                                          "change was accepted with --accept-change", evidence=[strip] if strip else [],
                                          accepted=True))
            else:
                why = f"a rendering-only change changed the picture: {r.summary}"
                out["gates"].append(_gate(f"{base}.regression", "FAIL", why, evidence=[strip] if strip else []))
                out["refuse"] = out["refuse"] or why
        else:
            out["gates"].append(_gate(f"{base}.regression", r.status, f"against the render it replaces: {r.summary}"))
    return out


def rollout_preview(film_dir: Path, *, act: int, preview: Path, reference: Path | None) -> dict:
    """A roll-out's first step: the act's one-sample preview against its reference in the old mode, where the reference
    holds still (no motion blur in either). Returns a gate result; FAIL stops the roll-out before the full render."""
    stability = deps.module("slate.qa.stability", "QA's render stability")
    name = f"render.act{act}.rollout.preview"
    if reference is None or not Path(reference).is_file():
        raise Refused(f"Act {act} has no reference render to roll out against.",
                      hint=f"Set one first (a one-sample render in the old mode, from the same code): "
                           f"slate qa stability reference --act {act} RENDER")
    r = stability.compare_renders(reference, preview, rest_only=True)
    return _gate(name, r.status, f"the one-sample preview against the reference: {r.summary}")
