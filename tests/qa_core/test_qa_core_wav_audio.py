"""The exact WAV reader and the loudness meter wrapper the audio gates stand on."""

import math

import numpy as np
import pytest

from slate.qa import audio, wav

from qa_core_helpers import needs_ffmpeg, tone


@pytest.mark.parametrize("bits,float_format", [(16, False), (24, False), (32, False), (32, True), (64, True), (8, False)])
def test_wav_round_trip_and_header(tmp_path, bits, float_format):
    x = np.linspace(-0.9, 0.9, 997)[:, None] * np.array([[1.0, -0.5]])
    p = wav.write(tmp_path / "x.wav", x, 48000, bits, float_format=float_format)
    wi = wav.info(p)
    assert wi.bits_per_sample == bits and wi.channels == 2 and wi.sample_rate == 48000 and wi.frames == 997
    assert wi.format == ("float" if float_format else "pcm")
    y, _ = wav.read(p)
    step = 1.0 / 2 ** (bits - 1) if not float_format else 1e-6
    assert np.max(np.abs(y - x)) <= step + 1e-12


def test_wav_24_bit_sign_and_blocks(tmp_path):
    x = np.array([[-1.0], [-0.5], [0.0], [0.25], [0.9999998]])
    p = wav.write(tmp_path / "s.wav", x, 44100, 24)
    y, _ = wav.read(p)
    assert y[:, 0].tolist() == pytest.approx([-1.0, -0.5, 0.0, 0.25, 8388606 / 8388608], abs=1e-9)
    blocks = list(wav.blocks(p, block=2))
    assert [len(b) for b in blocks] == [2, 2, 1]
    part, _ = wav.read(p, start=3, frames=10)
    assert len(part) == 2


def test_wav_rejects_what_it_cannot_read(tmp_path):
    (tmp_path / "no.wav").write_bytes(b"not a wav at all")
    with pytest.raises(wav.WavError):
        wav.info(tmp_path / "no.wav")


def test_wav_extensible_header(tmp_path):
    import struct

    x = (np.arange(10, dtype=np.int32) * 1000).astype("<i2").tobytes()
    guid = struct.pack("<H", 1) + bytes.fromhex("000000001000800000aa00389b71")
    fmt = struct.pack("<HHIIHH", 0xFFFE, 1, 48000, 96000, 2, 16) + struct.pack("<HHI", 22, 16, 4) + guid
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(x)) + x
    p = tmp_path / "ext.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    wi = wav.info(p)
    assert wi.format == "pcm" and wi.bits_per_sample == 16 and wi.frames == 10


def test_levels_digital_silence_and_tone(tmp_path):
    silent = wav.write(tmp_path / "silent.wav", np.zeros((4800, 2)), 48000, 24)
    lv = audio.levels(silent)
    assert lv["digital_silence"] and lv["peak_dbfs"] == -math.inf
    loud = wav.write(tmp_path / "loud.wav", tone(0.1, amp=0.5), 48000, 24)
    lv = audio.levels(loud)
    assert lv["peak_dbfs"] == pytest.approx(20 * math.log10(0.5), abs=0.01)
    assert not lv["digital_silence"]


def test_parse_ebur128_floor_and_minus_inf():
    text = """
  Integrated loudness:
    I:         -70.0 LUFS
    Threshold:   0.0 LUFS

  Loudness range:
    LRA:         0.0 LU

  True peak:
    Peak:       -inf dBFS
"""
    m = audio.parse_ebur128(text)
    assert m["integrated_lufs"] == -70.0 and m["true_peak_dbtp"] == -math.inf and m["lra_lu"] == 0.0
    assert audio.rounded(m["true_peak_dbtp"]) == "-inf"


@needs_ffmpeg
def test_ebur128_reads_silence_at_the_floor(tmp_path):
    silent = wav.write(tmp_path / "silent.wav", np.zeros((48000 * 2, 2)), 48000, 24)
    m = audio.ebur128(silent)
    # ffmpeg reads digital silence as exactly the −70 LUFS floor: "< −70" can never be true for it
    assert m["integrated_lufs"] == -70.0
    assert not (m["integrated_lufs"] < -70)
    assert m["true_peak_dbtp"] == -math.inf


@needs_ffmpeg
def test_ebur128_on_a_known_tone(tmp_path):
    # a 997 Hz sine at amplitude a in both channels reads about 20·log10(a) LUFS (0 dBFS sine in L+R ≈ 0 LUFS)
    p = wav.write(tmp_path / "t.wav", tone(5.0, amp=0.2), 48000, 24)
    m = audio.ebur128(p)
    assert m["integrated_lufs"] == pytest.approx(20 * math.log10(0.2), abs=0.3)
    assert m["true_peak_dbtp"] == pytest.approx(20 * math.log10(0.2), abs=0.3)
