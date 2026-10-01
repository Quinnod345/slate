"""Plate timing repair: `slate plates repair <shot>` (the films' pipeline/tools/plate_repair.py, generalized).

Why. Generated video can hitch in the clip itself, at the model's chunk joins: the films' Seedance plates lost one frame
every second (a step about twice as far as its neighbours) or froze one (a step of nothing, then a double step to
catch up). The model's own file, the native-rate conform and the film-rate JPEG sequence all carry it, and on screen
it's a stutter in a slow landscape move, once a second, which the stability gate reads as snaps and reversals. The
conform can't fix it: ffmpeg's minterpolate turns 24p into the film's rate on an even clock, and it ignores a concat
list's frame durations, so it can't follow an uneven one.

What it does:

1. Measures each source step's motion on the plate's moving band (by default the middle three quarters across and
   the middle 44% down, where the films' terrain and ridge were; `--band` moves it), by iterative Lucas-Kanade
   (slate.gen.flow.shift) at half size. A whole-frame dense median isn't used: a still sky makes it noisy.
2. Puts every source frame on a true-time axis, where a step takes as many units as its motion against its
   neighbours' median (six each side): 1 for a normal step, its own ratio (about 1.8 in the films, 2 for a frame
   dropped outright) for a missing frame, its ratio for any step more than 12% off, and none for a frozen frame (under
   0.3 of its neighbours), whose copy is dropped. A plate that barely moves (its neighbours' median under 0.3 px a
   step) is left as it is.
3. Makes each film frame at its even place on that axis. The axis spans the source's first frame to its last (U units
   over D seconds), so plate frame k is still the film's k / fps, and the first and last frames stay where they were.
   Within 2% of a source frame, that frame is used as it is; otherwise its two neighbours are interpolated with
   optical flow (slate.gen.flow, at half size): each is warped to the in-between time and the two are blended by
   distance. `--blend` blends without warping: the fallback for a frame that reads as generated.
4. Moves the plate's edge track (track.json, the films' ridge samples) through the same time warp, then resamples it
   onto its own grid, so a line drawn on the ridge stays on it.
5. Checks the result: no output step over 1.3× the mean of its neighbours (the films' worst after repair was 1.13×,
   against 2.2× at each hitch before).

Everything is staged first, in plates/<shot>/plate/repair/ (the frames and a report naming every re-timed step, every
dropped frozen frame, every synthesised frame and the step check). `--install` puts the staged frames in place only
when the step check is clean, and keeps the old sequence, plate.json and track.json in plates/<shot>/plate/
before_repair/ (before_repair.2/ and so on for a later repair). The approved take itself is never touched.

The same step check runs at every `slate plates conform`, so a plate that hitches says so the moment it's conformed.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from slate import jsonio, log, procs
from slate.capture import media
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.gen import deps, flow, plates, store

COMMAND = "slate plates repair"
# The moving band, as fractions of the frame (x0, y0, x1, y1): the films' landscape band, where their terrain and ridge
# were (the middle three quarters across, the middle 44% down).
BAND = (0.125, 0.278, 0.875, 0.722)
MEASURE_SCALE = 0.5  # the band is measured at half size: Lucas-Kanade's 0.02 px there is 0.04 px on the plate
WIN = 6  # neighbours each side whose median a step is judged against
TOL = 0.12  # a step more than 12% off its neighbours is re-timed
FROZEN = 0.3  # under 0.3 of its neighbours: a frozen frame, which takes no time and whose copy is dropped
MISSING = 1.6  # over 1.6: a missing frame, which takes its measured units (at most 3)
MAX_UNITS = 3.0
STILL_PX = 0.3  # a plate whose steps' median is under this (px at full size) barely moves: left as it is
EXACT = 0.02  # within 2% of a source frame, that frame is used as it is
STEP_LIMIT = 1.3  # the check: no step over 1.3× the mean of its two neighbours
FLOW_SCALE = 0.5  # the flow is computed at half size, as the films did
CHUNK_BYTES = 96 << 20  # raw video decoded per ffmpeg call: bounded, so a long plate never sits in memory
JPEG_QUALITY = 95
REPAIR_DIR = "repair"
KEEP_DIR = "before_repair"


# ---------------------------------------------------------------------------------------------------------------
# The band and the step measurements


def parse_band(text: str | None) -> tuple[float, float, float, float]:
    """'0.125,0.278,0.875,0.722' → the band as fractions (x0, y0, x1, y1). None gives the default."""
    if text is None or str(text).strip() == "":
        return BAND
    try:
        x0, y0, x1, y1 = (float(v) for v in str(text).replace(" ", "").split(","))
    except ValueError as exc:
        raise UsageError(f"--band takes four fractions of the frame, x0,y0,x1,y1 (like {','.join(map(str, BAND))}), "
                         f"not {text!r}.") from exc
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise UsageError("--band's fractions run from 0 to 1, with x0 < x1 and y0 < y1.")
    return x0, y0, x1, y1


def band_box(size: tuple[int, int], band: tuple[float, float, float, float] = BAND) -> tuple[int, int, int, int]:
    """The band in pixels (x, y, w, h) on a frame of `size` (w, h), even-sized so it halves cleanly."""
    w, h = size
    x0, y0 = int(round(band[0] * w)), int(round(band[1] * h))
    x1, y1 = int(round(band[2] * w)), int(round(band[3] * h))
    bw, bh = max(8, (x1 - x0) // 2 * 2), max(8, (y1 - y0) // 2 * 2)
    return x0, y0, min(bw, w - x0), min(bh, h - y0)


def _half(img: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return img.astype(np.float64)
    from scipy import ndimage as ndi  # noqa: PLC0415

    return ndi.zoom(ndi.gaussian_filter(img.astype(np.float64), 0.5 / scale), scale, order=1)


def steps_of(bands: Iterable[np.ndarray], *, scale: float = 1.0) -> np.ndarray:
    """Each step's motion (px at full size): step i is frame i−1 → i, and step 0 is 0. `bands` are the band crops of
    consecutive frames (luma, 0–255), already at `scale` of full size."""
    out = [0.0]
    prev = None
    for b in bands:
        b = np.asarray(b, dtype=np.float64)
        if prev is not None:
            dx, dy = flow.shift(prev, b)
            out.append(float(np.hypot(dx, dy)) / scale)
        prev = b
    return np.array(out if prev is not None else [], dtype=np.float64)


def true_time(m: np.ndarray, *, win: int = WIN, tol: float = TOL) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(units per step, every source frame's place on the true-time axis, each step's kind: "", "missing", "uneven" or
    "frozen"). The films' rule, exactly (see the module docstring)."""
    n = len(m)
    units = np.ones(n)
    if n:
        units[0] = 0.0
    kinds = [""] * n
    for i in range(1, n):
        nb = [m[j] for j in range(max(1, i - win), min(n, i + win + 1)) if j != i]
        if not nb:
            continue
        ref = float(np.median(nb))
        if ref < STILL_PX:
            continue  # a plate that barely moves is left as it is
        r = m[i] / ref
        if r < FROZEN:
            units[i], kinds[i] = 0.0, "frozen"
        elif r > MISSING:
            units[i], kinds[i] = round(min(MAX_UNITS, r), 3), "missing"
        elif abs(r - 1) > tol:
            units[i], kinds[i] = round(r, 3), "uneven"
    return units, np.cumsum(units), kinds


def check(m: np.ndarray, *, limit: float = STEP_LIMIT) -> list[dict]:
    """Steps over `limit` × the mean of their two neighbours (where the neighbours move at all): none should be. Each
    is {frame: the frame the step lands on, step, neighbours, ratio}. `m` is steps_of's output (m[0] is no step).

    A frozen frame's own step is reported by frozen_steps; the normal steps either side of it, which look large only
    against it, aren't counted again here."""
    bad = []
    frozen = set(frozen_steps(m))
    s = np.asarray(m, dtype=np.float64)[1:]
    for i in range(1, len(s) - 1):
        if (i) in frozen or (i + 2) in frozen:  # s[i] is step i + 1; its neighbours are steps i and i + 2
            continue
        nb = (s[i - 1] + s[i + 1]) / 2
        if nb > STILL_PX and s[i] > limit * nb:
            bad.append({"frame": i + 1, "step": round(float(s[i]), 3), "neighbours": round(float(nb), 3),
                        "ratio": round(float(s[i] / nb), 2)})
    return bad


def frozen_steps(m: np.ndarray) -> list[int]:
    """Frames that repeat the one before while the plate around them moves (a step under 0.3 of its neighbours)."""
    s = np.asarray(m, dtype=np.float64)
    out = []
    for i in range(2, len(s) - 1):
        nb = (s[i - 1] + s[i + 1]) / 2
        if nb > STILL_PX and s[i] < FROZEN * nb:
            out.append(i)
    return out


def _jpeg_band(path: Path, box: tuple[int, int, int, int]) -> np.ndarray:
    from PIL import Image  # noqa: PLC0415

    x, y, w, h = box
    with Image.open(path) as im:
        g = np.asarray(im.convert("L").crop((x, y, x + w, y + h)), dtype=np.float64)
    return g


def sequence_steps(folder: Path, *, band: tuple[float, float, float, float] = BAND,
                   scale: float = MEASURE_SCALE) -> np.ndarray:
    """steps_of for a JPEG sequence on disk (f00000.jpg …), measured on its band at `scale`."""
    files = sorted(Path(folder).glob(media.GLOB))
    if len(files) < 2:
        return np.zeros(len(files))
    box = band_box(media.sequence_size(folder), band)
    return steps_of((_half(_jpeg_band(p, box), scale) for p in files), scale=scale)


def step_check(folder: Path, *, band: tuple[float, float, float, float] = BAND,
               scale: float = MEASURE_SCALE) -> dict:
    """The plate conform's step check: every step's motion on the band, the steps over 1.3× their neighbours, and
    frozen frames. Written into plate.json as `steps`."""
    m = sequence_steps(folder, band=band, scale=scale)
    moving = m[1:] if len(m) > 1 else m
    over = check(m)
    return {"band": list(band), "measured_at": scale, "median_px": round(float(np.median(moving)), 3) if len(moving)
            else 0.0, "over_1_3": over, "frozen": frozen_steps(m), "limit": STEP_LIMIT,
            "steps_px": [round(float(v), 3) for v in m]}


# ---------------------------------------------------------------------------------------------------------------
# The source, decoded the way the conform decodes it


@dataclass
class Source:
    """The approved take, decoded at its own rate, fitted to the film's frame like `slate plates conform` does."""

    path: Path
    info: dict
    fps: Fraction
    size: tuple[int, int]
    fit: str

    def _chain(self, extra: list[str]) -> list[str]:
        chain = media.fit_filter(self.fit, self.size)
        return chain + extra

    def _decode(self, start: int, count: int, extra: list[str], pix_fmt: str, frame_bytes: int) -> bytes:
        seek = max(0.0, (start - 0.5) / float(self.fps))
        argv = [media.ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{seek:.6f}", "-i", str(self.path), "-map", "0:v:0",
                "-an", "-sn", "-frames:v", str(count), "-vf", ",".join(self._chain(extra)), "-pix_fmt", pix_fmt,
                "-f", "rawvideo", "-"]
        proc = procs.run(argv, text=False, timeout=1800)
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
            raise SlateError(f"ffmpeg couldn't decode {self.path.name}: {err}")
        return proc.stdout or b""

    def bands(self, box: tuple[int, int, int, int], scale: float) -> Iterator[np.ndarray]:
        """Every frame's band, luma on 0–255 (10-bit precise), at `scale`, in order."""
        x, y, w, h = box
        sw, sh = max(8, int(round(w * scale)) // 2 * 2), max(8, int(round(h * scale)) // 2 * 2)
        extra = [f"crop={w}:{h}:{x}:{y}", f"scale={sw}:{sh}:flags=area"]
        frame_bytes = sw * sh * 2
        per = max(1, CHUNK_BYTES // frame_bytes)
        start = 0
        while True:
            raw = self._decode(start, per, extra, "gray16le", frame_bytes)
            got = len(raw) // frame_bytes
            arr = np.frombuffer(raw[:got * frame_bytes], dtype="<u2").reshape(got, sh, sw)
            for f in arr:
                yield f.astype(np.float64) * (255.0 / 65535.0)
            start += got
            if got < per:
                return

    def frames(self) -> "Frames":
        return Frames(self)


class Frames:
    """Full-size RGB frames of the source by index (floats, 0–255, from 16-bit decodes), read forward in bounded
    chunks: at most two chunks are ever held."""

    def __init__(self, src: Source):
        self.src = src
        w, h = src.size
        self.frame_bytes = w * h * 3 * 2
        self.per = max(2, CHUNK_BYTES // self.frame_bytes)
        self.chunks: dict[int, np.ndarray] = {}
        self.extra = [f"scale=iw:ih:in_color_matrix={media.input_matrix(src.info)}:out_range=pc"]

    def __getitem__(self, i: int) -> np.ndarray:
        c = i // self.per
        if c not in self.chunks:
            raw = self.src._decode(c * self.per, self.per, self.extra, "rgb48le", self.frame_bytes)
            got = len(raw) // self.frame_bytes
            w, h = self.src.size
            arr = np.frombuffer(raw[:got * self.frame_bytes], dtype="<u2").reshape(got, h, w, 3)
            for k in [k for k in self.chunks if k < c - 1]:
                del self.chunks[k]
            self.chunks[c] = arr
        arr = self.chunks[c]
        j = i - c * self.per
        if j >= len(arr):
            raise SlateError(f"The take ended before frame {i}.")
        return arr[j].astype(np.float32) * np.float32(255.0 / 65535.0)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]).astype(np.float32)


# ---------------------------------------------------------------------------------------------------------------
# The plan: where each film frame sits on the true-time axis


def placements(u: np.ndarray, kinds: list[str], *, src_fps: float, out_fps: float, n_out: int) -> list[dict]:
    """For every output frame k: the two source frames it sits between (frozen copies aren't on the axis) and how far
    along, or the one source frame it is. [{k, a, b, f, use}] with use "a", "b" or "between"."""
    n = len(u)
    keep = [i for i in range(n) if kinds[i] != "frozen"]
    if len(keep) < 2:
        raise Refused("The take has fewer than two distinct frames, so there's no timing to repair.")
    uk = np.array([u[i] for i in keep], dtype=np.float64)
    big_u = float(u[-1])
    dur = (n - 1) / src_fps
    out = []
    for k in range(n_out):
        uu = min(big_u, k / out_fps * big_u / dur) if dur > 0 else 0.0
        j = int(np.clip(np.searchsorted(uk, uu, side="right") - 1, 0, len(keep) - 2))
        span = uk[j + 1] - uk[j]
        f = 0.0 if span <= 0 else float((uu - uk[j]) / span)
        ia, ib = keep[j], keep[j + 1]
        use = "a" if f < EXACT else "b" if f > 1 - EXACT else "between"
        out.append({"k": k, "a": ia, "b": ib, "f": round(f, 6), "use": use})
    return out


def warp_samples(samples: list[dict], u: np.ndarray, *, src_fps: float, every: float, count: int,
                 out_fps: float) -> list[dict]:
    """A track's samples (keyed by plate time t, on an `every`-second grid) moved through the time warp and resampled
    onto the same grid, the films' warp_ridges: old plate time t is source time t (the conform runs on an even clock),
    so it moves to u(t · fps) · D / U. Each new sample interpolates its two neighbours' points and crest span."""
    if len(samples) < 2:
        return [dict(s) for s in samples]
    n = len(u)
    big_u = float(u[-1])
    dur = (n - 1) / src_fps
    frames = np.arange(n, dtype=np.float64)
    old = sorted(samples, key=lambda s: float(s["t"]))
    new_t = [float(np.interp(float(s["t"]) * src_fps, frames, u)) * dur / big_u if big_u > 0 else float(s["t"])
             for s in old]
    out = []
    t = 0.0
    end = max(new_t[-1], float(old[-1]["t"]))  # the same grid as before (a sample past the last warped one holds it)
    while t <= end + 1e-9:
        k = int(np.searchsorted(new_t, t, side="right")) - 1
        k = max(0, min(len(old) - 2, k))
        lo, hi = new_t[k], new_t[k + 1]
        f = 0.0 if hi == lo else min(1.0, max(0.0, (t - lo) / (hi - lo)))
        a, b = old[k], old[k + 1]
        pts = [[pa[0], round(pa[1] + f * (pb[1] - pa[1]), 2)] for pa, pb in zip(a["pts"], b["pts"])]
        s = {"t": round(t, 3), "frame": min(count - 1, int(round(t * out_fps))), "pts": pts}
        if "span" in a and "span" in b:
            s["span"] = [round(a["span"][j] + f * (b["span"][j] - a["span"][j]), 4) for j in range(2)]
        out.append(s)
        t = round(t + every, 6)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Stage and install


def _paths(film_dir: Path, shot_id: str, stage: str | None = None) -> dict[str, Path]:
    plate = store.shot_dir(film_dir, shot_id) / "plate"
    stage_dir = Path(stage).expanduser() if stage else plate / REPAIR_DIR
    if not stage_dir.is_absolute():
        stage_dir = Path(film_dir) / stage_dir
    return {"plate": plate, "record": plate / "plate.json", "frames": plate / "frames", "track": plate / "track.json",
            "stage": stage_dir, "staged": stage_dir / "frames", "report": stage_dir / "report.json"}


def _load_plate(film_dir: Path, shot_id: str, p: dict[str, Path]) -> dict:
    rec = jsonio.read(p["record"], default=None)
    if not isinstance(rec, dict):
        raise MissingPrerequisite(f"{shot_id} has no conformed plate yet, so there's nothing to repair.",
                                  hint=f"slate plates conform --shot {shot_id}")
    if (rec.get("source") or {}).get("codec") == "image" or rec.get("frames", {}).get("method") == "exact":
        raise Refused(f"{shot_id}'s plate is a still, so it has no timing to repair.")
    return rec


def _source(film_dir: Path, shot_id: str, rec: dict) -> Source:
    _rec, take, path = plates._approved(film_dir, shot_id)  # noqa: SLF001 - the one check that a take is the approved file
    if take.get("sha256") != (rec.get("source") or {}).get("sha256"):
        raise Refused(f"{shot_id}'s plate was conformed from another take than the one approved now.",
                      hint=f"slate plates conform --shot {shot_id} first, then repair it.")
    info = media.probe(path)
    rate = info.get("fps")
    fps = Fraction(rate) if rate else None
    if not fps or fps <= 0:
        raise UsageError(f"{path.name} doesn't say its frame rate, so its steps can't be put on a clock.")
    if info.get("vfr"):
        raise Refused(f"{path.name} has a variable frame rate: its timing is in its timestamps, not its frames, so "
                      "this repair (which reads an even clock) doesn't apply.")
    film = deps.load_film(film_dir)
    size = deps.film_size(film)
    fit = str((rec.get("tool") or {}).get("fit") or rec.get("frames", {}).get("fit") or "cover")
    return Source(path=path, info=info, fps=fps, size=size, fit=fit)


def _save_jpeg(rgb: np.ndarray, path: Path) -> None:
    from PIL import Image  # noqa: PLC0415

    img = Image.fromarray(np.clip(rgb + 0.5, 0, 255).astype(np.uint8), "RGB")
    img.save(path, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=False)


def stage(film_dir: Path, shot_id: str, *, blend: bool = False, band: tuple[float, float, float, float] = BAND,
          stage_dir: str | None = None, dry_run: bool = False) -> dict:
    """Measure, re-time and re-synthesise a plate into its staging folder, with its report. Nothing in place changes.
    A dry run measures and plans only: it writes nothing, not even the staging folder."""
    film_dir = Path(film_dir)
    store.check_shot_id(shot_id)
    p = _paths(film_dir, shot_id, stage_dir)
    rec = _load_plate(film_dir, shot_id, p)
    src = _source(film_dir, shot_id, rec)
    if not dry_run:
        _clear_stage(p)  # a stage is always this run's: an older one is never installed by mistake
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    n_out = int(rec["frames"]["count"])
    box = band_box(src.size, band)
    log.info(f"{shot_id}: measuring each step of {src.path.name} on the band {box}")
    m = steps_of(src.bands(box, MEASURE_SCALE), scale=MEASURE_SCALE)
    n = len(m)
    if n < 3:
        raise Refused(f"{src.path.name} has {n} frame(s): too few to judge its timing.")
    units, u, kinds = true_time(m)
    retimed = [{"step_to_source_frame": i, "kind": kinds[i], "units": float(units[i]), "motion_px": round(float(m[i]), 3)}
               for i in range(n) if kinds[i] in ("missing", "uneven")]
    frozen = [i for i in range(n) if kinds[i] == "frozen"]
    median = float(np.median(m[1:]))
    base = {"schema": 1, "shot": shot_id, "made_at": deps.now_iso(),
            "source": {"file": deps.rel(film_dir, src.path), "sha256": rec["source"]["sha256"],
                       "fps": f"{src.fps.numerator}/{src.fps.denominator}", "frames": n},
            "band": list(band), "band_px": list(box), "measured_at": MEASURE_SCALE, "median_step_px": round(median, 3),
            "source_steps": [round(float(v), 3) for v in m], "retimed_steps": retimed, "frozen_dropped": frozen,
            "frames": n_out, "fps": f"{clk.fps_num}/{clk.fps_den}"}
    if not retimed and not frozen:
        why = ("it barely moves" if median < STILL_PX else
               f"every step is within {round(TOL * 100)}% of its neighbours")
        return {**base, "needed": False, "staged": None,
                "summary": f"{shot_id}: nothing to repair: {why} (median step {median:.2f} px)."}
    big_u, dur = float(u[-1]), (n - 1) / float(src.fps)
    plan = placements(u, kinds, src_fps=float(src.fps), out_fps=clk.fps, n_out=n_out)
    if dry_run:
        between = sum(1 for row in plan if row["use"] not in ("a", "b"))
        return {**base, "needed": True, "dry_run": True, "staged": None, "placements": plan,
                "would_stage": deps.rel(film_dir, p["staged"]),
                "summary": f"{shot_id}: {n} source frames, {len(retimed)} step(s) to re-time, {len(frozen)} frozen to "
                           f"drop, {big_u:.2f} units over {dur:.3f} s; {between} of {n_out} frames would be "
                           f"{'blended' if blend else 'interpolated with optical flow'}. Dry run: nothing written."}
    staged = p["staged"]
    staged.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(staged)
    frames = src.frames()
    made, cache_pair, flows, splines = [], None, None, {}
    log.info(f"{shot_id}: {len(retimed)} step(s) to re-time and {len(frozen)} frozen frame(s) to drop; writing "
             f"{n_out} frames")
    for row in plan:
        k, ia, ib, f = row["k"], row["a"], row["b"], row["f"]
        if row["use"] == "a":
            out = frames[ia]
        elif row["use"] == "b":
            out = frames[ib]
        else:
            fa, fb = frames[ia], frames[ib]
            if blend:
                out = flow.blend(fa, fb, f)
            else:
                if cache_pair != (ia, ib):
                    la, lb = _luma(fa), _luma(fb)
                    flows = (flow.dense(la, lb, scale=FLOW_SCALE), flow.dense(lb, la, scale=FLOW_SCALE))
                    splines = {i: splines[i] for i in (ia, ib) if i in splines}
                    for i, fr in ((ia, fa), (ib, fb)):
                        if i not in splines:
                            splines[i] = flow.spline(fr)
                    cache_pair = (ia, ib)
                out = flow.between(fa, fb, flows[0], flows[1], f, prefiltered=(splines[ia], splines[ib]))
            made.append(k)
        _save_jpeg(out, staged / media.frame_name(k))
    if media.count_frames(staged) != n_out:
        raise SlateError(f"The repair wrote {media.count_frames(staged)} frames where {n_out} were needed.")
    after = sequence_steps(staged, band=band)
    over = check(after)
    track = jsonio.read(p["track"], default=None)
    warped = None
    if isinstance(track, dict) and track.get("samples"):
        every = float((track.get("params") or {}).get("every") or 0.25)
        warped = warp_samples(track["samples"], u, src_fps=float(src.fps), every=every, count=n_out,
                              out_fps=clk.fps)
    method = {"date": deps.now_iso()[:10],
              "retime": "true time from the moving band's motion (Lucas-Kanade at half size); frozen copies dropped",
              "conform": ("a blend of the two neighbouring source frames" if blend else
                          "dense optical flow (coarse-to-fine Lucas-Kanade, numpy/scipy, at half size): both "
                          "neighbours warped to the in-between time and blended by distance")
                         + "; each film frame at its even place on the true-time axis",
              "units": round(big_u, 3), "seconds": round(dur, 6), "blend": bool(blend)}
    report = {**base, "needed": True, "method": method, "interpolated_frames": made,
              "source_frames_used_as_they_are": n_out - len(made), "placements": plan,
              "step_check": {"median_px": round(float(np.median(after[1:])), 3) if len(after) > 1 else 0.0,
                             "over_1_3": over, "frozen": frozen_steps(after), "limit": STEP_LIMIT,
                             "steps_px": [round(float(v), 3) for v in after]},
              "track": None if warped is None else {"samples": warped, "every": float(
                  (track.get("params") or {}).get("every") or 0.25)},
              "staged": deps.rel(film_dir, staged)}
    report = jsonio.mark_generated(report, COMMAND)
    jsonio.write_atomic(p["report"], report)
    worst = max((o["ratio"] for o in over), default=None)
    report["summary"] = (f"{shot_id}: {n} source frames, {len(retimed)} step(s) re-timed, {len(frozen)} frozen "
                         f"dropped, {big_u:.2f} units over {dur:.3f} s; {len(made)} of {n_out} frames interpolated "
                         f"({'blended' if blend else 'optical flow'}); steps over {STEP_LIMIT}× their neighbours: "
                         + (f"{len(over)} (worst {worst}×)" if over else "none"))
    return report


def _clear_stage(p: dict[str, Path]) -> None:
    """Remove what an earlier stage wrote (only its own frames and report)."""
    media.clear_sequence(p["staged"])
    if p["staged"].is_dir() and not any(p["staged"].iterdir()):
        p["staged"].rmdir()
    if p["report"].is_file():
        p["report"].unlink()


def _keep_dir(plate_dir: Path) -> Path:
    first = plate_dir / KEEP_DIR
    if not first.exists():
        return first
    n = 2
    while (plate_dir / f"{KEEP_DIR}.{n}").exists():
        n += 1
    return plate_dir / f"{KEEP_DIR}.{n}"


def _mattes_on(film_dir: Path, frames_rel: str) -> list[str]:
    """Bridge mattes whose real frames are this plate (they were built on the old timing)."""
    out = []
    for path in sorted(store.plates_dir(film_dir).glob("*/matte/matte.json")):
        doc = jsonio.read(path, default=None) or {}
        if (doc.get("real") or {}).get("dir") == frames_rel:
            out.append(path.parent.parent.name)
    return out


def install(film_dir: Path, shot_id: str, *, stage_dir: str | None = None, dry_run: bool = False) -> dict:
    """Put the staged frames in place, keeping the old sequence and records. Refuses a stale or unclean stage. A dry
    run makes every check and says what would move, moving nothing."""
    film_dir = Path(film_dir)
    store.check_shot_id(shot_id)
    p = _paths(film_dir, shot_id, stage_dir)
    rec = _load_plate(film_dir, shot_id, p)
    report = jsonio.read(p["report"], default=None)
    if not isinstance(report, dict) or not report.get("needed"):
        raise MissingPrerequisite(f"There's no staged repair for {shot_id} to install.",
                                  hint=f"slate plates repair {shot_id} (it stages, and says what it found)")
    if report.get("source", {}).get("sha256") != rec["source"]["sha256"] or int(report.get("frames") or 0) != int(
            rec["frames"]["count"]):
        raise Refused(f"The staged repair of {shot_id} was made from another conform of the plate than the one in "
                      "place now.", hint=f"slate plates repair {shot_id} again")
    over = (report.get("step_check") or {}).get("over_1_3") or []
    if over:
        frames_txt = ", ".join(str(o["frame"]) for o in over[:8])
        raise Refused(f"The staged plate still steps over {STEP_LIMIT}× its neighbours at frame(s) {frames_txt}, so "
                      "it isn't installed.",
                      hint=f"Look at those frames; try slate plates repair {shot_id} --band … on the moving part of "
                           f"the picture, or --blend.")
    staged = p["staged"]
    if media.count_frames(staged) != int(rec["frames"]["count"]):
        raise Refused(f"The staged frames for {shot_id} are incomplete.", hint=f"slate plates repair {shot_id} again")
    keep = _keep_dir(p["plate"])
    if dry_run:
        return {"shot": shot_id, "dry_run": True, "installed": False, "count": int(rec["frames"]["count"]),
                "frames": deps.rel(film_dir, p["frames"]), "staged": deps.rel(film_dir, staged),
                "kept": deps.rel(film_dir, keep),
                "summary": f"{shot_id}: the staged repair passes its checks; installing would move the old sequence to "
                           f"{deps.rel(film_dir, keep)} and put {int(rec['frames']['count'])} repaired frames in "
                           f"{deps.rel(film_dir, p['frames'])}. Dry run: nothing moved.",
                "next": [f"slate plates repair {shot_id} --install"]}
    keep.mkdir(parents=True)
    shutil.copy2(p["record"], keep / "plate.json")
    if p["track"].is_file():
        shutil.copy2(p["track"], keep / "track.json")
    os.rename(p["frames"], keep / "frames")
    try:
        os.rename(staged, p["frames"])
    except OSError:
        os.rename(keep / "frames", p["frames"])  # put the old sequence back: the plate is never left without frames
        raise
    os.replace(p["report"], keep / "repair_report.json")  # the stage is used up: it installs once
    frames_rel = deps.rel(film_dir, p["frames"])
    method = dict(report.get("method") or {})
    rec = dict(rec)
    rec["repair"] = {"method": method, "report": deps.rel(film_dir, keep / "repair_report.json"),
                     "kept": deps.rel(film_dir, keep), "installed_at": deps.now_iso(),
                     "retimed_steps": len(report.get("retimed_steps") or []),
                     "frozen_dropped": len(report.get("frozen_dropped") or []),
                     "interpolated_frames": len(report.get("interpolated_frames") or []),
                     "step_check": {k: v for k, v in (report.get("step_check") or {}).items() if k != "steps_px"}}
    rec["steps"] = {"band": report.get("band"), "measured_at": report.get("measured_at"),
                    "median_px": (report.get("step_check") or {}).get("median_px"), "over_1_3": [],
                    "frozen": (report.get("step_check") or {}).get("frozen") or [], "limit": STEP_LIMIT,
                    "steps_px": (report.get("step_check") or {}).get("steps_px") or []}
    rec["warnings"] = [w for w in rec.get("warnings") or [] if "hitches" not in w]
    rec = jsonio.mark_generated(rec, COMMAND)
    deps.require_part(rec, "plate", what=f"{shot_id}'s plate record")
    jsonio.write_atomic(p["record"], rec)
    deps.record_made(film_dir, deps.rel(film_dir, p["record"]), producer=COMMAND,
                     inputs={rec["source"]["file"]: rec["source"]["sha256"]})
    track_out = None
    tr = report.get("track")
    if tr and p["track"].is_file():
        track = jsonio.read(p["track"])
        track["samples"] = tr["samples"]
        track["repaired"] = {"report": rec["repair"]["report"], "kept": deps.rel(film_dir, keep / "track.json"),
                             "how": "moved through the plate's time warp, then resampled onto its own grid"}
        track = jsonio.mark_generated(track, COMMAND)
        deps.require_part(track, "plate", "track", what=f"{shot_id}'s edge track")
        jsonio.write_atomic(p["track"], track)
        track_out = deps.rel(film_dir, p["track"])
    if p["stage"].is_dir() and not any(p["stage"].iterdir()):
        p["stage"].rmdir()
    stale = _mattes_on(film_dir, frames_rel)
    return {"shot": shot_id, "installed": True, "frames": frames_rel, "count": int(rec["frames"]["count"]),
            "kept": deps.rel(film_dir, keep), "plate": deps.rel(film_dir, p["record"]), "track": track_out,
            "mattes_to_rebuild": stale,
            "next": [f"slate plates matte --shot {s} … (it was built on the old timing)" for s in stale]}


def run(film_dir: Path, shot_id: str, *, blend: bool = False, band: str | None = None, do_install: bool = False,
        stage_dir: str | None = None, dry_run: bool = False) -> dict:
    """`slate plates repair`: stage the repair (or, with do_install, install what's staged). The full report stays in
    the staging folder; this returns what a person or an agent needs to decide. A dry run measures and plans the
    staging run and writes nothing; with do_install it checks the stage and says what would move."""
    if do_install:
        return install(film_dir, shot_id, stage_dir=stage_dir, dry_run=dry_run)
    rep = stage(film_dir, shot_id, blend=blend, band=parse_band(band), stage_dir=stage_dir, dry_run=dry_run)
    p = _paths(Path(film_dir), shot_id, stage_dir)
    out = {k: rep.get(k) for k in ("shot", "needed", "summary", "source", "band", "median_step_px", "retimed_steps",
                                   "frozen_dropped", "frames", "staged")}
    if rep.get("dry_run"):
        out.update(dry_run=True, would_stage=rep.get("would_stage"),
                   next=[f"slate plates repair {shot_id}   (stages the repair; nothing in place changes)"])
        return out
    if rep.get("needed"):
        check_ = rep.get("step_check") or {}
        out.update(report=deps.rel(Path(film_dir), p["report"]),
                   interpolated=len(rep.get("interpolated_frames") or []),
                   step_check={"median_px": check_.get("median_px"), "over_1_3": check_.get("over_1_3")},
                   track=bool(rep.get("track")), method=rep.get("method"))
        out["next"] = ([f"slate plates repair {shot_id} --install   (keeps the old sequence in "
                        f"plates/{shot_id}/plate/{KEEP_DIR}/)"] if not check_.get("over_1_3") else
                       [f"look at the frames named in the step check; try --band on the moving part, or --blend"])
    return out
