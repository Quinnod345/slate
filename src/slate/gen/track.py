"""Edge tracks: where a plate's land meets its sky, per sample time, so code can draw exactly on it.

Generalized from the films' plate_ridge.py, which tracked a landscape plate's ridgeline so the film's own line sat on
the ridge instead of floating near it. The technique is general (code graphics aligned to real footage), the colours
aren't: the films hard-coded one hue range. Here the colour of the "land" is data from the caller (a hue range in
degrees, a minimum saturation and brightness), usually taken from the brand's palette or read off the plate.

For each sample time the conformed plate frame is read, and per column the edge is the first row where the land
begins: the first row whose next few rows are mostly land-coloured, so a stray thin contour in the distance doesn't
count. The profile is median-filtered, lightly smoothed and resampled to N points across the frame (the films' line
used 64 vertices, the first one left-most). A temporal median over five samples removes one-off glitches where haze
breaks the edge. Each sample also carries its crest span: the part of the frame where the land rises clearly above
the level at the left edge, which is where a line should sit (never on the hazy ends).

Sizes are fractions of the frame; times are film seconds. Output: plates/<shot>/plate/track.json.
"""

from __future__ import annotations

from pathlib import Path

from slate import jsonio
from slate.capture import media
from slate.errors import UsageError
from slate.gen import deps, store

DEFAULTS = {
    "sat_min": 0.45,        # how saturated a land pixel is (0–1)
    "val_min": 0.18,        # how bright (0–1)
    "run": 0.0139,          # of frame height: rows below the edge that must be mostly land (15 px on a full-HD frame)
    "fill": 0.8,            # …and how much of them
    "median": 0.013,        # of frame width: the median filter's half-width (25 px)
    "smooth": 0.0089,       # of frame width: the box smoothing (17 px)
    "crest": 0.037,         # of frame height: how far above the left edge's level counts as the crest (40 px)
    "points": 64,
    "every": 0.25,          # seconds between samples
}


def parse_hue(text: str) -> tuple[float, float]:
    """'165-215' → (165, 215). A range that wraps past 360 is fine: '340-20'."""
    try:
        lo, hi = (float(x) for x in str(text).split("-", 1))
    except ValueError as exc:
        raise UsageError(f"--hue takes a range in degrees like 165-215, not {text!r}.") from exc
    if not (0 <= lo < 360 and 0 <= hi < 360):
        raise UsageError("Hue degrees run from 0 to 359.")
    return lo, hi


def land_mask(img, hue: tuple[float, float], sat_min: float, val_min: float):
    import numpy as np  # noqa: PLC0415

    rgb = img.astype(np.float32) / 255.0
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    deg = np.degrees(np.arctan2(np.sqrt(3) * (g - b), 2 * r - g - b)) % 360
    lo, hi = hue
    in_hue = (deg >= lo) & (deg <= hi) if lo <= hi else (deg >= lo) | (deg <= hi)
    return (sat >= sat_min) & (mx >= val_min) & in_hue


def edge(img, hue: tuple[float, float], params: dict | None = None) -> list[list[float]] | None:
    """The land's top edge in one frame, as `points` [x, y] pairs across the width. None when there's no land."""
    import numpy as np  # noqa: PLC0415
    from scipy import ndimage  # noqa: PLC0415

    p = {**DEFAULTS, **(params or {})}
    height, width = img.shape[:2]
    mask = land_mask(img, hue, p["sat_min"], p["val_min"])
    run = max(2, round(p["run"] * height))
    need = max(1, int(np.ceil(p["fill"] * run)))
    cum = np.cumsum(np.vstack([np.zeros((1, width), np.float32), mask.astype(np.float32)]), axis=0)
    solid = (cum[run:, :] - cum[:-run, :]) >= need
    has = solid.any(axis=0)
    if not has.any():
        return None
    ys = np.where(has, solid.argmax(axis=0), np.nan).astype(np.float64)
    xs = np.arange(width)
    good = ~np.isnan(ys)
    ys = np.interp(xs, xs[good], ys[good])
    k = max(1, round(p["median"] * width))
    med = ndimage.median_filter(ys, size=2 * k + 1, mode="nearest")
    s = max(1, round(p["smooth"] * width)) | 1
    smooth = np.convolve(np.pad(med, s // 2, mode="edge"), np.ones(s) / s, mode="valid")
    px = np.linspace(0, width - 1, int(p["points"]))
    return [[round(float(x), 2), round(float(np.interp(x, xs, smooth)), 2)] for x in px]


def track_frames(frames: list[Path], times: list[float], indices: list[int], hue: tuple[float, float],
                 params: dict | None = None) -> list[dict]:
    """Samples [{t, frame, pts, span}], with a five-sample temporal median per vertex and per span end."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    p = {**DEFAULTS, **(params or {})}
    samples = []
    height = None
    for t, i, path in zip(times, indices, frames):
        with Image.open(path) as im:
            img = np.asarray(im.convert("RGB"))
        height = img.shape[0]
        pts = edge(img, hue, p)
        if pts is None:
            raise UsageError(f"No land in the given colour range at {t:.2f} s ({path.name}): check --hue, --sat "
                             "and --val against the plate.")
        samples.append({"t": round(t, 3), "frame": i, "pts": pts})
    ys = np.array([[pt[1] for pt in s["pts"]] for s in samples])
    for k, s in enumerate(samples):
        lo, hi = max(0, k - 2), min(len(samples), k + 3)
        med = np.median(ys[lo:hi], axis=0)
        s["pts"] = [[pt[0], round(float(med[j]), 2)] for j, pt in enumerate(s["pts"])]
        plateau = float(np.median(med[:6]))
        crest = np.nonzero(med < plateau - p["crest"] * (height or 1))[0]
        n = len(med) - 1
        i0, i1 = (max(0, crest[0] - 2), min(n, crest[-1] + 2)) if len(crest) else (0, n)
        s["span"] = [round(i0 / n, 4), round(i1 / n, 4)]
    spans = np.array([s["span"] for s in samples])
    for k, s in enumerate(samples):
        lo, hi = max(0, k - 2), min(len(samples), k + 3)
        s["span"] = [round(float(np.median(spans[lo:hi, 0])), 4), round(float(np.median(spans[lo:hi, 1])), 4)]
    return samples


def run(film_dir: Path, shot_id: str, *, hue: str, frames_dir: str | None = None, params: dict | None = None,
        until: float | None = None) -> dict:
    """Track the land's edge through a shot's conformed plate (or any film-rate frames) → track.json."""
    film_dir = Path(film_dir)
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    folder = store.shot_dir(film_dir, shot_id) / "plate" / "frames"
    if frames_dir:
        folder = Path(frames_dir).expanduser()
        folder = folder if folder.is_absolute() else film_dir / folder
    files = sorted(folder.glob(media.GLOB))
    if not files:
        raise UsageError(f"No frames to track at {folder}.", hint=f"slate plates conform --shot {shot_id} first.")
    p = {**DEFAULTS, **(params or {})}
    last = (len(files) - 1) / clk.fps
    end = min(last, until) if until is not None else last
    times, indices = [], []
    t = 0.0
    while t <= end + 1e-9:
        i = min(len(files) - 1, clk.frame_of(t))
        times.append(t)
        indices.append(i)
        t += float(p["every"])
    range_ = parse_hue(hue)
    samples = track_frames([files[i] for i in indices], times, indices, range_, p)
    width, height = media.sequence_size(folder)
    out = {"schema": 1, "shot": shot_id, "frames": {"dir": deps.rel(film_dir, folder), "count": len(files)},
           "fps_num": clk.fps_num, "fps_den": clk.fps_den, "width": width, "height": height,
           "hue": list(range_), "params": p, "samples": samples, "made_at": deps.now_iso()}
    out = jsonio.mark_generated(out, "slate plates track")
    deps.require_part(out, "plate", "track", what=f"{shot_id}'s edge track")
    target = store.shot_dir(film_dir, shot_id) / "plate" / "track.json"
    jsonio.write_atomic(target, out)
    return {"shot": shot_id, "track": deps.rel(film_dir, target), "samples": len(samples),
            "y_range": [min(pt[1] for s in samples for pt in s["pts"]), max(pt[1] for s in samples for pt in s["pts"])]}
