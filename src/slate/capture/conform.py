"""Conform any recording to the film's frame rate as a JPEG sequence (the plate path).

Three sources end up in the same place, `captures/<name>/raw/f00000.jpg …`, one frame per film frame of capture time:

- a video (a Simulator recording, a screen recording you made): every film frame shows the source frame that was on
  screen at that instant, and the last frame is held to the declared duration (simctl only writes frames when the
  screen changes, so a still screen can leave a one-frame file for an eleven-second window);
- screenshots taken at known times (a real-clock web capture): the same rule, from their timestamps;
- screenshots already taken at the film's frame times (a virtual-clock web capture): fitted, frame for frame.

The frame count is exact: `clock.frame_of(duration)` frames, rate and size from film.json.
"""

from __future__ import annotations

import bisect
import os
from pathlib import Path

from slate import clock
from slate.capture import media, store
from slate.errors import UsageError
from slate.gen import deps


def target_size(spec: dict | None, film: dict) -> tuple[int, int] | None:
    """The frame size a capture is fitted to: the spec's size, else the film's for contain/cover, else native."""
    spec = spec or {}
    fit = spec.get("fit", "native")
    if fit == "native":
        return None
    size = spec.get("size") or {"width": film["width"], "height": film["height"]}
    return int(size["width"]), int(size["height"])


def frame_count(duration: float, clk: clock.Clock) -> int:
    count = clk.frame_of(duration)
    if count < 1:
        raise UsageError(f"{duration} s is shorter than one frame at this film's rate.")
    return count


def to_sequence(src: str | os.PathLike, film_dir: Path, name: str, *, duration: float | None = None,
                spec: dict | None = None, method: str = "dup") -> Path:
    """Conform a recording to captures/<name>/raw at the film's frame rate. Returns the raw folder.

    `duration` (or the spec's) is how long the capture is; without one, the recording's own length is used."""
    film_dir = Path(film_dir)
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    info = media.probe(src)
    length = duration or (spec or {}).get("duration") or info.get("duration")
    if not length:
        raise UsageError(f"Can't tell how long {Path(src).name} is; pass --duration.")
    raw = store.capture_dir(film_dir, name) / "raw"
    media.write_sequence(src, raw, fps_num=clk.fps_num, fps_den=clk.fps_den, count=frame_count(float(length), clk),
                         size=target_size(spec, film), fit=(spec or {}).get("fit", "native"), method=method,
                         background=(spec or {}).get("background", "#000000"), info=info)
    return raw


def from_frames(images: list[Path], film_dir: Path, name: str, *, spec: dict | None = None,
                quality: int = media.JPEG_QUALITY) -> Path:
    """Frames already taken at the film's frame times → raw, fitted. Returns the raw folder."""
    from PIL import Image  # noqa: PLC0415

    if not images:
        raise UsageError("No frames to conform.")
    film = deps.load_film(film_dir)
    raw = store.capture_dir(film_dir, name) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(raw)
    size, fit = target_size(spec, film), (spec or {}).get("fit", "native")
    background = (spec or {}).get("background", "#000000")
    for i, path in enumerate(images):
        if fit == "native" and Path(path).suffix.lower() in (".jpg", ".jpeg"):
            media.link_or_copy(Path(path), raw / media.frame_name(i))  # already a JPEG at its film time: no re-encode
            continue
        with Image.open(path) as im:
            media.save_jpeg(media.image_fit(im.convert("RGB"), fit, size, background), raw / media.frame_name(i),
                            quality)
    return raw


def from_timestamps(images: list[Path], stamps: list[float], film_dir: Path, name: str, *, duration: float,
                    spec: dict | None = None, quality: int = media.JPEG_QUALITY) -> Path:
    """Screenshots taken at `stamps` (seconds from the start) → raw at the film's rate. Each film frame shows the
    latest screenshot taken at or before it (the first one before the first stamp). Returns the raw folder."""
    from PIL import Image  # noqa: PLC0415

    if not images or len(images) != len(stamps):
        raise UsageError("Timestamped frames need one timestamp per screenshot.")
    order = sorted(range(len(stamps)), key=lambda i: stamps[i])
    times = [float(stamps[i]) for i in order]
    shots = [images[i] for i in order]
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    count = frame_count(duration, clk)
    raw = store.capture_dir(film_dir, name) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(raw)
    size, fit = target_size(spec, film), (spec or {}).get("fit", "native")
    background = (spec or {}).get("background", "#000000")
    cache: dict[int, Path] = {}
    for k in range(count):
        t = clk.secs_of(k)
        idx = max(0, bisect.bisect_right(times, t + 1e-9) - 1)
        out = raw / media.frame_name(k)
        if idx in cache:
            media.link_or_copy(cache[idx], out)
            continue
        with Image.open(shots[idx]) as im:
            media.save_jpeg(media.image_fit(im.convert("RGB"), fit, size, background), out, quality)
        cache[idx] = out
    return raw


def raw_block(film_dir: Path, raw: Path, film: dict, spec: dict | None) -> dict:
    width, height = media.sequence_size(raw)
    return {"dir": deps.rel(film_dir, raw), "pattern": media.PATTERN, "count": media.count_frames(raw),
            "fps_num": int(film["fps_num"]), "fps_den": int(film["fps_den"]), "width": width, "height": height,
            "fit": (spec or {}).get("fit", "native")}


def place(film_dir: Path, name: str, *, film_in: int | None = None, fit: str = "native") -> dict:
    """No beat map: the film plays the raw sequence as it is (frames/ links raw/). Returns the frames block."""
    cap = store.capture_dir(film_dir, name)
    raw, frames = cap / "raw", cap / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(frames)
    count = media.count_frames(raw)
    for i in range(count):
        media.link_or_copy(raw / media.frame_name(i), frames / media.frame_name(i))
    film = deps.load_film(film_dir)
    block = raw_block(film_dir, frames, film, {"fit": fit})
    block["film_in"] = film_in
    return block
