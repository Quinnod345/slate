"""Video in, JPEG sequences out: the plate path shared by captures and generated plates.

The films learned that video decoders crash long parallel renders, so everything recorded or generated is conformed
to a JPEG sequence at the film's frame rate and played from film time (`frame = round((t - t0) * fps)`). This module
does that conform, exactly:

- the frame rate and size always come from film.json, never from a constant;
- the output has exactly the frame count asked for (the last frame is held when a source runs short, as a Simulator
  recording does: it only emits frames when the screen changes);
- a variable-frame-rate source shows, at each film frame, the source frame that was on screen at that moment;
- colour is converted explicitly (BT.709 video to full-range JPEG), because a silent matrix mismatch shifts brand
  colours, and 4:4:4 chroma keeps UI text crisp.

All child processes go through slate.procs (BROWSER=none, no shell, a timeout).
"""

from __future__ import annotations

import json
import os
import shutil
from fractions import Fraction
from pathlib import Path
from typing import Iterable

from slate import procs
from slate.errors import MissingPrerequisite, SlateError, UsageError

PATTERN = "f%05d.jpg"
GLOB = "f[0-9][0-9][0-9][0-9][0-9].jpg"
FITS = ("native", "contain", "cover")
METHODS = ("mci", "blend", "dup")
JPEG_QUALITY = 95


def frame_name(index: int) -> str:
    return f"f{index:05d}.jpg"


def ffmpeg() -> str:
    return procs.require("ffmpeg", "Slate needs ffmpeg for conforms: `slate doctor --install` sets it up.")


def ffprobe() -> str:
    return procs.require("ffprobe", "Slate needs ffprobe for conforms: `slate doctor --install` sets it up.")


def ffmpeg_version() -> str:
    out = procs.run([ffmpeg(), "-version"], timeout=30).stdout or ""
    first = out.splitlines()[0] if out else ""
    parts = first.split()
    return parts[2] if len(parts) > 2 else "unknown"


def _fraction(text: str | None) -> Fraction | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        value = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe(path: str | os.PathLike) -> dict:
    """What a video (or image) is: size, codec, frame rates, duration, frame count, audio, colour, VFR."""
    src = Path(path)
    if not src.is_file():
        raise UsageError(f"No such file: {src}")
    out = procs.run([ffprobe(), "-v", "error", "-show_entries",
                     "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration,"
                     "pix_fmt,color_space,color_transfer,color_primaries,sample_rate,channels"
                     ":format=duration,format_name", "-of", "json", str(src)], timeout=60, check=True).stdout
    data = json.loads(out or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise UsageError(f"{src.name} has no video stream.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    r_rate, avg_rate = _fraction(video.get("r_frame_rate")), _fraction(video.get("avg_frame_rate"))
    duration = None
    for candidate in (video.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue
    frames = None
    try:
        frames = int(video.get("nb_frames"))
    except (TypeError, ValueError):
        frames = None
    fps = avg_rate or r_rate
    vfr = bool(r_rate and avg_rate and abs(float(r_rate) - float(avg_rate)) > 0.01)
    if frames and duration and fps and abs(frames - duration * float(fps)) > 1.5:
        vfr = True
    return {
        "file": str(src), "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        "codec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
        "fps": str(fps) if fps else None, "fps_float": round(float(fps), 6) if fps else None,
        "r_frame_rate": video.get("r_frame_rate"), "avg_frame_rate": video.get("avg_frame_rate"),
        "duration": duration, "frames": frames, "vfr": vfr,
        "audio": audio is not None,
        "audio_rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "audio_channels": audio.get("channels") if audio else None,
        "color_space": video.get("color_space"), "color_transfer": video.get("color_transfer"),
        "color_primaries": video.get("color_primaries"),
    }


def input_matrix(info: dict) -> str:
    """The colour matrix a source was encoded with. Untagged HD video is BT.709 by convention, SD is BT.601."""
    tag = (info.get("color_space") or "").lower()
    if tag in ("bt709",):
        return "bt709"
    if tag in ("smpte170m", "bt470bg", "bt601"):
        return "bt601"
    if tag.startswith("bt2020"):
        return "bt2020"
    return "bt709" if min(info.get("width") or 0, info.get("height") or 0) >= 720 else "bt601"


def fit_filter(fit: str, size: tuple[int, int] | None, background: str = "#000000") -> list[str]:
    """ffmpeg filters that fit a picture into `size` (contain pads, cover crops, native keeps the source size)."""
    if fit not in FITS:
        raise UsageError(f"fit must be one of {', '.join(FITS)}, not {fit!r}.")
    if fit == "native" or size is None:
        return []
    width, height = size
    if fit == "contain":
        colour = "0x" + background.lstrip("#")
        return [f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={colour}"]
    return [f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={width}:{height}"]


def rate_filter(method: str, fps_num: int, fps_den: int) -> str:
    """Frame-rate conversion. dup: the frame on screen at each film frame (right for captures and VFR sources);
    mci: motion-compensated interpolation (the films' plate conform, for slow drifts); blend: cross-fades."""
    rate = f"{fps_num}/{fps_den}"
    if method == "dup":
        return f"fps=fps={rate}:start_time=0:round=up"
    if method == "mci":
        return f"minterpolate=fps={rate}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    if method == "blend":
        return f"minterpolate=fps={rate}:mi_mode=blend"
    raise UsageError(f"method must be one of {', '.join(METHODS)}, not {method!r}.")


def clear_sequence(folder: Path) -> None:
    """Remove a previous sequence's frames (only files named like ours) so counts never mix two runs."""
    if folder.is_dir():
        for f in folder.glob(GLOB):
            f.unlink()


def count_frames(folder: Path) -> int:
    return len(list(Path(folder).glob(GLOB))) if Path(folder).is_dir() else 0


def sequence_size(folder: Path) -> tuple[int, int]:
    from PIL import Image  # noqa: PLC0415

    first = Path(folder) / frame_name(0)
    if not first.is_file():
        raise UsageError(f"{folder} has no first frame ({frame_name(0)}).")
    with Image.open(first) as im:
        return im.size


def write_sequence(src: str | os.PathLike, out_dir: str | os.PathLike, *, fps_num: int, fps_den: int, count: int,
                   size: tuple[int, int] | None = None, fit: str = "native", method: str = "dup",
                   background: str = "#000000", info: dict | None = None, timeout: float = 3600) -> dict:
    """Conform a video to exactly `count` JPEG frames at fps_num/fps_den in out_dir (f00000.jpg …)."""
    if count < 1:
        raise UsageError("A sequence needs at least one frame.")
    info = info or probe(src)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    clear_sequence(out)
    seconds = count * fps_den / fps_num
    chain = [rate_filter(method, fps_num, fps_den), f"tpad=stop_mode=clone:stop_duration={seconds + 1:.3f}"]
    chain += fit_filter(fit, size, background)
    chain.append(f"scale=iw:ih:in_color_matrix={input_matrix(info)}:out_color_matrix=bt601:out_range=pc")
    chain.append("format=yuvj444p")
    argv = [ffmpeg(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-an", "-vf", ",".join(chain),
            "-fps_mode", "passthrough", "-frames:v", str(count), "-q:v", "2", "-start_number", "0",
            str(out / PATTERN)]
    procs.run(argv, check=True, timeout=timeout)
    made = count_frames(out)
    if made != count:
        raise SlateError(f"The conform made {made} frames where {count} were needed ({Path(src).name}).",
                         hint="The source may be damaged; ffprobe it, or record it again.")
    width, height = sequence_size(out)
    return {"dir": str(out), "count": made, "fps_num": fps_num, "fps_den": fps_den, "width": width,
            "height": height, "method": method, "fit": fit}


def write_audio(src: str | os.PathLike, out: str | os.PathLike, *, rate: int = 48000) -> dict:
    """A source's own audio as 48 kHz / 24-bit PCM WAV (the films' ambience format), with a long resampling filter."""
    dest = Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    procs.run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-af",
               "aresample=filter_size=64:phase_shift=10:cutoff=0.97", "-ar", str(rate), "-c:a", "pcm_s24le",
               str(dest)], check=True, timeout=600)
    duration = None
    try:
        out_json = procs.run([ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(dest)],
                             timeout=60).stdout
        duration = float(json.loads(out_json)["format"]["duration"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        duration = None
    return {"file": str(dest), "rate": rate, "bits": 24, "duration": duration}


def write_prores(src: str | os.PathLike, out: str | os.PathLike, *, info: dict | None = None) -> dict:
    """ProRes 422 HQ at the clip's native rate, Rec.709 tags (the films' plate_conform.py, for After Effects)."""
    info = info or probe(src)
    dest = Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    rate = info.get("r_frame_rate") or info.get("fps") or "24/1"
    procs.run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-c:v", "prores_ks", "-profile:v", "3",
               "-pix_fmt", "yuv422p10le", "-vendor", "apl0", "-r", str(rate), "-fps_mode", "cfr",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
               "-an", str(dest)], check=True, timeout=3600)
    return {"file": str(dest), "codec": "prores_ks profile 3 (422 HQ)", "fps": str(rate)}


def extract_png(src: str | os.PathLike, out_dir: str | os.PathLike, *, size: tuple[int, int] | None = None,
                prefix: str = "b") -> list[Path]:
    """Every frame of a clip as lossless PNG (for mattes: JPEG noise would count as change)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chain = [f"scale={size[0]}:{size[1]}:flags=lanczos"] if size else []
    chain.append("format=rgb24")
    procs.run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-vf", ",".join(chain),
               "-fps_mode", "passthrough", "-start_number", "0", str(out / f"{prefix}%05d.png")], check=True,
              timeout=3600)
    return sorted(out.glob(f"{prefix}[0-9][0-9][0-9][0-9][0-9].png"))


def sample(src: str | os.PathLike, times: Iterable[float], *, width: int) -> list:
    """Still frames at the given times, scaled to `width` (for contact sheets). Returns PIL images."""
    from PIL import Image  # noqa: PLC0415
    import io  # noqa: PLC0415

    images = []
    for t in times:
        raw = procs.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
                         "-vf", f"scale={width}:-2:flags=lanczos", "-f", "image2pipe", "-c:v", "png", "-"],
                        text=False, timeout=120).stdout
        if raw:
            images.append(Image.open(io.BytesIO(raw)).convert("RGB"))
    return images


def image_fit(img, fit: str, size: tuple[int, int] | None, background: str = "#000000"):
    """Pillow version of fit_filter, for frames that are already images (web captures, retimed frames)."""
    from PIL import Image  # noqa: PLC0415

    if fit == "native" or size is None:
        return img
    width, height = size
    src_w, src_h = img.size
    if fit == "contain":
        scale = min(width / src_w, height / src_h)
        new = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
        canvas = Image.new("RGB", (width, height), background)
        canvas.paste(new, ((width - new.size[0]) // 2, (height - new.size[1]) // 2))
        return canvas
    scale = max(width / src_w, height / src_h)
    new = img.resize((max(width, round(src_w * scale)), max(height, round(src_h * scale))), Image.LANCZOS)
    left, top = (new.size[0] - width) // 2, (new.size[1] - height) // 2
    return new.crop((left, top, left + width, top + height))


def save_jpeg(img, path: Path, quality: int = JPEG_QUALITY) -> None:
    img.convert("RGB").save(path, "JPEG", quality=quality, subsampling=0, optimize=False)


def link_or_copy(src: Path, dest: Path) -> None:
    """Reuse a frame without re-encoding it: a hard link where the disk allows, else a copy."""
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def require_tools() -> None:
    if not procs.which("ffmpeg") or not procs.which("ffprobe"):
        raise MissingPrerequisite("ffmpeg and ffprobe are needed here.", hint="Run `slate doctor --install`.")
