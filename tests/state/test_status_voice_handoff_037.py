"""A standing cast approval selects a narrator; it never creates this film's narration."""

from __future__ import annotations

import copy
import sys

import pytest

from slate import gates, jsonio, procs
from slate.brand import pack as brand_pack
from slate.errors import UsageError
from slate.state import approvals, deps, film, lock, pins, status
from slate.voice import profile, tts


@pytest.fixture
def chosen_brand(state_env, monkeypatch):
    """Use the real reference authority on temporary neutral data, including its exact file and profile hashes."""
    monkeypatch.setitem(sys.modules, "slate.brand.pack", brand_pack)
    folder = state_env["brands"] / "onboarded-brand"
    raw = jsonio.read(folder / "brand.json")
    raw["voice"].update(source="library", seed=71)
    reference = folder / "refs" / "voice.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"neutral approved voice reference")
    raw["references"]["voice"].update(
        sha256=jsonio.sha256(reference), covers_sha256=brand_pack.fingerprint(raw, "voice"))
    jsonio.write_atomic(folder / "brand.json", raw)
    return folder


@pytest.fixture
def narrated_film(chosen_brand, new_film, script_writer):
    def make(*, mode="supervised", shots=2, film_id="voice-handoff"):
        fd = new_film(film_id, brand="onboarded-brand", mode=mode, format="fixture-reel",
                      request="Make a short planner film.", request_source="Test Person")
        script_writer(fd, shots=shots)
        jsonio.write_atomic(fd / "script" / "storyboard.json", jsonio.read(fd / "script" / "script.json"))
        lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
        gates.write_report(fd, [gates.GateResult.pass_("claims", "every claim sourced")])
        return fd

    return make


def _voice(st):
    return next(row for row in st["stages"] if row["stage"] == "voice")


def _approve_preproduction(fd):
    for what, path in (("checklist", "brief/GOAL.md"), ("claims", "script/script.json"),
                       ("script", "script/storyboard.json")):
        approvals.record(fd, what=what, path=path, approved_by="Test Person")


def _selected_take(fd, shot, *, metadata=True):
    """Record selected neutral bytes; status must inspect records without decoding audio or calling TTS."""
    raw = f"voice/takes/{shot}/t01.wav"
    sidecar = f"voice/takes/{shot}/t01.json"
    path = fd / raw
    path.parent.mkdir(parents=True)
    path.write_bytes(f"neutral selected audio for {shot}".encode())
    lock.record(fd, raw, producer="slate voice tts", inputs=["script/storyboard.json"])
    manifest = jsonio.read(fd / "voice" / "takes.json", default={"shots": {}})
    manifest["shots"][shot] = {"selected": "t01", "takes": {"t01": {"raw": raw, "json": sidecar}},
                              "approved": None}
    jsonio.write_atomic(fd / "voice" / "takes.json", manifest)
    lock.record(fd, "voice/takes.json", producer="slate voice tts", inputs=[])
    if metadata:
        text = next(s["vo"] for a in jsonio.read(fd / "script" / "storyboard.json")["acts"]
                    for s in a["shots"] if s["id"] == shot)
        prof = profile.for_film(fd)
        requested = tts.requested_pin(prof, film.load(fd))
        for key, value in requested.items():
            pins.pin(fd, key, value)
        pins.pin(fd, f"seed.{shot}", prof["seed"])
        jsonio.write_atomic(fd / sidecar, {"shot": shot, "take": "t01", "text": text,
                                         "file": raw, "sha256": jsonio.sha256(path),
                                         "voice_id": requested["voice_id"], "model": requested["tts"],
                                         "voice_settings": requested["voice_settings"], "seed": prof["seed"]})
        lock.record(fd, sidecar, producer="slate voice tts", inputs=[])


@pytest.mark.parametrize("mode", ["supervised", "express", "draft", "autonomous"])
def test_exact_approved_brand_cast_is_reused_in_every_mode(narrated_film, mode):
    fd = narrated_film(mode=mode)
    covered = approvals.covered_by_brand(fd)
    assert "voice_shortlist" in covered
    sitting = "preproduction" if mode == "supervised" else "final_cut"
    if mode != "draft":
        assert "voice_shortlist" in approvals.sittings(fd)[sitting]["approved"]
    assert approvals.records(fd, "take") == []
    assert approvals.records(fd, "spend") == []
    assert approvals.records(fd, "final_cut") == []


def test_brand_choice_without_narration_reaches_preproduction_first(narrated_film):
    fd = narrated_film()
    st = status.compute(fd)
    voice = _voice(st)
    assert voice["state"] == "missing" and not voice["ok"]
    assert voice["command"] == ["voice", "tts"]
    assert st["next"]["sitting"] == "preproduction" and st["next"]["who"] == "person"
    assert st["sittings"]["preproduction"]["pending"] == ["checklist", "claims", "script"]


@pytest.mark.parametrize("mode", ["supervised", "express", "draft", "autonomous"])
def test_brand_choice_hands_off_to_tts_without_recasting(narrated_film, mode):
    fd = narrated_film(mode=mode)
    if mode == "supervised":
        _approve_preproduction(fd)
    st = status.compute(fd)
    assert st["next"]["command"] == "slate voice tts" and st["next"]["stage"] == "voice"
    assert not _voice(st)["ok"] and not st["ok"]


@pytest.mark.parametrize("field,value", [
    ("voice_id", "another-neutral-voice"), ("source", "designed"), ("provider", "recording"),
    ("model", "another-neutral-model"), ("settings", {"stability": 0.9}), ("seed", 72),
    ("clone_of", "someone-else"), ("clone_kind", "instant"), ("verified_by_owner", True),
    ("public_figure", True), ("speaker", "another speaker"),
])
def test_effective_film_override_does_not_inherit_brand_cast(narrated_film, field, value):
    fd = narrated_film(mode="autonomous")
    film.update(fd, lambda fj: fj["voice"].update(profile={field: value}))
    assert "voice_shortlist" not in approvals.covered_by_brand(fd)
    assert "voice_shortlist" in approvals.sittings(fd)["final_cut"]["pending"]


@pytest.mark.parametrize("key,value", [
    ("voice_id", "another-neutral-voice"), ("tts", "another-neutral-model"),
    ("voice_settings", {"stability": 0.9}), ("seed.S01", 72),
    ("voice_settings", "malformed"), ("tts", None), ("seed.S01", True),
])
def test_actual_tts_pins_must_match_the_approved_choice(narrated_film, key, value):
    fd = narrated_film(mode="autonomous")
    film.update(fd, lambda fj: fj["pins"].update({key: value}))
    assert "voice_shortlist" not in approvals.covered_by_brand(fd)


def test_matching_pins_and_normalized_defaults_keep_brand_cast(narrated_film):
    fd = narrated_film()
    prof = profile.for_film(fd)
    for key, value in tts.requested_pin(prof, film.load(fd)).items():
        pins.pin(fd, key, value)
    pins.pin(fd, "seed.S01", 71)
    pins.pin(fd, "seed.S02", 71)
    film.update(fd, lambda fj: fj["voice"].update(profile={"settings": {"stability": 0.4}}))
    assert "voice_shortlist" in approvals.covered_by_brand(fd)


def test_brand_snapshot_uses_effective_profile_not_old_snapshot(narrated_film):
    fd = narrated_film()
    film.update(fd, lambda fj: fj["voice"].update(voice_id="old-neutral-snapshot", model="old-model"))
    assert profile.for_film(fd)["voice_id"] == "fixture-voice-0002"
    assert "voice_shortlist" in approvals.covered_by_brand(fd)


def test_same_id_with_changed_clone_claims_is_not_brand_reuse(narrated_film):
    fd = narrated_film(mode="autonomous")
    film.update(fd, lambda fj: fj["voice"].update(profile={"source": "clone", "clone_of": "owner",
                                                       "clone_kind": "professional", "verified_by_owner": True}))
    assert "voice_shortlist" not in approvals.covered_by_brand(fd)


def test_model_override_cannot_bypass_an_approved_default_model(narrated_film, chosen_brand):
    raw = jsonio.read(chosen_brand / "brand.json")
    raw["voice"]["model"] = None
    raw["references"]["voice"]["covers_sha256"] = brand_pack.fingerprint(raw, "voice")
    jsonio.write_atomic(chosen_brand / "brand.json", raw)
    fd = narrated_film(mode="autonomous")
    assert "voice_shortlist" in approvals.covered_by_brand(fd)
    film.update(fd, lambda fj: fj["models"].update(tts="another-neutral-model"))
    assert "voice_shortlist" not in approvals.covered_by_brand(fd)


def test_a_reference_without_a_selected_voice_is_not_a_cast(narrated_film, chosen_brand):
    fd = narrated_film()
    raw = jsonio.read(chosen_brand / "brand.json")
    raw["voice"]["voice_id"] = None
    raw["references"]["voice"]["covers_sha256"] = brand_pack.fingerprint(raw, "voice")
    jsonio.write_atomic(chosen_brand / "brand.json", raw)
    assert "voice_shortlist" not in approvals.covered_by_brand(fd)
    assert status.compute(fd)["next"]["command"] == "slate voice cast"


@pytest.mark.parametrize("mutation", ["reference_bytes", "reference_fingerprint", "invalid_profile", "unreadable"])
def test_changed_or_unreadable_brand_voice_covers_no_cast(narrated_film, chosen_brand, mutation, monkeypatch):
    fd = narrated_film(mode="autonomous")
    raw = jsonio.read(chosen_brand / "brand.json")
    if mutation == "reference_bytes":
        (chosen_brand / "refs" / "voice.wav").write_bytes(b"different reference")
    elif mutation == "reference_fingerprint":
        raw["voice"]["seed"] = 99
        jsonio.write_atomic(chosen_brand / "brand.json", raw)
    elif mutation == "invalid_profile":
        raw["voice"]["settings"]["stability"] = 2
        raw["references"]["voice"]["covers_sha256"] = brand_pack.fingerprint(raw, "voice")
        jsonio.write_atomic(chosen_brand / "brand.json", raw)
    else:
        def unreadable(_name):
            raise UsageError("neutral profile unavailable")
        monkeypatch.setattr(deps, "brand_load", unreadable)
    assert "voice_shortlist" not in approvals.covered_by_brand(fd)


def test_selected_film_shortlist_is_not_narration(narrated_film):
    fd = narrated_film()
    film.update(fd, lambda fj: fj["voice"].update(source="library", voice_id="own-neutral-voice"))
    jsonio.write_atomic(fd / "voice" / "shortlist.json", {"selected": "own-neutral-voice"})
    lock.record(fd, "voice/shortlist.json", producer="slate voice cast", inputs=[])
    _approve_preproduction(fd)
    before = status.compute(fd)
    assert not _voice(before)["ok"] and before["next"]["sitting"] == "preproduction"
    assert before["sittings"]["preproduction"]["pending"] == ["voice_shortlist"]
    approvals.record(fd, what="voice_shortlist", path="voice/shortlist.json", approved_by="Test Person")
    after = status.compute(fd)
    assert after["next"]["command"] == "slate voice tts" and not _voice(after)["ok"]


@pytest.mark.parametrize("artifact", ["direction", "casting", "unrelated_take"])
def test_voice_folder_artifacts_do_not_complete_narration(narrated_film, artifact):
    fd = narrated_film()
    _approve_preproduction(fd)
    if artifact == "direction":
        jsonio.write_atomic(fd / "voice" / "direction.json", {"lines": {"S01": "[warm] Line 1."}})
    elif artifact == "casting":
        jsonio.write_atomic(fd / "voice" / "shortlist.json", {"ranking": []})
    else:
        _selected_take(fd, "S99", metadata=False)
    st = status.compute(fd)
    assert not _voice(st)["ok"] and st["next"]["command"] == "slate voice tts"


def test_partially_voiced_film_keeps_missing_lines_open(narrated_film):
    fd = narrated_film()
    _approve_preproduction(fd)
    _selected_take(fd, "S01")
    st = status.compute(fd)
    voice = _voice(st)
    assert not voice["ok"] and voice["narration"]["missing"] == ["S02"]
    assert st["next"]["command"] == "slate voice tts"


def test_complete_selected_reads_allow_timing_without_claiming_take_approval(narrated_film):
    fd = narrated_film()
    _approve_preproduction(fd)
    _selected_take(fd, "S01")
    _selected_take(fd, "S02")
    st = status.compute(fd)
    voice = _voice(st)
    assert voice["ok"] and voice["narration"]["missing"] == []
    assert voice["state"] == "fresh" and st["next"]["command"] == "slate timing"
    assert approvals.records(fd, "take") == [] and not st["sittings"]["final_cut"]["done"]


def test_status_reads_narration_without_processes_or_changing_its_files(narrated_film, monkeypatch):
    fd = narrated_film()
    _approve_preproduction(fd)
    _selected_take(fd, "S01")
    _selected_take(fd, "S02")
    protected = [fd / "film.json", fd / "approvals.json", *(fd / "voice").rglob("*")]
    before = {str(path): jsonio.sha256(path) for path in protected if path.is_file()}

    def no_process(*_args, **_kwargs):
        raise AssertionError("Reading the voice handoff must not start TTS, decoding or any other process.")

    monkeypatch.setattr(procs, "run", no_process)
    st = status.compute(fd)
    assert _voice(st)["ok"] and st["next"]["command"] == "slate timing"
    assert {str(path): jsonio.sha256(path) for path in protected if path.is_file()} == before


@pytest.mark.parametrize("problem", ["absent_sidecar", "changed_text", "changed_audio", "missing_selected_audio",
                                    "missing_tts_pin", "mismatched_sidecar_selection", "unrecorded_selected_audio",
                                    "malformed_tts_pin"])
def test_unproved_selected_read_requires_check_before_more_tts(narrated_film, problem):
    fd = narrated_film(shots=1)
    _approve_preproduction(fd)
    _selected_take(fd, "S01", metadata=problem != "absent_sidecar")
    sidecar = fd / "voice" / "takes" / "S01" / "t01.json"
    audio = sidecar.with_suffix(".wav")
    if problem == "changed_text":
        record = jsonio.read(sidecar)
        record["text"] = "Old line."
        jsonio.write_atomic(sidecar, record)
    elif problem == "changed_audio":
        audio.write_bytes(b"changed selected audio")
    elif problem == "missing_selected_audio":
        audio.unlink()
    elif problem == "missing_tts_pin":
        film.update(fd, lambda fj: fj["pins"].pop("voice_settings"))
    elif problem == "mismatched_sidecar_selection":
        record = jsonio.read(sidecar)
        record["shot"] = "S99"
        jsonio.write_atomic(sidecar, record)
    elif problem == "unrecorded_selected_audio":
        data = lock.load(fd)
        del data["files"]["voice/takes/S01/t01.wav"]
        jsonio.write_atomic(fd / "film.lock.json", data)
    elif problem == "malformed_tts_pin":
        film.update(fd, lambda fj: fj["pins"].update(voice_settings="malformed"))
    st = status.compute(fd)
    expected = "slate voice profile" if problem == "malformed_tts_pin" else "slate voice check"
    assert not _voice(st)["ok"] and st["next"]["command"] == expected


def test_legacy_read_does_not_prove_every_narrated_line(narrated_film):
    fd = narrated_film(shots=1)
    _approve_preproduction(fd)
    raw = fd / "voice" / "takes" / "S01.wav"
    raw.parent.mkdir()
    raw.write_bytes(b"neutral legacy read")
    lock.record(fd, "voice/takes/S01.wav", producer="slate voice tts", inputs=[])
    st = status.compute(fd)
    assert not _voice(st)["ok"] and st["next"]["command"] == "slate voice check"


def test_inherited_selected_reads_use_parent_files_and_remain_unapproved(narrated_film):
    parent = narrated_film(film_id="voice-parent")
    _selected_take(parent, "S01")
    _selected_take(parent, "S02")
    child = narrated_film(film_id="voice-child")
    for rel in [r for r in lock.load(parent)["files"] if r.startswith("voice/")]:
        lock.add_ref(child, rel, parent_id="voice-parent", parent_dir=parent, parent_rel=rel,
                     sha256=jsonio.sha256(parent / rel))
    film.update(child, lambda fj: fj["pins"].update(film.load(parent)["pins"]))
    _approve_preproduction(child)
    st = status.compute(child)
    assert _voice(st)["ok"] and st["next"]["command"] == "slate timing"
    assert approvals.records(child, "take") == []
    assert not (child / "voice" / "takes.json").exists()


def test_explicitly_silent_storyboard_does_not_request_tts(narrated_film):
    fd = narrated_film()
    sb = jsonio.read(fd / "script" / "script.json")
    for act in sb["acts"]:
        for shot in act["shots"]:
            shot["vo"] = ""
    jsonio.write_atomic(fd / "script" / "script.json", copy.deepcopy(sb))
    jsonio.write_atomic(fd / "script" / "storyboard.json", sb)
    lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
    _approve_preproduction(fd)
    st = status.compute(fd)
    assert _voice(st)["state"] == "skipped" and _voice(st)["ok"]
    assert st["next"]["command"] == "slate timing"
