"""The sound gates, each proven both ways: it passes what it should, and it catches what it exists to catch."""

import json

import numpy as np
import pytest

from slate import gates, jsonio
from slate.cli import run_cli
from slate.errors import MissingPrerequisite
from slate.sound import audio, checks, dsp, mix, verify

import sound_helpers as H

SR = 48000


def _by_name(results):
    return {r.gate: r for r in results}


def _approve_reference(brands_dir, levels, name="testbrand"):
    d = brands_dir / name
    ref = audio.write_wav(d / "sound_reference.wav", H.pad(12.0), SR)
    data = json.loads((d / "brand.json").read_text())
    data["references"]["sound"] = {"file": "sound_reference.wav", "sha256": jsonio.sha256(ref),
                                   "approved_by": "A. Person", "approved_at": "2026-10-01T10:00:00+00:00"}
    data["sound"]["reference_levels"] = levels
    (d / "brand.json").write_text(json.dumps(data, indent=1))


def test_every_gate_passes_on_a_good_mix(slate_home, sound_film):
    mix.run(sound_film)
    rep = jsonio.read(sound_film / "sound/mix/final_mix.json")
    _approve_reference(slate_home["brands"], {"music": rep["levels_vs_voice_lu"]["music"]})
    every = _by_name(verify.run(sound_film))
    # `slate verify` runs the core sound gates and every gate merged in from slate.sound.balance (its own tests prove
    # those); here every core gate passes, and the run reports exactly what it ran
    assert set(every) == set(verify.gate_names())
    res = {k: r for k, r in every.items() if k in verify.GATE_NAMES}
    assert set(res) == set(verify.GATE_NAMES)
    bad = {k: (r.status, r.summary) for k, r in res.items() if r.status != "PASS"}
    assert not bad, bad
    assert gates.overall(res.values()) == "PASS"
    report = jsonio.read(sound_film / "qa/gates.json")
    assert set(verify.gate_names()) <= set(report["gates"])
    assert jsonio.read(sound_film / "sound/mix/verify.json")["summary"]["status"] == gates.overall(every.values())
    lr = res["sound.loudness"]
    assert lr.engine["name"] == "slate bs1770-4" and lr.inputs  # evidence names its meter and the file it measured
    code, out, _ = run_cli(["verify", "--film", str(sound_film), "--json", *verify.GATE_NAMES])
    assert code == 0 and json.loads(out)["summary"]["status"] == "PASS"


def test_merged_gates_run_in_verify_and_a_broken_one_is_unchecked(sound_film, monkeypatch):
    """slate.sound.balance's gates join `slate verify` once that module is installed (an import guard otherwise), and
    a merged gate that crashes is UNCHECKED, never a crashed verify or a pass."""
    import sys
    import types

    mix.run(sound_film)

    def boom(ctx):
        raise RuntimeError("a bug in the merged gate")

    def fine(film_dir):
        return gates.GateResult.pass_("sound.extra_ok", f"read {film_dir.name}", engine=gates.engine("fixture", "1"))

    fake = types.ModuleType("slate.sound.balance")
    fake.GATES = {"sound.extra_boom": boom, "extra_ok": fine, "sound.loudness": boom}  # a core name is never replaced
    monkeypatch.setitem(sys.modules, "slate.sound.balance", fake)
    res = _by_name(verify.run(sound_film, write=False))
    assert res["sound.extra_boom"].status == "UNCHECKED" and "a bug in the merged gate" in res["sound.extra_boom"].summary
    assert res["sound.extra_ok"].status == "PASS" and res["sound.extra_ok"].summary == f"read {sound_film.name}"
    assert res["sound.loudness"].engine["name"] == "slate bs1770-4"  # still the core gate
    assert verify.EXTERNAL_STATUS["slate.sound.balance"] == "merged: sound.extra_boom, sound.extra_ok"
    assert verify.gate_names()[-2:] == ["sound.extra_boom", "sound.extra_ok"]
    monkeypatch.setitem(sys.modules, "slate.sound.balance", None)  # not installed: the core gates alone
    assert set(_by_name(verify.run(sound_film, write=False))) == set(verify.GATE_NAMES)
    assert verify.EXTERNAL_STATUS["slate.sound.balance"] == "not installed yet"


def test_reference_is_unchecked_until_onboarding_approves_one(sound_film):
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film))["sound.reference"]
    assert r.status == "UNCHECKED" and "onboard" in r.summary
    code, _, _ = run_cli(["verify", "--film", str(sound_film)])
    assert code == 1  # unchecked is never passed


def test_reference_levels_catch_a_different_balance(slate_home, sound_film):
    mix.run(sound_film)
    rep = jsonio.read(sound_film / "sound/mix/final_mix.json")
    _approve_reference(slate_home["brands"], {"music": rep["levels_vs_voice_lu"]["music"] + 6.0})
    r = _by_name(verify.run(sound_film, ["sound.reference"]))["sound.reference"]
    assert r.status == "FAIL" and "music" in r.details["off"]


def test_loudness_and_stems_sum_fail_when_the_mix_is_touched(sound_film):
    mix.run(sound_film)
    p = sound_film / "sound/mix/final_mix.wav"
    audio.write_wav(p, H.read(p) * 10 ** (-3 / 20), SR)
    res = _by_name(verify.run(sound_film, ["sound.loudness", "sound.stems_sum"]))
    assert res["sound.loudness"].status == "FAIL" and "-17" in res["sound.loudness"].summary
    assert res["sound.stems_sum"].status == "FAIL"


def test_gates_go_stale_when_the_mix_changes(sound_film):
    mix.run(sound_film)
    verify.run(sound_film, ["sound.loudness"])
    p = sound_film / "sound/mix/final_mix.wav"
    audio.write_wav(p, H.read(p) * 0.99, SR)
    assert gates.load_report(sound_film)["sound.loudness"].status == "STALE"


def test_gap_catches_a_hole_unless_it_is_planned(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "holey")
    score = H.pad(14.0)
    score[int(6.0 * SR):int(7.5 * SR)] = 0.0
    audio.write_wav(fd / "sound/score/score.wav", score, SR)
    mix.run(fd)
    r = _by_name(verify.run(fd, ["sound.gap"]))["sound.gap"]
    assert r.status == "FAIL" and r.details["failures"][0]["t"] == 6.0
    jsonio.write_atomic(fd / "sound/score/directions.json",
                        {"planned_stops": [{"from": 5.9, "to": 7.6, "why": "a held breath"}]})
    mix.run(fd)
    assert _by_name(verify.run(fd, ["sound.gap"]))["sound.gap"].status == "PASS"


def test_a_fade_into_a_planned_silence_is_part_of_the_silence(slate_home):
    """The owner approved the music fading into a planned stop as part of the stop (2026-09-30): the gap gate widens
    each stop by the music's own fade, as sound.planned_stops does, and still fails a hole anywhere else."""
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "fading")
    score = H.pad(14.0)
    t = np.arange(len(score)) / SR
    fade = np.clip((7.0 - t) / 1.2, 0.0, 1.0) ** 3  # a 1.2 s fade, mostly far under the floor, into the stop at 7 s
    score = score * np.where(t < 7.0, fade, 0.0)[:, None] + score * (t >= 8.5)[:, None]
    audio.write_wav(fd / "sound/score/score.wav", score, SR)
    jsonio.write_atomic(fd / "sound/score/directions.json",
                        {"planned_stops": [{"from": 7.0, "to": 8.5, "why": "a held breath"}]})
    mix.run(fd)
    r = _by_name(verify.run(fd, ["sound.gap"]))["sound.gap"]
    assert r.status == "PASS", (r.summary, r.details.get("failures"))
    score[int(3.0 * SR):int(4.0 * SR)] = 0.0  # a hole nobody planned
    audio.write_wav(fd / "sound/score/score.wav", score, SR)
    mix.run(fd)
    r = _by_name(verify.run(fd, ["sound.gap"]))["sound.gap"]
    assert r.status == "FAIL" and r.details["failures"][0]["t"] == 3.0


def test_voice_sync_catches_a_take_ten_ms_off(sound_film):
    mix.run(sound_film)
    cues = jsonio.read(sound_film / "timing/vo_cues.json")
    cues["cues"][1]["at"] += 0.010  # the cue now says 10 ms later than where the mix put the take
    jsonio.write_atomic(sound_film / "timing/vo_cues.json", cues)
    r = _by_name(verify.run(sound_film, ["sound.voice_sync"]))["sound.voice_sync"]
    assert r.status == "FAIL" and r.details["off"][0]["shot"] == "S02"
    assert abs(r.details["off"][0]["offset_ms"] + 10.0) < 0.05


# ---------------------------------------------------------------------------------------------------------------
# Effect cues: anchors, overlapping notes, repeats


def _note(seed, pitch, level_db=0.0, dur=0.6):
    t = np.arange(int(dur * SR)) / SR
    y = np.sin(2 * np.pi * pitch * t) * np.exp(-t / 0.25) * (1 - np.exp(-t / 0.002))
    y *= 10 ** (level_db / 20) * 0.5
    return np.stack([y, y], 1)


def _write(fd, rel, x):
    audio.write_wav(fd / rel, x, SR)
    return rel


def _sheet(fd, cues, **extra):
    jsonio.write_atomic(fd / "sound/sound_cues.json", {"film": fd.name, "cues": cues, **extra})
    assert jsonio.validate(jsonio.read(fd / "sound/sound_cues.json"), "sound_cues") == []


def test_overlapping_notes_are_each_measured_on_their_own(sound_film):
    """The films' false sync failure: a gesture's first note sat 4 dB (here 14 dB) under the notes that overlapped it,
    so measured on the summed gesture the 12 dB onset was first crossed on a later note (C's +107.7 ms). Each attack is
    measured on its own now."""
    mix.run(sound_film)
    beats = [5.0, 5.05, 5.10, 5.15]
    levels = [-14.0, 0.0, 0.0, 0.0]
    pitches = [587.33, 739.99, 880.0, 1174.66]
    notes, gesture = [], np.zeros((int(1.0 * SR), 2))
    for i, (b, lv, p) in enumerate(zip(beats, levels, pitches)):
        y = _note(i, p, lv)
        notes.append({"t": b, "beat": b, "file": _write(sound_film, f"sound/cues/motif_{i}.wav", y)})
        k = int(round((b - beats[0]) * SR))
        gesture[k:k + len(y)] += y[: len(gesture) - k]
    # the old measurement, on the summed gesture: its onset lands on a later note, more than a frame late
    late = verify.anchor_offset(gesture, "onset", SR) / SR
    assert late > 0.040  # a frame at this film's 25 fps
    _sheet(sound_film, [{"id": "motif1", "kind": "character", "sound": "felt notes", "stem": "tonal", "anchor": "onset",
                         "notes": notes}])
    r = _by_name(verify.run(sound_film, ["sound.cue_sync"]))["sound.cue_sync"]
    assert r.status == "PASS" and r.details["anchors"] == 4 and r.details["max_abs_delta_ms"] < 5


def test_a_late_hit_fails_sync(sound_film):
    mix.run(sound_film)
    late_hit = np.concatenate([np.zeros((int(0.06 * SR), 2)), H.wood_hit(1)])
    _sheet(sound_film, [{"id": "tap1", "kind": "tap", "sound": "wood block", "stem": "ui", "t": 3.0, "beat": 3.0,
                         "anchor": "onset", "file": _write(sound_film, "sound/cues/tap1.wav", late_hit)}])
    r = _by_name(verify.run(sound_film, ["sound.cue_sync"]))["sound.cue_sync"]
    assert r.status == "FAIL" and abs(r.details["over_one_frame"][0]["delta_ms"] - 60.0) < 2.0


def test_air_is_anchored_on_its_crest(sound_film):
    mix.run(sound_film)
    rng = np.random.default_rng(3)
    k = int(0.8 * SR)
    whoosh = rng.standard_normal((k, 2)) * np.hanning(k)[:, None] * 0.2  # crest in the middle
    _sheet(sound_film, [{"id": "w1", "kind": "whoosh", "sound": "air", "stem": "fx", "t": 2.0, "beat": 2.4,
                         "anchor": "peak20", "file": _write(sound_film, "sound/cues/w1.wav", whoosh)}])
    r = _by_name(verify.run(sound_film, ["sound.cue_sync"]))["sound.cue_sync"]
    assert r.status == "PASS", r.details


def test_cues_without_their_own_audio_are_unchecked_while_the_engine_waits(sound_film):
    mix.run(sound_film)
    _sheet(sound_film, [{"id": "p1", "kind": "tap", "sound": "wood block", "stem": "ui", "t": 1.0, "beat": 1.0,
                         "anchor": "onset", "params": {"pitch": "D6", "seed": 3}}])
    r = _by_name(verify.run(sound_film, ["sound.cue_sync"]))["sound.cue_sync"]
    assert r.status == "UNCHECKED" and "proof 3" in r.summary


def _taps(fd, files, kinds=None, sounds=None):
    cues = []
    for i, f in enumerate(files):
        t = 1.0 + 0.5 * i
        cues.append({"id": f"c{i}", "kind": (kinds or ["tap"] * len(files))[i],
                     "sound": (sounds or ["wood block"] * len(files))[i], "stem": "ui", "t": t, "beat": t,
                     "anchor": "onset", "file": f})
    _sheet(fd, cues)


def test_repeats_one_sound_per_kind_with_variation(sound_film):
    mix.run(sound_film)
    files = [_write(sound_film, f"sound/cues/tap{i}.wav", H.wood_hit(i, pitch=1100 * 2 ** (i / 12))) for i in range(5)]
    _taps(sound_film, files)
    r = _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"]
    assert r.status == "PASS" and r.details["distinct"] == 5


def test_repeats_catch_the_same_hit_twice_in_a_row(sound_film):
    mix.run(sound_film)
    same = _write(sound_film, "sound/cues/tap.wav", H.wood_hit(1) * 0.4)
    louder = _write(sound_film, "sound/cues/tap_loud.wav", H.wood_hit(1) * 0.8)  # a level change isn't a new sound
    _taps(sound_film, [same, louder, _write(sound_film, "sound/cues/tap2.wav", H.wood_hit(2))])
    r = _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"]
    assert r.status == "FAIL" and len(r.details["identical_repeats"]) == 1


def test_repeats_catch_two_sounds_for_one_kind_and_one_sound_for_two_kinds(sound_film):
    mix.run(sound_film)
    files = [_write(sound_film, f"sound/cues/h{i}.wav", H.wood_hit(i)) for i in range(4)]
    _taps(sound_film, files, sounds=["wood block", "clave", "wood block", "wood block"])
    r = _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"]
    assert r.status == "FAIL" and "tap" in r.details["kinds_with_several_sounds"]
    _taps(sound_film, files, kinds=["tap", "land", "tap", "land"])
    r = _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"]
    assert r.status == "FAIL" and "wood block" in r.details["sounds_serving_several_kinds"]


# ---------------------------------------------------------------------------------------------------------------
# Glides, drones, levels, clarity, licence


def _glide(f0, f1, secs=1.6):
    t = np.arange(int(secs * SR)) / SR
    f = f0 * (f1 / f0) ** (t / secs)
    y = 0.2 * np.sin(2 * np.pi * np.cumsum(f) / SR) * dsp.fade(len(t), 0.05, 0.1, SR)
    return np.stack([y, y], 1)


def _held_then_crossfade(f0, f1, secs=1.6):
    n = int(secs * SR)
    t = np.arange(n) / SR
    u = np.clip((t - 0.4) / 0.8, 0, 1)
    y = 0.2 * (np.cos(np.pi / 2 * u) * np.sin(2 * np.pi * f0 * t) + np.sin(np.pi / 2 * u) * np.sin(2 * np.pi * f1 * t))
    y *= dsp.fade(n, 0.05, 0.1, SR)
    return np.stack([y, y], 1)


def test_glide_check_catches_a_siren_and_passes_a_crossfade():
    siren = checks.glide_check(_glide(220.0, 329.63), SR)
    assert not siren["pass"] and siren["slide_ups"]
    fall = checks.glide_check(_glide(440.0, 293.66), SR)
    assert not fall["pass"] and not fall["slide_ups"]
    assert checks.glide_check(_held_then_crossfade(220.0, 329.63), SR)["pass"]


def test_glides_gate_reads_the_tonal_effects(sound_film):
    tonal = np.zeros((14 * SR, 2))
    tonal[4 * SR:4 * SR + int(1.6 * SR)] = _glide(220.0, 329.63)
    audio.write_wav(sound_film / "sound/stems/tonal.wav", tonal, SR)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.glides"]))["sound.glides"]
    assert r.status == "FAIL" and "slide-up" in r.summary
    tonal[4 * SR:4 * SR + int(1.6 * SR)] = _held_then_crossfade(220.0, 329.63)
    audio.write_wav(sound_film / "sound/stems/tonal.wav", tonal, SR)
    mix.run(sound_film)
    assert _by_name(verify.run(sound_film, ["sound.glides"]))["sound.glides"].status == "PASS"


def _room_tone(seconds, db=-45.0, seed=5):
    x = np.random.default_rng(seed).standard_normal((int(round(seconds * SR)), 2))
    x = dsp.lowpass(x, 3000, SR)
    return x * 10 ** (db / 20) / np.sqrt(np.mean(x ** 2))


def test_drone_catches_a_room_tone_bed_unless_it_is_planned(sound_film):
    audio.write_wav(sound_film / "sound/stems/amb.wav", _room_tone(14.0), SR)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.drone"]))["sound.drone"]
    assert r.status == "FAIL" and r.details["drones"][0]["stem"] == "amb"
    _sheet(sound_film, [], beds=[{"from": 0.0, "to": 14.0, "why": "the plate's own wind"}])
    assert _by_name(verify.run(sound_film, ["sound.drone"]))["sound.drone"].status == "PASS"


def test_drone_check_passes_event_tied_clicks_and_a_swell():
    x = np.zeros((10 * SR, 2))
    for k, t in enumerate(np.arange(0.5, 9.5, 0.7)):
        h = H.wood_hit(k)
        x[int(t * SR):int(t * SR) + len(h)] += h
    swell = _room_tone(3.0, -30.0) * np.linspace(0, 1, 3 * SR)[:, None] ** 2
    x[6 * SR:9 * SR] += swell
    assert checks.drone_spans(x, SR)["pass"]


def test_levels_follow_the_palette(sound_film):
    ui = np.zeros((14 * SR, 2))
    for k, t in enumerate(np.arange(1.0, 13.0, 0.9)):
        h = H.wood_hit(k, pitch=900 + 30 * k)
        ui[int(t * SR):int(t * SR) + len(h)] += h
    audio.write_wav(sound_film / "sound/stems/ui.wav", ui * 0.05, SR)
    mix.run(sound_film, calibrate=True)
    assert _by_name(verify.run(sound_film, ["sound.levels"]))["sound.levels"].status == "PASS"
    mix.run(sound_film)  # uncalibrated: the clicks as they came, far from the palette's -17 LU
    r = _by_name(verify.run(sound_film, ["sound.levels"]))["sound.levels"]
    assert r.status == "FAIL" and "ui" in r.details["off"]


def test_clarity_catches_a_word_buried_under_an_effect(sound_film):
    noise = np.zeros((14 * SR, 2))
    a, b = int(0.9 * SR), int(3.2 * SR)
    noise[a:b] = _room_tone((b - a) / SR, -14.0, seed=8)
    audio.write_wav(sound_film / "sound/stems/fx.wav", noise, SR)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.clarity"]))["sound.clarity"]
    assert r.status == "FAIL" and r.details["masked"]


def test_clarity_catches_an_effect_louder_than_the_voice(sound_film):
    hit = np.zeros((14 * SR, 2))
    hit[int(0.02 * SR):int(0.02 * SR) + int(0.3 * SR)] = _room_tone(0.3, -3.0, seed=2)
    audio.write_wav(sound_film / "sound/stems/fx.wav", hit, SR)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.clarity"]))["sound.clarity"]
    assert r.status == "FAIL" and not r.details["never_over"]["pass"]


def test_broadcast_needs_the_enterprise_music_licence(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "tv", extra={"broadcast": True})
    mix.run(fd)
    r = _by_name(verify.run(fd, ["sound.music_licence"]))["sound.music_licence"]
    assert r.status == "REVIEW" and "Enterprise" in r.summary
    data = json.loads((fd / "film.json").read_text())
    data["licences"] = {"eleven_music": "enterprise"}
    (fd / "film.json").write_text(json.dumps(data))
    assert _by_name(verify.run(fd, ["sound.music_licence"]))["sound.music_licence"].status == "PASS"


def test_verify_before_mixing_names_the_next_step(sound_film):
    with pytest.raises(MissingPrerequisite):
        verify.run(sound_film)
    code, out, _ = run_cli(["verify", "--film", str(sound_film), "--json"])
    assert code == 3 and "slate mix" in json.loads(out)["hint"]


def test_unknown_gate_is_a_usage_error(sound_film):
    mix.run(sound_film)
    code, _, _ = run_cli(["verify", "loudnes", "--film", str(sound_film)])
    assert code == 2


def test_every_sound_gate_names_its_stage(sound_film):
    """sound.voice_sync has "voice" in its name; the stage it declares keeps it in the sound stage of `slate status`."""
    from slate.sound import mix as smix
    from slate.sound import verify as sverify

    smix.run(sound_film)
    results = sverify.run(sound_film, ["voice_sync", "loudness"])
    assert {r.details["stage"] for r in results} == {"sound"}
    status = pytest.importorskip("slate.state.status")
    assert {status._gate_stage(r) for r in results} == {"sound"}  # noqa: SLF001


# ---------------------------------------------------------------------------------------------------------------
# The voice limiter (L-sound-022), the key (L-sound-060), timbre (L-sound-078), brightness against the reference
# (L-sound-D017), the sonic logo's bookend (L-sound-S023) and Whisper on the mix (L-sound-S019)


def test_the_voice_limiter_gate_fails_a_hot_take_and_passes_the_normal_one(slate_home):
    H.brand(slate_home["brands"])
    hot, calm = H.film(slate_home["films"], "hot", plosives=True), H.film(slate_home["films"], "calm")
    for fd in (hot, calm):
        mix.run(fd)
    r = _by_name(verify.run(hot, ["sound.voice_limiter"]))["sound.voice_limiter"]
    assert r.status == "FAIL" and "of the speech" in r.summary and r.details["share"] >= 0.05
    r = _by_name(verify.run(calm, ["sound.voice_limiter"]))["sound.voice_limiter"]
    assert r.status == "PASS" and r.details["share"] == 0.0
    rep = jsonio.read(calm / "sound/mix/final_mix.json")
    rep.pop("voice_limiter")  # a mix made before the measure existed
    jsonio.write_atomic(calm / "sound/mix/final_mix.json", rep)
    r = _by_name(verify.run(calm, ["sound.voice_limiter"]))["sound.voice_limiter"]
    assert r.status == "UNCHECKED" and "mix again" in r.summary


A4, C5, D5, E5, F5, G5 = 440.0, 523.25, 587.33, 659.26, 698.46, 783.99


def _pitched_stem(fd, role, pitches, start=1.0, step=0.9):
    x = np.zeros((14 * SR, 2))
    for i, p in enumerate(pitches):
        y = _note(i, p, -6.0, dur=0.5)
        k = int((start + step * i) * SR)
        x[k:k + len(y)] += y
    audio.write_wav(fd / f"sound/stems/{role}.wav", x, SR)


def test_the_key_gate_fails_a_note_off_the_pentatonic(sound_film):
    """The brand is in A minor: its pentatonic is A, C, D, E and G. An F in the UI stem is off it."""
    _pitched_stem(sound_film, "ui", [A4, C5, D5, E5, F5, G5])
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.key"]))["sound.key"]
    assert r.status == "FAIL" and "F5" in r.summary and r.details["off_key"][0]["stem"] == "ui"
    assert r.details["stems"]["ui"]["notes"] == 6


def test_the_key_gate_passes_the_pentatonic_and_asks_about_one_note_played_over_and_over(sound_film):
    _pitched_stem(sound_film, "ui", [A4, C5, D5, E5, G5, A4])
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.key"]))["sound.key"]
    assert r.status == "PASS" and "on A minor's pentatonic" in r.summary
    _pitched_stem(sound_film, "tonal", [A4, E5, A4, E5, A4, E5, A4, E5, C5, G5], step=0.8)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.key"]))["sound.key"]
    assert r.status == "REVIEW" and "80% of 10 notes on A4 and E5" in r.summary


def _bell(f0=800.0, secs=2.0):
    t = np.arange(int(secs * SR)) / SR
    y = sum(a * np.sin(2 * np.pi * f0 * r * t) * np.exp(-t / d)
            for r, a, d in ((1, 1.0, 1.5), (2.76, 0.6, 1.2), (5.4, 0.4, 0.8), (8.93, 0.25, 0.5)))
    y = 0.3 * y * (1 - np.exp(-t / 0.001))
    return np.stack([y, y], 1)


def test_the_timbre_gate_fails_a_bell_and_passes_a_wood_click(sound_film):
    """No chimes: a bell's partials ring on past 300 ms, bright and not one note's harmonics. A wood click is over in
    a tenth of a second, its energy low."""
    x = np.zeros((14 * SR, 2))
    for t in (2.0, 6.0, 10.0):
        y = H.wood_hit(int(t), pitch=500.0)
        x[int(t * SR):int(t * SR) + len(y)] += y
    audio.write_wav(sound_film / "sound/stems/ui.wav", x, SR)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.timbre"]))["sound.timbre"]
    assert r.status == "PASS" and r.details["stems"]["ui"]["hits"] == 3
    assert r.details["stems"]["ui"]["median_centroid_hz"] < 1500
    bell = np.zeros((14 * SR, 2))
    bell[4 * SR:4 * SR + 2 * SR] += _bell()
    audio.write_wav(sound_film / "sound/stems/tonal.wav", bell, SR)
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.timbre"]))["sound.timbre"]
    assert r.status == "FAIL" and "tonal hit at" in " ".join(r.evidence)
    tonal = r.details["stems"]["tonal"]
    assert tonal["chime_tails"] and tonal["metallic"]


def test_effects_brighter_than_the_reference_wait_for_a_person(slate_home, sound_film):
    """Moving from chimes to pads, notes and wood took the films' effects from 583 to 382 Hz (and from 3.7% to 1.2%
    of their energy above 2 kHz): an effects stem more than 25% brighter than the brand's approved reference is a
    person's call."""
    x = np.zeros((14 * SR, 2))
    for i, t in enumerate(np.arange(1.0, 13.0, 1.5)):
        y = H.wood_hit(i, pitch=2600.0)
        x[int(t * SR):int(t * SR) + len(y)] += y * 0.3
    audio.write_wav(sound_film / "sound/stems/ui.wav", x, SR)
    mix.run(sound_film)
    rep = jsonio.read(sound_film / "sound/mix/final_mix.json")
    _approve_reference(slate_home["brands"], {"ui": rep["levels_vs_voice_lu"]["ui"]})
    brand = slate_home["brands"] / "testbrand" / "brand.json"
    data = json.loads(brand.read_text())
    data["sound"]["reference_brightness"] = {"centroid_hz": 382.0, "above_2k_share": 0.012}
    brand.write_text(json.dumps(data, indent=1))
    r = _by_name(verify.run(sound_film, ["sound.reference"]))["sound.reference"]
    assert r.status == "REVIEW" and "brighter than it" in r.summary and r.details["brightness"]["brighter"]
    data["sound"]["reference_brightness"] = {"centroid_hz": r.details["brightness"]["film"]["centroid_hz"],
                                             "above_2k_share": r.details["brightness"]["film"]["above_2k_share"]}
    brand.write_text(json.dumps(data, indent=1))
    r = _by_name(verify.run(sound_film, ["sound.reference"]))["sound.reference"]
    assert r.status == "PASS" and "within 25%" in r.summary


def test_the_reference_brightness_is_measured_on_the_stems_beside_it(slate_home, sound_film):
    mix.run(sound_film)
    d = slate_home["brands"] / "testbrand"
    ref = audio.write_wav(d / "proof_mix.wav", H.pad(4.0), SR)
    stems = d / "proof_stems"
    audio.write_wav(stems / "vo.wav", H.speech(4.0, 1)[0], SR)
    ui = np.zeros((4 * SR, 2))
    ui[SR:SR + int(0.25 * SR)] += H.wood_hit(1, pitch=400.0)
    audio.write_wav(stems / "ui.wav", ui, SR)
    from slate.sound.film import load as load_film

    b, why = verify.reference_brightness(load_film(sound_film), {"file": str(ref)})
    assert why == "measured on proof_stems" and b["centroid_hz"] < 1000 and b["above_2k_share"] < 0.05


def test_the_sonic_logo_closing_the_film_as_it_opened_is_a_bookend_not_a_repeat(sound_film):
    mix.run(sound_film)
    logo = _write(sound_film, "sound/cues/logo.wav", _note(1, 659.26, 0.0, dur=0.8))
    tap = _write(sound_film, "sound/cues/tap.wav", H.wood_hit(3))
    other = _write(sound_film, "sound/cues/tap2.wav", H.wood_hit(4))
    cues = [{"id": "logo_in", "kind": "logo", "sound": "the mark", "stem": "tonal", "t": 0.5, "beat": 0.5,
             "anchor": "onset", "sonic_logo": True, "file": logo},
            {"id": "t1", "kind": "tap", "sound": "wood block", "stem": "ui", "t": 5.0, "beat": 5.0, "anchor": "onset",
             "file": tap},
            {"id": "t2", "kind": "tap", "sound": "wood block", "stem": "ui", "t": 7.0, "beat": 7.0, "anchor": "onset",
             "file": other},
            {"id": "logo_out", "kind": "logo", "sound": "the mark", "stem": "tonal", "t": 12.5, "beat": 12.5,
             "anchor": "onset", "sonic_logo": True, "file": logo}]
    _sheet(sound_film, cues)
    r = _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"]
    assert r.status == "PASS" and "deliberate bookend" in r.summary
    assert r.details["intentional"][0]["t"] == 12.5 and r.details["intentional"][0]["after_t"] == 0.5
    cues[2]["file"] = tap  # the same tap twice in a row still fails
    _sheet(sound_film, cues)
    r = _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"]
    assert r.status == "FAIL" and len(r.details["identical_repeats"]) == 1 and len(r.details["intentional"]) == 1
    for c in (cues[0], cues[3]):  # the same pair without the logo's mark: a repeat like any other
        c.pop("sonic_logo")
        c["kind"] = "impact"
    cues[2]["file"] = other
    _sheet(sound_film, cues)
    assert _by_name(verify.run(sound_film, ["sound.repeats"]))["sound.repeats"].status == "FAIL"


def _heard_fixture(film_dir, words):
    """Record what Whisper 'heard' in this exact mix (a replay fixture keyed by the mix's own hash)."""
    from slate import replay
    from slate.voice import align as voice_align

    sha = jsonio.sha256(film_dir / "sound/mix/final_mix.wav")
    key = f"mix-{sha[:16]}-{voice_align.whisper_model(None)}"
    text = "".join(f" {w}" for w, *_ in words)
    replay.record("whisper", key, {"text": text, "language": "en", "segments": [{"text": text, "words": [
        {"word": f" {w}", "start": s, "end": e, "probability": p} for w, s, e, p in words]}]})


def test_mix_words_sets_aside_a_word_heard_over_the_music_and_fails_one_in_the_voice(sound_film, monkeypatch, tmp_path):
    """Whisper invents words after speech ends (one film's mix came back "...where you stand. you"). A word heard
    where the voice isn't sounding, or that Whisper is less than 20% sure of, is listed as heard over the music; an
    extra word in the voice, or a script word not heard, fails."""
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fixtures"))
    from slate.sound.film import load as load_film, words_for

    mix.run(sound_film)
    placed = [(w, round(s, 3), round(e, 3), 0.9) for w, s, e in words_for(load_film(sound_film))]
    _heard_fixture(sound_film, placed + [("you", 13.75, 13.9, 0.8)])
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "PASS" and "heard over the music" in r.summary and r.details["over_music"][0]["word"] == "you"
    assert r.details["heard"]["recorded"] is True and "stand-in" not in r.summary
    w, s, e, _p = placed[5]
    _heard_fixture(sound_film, placed[:6] + [("so", s + 0.02, s + 0.08, 0.5)] + placed[6:])
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "FAIL" and "an extra 'so'" in r.summary
    _heard_fixture(sound_film, placed[:6] + [("so", s + 0.02, s + 0.08, 0.1)] + placed[6:])  # barely sure: the music
    assert _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"].status == "PASS"
    _heard_fixture(sound_film, placed[:3] + placed[4:])
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "FAIL" and "not heard" in r.summary


def test_mix_words_never_passes_a_real_voice_on_a_stand_in_hearing(sound_film, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fixtures"))
    mix.run(sound_film)
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "PASS" and "stand-in" in r.summary  # the fixture film's voice is a stand-in itself
    doc = jsonio.read(sound_film / "timing/vo_words.json")
    for entry in doc["shots"].values():
        entry["aligner"] = {"engine": "whisper", "model": "turbo", "version": "20250625", "replay": False}
    jsonio.write_atomic(sound_film / "timing/vo_words.json", doc)
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "UNCHECKED" and "--live" in r.evidence[0]


def test_mix_words_reads_numbers_percent_and_the_brands_spellings_as_a_listener_would():
    from slate.sound import hear

    lines = [{"shot": "S01", "line": "Twenty-five hundred teams, 30% faster with Demo Studio.", "start": 0.0, "end": 4.0}]
    heard = [{"word": w, "start": 0.3 * i, "end": 0.3 * i + 0.25, "probability": 0.9}
             for i, w in enumerate(["2,500", "team's,", "30", "percent", "faster", "with", "Demo", "Studios."])]
    res = hear.compare(lines, heard, lambda t: True, names={"Demo Studio": ["Demo Studios"]})
    assert res["errors"] == [] and res["per_shot"]["S01"]["wer"] == 0.0
    res = hear.compare(lines, heard, lambda t: True)  # without the brand's spelling, it's a misheard word
    assert [e["what"] for e in res["errors"]] == ["misheard"]


def test_a_first_word_stretched_across_the_pause_belongs_to_its_line():
    """Whisper stamps a segment's first word from the end of the word before, so "For" can run 50.24-51.40 s into a
    line that starts at 51.27 s. It belongs to that line (and is checked there), not between the lines."""
    from slate.sound import hear

    lines = [{"shot": "L08", "line": "a score between 400 and 900.", "start": 46.0, "end": 50.16},
             {"shot": "L09", "line": "For teams, that means more.", "start": 51.27, "end": 53.0}]
    said = [("a", 46.0, 46.2), ("score", 46.3, 46.8), ("between", 46.9, 47.4), ("400", 47.5, 48.6), ("and", 49.36, 49.8),
            ("900.", 49.8, 50.24), ("For", 50.24, 51.4), ("teams,", 51.4, 51.78), ("that", 52.04, 52.36),
            ("means", 52.4, 52.7), ("more.", 52.7, 53.0)]
    heard = [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in said]
    res = hear.compare(lines, heard, lambda t: True)
    assert res["errors"] == [], res["errors"]
    assert res["per_shot"]["L09"]["heard"].startswith("For teams")
    # a stray word inside the pause that reaches no line's words is still between the lines, and still counts
    stray = heard[:6] + [{"word": "so", "start": 50.6, "end": 50.8, "probability": 0.9}] + heard[6:]
    stray[7] = {**stray[7], "start": 51.2}
    res = hear.compare(lines, stray, lambda t: True)
    assert [(e["word"], e["where"]) for e in res["errors"]] == [("so", "between lines")]


def test_the_mix_is_heard_without_whispers_running_context(monkeypatch, tmp_path):
    """Over a whole mix, Whisper's previous-text conditioning can loop (a film's mix came back with a line said twice
    and three section names as the brand). The mix is heard window by window; a take's hearing is unchanged."""
    from slate import procs
    from slate.voice import align as voice_align

    seen = []
    monkeypatch.setattr(voice_align, "whisper_info", lambda: {"path": "/fake/whisper", "version": "test"})
    monkeypatch.setattr(voice_align, "_hold", lambda resource, fn: fn())
    monkeypatch.setattr(procs, "run", lambda argv, **kw: seen.append(list(argv)))
    wav = tmp_path / "mix.wav"
    wav.write_bytes(b"")
    voice_align.run_whisper([wav], tmp_path / "out", context=False)
    voice_align.run_whisper([wav], tmp_path / "out")
    assert seen[0][-2:] == ["--condition_on_previous_text", "False"]
    assert "--condition_on_previous_text" not in seen[1]

    from slate.sound import hear

    calls = []
    monkeypatch.setattr(hear.replay, "fake_vendors", lambda: False)
    monkeypatch.setattr(hear.audio, "duration_s", lambda p: 10.0)
    monkeypatch.setattr(voice_align, "transcribe", lambda path, **kw: calls.append(kw) or {"segments": []})
    _result, how = hear.hear(None, wav)
    assert calls[0]["context"] is False and how["previous_text"] is False


def test_a_voice_only_film_has_nothing_to_hold_against_the_reference(slate_home):
    """A film mixed from the voice alone (the score and effects still to come) has no balance that could be off the
    brand's reference: it passes, as the gap, cue and level gates pass a film with none of theirs. A film with a stem
    the reference doesn't have stays unchecked."""
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "vonly-ref", score=False)
    mix.run(fd)
    _approve_reference(slate_home["brands"], {"accents": -9.0, "score": -18.0})
    r = _by_name(verify.run(fd, ["sound.reference"]))["sound.reference"]
    assert r.status == "PASS" and "nothing but the voice" in r.summary and r.details["voice_only"] is True
    scored = H.film(slate_home["films"], "scored-ref")
    mix.run(scored)
    r = _by_name(verify.run(scored, ["sound.reference"]))["sound.reference"]
    assert r.status == "UNCHECKED" and "share no stem" in r.summary  # this mix wrote no family stems to compare


def test_a_family_keyed_reference_is_compared_on_the_family_stems(slate_home):
    """A brand reference measured by family (accents, score, swells, textures) is held against the mix's family stems
    (sound/mix/family_stems/), each against the voice; the starting palette's stem targets give way to it."""
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "fam-ref")
    mix.run(fd)
    d = fd / "sound/mix/family_stems"
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    voice = rng.standard_normal((SR * 6, 2)) * 0.1
    audio.write_wav(d / "voice.wav", voice, SR)
    audio.write_wav(d / "score.wav", voice * 10 ** (-21 / 20), SR)       # 21 LU under the voice
    audio.write_wav(d / "textures.wav", voice * 10 ** (-21 / 20), SR)    # 21 LU under: louder than the reference
    _approve_reference(slate_home["brands"], {"score": -21.4, "textures": -25.3})
    r = _by_name(verify.run(fd, ["sound.reference"]))["sound.reference"]
    assert r.status == "FAIL" and "textures" in r.details["off"] and "score" not in r.details["off"]
    audio.write_wav(d / "textures.wav", voice * 10 ** (-26 / 20), SR)
    r = _by_name(verify.run(fd, ["sound.reference"]))["sound.reference"]
    assert r.status in ("PASS", "REVIEW"), r.summary
    lv = _by_name(verify.run(fd, ["sound.levels"]))["sound.levels"]
    assert lv.status == "PASS" and lv.details["deferred_to"] == "sound.reference"


def test_a_line_the_long_pass_skips_is_heard_on_a_second_listen(sound_film, monkeypatch, tmp_path):
    """Over a whole mix Whisper can jump past half a line while its take sits at its cue. The line is heard again on
    its own span of the mix and judged on that; a line the second listen misses too still fails."""
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fixtures"))
    from slate.sound import hear
    from slate.sound.film import load as load_film, words_for

    mix.run(sound_film)
    placed = [(w, round(s, 3), round(e, 3), 0.9) for w, s, e in words_for(load_film(sound_film))]
    _heard_fixture(sound_film, placed[:2] + placed[5:])  # the long pass skips three words
    lines = hear.script_lines(load_film(sound_film))
    skipped = {ln["shot"] for ln in lines if ln["start"] - 0.35 <= placed[2][1] <= ln["end"] + 0.35}
    first_pass = hear.hear
    monkeypatch.setattr(hear, "hear", lambda *a, **k: (lambda r: (r[0], {**r[1], "replay": False}))(first_pass(*a, **k)))

    def second(film, mix_path, lines, shots, *, language="en"):
        assert set(shots) == skipped
        return [{"word": w, "start": s, "end": e, "probability": p, "relistened": next(iter(skipped))}
                for w, s, e, p in placed if any(ln["shot"] in skipped and ln["start"] - 0.35 <= (s + e) / 2 <= ln["end"] + 0.35
                                                for ln in lines)]
    monkeypatch.setattr(hear, "relisten", second)
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "PASS" and "second listen" in r.summary, r.summary
    assert all(v["settled"] for v in r.details["relistened"].values())
    monkeypatch.setattr(hear, "relisten", lambda *a, **k: [])  # the second listen hears nothing either: it fails
    r = _by_name(verify.run(sound_film, ["sound.mix_words"]))["sound.mix_words"]
    assert r.status == "FAIL" and "not heard" in r.summary


def test_textures_held_under_the_reference_pass_but_louder_ones_fail(slate_home):
    """Textures and swells are held under the voice on purpose, further in a film that talks more than its reference:
    quieter than the reference passes (and says so); louder fails. Accents are held both ways."""
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "guarded")
    mix.run(fd)
    d = fd / "sound/mix/family_stems"
    d.mkdir(parents=True, exist_ok=True)
    voice = np.random.default_rng(2).standard_normal((SR * 6, 2)) * 0.1
    audio.write_wav(d / "voice.wav", voice, SR)
    for fam, lu in (("textures", -30.0), ("accents", -19.0)):
        audio.write_wav(d / f"{fam}.wav", voice * 10 ** (lu / 20), SR)
    _approve_reference(slate_home["brands"], {"textures": -25.3, "accents": -18.8})
    r = _by_name(verify.run(fd, ["sound.reference"]))["sound.reference"]
    assert r.status == "PASS" and "held further under" in r.summary and "textures" in r.details["held_under"]
    audio.write_wav(d / "textures.wav", voice * 10 ** (-21.0 / 20), SR)     # louder than the reference: off it
    assert _by_name(verify.run(fd, ["sound.reference"]))["sound.reference"].status == "FAIL"
    audio.write_wav(d / "textures.wav", voice * 10 ** (-26.0 / 20), SR)
    audio.write_wav(d / "accents.wav", voice * 10 ** (-24.0 / 20), SR)      # accents buried: off it
    r = _by_name(verify.run(fd, ["sound.reference"]))["sound.reference"]
    assert r.status == "FAIL" and "accents" in r.details["off"]
