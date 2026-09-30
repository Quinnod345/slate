"""ffmpeg and file helpers for finishing: encoders, filters, sizes, probes, and copies that never change a byte.

The film pipeline's hard-won rules live here so every finishing step follows them:

- never `-shortest` when muxing the mix (the mix can be microseconds shorter than the picture, and -shortest once
  dropped the last four frames of a master);
- every picture carries BT.709 tags;
- frame counts come from counted packets, taking the first number (slate.qa.media.video_info does this);
- the ffmpeg in Homebrew's bottle has no `drawtext`, so burn-ins check for it and fall back to labels drawn with
  Pillow (slate.finish.burnin).

Sizes and frame rates always come from the film (film.json and timing/shots.json), never from this file.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import os
import shutil
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from slate import jsonio, procs
from slate.errors import MissingPrerequisite, SlateError

FFMPEG_HINT = "Install it with Homebrew (`brew install ffmpeg`), or run `slate doctor --install`."
BT709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
MEZZANINE = ("prores",)


def require() -> None:
    """ffmpeg and ffprobe, or a MissingPrerequisite (exit 3) that says how to get them."""
    procs.require("ffmpeg", hint=FFMPEG_HINT)
    procs.require("ffprobe", hint=FFMPEG_HINT)


def _qa_media():
    from slate.finish import deps  # noqa: PLC0415

    mod = deps.module("slate.qa.media")
    if mod is None:
        raise MissingPrerequisite("Finishing reads media through slate.qa, which isn't installed.",
                                  hint="slate doctor names what's missing.")
    return mod


@functools.lru_cache(maxsize=None)
def _listing(flag: str) -> str:
    proc = procs.run(["ffmpeg", "-hide_banner", flag], timeout=60)
    return proc.stdout or ""


def _names(listing: str) -> set[str]:
    out = set()
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) <= 6 and not parts[0].startswith("-"):
            out.add(parts[1])
    return out


def has_filter(name: str) -> bool:
    """True when this ffmpeg build has the filter (Homebrew's bottle has no drawtext)."""
    try:
        return name in _names(_listing("-filters"))
    except SlateError:
        return False


def has_encoder(name: str) -> bool:
    try:
        return name in _names(_listing("-encoders"))
    except SlateError:
        return False


@functools.lru_cache(maxsize=1)
def version() -> str | None:
    try:
        first = (procs.run(["ffmpeg", "-hide_banner", "-version"], timeout=30).stdout or "").splitlines()
    except SlateError:
        return None
    parts = first[0].split() if first else []
    return parts[2] if len(parts) > 2 else None


def engine(what: str, **extra) -> dict:
    """The `engine` field of a gate result made with ffmpeg."""
    out = {"name": f"ffmpeg {what}", "version": version() or "unknown"}
    out.update(extra)
    return out


def run(argv: list[str], *, what: str, timeout: float | None = None) -> None:
    """Run ffmpeg (through slate.procs: no shell, BROWSER=none) and raise a plain-words error when it fails."""
    proc = procs.run(argv, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-900:]
        raise SlateError(f"{what} failed: ffmpeg exited with {proc.returncode}." + (f"\n{tail}" if tail else ""))


def timeout_for(frames: int, per_frame_s: float = 0.25, floor_s: float = 600.0) -> float:
    """A generous timeout that grows with the film (ffmpeg is fast; this only stops a hang)."""
    return max(floor_s, frames * per_frame_s)


# ---------------------------------------------------------------------------------------------------------------
# Sizes and rates


def even(n: float) -> int:
    """The nearest even whole number, at least 2 (H.264 at 4:2:0 needs even sides)."""
    v = int(round(float(n)))
    if v % 2:
        v -= 1
    return max(2, v)


def fit_short_side(width: int, height: int, short_side: int) -> tuple[int, int]:
    """Scale (width, height) so the short side is `short_side`, keeping the aspect; never upscales."""
    short = min(width, height)
    target = min(int(short_side), short)
    k = target / short
    return even(width * k), even(height * k)


def half_size(width: int, height: int) -> tuple[int, int]:
    return even(width / 2), even(height / 2)


def rate_text(fps_num: int, fps_den: int) -> str:
    return f"{int(fps_num)}/{int(fps_den)}"


def rate_of(info: dict) -> Fraction | None:
    if info.get("fps_num") and info.get("fps_den"):
        return Fraction(int(info["fps_num"]), int(info["fps_den"]))
    return None


# ---------------------------------------------------------------------------------------------------------------
# Encoders


def h264_encoder(choice: str) -> str:
    """The H.264 encoder to use: 'auto' is VideoToolbox when this ffmpeg has it (the CPU stays free), else libx264."""
    choice = (choice or "auto").strip()
    if choice == "auto":
        return "h264_videotoolbox" if has_encoder("h264_videotoolbox") else "libx264"
    if choice not in ("libx264", "h264_videotoolbox"):
        raise SlateError(f"Unknown H.264 encoder {choice!r}; use auto, libx264 or h264_videotoolbox.")
    return choice


def h264_args(encoder: str, *, crf: int | None = None, preset: str | None = None, maxrate: str | None = None,
              bitrate: str | None = None, profile: str | None = "high") -> list[str]:
    """Video codec arguments for an H.264 file with BT.709 tags and 4:2:0 (phones and browsers play it)."""
    enc = h264_encoder(encoder)
    args = ["-c:v", enc]
    if enc == "libx264":
        args += ["-preset", str(preset or "medium"), "-crf", str(crf if crf is not None else 20)]
        if maxrate:
            args += ["-maxrate", str(maxrate), "-bufsize", _double(str(maxrate))]
        if profile:
            args += ["-profile:v", profile]
    else:
        args += ["-b:v", str(bitrate or maxrate or "8M"), "-tag:v", "avc1"]
        if maxrate:
            args += ["-maxrate", str(maxrate)]
    return args + ["-pix_fmt", "yuv420p", *BT709]


def _double(rate: str) -> str:
    """'700k' -> '1400k' (a VBV buffer twice the ceiling)."""
    num = "".join(ch for ch in rate if ch.isdigit() or ch == ".")
    unit = rate[len(num):]
    try:
        return f"{float(num) * 2:g}{unit}"
    except ValueError:
        return rate


# ---------------------------------------------------------------------------------------------------------------
# Probing


def video_info(path: str | os.PathLike, *, count: bool = True) -> dict:
    """The first video stream (size, exact rate, codec, pixel format, counted frames): slate.qa.media.video_info."""
    return _qa_media().video_info(Path(path), count=count)


def frames(path: str | os.PathLike) -> int | None:
    try:
        return video_info(path).get("frames")
    except SlateError:
        return None


def probe_summary(path: str | os.PathLike) -> dict | None:
    """What ffprobe reads from a media file, for the manifest (duration, video and audio streams); None otherwise."""
    p = Path(path)
    if p.suffix.lower() not in (".mov", ".mp4", ".m4v", ".mkv", ".wav", ".aiff", ".m4a", ".mp3"):
        return None
    try:
        data = _qa_media().probe(p)
    except SlateError as exc:
        return {"error": str(exc)[:200]}
    out: dict[str, Any] = {}
    try:
        out["duration"] = round(float((data.get("format") or {}).get("duration") or 0), 4)
    except (TypeError, ValueError):
        out["duration"] = None
    for st in data.get("streams") or []:
        if st.get("codec_type") == "video" and "video" not in out:
            out["video"] = {k: st.get(k) for k in ("codec_name", "profile", "width", "height", "r_frame_rate",
                                                   "pix_fmt", "color_primaries", "color_transfer", "nb_frames")}
        elif st.get("codec_type") == "audio" and "audio" not in out:
            out["audio"] = {k: st.get(k) for k in ("codec_name", "sample_rate", "channels", "bits_per_raw_sample",
                                                   "bits_per_sample")}
    return out


def stream_durations(path: str | os.PathLike) -> dict[str, float | None]:
    """{"video": seconds, "audio": seconds} of the first stream of each kind (None when there's none)."""
    out: dict[str, float | None] = {"video": None, "audio": None}
    try:
        streams = _qa_media().probe(Path(path)).get("streams") or []
    except SlateError:
        return out
    for st in streams:
        kind = st.get("codec_type")
        if kind in out and out[kind] is None:
            try:
                out[kind] = float(st.get("duration"))
            except (TypeError, ValueError):
                out[kind] = None
    return out


def audio_problem(path: str | os.PathLike, frame_s: float) -> str | None:
    """Why the sound doesn't run the picture's length (within a frame), or None when it does. The films' share-copy
    check: a mux with -shortest once cut the picture short to match a mix a few microseconds shorter."""
    d = stream_durations(path)
    if d["audio"] is None or d["video"] is None:
        return None
    if abs(d["audio"] - d["video"]) > frame_s + 0.03:
        return f"the sound runs {d['audio']:.3f} s and the picture {d['video']:.3f} s"
    return None


def has_audio(path: str | os.PathLike) -> bool:
    try:
        return bool(_qa_media().audio_streams(Path(path)))
    except SlateError:
        return False


# ---------------------------------------------------------------------------------------------------------------
# Files


def concat_list(files: list[Path], dest: Path) -> Path:
    """An ffconcat list (for `-f concat -safe 0 -i`), quoting each path the way the demuxer wants."""
    lines = []
    for f in files:
        text = str(Path(f).resolve()).replace("'", "'\\''")
        lines.append(f"file '{text}'\n")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(lines), encoding="utf-8")
    return dest


def _clonefile(src: Path, dst: Path) -> bool:
    """APFS clone (copy-on-write): instant, no extra space, and an independent file. False when unavailable."""
    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib", use_errno=True)
        fn = getattr(libc, "clonefile", None)
        if fn is None:
            return False
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        fn.restype = ctypes.c_int
        return fn(os.fsencode(str(src)), os.fsencode(str(dst)), 0) == 0
    except (OSError, AttributeError):
        return False


def copy_exact(src: Path, dst: Path, *, sha256: str | None = None) -> dict:
    """Copy `src` to `dst` (a clone where the disk allows it) and prove the bytes match. Never overwrites."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        raise SlateError(f"{dst} already exists; delivered files are never overwritten.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    method = "clone" if _clonefile(src, dst) else "copy"
    if method == "copy":
        shutil.copy2(src, dst)
    want = sha256 or jsonio.sha256(src)
    got = jsonio.sha256(dst)
    if got != want:
        dst.unlink(missing_ok=True)
        raise SlateError(f"Copying {src.name} changed its bytes (sha256 {got[:12]} ≠ {want[:12]}); nothing was kept.")
    return {"method": method, "sha256": got, "bytes": dst.stat().st_size}


def free_name(folder: Path, name: str) -> Path:
    """`folder/name`, or `name (2)`, `name (3)`… when it's taken (copies for a person never replace their files)."""
    cand = folder / name
    if not cand.exists():
        return cand
    stem, suffix = (name, "") if (folder / name).is_dir() or "." not in name else (Path(name).stem, Path(name).suffix)
    n = 2
    while True:
        cand = folder / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1
