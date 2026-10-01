"""The balance gates on small mixed films: what passes, what fails, what can't be measured yet (and says so), the
owner's trade waiting for a person, the reports, and the way slate verify calls them."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from slate import gates, jsonio
from slate.errors import MissingPrerequisite
from slate.sound import balance as B

WORD_STARTS = [0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2]


def _film(mix_film, helpers, *, score_db: float = -50.0, bright: bool = False, effects=None, families: bool = False,
          accent_amp: float | None = None, air_amp: float | None = None, starts=WORD_STARTS, **kw):
    f = mix_film(**kw)
    words = helpers.words_at(starts)
    f.words(words)
    voice = helpers.speech(f.n, words)
    music = helpers.pad(f.n, level_db=score_db, bright=bright)
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
    f = mix_film()
    words = helpers.words_at(WORD_STARTS)
    f.words(words)
    voice = helpers.speech(f.n, words)
    music = helpers.pad(f.n, level_db=-50)
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
                                          ("hole-in-mix", "FAIL"), ("rest-in-score", "PASS")])
def test_planned_stops_gate(mix_film, helpers, case, expect):
    sr = helpers.SR
    starts = [0.8, 1.2, 1.6, 2.0, 2.4]
    f = mix_film(film_id=f"stops-{case}")
    words = helpers.words_at(starts)
    f.words(words)
    voice = helpers.speech(f.n, words)
    music = helpers.pad(f.n, level_db=-40)
    stops = []
    if case.startswith("opening"):
        stops = [{"from": 0.0, "to": 0.6, "why": "the film opens out of silence"}]
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
        music[int(2.4 * sr):int(3.6 * sr)] = 0.0
    f.mix(dialogue=voice, music=music)
    r = B.gate_planned_stops(f.path)
    assert r.status == expect, (r.summary, r.details["checks"])
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    if case.startswith("opening"):
        assert checks["opening"] == expect
    if case == "ending-broken":
        assert checks["ending"] == "FAIL"
    if case == "hole-in-mix":
        assert checks["gap"] == "FAIL" and "opening" not in checks and "ending" not in checks
    if case == "rest-in-score":
        assert r.details["score_rests"] and r.details["score_rests"][0]["from"] == 2.6


def test_a_film_without_music_has_no_gaps_but_a_planned_score_must_sound(mix_film, helpers):
    f = mix_film()
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
