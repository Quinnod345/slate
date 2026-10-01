"""The tag-optimization loop: refused before any spend when a candidate breaks the rules, voiced and measured in replay
(every take a ledger row), resumable, gated and ranked on real reads, heard as an ABC reel, and adopted as the exact
file. Synthetic audio and a stand-in voice only; nothing is spent."""

import json

import pytest

from slate import jsonio, ledger
from slate.cli import run_cli
from slate.errors import Refused
from slate.voice import gates, tagloop
from slate.voice.film import VoiceFilm

from voice_helpers import rich_take, write_rich

LINE = "Slate hears each word, then fits it to the cut."


def write_candidates(film, lines):
    jsonio.write_atomic(film / "voice" / "tags" / "candidates.json", {"lines": lines})


def test_banned_tags_and_changed_words_are_refused_before_any_spend(voice_film):
    write_candidates(voice_film, {"S02": {"intent": "smile", "candidates": [
        {"key": "W1", "tagged": f"[whispering] {LINE}", "seeds": [1]},
        {"key": "C1", "tagged": "[confident] Slate hears every word, then fits it to the cut.", "seeds": [2]}]}})
    with pytest.raises(Refused) as exc:
        tagloop.optimize(voice_film)
    msg = exc.value.message
    assert "W1" in msg and "whisper" in msg and "C1" in msg and "strip back" in msg
    assert ledger.rows() == []


def test_a_round_in_replay_runs_offline_spends_through_the_ledger_and_resumes(voice_film):
    from slate.voice import tts

    tts.voice(voice_film, only="S02")
    rows_before = len(ledger.rows(op="tts"))
    write_candidates(voice_film, {"S02": {"intent": "none", "baseline_seeds": [301, 401], "candidates": [
        {"key": "N1", "tagged": f"[confident] {LINE}", "seeds": [101, 201]},
        {"key": "N2", "tagged": "Slate hears each word, [warmly, with a smile in her voice] then fits it to the cut.",
         "seeds": [102]}]}})
    code, out, err = run_cli(["voice", "tags", "optimize", "--estimate", "--film", str(voice_film), "--json"])
    assert code == 0, err
    est = json.loads(out)
    assert est["new"] == 5 and est["estimate"]["characters"] > 0 and len(ledger.rows(op="tts")) == rows_before
    res = tagloop.optimize(voice_film)
    assert len(res["voiced"]) == 5 and len(ledger.rows(op="tts")) == rows_before + 5
    folder = voice_film / "voice" / "tags" / "main"
    metrics = jsonio.read(folder / "metrics.json")
    assert set(metrics["takes"]) == {"S02_N1s101", "S02_N1s201", "S02_N2s102", "S02_Us301", "S02_Us401",
                                     "S02_current"}
    assert metrics["takes"]["S02_current"]["untagged"] and not metrics["takes"]["S02_N1s101"]["untagged"]
    assert metrics["takes"]["S02_N2s102"]["spans"]["tag"]["start_word"] == 4
    again = tagloop.optimize(voice_film)  # resumable: nothing new to voice
    assert again["voiced"] == [] and len(ledger.rows(op="tts")) == rows_before + 5
    code, out, err = run_cli(["voice", "tags", "pick", "--film", str(voice_film), "--json"])
    assert code == 0, err
    pick = json.loads(out)["picks"]["S02"]
    assert pick["pick"] is None and "replayed stand-ins" in pick["why"]
    assert (folder / "ranking.md").is_file()


def _real_round(film, *, candidates, baseline=3, intent="none", name="main"):
    """A round of real (non-replayed) synthetic reads of S02, words known, as if voiced live and transcribed."""
    vf = VoiceFilm(film)
    folder = film / "voice" / "tags" / name
    man = {"takes": {}}
    jobs = [("U", f"Us{300 + i}", LINE, "baseline", {"seed": 300 + i}) for i in range(baseline)]
    jobs += [(k, f"{k}s1", tagged, "candidate", kw) for k, tagged, kw in candidates]
    for cand, key, tagged, role, kw in jobs:
        rel = {d: f"voice/tags/{name}/{d}/S02_{key}.wav" for d in ("raw", "clean", "paced")}
        words = write_rich(film / rel["raw"], LINE, **{"seed": 5, **kw})
        for d in ("clean", "paced"):
            (film / rel[d]).parent.mkdir(parents=True, exist_ok=True)
            (film / rel[d]).write_bytes((film / rel["raw"]).read_bytes())
        man["takes"][f"S02_{key}"] = {"shot": "S02", "key": key, "cand": cand, "tagged": tagged, "seed": 1,
                                      "stability": None, "context": "neighbours", "role": role, "text": LINE,
                                      "intent": intent, **rel, "replay": False, "words": words,
                                      "request_id": f"req_{key}", "voice_id": "v", "model": "eleven_v4"}
    jsonio.write_atomic(folder / "takes.json", man)
    write_candidates(film, {"S02": {"intent": intent, "candidates": [{"key": k, "tagged": t, "seeds": [1]}
                                                                     for k, t, _ in candidates]}})
    return vf


def test_real_reads_are_gated_and_a_whisper_like_take_is_out(voice_film):
    vf = _real_round(voice_film, candidates=[
        ("Good", f"[confident] {LINE}", {"seed": 9}),
        ("Wisp", f"[matter-of-fact] {LINE}", {"seed": 9, "whisper": True}),
        ("Cut", f"[steady] {LINE}", {"seed": 9, "end_cut": True})])
    tagloop.measure_round(vf, "main", tagloop.load_candidates(vf))
    res = tagloop.pick_round(vf, "main")
    rows = {r["key"]: r for r in res["ranking"]["S02"]}
    assert rows["Wisps1"]["gates"]["full_voice"] is False and not rows["Wisps1"]["passes"]
    assert rows["Cuts1"]["gates"]["complete"] is False
    assert rows["Goods1"]["passes"], rows["Goods1"]["gates"]
    assert res["picks"]["S02"]["pick"] == "S02_Goods1"
    assert rows["Goods1"]["breath_vs_untagged"]["line"]["untagged_max"] is not None


def test_the_feelings_are_measured_against_the_untagged_reads():
    base = {"f0_median_hz": 180.0, "centroid_hz": 1000.0, "f0_sd_st": 1.0, "effort_level_db": -20.0,
            "voiced_frac": 0.8, "lift_vs_rest_st": 0.5}
    smile = {**base, "f0_median_hz": 180.0 * 2 ** (1.5 / 12), "centroid_hz": 1060.0}
    assert tagloop.emotion("smile", smile, base, {})[0]
    assert not tagloop.emotion("smile", {**smile, "f0_median_hz": 180.0 * 2 ** (5 / 12)}, base, {})[0]  # theatrical
    assert tagloop.emotion("reassure", {**base, "f0_sd_st": 0.7}, base, {})[0]
    assert not tagloop.emotion("reassure", {**base, "f0_sd_st": 0.7, "effort_level_db": -22.0}, base, {})[0]
    assert tagloop.emotion("hopeful_end", {**base, "lift_vs_rest_st": 1.2}, base, {})[0]
    ctx = {"question_words": {"4": {"word": "why", "rise_st": 1.0}}, "base_q": {"4": {"max": 6.0, "mean": 4.0}}}
    assert tagloop.emotion("rhetorical", base, base, ctx)[0]
    ref = {"range_p10_p90_st": 6.0, "peak_lift_st": 7.0, "level_sd_db": 9.0, "words_per_s": 2.6,
           "median_shift_st": 1.0, "last_word_rise_creak_free_st": 2.0}
    calm = {"range_p10_p90_st": 3.0, "peak_lift_st": 4.0, "level_sd_db": 5.0, "words_per_s": 2.9,
            "median_shift_st": 1.2, "last_word_rise_creak_free_st": 1.0}
    u = {"range_p10_p90_st": [3.5, 4.0], "peak_lift_st": [3.0, 4.0], "level_sd_db": [5.0, 8.0],
         "words_per_s": [2.8, 3.0], "median_shift_st": [0.0, 0.0]}
    ok, score, notes = tagloop.emotion("calmer", base, base, {"prosody": calm, "reference_prosody": ref,
                                                              "untagged_prosody": u})
    assert ok and score > 1 and all(notes["calmer"].values()) and all(notes["not_flat"].values())
    flat = {**calm, "range_p10_p90_st": 1.0}
    assert not tagloop.emotion("calmer", base, base, {"prosody": flat, "reference_prosody": ref,
                                                      "untagged_prosody": u})[0]
    assert tagloop.emotion("none", {}, {}, {})[0]


def test_abc_then_adopt_puts_the_exact_file_in_the_film(voice_film, fake_whisper):
    vf = VoiceFilm(voice_film)
    rich_take(vf, "S02", seed=11)  # the film's own take of the line (untagged): A in the reel, and a baseline
    vf = _real_round(voice_film, candidates=[("Good", f"[confident] {LINE}", {"seed": 9}),
                                              ("Also", f"[steady] {LINE}", {"seed": 12})])
    tagloop.measure_round(vf, "main", tagloop.load_candidates(vf))
    tagloop.pick_round(vf, "main")
    code, out, err = run_cli(["voice", "tags", "abc", "S02", "--film", str(voice_film), "--json"])
    assert code == 0, err
    abc = json.loads(out)
    assert abc["order"][0] == "A" and len(abc["order"]) >= 2
    folder = voice_film / "voice" / "tags" / "main" / "abc" / "S02"
    assert (folder / "reel.wav").is_file() and (folder / "abc.json").is_file()
    assert all((voice_film / s).read_bytes()[:4] == b"\x89PNG" for s in abc["sheets"])
    assert all(abs(t["lufs_after"] + 20.0) < 0.3 for t in abc["takes"].values())  # level-matched
    round_clean = voice_film / "voice" / "tags" / "main" / "clean" / "S02_Goods1.wav"
    code, out, err = run_cli(["voice", "tags", "adopt", "S02", "Goods1", "--film", str(voice_film), "--json"])
    assert code == 0, err
    res = json.loads(out)
    vf = VoiceFilm(voice_film)
    take = res["take"]
    assert vf.selected("S02")[0] == take == "t02"
    assert jsonio.sha256(vf.take_dir("S02") / f"{take}.clean.wav") == jsonio.sha256(round_clean)  # the exact file
    assert vf.tagged_for("S02") == f"[confident] {LINE}"
    tj = vf.take_json("S02", take)
    assert tj["stages"]["tag_round"]["gates"]["full_voice"] is True and tj["sent"] == f"[confident] {LINE}"
    lock = jsonio.read(voice_film / "film.lock.json")["files"][f"voice/takes/S02/{take}.clean.wav"]
    assert lock["producer"] == "slate voice tags adopt"
    # the inflection gate reads the round's same-words verdict for the adopted take: no ear needed for the tag
    set_transcript, _ = fake_whisper
    for shot in ("S01", "S04"):
        wav, words = rich_take(vf, shot, seed=30 + len(shot))
        set_transcript(wav, {"text": "", "segments": [{"words": [{"word": " " + w["word"], "start": w["start"],
                                                                   "end": w["end"]} for w in words]}]})
    paced = vf.final_audio("S02")
    from voice_helpers import rich_voice  # the adopted take's words, as Whisper would hear them

    _, words = rich_voice(LINE, seed=9)
    set_transcript(paced, {"text": LINE, "segments": [{"words": [{"word": " " + w["word"], "start": w["start"],
                                                                  "end": w["end"]} for w in words]}]})
    from slate.voice import align

    align.align(voice_film)
    r = {g.gate: g for g in gates.run(voice_film, ["voice_inflection"], write=False)}["voice_inflection"]
    assert r.details["lines"]["S02"]["same_words"]["gates"]["full_voice"] is True
    assert not any("S02: tagged" in x for x in r.details["reviews"])


def test_adopting_over_an_approved_line_needs_the_persons_go(voice_film):
    vf = VoiceFilm(voice_film)
    rich_take(vf, "S02", seed=11)
    code, _, err = run_cli(["voice", "approve", "S02", "--by", "the owner", "--film", str(voice_film)])
    assert code == 0, err
    vf = _real_round(voice_film, candidates=[("Good", f"[confident] {LINE}", {"seed": 9})])
    with pytest.raises(Refused):
        tagloop.adopt(vf, "main", "S02", "Goods1")
    res = tagloop.adopt(vf, "main", "S02", "Goods1", force=True)
    assert res["take"] == "t02"
