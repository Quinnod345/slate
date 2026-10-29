"""Golden data: the balance check rerun on a delivered p3 mix must say what the films' own check said.

Film B's balanced mix (build/B/audio_p3_score, the composed score, with the owner's half-depth rides) is measured from
its family stems and its word list, read-only, and compared with its balance_check.json, planned_stops.json and
half_depth_words.md. It meters 7.4 minutes of six stems (about ten seconds), so it runs with SLATE_GOLDEN_HEAVY=1.
"""

import json
import math
import os

import pytest

from slate.sound import balance as B

heavy = pytest.mark.skipif(not os.environ.get("SLATE_GOLDEN_HEAVY"),
                           reason="meters a whole delivered mix: set SLATE_GOLDEN_HEAVY=1")
MIX = "build/B/audio_p3_score"


def _load(filmroot):
    d = filmroot / MIX
    if not (d / "balance_check.json").exists():
        pytest.skip("film B's p3 mix isn't on this machine")
    ref = json.loads((d / "balance_check.json").read_text())
    sheet = json.loads((d / "cue_sheet.json").read_text())
    used = json.loads((d / "balance_used.json").read_text())
    fam = {p.stem: p for p in (d / "family_stems").glob("*.wav")}
    streams = {"voice": [(fam["voice"], 1.0)], **{k: [(p, 1.0)] for k, p in fam.items() if k != "voice"}}
    spans = {h["hk"]: (h["t"], h["end"]) for h in ref["hits"]}
    # The source checker also treats absent pinned metadata as no pinned spans. Full-depth rides are not approvals.
    rides = {"rides": [{"hit": hk, "t": spans[hk][0], "end": spans[hk][1], "half_depth": True}
                       for hk in used["half_depth"]["hits"] if hk in spans],
             "pinned": [{"hit": hk, "t": spans[hk][0], "end": spans[hk][1]} for hk in (used.get("pinned") or {}).get("hits", [])
                        if hk in spans]}
    return d, ref, sheet, streams, rides


@heavy
def test_the_balance_check_matches_film_bs_own(filmroot):
    d, ref, sheet, streams, rides = _load(filmroot)
    res = B.measure(streams, 48000, words=[tuple(w) for w in sheet["words"]], rides=rides)
    ours = {(w, round(s, 3)): db for w, s, _e, db in res["clarity"]["per_unit"]}
    theirs = {(w, round(s, 3)): db for w, s, db in ref["clarity"]["per_word"]}
    assert set(ours) == set(theirs) and len(ours) == 978
    assert max(abs(ours[k] - theirs[k]) for k in ours) <= 0.05
    assert (res["clarity"]["min_db"], res["clarity"]["median_db"]) == (ref["clarity"]["min_db"], ref["clarity"]["median_db"])
    for f, v in res["clarity"]["each_family_alone_worst"].items():
        assert abs(v["db"] - ref["clarity"]["each_family_alone_worst"][f]["db"]) <= 0.05
    # the owner's trade, word by word: the same words traded, approved, failing and flagged
    assert len(res["clarity"]["traded_under_half_depth"]) == len(ref["clarity"]["traded_under_half_depth"])
    assert len(res["clarity"]["approved_span_under_rule"]) == len(ref["clarity"]["approved_span_under_rule"])
    assert res["clarity"]["failing"] == [] == ref["clarity"]["failing"]
    flagged = sorted((r["word"], r["s"]) for r in res["clarity"]["traded_flagged"] + res["clarity"]["traded_under_floor"])
    assert flagged == sorted((w["word"], w["s"]) for w in ref["clarity"]["flagged_under_db"]["words"])
    for f, v in ref["never_over"]["by_family"].items():
        mine = res["never_over"]["by_family"].get(f)
        if v is None:
            assert mine is None
            continue
        assert abs(mine["min_margin_db"] - v["min_margin_db"]) <= 0.1 and mine["word"] == v["word"]
        assert abs(mine["at"] - v["at"]) <= 0.011
    assert abs(res["texture_air"]["min_db_under_voice"] - ref["texture_air"]["min_db_under_voice"]) <= 0.1
    assert res["texture_air"]["units_with_texture"] == ref["texture_air"]["words_with_texture"]
    assert abs(res["voice_lufs"] - ref["levels"]["voice_lufs"]) <= 0.1
    for f, v in ref["levels"]["integrated_lu_vs_voice"].items():
        if v is not None:
            delta = abs(res["levels"]["integrated_lu_vs_voice"][f] - v)
            # Both records round to tenths; binary subtraction must not reject the exact 0.1 LU boundary.
            assert delta <= 0.1 or math.isclose(delta, 0.1, rel_tol=1e-12), f
    for f, v in ref["under_speech"].items():
        for k in ("inside_words_lu", "between_words_lu"):
            assert abs(res["under_speech"][f][k] - v[k]) <= 0.15, (f, k)


@heavy
def test_planned_stops_and_score_rests_match_film_bs(filmroot):
    d, _ref, sheet, _streams, _rides = _load(filmroot)
    recorded = json.loads((d / "planned_stops.json").read_text())
    widened, med = B.widen_stops(d / "stems" / "music.wav", None, sheet["planned_stops"])
    assert round(med, 1) == recorded["music_median_dbfs"]
    assert [(s["from"], s["to"], s["fade_tails_s"]) for s in widened] == \
        [(s["from"], s["to"], s["fade_tails_s"]) for s in recorded["planned_stops"][:len(widened)]]
    score = filmroot / "sound" / "music" / "B" / "score_B_v2.wav"
    if score.exists():
        rests, _ = B.score_rests(score)
        assert [(r["from"], r["to"]) for r in rests] == [(r["from"], r["to"]) for r in recorded["score_rests"]]
        assert B.opens_in_silence(score) is False  # B's score sounds from frame one, by its own design
    s = B.silences(d / "final_mix.wav")
    assert s["first_sound_s"] == 0.0 and s["last_peak_dbfs"] <= -90
