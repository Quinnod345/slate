"""The four modes as rules: settings(mode) and check(film_dir, action), plus parking."""

from __future__ import annotations

import json

import pytest

from slate import config, gates, ledger
from slate.cli import run_cli
from slate.errors import Refused, SpendRefused, UsageError
from slate.state import approvals, film, modes, park, status


def test_settings_are_data():
    render = config.load()["render"]
    sup = modes.settings("supervised")
    assert sup["blocking_sittings"] == ["preproduction", "final_cut"] and sup["deliver_final"]
    assert sup["motion_blur_samples"] == render["motion_blur_samples"]
    ex = modes.settings("express")
    assert ex["blocking_sittings"] == ["final_cut"] and ex["batch_approvals_into_final_cut"]
    dr = modes.settings("draft")
    assert dr["plates"] is False and dr["deliver_final"] is False and dr["voice_design"] is False
    assert dr["motion_blur_samples"] == render["draft_samples"] and dr["master"] == "ffmpeg"
    au = modes.settings("autonomous")
    assert au["after_effects"] is False and au["higgsfield_approval_each_call"] and au["brand_references_required"]
    assert au["default_output"] == {"aspect": "9:16", "runtime_s": [15.0, 45.0]} and au["held_for_person"]
    with pytest.raises(UsageError):
        modes.settings("fast")


def test_autonomous_refuses_after_effects_and_unapproved_brands(new_film):
    fd = new_film("auto", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    for action in ("ae", "ae_build", "ae_master", "ae_finish", "ae_render"):
        with pytest.raises(Refused) as exc:
            modes.check(fd, action)
        assert "After Effects" in exc.value.message
    ok, why = modes.allowed(fd, "ae")
    assert not ok and "After Effects" in why
    with pytest.raises(Refused):
        new_film("auto2", mode="autonomous", brand="open-brand")
    sup = new_film("sup")
    assert modes.check(sup, "ae_build")["allowed"]
    with pytest.raises(Refused):
        film.set_value(sup, "mode", "autonomous")  # switching to autonomous re-checks the brand


def test_draft_is_never_final_and_has_no_plates(new_film):
    fd = new_film("drafty", mode="draft")
    for action in ("deliver_final", "final", "publish"):
        with pytest.raises(Refused):
            modes.check(fd, action, path="masters/film.mov")
    with pytest.raises(Refused):
        modes.check(fd, "approve", what="final_cut")
    with pytest.raises(Refused):
        modes.check(fd, "plate")
    with pytest.raises(Refused):
        modes.check(fd, "voice_design")
    with pytest.raises(Refused):
        modes.check(fd, "ae_master")
    assert modes.check(fd, "ae_build")["allowed"]


def test_generation_rules(new_film):
    none = new_film("nogen", generation="none")
    with pytest.raises(Refused):
        modes.check(none, "generate")
    sup = new_film("gen")
    assert modes.check(sup, "generate")["allowed"]
    auto = new_film("autogen", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    with pytest.raises(SpendRefused):
        modes.check(auto, "generate")  # every generation waits for a person's yes
    with pytest.raises(SpendRefused):
        modes.check(auto, "generate", approval_id="ap_0123456789")  # an id nobody recorded
    yes = approvals.record(auto, what="spend", path="scenes/plates/S03.mov", approved_by="Test Person",
                           note="yes by message")
    assert modes.check(auto, "generate", approval_id=yes["id"])["allowed"]


def test_voice_clone_only_the_owners_verified_voice(new_film):
    fd = new_film()
    with pytest.raises(Refused):
        modes.check(fd, "voice_clone")
    assert modes.check(fd, "voice_clone", owner_verified=True)["allowed"]


def test_spend_past_the_film_budget_asks_first(new_film, monkeypatch):
    fd = new_film()
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")  # budgets bite only on real spend
    big = ledger.estimate("elevenlabs", "tts", amount=film.load(fd)["budget"]["elevenlabs"] + 1)
    with pytest.raises(SpendRefused):
        modes.check(fd, "spend", estimate=big)
    small = ledger.estimate("elevenlabs", "tts", amount=10)
    assert modes.check(fd, "spend", estimate=small)["allowed"]


def test_publish_needs_the_final_cut_approval(new_film):
    fd = new_film()
    (fd / "masters" / "film.mov").write_text("master")
    with pytest.raises(Refused):
        modes.check(fd, "publish", path="masters/film.mov")
    approvals.record(fd, what="final_cut", path="masters/film.mov", approved_by="Test Person")
    assert modes.check(fd, "publish", path="masters/film.mov")["allowed"]


def test_a_new_claim_parks_an_autonomous_film(new_film):
    fd = new_film("claims", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    assert modes.check(fd, "claim", claim="Works offline.")["allowed"]  # on the approved list
    with pytest.raises(Refused):
        modes.check(fd, "claim", claim="Twice as fast as anything else.")
    parked = modes.parked(fd)
    assert len(parked) == 1 and "Twice as fast" in parked[0]["text"]
    st = status.compute(fd)
    assert st["parked"] and st["next"]["who"] == "person" and st["next"]["command"].startswith("slate film answer")
    assert "PARKED" in status.format_human(st)
    with pytest.raises(Refused):  # a parked film refuses production actions
        modes.check(fd, "render")
    approvals.answer(fd, parked[0]["id"], text="No: keep to the approved claims.", by="Test Person")
    assert not modes.parked(fd)
    assert modes.check(fd, "render")["allowed"]
    sup = new_film("claims-sup")
    assert modes.check_claims(sup, ["Anything at all"]) == []  # supervised films leave claims to the source gate


def test_review_verdicts_park_autonomous_films_with_evidence(new_film):
    fd = new_film("rev", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    results = [gates.GateResult.pass_("loudness", "fine"),
               gates.GateResult.review("contact_sheet", "a person judges the look", evidence=["review/sheet.jpg"])]
    with pytest.raises(Refused):
        modes.check(fd, "review", results=results)
    q = modes.parked(fd)[0]
    assert "contact_sheet" in q["text"] and q["evidence"] == ["review/sheet.jpg"]
    sup = new_film("rev-sup")
    assert modes.park_on_review(sup, results) == []


def test_park_door_for_other_modules_is_idempotent_and_syncs_with_qa(new_film):
    fd = new_film()
    q1 = park.park(fd, reason="loudness REVIEW: check the swell", evidence=["qa/loudness.json"], source="qa")
    q2 = park.park(fd, reason="loudness REVIEW; glitch REVIEW", evidence=["qa/glitch.json"], source="qa")
    assert q1["id"] == q2["id"] and len(modes.parked(fd)) == 1
    assert modes.parked(fd)[0]["text"] == "loudness REVIEW; glitch REVIEW"
    (fd / "qa" / "parked.json").write_text(json.dumps({"parked": False, "released_by": "Test Person",
                                                       "released_at": "2026-10-01T10:00:00"}))
    st = status.compute(fd)  # qa's release answers the question, so the two records agree
    assert not st["parked"] and not modes.parked(fd)


def test_park_and_answer_cli(new_film):
    fd = new_film()
    code, out, _ = run_cli(["film", "park", "Which ending: A or B?", "--source", "agent", "--evidence",
                            "review/v1.mp4", "--film", str(fd), "--json"])
    assert code == 0
    qid = json.loads(out)["id"]
    code, out, _ = run_cli(["film", "questions", "--film", str(fd), "--json"])
    assert [q["id"] for q in json.loads(out)["questions"]] == [qid]
    code, out, _ = run_cli(["film", "answer", qid, "--by", "Test Person", "--text", "B, the quiet one.", "--film",
                            str(fd), "--json"])
    assert code == 0 and not json.loads(out)["still_parked"]
    prod = (fd / "brief" / "PRODUCTION.md").read_text()
    assert "B, the quiet one." in prod  # the answer is logged verbatim as a direction change


def test_the_autonomous_default_format_is_the_one_marked_so(state_env, monkeypatch):
    """formats' ask: prefer the format marked autonomous_default among the 15-45 s 9:16 reels, not the first by name."""
    from slate.state import deps

    reels = {
        "a-reel": {"name": "a-reel", "width": 1080, "height": 1920, "fps_num": 25, "fps_den": 1,
                   "runtime_s": {"min": 15, "max": 45, "target": 30}},
        "z-reel": {"name": "z-reel", "width": 1080, "height": 1920, "fps_num": 25, "fps_den": 1,
                   "runtime_s": {"min": 20, "max": 40, "target": 30}, "autonomous_default": True},
    }
    monkeypatch.setattr(deps, "format_names", lambda: sorted(reels))
    monkeypatch.setattr(deps, "format_load", lambda name: (reels[name], "fake"))
    assert modes.default_format("autonomous") == "z-reel"
    reels["z-reel"]["autonomous_default"] = False
    assert modes.default_format("autonomous") == "a-reel"  # without the mark: exact fits, then by name


def test_a_final_cut_approval_answers_the_hold_it_was_waiting_for(new_film):
    """finish's ask: a held film parks on its review packet's question; approving the final cut answers it, so an
    approved film doesn't stay parked."""
    from slate.state import approvals, park

    fd = new_film()
    master = fd / "masters" / "final.mov"
    master.parent.mkdir(exist_ok=True)
    master.write_bytes(b"a master")
    park.park(fd, reason="Held for Test Person: approve the final cut", source="slate review v1")
    park.park(fd, reason="Which logo lock-up?", source="reviewer")
    assert len(modes.parked(fd)) == 2
    approvals.record(fd, what="final_cut", path="masters/final.mov", approved_by="Test Person")
    left = modes.parked(fd)
    assert [q["source"] for q in left] == ["reviewer"]  # only the hold is answered; other questions still wait
    held = next(q for q in approvals.load(fd)["questions"] if q["source"] == "slate review v1")
    assert held["answered_by"] == "Test Person" and "masters/final.mov" in held["answer"]
