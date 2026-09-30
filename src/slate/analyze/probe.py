"""What the reference film is: container, size, clock, exact frame count, audio, and its SHA-256 for lineage."""

from __future__ import annotations

from pathlib import Path

from slate import jsonio
from slate.errors import UsageError
from slate.qa import media


def probe(video: Path) -> dict:
    """ffprobe's facts about a film, in the shape every analysis step reads."""
    video = Path(video)
    if not video.is_file():
        raise UsageError(f"No video at {video}.", hint="Pass the path to a video file: slate analyze <video>")
    raw = media.probe(video)
    streams = raw.get("streams") or []
    fmt = raw.get("format") or {}
    if not any(s.get("codec_type") == "video" for s in streams):
        raise UsageError(f"{video.name} has no video stream, so there's no film to analyze.")
    vi = media.video_info(video, count=True)
    if not vi.get("fps_num"):
        raise UsageError(f"{video.name} doesn't declare a frame rate.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _num(fmt.get("duration")) or (vi["frames"] * vi["fps_den"] / vi["fps_num"] if vi.get("frames") else None)
    return {
        "path": str(video.resolve()),
        "name": video.name,
        "bytes": video.stat().st_size,
        "sha256": jsonio.sha256(video),
        "container": fmt.get("format_name"),
        "duration_s": round(duration, 4) if duration else None,
        "video": vi,
        "audio": None if audio is None else {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio["sample_rate"]) if str(audio.get("sample_rate", "")).isdigit() else None,
            "channels": audio.get("channels"),
            "duration_s": _num(audio.get("duration")),
        },
        "engine": media.engine("probe", "ffprobe"),
    }


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_audio(video: Path, out: Path, *, sample_rate: int = 44100) -> Path:
    """The film's soundtrack as 16-bit stereo WAV (what Demucs and librosa read)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    media._run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video), "-vn", "-c:a", "pcm_s16le",
                "-ar", str(sample_rate), "-ac", "2", str(out)])
    return out
