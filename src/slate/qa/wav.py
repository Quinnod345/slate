"""Read WAV files exactly, without ffmpeg: the format as written, and the samples as numbers.

The audio gates need two things a decoder can hide. First, the format itself: a 16-bit mix that measures −14 LUFS is
still a 16-bit mix, and the films' validator reported the bit depth without ever gating it. Reading the `fmt ` chunk
directly means the bit depth is what the file says, not what a decoder converted it to. Second, the samples, exactly,
so stems can be summed against the mix to 1e-6 and digital silence can be told from very quiet sound.

Supports RIFF and RF64 files with PCM (8/16/24/32-bit integer), IEEE float (32/64-bit) and WAVE_FORMAT_EXTENSIBLE
wrapping either. Reads in blocks, so a six-minute stereo mix never has to sit in memory as float64 all at once.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

PCM = 1
IEEE_FLOAT = 3
EXTENSIBLE = 0xFFFE
# The first two bytes of an EXTENSIBLE SubFormat GUID are the plain format tag (KSDATAFORMAT_SUBTYPE_PCM / _IEEE_FLOAT).
_GUID_TAIL = bytes.fromhex("000000001000800000aa00389b71")


class WavError(ValueError):
    """The file isn't a WAV this reader understands (the message says what was found instead)."""


@dataclass(frozen=True)
class WavInfo:
    path: str
    format: str  # "pcm" or "float"
    channels: int
    sample_rate: int
    bits_per_sample: int  # the container's sample width in bits (24 for a 24-bit file)
    valid_bits: int  # EXTENSIBLE's wValidBitsPerSample; equal to bits_per_sample otherwise
    frames: int  # samples per channel
    data_offset: int
    block_align: int

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def describe(self) -> str:
        kind = "float" if self.format == "float" else "PCM"
        bits = self.valid_bits if self.valid_bits and self.valid_bits != self.bits_per_sample else self.bits_per_sample
        return f"{bits}-bit {kind}, {self.sample_rate} Hz, {self.channels} ch, {self.seconds:.3f} s"

    def to_dict(self) -> dict:
        return {"format": self.format, "channels": self.channels, "sample_rate": self.sample_rate,
                "bits_per_sample": self.bits_per_sample, "valid_bits": self.valid_bits, "frames": self.frames,
                "seconds": round(self.seconds, 6)}


def info(path: str | Path) -> WavInfo:
    """Parse the header of a WAV file. Raises WavError for anything that isn't a readable WAV."""
    p = Path(path)
    with p.open("rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[8:12] != b"WAVE" or head[:4] not in (b"RIFF", b"RF64"):
            raise WavError(f"{p.name} isn't a WAV file (no RIFF/RF64 WAVE header).")
        rf64 = head[:4] == b"RF64"
        data_size64 = None
        fmt = None
        while True:
            chunk = fh.read(8)
            if len(chunk) < 8:
                break
            cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            start = fh.tell()
            if cid == b"ds64" and rf64:
                body = fh.read(size)
                data_size64 = struct.unpack("<Q", body[8:16])[0]
            elif cid == b"fmt ":
                fmt = fh.read(size)
            elif cid == b"data":
                if fmt is None:
                    raise WavError(f"{p.name}: the data chunk comes before the fmt chunk.")
                data_size = data_size64 if (rf64 and size == 0xFFFFFFFF and data_size64 is not None) else size
                return _build(p, fmt, start, data_size)
            fh.seek(start + size + (size & 1))  # chunks are word-aligned
        raise WavError(f"{p.name} has no data chunk.")


def _build(p: Path, fmt: bytes, data_offset: int, data_size: int) -> WavInfo:
    if len(fmt) < 16:
        raise WavError(f"{p.name}: its fmt chunk is too short ({len(fmt)} bytes).")
    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack("<HHIIHH", fmt[:16])
    valid = bits
    if tag == EXTENSIBLE:
        if len(fmt) < 40:
            raise WavError(f"{p.name}: WAVE_FORMAT_EXTENSIBLE without its extension.")
        valid = struct.unpack("<H", fmt[18:20])[0] or bits
        guid = fmt[24:40]
        if guid[2:] != _GUID_TAIL:
            raise WavError(f"{p.name}: unsupported EXTENSIBLE sub-format {guid.hex()}.")
        tag = struct.unpack("<H", guid[:2])[0]
    if tag == PCM:
        kind = "pcm"
        if bits not in (8, 16, 24, 32):
            raise WavError(f"{p.name}: {bits}-bit PCM isn't supported.")
    elif tag == IEEE_FLOAT:
        kind = "float"
        if bits not in (32, 64):
            raise WavError(f"{p.name}: {bits}-bit float isn't supported.")
    else:
        raise WavError(f"{p.name}: format tag {tag:#06x} isn't PCM or float.")
    if channels < 1 or block_align != channels * (bits // 8):
        raise WavError(f"{p.name}: inconsistent header ({channels} ch, block align {block_align}, {bits} bits).")
    frames = data_size // block_align
    return WavInfo(str(p), kind, channels, rate, bits, valid, frames, data_offset, block_align)


def _decode(raw: bytes, wi: WavInfo) -> np.ndarray:
    """Bytes of whole sample frames → float64 array (frames, channels) in −1…1."""
    if wi.format == "float":
        dtype = "<f4" if wi.bits_per_sample == 32 else "<f8"
        x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    elif wi.bits_per_sample == 8:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif wi.bits_per_sample == 16:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif wi.bits_per_sample == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float64) / 8388608.0
    else:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    return x.reshape(-1, wi.channels)


def read(path: str | Path, start: int = 0, frames: int | None = None) -> tuple[np.ndarray, WavInfo]:
    """Samples [start, start + frames) as float64 (frames, channels). Past the end, the array is simply shorter."""
    wi = info(path)
    start = max(0, int(start))
    n = wi.frames - start if frames is None else max(0, min(int(frames), wi.frames - start))
    if n <= 0:
        return np.zeros((0, wi.channels)), wi
    with open(path, "rb") as fh:
        fh.seek(wi.data_offset + start * wi.block_align)
        raw = fh.read(n * wi.block_align)
    usable = len(raw) - len(raw) % wi.block_align
    return _decode(raw[:usable], wi), wi


def blocks(path: str | Path, block: int = 1 << 18) -> Iterator[np.ndarray]:
    """Yield the whole file in blocks of `block` sample frames, as float64 (frames, channels)."""
    wi = info(path)
    with open(path, "rb") as fh:
        fh.seek(wi.data_offset)
        left = wi.frames
        while left > 0:
            n = min(block, left)
            raw = fh.read(n * wi.block_align)
            usable = len(raw) - len(raw) % wi.block_align
            if usable <= 0:
                break
            yield _decode(raw[:usable], wi)
            left -= usable // wi.block_align


def write(path: str | Path, samples: np.ndarray, sample_rate: int, bits: int = 24, *, float_format: bool = False) -> Path:
    """Write a PCM (or float) WAV. Used by tests and fixtures; values are clipped to −1…1 for integer formats."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    channels = x.shape[1]
    if float_format:
        if bits not in (32, 64):
            raise ValueError("float WAVs are 32- or 64-bit")
        payload = x.astype("<f4" if bits == 32 else "<f8").tobytes()
        tag = IEEE_FLOAT
    else:
        full = float(2 ** (bits - 1))
        q = np.clip(np.round(x * full), -full, full - 1).astype(np.int64)
        if bits == 8:
            payload = (q + 128).astype(np.uint8).tobytes()
        elif bits == 16:
            payload = q.astype("<i2").tobytes()
        elif bits == 24:
            u = (q & 0xFFFFFF).astype(np.uint32)
            payload = np.stack([(u & 0xFF), (u >> 8) & 0xFF, (u >> 16) & 0xFF], axis=-1).astype(np.uint8).tobytes()
        elif bits == 32:
            payload = q.astype("<i4").tobytes()
        else:
            raise ValueError(f"unsupported bit depth {bits}")
        tag = PCM
    block_align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, int(sample_rate), int(sample_rate) * block_align, block_align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        body += b"\0"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return p
