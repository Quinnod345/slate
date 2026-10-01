"""The score's own clarity guard (slate.sound.guard): its top dips under the words it would crowd, by just enough, and
nowhere else. Synthetic audio whose answers are known."""

import numpy as np
from scipy import signal

import sound_helpers as H
from slate import jsonio
from slate.sound import audio, balance, guard, mix

SR = H.SR
WORDS = [("one", 0.5, 0.8), ("two", 1.5, 1.8), ("three", 2.5, 2.9)]


def _voice(n: int) -> np.ndarray:
    """Voiced syllables with harmonics well into 1-5 kHz inside each word."""
    rng = np.random.default_rng(3)
    y = np.zeros(n)
    for _w, s, e in WORDS:
        k = np.arange(int((e - s) * SR)) / SR
        harm = sum(np.sin(2 * np.pi * 140 * h * k + rng.uniform(0, 6.28)) / h ** 0.6 for h in range(1, 40))
        y[int(s * SR):int(s * SR) + len(k)] += 0.3 * np.sin(np.pi * k / (e - s)) ** 2 * harm / np.max(np.abs(harm))
    return np.stack([y, y], 1)


def _bright(n: int, level_db: float) -> np.ndarray:
    """A score with a shimmer in the consonant band (1.2-4.5 kHz) over a low chord."""
    rng = np.random.default_rng(5)
    t = np.arange(n) / SR
    sh = signal.sosfilt(signal.butter(4, [1200, 4500], "bandpass", fs=SR, output="sos"), rng.standard_normal(n))
    x = np.sin(2 * np.pi * 220 * t) + 1.5 * sh / sh.std()
    x *= 10 ** (level_db / 20) / np.sqrt(np.mean(x ** 2))
    return np.stack([x, 0.98 * x], 1)


def test_the_top_dips_under_crowded_words_by_just_enough_and_nowhere_else():
    n = 4 * SR
    voice, music = _voice(n), _bright(n, -32.0)
    out, rep = guard.apply(music, voice, WORDS, SR)
    assert rep["words"] == 3 and rep["words_dipped"] >= 1
    assert rep["alone_worst_before_db"] < 19.0 <= rep["alone_worst_after_db"] + 0.3
    assert all(-20.0 <= d["dip_db"] < 0 for d in rep["dips"]) and not rep["capped"]
    # past the last word's hold and ramp the score plays exactly as composed, and it was never raised anywhere
    assert np.allclose(out[int(3.6 * SR):], music[int(3.6 * SR):], atol=1e-9)
    assert np.sum(out ** 2) < np.sum(music ** 2)
    assert music is not out  # the caller's score is never changed in place


def test_a_score_that_already_clears_the_words_is_left_alone():
    n = 4 * SR
    voice = _voice(n)
    dark = H.pad(4.0, level_db=-30.0)[:n]  # a chord under 600 Hz: nothing in the consonant band
    out, rep = guard.apply(dark, voice, WORDS, SR)
    assert rep["words_dipped"] == 0 and rep["alone_worst_before_db"] >= 19.0
    assert np.array_equal(out, dark)


def test_a_dip_never_goes_past_its_cap_and_says_so():
    n = 4 * SR
    out, rep = guard.apply(_bright(n, -8.0), _voice(n), WORDS, SR, {"max_dip_db": -6.0})
    assert rep["capped"] and min(d["dip_db"] for d in rep["dips"]) >= -6.0 - 1e-9
    assert rep["alone_worst_after_db"] < 19.0  # the cap holds, and the report says the word is still crowded


def test_the_mix_guards_its_score_and_the_score_alone_floor_holds(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "bright", score=False)
    audio.write_wav(fd / "sound" / "score" / "score.wav", _bright(14 * SR, -20.0), SR, 24)
    rep = jsonio.read(mix.run(fd).with_name("final_mix.json"))
    g = rep["score_guard"]
    assert g["words_dipped"] > 0 and g["target_db"] == 19.0 and g["split_hz"] == 600.0
    assert jsonio.validate(rep, "mix") == []
    guarded = balance.gate_clarity(fd)
    assert not [r for r in guarded.details["alone_under_floor"] if r["family"] == "score"], guarded.summary
    # a brand that turns the guard off gets the score as composed, and the score-alone floor names the crowded words
    H.brand(slate_home["brands"], palette={"balance": {"score_guard": False}})
    rep = jsonio.read(mix.run(fd).with_name("final_mix.json"))
    assert "score_guard" not in rep
    plain = balance.gate_clarity(fd)
    assert plain.status == "FAIL" and [r for r in plain.details["alone_under_floor"] if r["family"] == "score"]
