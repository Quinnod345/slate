"""What a reference file is: a still or a moving picture, how long, and whether it has sound.

Approvals check these facts before recording anything, because the plan's bars are about them: the look-and-motion
reference is a moving 10–15 second test with sound (stills don't count), the sound reference a 10–20 second proof.
Probing goes through the QA module's ffprobe helpers (`slate.qa.media`) and loudness through the sound module's meter
(`slate.sound.measure`), so a reference is measured the same way films are.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from slate.errors import MissingPrerequisite, SlateError

STILL_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".heic", ".svg", ".pdf"})
AUDIO_EXTS = frozenset({".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".aac", ".caf", ".ogg", ".opus"})
STILL_CODECS = frozenset({"png", "mjpeg", "jpegls", "webp", "bmp", "tiff", "gif", "apng", "jpeg2000"})
SILENT_LUFS = -70.0


def _rate(text: str | None) -> float | None:
    try:
        r = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return float(r) if r > 0 else None


def probe(path: str | Path) -> dict:
    """{kind: video|still|audio, duration_s, frames, width, height, fps, has_audio, audio_lufs, engine}.

    Raises MissingPrerequisite when ffprobe isn't installed: an approval Slate can't check isn't recorded."""
    p = Path(path)
    if p.suffix.lower() in STILL_EXTS:
        return {"kind": "still", "duration_s": 0.0, "frames": 1, "has_audio": False, "audio_lufs": None,
                "engine": "by extension"}
    try:
        from slate.qa import media as qa_media  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - qa ships with Slate
        raise MissingPrerequisite("Slate's QA media helpers aren't installed, so references can't be checked.") from exc
    info = qa_media.probe(p)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and
                  not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = info.get("format") or {}
    duration = None
    for src in (fmt, video or {}, audio[0] if audio else {}):
        try:
            duration = float(src.get("duration"))
            break
        except (TypeError, ValueError):
            continue
    out: dict = {"kind": "audio" if video is None else "video", "duration_s": duration, "has_audio": bool(audio),
                 "audio_lufs": None, "engine": qa_media.engine("probe", "ffprobe")}
    if video is not None:
        fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
        frames = None
        if str(video.get("nb_frames", "")).isdigit():
            frames = int(video["nb_frames"])
        elif duration and fps:
            frames = int(round(duration * fps))
        out.update(width=int(video.get("width") or 0), height=int(video.get("height") or 0), fps=fps, frames=frames,
                   codec=video.get("codec_name"))
        if video.get("codec_name") in STILL_CODECS and (frames or 1) <= 1:
            out["kind"] = "still"
        elif frames is not None and frames <= 1:
            out["kind"] = "still"
    return out


def loudness(path: str | Path) -> float | None:
    """Integrated loudness (LUFS) of a file's sound, through the sound module's meter; None if it can't be read."""
    try:
        from slate.sound import measure  # noqa: PLC0415

        return float(measure.loudness(Path(path), true_peak=False, lra=False)["integrated_lufs"])
    except (ImportError, SlateError, ValueError, OSError):
        return None


def audible(path: str | Path) -> tuple[bool, float | None]:
    """(has sound above silence, integrated LUFS)."""
    lufs = loudness(path)
    return (lufs is not None and lufs > SILENT_LUFS), lufs
