"""Installed-library playback using tiny neutral EXS/WAV fixtures, with no real library reads or subprocesses."""
import hashlib
import struct

import numpy as np
import pytest

from slate.errors import MissingPrerequisite
from slate.sound import audio
from slate.sound.effects import sampler, voices


def chunk(kind, name, data):
    return struct.pack("<IIII4s64s", (kind << 24) | 0x0101, len(data), 0, 0, b"TBOS", name.encode()) + data


@pytest.fixture
def sampled_library(tmp_path, monkeypatch):
    monkeypatch.setenv("SLATE_APPLE_LIBRARY", str(tmp_path))
    rate = 24000
    folder = tmp_path / "neutral-samples"
    folder.mkdir()
    t = np.arange(rate) / rate
    for index in range(2):
        y = 0.5 * np.sin(2 * np.pi * 443 * t + index * 0.7)
        audio.write_wav(folder / f"take-{index}.wav", np.stack([y, y], axis=1), rate, 24)
    parts = [chunk(0, "Neutral", bytes(16))]
    for index in range(2):
        parts.append(chunk(2, f"take-{index}", bytes([0, 0, 0, 0, 0, 0, 127, 0])))
        zone = bytearray(96)
        zone[1], zone[6], zone[7], zone[9], zone[10], zone[33] = 69, 0, 127, 0, 127, 1
        struct.pack_into("<IIII", zone, 12, 0, rate, rate//4, 3*rate//4)
        struct.pack_into("<II", zone, 88, index, index)
        parts.append(chunk(1, f"zone-{index}", zone))
    for index in range(2):
        data = bytearray(592)
        struct.pack_into("<II", data, 8, rate, 24)
        directory, name = str(folder).encode(), f"take-{index}.wav".encode()
        data[80:80+len(directory)], data[336:336+len(name)] = directory, name
        parts.append(chunk(3, name.decode(), data))
    exs = tmp_path / "Application Support/Logic/Sampler Instruments/Neutral.exs"
    exs.parent.mkdir(parents=True)
    exs.write_bytes(b"".join(parts))
    return tmp_path, {"instruments": {"felt_note": {"library": "logic-sampler", "exs": "Neutral.exs"}}}


def fingerprint(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_sampler_tunes_the_recording_and_preserves_alternate_takes_read_only(sampled_library):
    root, palette = sampled_library
    before = fingerprint(root)
    inst = sampler.Instrument("felt_note", palette)
    one = inst.play("A4", dur=0.5, take=0, sample_rate=24000)
    two = inst.play("A4", dur=0.5, take=1, sample_rate=24000)
    assert one.shape == two.shape == (12000, 2) and not np.array_equal(one, two)
    segment = one[:, 0] * np.hanning(len(one))
    peak = np.argmax(abs(np.fft.rfft(segment, 1 << 18))) * 24000 / (1 << 18)
    assert peak == pytest.approx(440, abs=0.2)
    assert fingerprint(root) == before


def test_sustained_note_uses_recorded_loop_at_requested_sample_rate(sampled_library):
    _, palette = sampled_library
    inst = sampler.Instrument("felt_note", palette)
    y = inst.play("A4", dur=1.4, sustain=True, skip="loop", sample_rate=16000)
    assert y.shape == (22400, 2) and np.isfinite(y).all()
    assert np.sqrt(np.mean(y[-1600:] ** 2)) > 0.1


def test_missing_instrument_or_missing_zone_never_synthesizes(sampled_library):
    root, palette = sampled_library
    with pytest.raises(MissingPrerequisite):
        sampler.Instrument("pad_mid", palette)
    inst = sampler.Instrument("felt_note", palette)
    inst.zones.clear()
    with pytest.raises(MissingPrerequisite, match="no sample"):
        inst.play("A4", dur=0.1)


def test_no_loop_fails_for_a_sustained_pad(sampled_library):
    _, palette = sampled_library
    inst = sampler.Instrument("felt_note", palette)
    for zone in inst.zones:
        zone["loop_on"] = False
    with pytest.raises(MissingPrerequisite, match="no steady sustain loop"):
        inst.play("A4", dur=0.5, sustain=True, skip="loop")


def test_dependencies_follow_selected_zones_layers_and_independent_gesture_notes(sampled_library, monkeypatch):
    root, palette = sampled_library
    before = fingerprint(root)
    def no_decode(*args, **kwargs):
        pytest.fail("dependency discovery must not decode samples")
    monkeypatch.setattr(audio, "load", no_decode)
    cue = {"type": "layered", "params": {"layers": [
        {"type": "snote", "pitch": "A4", "take": 1},
        {"type": "sample", "file": "FCP:Motions & Transitions/Swish 1.caf"}]}}
    paths = voices.dependencies(cue, palette=palette)
    assert root / "neutral-samples/take-1.wav" in paths
    assert root / "neutral-samples/take-0.wav" not in paths
    assert any(path.suffix == ".exs" for path in paths)
    assert any(path.name == "Swish 1.caf" for path in paths)
    gesture = {"type": "countup3", "notes": [{"type": "snote", "params": {"pitch": "A4", "take": 0}}]}
    assert root / "neutral-samples/take-0.wav" in voices.dependencies(gesture, palette=palette)
    assert fingerprint(root) == before
