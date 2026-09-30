"""Audio in and out, the plain way.

WAV files are read and written here natively (PCM 8/16/24/32-bit and float 32/64, including WAVE_FORMAT_EXTENSIBLE),
so the mix, the meters and the checks never need ffmpeg for the files Slate makes itself. Anything else (a take as
MP3, an Eleven Music score as MP3, a file at another sample rate) is decoded by ffmpeg through `slate.procs.run`.

Conventions every sound module follows:

- signals are float64 NumPy arrays shaped (samples, channels); film sound is stereo;
- 24-bit files are written as round(x * 8388607) and read back as v / 8388608, exactly as the films' delivery tool
  did (its ffmpeg packing), so a rerun of the plumbing reproduces the delivered numbers;
- every file is written atomically (temp file, then rename), so a killed run never leaves half a WAV behind.
"""

from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from slate.errors import MissingPrerequisite, UsageError

DEFAULT_SR = 48000  # film delivery rate (48 kHz / 24-bit); a film can override it in film.json targets.sample_rate
_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE


@dataclass(frozen=True)
class WavInfo:
    path: Path
    sample_rate: int
    channels: int
    bits: int
    kind: str  # "pcm" or "float"
    frames: int
    data_offset: int
    block_align: int

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate


def is_wav(path: str | os.PathLike) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return len(head) == 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def wav_info(path: str | os.PathLike) -> WavInfo:
    """Parse a WAV header (RIFF chunks); raises UsageError for anything that isn't a readable WAV."""
    p = Path(path)
    with p.open("rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise UsageError(f"{p} isn't a WAV file.")
        fmt = None
        while True:
            chunk = fh.read(8)
            if len(chunk) < 8:
                break
            cid, size = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
            if cid == b"fmt ":
                body = fh.read(size)
                tag, ch, sr, _rate, align, bits = struct.unpack("<HHIIHH", body[:16])
                if tag == _EXTENSIBLE and len(body) >= 26:
                    tag = struct.unpack("<H", body[24:26])[0]
                fmt = (tag, ch, sr, align, bits)
                if size % 2:
                    fh.read(1)
            elif cid == b"data":
                if fmt is None:
                    raise UsageError(f"{p}: the data chunk comes before the format chunk.")
                tag, ch, sr, align, bits = fmt
                if tag not in (_PCM, _FLOAT):
                    raise UsageError(f"{p}: WAV format {tag} isn't PCM or float; convert it first.")
                offset = fh.tell()
                total = p.stat().st_size - offset
                size = min(size, total) if size else total
                return WavInfo(p, sr, ch, bits, "pcm" if tag == _PCM else "float", size // align, offset, align)
            else:
                fh.seek(size + (size % 2), os.SEEK_CUR)
    raise UsageError(f"{p}: no audio data chunk found.")


def _decode(raw: bytes, info: WavInfo) -> np.ndarray:
    ch = info.channels
    if info.kind == "float":
        dtype = "<f4" if info.bits == 32 else "<f8"
        x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    elif info.bits == 16:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif info.bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = (v << 8) >> 8  # sign-extend the 24-bit value
        x = v.astype(np.float64) / 8388608.0
    elif info.bits == 32:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    elif info.bits == 8:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    else:
        raise UsageError(f"{info.path}: {info.bits}-bit PCM isn't supported.")
    return x.reshape(-1, ch)


def read_wav(path: str | os.PathLike, start: int = 0, frames: int | None = None) -> tuple[np.ndarray, int]:
    """(samples as float64 (n, channels), sample rate). `start` and `frames` select a range without reading the rest."""
    info = wav_info(path)
    start = max(0, min(int(start), info.frames))
    count = info.frames - start if frames is None else max(0, min(int(frames), info.frames - start))
    with open(info.path, "rb") as fh:
        fh.seek(info.data_offset + start * info.block_align)
        raw = fh.read(count * info.block_align)
    return _decode(raw[: (len(raw) // info.block_align) * info.block_align], info), info.sample_rate


def iter_wav(path: str | os.PathLike, block_frames: int) -> Iterator[np.ndarray]:
    """Stream a WAV in blocks (float64 (n, channels)), so long files are measured without loading them whole."""
    info = wav_info(path)
    with open(info.path, "rb") as fh:
        fh.seek(info.data_offset)
        left = info.frames
        while left > 0:
            take = min(block_frames, left)
            raw = fh.read(take * info.block_align)
            if not raw:
                break
            yield _decode(raw[: (len(raw) // info.block_align) * info.block_align], info)
            left -= take


def _wav_bytes(x: np.ndarray, sr: int, bits: int) -> bytes:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    ch = x.shape[1]
    if bits == 24:
        q = np.ascontiguousarray(np.clip(np.round(x * 8388607.0), -8388608, 8388607).astype("<i4"))
        data = q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        tag = _PCM
    elif bits == 16:
        data = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes()
        tag = _PCM
    elif bits == 32:  # 32-bit float (scratch files, analysis stems)
        data = x.astype("<f4").tobytes()
        tag = _FLOAT
    else:
        raise UsageError(f"Slate writes 16- or 24-bit PCM or 32-bit float WAV, not {bits}-bit.")
    align = ch * bits // 8
    fmt = struct.pack("<HHIIHH", tag, ch, sr, sr * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    if len(data) % 2:
        body += b"\x00"
    return b"RIFF" + struct.pack("<I", len(body)) + body


def write_wav(path: str | os.PathLike, x: np.ndarray, sr: int = DEFAULT_SR, bits: int = 24) -> Path:
    """Write a WAV atomically. 24-bit PCM is the delivery format; bits=32 writes 32-bit float."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(_wav_bytes(x, sr, bits))
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def to_stereo(x: np.ndarray) -> np.ndarray:
    """Mono is duplicated to both sides (ffmpeg's -ac 2 does the same); more than two channels keep the first two."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == 1:
        return np.repeat(x, 2, axis=1)
    return x[:, :2]


def mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x)
    return x.mean(axis=1) if x.ndim == 2 else x


def fit(x: np.ndarray, n: int) -> np.ndarray:
    """Pad with silence or cut to exactly n samples."""
    out = np.zeros((n, x.shape[1]) if x.ndim == 2 else n)
    m = min(n, len(x))
    out[:m] = x[:m]
    return out


def _ffmpeg_decode(path: Path, sr: int, channels: int) -> np.ndarray:
    from slate import procs  # noqa: PLC0415

    if not procs.which("ffmpeg"):
        raise MissingPrerequisite(f"{path.name} isn't a WAV at {sr} Hz, and ffmpeg (needed to decode it) isn't installed.",
                                  hint="Run `slate doctor --install` (it installs ffmpeg with your yes), or supply a WAV.")
    proc = procs.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "f32le", "-ac", str(channels),
                      "-ar", str(sr), "-"], text=False, timeout=900)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise UsageError(f"ffmpeg couldn't decode {path}: {tail}")
    return np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, channels).astype(np.float64)


def load(path: str | os.PathLike, sr: int = DEFAULT_SR, n: int | None = None, channels: int = 2) -> np.ndarray:
    """Any audio file as float64 (samples, channels) at `sr`, padded or cut to n samples when n is given.

    WAVs at the right rate are read natively; everything else goes through ffmpeg (decode, resample, channel map).
    """
    p = Path(path)
    if not p.exists():
        raise UsageError(f"Missing audio file: {p}")
    if is_wav(p):
        info = wav_info(p)
        if info.sample_rate == sr and info.channels <= 2:
            x, _ = read_wav(p)
            x = to_stereo(x) if channels == 2 else mono(x)[:, None]
            return fit(x, n) if n is not None else x
    x = _ffmpeg_decode(p, sr, channels)
    return fit(x, n) if n is not None else x


def duration_s(path: str | os.PathLike) -> float:
    """A file's length in seconds (WAV natively; other formats through ffprobe)."""
    p = Path(path)
    if is_wav(p):
        return wav_info(p).seconds
    from slate import procs  # noqa: PLC0415

    if not procs.which("ffprobe"):
        raise MissingPrerequisite("ffprobe isn't installed, so a non-WAV file's length can't be read.",
                                  hint="Run `slate doctor --install`.")
    out = procs.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], timeout=60)
    try:
        return float(out.stdout.strip())
    except ValueError as exc:
        raise UsageError(f"ffprobe couldn't read the length of {p}.") from exc
