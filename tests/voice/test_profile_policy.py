"""VoiceProfile loading and the cloning policy, enforced in code in every mode."""

import json

import pytest

from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.voice import profile

OWNER_CLONE = {"provider": "elevenlabs", "source": "clone", "voice_id": "owner-pvc", "clone_of": "owner",
               "clone_kind": "professional", "verified_by_owner": True}


def test_load_fills_defaults_and_validates():
    p = profile.load({"name": "neutral", "voice": {"voice_id": "v1", "lexicon": ["Slate"]}})
    assert p["brand"] == "neutral" and p["voice_id"] == "v1" and p["source"] == "library"
    assert p["settings"] == {"stability": 0.5, "similarity_boost": 0.75}
    assert p["pace"]["tempo"] == 1.06 and p["fit"] == {"lead_s": 0.4, "tail_s": 0.25}
    with pytest.raises(UsageError):
        profile.load({"name": "x", "voice": {"source": "cloned-by-magic"}})
    with pytest.raises(UsageError):
        profile.load({"name": "x", "voice": {"settings": {"stability": 3}}})


def test_film_overrides_field_by_field():
    brand = {"name": "b", "voice": {**OWNER_CLONE, "lexicon": ["Slate"]}}
    # slate.state's film voice (source "brand", null fields) changes nothing and can't relabel the voice
    p = profile.load(brand, film={"voice": {"source": "brand", "voice_id": "owner-pvc", "model": None,
                                             "settings": None, "note": "from the brand pack"}})
    assert p["source"] == "clone" and p["verified_by_owner"] is True
    # a draft film's "library" note doesn't turn the owner's clone into a library voice either
    p = profile.load(brand, film={"voice": {"source": "library", "voice_id": None}})
    assert p["source"] == "clone"
    # a snapshot of an older brand voice follows the brand (pins, not the snapshot, keep a voiced film stable)
    p = profile.load(brand, film={"voice": {"source": "brand", "voice_id": "older-voice", "model": "older-model"}})
    assert p["voice_id"] == "owner-pvc" and p["model"] is None
    # a film with a voice of its own brings its own claims
    p = profile.load(brand, film={"voice": {"source": "library", "voice_id": "other-library-voice",
                                             "settings": {"stability": 0.4}}})
    assert p["voice_id"] == "other-library-voice" and p["source"] == "library" and p["clone_of"] is None
    assert p["verified_by_owner"] is False and p["settings"]["stability"] == 0.4


def test_library_and_designed_voices_are_the_default_and_allowed():
    for source in ("library", "designed"):
        entry = {"source": source, "voice_id": "v"}
        for mode in ("supervised", "express", "draft", "autonomous"):
            assert profile.policy_problems(entry, mode=mode) == []


def test_owner_verified_professional_clone_is_allowed_in_every_mode():
    for mode in ("supervised", "express", "draft", "autonomous"):
        assert profile.policy_problems(OWNER_CLONE, mode=mode) == []
        profile.enforce(OWNER_CLONE, mode=mode)


def test_someone_elses_clone_is_refused():
    entry = {**OWNER_CLONE, "clone_of": "a colleague"}
    problems = profile.policy_problems(entry)
    assert problems and "only clone" in problems[0] and "real recording" in problems[0]
    with pytest.raises(Refused):
        profile.enforce(entry)


def test_unverified_owner_clone_is_refused():
    entry = {**OWNER_CLONE, "verified_by_owner": False}
    problems = profile.policy_problems(entry)
    assert any("isn't verified" in p for p in problems)
    with pytest.raises(Refused) as exc:
        profile.enforce(entry, mode="supervised")
    assert exc.value.exit_code == 1


def test_instant_clone_is_refused():
    assert any("professional" in p for p in profile.policy_problems({**OWNER_CLONE, "clone_kind": "instant"}))


def test_autonomous_non_owner_clone_is_refused():
    entry = {**OWNER_CLONE, "clone_of": "someone else"}
    problems = profile.policy_problems(entry, mode="autonomous")
    assert any("Autonomous films may use the owner's clone and no other" in p for p in problems)
    with pytest.raises(Refused):
        profile.enforce(entry, mode="autonomous")


def test_public_figures_are_refused_cloned_or_recorded():
    assert profile.policy_problems({"source": "recording", "provider": "recording", "speaker": "a celebrity",
                                    "public_figure": True})
    assert profile.policy_problems({"source": "library", "voice_id": "v", "public_figure": True})


def test_real_recordings_of_someone_else_are_allowed_but_never_synthesized():
    rec = profile.load({"name": "b", "voice": {"source": "recording", "speaker": "a teammate"}})
    assert rec["provider"] == "recording"
    assert profile.policy_problems(rec) == []
    with pytest.raises(Refused) as exc:
        profile.require_voice(rec)
    assert "real recordings" in exc.value.message


def test_vendor_record_catches_a_clone_posing_as_a_library_voice():
    library_claim = {"source": "library", "voice_id": "v"}
    assert profile.vendor_problems({"category": "premade"}, library_claim) == []
    assert profile.vendor_problems({"category": "generated"}, library_claim) == []
    problems = profile.vendor_problems({"category": "cloned", "name": "X"}, library_claim)
    assert problems and "cloned voice" in problems[0]
    assert profile.vendor_problems({"category": "famous", "name": "Y"}, library_claim)
    # ElevenLabs' "cloned" category is an instant clone, refused even for the owner
    assert profile.vendor_problems({"category": "cloned"}, OWNER_CLONE)
    assert profile.vendor_problems({"category": "professional"}, OWNER_CLONE) == []


def test_no_clone_making_method_exists():
    from slate.vendors import elevenlabs

    names = [n for n in dir(elevenlabs.Client) if "clone" in n.lower() or "ivc" in n.lower() or "pvc" in n.lower()]
    assert names == []


def test_profile_command(voice_film, brand, tmp_path):
    code, out, _ = run_cli(["voice", "profile", "--film", str(voice_film), "--json"])
    data = json.loads(out)
    assert code == 0 and data["allowed"] and data["profile"]["voice_id"] == "demo-narrator-01"
    brand["voice"].update({"source": "clone", "clone_of": "a colleague"})
    code, out, _ = run_cli(["voice", "profile", "--film", str(voice_film), "--json"])
    assert code == 1 and not json.loads(out)["allowed"]
    code, out, err = run_cli(["voice", "tts", "--film", str(voice_film)])
    assert code == 1 and "only clone" in err


# -- owner-approved Voice Library voices (Quinn, 2026-10-12) ----------------------------------------------------------

APPROVED_LIBRARY = {"source": "library", "voice_id": "lib-pro-1",
                    "library_approval": {"voice_id": "lib-pro-1", "vendor_category": "professional",
                                         "approved_by": "Quinn", "approved_at": "2026-10-12",
                                         "ruling": "brief/sources/voice-ruling-2026-10-12.md"}}


def test_professional_library_voice_is_refused_without_an_approval():
    problems = profile.vendor_problems({"category": "professional", "name": "Adam", "voice_id": "lib-pro-1"},
                                       {"source": "library", "voice_id": "lib-pro-1"})
    assert problems and "cloned voice" in problems[0]


def test_owner_approved_professional_library_voice_is_allowed():
    info = {"category": "professional", "name": "Adam", "voice_id": "lib-pro-1"}
    for mode in ("supervised", "express", "draft"):
        assert profile.policy_problems(APPROVED_LIBRARY, mode=mode) == []
        assert profile.vendor_problems(info, APPROVED_LIBRARY, mode=mode) == []
    loaded = profile.load({"name": "b", "voice": APPROVED_LIBRARY})
    assert loaded["library_approval"]["approved_by"] == "Quinn"
    assert "approved by Quinn" in profile.summary(loaded)


def test_an_approval_covers_only_the_voice_it_names():
    info = {"category": "professional", "name": "Someone", "voice_id": "lib-pro-2"}
    entry = {**APPROVED_LIBRARY, "voice_id": "lib-pro-2"}
    assert any("covers the one voice it names" in p for p in profile.policy_problems(entry))
    assert profile.vendor_problems(info, entry)


def test_an_approval_never_covers_instant_clones_famous_voices_or_named_people():
    assert profile.vendor_problems({"category": "cloned", "voice_id": "lib-pro-1"}, APPROVED_LIBRARY)
    assert profile.vendor_problems({"category": "famous", "voice_id": "lib-pro-1"}, APPROVED_LIBRARY)
    named = {**APPROVED_LIBRARY, "clone_of": "a colleague"}
    assert profile.policy_problems(named)
    assert profile.vendor_problems({"category": "professional", "voice_id": "lib-pro-1"}, named)
    with pytest.raises(UsageError):
        profile.load({"name": "x", "voice": {**APPROVED_LIBRARY, "library_approval": {
            **APPROVED_LIBRARY["library_approval"], "vendor_category": "cloned"}}})


def test_an_approval_must_be_complete_and_on_a_library_voice():
    incomplete = {**APPROVED_LIBRARY, "library_approval": {"voice_id": "lib-pro-1", "vendor_category": "professional"}}
    assert any("is missing approved_by, approved_at, ruling" in p for p in profile.policy_problems(incomplete))
    on_clone = {**OWNER_CLONE, "library_approval": APPROVED_LIBRARY["library_approval"]}
    assert any("belongs to a library voice" in p for p in profile.policy_problems(on_clone))


def test_autonomous_films_need_the_approval_to_say_so():
    info = {"category": "professional", "voice_id": "lib-pro-1"}
    assert any("autonomous: true" in p for p in profile.policy_problems(APPROVED_LIBRARY, mode="autonomous"))
    assert profile.vendor_problems(info, APPROVED_LIBRARY, mode="autonomous")
    yes = {**APPROVED_LIBRARY, "library_approval": {**APPROVED_LIBRARY["library_approval"], "autonomous": True}}
    assert profile.policy_problems(yes, mode="autonomous") == []
    assert profile.vendor_problems(info, yes, mode="autonomous") == []


def test_a_films_own_voice_carries_its_approval_with_it():
    brand = {"name": "b", "voice": {"source": "library", "voice_id": "brand-voice"}}
    film = {"voice": {**APPROVED_LIBRARY}}
    p = profile.load(brand, film=film)
    assert p["voice_id"] == "lib-pro-1" and p["library_approval"]["voice_id"] == "lib-pro-1"
    # a film note can't borrow an approval for the brand's own voice
    p = profile.load({"name": "b", "voice": APPROVED_LIBRARY}, film={"voice": {"source": "brand", "voice_id": "lib-pro-1"}})
    assert p["library_approval"]["approved_by"] == "Quinn"
