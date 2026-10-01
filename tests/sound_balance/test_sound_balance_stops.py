"""Planned stops: a fade into a planned silence belongs to the silence, the composed score's own rests are planned
beats, and the film opens out of silence and ends in it when its plan says so."""

from __future__ import annotations

import numpy as np

from slate.sound import balance as B
from slate.sound import gap


def _music(helpers, seconds: float = 6.0) -> np.ndarray:
    return helpers.pad(int(seconds * helpers.SR), level_db=-30)


def test_a_fade_into_a_planned_stop_is_part_of_the_stop(helpers):
    sr = helpers.SR
    music = helpers.pad(int(6.0 * sr), level_db=-40)
    stop = {"from": 3.0, "to": 3.5, "why": "a held breath"}
    fade = int(1.0 * sr)
    music[int(2.0 * sr):int(3.0 * sr)] *= np.linspace(1, 0, fade)[:, None] ** 6  # the music fading into the breath
    music[int(3.0 * sr):int(3.5 * sr)] = 0.0
    plain = gap.check(music, sr, [stop])
    assert not plain["pass"], "the fade's last half second is under -60 dBFS: a hole without its tail"
    widened, med = B.widen_stops(music, sr, [stop])
    w = widened[0]
    assert w["planned"] == [3.0, 3.5] and w["from"] < 3.0 and 0 < w["fade_tails_s"][0] <= 2.0
    assert gap.check(music, sr, widened)["pass"]
    assert -50 < med < -30


def test_a_hole_away_from_the_plan_is_still_a_hole(helpers):
    sr = helpers.SR
    music = _music(helpers)
    music[int(4.2 * sr):int(5.0 * sr)] = 0.0
    widened, _ = B.widen_stops(music, sr, [{"from": 1.0, "to": 1.2}])
    res = gap.check(music, sr, widened)
    assert not res["pass"] and res["failures"][0]["t"] >= 4.0


def test_the_scores_own_rests_are_planned_beats(helpers):
    sr = helpers.SR
    score = _music(helpers)
    score[int(2.0 * sr):int(2.6 * sr)] = 0.0
    rests, med = B.score_rests(score, sr)
    assert [(r["from"], r["to"]) for r in rests] == [(2.0, 2.6)] and "silent" in rests[0]["why"]
    assert gap.check(score, sr, rests)["pass"]


def test_whether_a_score_opens_out_of_silence(helpers):
    sr = helpers.SR
    score = _music(helpers)
    assert not B.opens_in_silence(score, sr)
    score[: int(0.6 * sr)] = 0.0
    assert B.opens_in_silence(score, sr)


def test_silences_reads_the_first_sound_and_the_last_second(helpers, tmp_path):
    from slate.sound import audio

    sr = helpers.SR
    mix = _music(helpers)
    mix[: int(0.7 * sr)] = 0.0
    mix[-sr:] = 0.0
    s = B.silences(mix, sr)
    assert s["first_sound_s"] == 0.7 and s["last_peak_dbfs"] == -240.0 and s["opening_peak_dbfs"] == -240.0
    p = audio.write_wav(tmp_path / "mix.wav", mix, sr, 24)
    assert B.silences(p)["first_sound_s"] == 0.7
