"""Perfect, don't replace, measured for the picture's palette and the film's sync (`slate revise check`).

slate.state.revise loads this module and runs each hook on a revision of an approved artifact, as
`fn(film_dir, approved, current, revision) -> GateResult` (the state module files each result as `revise.<name>`):

- palette  a revised render, master or still keeps the approved palette. Frames are sampled at the same points in
           both (nine along a video, or the image itself), and each pair is compared by its mean colour in CIELAB
           (ΔE, where about 2.3 is just noticeable) and by how its lightness and colour are spread (L*, a* and b*
           histograms). The same palette passes; a shift past those is REVIEW with the numbers, because only a
           person can say whether the notes asked for it ("warmer" is a palette change, "move the title" isn't).
- sync     a revised beats file (timing/visual_events/actN.json), cue sheet (timing/vo_cues.json) or mix keeps the
           film in sync. Beats or cues that moved more than half a frame, appeared or went are listed: REVIEW, since
           the notes may have asked for it and the sound has to follow. Then the film's own sync gate runs on the
           revision: its FAIL is this FAIL, and until it can confirm the sync (no verification yet) this is
           UNCHECKED, never a quiet pass.
"""

from __future__ import annotations

import fnmatch
from fractions import Fraction
from pathlib import Path
from typing import Any

from slate import __version__, gates, jsonio
from slate.errors import SlateError
from slate.gates import GateResult

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
VIDEO_EXT = {".mov", ".mp4", ".mxf", ".mkv", ".m4v"}
SAMPLES = 9  # frames compared along a video, at the same fractions of its length on both sides
LONG_SIDE = 96  # analysis size: frames are averaged down to this many px on their long side
MEAN_DE = 2.3  # CIELAB ΔE76 between mean colours: about one just-noticeable difference
SPREAD = 0.15  # the largest share of a histogram allowed to move (total variation distance)
SYNC_FILES = ("timing/visual_events/act*.json", "timing/vo_cues.json", "sound/*.wav", "sound/*/*.wav",
              "sound/*/*/*.wav")


# ---------------------------------------------------------------------------------------------- palette
def applies_palette(path: Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXT | VIDEO_EXT


def _small(w: int, h: int) -> tuple[int, int]:
    if w >= h:
        return LONG_SIDE, max(2, int(round(LONG_SIDE * h / w)))
    return max(2, int(round(LONG_SIDE * w / h))), LONG_SIDE


def frames_for_palette(path: Path, n: int = SAMPLES) -> list:
    """RGB frames (uint8 arrays) sampled evenly along a video, or the image itself, at a small size."""
    import numpy as np  # noqa: PLC0415

    path = Path(path)
    if path.suffix.lower() in IMAGE_EXT:
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as im:
            rgb = im.convert("RGB")
            return [np.asarray(rgb.resize(_small(*rgb.size), Image.Resampling.BOX))]
    from slate.qa import media  # noqa: PLC0415

    info = media.video_info(path)
    total = int(info.get("frames") or 0)
    if total <= 0:
        raise SlateError(f"{path.name} has no frames to sample.")
    picks = sorted({int(round(k * (total - 1) / max(1, n - 1))) for k in range(n)})
    got = media.read_frames(path, picks, size=_small(int(info["width"]), int(info["height"])), pix_fmt="rgb24",
                            info=info)
    return [got[i] for i in picks if i in got]


def lab(frame) -> Any:
    """CIELAB (D65) of an sRGB frame, per pixel, as an (h, w, 3) float array."""
    import numpy as np  # noqa: PLC0415

    x = np.asarray(frame, dtype=np.float64)[..., :3] / 255.0
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def _spread(a, b) -> float:
    """The largest share of the L*, a* or b* histogram that moved between two frames (total variation distance)."""
    import numpy as np  # noqa: PLC0415

    worst = 0.0
    for ch, (lo, hi, bins) in enumerate(((0, 100, 10), (-64, 64, 16), (-64, 64, 16))):
        ha, _ = np.histogram(np.clip(a[..., ch], lo, hi), bins=bins, range=(lo, hi))
        hb, _ = np.histogram(np.clip(b[..., ch], lo, hi), bins=bins, range=(lo, hi))
        ha = ha / max(1, ha.sum())
        hb = hb / max(1, hb.sum())
        worst = max(worst, 0.5 * float(np.abs(ha - hb).sum()))
    return worst


def compare_palettes(approved: list, current: list) -> dict:
    """{mean_de, spread, at} for the worst sample pair (frames compared in order)."""
    import numpy as np  # noqa: PLC0415

    rows = []
    for k, (fa, fb) in enumerate(zip(approved, current)):
        la, lb = lab(fa), lab(fb)
        de = float(np.linalg.norm(la.reshape(-1, 3).mean(axis=0) - lb.reshape(-1, 3).mean(axis=0)))
        rows.append({"sample": k, "mean_de": round(de, 3), "spread": round(_spread(la, lb), 4)})
    worst_de = max(rows, key=lambda r: r["mean_de"]) if rows else None
    worst_spread = max(rows, key=lambda r: r["spread"]) if rows else None
    return {"samples": rows, "mean_de": worst_de["mean_de"] if worst_de else None,
            "spread": worst_spread["spread"] if worst_spread else None,
            "at": {"mean_de": worst_de["sample"] if worst_de else None,
                   "spread": worst_spread["sample"] if worst_spread else None}}


def palette(film_dir: Path, approved: Path | None, current: Path, revision: dict | None = None) -> GateResult:
    """The revised picture keeps the approved palette (mean colour within a just-noticeable ΔE, spread within 15%)."""
    from slate.qa import media  # noqa: PLC0415

    engine = gates.engine("slate qa palette (CIELAB mean ΔE76 and L*a*b* spread)", __version__,
                          decoder=f"ffmpeg {media.version('ffmpeg') or 'not installed'}")
    if approved is None or not Path(approved).is_file():
        return GateResult.unchecked("palette", "the approved version wasn't kept, so there's nothing to compare the "
                                    "palette with", engine=engine)
    try:
        a, b = frames_for_palette(Path(approved)), frames_for_palette(Path(current))
    except (SlateError, OSError, ValueError) as exc:
        return GateResult.unchecked("palette", f"the pictures couldn't be read: {exc}", engine=engine)
    if not a or not b:
        return GateResult.unchecked("palette", "no frames could be read from one of the pictures", engine=engine)
    m = compare_palettes(a, b)
    line = (f"worst mean-colour shift ΔE {m['mean_de']:.2f} (sample {m['at']['mean_de']}), worst spread "
            f"{m['spread'] * 100:.0f}% (sample {m['at']['spread']}) over {len(m['samples'])} sample"
            f"{'s' if len(m['samples']) != 1 else ''}")
    details = {**m, "limits": {"mean_de": MEAN_DE, "spread": SPREAD}}
    evidence = [f"approved {Path(approved).name} vs revised {Path(current).name}: {line}"]
    if m["mean_de"] > MEAN_DE or m["spread"] > SPREAD:
        return GateResult.review("palette", f"the palette moved ({line}): look before calling it the same look, "
                                 "unless the notes asked for it", evidence=evidence, engine=engine, details=details)
    return GateResult.pass_("palette", f"the approved palette holds ({line})", evidence=evidence, engine=engine,
                            details=details)


# ---------------------------------------------------------------------------------------------- sync
def _rel(film_dir: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(film_dir).resolve()))
    except ValueError:
        return str(path)


def applies_sync(path: Path) -> bool:
    p = Path(path)
    parts = p.parts
    for i in range(len(parts)):  # the path relative to some film folder: match on its tail
        tail = "/".join(parts[i:])
        if any(fnmatch.fnmatch(tail, pat) for pat in SYNC_FILES):
            return True
    return False


def _read(path: Path | None) -> Any:
    if path is None or not Path(path).is_file():
        return None
    try:
        return jsonio.read(Path(path))
    except SlateError:
        return None


def _beats(doc: Any) -> dict[tuple[str, str], float] | None:
    items = doc if isinstance(doc, list) else (doc.get("events") if isinstance(doc, dict) else None)
    if isinstance(doc, dict) and doc.get("silent") is True:
        return {}
    if not isinstance(items, list):
        return None
    return {(str(e.get("shot")), str(e.get("id"))): float(e["t"]) for e in items
            if isinstance(e, dict) and isinstance(e.get("t"), (int, float)) and not isinstance(e.get("t"), bool)}


def _cues(doc: Any) -> dict[tuple[str, str], float] | None:
    items = doc.get("cues") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return None
    return {(str(c.get("shot")), "cue"): float(c["at"]) for c in items
            if isinstance(c, dict) and isinstance(c.get("at"), (int, float)) and not isinstance(c.get("at"), bool)}


def moved_items(before: dict, after: dict, tolerance: float) -> dict[str, list[str]]:
    """What moved (more than `tolerance` seconds), appeared or went between two {key: seconds} maps."""
    moved = [f"{k[0]}/{k[1]}: {before[k]:.3f} s → {after[k]:.3f} s ({(after[k] - before[k]) * 1000:+.0f} ms)"
             for k in sorted(set(before) & set(after)) if abs(after[k] - before[k]) > tolerance]
    added = [f"{k[0]}/{k[1]} at {after[k]:.3f} s" for k in sorted(set(after) - set(before))]
    gone = [f"{k[0]}/{k[1]} (was at {before[k]:.3f} s)" for k in sorted(set(before) - set(after))]
    return {"moved": moved, "added": added, "gone": gone}


def sync(film_dir: Path, approved: Path | None, current: Path, revision: dict | None = None) -> GateResult:
    """The revision keeps the film in sync: moved, new or removed beats and cues are listed for a person, and the
    film's sync gate runs on it."""
    from slate.qa.checks.sync import sync as sync_gate  # noqa: PLC0415

    film_dir = Path(film_dir)
    engine = gates.engine("slate qa revise sync", __version__)
    film = jsonio.read(film_dir / "film.json", default={}) or {}
    try:
        fps = Fraction(int(film["fps_num"]), int(film["fps_den"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        fps = None
    half = 0.5 / float(fps) if fps else 0.0
    rel = _rel(film_dir, current)
    changes: dict[str, list[str]] = {"moved": [], "added": [], "gone": []}
    reader = _beats if fnmatch.fnmatch(rel, "timing/visual_events/act*.json") else (
        _cues if rel.endswith("timing/vo_cues.json") else None)
    if reader is not None:
        before, after = reader(_read(approved)), reader(_read(current))
        if before is None or after is None:
            return GateResult.unchecked("sync", f"{rel} or its approved version couldn't be read as "
                                        f"{'beats' if reader is _beats else 'cues'}", engine=engine)
        changes = moved_items(before, after, half)
    try:
        film_sync = sync_gate(film_dir)
    except Exception as exc:  # noqa: BLE001 - the sync gate failing to run is unchecked, never a pass
        return GateResult.unchecked("sync", f"the film's sync gate couldn't run: {type(exc).__name__}: {exc}",
                                    engine=engine)
    listed = [f"moved {m}" for m in changes["moved"]] + [f"new {m}" for m in changes["added"]] + \
        [f"removed {m}" for m in changes["gone"]]
    details = {**changes, "film_sync": {"status": film_sync.status, "summary": film_sync.summary},
               "tolerance_s": round(half, 6)}
    evidence = listed[:30] + [f"sync gate: {film_sync.status}: {film_sync.summary}"]
    if film_sync.status == "FAIL":
        return GateResult.fail("sync", f"the revision leaves the film out of sync: {film_sync.summary}",
                               evidence=evidence, engine=engine, details=details)
    if listed:
        n = len(listed)
        return GateResult.review("sync", f"{n} beat{'s' if n != 1 else ''} or cue{'s' if n != 1 else ''} moved, "
                                 f"appeared or went ({listed[0]}): check the notes asked for {'it' if n == 1 else 'them'}"
                                 " and that the sound follows", evidence=evidence, engine=engine, details=details)
    if film_sync.status != "PASS":
        return GateResult.unchecked("sync", f"nothing moved, but the film's sync can't be confirmed yet "
                                    f"({film_sync.status}: {film_sync.summary})", evidence=evidence, engine=engine,
                                    details=details)
    return GateResult.pass_("sync", "nothing moved and the film's sync gate passes", evidence=evidence,
                            engine=engine, details=details)


# ---------------------------------------------------------------------------------------------- registration
def _register() -> None:
    try:
        from slate.state import revise  # noqa: PLC0415
    except ImportError:
        return
    if hasattr(revise, "register_hook"):
        revise.register_hook("palette", palette, applies=applies_palette, owner="slate.qa")
        revise.register_hook("sync", sync, applies=applies_sync, owner="slate.qa")


_register()

__all__ = ["palette", "sync", "applies_palette", "applies_sync", "compare_palettes", "moved_items", "lab",
           "frames_for_palette"]
