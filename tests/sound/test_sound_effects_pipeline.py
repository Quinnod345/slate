"""Real neutral spotting, effects and master integration; no vendors or installed sample library required."""
from __future__ import annotations

import numpy as np
import pytest

from slate import jsonio, paths
from slate.errors import Refused
from slate.sound import audio, mix, verify
from slate.sound.effects import engine
from slate.sound.film import load
from slate.state import approvals
import sound_helpers as H


@pytest.fixture
def effects_film(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], voice=False, score=False)
    data = jsonio.read(fd / "film.json")
    data["sample_rate"] = 16000
    jsonio.write_atomic(fd / "film.json", data)
    shots = jsonio.read(fd / "timing/shots.json")
    shots.update(total_frames=100, shots=shots["shots"][:1], acts=[{"index": 1, "shots": ["S01"]}])
    jsonio.write_atomic(fd / "timing/shots.json", shots)
    voice, words = H.speech(4, seed=47, sr=16000)
    audio.write_wav(fd / "voice/take.wav", voice, 16000)
    jsonio.write_atomic(fd / "timing/vo_cues.json", {"cues": [{"shot": "S01", "t": 0, "file": "voice/take.wav", "gain": -20}]})
    jsonio.write_atomic(fd / "timing/vo_words.json", {"shots": {"S01": {"words": words}}})
    jsonio.write_atomic(fd / "timing/visual_events/act1.json",
                        [{"shot": "S01", "id": f"tap{i}", "kind": "tap", "t": t}
                         for i, t in enumerate((.5, 1.2, 2.0))])
    proof = slate_home["data"] / "approved-proof.wav"
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_bytes(b"neutral fixture proof")
    jsonio.write_atomic(paths.data() / "approvals.json", {"approvals": [{"what": "proof3", "path": str(proof),
                        "sha256": jsonio.sha256(proof), "approved_by": "A. Person"}]})
    return fd


def test_events_to_balanced_stems_to_additive_master(effects_film):
    fd = effects_film
    first = engine.spot(fd, seed=19)
    cues = jsonio.read(first)
    assert len(cues["cues"]) == 3
    assert all(c["family"] == "accents" for c in cues["cues"])
    sha = jsonio.sha256(first)
    engine.spot(fd, seed=19)
    assert jsonio.sha256(first) == sha
    rendered = engine.render(fd)
    assert rendered["report"]["provisional"]
    assert rendered["report"]["guard_report"]["delivered_voice_feedback"]
    final = mix.run(fd)
    report = jsonio.read(final.with_suffix(".json"))
    assert report["score_prepared"] and report["stems_sum_max_abs"] < 1e-6
    expected, _ = audio.read_wav(final)
    families = [audio.read_wav(fd / p)[0] for p in report["outputs"]["family_stems"].values()]
    assert np.max(abs(sum(families) - expected)) < 1e-6
    rides = jsonio.read(fd / "sound/mix/rides.json")
    assert rides["mix_sha256"] == jsonio.sha256(final)
    assert rides["family_sum_max_abs"] < 1e-9
    assert verify.gate_cue_sync(verify._Ctx(load(fd))).status == "PASS"
    repeats = verify.gate_repeats(verify._Ctx(load(fd)))
    assert repeats.status == "PASS" and repeats.details["render_log_verified"]
    path = fd / "sound/mix/render_log.json"
    log = jsonio.read(path)
    log["cues"][0]["waveform_identity"] = "different sound"
    jsonio.write_atomic(path, log)
    assert verify.gate_repeats(verify._Ctx(load(fd))).status == "UNCHECKED"
    path.unlink()
    assert verify.gate_repeats(verify._Ctx(load(fd))).status == "UNCHECKED"


def test_changed_picture_requires_respot_and_keeps_existing_stems(effects_film):
    engine.spot(effects_film)
    marker = effects_film / "sound/stems/ui.wav"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"previous complete render")
    path = effects_film / "timing/visual_events/act1.json"
    events = jsonio.read(path)
    events[0]["t"] += .1
    jsonio.write_atomic(path, events)
    with pytest.raises(Refused, match="changed after effects"):
        engine.render(effects_film)
    assert marker.read_bytes() == b"previous complete render"


def test_failed_mix_keeps_previous_complete_publication(effects_film, monkeypatch):
    final = mix.run(effects_film)
    original = jsonio.sha256(final)
    saved = jsonio.sha256(final.with_suffix(".json"))
    write = audio.write_wav
    def fail(path, *args, **kwargs):
        if str(path).endswith("effects.wav"):
            raise OSError("simulated full volume")
        return write(path, *args, **kwargs)
    monkeypatch.setattr(audio, "write_wav", fail)
    with pytest.raises(OSError, match="full volume"):
        mix.run(effects_film)
    assert jsonio.sha256(final) == original
    assert jsonio.sha256(final.with_suffix(".json")) == saved
    assert not list((effects_film / "sound").glob(".mix-*"))


def test_voice_changes_during_read_refuse_generated_mix_publication(effects_film, monkeypatch):
    from slate.sound import vo

    engine.spot(effects_film)
    jsonio.write_atomic(effects_film / "sound/stems/stems.json", {"producer": "slate sfx", "stems": {}})
    final = effects_film / "sound/mix/final_mix.wav"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"previous mix")
    render = vo.render_stem
    def change_after_read(film):
        result = render(film)
        p = film.path("voice/take.wav")
        samples, sr = audio.read_wav(p)
        audio.write_wav(p, samples*.5, sr)
        return result
    monkeypatch.setattr(vo, "render_stem", change_after_read)
    with pytest.raises(Refused, match="changed during"):
        mix.run(effects_film)
    assert final.read_bytes() == b"previous mix"


def test_approved_reference_bundle_binds_rides_and_audio(effects_film):
    folder = effects_film / "sound/references/chosen"
    jsonio.write_atomic(folder / "cue_sheet.json", {"window": [0, 1], "cues": []})
    jsonio.write_atomic(folder / "balance_used.json", {"rides": {}})
    loader = engine.reference_loader(load(effects_film))
    with pytest.raises(Refused, match="not approved"):
        loader("sound/references/chosen")
    approvals.record(effects_film, what="sound_proof", path="sound/references/chosen", approved_by="A. Person")
    assert loader("sound/references/chosen")["balance_used"] == {"rides": {}}
    jsonio.write_atomic(folder / "balance_used.json", {"rides": {"new": -2}})
    with pytest.raises(Refused, match="changed since"):
        loader("sound/references/chosen")


def test_prepared_score_matches_single_preparation_exactly():
    sr = 16000
    voice, _ = H.speech(2, seed=91, sr=sr)
    score = H.pad(2, sr=sr)
    targets = {"lufs": -14, "tol": .5, "true_peak": -1}
    normal = mix.mix_arrays(voice, {}, score, sr, targets=targets)
    prepared, report = mix.prepare_score(score, voice, sr)
    staged = mix.mix_arrays(voice, {}, prepared, sr, targets=targets, score_prepared=True,
                            prepared_score_report=report)
    assert np.array_equal(normal["mix"], staged["mix"])
    assert np.array_equal(normal["final"]["music"], staged["final"]["music"])


def test_additive_master_transform_uses_whole_bus_silence_mask():
    sr = 16000
    a = np.zeros((sr*2, 2)); b = a.copy()
    a[5000:10000] = H.sine(-20, 5000/sr, sr=sr)
    b[9000:18000] = H.sine(-18, 9000/sr, f=150, sr=sr)
    mastered = mix.mix_arrays(np.zeros_like(a), {"ui": a, "fx": b}, None, sr,
                              targets={"lufs": -18, "tol": .5, "true_peak": -1})
    summed = mastered["engine"].get("ui") + mastered["engine"].get("fx")
    assert np.max(abs(summed-mastered["final"]["effects"])) < 1e-10


@pytest.mark.parametrize("start", [0, 3*16000, 7*16000])
def test_short_master_window_matches_full_film_transform(start):
    sr, n = 16000, 8*16000
    hit = H.wood_hit(47, sr=sr)
    full = np.zeros((n, 2));full[start:start+len(hit)] = hit
    curve = np.linspace(.4, 1, n)
    transform = mix.EngineStems({}, {}, {}, curve, 2, 25, sr, {"effects": np.ones(n)})
    a, short = transform.transform_window(start, hit, "effects", n)
    restored = np.zeros_like(full);restored[a:a+len(short)] = short
    assert np.max(abs(restored-transform.transform(full, "effects"))) < 1e-12


def test_composed_score_replacement_is_recorded_without_losing_sync_evidence(effects_film):
    fd = effects_film
    path = engine.spot(fd)
    sheet = jsonio.read(path)
    sheet['cues'].append({'id': 'score-bloom', 'kind': 'impact', 'type': 'wood', 'role': 'bloom', 'sound': 'bloom',
                          'stem': 'tonal', 'family': 'swells', 't': 2.5, 'beat': 2.5, 'anchor': 'onset',
                          'params': {'kind': 'block', 'dur': .2, 'pitch': 'A4'}})
    jsonio.write_atomic(path, sheet)
    audio.write_wav(fd / 'sound/score/score.wav', H.pad(4, sr=16000), 16000)
    engine.render(fd)
    mix.run(fd)
    log = jsonio.read(fd / 'sound/mix/render_log.json')
    assert log['suppressed_by_score'] == ['score-bloom']
    assert 'score-bloom' not in {r['hit'] for r in log['cues']}
    context = verify._Ctx(load(fd))
    assert verify.gate_cue_sync(context).status == 'PASS'
    assert verify.gate_repeats(context).status == 'PASS'
    log['suppressed_by_score'].append(log['cues'].pop()['hit'])
    jsonio.write_atomic(fd / 'sound/mix/render_log.json', log)
    assert verify.gate_cue_sync(context).status == 'UNCHECKED'
