"""Audio in and out for the voice stages, in numpy and scipy only.

Why a WAV reader of our own: the standard library's `wave` can't read 32-bit float WAVs (what Rubber Band and some
tools write), and decoding every take through ffmpeg would make the tests depend on it. WAV is read and written
here; anything else (the mp3 that ElevenLabs' lower tiers return) is decoded through ffmpeg via `slate.procs`.

Loudness is ITU-R BS.1770-4 (K-weighted, gated), so level-matched listening reels and A/B clips don't need ffmpeg
either. The sound module's gates use ffmpeg's ebur128; both follow the same standard.
"""

from __future__ import annotations

import math
import struct
from math import gcd
from pathlib import Path

import numpy as np

from slate import jsonio, procs
from slate.errors import MissingPrerequisite, UsageError

PCM_INT = 1
PCM_FLOAT = 3
EXTENSIBLE = 0xFFFE


# ---------------------------------------------------------------------------------------------------------------
# WAV


def parse_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV bytes to float64 samples in [-1, 1): shape (n,) for mono, (n, channels) otherwise."""
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise UsageError("Not a WAV file (no RIFF/WAVE header).")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        start = pos + 8
        if cid == b"fmt ":
            tag, channels, rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", data[start:start + 16])
            if tag == EXTENSIBLE and size >= 26:
                tag = struct.unpack("<H", data[start + 24:start + 26])[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            end = len(data) if size == 0xFFFFFFFF or start + size > len(data) else start + size
            body = data[start:end]
        if body is not None and fmt is not None:
            break
        pos = start + size + (size & 1)
    if fmt is None or body is None:
        raise UsageError("WAV file has no fmt or data chunk.")
    tag, channels, rate, bits = fmt
    width = bits // 8
    usable = len(body) - len(body) % (width * channels)
    raw = body[:usable]
    if tag == PCM_INT and bits == 16:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0
    elif tag == PCM_INT and bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float64) / float(1 << 23)
    elif tag == PCM_INT and bits == 32:
        x = np.frombuffer(raw, "<i4").astype(np.float64) / float(1 << 31)
    elif tag == PCM_INT and bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128.0) / 128.0
    elif tag == PCM_FLOAT and bits == 32:
        x = np.frombuffer(raw, "<f4").astype(np.float64)
    elif tag == PCM_FLOAT and bits == 64:
        x = np.frombuffer(raw, "<f8").astype(np.float64)
    else:
        raise UsageError(f"Unsupported WAV encoding (format {tag}, {bits}-bit).")
    if channels > 1:
        x = x.reshape(-1, channels)
    return x, int(rate)


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    return parse_wav(Path(path).read_bytes())


def wav_bytes(x: np.ndarray, sr: int, bits: int = 24) -> bytes:
    """Encode float samples (mono (n,) or (n, ch)) as PCM WAV bytes, 16- or 24-bit."""
    x = np.asarray(x, dtype=np.float64)
    channels = 1 if x.ndim == 1 else x.shape[1]
    flat = np.clip(x.reshape(-1), -1.0, 1.0)
    if bits == 16:
        payload = np.round(flat * 32767.0).astype("<i2").tobytes()
    elif bits == 24:
        v = np.round(flat * 8388607.0).astype(np.int32)
        v = np.where(v < 0, v + (1 << 24), v).astype(np.uint32)
        b = np.empty((len(v), 3), np.uint8)
        b[:, 0], b[:, 1], b[:, 2] = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
        payload = b.tobytes()
    else:
        raise UsageError(f"Slate writes 16- or 24-bit WAV, not {bits}-bit.")
    width = bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, PCM_INT, channels, sr, sr * channels * width, channels * width, bits)
    header += b"data" + struct.pack("<I", len(payload))
    return header + payload


def write_wav(path: str | Path, x: np.ndarray, sr: int, bits: int = 24) -> Path:
    """Write a WAV atomically (a half-written take never looks finished)."""
    return jsonio.write_text_atomic(path, wav_bytes(x, sr, bits))


def pcm16_to_wav(raw: bytes, sr: int, channels: int = 1) -> bytes:
    """Wrap raw little-endian 16-bit PCM (ElevenLabs' pcm_* formats) in a WAV container, bit for bit."""
    raw = raw[: len(raw) - len(raw) % (2 * channels)]
    header = b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, PCM_INT, channels, sr, sr * channels * 2, channels * 2, 16)
    header += b"data" + struct.pack("<I", len(raw))
    return header + raw


# ---------------------------------------------------------------------------------------------------------------
# Loading any audio


def to_mono(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 1 else x.mean(axis=1)


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return x
    from scipy.signal import resample_poly  # noqa: PLC0415 (keeps `slate --help` fast)

    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(x, int(sr_to) // g, int(sr_from) // g, axis=0)


def _ffmpeg_decode(path: Path, sr: int) -> np.ndarray:
    ffmpeg = procs.which("ffmpeg")
    if not ffmpeg:
        raise MissingPrerequisite(f"Decoding {path.name} needs ffmpeg, which isn't installed.",
                                  hint="Run `slate doctor --install` (it asks before installing anything).")
    proc = procs.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                     text=False, check=True, timeout=600)
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float64)


def load(path: str | Path, sr: int | None = None, mono: bool = True) -> tuple[np.ndarray, int]:
    """Samples and rate. WAV is read natively; other formats go through ffmpeg (always decoded to mono)."""
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"Missing audio file: {p}")
    if p.suffix.lower() in (".wav", ".wave"):
        x, rate = read_wav(p)
        if mono:
            x = to_mono(x)
        if sr and sr != rate:
            x, rate = resample(x, rate, sr), sr
        return x, rate
    rate = sr or 48000
    return _ffmpeg_decode(p, rate), rate


def duration(path: str | Path) -> float:
    """Length in seconds (WAV from its header; anything else through ffprobe)."""
    p = Path(path)
    if p.suffix.lower() in (".wav", ".wave"):
        x, rate = read_wav(p)
        return len(x) / float(rate)
    ffprobe = procs.which("ffprobe")
    if not ffprobe:
        x, rate = load(p)
        return len(x) / float(rate)
    out = procs.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                    check=True, timeout=60).stdout.strip()
    return float(out)


# ---------------------------------------------------------------------------------------------------------------
# Levels


def db(value: float, floor: float = -150.0) -> float:
    return 20.0 * math.log10(value) if value > 0 else floor


def rms_dbfs(x: np.ndarray) -> float:
    x = to_mono(np.asarray(x, dtype=np.float64))
    return db(float(np.sqrt(np.mean(x ** 2)))) if len(x) else -150.0


def _k_weighting(sr: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """BS.1770 K-weighting as two biquads, designed for any rate (the libebur128 formulation)."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf_b = np.array([(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0])
    shelf_a = np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0])
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    den = 1.0 + k / q + k * k
    hp_a = np.array([1.0, 2.0 * (k * k - 1.0) / den, (1.0 - k / q + k * k) / den])
    hp_b = np.array([1.0, -2.0, 1.0])
    return [(shelf_b, shelf_a), (hp_b, hp_a)]


def loudness(x: np.ndarray, sr: int) -> dict:
    """Integrated loudness (LUFS, gated) and true peak (dBTP, 4x oversampled) of a signal."""
    from scipy.signal import lfilter, resample_poly  # noqa: PLC0415

    x = np.asarray(x, dtype=np.float64)
    chans = x.reshape(-1, 1) if x.ndim == 1 else x
    weighted = chans.copy()
    for b, a in _k_weighting(sr):
        weighted = lfilter(b, a, weighted, axis=0)
    block, step = int(round(0.4 * sr)), int(round(0.1 * sr))
    lufs = None
    if len(weighted) >= block:
        starts = np.arange(0, len(weighted) - block + 1, step)
        power = np.array([np.mean(weighted[s:s + block] ** 2, axis=0).sum() for s in starts])
        with np.errstate(divide="ignore"):
            block_l = -0.691 + 10 * np.log10(np.maximum(power, 1e-20))
        gated = power[block_l > -70.0]
        if len(gated):
            rel = -0.691 + 10 * math.log10(float(np.mean(gated))) - 10.0
            final = power[(block_l > -70.0) & (block_l > rel)]
            if len(final):
                lufs = round(-0.691 + 10 * math.log10(float(np.mean(final))), 2)
    over = resample_poly(chans, 4, 1, axis=0) if len(chans) else chans
    peak = float(np.max(np.abs(over))) if len(over) else 0.0
    return {"lufs": lufs, "true_peak_dbtp": round(db(peak), 2)}


def gain_to(x: np.ndarray, sr: int, target_lufs: float) -> tuple[np.ndarray, float]:
    """Scale a signal to a target integrated loudness; returns (signal, gain in dB). Silence is left alone."""
    meas = loudness(x, sr)["lufs"]
    if meas is None:
        return x, 0.0
    gain = target_lufs - meas
    return x * (10 ** (gain / 20.0)), round(gain, 2)


def silence(seconds: float, sr: int) -> np.ndarray:
    return np.zeros(int(round(seconds * sr)))
