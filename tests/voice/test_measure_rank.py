"""Measurement proxies, ranking, breath, loudness and WAV I/O on known signals."""

import math
import struct

import numpy as np
import pytest

from slate.voice import audio, breath, lang, measure, rank

SR = 48000


def harmonic(f0, seconds=1.0, sr=SR, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    return amp * (np.sin(2 * np.pi * f0 * t) + 0.4 * np.sin(4 * np.pi * f0 * t) + 0.2 * np.sin(6 * np.pi * f0 * t))


def test_yin_finds_the_pitch():
    x = harmonic(200.0, sr=16000)
    f0 = measure.yin_f0(x, 16000)
    v = f0[~np.isnan(f0)]
    assert len(v) > 50 and abs(np.median(v) - 200.0) < 2.0
    noise = np.random.default_rng(0).normal(0, 0.1, 16000)
    assert np.mean(~np.isnan(measure.yin_f0(noise, 16000))) < 0.2


def test_loudness_on_known_sines():
    t = np.arange(3 * SR) / SR
    full = np.sin(2 * np.pi * 997 * t)
    assert audio.loudness(full, SR)["lufs"] == pytest.approx(-3.01, abs=0.1)
    assert audio.loudness(full * 0.1, SR)["lufs"] == pytest.approx(-23.01, abs=0.1)
    assert audio.loudness(full * 0.5, SR)["true_peak_dbtp"] == pytest.approx(-6.02, abs=0.1)
    y, gain = audio.gain_to(full * 0.1, SR, -20.0)
    assert gain == pytest.approx(3.01, abs=0.1) and audio.loudness(y, SR)["lufs"] == pytest.approx(-20.0, abs=0.05)
    assert audio.loudness(np.zeros(SR), SR)["lufs"] is None


def test_wav_round_trips(tmp_path):
    x = np.sin(np.linspace(0, 40, 4800)) * 0.5
    for bits, tol in ((16, 1 / 32767), (24, 1 / 8388607)):
        audio.write_wav(tmp_path / f"{bits}.wav", x, SR, bits)
        y, sr = audio.read_wav(tmp_path / f"{bits}.wav")
        assert sr == SR and np.max(np.abs(y - x)) <= tol
    stereo = np.stack([x, -x], axis=1)
    audio.write_wav(tmp_path / "st.wav", stereo, SR)
    y, _ = audio.read_wav(tmp_path / "st.wav")
    assert y.shape == stereo.shape
    raw = (x * 32767).astype("<i2").tobytes()
    wav = audio.pcm16_to_wav(raw, 44100)
    y, sr = audio.parse_wav(wav)
    assert sr == 44100 and np.array_equal(np.round(y * 32768).astype(int), np.frombuffer(raw, "<i2").astype(int))
    fmt = struct.pack("<IHHIIHH", 16, 3, 1, SR, SR * 4, 4, 32)
    data = x.astype("<f4").tobytes()
    floatwav = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(data)) + data
    y, _ = audio.parse_wav(floatwav)
    assert np.allclose(y, x, atol=1e-6)
    with pytest.raises(Exception):
        audio.parse_wav(b"not a wav at all")


def test_words_normalize_across_scripts_and_numbers():
    assert lang.norm_words("Don’t stop—it's 2,500 now.") == ["don't", "stop", "it's", "2500", "now"]
    assert lang.norm_words("Café's déjà-vu") == ["café's", "déjà", "vu"]
    assert lang.norm_words("3.5 percent") == ["3.5", "percent"]
    assert lang.norm_token("Hello,") == "hello"


def test_word_check_names_what_differs():
    heard = [{"word": w, "start": 0, "end": 0} for w in "Slate hears each word then fits it".split()]
    chk = measure.word_check("Slate hears each word, then fits it to the cut.", heard, lexicon=["Slate", "the cut"])
    assert chk["wer"] > 0 and "del:to->None" in chk["word_errors"]
    assert chk["lexicon"] == {"Slate": True, "the cut": False}
    ok = measure.word_check("Say it twice.", "say it twice")
    assert ok["wer"] == 0 and ok["repeats"] == []
    rep = measure.word_check("Then let's talk.", "then let's talk then let's talk")
    assert "then let's talk" in rep["repeats"]


def test_measure_take_on_speech_like_audio(tmp_path):
    from slate.vendors.elevenlabs import fake_speech

    text = "Every film starts with one line. Then it lands."
    x, words = fake_speech(text, "voice-one")
    audio.write_wav(tmp_path / "take.wav", x, SR)
    m = measure.measure_take(tmp_path / "take.wav", script=text, words=words, lexicon=["film"])
    assert m["wer"] == 0 and m["lexicon"] == {"film": True}
    assert m["words"] == 9 and 50 < m["wpm_span"] < 400
    assert m["f0_median_hz"] and 100 < m["f0_median_hz"] < 300
    assert m["sentence_pauses"] and m["sentence_pauses"][0] > 0.2
    assert m["lufs"] is not None and m["pause_floor_dbfs"] is not None
    bare = measure.measure_take(tmp_path / "take.wav", script=text)
    assert "wer" not in bare  # without words the transcript measures are left out, never guessed


def test_ranking_prefers_the_read_that_hits_its_targets():
    targets = {"wpm": [150, 170], "sentence_pause_s": [0.35, 0.7], "f0_range_st": [9.5, 13]}
    good = {"wer": 0.0, "lexicon": {"Slate": True}, "wpm_span": 160, "statements_measured": 3,
            "statement_end_mean_st": -2.0, "statement_rises_gt_1p5st": 0, "sentence_pause_mean_s": 0.5,
            "sentence_pause_min_s": 0.3, "f0_range_st_p5_p95": 11, "pause_floor_dbfs": -80,
            "final_word_voicing_rel": 0.8}
    bad = {**good, "wer": 0.2, "lexicon": {"Slate": False}, "wpm_span": 210, "statement_end_mean_st": 1.0,
           "statement_rises_gt_1p5st": 2, "sentence_pause_mean_s": 0.1, "sentence_pause_min_s": 0.05,
           "f0_range_st_p5_p95": 5, "pause_floor_dbfs": -50}
    out = rank.rank([{"id": "bad", "metrics": bad}, {"id": "good", "metrics": good}], targets)
    assert [r["id"] for r in out["ranked"]] == ["good", "bad"]
    assert out["ranked"][0]["score"] == pytest.approx(1.0) and out["ranked"][1]["lexicon_missed"] == ["Slate"]
    assert rank.score({"words": 1.0, "pace": None}) == 1.0  # unmeasured parts drop out


def test_breathiness_flags_a_whisper_against_full_voice(tmp_path):
    rng = np.random.default_rng(2)
    def burst(kind):
        pieces = []
        for _ in range(3):
            seg = harmonic(210.0, 0.5) if kind == "voice" else rng.normal(0, 0.08, int(0.5 * SR))
            pieces += [seg * np.hanning(len(seg)) ** 0.2, np.zeros(int(0.15 * SR))]
        return np.concatenate(pieces)
    for name in ("full1", "full2"):
        audio.write_wav(tmp_path / f"{name}.wav", burst("voice"), SR)
    audio.write_wav(tmp_path / "whisper.wav", burst("noise"), SR)
    spans = {n: breath.span(breath.Frames(tmp_path / f"{n}.wav"), None) for n in ("full1", "full2", "whisper")}
    assert spans["whisper"]["voiced_frac"] < 0.3 < spans["full1"]["voiced_frac"]
    cal = breath.calibrate({"line": [spans["full1"], spans["full2"]]})
    assert breath.breathiness(spans["whisper"], "line", cal)["breathier"] is True
    assert breath.breathiness(spans["full2"], "line", cal)["breathier"] is False
