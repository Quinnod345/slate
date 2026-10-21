"""The score: plans that follow the playbook, composing through the vendor client, take QA, measured assembly."""

import json
import sys

import numpy as np
import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.errors import MissingPrerequisite, SpendRefused, UsageError
from slate.sound import audio, dsp, measure
from slate.sound.score import assemble, compose, plan, qa

import sound_helpers as H

SR = 48000


# ---------------------------------------------------------------------------------------------------------------
# plan


def test_plan_follows_the_playbook(sound_film):
    p = plan.build(sound_film)
    assert [x["id"] for x in p["parts"]] == ["act1", "act2"]
    a1, a2 = p["parts"]
    assert (a1["join"], a1["landing_s"], a1["hand_over_s"]) == ("start", 0.0, 8.0)
    assert (a2["join"], a2["landing_s"], a2["hand_over_s"]) == ("act", 8.0, 14.0)
    for part in p["parts"]:
        blocks = plan.plan_blocks(part["composition_plan"])
        assert blocks[0] == ("pre-roll", 3000)  # cut away at assembly
        assert blocks[-1][0] == "tail" and blocks[-1][1] >= 3000   # runs past the hand-over
        assert all(ms >= 3000 for _, ms in blocks)
        assert sum(ms for _, ms in blocks) == round(part["seconds"] * 1000)
        assert part["seconds"] < 90
    assert [name for name, _ in plan.plan_blocks(a1["composition_plan"])] == ["pre-roll", "S01", "S02", "tail"]
    assert p["model_id"] == "music_v2_5"  # the registry's best, always passed
    neg = p["styles"]["negative"]
    for word in ("woodblock", "marimba", "bells", "chimes", "kazoo", "paper"):  # effects' materials, banned timbres
        assert word in neg
    pos = p["styles"]["positive"]
    assert "in A minor" in pos and "70-80 BPM" in pos and "soft piano" in pos
    assert jsonio.validate(jsonio.read(sound_film / "sound/score/plan.json"), "score") == []
    assert jsonio.read(sound_film / "sound/score/plans/act1.json") == a1["composition_plan"]
    assert p["licence"]["needs_enterprise"] is False


def _long_film(slate_home, film_id="long", shot_s=20.0, shots=10, fps=(24, 1)):
    H.brand(slate_home["brands"])
    fd = slate_home["films"] / film_id
    (fd / "timing").mkdir(parents=True)
    num, den = fps
    per = int(shot_s * num / den)
    rows = [{"id": f"L{i + 1:02d}", "act": 1, "frame_in": i * per, "frame_out": (i + 1) * per,
             "sec_in": i * per * den / num, "sec_out": (i + 1) * per * den / num} for i in range(shots)]
    jsonio.write_atomic(fd / "timing/shots.json", {"fps_num": num, "fps_den": den, "total_frames": shots * per, "shots": rows})
    (fd / "film.json").write_text(json.dumps({"id": film_id, "fps_num": num, "fps_den": den, "brand": "testbrand",
                                              "format": "brand-film"}))
    return fd


def test_a_long_act_splits_at_shots_into_parts_under_90_seconds(slate_home):
    fd = _long_film(slate_home)
    p = plan.build(fd)
    assert len(p["parts"]) >= 3
    assert all(part["seconds"] < 90 for part in p["parts"])
    assert [x["id"] for x in p["parts"]][:2] == ["act1a", "act1b"]
    bounds = [part["landing_s"] for part in p["parts"]]
    assert all(abs(b / 20.0 - round(b / 20.0)) < 1e-9 for b in bounds)  # splits fall on shot boundaries


def test_short_shots_merge_into_sections_of_at_least_three_seconds(slate_home):
    fd = _long_film(slate_home, "short", shot_s=1.0, shots=11)
    p = plan.build(fd)
    blocks = plan.plan_blocks(p["parts"][0]["composition_plan"])
    assert all(ms >= 3000 for _, ms in blocks)
    assert "L01+L02+L03" in [name for name, _ in blocks]


def test_v2_models_get_chunk_plans_never_v1_sections(sound_film):
    """music_v2 and music_v2_5 reject v1 section plans: the plan is chunks, with the brand's global styles on every
    chunk (the first chunk sets the tone) and nothing but the [name] in each chunk's text."""
    from slate.vendors import elevenlabs

    p = plan.build(sound_film)
    assert p["plan_schema"] == "chunks" and elevenlabs.music_plan_schema(p["model_id"]) == "chunks"
    for part in p["parts"]:
        comp = part["composition_plan"]
        assert set(comp) == {"chunks"}  # no sections, no global styles: v2 has neither
        assert elevenlabs.plan_problems(comp, p["model_id"]) == []
        for chunk in comp["chunks"]:
            assert chunk["text"].startswith("[") and chunk["text"].endswith("]") and "\n" not in chunk["text"]
            assert 3000 <= chunk["duration_ms"] <= 120000
            for style in ("in A minor", "70-80 BPM", "soft piano", "instrumental"):
                assert style in chunk["positive_styles"]
            for style in ("woodblock", "marimba", "vocals"):
                assert style in chunk["negative_styles"]
        assert [b["name"] for b in part["blocks"]] == [n for n, _ in plan.plan_blocks(comp)]
    assert jsonio.read(sound_film / "sound/score/plans/act1.json") == p["parts"][0]["composition_plan"]
    assert jsonio.validate(jsonio.read(sound_film / "sound/score/plan.json"), "score") == []


def test_a_v1_model_still_gets_its_section_plan(sound_film):
    data = json.loads((sound_film / "film.json").read_text())
    data["models"] = {"music": "music_v1"}
    (sound_film / "film.json").write_text(json.dumps(data))
    p = plan.build(sound_film)
    assert p["model_id"] == "music_v1" and p["plan_schema"] == "sections"
    comp = p["parts"][0]["composition_plan"]
    assert set(comp) == {"positive_global_styles", "negative_global_styles", "sections"}
    assert "in A minor" in comp["positive_global_styles"] and all(s["lines"] == [] for s in comp["sections"])
    assert jsonio.validate(jsonio.read(sound_film / "sound/score/plan.json"), "score") == []


def test_a_long_run_of_short_shots_keeps_names_within_the_limit(slate_home):
    fd = _long_film(slate_home, "many", shot_s=0.1, shots=90)  # 2-frame shots: 36 of them to make 3 s
    p = plan.build(fd)
    names = [name for part in p["parts"] for name, _ in plan.plan_blocks(part["composition_plan"])]
    assert all(1 <= len(name) <= 100 for name in names)
    assert any("…" in name and "shots)" in name for name in names)  # shortened, still saying what it spans


def test_broadcast_formats_flag_the_enterprise_licence(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "tv", extra={"broadcast": True})
    p = plan.build(fd)
    assert p["licence"]["broadcast"] and p["licence"]["needs_enterprise"]
    code, out, _ = run_cli(["score", "plan", "--film", str(fd)])
    assert code == 0 and "BROADCAST" in out


def test_directions_land_parts_on_beats_words_and_held_breaths(sound_film):
    jsonio.write_atomic(sound_film / "timing/visual_events/act2.json",
                        [{"shot": "S03", "id": "the_landing", "t": 9.2, "kind": "impact", "size": "l", "note": "lands"}])
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act1": {"end": "stop", "stop_before_s": 0.5},
                                   "act2": {"join": "bloom", "landing_event": "the_landing"}}})
    p = plan.build(sound_film)
    a1, a2 = p["parts"]
    assert a2["landing_s"] == 9.2 and a2["join"] == "bloom" and a1["hand_over_s"] == 9.2
    assert a1["end"] == "stop" and a1["tail_s"] == 4.5  # built to stay at level past its cut
    assert p["planned_stops"][0]["from"] == pytest.approx(8.7) and p["planned_stops"][0]["to"] == pytest.approx(9.2)
    words = jsonio.read(sound_film / "timing/vo_words.json")["shots"]["S03"]["words"]
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act2": {"join": "lift", "landing_word": {"shot": "S03", "word": words[1]["w"]}}}})
    p = plan.build(sound_film)
    assert p["parts"][1]["landing_s"] == pytest.approx(8.0 + 0.3 + words[1]["start"], abs=1e-6)


def test_an_unknown_landing_is_a_usage_error(sound_film):
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act2": {"join": "bloom", "landing_event": "nowhere"}}})
    with pytest.raises(UsageError):
        plan.build(sound_film)


# ---------------------------------------------------------------------------------------------------------------
# compose


class FakeMusic:
    """Stands in for the ElevenLabs client: records every call and writes an Eleven-Music-like take (fades at both
    ends whatever the plan says) of exactly the plan's length."""

    def __init__(self, maker=None):
        self.calls = []
        self.maker = maker

    def compose_music(self, *, composition_plan=None, prompt=None, model_id=None, seed=None, output_format=None,
                      respect_sections_durations=None, out=None, note=None):
        self.calls.append({"model_id": model_id, "seed": seed, "respect": respect_sections_durations, "note": note,
                           "shape": "chunks" if "chunks" in composition_plan else "sections"})
        secs = sum(ms for _, ms in plan.plan_blocks(composition_plan)) / 1000
        y = self.maker(secs, seed, note) if self.maker else H.pad(secs, seed=seed or 1, fade_in=1.5, fade_out=2.5)
        audio.write_wav(out, y, SR)
        return {"file": str(out), "request_id": f"req_{seed}", "replay": True, "format": "pcm_48000",
                "character_cost": None}


@pytest.fixture
def fake_music(monkeypatch):
    fake = FakeMusic()
    monkeypatch.setattr(compose, "client", lambda film: fake)
    return fake


def test_compose_always_passes_the_model_and_keeps_what_it_has(sound_film, fake_music):
    plan.build(sound_film)
    res = compose.compose(sound_film)
    assert len(res["composed"]) == 4 and res["replay"]
    assert {c["model_id"] for c in fake_music.calls} == {"music_v2_5"}
    assert {c["seed"] for c in fake_music.calls} == {7, 21}
    # chunk plans for music_v2_5, and respect_sections_durations (a music_v1 option) never sent with them
    assert all(c["shape"] == "chunks" and c["respect"] is None for c in fake_music.calls)
    rec = jsonio.read(sound_film / "sound/score/takes/act1_s7.json")
    assert jsonio.validate(rec, "score") == [] and rec["request_id"] == "req_7"
    assert (sound_film / "sound/score/takes/act1_s7.plan.json").exists()
    again = compose.compose(sound_film)
    assert again["composed"] == [] and len(again["kept"]) == 4 and len(fake_music.calls) == 4  # nothing paid twice


def test_compose_dry_run_estimates_and_spends_nothing(sound_film, fake_music):
    plan.build(sound_film)
    res = compose.compose(sound_film, dry_run=True)
    assert res["estimate"]["amount"] > 0 and res["estimate"]["unit"] == "credits"
    assert fake_music.calls == [] and not list((sound_film / "sound/score").glob("takes/*.wav"))


def test_compose_refuses_a_batch_over_budget_before_calling(sound_film, fake_music, monkeypatch):
    plan.build(sound_film)
    data = json.loads((sound_film / "film.json").read_text())
    data["budget"] = {"elevenlabs": 10}
    (sound_film / "film.json").write_text(json.dumps(data))
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")  # budgets only bite on real spend; the fake client is never reached
    with pytest.raises(SpendRefused):
        compose.compose(sound_film)
    assert fake_music.calls == []
    code, out, _ = run_cli(["score", "compose", "--film", str(sound_film), "--json"])
    assert code == 4 and json.loads(out)["error"] == "spend_refused"


def test_compose_refuses_a_plan_built_for_another_models_shape(sound_film, fake_music):
    """A plan.json made with v1 sections, then sent on a v2 model, is refused before anything is sent (exit 3)."""
    plan.build(sound_film)
    path = sound_film / "sound/score/plan.json"
    old = jsonio.read(path)
    for part in old["parts"]:
        part["composition_plan"] = {"positive_global_styles": ["warm"], "negative_global_styles": ["kazoo"],
                                    "sections": [{"section_name": "one", "positive_local_styles": [],
                                                  "negative_local_styles": [], "duration_ms": 8000, "lines": []}]}
    jsonio.write_atomic(path, old)
    with pytest.raises(MissingPrerequisite) as exc:
        compose.compose(sound_film)
    assert "slate score plan" in exc.value.hint and fake_music.calls == []
    code, out, _ = run_cli(["score", "compose", "--film", str(sound_film), "--json"])
    assert code == 3 and json.loads(out)["error"] == "missing_prerequisite"


def test_compose_sends_a_v1_section_plan_with_respect_for_its_durations(sound_film, fake_music):
    data = json.loads((sound_film / "film.json").read_text())
    data["models"] = {"music": "music_v1"}
    (sound_film / "film.json").write_text(json.dumps(data))
    plan.build(sound_film)
    compose.compose(sound_film, ["act1"], [7])
    (call,) = fake_music.calls
    assert call["model_id"] == "music_v1" and call["shape"] == "sections" and call["respect"] is True


def test_compose_says_what_is_missing_without_the_vendor_client(sound_film, monkeypatch):
    plan.build(sound_film)
    monkeypatch.setitem(sys.modules, "slate.vendors.elevenlabs", None)
    with pytest.raises(MissingPrerequisite):
        compose.compose(sound_film)


def test_compose_through_the_real_client_in_replay(sound_film):
    """The ElevenLabs client in replay mode: this is the call a live run makes, answered from its faker."""
    pytest.importorskip("slate.vendors.elevenlabs")
    plan.build(sound_film)
    res = compose.compose(sound_film, ["act1"], [7])
    rec = res["composed"][0]
    take = sound_film / rec["file"]
    assert rec["replay"] and rec["model_id"] == "music_v2_5" and audio.is_wav(take)
    sent = jsonio.read(sound_film / rec["plan_file"])
    assert set(sent) == {"chunks"}  # what went to the model: a chunk plan
    assert abs(audio.wav_info(take).seconds - rec["seconds_planned"]) < 0.05
    if pytest.importorskip("slate.state.pins"):
        pinned = json.loads((sound_film / "film.json").read_text())["pins"].get("music")
        assert pinned == "music_v2_5" or (isinstance(pinned, dict) and pinned.get("value") == "music_v2_5")


def test_a_broadcast_format_flags_it_too(sound_film, monkeypatch):
    from slate.sound import film as sfilm

    monkeypatch.setattr(sfilm, "load_format", lambda name: {"name": name, "broadcast": True})
    assert plan.build(sound_film)["licence"]["needs_enterprise"] is True


# ---------------------------------------------------------------------------------------------------------------
# take QA


def test_qa_flags_silence_and_noise_but_not_the_edges():
    clean = H.pad(12.0, fade_in=0.8, fade_out=0.8)
    assert qa.analyse(clean, SR)["problems"] == []
    holey = clean.copy()
    holey[int(5 * SR):int(7 * SR)] = 0.0
    rep = qa.analyse(holey, SR)
    assert rep["problems"] == [{"kind": "silence", "from": 5.0, "to": 7.0}] and rep["score"] == 2.0
    noisy = clean.copy()
    noisy[int(4 * SR):int(6.5 * SR)] = 0.1 * np.random.default_rng(1).standard_normal((int(2.5 * SR), 2))
    assert [p["kind"] for p in qa.analyse(noisy, SR)["problems"]] == ["noise"]
    edge = clean.copy()
    edge[: int(0.9 * SR)] = 0.0  # a take's own fade-in is fine
    assert qa.analyse(edge, SR)["problems"] == []


# ---------------------------------------------------------------------------------------------------------------
# assemble


def _takes(fd, maker):
    fake = FakeMusic(maker)
    compose.client = lambda film: fake  # restored by the fixture below
    return fake


@pytest.fixture
def restore_client():
    original = compose.client
    yield
    compose.client = original


def _level(x, a, b):
    seg = x[int(a * SR):int(b * SR)]
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-12)


def test_assembly_places_joins_and_ends_on_the_last_frame(sound_film, restore_client):
    def maker(secs, seed, note):
        take = H.pad(secs, seed=seed, fade_in=1.5, fade_out=2.5, notes=(146.83, 220.0, 293.66) if "act1" in note
                     else (196.0, 246.94, 392.0))
        if note.startswith("score act1") and seed == 7:
            take[int(6 * SR):int(8 * SR)] = 0.0  # a take with a hole: QA marks it down, the other seed wins
        return take

    plan.build(sound_film)
    _takes(sound_film, maker)
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    assert rec["pass"], assemble.describe(rec)
    assert rec["parts"]["act1"]["file"].endswith("act1_s21.wav")  # the clean take
    assert rec["parts"]["act1"]["alternatives"][0]["qa_score"] == 2.0
    score = H.read(sound_film / "sound/score/score.wav")
    assert len(score) == 14 * SR
    body = _level(score, 2.0, 6.0)
    assert body - _level(score, 0.0, 0.5) < 3.0  # the pre-roll was cut: at level on frame one
    lv = [_level(score, t, t + 0.05) for t in np.arange(7.0, 9.0, 0.05)]
    assert max(lv) - min(lv) < 2.5  # the act join is equal power: no dip between the two takes
    assert np.max(np.abs(score[-10:])) < 0.01 and _level(score, 13.9, 14.0) < body - 12  # silence on the final frame
    assert rec["music_gap_check"]["pass"] and rec["assembled_qa"]["problems"] == []
    assert jsonio.validate(jsonio.read(sound_film / "sound/score/score.json"), "score") == []


def test_a_take_that_runs_out_is_held_with_its_own_chord(sound_film, restore_client):
    def maker(secs, seed, note):
        take = H.pad(secs, seed=seed, fade_in=1.5, fade_out=2.5)
        if "act1" in note:
            take[int(9.0 * SR):] = 0.0  # every act1 take runs out 2 s before its hand-over (film 6.0 of 8.0)
        return take

    plan.build(sound_film)
    _takes(sound_film, maker)
    compose.compose(sound_film)
    bare = assemble.assemble(sound_film, repairs=False)
    assert not bare["music_gap_check"]["pass"]  # the hole is real
    code, _, _ = run_cli(["score", "assemble", "--no-repairs", "--film", str(sound_film)])
    assert code == 1
    rec = assemble.assemble(sound_film)
    assert rec["music_gap_check"]["pass"], rec["music_gap_check"]
    assert any("held" in r and "freeze" in r for r in rec["parts"]["act1"]["repairs"])
    score = H.read(sound_film / "sound/score/score.wav")
    assert _level(score, 6.2, 7.2) > _level(score, 2.0, 5.0) - 8.0  # the hold carries it at about its own level


def test_a_late_take_is_carried_over_until_it_is_in(sound_film, restore_client):
    def maker(secs, seed, note):
        take = H.pad(secs, seed=seed, fade_in=1.5, fade_out=2.5)
        if "act2" in note:
            take[: int(4.6 * SR)] = 0.0  # enters 1.6 s after its pre-roll
            k = int(0.3 * SR)
            take[int(4.6 * SR):int(4.6 * SR) + k] *= np.linspace(0, 1, k)[:, None]
        return take

    plan.build(sound_film)
    _takes(sound_film, maker)
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    assert rec["music_gap_check"]["pass"]
    assert any("carries on" in r for r in rec["parts"]["act1"]["repairs"])
    assert rec["parts"]["act2"]["fade_in"][0] > 9.5


def test_a_stop_is_a_planned_silence_and_the_next_part_enters_on_its_landing(sound_film, restore_client):
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act1": {"end": "stop", "stop_before_s": 0.5}}})
    plan.build(sound_film)
    _takes(sound_film, lambda secs, seed, note: H.pad(secs, seed=seed, fade_in=1.5, fade_out=2.5))
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    assert rec["planned_stops"][0]["from"] == pytest.approx(7.5) and rec["music_gap_check"]["pass"]
    score = H.read(sound_film / "sound/score/score.wav")
    assert np.max(np.abs(score[int(7.52 * SR):int(7.99 * SR)])) < 1e-6  # the held breath is silent
    assert _level(score, 8.35, 8.6) > _level(score, 2.0, 5.0) - 3.0  # back at level just after the landing


def test_a_bloom_lands_within_a_frame_of_its_beat(sound_film, restore_client):
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act1": {"end": "stop", "stop_before_s": 0.5},
                                   "act2": {"join": "bloom", "landing_s": 8.4}}})

    def maker(secs, seed, note):
        if "act1" in note:
            return H.pad(secs, seed=seed, fade_in=1.5, fade_out=2.5)
        soft = H.pad(3.0, seed=seed, level_db=-40.0, fade_in=1.0)
        bloom = H.pad(secs - 3.0, seed=seed + 1, notes=(196.0, 293.66, 369.99, 440.0), level_db=-20.0, fade_out=2.5)
        k = int(0.004 * SR)
        bloom[:k] *= np.linspace(0, 1, k)[:, None]  # struck on its section boundary
        return np.concatenate([soft, bloom])

    plan.build(sound_film)
    _takes(sound_film, maker)
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    land = rec["landings"][0]
    assert land["part"] == "act2" and land["within_frame"], land
    assert abs(land["delta_ms"]) <= 40.0  # a frame at 25 fps


def test_assembly_needs_takes_first(sound_film):
    plan.build(sound_film)
    with pytest.raises(MissingPrerequisite):
        assemble.assemble(sound_film)
    code, out, _ = run_cli(["score", "assemble", "--film", str(sound_film), "--json"])
    assert code == 3 and "slate score compose" in json.loads(out)["hint"]


def test_the_whole_score_chain_runs_from_the_cli_in_replay(sound_film):
    """plan -> compose (the real client, replayed) -> assemble -> qa -> mix: what `slate demo` and the first reel do."""
    pytest.importorskip("slate.vendors.elevenlabs")
    for argv in (["score", "plan"], ["score", "compose"], ["score", "assemble"], ["score", "qa"], ["mix"]):
        code, out, err = run_cli([*argv, "--film", str(sound_film), "--json"])
        assert code == 0, (argv, out, err)
    rec = jsonio.read(sound_film / "sound/score/score.json")
    assert rec["pass"] and len(rec["parts"]) == 2
    got = measure.loudness(sound_film / "sound/mix/final_mix.wav")
    assert abs(got["integrated_lufs"] + 14.0) <= 0.5 and got["true_peak_dbtp"] <= -1.0
    assert dsp.db(0.0) == 1.0


# ---------------------------------------------------------------------------------------------------------------
# Big moments together (L-sound-S026), the joins measured (L-sound-062), the silent last second (L-sound-S022)


def _steady(secs, seed, note):
    """Steady takes (a held tone whose 5 ms level doesn't ripple), so a rise is the assembler's own doing."""
    t = np.arange(int(round(secs * SR))) / SR
    f = 200.0 if "act1" in note else 400.0  # periods that fit a 5 ms window whole
    y = 0.05 * np.sin(2 * np.pi * f * t + seed)
    return np.stack([y, y], 1)


def test_a_big_moment_holds_its_breath_and_comes_back_on_the_hit(sound_film, restore_client):
    """The films' keystone: the score fades out over 80 ms into a short silence and comes back exactly on the hit,
    3 dB over its own level and settling over 2 s, its steepest rise ending 20-30 ms after the hit."""
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act2": {"join": "bloom", "landing_s": 8.4, "together": True}}})
    p = plan.build(sound_film)
    assert p["parts"][1]["together"] and p["parts"][1]["breath_s"] == 0.35
    assert {"from": 8.05, "to": 8.4} in [{k: s[k] for k in ("from", "to")} for s in p["planned_stops"]]
    _takes(sound_film, _steady)
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    land = next(lv for lv in rec["landings"] if lv["part"] == "act2")
    assert land["together"] and 20.0 <= land["steepest_rise_end_ms"] <= 30.0, land
    score = H.read(sound_film / "sound/score/score.wav")
    assert np.max(np.abs(score[int(8.06 * SR):int(8.399 * SR)])) == 0.0  # the breath is silence
    assert _level(score, 7.80, 7.95) - _level(score, 8.040, 8.049) > 9.0  # the end of the 80 ms fade into it
    lifted, settled = _level(score, 8.45, 8.55), _level(score, 10.6, 11.0)
    assert 2.5 <= lifted - settled <= 3.1  # 3 dB over its own level on the hit, settled 2 s on


def test_a_big_moment_inside_a_part_recuts_the_music_already_there(sound_film, restore_client):
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"moments": [{"at": 5.0, "why": "the stress hit"}]})
    p = plan.build(sound_film)
    assert p["moments"][0]["t"] == 5.0 and any(s["from"] == 4.65 and s["to"] == 5.0 for s in p["planned_stops"])
    _takes(sound_film, _steady)
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    assert rec["pass"], assemble.describe(rec)
    assert 20.0 <= rec["moments"][0]["steepest_rise_end_ms"] <= 30.0
    score = H.read(sound_film / "sound/score/score.wav")
    assert np.max(np.abs(score[int(4.66 * SR):int(4.999 * SR)])) == 0.0
    assert _level(score, 5.05, 5.15) - _level(score, 7.2, 7.6) == pytest.approx(3.0, abs=0.4)


def test_an_act_change_that_breathes_too_deep_for_too_long_fails_the_assembly(sound_film, restore_client):
    """An act change breathes at most 7 dB, for a quarter second at most (the films' rule). This one dips 9 dB for
    0.4 s across its crossfade."""
    def maker(secs, seed, note):
        y = H.pad(secs, seed=seed, notes=(146.83, 220.0, 293.66) if "act1" in note else (196.0, 246.94, 392.0))
        a, b = (10.8, 11.2) if "act1" in note else (2.8, 3.2)  # film 7.8-8.2 s in each take's own time
        y[int(a * SR):int(b * SR)] *= 10 ** (-9 / 20)
        return y

    plan.build(sound_film)
    _takes(sound_film, maker)
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    join = rec["joins"][0]
    assert join["part"] == "act2" and join["judged"] and not join["ok"], join
    assert join["dip_db"] > 7.0 and join["dip_s"] > 0.25
    assert not rec["pass"] and "PAST THE LIMIT" in assemble.describe(rec)


def test_a_clean_join_passes_and_the_score_ends_in_a_silent_last_second(sound_film, restore_client):
    plan.build(sound_film)
    _takes(sound_film, lambda secs, seed, note: H.pad(secs, seed=seed))
    compose.compose(sound_film)
    rec = assemble.assemble(sound_film)
    assert rec["pass"] and rec["joins"][0]["ok"] and rec["joins"][0]["dip_db"] <= 3.0
    assert rec["planned_stops"][-1] == {"from": 13.0, "to": 14.0, "why": "the film ends in silence: its last second, always"}
    score = H.read(sound_film / "sound/score/score.wav")
    assert np.max(np.abs(score[13 * SR:])) == 0.0 and _level(score, 11.0, 11.4) > _level(score, 2.0, 6.0) - 6.0


def test_a_brand_that_leaves_its_ending_to_the_plan_keeps_the_music_to_the_last_frame(slate_home, restore_client):
    H.brand(slate_home["brands"], palette={"balance": {"stops": {"end_silent": "plan"}}})
    fd = H.film(slate_home["films"])
    p = plan.build(fd)
    assert not p["planned_stops"]
    _takes(fd, lambda secs, seed, note: H.pad(secs, seed=seed))
    compose.compose(fd)
    assemble.assemble(fd)
    score = H.read(fd / "sound/score/score.wav")
    assert _level(score, 13.0, 13.5) > _level(score, 2.0, 6.0) - 12.0 and np.max(np.abs(score[-10:])) < 0.01


def test_a_landing_names_its_shot_when_two_events_share_an_id(sound_film):
    """A film's two keystones share the id: "S04:keystone" lands on the later one; a bare id is still the first."""
    jsonio.write_atomic(sound_film / "timing/visual_events/act2.json",
                        [{"shot": "S03", "id": "keystone", "t": 9.2, "kind": "impact", "size": "l", "note": "lands"},
                         {"shot": "S04", "id": "keystone", "t": 13.1, "kind": "impact", "size": "l", "note": "again"}])
    jsonio.write_atomic(sound_film / "sound/score/directions.json",
                        {"parts": {"act2": {"join": "bloom", "landing_event": "keystone"}},
                         "moments": [{"event": "S04:keystone", "why": "the second keystone"}]})
    p = plan.build(sound_film)
    assert p["parts"][1]["landing_s"] == 9.2
    assert p["moments"][0]["t"] == 13.1
