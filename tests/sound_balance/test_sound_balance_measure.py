"""The balance measurements on synthetic audio: every rule fails where it should and passes where it should, the
owner's half-depth trade is sorted the way he chose, the levels are palette data, and the streamed meters equal one
pass. Pure numpy: no film folder."""

from __future__ import annotations

import math

import numpy as np
import pytest

from slate.sound import balance as B
from slate.sound import measure as M

WORD_STARTS = [0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2]


@pytest.fixture
def base(helpers):
    sr = helpers.SR
    n = sr * 4
    words = helpers.words_at(WORD_STARTS)
    return {"sr": sr, "n": n, "words": words, "voice": helpers.speech(n, words),
            "score": helpers.pad(n, level_db=-50)}  # about 21.6 LU under the voice, where the films keep it


def _m(base, extra=None, *, hits=None, rides=None, bal=None, words="yes"):
    streams = {"voice": [(base["voice"], 1.0)], "score": [(base["score"], 1.0)], **(extra or {})}
    return B.measure(streams, base["sr"], words=base["words"] if words == "yes" else None, hits=hits, rides=rides, bal=bal)


def _unit(res, word):
    return next(r for r in res["clarity"]["worst"] + res["clarity"]["failing"] if r["word"] == word)


def test_a_well_balanced_score_and_voice_pass_every_rule(base):
    r = _m(base)
    assert r["unit"] == "word" and r["units"] == 8
    assert r["clarity"]["failing"] == [] and r["clarity"]["min_db"] > 30
    assert -23 < r["levels"]["integrated_lu_vs_voice"]["score"] < -20 and r["levels"]["failing"] == []
    assert all(v["min_margin_db"] > 0 for v in r["never_over"]["by_family"].values() if v)
    assert r["clarity"]["alone_under_floor"] == []
    assert r["under_speech"]["score"]["inside_words_lu"] < -19


def test_a_click_on_a_word_costs_that_word_and_the_same_click_in_a_pause_doesnt(base, helpers):
    on = helpers.place(base["n"], helpers.wood(amp=0.06), 1.25)  # on w3 (1.2-1.44 s)
    r = _m(base, {"accents": [(on, 1.0)]})
    assert [x["word"] for x in r["clarity"]["failing"]] == ["w3"]
    w3 = r["clarity"]["failing"][0]
    assert w3["voice_over_rest_db"] < 12 and w3["by_family_db"]["accents"] < 12 and w3["by_family_db"]["score"] > 30
    off = helpers.place(base["n"], helpers.wood(amp=0.06), 1.47)  # in the pause between w3 and w4
    assert _m(base, {"accents": [(off, 1.0)]})["clarity"]["failing"] == []


def test_without_word_timings_the_voice_is_measured_phrase_by_phrase(base, helpers):
    """Phrases are the voice's own spoken stretches, split at pauses of 300 ms or more: coarser than words (a click
    is averaged over its phrase), but nothing goes unmeasured while timing hasn't run."""
    spaced = helpers.words_at([0.4, 1.0, 1.6, 2.2, 2.8, 3.4])
    alone = dict(base, voice=helpers.speech(base["n"], spaced))
    on = helpers.place(base["n"], helpers.wood(amp=0.06), 1.65)  # on the third phrase
    r = _m(alone, {"accents": [(on, 1.0)]}, words=None)
    assert r["unit"] == "phrase" and r["units"] == 6
    assert [x["word"] for x in r["clarity"]["failing"]] == ["phrase 3"]
    gaps = B.phrases(np.array([True] * 30 + [False] * 40 + [True] * 20 + [False] * 10 + [True] * 25))
    assert [p[0] for p in gaps] == ["phrase 1", "phrase 2"]  # a 0.1 s pause doesn't split a phrase; 0.4 s does
    assert gaps[0][1:] == (0.0, 0.3) and gaps[1][1:] == (0.7, 1.25)


def test_never_over_names_the_family_the_moment_and_the_word(base, helpers):
    loud = helpers.place(base["n"], helpers.air(0.4, amp=0.12), 2.0)  # a whoosh over w5 (2.0-2.24 s), louder than him
    r = _m(base, {"textures": [(loud, 1.0)]})
    bad = r["never_over"]["failing"]
    assert set(bad) >= {"textures", "effects", "everything_else"}
    assert bad["textures"]["word"] == "w5" and 2.0 <= bad["textures"]["at"] <= 2.3 and bad["textures"]["min_margin_db"] < 0
    quiet = helpers.place(base["n"], helpers.air(0.25, amp=0.12), 3.7)  # the same whoosh once he's done: not "over"
    assert _m(base, {"textures": [(quiet, 1.0)]})["never_over"]["failing"] == {}


@pytest.mark.parametrize("amp, ok", [(0.001, True), (0.003, False)])
def test_texture_air_keeps_the_consonant_band_clear(base, helpers, amp, ok):
    tex = helpers.place(base["n"], helpers.air(1.0, amp=amp), 1.0)
    t = _m(base, {"textures": [(tex, 1.0)]})["texture_air"]
    assert t["units_with_texture"] >= 2
    assert (t["failing"] == []) is ok
    assert (t["min_db_under_voice"] >= 20) is ok


def test_accents_must_clear_the_score_in_their_own_band(base, helpers):
    clear = helpers.place(base["n"], helpers.wood(amp=0.01), 1.5)
    r = _m(base, {"accents": [(clear, 1.0)]}, hits=[{"id": "tick", "kind": "tap", "family": "accents", "t": 1.5,
                                                      "end": 1.62}])
    assert r["accents_over_score"]["failing"] == [] and r["accents_over_score"]["min_db"] > 3
    thump = helpers.place(base["n"], helpers.wood(pitch=220.0, amp=0.008, dur=0.2), 1.48)  # in the score's own band
    r = _m(base, {"accents": [(thump, 1.0)]}, hits=[{"id": "card", "kind": "land", "family": "accents", "t": 1.48,
                                                      "end": 1.68}])
    assert [row[1] for row in r["accents_over_score"]["failing"]] == ["card"]


def test_levels_check_each_family_and_the_ranges_are_palette_data(base, helpers):
    loud_score = dict(base, score=helpers.pad(base["n"], level_db=-40))  # about 11.6 LU under: too loud
    r = _m(loud_score)
    assert any(f.startswith("score: integrated") for f in r["levels"]["failing"])
    assert any("inside words" in f for f in r["levels"]["failing"])
    # a brand that wants its score nearer the voice says so in its palette, and the same mix passes
    bal = B.palette_balance({"balance": {"levels": {"score": {"integrated_lu": [-14, -8], "inside_words_max_lu": -9}},
                                         "clarity": {"alone_min_db": {}}}})
    assert _m(loud_score, bal=bal)["levels"]["failing"] == []
    # per hit: an accent hit far too loud for its family
    acc = helpers.place(base["n"], helpers.wood(amp=0.3), 1.5)
    r = _m(base, {"accents": [(acc, 1.0)]}, hits=[{"id": "k", "kind": "tap", "family": "accents", "t": 1.5, "end": 1.62}])
    assert r["hits"][0]["momentary_lu"] > -8
    assert any(f.startswith("accents: loudest hit") for f in r["levels"]["failing"])


def test_the_score_alone_keeps_its_own_floor(base, helpers):
    bright = dict(base, score=helpers.pad(base["n"], level_db=-50, bright=True))
    r = _m(bright)
    alone = r["clarity"]["each_family_alone_worst"]["score"]["db"]
    assert alone < 19
    assert r["clarity"]["alone_under_floor"] and r["clarity"]["alone_under_floor"][0]["family"] == "score"
    assert r["clarity"]["failing"] == [] or r["clarity"]["min_db"] < 12  # the 12 dB rule is judged on its own


def _rides(*hits):
    return {"rides": [{"hit": h, "t": t, "end": e, "family": "accents", "depth_db": -7.5, "full_depth_db": -15.0,
                       "half_depth": True} for h, t, e in hits], "pinned": []}


@pytest.mark.parametrize("amp, where", [(0.045, "traded"), (0.06, "flagged"), (0.12, "floor")])
def test_the_owners_in_between_trade(base, helpers, amp, where):
    """A word under a half-ridden accent may sit under 12 dB: listed as the trade; under 8 dB it's flagged for a
    person; under 6 dB the trade stops."""
    on = helpers.place(base["n"], helpers.wood(amp=amp), 1.25)
    r = _m(base, {"accents": [(on, 1.0)]}, rides=_rides(("tick", 1.25, 1.37)))
    c = r["clarity"]
    assert c["failing"] == []
    assert [x["word"] for x in c["traded_under_half_depth"]] == ["w3"]
    assert c["traded_under_half_depth"][0]["under_half_depth"] == ["tick"]
    assert ([x["word"] for x in c["traded_flagged"]] == ["w3"]) is (where == "flagged")
    assert ([x["word"] for x in c["traded_under_floor"]] == ["w3"]) is (where == "floor")


def test_an_approved_span_keeps_what_was_approved(base, helpers):
    on = helpers.place(base["n"], helpers.wood(amp=0.06), 1.25)
    bal = B.palette_balance({"balance": {"approved_spans": [{"from": 1.0, "to": 1.5, "why": "approved in a clip"}]}})
    c = _m(base, {"accents": [(on, 1.0)]}, bal=bal)["clarity"]
    assert c["failing"] == [] and [x["word"] for x in c["approved_span_under_rule"]] == ["w3"]
    pinned = {"rides": [], "pinned": [{"hit": "tick", "t": 1.25, "end": 1.37}]}
    c = _m(base, {"accents": [(on, 1.0)]}, rides=pinned)["clarity"]
    assert c["failing"] == [] and [x["word"] for x in c["approved_span_under_rule"]] == ["w3"]


def test_half_depth_words_compare_this_mix_with_the_one_before(base, helpers):
    full = _m(base, {"accents": [(helpers.place(base["n"], helpers.wood(amp=0.02), 1.25), 1.0)]})
    half_rides = _rides(("tick", 1.25, 1.37))
    now = _m(base, {"accents": [(helpers.place(base["n"], helpers.wood(amp=0.06), 1.25), 1.0)]}, rides=half_rides)
    hd = B.half_depth_words(now, full, half_rides)
    assert hd["half_ridden_hits"] == 1 and hd["words_under_them"] == 1
    row = hd["words"][0]
    assert row["word"] == "w3" and row["before_db"] > row["now_db"] and row["change_db"] < -5
    assert row["under"] == [{"hit": "tick", "full_db": -15.0, "now_db": -7.5}]
    assert hd["flagged"] and hd["flagged"][0]["word"] == "w3"
    md = B.half_depth_markdown(hd)
    assert "| w3 **(flag)** |" in md and "`tick` -15.0 → -7.5 dB" in md
    alone = B.half_depth_words(now, None, half_rides)
    assert alone["words"][0]["before_db"] is None and "| – |" in B.half_depth_markdown(alone)


def test_streamed_meters_equal_one_block(base, helpers):
    acc = helpers.place(base["n"], helpers.wood(amp=0.05), 2.1)
    streams = {"voice": [(base["voice"], 1.0)], "rest": [(base["score"], 1.0), (acc, 1.0)]}
    bands = {"voice": [(1000.0, 5000.0)], "rest": [(1000.0, 5000.0), (2000.0, 6000.0)]}
    one = B.run_meters(streams, base["sr"], bands=bands, block_s=100.0)
    many = B.run_meters(streams, base["sr"], bands=bands, block_s=0.37)
    for name in streams:
        np.testing.assert_allclose(many.ksum[name], one.ksum[name], rtol=1e-9, atol=1e-18)
    for key, v in one.band.items():
        np.testing.assert_allclose(many.band[key], v, rtol=1e-6, atol=1e-14)


def test_integrated_loudness_is_the_bs1770_meters(base):
    c = B.run_meters({"voice": [(base["voice"], 1.0)]}, base["sr"])
    assert math.isclose(c.integrated("voice"), M.integrated(base["voice"], base["sr"]), abs_tol=0.02)


def test_a_coefficient_makes_the_score_the_music_less_the_tonal_effects(base, helpers):
    tonal = helpers.place(base["n"], helpers.wood(pitch=880.0, amp=0.05, dur=0.4), 1.0)
    music = base["score"] + tonal
    c = B.run_meters({"score": [(music, 1.0), (tonal, -1.0)], "plain": [(base["score"], 1.0)]}, base["sr"])
    np.testing.assert_allclose(c.ksum["score"], c.ksum["plain"], rtol=1e-9, atol=1e-18)


def test_palette_balance_and_families(helpers):
    bal = B.palette_balance({"balance": {"clarity": {"min_db": 10.0}}})
    assert bal["clarity"]["min_db"] == 10.0 and bal["clarity"]["band_hz"] == [1000.0, 5000.0]
    assert B.palette_balance(None) == B.STARTING_BALANCE
    assert B.family_of_kind("tap", bal) == "accents" and B.family_of_kind("whoosh", bal) == "textures"
    assert B.family_of_kind("impact", bal) == "impacts" and B.family_of_kind("swell", bal) == "swells"
    own = B.palette_balance({"balance": {"families": {"clicks": {"kinds": ["tap"]}}}})
    assert set(own["families"]) == {"clicks"} and B.family_of_kind("tap", own) == "clicks"
    assert B.is_spoken("25%") and B.is_spoken("word") and not B.is_spoken("–")
