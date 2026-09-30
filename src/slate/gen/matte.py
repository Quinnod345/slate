"""Bridge mattes: a generation's shape reveals real frames; its pixels never become the picture.

Generalized from the films' bridge_matte.py. Quinn liked a generated ink bleed between two shots, but not what the
generated pixels did next ("you can obviously tell it's AI-generated"). So only the bridge's shape survives:

1. Matte: each bridge frame's difference from its own first frame (luminance, blurred so a thin generated line and
   the model's grain don't count), through a soft threshold. It's held monotonic (the ink never retreats) and holes
   are filled, so where the generation stayed near the base's tone the reveal doesn't read as a tear.
2. Spread: late in the bridge, every pixel within a growing reach of the ink joins it, through a feathered front
   made uneven by smooth noise, so it finishes like ink rather than a wipe. A last short ramp takes the matte to
   exactly 1 on the final frame, the hand-over.
3. Composite, per film frame: the real frames where the matte is, the film's own base (its background at that
   moment, no line, words or grain) where it isn't. Only in the wet edge band, 4·m·(1−m), does the bridge's own
   texture show, and an optional tint, the way a watercolour bleed dries. Code draws everything else on top.

Sizes are fractions of the frame height and times are fractions of the bridge's length, so the same look holds at
any film size and frame rate. The defaults are the films' values (8 px of blur on a full-HD frame, the spread from
55 % to 95 % of the bridge, and so on).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from slate import jsonio, log
from slate.capture import media, store as capture_store
from slate.errors import Refused, UsageError
from slate.gen import deps, plates, store

DEFAULTS: dict = {
    "lo": 5.0,               # difference (0–255) where the matte starts
    "hi": 34.0,              # …and where it's full: a soft, feathered edge
    "blur": 0.0074,          # of frame height: the difference is blurred first (8 px on a full-HD frame)
    "hole_threshold": 0.3,
    "hole_blur": 0.0056,     # of frame height (6 px on a full-HD frame)
    "spread_start": 0.55,    # of the bridge's length (2.2 s of the films' 4 s bridge)
    "spread_end": 0.95,      # (3.8 s of 4 s)
    "reach": 0.926,          # of frame height: how far the spread reaches (1000 px on a full-HD frame)
    "feather": 0.13,         # of frame height: the spread's soft front (140 px)
    "wobble_sigma": 0.037,   # of frame height: smoothness of the front's unevenness (40 px)
    "wobble_amp": 0.065,     # of frame height: how uneven the front is (70 px)
    "seed": 7,
    "complete": 0.05,        # of the bridge's length: the last ramp to exactly 1 (0.2 s of 4 s)
    "ai_edge": 0.65,         # how much of the bridge's own texture shows, only in the edge band
    "edge": 0.14,            # the tint's strength in the middle of the edge band
    "tint": None,            # "#rrggbb" for a wet-edge tint (brand data), or None for none
}


def smoothstep(a: float, b: float, x):
    import numpy as np  # noqa: PLC0415

    u = np.clip((x - a) / (b - a), 0, 1)
    return u * u * (3 - 2 * u)


def _lum(img):
    import numpy as np  # noqa: PLC0415

    return img @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _load(path: Path, size: tuple[int, int]):
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    with Image.open(path) as im:
        img = media.image_fit(im.convert("RGB"), "cover", size)
        return np.asarray(img, dtype=np.float32)


def _tint(value: str | None):
    import numpy as np  # noqa: PLC0415

    if not value:
        return None
    text = value.lstrip("#")
    if len(text) != 6:
        raise UsageError(f"tint must be #rrggbb, not {value!r}.")
    return np.array([int(text[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def composite(bridge: list, real: list[Path], base, *, size: tuple[int, int], fps: float, bridge_fps: float,
              count: int, params: dict, out_dir: Path, preview_dir: Path | None = None) -> list[Path]:
    """Write `count` composite frames (JPEG) into out_dir. `bridge` is a list of frame paths (PNG) or arrays."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415
    from scipy import ndimage  # noqa: PLC0415

    width, height = size
    p = {**DEFAULTS, **(params or {})}
    blur, hole_blur = p["blur"] * height, p["hole_blur"] * height
    reach, feather = p["reach"] * height, p["feather"] * height
    tint = _tint(p.get("tint"))
    n_ai = len(bridge)
    if n_ai < 2:
        raise UsageError("A bridge matte needs a generated clip of at least two frames.")
    if not real:
        raise UsageError("A bridge matte needs the real frames it reveals.")

    def frame(k: int):
        item = bridge[k]
        return item if isinstance(item, np.ndarray) else _load(item, size)

    base_lum = _lum(frame(0))
    mattes: dict[int, np.ndarray] = {}
    frames_cache: dict[int, np.ndarray] = {}
    state = {"k": -1, "run": None}

    def matte_at(k: int):
        while state["k"] < k:
            state["k"] += 1
            f = frame(state["k"])
            frames_cache[state["k"]] = f
            d = ndimage.gaussian_filter(np.abs(_lum(f) - base_lum), blur)
            m = smoothstep(p["lo"], p["hi"], d)
            run = m if state["run"] is None else np.maximum(state["run"], m)
            filled = ndimage.binary_fill_holes(run > p["hole_threshold"]).astype(np.float32)
            run = np.maximum(run, ndimage.gaussian_filter(filled, hole_blur))
            state["run"] = run
            mattes[state["k"]] = run.astype(np.float32)
            for old in [j for j in mattes if j < state["k"] - 1]:
                mattes.pop(old, None)
                frames_cache.pop(old, None)
        return mattes[k], frames_cache[k]

    rng = np.random.default_rng(int(p["seed"]))
    noise = rng.standard_normal((height, width)).astype(np.float32)
    wobble = ndimage.gaussian_filter(noise, p["wobble_sigma"] * height)
    wobble = wobble / (np.abs(wobble).max() + 1e-6) * (p["wobble_amp"] * height)
    last_t = (count - 1) / fps if count > 1 else 0.0
    length = max(last_t, 1e-6)
    s0, s1 = p["spread_start"] * length, p["spread_end"] * length
    complete = max(p["complete"] * length, 1e-6)
    out_dir.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(out_dir)
    written = []
    real_cache: dict[int, np.ndarray] = {}
    for i in range(count):
        u = i / fps
        x = min(u * bridge_fps, n_ai - 1)
        k0 = int(np.floor(x))
        k1 = min(k0 + 1, n_ai - 1)
        w = x - k0
        m0, f0 = matte_at(k0)
        m1, f1 = matte_at(k1)
        m = m0 * (1 - w) + m1 * w
        g = float(np.clip((u - s0) / max(s1 - s0, 1e-6), 0, 1))
        r = reach * (g * g * (3 - 2 * g))
        if r > 0:
            dist = ndimage.distance_transform_edt(m < 0.5) + wobble
            m = np.maximum(m, 1 - smoothstep(r - feather, r, dist))
        done = float(np.clip((u - (last_t - complete)) / complete, 0, 1)) if count > 1 else 1.0
        m = np.maximum(m, done * done * (3 - 2 * done))
        if i == count - 1:
            m = np.ones_like(m)  # the hand-over frame is exactly the real frame
        ri = min(i, len(real) - 1)
        if ri not in real_cache:
            real_cache.clear()
            real_cache[ri] = _load(real[ri], size)
        plate = real_cache[ri]
        ai = f0 * (1 - w) + f1 * w
        band = 4 * m * (1 - m)
        comp = base * (1 - m[..., None]) + plate * m[..., None]
        comp = comp * (1 - (p["ai_edge"] * band)[..., None]) + ai * (p["ai_edge"] * band)[..., None]
        if tint is not None:
            comp = comp * (1 - (p["edge"] * band)[..., None]) + tint * (p["edge"] * band)[..., None]
        path = out_dir / media.frame_name(i)
        media.save_jpeg(Image.fromarray(np.clip(comp, 0, 255).astype(np.uint8)), path)
        written.append(path)
        if preview_dir is not None and (i % max(1, count // 10) == 0 or i == count - 1):
            preview_dir.mkdir(parents=True, exist_ok=True)
            Image.fromarray((m * 255).astype(np.uint8)).save(preview_dir / f"m{i:05d}.png")
    return written


def _real_frames(film_dir: Path, ref: str) -> tuple[list[Path], str]:
    """The real frames: `capture:<name>`, a shot id with a conformed plate, or a folder of f00000.jpg frames."""
    if ref.startswith("capture:"):
        folder = capture_store.capture_dir(film_dir, ref[len("capture:"):]) / "frames"
    elif _looks_like_shot(ref) and (store.shot_dir(film_dir, ref) / "plate" / "frames").is_dir():
        folder = store.shot_dir(film_dir, ref) / "plate" / "frames"
    else:
        folder = Path(ref).expanduser()
        folder = folder if folder.is_absolute() else Path(film_dir) / folder
    frames = sorted(folder.glob(media.GLOB))
    if not frames:
        raise UsageError(f"No real frames at {folder} (f00000.jpg …).",
                         hint="Point --real at a conformed plate (its shot id), capture:<name>, or a frames folder.")
    return frames, deps.rel(film_dir, folder)


def _looks_like_shot(ref: str) -> bool:
    try:
        store.check_shot_id(ref)
        return "/" not in ref
    except UsageError:
        return False


def build(film_dir: Path, shot_id: str, *, base: str, real: str, duration: float | None = None,
          params: dict | None = None, preview: bool = False) -> dict:
    """The bridge matte for a shot whose generate block is a bridge: its approved take's shape reveals `real`."""
    film_dir = Path(film_dir)
    rec, take, src = plates._approved(film_dir, shot_id)  # noqa: SLF001 - the same approval rule as a plate
    if (rec.get("block") or {}).get("reason") != "bridge-matte":
        log.warn(f"{shot_id}'s generate block says '{(rec.get('block') or {}).get('reason')}', not bridge-matte")
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    size = deps.film_size(film)
    info = media.probe(src)
    if not info.get("fps_float"):
        raise Refused(f"{shot_id}'s approved take has no frame rate; a bridge matte needs a video.")
    length = duration or float(info["duration"])
    count = clk.frame_of(length)
    if count < 2:
        raise UsageError("A bridge matte needs at least two film frames.")
    base_path = Path(base).expanduser()
    base_path = base_path if base_path.is_absolute() else film_dir / base_path
    if not base_path.is_file():
        raise UsageError(f"No base image at {base_path}: the film's own background at the bridge's first frame, "
                         "with no line, words or grain.")
    real_frames, real_rel = _real_frames(film_dir, real)
    params = {**DEFAULTS, **(params or {})}
    out = store.shot_dir(film_dir, shot_id) / "matte"
    tmp = Path(tempfile.mkdtemp(prefix="slate-matte-"))
    try:
        bridge = media.extract_png(src, tmp, size=size)
        base_img = _load(base_path, size)
        written = composite(bridge, real_frames, base_img, size=size, fps=clk.fps, bridge_fps=float(info["fps_float"]),
                            count=count, params=params, out_dir=out / "frames",
                            preview_dir=(out / "preview") if preview else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    record = {"schema": 1, "shot": shot_id, "take": take["id"],
              "bridge": {"file": deps.rel(film_dir, src), "sha256": take["sha256"], "fps": info.get("fps"),
                         "frames": info.get("frames"), "request_id": take.get("request_id")},
              "base": {"file": deps.rel(film_dir, base_path), "sha256": jsonio.sha256(base_path)},
              "real": {"dir": real_rel, "count": len(real_frames)},
              "params": params,
              "frames": {"dir": deps.rel(film_dir, out / "frames"), "pattern": media.PATTERN, "count": len(written),
                         "first": 0, "fps_num": clk.fps_num, "fps_den": clk.fps_den, "width": size[0],
                         "height": size[1], "method": "exact", "fit": "cover"},
              "mattes": {"dir": deps.rel(film_dir, out / "preview")} if preview else None,
              "made_at": deps.now_iso()}
    record = jsonio.mark_generated(record, "slate plates matte")
    deps.require_part(record, "plate", "matte", what=f"{shot_id}'s matte record")
    path = jsonio.write_atomic(out / "matte.json", record)
    deps.lock_record(film_dir, deps.rel(film_dir, path), producer="slate plates matte",
                     inputs={deps.rel(film_dir, src): take["sha256"], record["base"]["file"]: record["base"]["sha256"]})
    return {"shot": shot_id, "matte": deps.rel(film_dir, path), "frames": record["frames"]}
