"""loudness, stems_sum and the no-mix paths of the audio gates."""

import numpy as np
import pytest

from slate.qa import audio, wav
from slate.qa.checks import audio as audio_gates

from qa_core_helpers import needs_ffmpeg, tone, write_json


def _sub(result, name):
    return next(c for c in result.details["checks"] if c["check"] == name)


def _scale_to(path_builder, target: float, *, seconds: float = 4.0) -> float:
    """The amplitude of a stereo 997 Hz tone that ffmpeg meters at `target` LUFS."""
    p = path_builder("probe.wav", tone(seconds, amp=0.1))
    measured = audio.ebur128(p)["integrated_lufs"]
    p.unlink()
    return 0.1 * 10 ** ((target - measured) / 20)


@needs_ffmpeg
def test_loudness_on_target_passes_and_names_ffmpeg(film_builder):
    fb = film_builder()
    amp = _scale_to(lambda rel, x: fb.wav(rel, x), -14.0)
    fb.mix({"dialogue": tone(4.0, amp=amp)})
    r = audio_gates.loudness(fb.path)
    assert r.status == "PASS", r.summary
    assert r.engine["name"] == "ffmpeg ebur128" and r.engine["version"][0].isdigit()
    assert abs(r.details["integrated_lufs"] + 14.0) <= 0.1
    assert fb.MIX in r.inputs


@needs_ffmpeg
def test_loudness_off_target_and_over_the_ceiling(film_builder):
    fb = film_builder()
    amp = _scale_to(lambda rel, x: fb.wav(rel, x), -11.0)
    fb.mix({"dialogue": tone(4.0, amp=amp)})
    r = audio_gates.loudness(fb.path)
    assert r.status == "FAIL" and _sub(r, "integrated")["status"] == "FAIL"
    # on target, but one sample near full scale: the true peak breaks the −1 dBTP ceiling
    amp = _scale_to(lambda rel, x: fb.wav(rel, x), -14.0)
    x = tone(4.0, amp=amp)
    x[48000, :] = 0.99
    fb.mix({"dialogue": x})
    r = audio_gates.loudness(fb.path)
    assert _sub(r, "integrated")["status"] == "PASS"
    assert r.status == "FAIL" and _sub(r, "true_peak")["status"] == "FAIL"


@needs_ffmpeg
def test_loudness_follows_film_targets(film_builder):
    fb = film_builder(targets={"lufs": -16.0, "lufs_tolerance": 1.0, "true_peak": -2.0})
    amp = _scale_to(lambda rel, x: fb.wav(rel, x), -16.5)
    fb.mix({"dialogue": tone(4.0, amp=amp)})
    r = audio_gates.loudness(fb.path)
    assert r.status == "PASS" and r.details["target_lufs"] == -16.0


def test_audio_gates_unchecked_without_a_mix(film_builder):
    fb = film_builder()
    for gate in (audio_gates.loudness, audio_gates.bit_depth, audio_gates.silent_stems, audio_gates.stems_sum):
        r = gate(fb.path)
        assert r.status == "UNCHECKED" and "no final mix" in r.summary, gate.__name__
        assert fb.MIX in r.inputs  # it goes STALE when the mix appears


def test_stems_sum_exact_and_off(film_builder):
    fb = film_builder()
    fb.mix({"dialogue": tone(1.0, amp=0.3), "music": tone(1.0, amp=0.1, freq=220.0), "effects": tone(1.0, amp=0.05, freq=3000)})
    r = audio_gates.stems_sum(fb.path)
    assert r.status == "PASS", r.summary
    assert r.details["max_abs"] <= 1e-6
    # a stem changed after the mix was made
    fb.wav(fb.stem("music"), tone(1.0, amp=0.12, freq=220.0))
    r = audio_gates.stems_sum(fb.path)
    assert r.status == "FAIL" and r.details["max_abs"] > 0.01


def test_stems_sum_tail_and_shape(film_builder):
    fb = film_builder()
    fb.mix({"dialogue": tone(1.0), "music": tone(1.0, amp=0.1)})
    longer = np.concatenate([wav.read(fb.path / fb.stem("music"))[0], tone(0.5, amp=0.1)])
    fb.wav(fb.stem("music"), longer)
    r = audio_gates.stems_sum(fb.path)
    assert r.status == "FAIL" and _sub(r, "tails")["status"] == "FAIL"
    fb.wav(fb.stem("music"), tone(1.0, amp=0.1, channels=1))
    r = audio_gates.stems_sum(fb.path)
    assert r.status == "FAIL" and "1 ch" in r.summary


def test_stems_from_a_mix_record(film_builder):
    fb = film_builder()
    fb.wav("sound/out/mix_v3.wav", tone(1.0, amp=0.2))
    fb.wav("sound/out/voice.wav", tone(1.0, amp=0.2))
    write_json(fb.path / "sound" / "mix.json", {"mix": "sound/out/mix_v3.wav", "stems": {"dialogue": "sound/out/voice.wav"}})
    r = audio_gates.stems_sum(fb.path)
    assert r.status == "PASS" and "sound/out/mix_v3.wav" in r.inputs


@needs_ffmpeg
def test_silent_stems_pass_when_every_stem_sounds(film_builder):
    fb = film_builder()
    fb.mix({"dialogue": tone(3.0), "music": tone(3.0, amp=0.05, freq=220.0)})
    r = audio_gates.silent_stems(fb.path)
    assert r.status == "PASS" and "ffmpeg ebur128" in r.engine["meter"]
