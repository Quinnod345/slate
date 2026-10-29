"""Picture timing measures the pre-polish take while the film plays the polished one."""
from pathlib import Path

import numpy as np
import pytest
from storyfix import write_json, write_wav

from slate import jsonio
from slate.cli import run_cli
from slate.errors import MissingPrerequisite
from slate.timing import align, audio, builder, takes


@pytest.fixture
def polished_film(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    folder = film / "voice/takes/S02"
    raw = write_wav(folder / "t01.wav", 3.1, bursts=[(.1, .2)])
    paced = write_wav(folder / "t01.paced.wav", 3.1, bursts=[(.3, .5)])
    polished = write_wav(folder / "t01.polished.wav", 3.1, bursts=[(.3, .7)])
    rel = lambda p: p.relative_to(film).as_posix()
    write_json(film / "voice/takes.json", {"shots": {
        "S02": {"selected": "t01", "takes": {"t01": {
            "raw": rel(raw), "paced": rel(paced), "polished": rel(polished), "dur": 3.0}}},
        "S03": {"file": "voice/takes/S03.wav"}, "S04": {"file": "voice/takes/S04.wav"}}})
    write_json(folder / "t01.json", {"stages": {"polish": {
        "source": rel(paced), "source_stage": "paced", "source_sha256": jsonio.sha256(paced),
        "file": rel(polished), "sha256": jsonio.sha256(polished)}}})
    return film, raw, paced, polished


def test_whisper_and_onsets_read_the_source_but_cues_play_the_polish(polished_film, monkeypatch):
    film, _, paced, polished = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    lines = {sh["id"]: sh.get("vo", "") for act in jsonio.read(film / "script/storyboard.json")["acts"]
             for sh in act["shots"]}
    heard, decoded = [], []

    def transcribe(path, **_):
        heard.append(path)
        shot = path.parent.name if path.parent.name == "S02" else path.stem
        start = .7 if path == polished else .3
        words = [{"word": w, "start": start + i * .2, "end": start + i * .2 + .15}
                 for i, w in enumerate(lines[shot].split())]
        return {"text": lines[shot], "segments": [{"words": words}], "language": "en"}

    def decode(path, *_):
        decoded.append(path)
        return np.zeros(int(3.4 * audio.RATE))

    monkeypatch.setattr(align, "run_whisper", transcribe)
    monkeypatch.setattr(audio, "decode_mono", decode)
    result = builder.run(film)
    rec = result.docs["vo_words"]["shots"]["S02"]
    cue = next(c for c in result.docs["vo_cues"]["cues"] if c["shot"] == "S02")
    assert heard[0] == paced and decoded[0] == paced
    assert rec["words"][0]["start"] == .3
    assert cue["file"] == rec["take"] == polished.relative_to(film).as_posix()
    assert rec["timing_source"]["file"] == paced.relative_to(film).as_posix()
    assert rec["timing_source"]["sha256"] == jsonio.sha256(paced)
    inputs = result.results[0].inputs
    assert inputs[paced.relative_to(film).as_posix()] == jsonio.sha256(paced)
    assert "voice:S02#timing" in inputs


def test_cache_and_dry_run_do_not_reuse_the_polished_transcript(polished_film, monkeypatch):
    film, _, paced, polished = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    take = takes.find_takes(film)["S02"]
    path = align.cache_path(film, "whisper", "S02")
    cache = {"take": take.rel, "take_sha256": jsonio.sha256(polished), "language": "en",
             "model": align.whisper_model(), "replay": False, "result": {"segments": []}}
    write_json(path, cache)
    assert align.reusable(film, "S02", take) is None
    assert "S02" in builder.alignments_to_make(film)
    cache.update(take=paced.relative_to(film).as_posix(), take_sha256=jsonio.sha256(paced))
    write_json(path, cache)
    assert align.reusable(film, "S02", take)["take_sha256"] == jsonio.sha256(paced)
    assert "S02" not in builder.alignments_to_make(film)


@pytest.mark.parametrize("changed", ["source", "output"])
def test_stale_explicit_provenance_refuses_before_alignment(polished_film, changed):
    film, _, paced, polished = polished_film
    path = paced if changed == "source" else polished
    write_wav(path, 3.1, bursts=[(.9, 1.0)])
    take = takes.find_takes(film)["S02"]
    with pytest.raises(MissingPrerequisite, match="polish"):
        align.align_take(film, "S02", take, "Every morning")
    assert not align.cache_path(film, "whisper", "S02").exists()


@pytest.mark.parametrize("rate,seconds", [(8000, 3.1), (16000, 3.0)])
def test_equal_sample_clock_is_required(polished_film, rate, seconds):
    film, _, paced, polished = polished_film
    write_wav(paced, seconds, rate=rate)
    sidecar = film / "voice/takes/S02/t01.json"
    doc = jsonio.read(sidecar)
    doc["stages"]["polish"]["source_sha256"] = jsonio.sha256(paced)
    write_json(sidecar, doc)
    with pytest.raises(MissingPrerequisite, match="sample"):
        align.align_take(film, "S02", takes.find_takes(film)["S02"], "Every morning")


def test_legacy_manifest_uses_nearest_stage_and_retains_the_trim(polished_film):
    film, _, paced, _ = polished_film
    (film / "voice/takes/S02/t01.json").unlink()
    source = takes.for_alignment(film, takes.find_takes(film)["S02"])
    assert source.path == paced and source.read_seconds == 3.0
    write_wav(paced, 2.0)
    with pytest.raises(MissingPrerequisite, match="sample"):
        takes.for_alignment(film, takes.find_takes(film)["S02"])


def test_declared_source_must_match_the_selected_take_stage(polished_film):
    film, raw, _, _ = polished_film
    sidecar = film / "voice/takes/S02/t01.json"
    doc = jsonio.read(sidecar)
    doc["stages"]["polish"].update(source=raw.relative_to(film).as_posix(), source_sha256=jsonio.sha256(raw))
    write_json(sidecar, doc)
    with pytest.raises(MissingPrerequisite, match="source"):
        takes.for_alignment(film, takes.find_takes(film)["S02"])


def test_timing_freshness_tracks_source_and_provenance_not_approval(polished_film):
    from slate.state import lock
    from slate import gates

    film, _, paced, _ = polished_film
    builder.run(film)
    assert not lock.is_stale(film, "timing/vo_words.json")
    assert all(r.status == "PASS" for r in gates.load_report(film).values() if r.gate.startswith("timing."))
    sidecar = film / "voice/takes/S02/t01.json"
    doc = jsonio.read(sidecar)
    doc.update(approved={"by": "Q"}, align="voice/align/S02.json", checked_at="later")
    doc["stages"]["polish"]["checked_at"] = "later"
    write_json(sidecar, doc)
    manifest = film / "voice/takes.json"
    doc = jsonio.read(manifest)
    doc["shots"]["S02"]["approved"] = {"by": "Q"}
    doc["shots"]["S02"]["takes"]["t01"].update(align="voice/align/S02.json", note="Approved")
    write_json(manifest, doc)
    assert not lock.is_stale(film, "timing/vo_words.json")
    write_wav(paced, 3.1, bursts=[(1.0, 1.1)])
    assert lock.is_stale(film, "timing/vo_words.json")
    assert all(r.status == "STALE" for r in gates.load_report(film).values() if r.gate.startswith("timing."))


def test_provenance_and_selected_read_length_invalidate_timing(polished_film):
    from slate.state import lock

    film, *_ = polished_film
    builder.run(film)
    sidecar = film / "voice/takes/S02/t01.json"
    original = jsonio.read(sidecar)
    changed = jsonio.read(sidecar)
    changed["stages"]["polish"]["source_sha256"] = "different"
    write_json(sidecar, changed)
    assert lock.is_stale(film, "timing/vo_words.json")
    write_json(sidecar, original)
    assert not lock.is_stale(film, "timing/vo_words.json")
    manifest = film / "voice/takes.json"
    changed = jsonio.read(manifest)
    changed["shots"]["S02"]["takes"]["t01"]["dur"] = 2.9
    write_json(manifest, changed)
    assert lock.is_stale(film, "timing/vo_words.json")


def test_derived_film_resolves_source_and_sidecar_by_reference(polished_film, tmp_path):
    from slate.state import lock

    parent, _, paced, polished = polished_film
    derived = tmp_path / "derived"
    derived.mkdir()
    refs = []
    for file in (parent / "voice").rglob("*"):
        if file.is_file():
            rel = file.relative_to(parent).as_posix()
            refs.append({"path": rel, "sha256": jsonio.sha256(file)})
    lock.add_refs(derived, refs, parent_id="parent", parent_dir=parent)
    take = takes.find_takes(derived)["S02"]
    assert take.path == polished
    assert takes.for_alignment(derived, take).path == paced
    assert takes.timing_selector(derived, "S02") == takes.timing_selector(parent, "S02")
    original = lock.hash_inputs(derived, ["voice:S02#timing"])
    sidecar = parent / "voice/takes/S02/t01.json"
    doc = jsonio.read(sidecar)
    doc["stages"]["polish"]["source_stage"] = "raw"
    write_json(sidecar, doc)
    assert lock.hash_inputs(derived, ["voice:S02#timing"]) != original
    with pytest.raises(MissingPrerequisite, match="source"):
        takes.for_alignment(derived, take)


def test_voice_stage_polished_alignment_does_not_authorise_source_timing(polished_film, monkeypatch):
    film, _, paced, polished = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    doc = {"sha256": jsonio.sha256(polished), "language": "en", "segments": [],
           "engine": {"name": "openai-whisper", "model": align.whisper_model()}}
    path = film / "voice/align/S02.json"
    write_json(path, doc)
    take = takes.find_takes(film)["S02"]
    assert align.reusable(film, "S02", take) is None
    doc["sha256"] = jsonio.sha256(paced)
    write_json(path, doc)
    assert align.reusable(film, "S02", take)["take_sha256"] == jsonio.sha256(paced)


def test_legacy_source_appearance_changes_selector_and_timing_freshness(polished_film):
    from slate.state import lock

    film, raw, paced, _ = polished_film
    (film / "voice/takes/S02/t01.json").unlink()
    paced.unlink()
    assert takes.for_alignment(film, takes.find_takes(film)["S02"]).path == raw
    before = lock.hash_inputs(film, ["voice:S02#timing"])
    builder.run(film)
    assert not lock.is_stale(film, "timing/vo_words.json")
    write_wav(paced, 3.1, bursts=[(.8, .9)])
    assert takes.for_alignment(film, takes.find_takes(film)["S02"]).path == paced
    assert lock.hash_inputs(film, ["voice:S02#timing"]) != before
    assert lock.is_stale(film, "timing/vo_words.json")


def test_derived_timing_gates_compare_the_resolved_parent_files(polished_film, tmp_path):
    from slate import gates
    from slate.state import lock

    parent, _, paced, polished = polished_film
    derived = tmp_path / "derived"
    derived.mkdir()
    write_json(derived / "film.json", jsonio.read(parent / "film.json"))
    files = [file for file in (parent / "voice").rglob("*") if file.is_file()]
    files.append(parent / "script/storyboard.json")
    lock.add_refs(derived, [{"path": file.relative_to(parent).as_posix(), "sha256": jsonio.sha256(file)}
                            for file in files], parent_id="parent", parent_dir=parent)
    result = builder.run(derived)
    rec = result.docs["vo_words"]["shots"]["S02"]
    assert rec["take"] == polished.relative_to(parent).as_posix()
    assert rec["timing_source"]["file"] == paced.relative_to(parent).as_posix()
    assert paced.relative_to(parent).as_posix() in result.results[0].inputs
    assert not lock.is_stale(derived, "timing/vo_words.json")
    assert all(r.status == "PASS" for r in gates.load_report(derived).values() if r.gate.startswith("timing."))
    write_wav(paced, 3.1, bursts=[(.9, 1.0)])
    assert lock.is_stale(derived, "timing/vo_words.json")
    assert all(r.status == "STALE" for r in gates.load_report(derived).values() if r.gate.startswith("timing."))


def test_inherited_gate_inputs_follow_child_overrides_and_retargeting(polished_film, tmp_path):
    from slate import gates
    from slate.state import lock

    parent, *_ = polished_film
    derived = tmp_path / "derived"
    derived.mkdir()
    rel = "script/storyboard.json"
    lock.add_ref(derived, rel, parent_id="parent", parent_dir=parent, parent_rel=rel,
                 sha256=jsonio.sha256(parent / rel))
    result = gates.GateResult("test.reference", "PASS", "checked", inputs=gates.inputs_for(derived, [rel]))
    gates.write_report(derived, [result])
    assert gates.load_report(derived)["test.reference"].status == "PASS"
    write_json(derived / rel, {"child": "override"})
    assert gates.load_report(derived)["test.reference"].status == "STALE"
    (derived / rel).unlink()
    assert gates.load_report(derived)["test.reference"].status == "PASS"
    other = tmp_path / "other"
    write_json(other / "film.json", {"id": "other"})
    write_json(other / rel, {"retargeted": True})
    lock.add_ref(derived, rel, parent_id="other", parent_dir=other, parent_rel=rel, sha256=jsonio.sha256(other / rel))
    assert gates.load_report(derived)["test.reference"].status == "STALE"


@pytest.mark.parametrize("field,value", [("language", "fr"), ("model", "different")])
def test_voice_stage_reuse_requires_the_requested_language_and_model(polished_film, monkeypatch, field, value):
    film, _, paced, _ = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    doc = {"sha256": jsonio.sha256(paced), "language": "English", "segments": [],
           "engine": {"name": "openai-whisper", "model": align.whisper_model()}}
    path = film / "voice/align/S02.json"
    write_json(path, doc)
    take = takes.find_takes(film)["S02"]
    assert align.reusable(film, "S02", take) is not None
    (doc["engine"] if field == "model" else doc)[field] = value
    write_json(path, doc)
    assert align.reusable(film, "S02", take) is None


def test_forced_alignment_cache_requires_the_same_script_text(polished_film, monkeypatch):
    film, *_ = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    calls = []

    def forced(path, line, *_):
        calls.append(line)
        return {"text": line, "segments": []}, {"engine": "elevenlabs", "model": "fixture", "replay": False}

    monkeypatch.setattr(align, "_elevenlabs_align", forced)
    take = takes.find_takes(film)["S02"]
    align.align_take(film, "S02", take, "first text", engine="elevenlabs")
    align.align_take(film, "S02", take, "first text", engine="elevenlabs")
    assert calls == ["first text"]
    assert align.reusable(film, "S02", take, engine="elevenlabs", line="second text") is None
    align.align_take(film, "S02", take, "second text", engine="elevenlabs")
    assert calls == ["first text", "second text"]


def test_regenerated_valid_provenance_invalidates_old_source_cache(polished_film, monkeypatch):
    film, _, paced, _ = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    take = takes.find_takes(film)["S02"]
    write_json(align.cache_path(film, "whisper", "S02"), {"take_sha256": jsonio.sha256(paced),
               "language": "en", "model": align.whisper_model(), "replay": False, "result": {"segments": []}})
    assert align.reusable(film, "S02", take) is not None
    write_wav(paced, 3.1, bursts=[(1.0, 1.1)])
    sidecar = film / "voice/takes/S02/t01.json"
    doc = jsonio.read(sidecar)
    doc["stages"]["polish"]["source_sha256"] = jsonio.sha256(paced)
    write_json(sidecar, doc)
    assert takes.for_alignment(film, take).path == paced
    assert align.reusable(film, "S02", take) is None


@pytest.mark.parametrize("source_text", ["first text", "other text", None])
def test_forced_voice_stage_reuse_requires_recorded_request_text(polished_film, monkeypatch, source_text):
    film, _, paced, _ = polished_film
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    doc = {"sha256": jsonio.sha256(paced), "segments": [], "text": "first text",
           "engine": {"name": "elevenlabs forced alignment"}}
    if source_text is not None:
        doc["source_text"] = source_text
    write_json(film / "voice/align/S02.json", doc)
    rec = align.reusable(film, "S02", takes.find_takes(film)["S02"], engine="elevenlabs", line="first text")
    assert (rec is not None) == (source_text == "first text")
