"""The balance gates on small mixed films: what passes, what fails, what can't be measured yet (and says so), the
owner's trade waiting for a person, the reports, and the way slate verify calls them."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import numpy as np
import pytest

from slate import gates, jsonio
from slate.errors import MissingPrerequisite
from slate.sound import balance as B

WORD_STARTS = [0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2]


def _film(mix_film, helpers, *, score_db: float = -50.0, bright: bool = False, effects=None, families: bool = False,
          accent_amp: float | None = None, air_amp: float | None = None, starts=WORD_STARTS, **kw):
    """A balanced film that ends the films' way, in a silent last second: its music stops there, a planned stop."""
    kw.setdefault("seconds", 4.6)
    f = mix_film(**kw)
    words = helpers.words_at(starts)
    f.words(words)
    voice = helpers.speech(f.n, words)
    music = helpers.pad(f.n, level_db=score_db, bright=bright)
    music[f.n - helpers.SR:] = 0.0
    helpers.write_json(f.path / "sound" / "score" / "score.json", {"planned_stops": [
        {"from": round(f.seconds - 1.0, 3), "to": f.seconds, "why": "the film ends in silence"}]})
    fam = {}
    hits = []
    if accent_amp is not None:
        fam["accents"] = helpers.place(f.n, helpers.wood(amp=accent_amp), 1.25)
        hits.append({"id": "tick", "kind": "tap", "t": 1.25, "beat": 1.25, "dur": 0.12})
    if air_amp is not None:
        fam["textures"] = helpers.place(f.n, helpers.air(1.0, amp=air_amp), 1.0)
        hits.append({"id": "write", "kind": "type", "t": 1.0, "beat": 1.5, "dur": 1.0})
    eff = sum(fam.values()) if fam else effects
    f.mix(dialogue=voice, music=music, effects=eff,
          families={"voice": voice, "score": music, **fam} if families else None)
    if hits:
        f.cues(hits)
    return f


def _by_name(results):
    return {r.gate: r for r in results}


def test_a_balanced_score_and_voice_pass_every_gate(mix_film, helpers):
    f = _film(mix_film, helpers)
    res = _by_name(B.run(f.path))
    assert set(res) == set(B.GATES)
    assert {k: r.status for k, r in res.items()} == {k: "PASS" for k in B.GATES}, {k: r.summary for k, r in res.items()}
    assert "no textures" in res["sound.balance.texture_air"].summary
    assert "8 words" in res["sound.balance.clarity"].summary
    from slate import qa

    for r in res.values():
        assert qa.valid_result(r) == [], r.gate
        assert r.engine["name"] == "slate balance" and r.details["stage"] == "sound"
    assert "sound/mix/stems/dialogue.wav" in res["sound.balance.clarity"].inputs
    report = jsonio.read(f.path / B.REPORT)
    assert report["kind"] == "balance_check" and report["where"]["source"] == "delivery stems"
    assert gates.load_report(f.path)["sound.balance.levels"].status == "PASS"


def test_a_loud_score_fails_its_range_and_a_bright_one_crowds_the_words(mix_film, helpers):
    loud = _by_name(B.run(_film(mix_film, helpers, score_db=-40).path, write=False))
    assert loud["sound.balance.levels"].status == "FAIL" and "score: integrated" in loud["sound.balance.levels"].summary
    bright = _by_name(B.run(_film(mix_film, helpers, bright=True).path, write=False))
    c = bright["sound.balance.clarity"]
    assert c.status == "FAIL" and "the score on its own crowds" in c.summary


def test_the_brands_palette_and_the_films_own_set_the_levels(mix_film, helpers, slate_home):
    """The ranges are sound-palette data: a brand that wants its score nearer the voice says so in brand.json
    (sound.palette.balance), and a film can say so in its own film.json; the same loud mix then passes."""
    near = {"balance": {"levels": {"score": {"integrated_lu": [-14, -8], "inside_words_max_lu": -9}},
                        "clarity": {"alone_min_db": {}}}}
    pack = slate_home["brands"] / "nearbrand"
    pack.mkdir()
    (pack / "brand.json").write_text(json.dumps({"name": "nearbrand", "sound": {"palette": near},
                                                 "references": {"truth": None, "look_motion": None, "voice": None,
                                                                "sound": None}}))
    by_brand = _film(mix_film, helpers, score_db=-40, brand="nearbrand")
    assert _by_name(B.run(by_brand.path, write=False))["sound.balance.levels"].status == "PASS"
    by_film = _film(mix_film, helpers, score_db=-40, palette=near)
    assert _by_name(B.run(by_film.path, write=False))["sound.balance.levels"].status == "PASS"
    starting = _film(mix_film, helpers, score_db=-40)
    assert _by_name(B.run(starting.path, write=False))["sound.balance.levels"].status == "FAIL"


def test_effects_in_one_stem_leave_the_family_rules_unchecked_never_passed(mix_film, helpers):
    f = mix_film()
    words = helpers.words_at(WORD_STARTS)
    f.words(words)
    voice = helpers.speech(f.n, words)
    f.mix(dialogue=voice, music=helpers.pad(f.n, level_db=-50),
          effects=helpers.place(f.n, helpers.wood(amp=0.01), 1.5))
    res = _by_name(B.run(f.path, write=False))
    for g in ("sound.balance.texture_air", "sound.balance.accents", "sound.balance.levels"):
        assert res[g].status == "UNCHECKED", (g, res[g].summary)
        assert "family stems" in " ".join(res[g].evidence)
    assert res["sound.balance.clarity"].status == "PASS" and res["sound.balance.never_over"].status == "PASS"


def test_family_stems_are_measured_family_by_family(mix_film, helpers):
    f = _film(mix_film, helpers, families=True, accent_amp=0.01, air_amp=0.003)
    res = _by_name(B.run(f.path))
    assert res["sound.balance.texture_air"].status == "FAIL"
    assert res["sound.balance.accents"].status == "PASS" and "1 hit(s)" in res["sound.balance.accents"].summary
    report = jsonio.read(f.path / B.REPORT)
    assert report["where"]["source"] == "family stems"
    assert {h["family"] for h in report["hits"]} == {"accents", "textures"}
    quiet = _film(mix_film, helpers, families=True, accent_amp=0.01, air_amp=0.001)
    assert _by_name(B.run(quiet.path, write=False))["sound.balance.texture_air"].status == "PASS"


def test_a_traded_word_waits_for_a_person_and_passes_once_accepted(mix_film, helpers):
    from slate.qa import accept

    f = _film(mix_film, helpers, families=True, accent_amp=0.06)
    f.rides({"rides": [{"hit": "tick", "depth_db": -7.5, "full_depth_db": -15.0, "half_depth": True}]})
    r = _by_name(B.run(f.path))["sound.balance.clarity"]
    assert r.status == "REVIEW", r.summary
    assert [i["key"] for i in r.details["review_items"]] == ["word:1.200"]
    assert "the trade" in r.details["review_items"][0]["what"]
    md = (f.path / "sound/mix/half_depth_words.md").read_text()
    assert "| w3 **(flag)** |" in md
    accept.accept(f.path, "sound.balance.clarity:word:1.200", by="Quinn", reason="brief, under the click; fine by ear")
    again = _by_name(B.run(f.path))["sound.balance.clarity"]
    assert again.status == "PASS" and again.details["accepted"][0]["accepted_by"] == "Quinn"


def test_a_trade_below_the_floor_fails(mix_film, helpers):
    f = _film(mix_film, helpers, families=True, accent_amp=0.12)
    f.rides({"rides": [{"hit": "tick", "half_depth": True}]})
    r = _by_name(B.run(f.path, write=False))["sound.balance.clarity"]
    assert r.status == "FAIL" and "floor" in r.summary


def test_the_previous_mix_is_kept_for_the_half_depth_comparison(mix_film, helpers):
    f = _film(mix_film, helpers, families=True, accent_amp=0.02, film_id="remix")
    B.run(f.path)
    first = jsonio.read(f.path / B.REPORT)["mix_sha"]
    g = _film(mix_film, helpers, families=True, accent_amp=0.06, film_id="remix-next")
    # the same film remixed: move the new mix into the first film's folder
    import shutil

    shutil.rmtree(f.path / "sound" / "mix" / "family_stems")
    shutil.rmtree(f.path / "sound" / "mix" / "stems")
    for sub in ("family_stems", "stems"):
        shutil.copytree(g.path / "sound" / "mix" / sub, f.path / "sound" / "mix" / sub)
    f.rides({"rides": [{"hit": "tick", "depth_db": -7.5, "full_depth_db": -15.0, "half_depth": True}]})
    B.run(f.path)
    before = jsonio.read(f.path / B.BEFORE)
    assert before["mix_sha"] == first != jsonio.read(f.path / B.REPORT)["mix_sha"]
    hd = jsonio.read(f.path / "sound/mix/half_depth_words.json")
    row = next(r for r in hd["words"] if r["word"] == "w3")
    assert row["before_db"] is not None and row["change_db"] < -5


def test_no_mix_yet_is_unchecked_with_the_next_step(mix_film, helpers):
    f = mix_film()
    f.words(helpers.words_at(WORD_STARTS))
    res = B.run(f.path, write=False)
    assert all(r.status == "UNCHECKED" for r in res)
    assert all("next: Run `slate mix` first." in r.evidence for r in res)
    with pytest.raises(MissingPrerequisite):
        B.gate_clarity(f.path)


def test_a_stem_at_the_wrong_rate_is_unchecked_not_a_crash(mix_film, helpers):
    from slate.sound import audio

    f = _film(mix_film, helpers)
    x, _ = audio.read_wav(f.path / "sound/mix/stems/dialogue.wav")
    audio.write_wav(f.path / "sound/mix/stems/dialogue.wav", x, 44100, 24)
    r = B.gate_clarity(f.path)
    assert r.status == "UNCHECKED" and "44100" in r.summary


def test_the_gates_take_verifys_context(mix_film, helpers):
    from slate.sound.film import load

    f = _film(mix_film, helpers)
    ctx = SimpleNamespace(film=load(f.path))
    for name, gate in B.GATES.items():
        assert gate(ctx).gate == name


def test_the_score_is_the_music_less_the_tonal_effects(mix_film, helpers):
    f = mix_film(seconds=4.6)  # made as _film makes the plain one below, ending in a silent last second
    words = helpers.words_at(WORD_STARTS)
    f.words(words)
    voice = helpers.speech(f.n, words)
    music = helpers.pad(f.n, level_db=-50)
    music[f.n - helpers.SR:] = 0.0
    tonal = helpers.place(f.n, helpers.wood(pitch=440.0, amp=0.2, dur=0.4), 2.1)
    f.mix(dialogue=voice, music=music, tonal=tonal)
    B.run(f.path, write=False)
    assert not (f.path / B.REPORT).exists() and not (f.path / "qa" / "gates.json").exists()  # a dry run writes nothing
    B.run(f.path)
    rep = jsonio.read(f.path / B.REPORT)
    assert rep["where"]["score_less_tonal"] == ["sound/mix/engine_stems/tonal.wav"]
    plain = _film(mix_film, helpers)
    B.run(plain.path)
    plain_rep = jsonio.read(plain.path / B.REPORT)
    a, b = rep["levels"]["integrated_lu_vs_voice"]["score"], plain_rep["levels"]["integrated_lu_vs_voice"]["score"]
    assert abs(a - b) <= 0.1  # the tonal hit isn't counted as score
    assert rep["levels"]["integrated_lu_vs_voice"]["effects"] is not None  # it's counted with the effects


@pytest.mark.parametrize("case, expect", [("opening-held", "PASS"), ("opening-broken", "FAIL"), ("ending-broken", "FAIL"),
                                          ("hole-in-mix", "FAIL"), ("rest-in-score", "PASS"), ("ending-sounds", "FAIL"),
                                          ("ending-left-to-the-plan", "PASS")])
def test_planned_stops_gate(mix_film, helpers, case, expect):
    """The films' rule (L-sound-S022): a silent last second always, and nothing before 0.5 s only when the plan opens
    out of silence. A brand can leave the ending to its plan (end_silent "plan")."""
    sr = helpers.SR
    starts = [0.8, 1.2, 1.6, 2.0, 2.4]
    plan_only = {"balance": {"stops": {"end_silent": "plan"}}} if case == "ending-left-to-the-plan" else None
    f = mix_film(film_id=f"stops-{case}", palette=plan_only)
    words = helpers.words_at(starts)
    f.words(words)
    voice = helpers.speech(f.n, words)
    music = helpers.pad(f.n, level_db=-40)
    ending = {"from": round(f.seconds - 1.0, 3), "to": f.seconds, "why": "the film ends in silence"}
    stops = []
    if case not in ("ending-broken", "ending-sounds", "ending-left-to-the-plan"):
        music[f.n - sr:] = 0.0  # a silent last second, planned
        stops = [ending]
    if case.startswith("opening"):
        stops = [{"from": 0.0, "to": 0.6, "why": "the film opens out of silence"}, ending]
        if case == "opening-held":
            music[: int(0.6 * sr)] = 0.0
    if case == "ending-broken":
        stops = [{"from": 3.4, "to": f.seconds, "why": "a fade, then a silent end"}]
        music[int(3.4 * sr):int(3.6 * sr)] = 0.0  # the music stops, but comes back before the end
    score = music.copy()
    if case == "rest-in-score":  # the composer's own rest: a planned beat
        score[int(2.6 * sr):int(3.4 * sr)] = 0.0
        music = score.copy()
    f.score(score, stops=stops)
    if case == "hole-in-mix":  # a hole the mix made (over a second): the score itself plays on there
        music[int(1.0 * sr):int(2.2 * sr)] = 0.0
    f.mix(dialogue=voice, music=music)
    r = B.gate_planned_stops(f.path)
    assert r.status == expect, (r.summary, r.details["checks"])
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    if case.startswith("opening"):
        assert checks["opening"] == expect
    if case in ("ending-broken", "ending-sounds"):
        assert checks["ending"] == "FAIL"  # the music sounds in the last second (ending-sounds: no stop planned at all)
    if case == "ending-left-to-the-plan":
        assert "ending" not in checks  # the brand leaves its ending to the plan, and this plan doesn't end in a stop
    if case == "hole-in-mix":
        assert checks["gap"] == "FAIL" and "opening" not in checks and checks["ending"] == "PASS"
    if case == "rest-in-score":
        assert r.details["score_rests"] and r.details["score_rests"][0]["from"] == 2.6


def test_a_film_without_music_has_no_gaps_but_a_planned_score_must_sound(mix_film, helpers):
    f = mix_film(seconds=4.6)  # the voice ends before the film's silent last second
    words = helpers.words_at(WORD_STARTS)
    f.words(words)
    f.mix(dialogue=helpers.speech(f.n, words))
    assert B.gate_planned_stops(f.path).status == "PASS"
    f.score(helpers.pad(f.n, level_db=-40))
    r = B.gate_planned_stops(f.path)
    assert r.status == "FAIL" and "music stem is silent" in r.summary


def test_the_command(mix_film, helpers, capsys):
    f = _film(mix_film, helpers)
    parser = argparse.ArgumentParser(prog="slate sound")
    sub = parser.add_subparsers(dest="stage")
    B.register(sub)
    from slate import log

    log.set_json(True)
    try:
        args = parser.parse_args(["balance", "clarity", "levels", "--words", "--film", str(f.path), "--json"])
        assert args.handler(args) == 0
        out = json.loads(capsys.readouterr().out)
        assert [g["gate"] for g in out["gates"]] == ["sound.balance.clarity", "sound.balance.levels"]
    finally:
        log.set_json(False)
    for tool in ("Bash", "TodoWrite", "WebFetch", "ToolSearch"):
        assert tool not in B.DESCRIPTION + B.HELP


def test_no_company_material_or_assumed_clock_in_the_new_code():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "slate"
    banned = re.compile(r"truespace|vixa|trudy|\beos\b|inner.?echo", re.I)
    literals = re.compile(r"(?<![\d.])(1920|1080|30000\s*/\s*1001|29\.97)(?![\d])")
    for f in (root / "sound" / "balance.py", root / "qa" / "stability.py"):
        text = f.read_text()
        assert not banned.search(text), f"{f} names company material"
        assert not literals.search(text), f"{f} assumes a frame size or rate"


# ---------------------------------------------------------------------------------------------------------------
# Approved spans (L-sound-S031): the film keeps what the owner approved in a clip


def _clip_of(f, out, *, accents_db: float = 0.0, ride_db: float | None = None):
    """An approved clip of the film's whole span: its family stems as mixed (the accents moved by accents_db) and the
    rides the clip was approved with."""
    from slate.sound import audio

    d = out / "sound" / "mix"
    for p in sorted((f.path / "sound" / "mix" / "family_stems").glob("*.wav")):
        x, _ = audio.read_wav(p)
        if p.stem == "accents":
            x = x * 10 ** (accents_db / 20)
        audio.write_wav(d / "family_stems" / p.name, x, 48000, 24)
    if ride_db is not None:
        jsonio.write_atomic(d / "rides.json", {"rides": [{"hit": "tick", "t": 1.25, "end": 1.37, "family": "accents",
                                                          "depth_db": ride_db}]})
    return out


def test_an_approved_span_is_kept_as_approved_and_a_ride_that_moved_fails(mix_film, helpers, tmp_path):
    span = {"from": 0.0, "to": 4.6, "why": "the owner approved this clip"}
    f = _film(mix_film, helpers, families=True, accent_amp=0.2,
              palette={"balance": {"approved_spans": [{**span, "clip": str(tmp_path / "clip_same")}]}})
    f.rides({"rides": [{"hit": "tick", "t": 1.25, "end": 1.37, "family": "accents", "depth_db": -2.0}]})
    _clip_of(f, tmp_path / "clip_same", ride_db=-2.0)
    r = B.gate_approved_spans(f.path)
    assert r.status == "PASS", (r.summary, r.evidence)
    assert r.details["spans"][0]["rides"] == [{"hit": "tick", "clip_db": -2.0, "film_db": -2.0, "diff_db": 0.0}]
    # the clip the owner approved rode that accent harder (and its accents sat 6 dB lower): the film no longer matches
    g = _film(mix_film, helpers, families=True, accent_amp=0.2,
              palette={"balance": {"approved_spans": [{**span, "clip": str(tmp_path / "clip_rode")}]}})
    g.rides({"rides": [{"hit": "tick", "t": 1.25, "end": 1.37, "family": "accents", "depth_db": -2.0}]})
    _clip_of(g, tmp_path / "clip_rode", accents_db=-6.0, ride_db=-8.0)
    r = B.gate_approved_spans(g.path)
    assert r.status == "FAIL"
    text = " ".join(r.evidence)
    assert "the ride on tick: -2.0 dB in the film against -8.0 in the approved clip" in text
    assert "accents" in text and "approved clip" in text


def test_an_approved_span_without_its_clip_is_unchecked_and_no_spans_pass(mix_film, helpers, tmp_path):
    f = _film(mix_film, helpers, palette={"balance": {"approved_spans": [{"from": 0.5, "to": 2.5}]}})
    r = B.gate_approved_spans(f.path)
    assert r.status == "UNCHECKED" and "names no approved clip" in r.summary
    assert B.gate_approved_spans(_film(mix_film, helpers).path).summary == "no approved spans in this film's palette"


# ---------------------------------------------------------------------------------------------------------------
# The listening file of the hardest-ridden accents (L-sound-S030)


def test_the_listening_file_plays_each_ridden_accent_as_mixed_before_and_now(mix_film, helpers):
    """Each excerpt plays twice: the mix plus exactly what that hit's ride took off (nothing else), then the mix as it
    is. Black with labels here, where the film has no master."""
    from slate import procs
    from slate.sound import audio, listen

    if not procs.which("ffmpeg"):
        pytest.skip("the listening file is made with ffmpeg")
    f = _film(mix_film, helpers, families=True, accent_amp=0.2)
    f.rides({"rides": [{"hit": "tick", "t": 1.25, "end": 1.37, "family": "accents", "depth_db": -7.5,
                        "half_depth": True}]})
    rec = listen.ridden_accents(f.path, size=(320, 180))
    assert rec["rides_heard"] == 1 and len(rec["excerpts"]) == 1 and rec["picture"].startswith("black")
    ex = rec["excerpts"][0]
    assert ex["from"] == 0.0 and ex["to"] == pytest.approx(4.37) and ex["deepest_ride_db"] == -7.5
    parts = f.path / "sound/mix/listen/parts"
    before, _ = audio.read_wav(parts / "01_before.wav")
    now, _ = audio.read_wav(parts / "01_now.wav")
    acc, _ = audio.read_wav(f.path / "sound/mix/family_stems/accents.wav", 0, len(now))
    added = before - now
    a, b = int(1.25 * helpers.SR), int(1.37 * helpers.SR)
    assert np.allclose(added[a:b], acc[a:b] * (10 ** (7.5 / 20) - 1), atol=2e-6)  # the ride undone on that hit...
    assert np.max(np.abs(added[:a])) < 2e-6 and np.max(np.abs(added[b:])) < 2e-6  # ...and nowhere else
    mp4 = f.path / rec["file"]
    from slate.finish import media

    assert mp4.is_file() and media.has_audio(mp4)
    assert abs(media.stream_durations(mp4)["video"] - 2 * 4.37) < 0.2


def test_the_listening_file_says_what_it_needs(mix_film, helpers):
    from slate.cli import run_cli
    from slate.sound import listen

    f = _film(mix_film, helpers, families=False)
    with pytest.raises(MissingPrerequisite, match="Nothing was ridden"):
        listen.ridden_accents(f.path)
    f.rides({"rides": [{"hit": "tick", "t": 1.25, "end": 1.37, "family": "accents", "depth_db": -6.0}]})
    with pytest.raises(MissingPrerequisite, match="family stem"):
        listen.ridden_accents(f.path)
    code, _out, err = run_cli(["mix", "--listen", "--film", str(f.path)])
    assert code == 3 and "family stem" in err
