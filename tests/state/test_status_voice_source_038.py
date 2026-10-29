"""Malformed film voice data cannot inherit a standing brand cast approval through normalization."""

from __future__ import annotations

import copy
import sys

import pytest

from slate import jsonio, procs
from slate.brand import pack as brand_pack
from slate.state import approvals, film
from slate.voice import profile, tts


@pytest.fixture
def source_brand_038(state_env, monkeypatch):
    """Keep approval authority real while every reference and film lives in neutral temporary folders."""
    def no_process(*args, **kwargs):
        raise AssertionError("Cast-coverage checks must not start a process or contact a voice provider.")

    monkeypatch.setattr(procs, "run", no_process)
    monkeypatch.setitem(sys.modules, "slate.brand.pack", brand_pack)
    folder = state_env["brands"] / "onboarded-brand"
    raw = jsonio.read(folder / "brand.json")
    raw["voice"].update(source="library", seed=71)
    for key, rec in raw["references"].items():
        reference = folder / rec["file"]
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(f"Neutral approved {key} reference.".encode())
        rec["sha256"] = jsonio.sha256(reference)
    jsonio.write_atomic(folder / "brand.json", raw)
    loaded = brand_pack.load("onboarded-brand")
    for key, rec in raw["references"].items():
        rec["covers_sha256"] = brand_pack.fingerprint(loaded, key)
    jsonio.write_atomic(folder / "brand.json", raw)
    assert brand_pack.missing_references(brand_pack.load("onboarded-brand")) == []
    return folder


@pytest.fixture
def source_film_038(source_brand_038, new_film):
    def make(*, mode="supervised"):
        fd = new_film("voice-source-038", brand="onboarded-brand", mode=mode, format="fixture-reel",
                      request="Make a neutral narrated card.", request_source="Neutral Fixture Owner")
        expected = profile.for_film(fd)
        film.update(fd, lambda fj: fj["pins"].update({**tts.requested_pin(expected, fj), "seed.S01": 71}))
        return fd, expected

    return make


def _set_voice(fd, raw):
    """Simulate a loaded legacy or damaged manifest; the normal film writer rejects malformed input already."""
    fj = film.load(fd)
    fj["voice"] = copy.deepcopy(raw)
    jsonio.write_atomic(fd / "film.json", fj)


def _coverage(fd, *, cast):
    """Coverage is read-only and answers only the standing reference scopes that remain current."""
    before = {name: (fd / name).read_bytes() for name in ("film.json", "approvals.json")}
    pins_before = copy.deepcopy(film.load(fd)["pins"])
    mode = film.mode_of(fd)
    covered = approvals.covered_by_brand(fd)
    expected = {"look_test"}
    if mode == "autonomous":
        expected.add("claims")
    if cast:
        expected.add("voice_shortlist")
    assert set(covered) == expected
    sittings = approvals.sittings(fd)
    if mode != "draft":
        sitting = sittings["preproduction" if mode == "supervised" else "final_cut"]
        assert ("voice_shortlist" in sitting["approved"]) is cast
        assert ("voice_shortlist" in sitting["pending"]) is not cast
        assert {"checklist", "script"}.issubset(sitting["pending"])
        assert "final_cut" in sittings["final_cut"]["pending"]
    assert approvals.records(fd) == []
    assert film.load(fd)["pins"] == pins_before
    assert {name: (fd / name).read_bytes() for name in before} == before
    return covered


@pytest.mark.parametrize("mode", ["supervised", "express", "draft", "autonomous"])
@pytest.mark.parametrize("source", ["invalid-source", None, ["library"], True],
                         ids=["unknown", "null", "list", "boolean"])
def test_malformed_source_cannot_restore_brand_cast(source_film_038, source, mode):
    fd, expected = source_film_038(mode=mode)
    _set_voice(fd, {"source": source, "voice_id": "different-neutral-voice"})
    # The loader still follows the brand for these malformed records; that fallback is not approval evidence.
    assert profile.for_film(fd)["voice_id"] == expected["voice_id"]
    _coverage(fd, cast=False)


@pytest.mark.parametrize("source", ["", 7, {"source": "library"}], ids=["empty", "number", "object"])
def test_other_malformed_source_shapes_cannot_restore_brand_cast(source_film_038, source):
    fd, expected = source_film_038()
    _set_voice(fd, {"source": source, "voice_id": "different-neutral-voice"})
    assert profile.for_film(fd)["voice_id"] == expected["voice_id"]
    _coverage(fd, cast=False)


@pytest.mark.parametrize("source", ["invalid-source", None, ["library"], True],
                         ids=["unknown", "null", "list", "boolean"])
def test_matching_nested_profile_does_not_launder_malformed_source(source_film_038, source):
    fd, expected = source_film_038()
    _set_voice(fd, {"source": source, "profile": expected})
    assert profile.for_film(fd) == expected
    _coverage(fd, cast=False)


@pytest.mark.parametrize("field,value", [
    ("provider", True), ("voice_id", ["different-neutral-voice"]), ("model", 7),
    ("settings", []), ("note", {"text": "invalid raw note"}),
])
def test_malformed_snapshot_fields_cannot_restore_brand_cast(source_film_038, field, value):
    fd, expected = source_film_038()
    _set_voice(fd, {"source": "brand", field: value})
    assert profile.for_film(fd) == expected
    _coverage(fd, cast=False)


@pytest.mark.parametrize("representation", [
    "missing", "null", "empty", "brand", "nullable-brand", "unset-snapshot", "source-omitted",
])
def test_supported_inherited_voice_forms_keep_exact_brand_cast(source_film_038, representation):
    fd, expected = source_film_038()
    forms = {
        "null": None,
        "empty": {},
        "brand": {"source": "brand"},
        "nullable-brand": {"source": "brand", "provider": None, "voice_id": None, "model": None,
                           "settings": None, "note": None},
        "unset-snapshot": {"source": "unset", "voice_id": "old-neutral-snapshot", "model": "old-model"},
        "source-omitted": {"voice_id": None, "settings": None},
    }
    if representation == "missing":
        fj = film.load(fd)
        fj.pop("voice")
        jsonio.write_atomic(fd / "film.json", fj)
    else:
        _set_voice(fd, forms[representation])
    assert profile.for_film(fd) == expected
    _coverage(fd, cast=True)


@pytest.mark.parametrize("selected", [False, True], ids=["empty-default", "selected-id"])
def test_legacy_string_voice_form_keeps_loader_contract(source_film_038, selected):
    fd, expected = source_film_038()
    _set_voice(fd, expected["voice_id"] if selected else "")
    assert profile.for_film(fd) == expected
    _coverage(fd, cast=True)


@pytest.mark.parametrize("source", ["library", "designed"])
def test_valid_same_choice_source_keeps_inherited_brand_claims(source_film_038, source):
    fd, expected = source_film_038()
    _set_voice(fd, {"source": source, "voice_id": expected["voice_id"]})
    assert profile.for_film(fd) == expected
    _coverage(fd, cast=True)


@pytest.mark.parametrize("representation", ["empty", "partial", "complete"])
def test_valid_matching_nested_profiles_keep_exact_brand_cast(source_film_038, representation):
    fd, expected = source_film_038()
    nested = {"empty": {}, "partial": {"settings": {"stability": 0.4}}, "complete": expected}[representation]
    _set_voice(fd, {"source": "brand", "profile": nested})
    assert profile.for_film(fd) == expected
    _coverage(fd, cast=True)


@pytest.mark.parametrize("representation", ["selected", "legacy", "nested"])
def test_valid_different_choices_do_not_inherit_brand_cast(source_film_038, representation):
    fd, _ = source_film_038()
    voice_id = "different-neutral-voice"
    raw = {"selected": {"source": "library", "voice_id": voice_id}, "legacy": voice_id,
           "nested": {"source": "brand", "profile": {"voice_id": voice_id}}}[representation]
    _set_voice(fd, raw)
    assert profile.for_film(fd)["voice_id"] == voice_id
    _coverage(fd, cast=False)


@pytest.mark.parametrize("nested", [None, [], True, "invalid-profile"], ids=["null", "list", "boolean", "string"])
def test_malformed_nested_profiles_do_not_inherit_brand_cast(source_film_038, nested):
    fd, _ = source_film_038()
    _set_voice(fd, {"source": "brand", "profile": nested})
    _coverage(fd, cast=False)


def test_valid_raw_voice_still_requires_exact_actual_pins(source_film_038):
    fd, expected = source_film_038()
    _set_voice(fd, {"source": "library", "voice_id": expected["voice_id"]})
    film.update(fd, lambda fj: fj["pins"].update(voice_id="different-neutral-voice"))
    _coverage(fd, cast=False)


@pytest.mark.parametrize("mode", ["supervised", "express", "draft", "autonomous"])
def test_valid_cast_reuse_cannot_approve_spend_or_film_artifacts(source_film_038, mode):
    fd, _ = source_film_038(mode=mode)
    covered = _coverage(fd, cast=True)
    assert {"checklist", "script", "take", "spend", "final_cut"}.isdisjoint(covered)
    assert approvals.spend_covers(fd, "unapproved-spend", "voice/takes/S01/t01.wav") is False
    assert not (fd / "voice" / "takes.json").exists()
