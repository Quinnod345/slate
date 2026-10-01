"""Approving the four references, and what depends on them: onboarding status, autonomous refusal, later films."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import jsonio
from slate.brand import approve, onboard, pack, validate
from slate.errors import Refused, UsageError

from brand_helpers import make_png, make_video, needs_ffmpeg, speechy, write_proof, write_wav

pytestmark = needs_ffmpeg


@pytest.fixture
def ready(studio, tmp_path):
    """The studio pack with every step's inputs in place, nothing approved yet. Returns the files to approve."""
    onboard.truth("studio", source=str(studio / "README.md"), source_id="pack", authority="Pat Lee",
                  authority_org="Studio", claims=["Studio makes short films."], claim_source="pack",
                  banned=["best-in-class"])
    for i, direction in enumerate(("warm stone", "harbour night")):
        onboard.look("studio", moodboards=[str(make_png(tmp_path / f"mb{i}.png", (40 * i, 90, 120)))],
                     direction=direction, grounded_in="pack")
    take = write_wav(tmp_path / "take.wav", speechy(4.0))
    onboard.voice("studio", model="tts-model-x", take=str(take))
    mix, stems = write_proof(tmp_path / "proof", seconds=12.0)
    onboard.sound("studio", proof=str(mix), stems=str(stems))
    return {"truth": studio / onboard.TRUTH_MD, "look_motion": make_video(tmp_path / "look.mov", 12.0),
            "voice": take, "sound": mix, "dir": studio, "tmp": tmp_path}


def _approve_all(r):
    for key in ("truth", "look_motion", "voice", "sound"):
        approve.approve("studio", key, str(r[key]), "Pat Lee")


def test_truth_is_approved_by_the_claim_authority(ready):
    with pytest.raises(Refused) as exc:
        approve.approve("studio", "truth", str(ready["truth"]), "Somebody Else")
    assert "claim authority, Pat Lee" in exc.value.message
    res = approve.approve("studio", "truth", str(ready["truth"]), "pat  lee")
    rec = res["record"]
    assert rec["file"] == onboard.TRUTH_MD  # already in the pack: recorded where it is, not copied
    assert rec["sha256"] == jsonio.sha256(ready["truth"]) and rec["approved_by"] == "pat  lee"
    assert rec["covers"] == ["truth"] and rec["covers_sha256"] == pack.fingerprint(pack.load("studio"), "truth")
    assert rec["approved_at"] and rec["claims"] == 1 and rec["method"] == "slate brand approve"
    assert pack.reference_status(pack.load("studio"), "truth")["approved"]


def test_truth_cant_be_approved_before_its_checklist_is_done(studio, tmp_path):
    onboard.truth("studio", authority="Pat Lee")
    with pytest.raises(Refused) as exc:
        approve.approve("studio", "truth", str(studio / onboard.TRUTH_MD), "Pat Lee")
    assert set(exc.value.details["open"]) >= {"sources", "claims"}


def test_look_motion_is_a_moving_test_with_sound(ready):
    tmp = ready["tmp"]
    for bad, why in ((make_png(tmp / "still.png"), "stills don't count"),
                     (make_video(tmp / "short.mov", 5.0), "runs 5.00 s"),
                     (make_video(tmp / "long.mov", 17.0), "runs 17.00 s"),
                     (make_video(tmp / "mute.mov", 12.0, sound="none"), "has no sound"),
                     (make_video(tmp / "silent.mov", 12.0, sound="silent"), "is silent")):
        with pytest.raises(Refused) as exc:
            approve.approve("studio", "look_motion", str(bad), "Pat Lee")
        assert why in exc.value.message, (bad.name, exc.value.message)
    res = approve.approve("studio", "look", str(ready["look_motion"]), "Pat Lee", note="the second pass")
    rec = res["record"]
    assert rec["file"] == "references/look_motion.mov" and (ready["dir"] / rec["file"]).is_file()
    assert rec["copied_from"] == str(ready["look_motion"].resolve())
    assert rec["duration_s"] == pytest.approx(12.0, abs=0.05) and rec["audio_lufs"] > -70
    assert rec["moodboards"] == ["harbour night", "warm stone"] and rec["note"] == "the second pass"


def test_look_motion_needs_two_grounded_moodboard_directions(studio, tmp_path):
    onboard.look("studio", moodboards=[str(make_png(tmp_path / "a.png"))], direction="only one", grounded_in="pack")
    with pytest.raises(Refused) as exc:
        approve.approve("studio", "look_motion", str(make_video(tmp_path / "t.mov", 12.0)), "Pat Lee")
    assert "moodboards" in exc.value.details["open"]


def test_voice_needs_its_model_settings_and_seed(studio, tmp_path):
    take = write_wav(tmp_path / "take.wav", speechy(3.0))
    with pytest.raises(Refused) as exc:  # the demo's voice leaves the model to the registry
        approve.approve("studio", "voice", str(take), "Pat Lee")
    assert "pinned" in exc.value.details["open"]
    onboard.voice("studio", model="tts-model-x")
    rec = approve.approve("studio", "voice", str(take), "Pat Lee")["record"]
    assert rec["voice"]["model"] == "tts-model-x" and rec["voice"]["seed"] == 2026
    assert rec["voice"]["settings"] == {"stability": 0.5, "similarity_boost": 0.75}
    with pytest.raises(Refused):
        approve.approve("studio", "voice", str(write_wav(tmp_path / "quiet.wav", speechy(3.0, 0.0))), "Pat Lee")


def test_sound_is_approved_only_as_the_proof_that_was_measured(ready):
    other = write_wav(ready["tmp"] / "other.wav", speechy(12.0))
    with pytest.raises(Refused) as exc:
        approve.approve("studio", "sound", str(other), "Pat Lee")
    assert "measured against the voice" in exc.value.message
    rec = approve.approve("studio", "sound", str(ready["sound"]), "Pat Lee")["record"]
    assert set(rec["levels"]) == {"ui", "fx"} and rec["duration_s"] == pytest.approx(12.0, abs=0.01)


def test_all_four_approved_means_onboarded(ready):
    assert pack.status(pack.load("studio"))["status"] == "needs onboarding"
    _approve_all(ready)
    p = pack.load("studio")
    st = pack.status(p)
    assert st["status"] == "onboarded" and st["missing"] == [] and st["next"] is None
    assert pack.references_approved(p) and pack.missing_references(p) == []
    pack.require_onboarded(p)
    lines = (ready["dir"] / approve.HISTORY).read_text().splitlines()
    assert [json.loads(x)["reference"] for x in lines] == ["truth", "look_motion", "voice", "sound"]
    assert _gate_status(validate.validate("studio"), "references") == "PASS"


def _gate_status(results, name):
    return next(r.status for r in results if r.gate == f"brand.{name}")


def test_approve_records_the_sha_and_a_changed_file_unapproves(ready):
    _approve_all(ready)
    kept = ready["dir"] / "references" / "voice.wav"
    assert jsonio.read(ready["dir"] / "brand.json")["references"]["voice"]["sha256"] == jsonio.sha256(kept)
    write_wav(kept, speechy(4.0, 0.25))  # someone swaps the approved take
    p = pack.load("studio")
    st = pack.reference_status(p, "voice")
    assert st["state"] == "changed" and not st["approved"] and "has changed since Pat Lee approved it" in st["reason"]
    assert pack.missing_references(p) == ["voice"] and not pack.references_approved(p)
    assert pack.status(p)["status"] == "needs onboarding"
    assert _gate_status(validate.validate("studio"), "references") == "FAIL"
    with pytest.raises(Refused) as exc:
        pack.require_onboarded(p)
    assert exc.value.details["missing_references"] == ["voice"]


def test_changing_what_an_approval_covers_needs_revise_and_unapproves(ready):
    _approve_all(ready)
    with pytest.raises(Refused) as exc:
        onboard.truth("studio", claims=["A new claim nobody approved."], claim_source="pack")
    assert exc.value.details["would_unapprove"] == ["truth"]
    assert pack.references_approved(pack.load("studio"))  # the refusal changed nothing
    res = onboard.truth("studio", claims=["A new claim nobody approved."], claim_source="pack", revise=True)
    assert res["unapproved"] == ["truth"]
    st = pack.reference_status(pack.load("studio"), "truth")
    # TRUTH.md was rewritten with the new claim, so the file check speaks first; either way it needs a person again
    assert st["state"] in ("changed", "data_changed") and not st["approved"]
    assert pack.missing_references(pack.load("studio")) == ["truth"]
    # the data check on its own: the approved take is untouched, but the voice's settings change
    with pytest.raises(Refused):
        onboard.voice("studio", settings='{"stability": 0.9, "similarity_boost": 0.75}')
    onboard.voice("studio", settings='{"stability": 0.9, "similarity_boost": 0.75}', revise=True)
    st = pack.reference_status(pack.load("studio"), "voice")
    assert st["state"] == "data_changed" and "voice.settings" in st["reason"]
    assert pack.missing_references(pack.load("studio")) == ["truth", "voice"]


def test_editing_the_approved_look_in_brand_json_unapproves_it_but_notes_dont(ready):
    _approve_all(ready)
    raw = jsonio.read(ready["dir"] / "brand.json")
    raw["motion"]["eases"]["arrive"]["lineage"] = "a clearer note about where it comes from"
    raw["palette"]["notes"] = "reworded"
    (ready["dir"] / "brand.json").write_text(json.dumps(raw, indent=1))
    assert pack.reference_status(pack.load("studio"), "look_motion")["approved"]  # documentation isn't the look
    raw["motion"]["eases"]["arrive"]["bezier"] = [0.25, 0.9, 0.3, 1]
    (ready["dir"] / "brand.json").write_text(json.dumps(raw, indent=1))
    assert pack.reference_status(pack.load("studio"), "look_motion")["state"] == "data_changed"
    raw["motion"]["eases"]["arrive"]["bezier"] = [0.2, 0.9, 0.3, 1]
    (ready["dir"] / "brand.json").write_text(json.dumps(raw, indent=1))
    assert pack.reference_status(pack.load("studio"), "look_motion")["approved"]
    (ready["dir"] / "logos" / "mark.svg").write_text((ready["dir"] / "logos" / "mark.svg").read_text()
                                                     .replace("#A4532F", "#3355AA"))
    assert pack.reference_status(pack.load("studio"), "look_motion")["state"] == "data_changed"  # the artwork


def test_a_missing_approved_file_stays_approved_and_is_flagged(ready):
    _approve_all(ready)
    (ready["dir"] / "references" / "sound.wav").unlink()
    st = pack.reference_status(pack.load("studio"), "sound")
    assert st["approved"] and not st["file_present"] and "isn't here" in st["reason"]
    assert _gate_status(validate.validate("studio"), "references") == "REVIEW"


def test_a_later_approval_keeps_the_earlier_file(ready):
    approve.approve("studio", "voice", str(ready["voice"]), "Pat Lee")
    second = write_wav(ready["tmp"] / "take2.wav", speechy(4.0, 0.2))
    approve.approve("studio", "voice", str(second), "Pat Lee")
    history = list((ready["dir"] / "references" / "history").glob("voice-*.wav"))
    assert len(history) == 1 and jsonio.sha256(history[0]) == jsonio.sha256(ready["voice"])


def test_hand_written_records_without_a_hash_dont_count():
    p = {"name": "x", "references": {"truth": {"file": "t.md", "approved_by": "Someone"}}}
    assert pack.reference_status(p, "truth")["state"] == "incomplete"
    p = {"name": "x", "references": {"truth": {"file": "t.md", "sha256": "0" * 64, "approved_by": ""}}}
    assert pack.reference_status(p, "truth")["state"] == "not_approved"


def test_the_repo_packs_and_bad_input_are_refused(studio, tmp_path):
    f = write_wav(tmp_path / "x.wav", speechy(3.0))
    with pytest.raises(Refused):
        approve.approve("demo", "voice", str(f), "Pat Lee")
    with pytest.raises(UsageError):
        approve.approve("studio", "vibe", str(f), "Pat Lee")
    with pytest.raises(UsageError):
        approve.approve("studio", "voice", str(tmp_path / "nope.wav"), "Pat Lee")
    with pytest.raises(UsageError):
        approve.approve("studio", "voice", str(f), "  ")


# -------------------------------------------------------------------------------------- what depends on approval
def _format(tmp: Path) -> Path:
    from format_fixtures import write_format  # a real, valid format: slate new validates format files

    return write_format(tmp / "fmt-reel.json", name="reel-test", width=360, height=640, fps=(25, 1), runtime_s=15,
                        elevenlabs=1000, higgsfield=0)


def test_autonomous_films_refuse_a_brand_until_all_four_are_approved(ready):
    from slate.state import film

    fmt = _format(ready["tmp"])
    with pytest.raises(Refused) as exc:
        film.create("auto-1", brand="studio", format=str(fmt), mode="autonomous", git=False)
    assert "four references" in exc.value.message and exc.value.details["missing_references"] == list(pack.REFERENCES)
    _approve_all(ready)
    made = film.create("auto-2", brand="studio", format=str(fmt), mode="autonomous", git=False)
    assert (made / "film.json").is_file()


def test_the_spend_guard_sees_the_same_answer(ready):
    from slate import ledger

    assert ledger.brand_approved("studio") is False
    _approve_all(ready)
    assert ledger.brand_approved("studio") is True


def test_an_approved_look_skips_a_films_look_test(ready):
    from slate.state import approvals, film

    fmt = _format(ready["tmp"])
    f1 = film.create("sup-1", brand="studio", format=str(fmt), git=False)
    assert "look_test" not in approvals.covered_by_brand(f1)
    approve.approve("studio", "look_motion", str(ready["look_motion"]), "Pat Lee")
    assert "look_test" in approvals.covered_by_brand(f1)


def test_a_client_brands_use_is_approved_by_its_named_approver_there(store, tmp_path):
    """A film for a client is the client's: onboarding names an approver there for claims and brand use."""
    from slate.brand import create

    create.init("partner", from_pack="demo", organisation="Partner Co")
    for i, d in enumerate(("one", "two")):
        onboard.look("partner", moodboards=[str(make_png(tmp_path / f"p{i}.png"))], direction=d, grounded_in="pack")
    film = make_video(tmp_path / "look.mov", 12.0)
    with pytest.raises(Refused) as exc:
        approve.approve("partner", "look_motion", str(film), "Pat Lee")
    assert "nobody is named yet" in exc.value.message and "--authority-org" in exc.value.hint
    onboard.truth("partner", authority="Sam Doe", authority_org="Partner Co")
    with pytest.raises(Refused) as exc:
        approve.approve("partner", "look_motion", str(film), "Pat Lee")
    assert "approver at Partner Co, Sam Doe" in exc.value.message
    rec = approve.approve("partner", "look_motion", str(film), "Sam Doe")["record"]
    assert rec["approved_by"] == "Sam Doe"


def test_an_autonomous_film_parks_on_a_claim_that_isnt_approved(ready):
    from slate.state import film, modes

    _approve_all(ready)
    auto = film.create("auto-claims", brand="studio", format=str(_format(ready["tmp"])), mode="autonomous", git=False)
    assert modes.check_claims(auto, ["Studio makes short films."]) == []  # on the approved list
    assert modes.parked(auto) == []
    new = modes.check_claims(auto, ["Studio wins every award."])
    assert new == ["Studio wins every award."]
    questions = modes.parked(auto)
    assert questions and "Studio wins every award." in json.dumps(questions)  # parked with a question for a person
