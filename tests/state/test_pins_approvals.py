"""Pins (first use wins, explicit re-pins with history) and approvals (the SHA-256 of exactly what was approved)."""

from __future__ import annotations

import json

import pytest

from slate import config, jsonio
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.state import approvals, film, pins, production


def test_pins_first_use_wins(new_film):
    fd = new_film()
    assert pins.get(fd, "tts") is None
    assert pins.pin(fd, "tts", "model-a") == "model-a"
    assert pins.pin(fd, "tts", "model-b") == "model-a"  # never overwritten
    assert pins.get(fd, "tts") == "model-a"
    assert config.model("tts", film.load(fd)) == "model-a"  # what the voice stage asks for is the pin
    assert pins.pin(fd, "voice_settings", {"stability": 0.5}) == {"stability": 0.5}
    assert pins.pin(fd, "seed.S01", 1234) == 1234
    log = pins.history(fd)
    assert [e["key"] for e in log] == ["tts", "voice_settings", "seed.S01"] and all(e["event"] == "pin" for e in log)
    with pytest.raises(UsageError):
        pins.pin(fd, "bad key", 1)


def test_repin_is_explicit_and_keeps_history(new_film):
    fd = new_film()
    pins.pin(fd, "music", "music-model-1")
    with pytest.raises(Refused):
        pins.pin(fd, "music", "music-model-2", repin=True)  # no approver, no reason
    with pytest.raises(Refused):
        pins.repin(fd, "music", "music-model-2", approved_by="", reason="better")
    with pytest.raises(UsageError):
        pins.repin(fd, "never-pinned", "x", approved_by="Test Person", reason="why")
    got = pins.repin(fd, "music", "music-model-2", approved_by="Test Person", reason="A/B test won",
                     evidence="sound/ab/music.json")
    assert got == "music-model-2" and pins.get(fd, "music") == "music-model-2"
    hist = pins.history(fd, "music")
    assert [h["event"] for h in hist] == ["pin", "repin"]
    assert hist[-1]["previous"] == "music-model-1" and hist[-1]["approved_by"] == "Test Person"
    assert jsonio.validate(film.load(fd), "film") == []


def test_pinned_model_and_tool_at_first_use(new_film, monkeypatch):
    fd = new_film()
    first = pins.model(fd, "tts")
    assert first == config.model("tts") and pins.get(fd, "tts") == first
    monkeypatch.setenv("SLATE_MODEL_TTS", "a-newer-model")  # a registry or config change later…
    assert pins.model(fd, "tts") == first  # …never moves a pinned film
    assert pins.tool(fd, "remotion") == config.engine_version()


def test_pin_cli(new_film):
    fd = new_film()
    code, out, _ = run_cli(["film", "pin", "voice_id", "fixture-voice", "--film", str(fd), "--json"])
    assert code == 0 and json.loads(out)["first_use"] is True
    code, out, _ = run_cli(["film", "pin", "voice_id", "other-voice", "--film", str(fd), "--json"])
    assert code == 1 and "repin" in json.loads(out)["hint"]
    code, out, _ = run_cli(["film", "repin", "voice_id", "other-voice", "--approved-by", "Test Person", "--reason",
                            "the new voice won the A/B", "--film", str(fd), "--json"])
    assert code == 0 and json.loads(out)["previous"] == "fixture-voice"
    code, out, _ = run_cli(["film", "repin", "voice_id", "x", "--film", str(fd), "--json"])
    assert code == 2  # --approved-by and --reason are required


def test_approval_is_tied_to_the_exact_bytes(new_film):
    fd = new_film()
    sb = fd / "script" / "storyboard.json"
    sb.write_text('{"acts": ["one"]}')
    rec = approvals.record(fd, what="script", path="script/storyboard.json", approved_by="Test Person",
                           note="in chat: 'the script is great'")
    assert rec["sha256"] == jsonio.sha256(sb) and rec["basis"] == "file" and rec["kind"] == "script"
    assert rec["sitting"] == "preproduction" and rec["blocking"] and rec["version"] == 1
    assert approvals.is_approved(fd, "script", "script/storyboard.json")
    sb.write_text('{"acts": ["one", "two"]}')  # an edit after approval
    assert not approvals.is_approved(fd, "script", "script/storyboard.json")
    chk = approvals.check(fd, "script", "script/storyboard.json")
    assert "changed since" in chk["reason"] and chk["current_sha256"] != rec["sha256"]
    sb.write_text('{"acts": ["one"]}')  # the same bytes again: approved again
    assert approvals.is_approved(fd, "script", "script/storyboard.json")
    assert not approvals.is_approved(fd, "final_cut", "script/storyboard.json")
    with pytest.raises(UsageError):
        approvals.record(fd, what="script", path="script/none.json", approved_by="Test Person")
    with pytest.raises(UsageError):
        approvals.record(fd, what="Not A Kind!", path="script/storyboard.json", approved_by="Test Person")
    with pytest.raises(UsageError):
        approvals.record(fd, what="script", path="script/storyboard.json", approved_by=" ")
    assert jsonio.validate(approvals.load(fd), "approvals") == []


def test_folder_approval_and_qualified_kinds(new_film):
    fd = new_film()
    (fd / "voice" / "shortlist").mkdir()
    (fd / "voice" / "shortlist" / "a.json").write_text("1")
    rec = approvals.record(fd, what="voice_shortlist", path="voice/shortlist", approved_by="Test Person")
    assert rec["basis"] == "tree"
    assert approvals.is_approved(fd, "voice_shortlist", "voice/shortlist")
    (fd / "voice" / "shortlist" / "b.json").write_text("2")
    assert not approvals.is_approved(fd, "voice_shortlist", "voice/shortlist")
    (fd / "scenes" / "plates").mkdir()
    (fd / "scenes" / "plates" / "S05.mov").write_text("p")
    rec = approvals.record(fd, what="plate-take:S05", path="scenes/plates/S05.mov", approved_by="Test Person")
    assert rec["kind"] == "plate"
    assert approvals.is_approved(fd, "plate-take:S05", "scenes/plates/S05.mov")  # the exact name other modules use
    assert approvals.is_approved(fd, "plate", "scenes/plates/S05.mov")  # and the bare kind
    gen = approvals.record(fd, what="generation:S05", path="scenes/plates/S05.mov", approved_by="Test Person")
    assert gen["kind"] == "spend" and approvals.spend_covers(fd, gen["id"], "scenes/plates/S05.mov")


def test_checklist_approval_covers_the_asks_not_their_status(new_film):
    fd = new_film(request="Make a short film. Keep it calm.", request_source="Test Person")
    rec = approvals.record(fd, what="checklist", path="brief/GOAL.md", approved_by="Test Person")
    assert rec["basis"] == "asks"
    production.mark(fd, 1, status="done", evidence="script/storyboard.json S01")
    assert approvals.is_approved(fd, "checklist", "brief/GOAL.md")  # progress doesn't undo the approval
    production.add_ask(fd, "Also add captions.", source="Test Person")
    assert not approvals.is_approved(fd, "checklist", "brief/GOAL.md")  # a new ask needs the checklist re-approved


def test_sittings_by_mode(new_film):
    fd = new_film("sup")
    sit = approvals.sittings(fd)
    assert sit["preproduction"]["blocking"] and sit["preproduction"]["items"] == list(approvals.PREPRODUCTION)
    assert sit["final_cut"]["items"] == ["final_cut"]
    (fd / "script" / "storyboard.json").write_text("{}")
    approvals.record(fd, what="script", path="script/storyboard.json", approved_by="Test Person")
    assert "script" in approvals.sittings(fd)["preproduction"]["approved"]
    ex = new_film("exp", mode="express")
    sit = approvals.sittings(ex)
    assert not sit["preproduction"]["blocking"] and sit["preproduction"]["items"] == []
    assert sit["final_cut"]["items"] == [*approvals.PREPRODUCTION, "final_cut"]  # batched into the final cut
    au = new_film("aut", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    sit = approvals.sittings(au)
    assert sit["final_cut"]["blocking"] and {"claims", "voice_shortlist", "look_test"} <= set(sit["covered"])
    assert set(sit["final_cut"]["pending"]) == {"checklist", "script", "final_cut"}
    dr = new_film("drf", mode="draft")
    sit = approvals.sittings(dr)
    assert not sit["preproduction"]["blocking"] and not sit["final_cut"]["blocking"]
    (dr / "masters" / "film.mov").write_text("m")
    with pytest.raises(Refused):
        approvals.record(dr, what="final_cut", path="masters/film.mov", approved_by="Test Person")


def test_brand_look_approval_covers_the_look_test(new_film):
    fd = new_film("looked", brand="onboarded-brand")
    sit = approvals.sittings(fd)
    assert "look_test" in sit["covered"] and "look_test" in sit["preproduction"]["approved"]


def test_approve_cli(new_film):
    fd = new_film()
    (fd / "script" / "storyboard.json").write_text("{}")
    code, out, err = run_cli(["approve", "script", "script/storyboard.json", "--by", "Test Person", "--film",
                              str(fd), "--json"])
    assert code == 0, err
    rec = json.loads(out)
    assert rec["what"] == "script" and len(rec["sha256"]) == 64
    code, out, _ = run_cli(["approve", "spend", "voice", "--by", "Test Person", "--film", str(fd)])
    assert code == 0 and "--approval ap_" in out
    code, out, _ = run_cli(["approve", "script", "nope.json", "--by", "Test Person", "--film", str(fd), "--json"])
    assert code == 2
    prod = (fd / "brief" / "PRODUCTION.md").read_text()
    assert "| script | `script/storyboard.json`" in prod
