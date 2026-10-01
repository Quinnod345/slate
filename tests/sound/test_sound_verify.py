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
