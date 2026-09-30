"""The reviewer's grade: the brief (paths and criteria), the verdict, and holding an autonomous film only after a
passing grade (then it's parked for the owner and sent by iMessage with a push)."""

from __future__ import annotations

from pathlib import Path

import pytest

import filmkit
from slate import jsonio
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.finish import grade


def autonomous_delivered(make_film, film_id="held") -> Path:
    d = make_film(film_id, mode="autonomous", format="finish-reel")
    assert filmkit.cli(d, "master")[0] == 0
    filmkit.pass_gates(d)
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 0, err
    return d


def test_brief_needs_a_packet(make_film):
    d = make_film("nobrief", mode="draft", brand="open-brand", renders=False, mix=False)
    code, out, err = filmkit.cli(d, "review", "grade-brief")
    assert code == 3 and "slate review" in err


def test_brief_lists_paths_and_criteria(make_film):
    d = make_film("brief", mode="autonomous", format="finish-reel")
    filmkit.cli(d, "review", "--no-send")
    code, out = filmkit.cli_json(d, "review", "grade-brief")
    assert code == 0 and out["brief"] == "review/v1/grade_brief.md"
    b = jsonio.read(d / "review/v1/grade_brief.json")
    assert [Path(p).name for p in b["look_at"]["contact_sheets"]] == ["brief_v1_00.jpg"]
    assert Path(b["look_at"]["phone_cut"]).name == "brief_v1_phone.mp4"
    assert b["listen_to"]["film_sound"].endswith("final_mix.wav")
    refs = {r["reference"]: r for r in b["brand_references"]}
    assert set(refs) == {"look_motion", "sound", "voice", "truth"}
    assert all(r["approved"] and r["exists"] for r in refs.values())
    assert any(t.get("file", "").endswith("taste.md") for t in b["taste_notes"])
    ids = [c["id"] for c in b["criteria"]]
    assert ids == ["look", "motion", "sound", "voice", "truth", "taste", "legibility", "visionary"]
    assert "didn't make this film" in b["reviewer"]
    md = (d / "review/v1/grade_brief.md").read_text()
    assert "slate review grade --verdict pass" in md and "visionary" in md


def test_grade_needs_the_brief_and_honest_notes(make_film):
    d = make_film("g", mode="autonomous", format="finish-reel")
    filmkit.cli(d, "review", "--no-send")
    with pytest.raises(Refused, match="Grade from the brief"):
        grade.record(d, verdict="pass", notes="fine", by="r")
    grade.brief(d)
    with pytest.raises(UsageError):
        grade.record(d, verdict="maybe", notes="", by="r")
    with pytest.raises(UsageError, match="needs the notes"):
        grade.record(d, verdict="revise", notes="  ", by="r")
    rec = grade.record(d, verdict="pass", notes="on brand, clean", by="reviewer-agent")
    assert rec["packet_sha256"] == jsonio.sha256(d / "review/v1/packet.json")
    assert (d / "review/v1/grades.jsonl").read_text().count("\n") == 1


def test_parking_requires_a_grade(make_film, sends):
    d = autonomous_delivered(make_film)
    code, out = filmkit.cli_json(d, "review")
    assert code == 0 and out["sent"]["awaiting_grade"] is True and out["next"].startswith("slate review grade-brief")
    code, _o, err = filmkit.cli(d, "review", "--to", "imessage")
    assert code == 1 and "needs a reviewer's grade" in err
    from slate.state import approvals

    assert approvals.open_questions(d, parking=True) == []  # nothing parked, nothing held
    assert sends == []


def test_a_revise_verdict_holds_nothing_and_feeds_the_plan(make_film):
    d = autonomous_delivered(make_film, "rev")
    filmkit.cli(d, "review", "--no-send")
    filmkit.cli(d, "review", "grade-brief")
    code, out = filmkit.cli_json(d, "review", "grade", "--verdict", "revise", "--notes",
                                 "the swell is too loud at the end", "--by", "reviewer-agent")
    assert code == 0 and out["next"].startswith("slate notes plan")
    code, _o, err = filmkit.cli(d, "review", "--to", "imessage")
    assert code == 1 and "verdict on v1 is revise" in err
    code, out = filmkit.cli_json(d, "notes", "list")
    assert out["notes"][0]["source_kind"] == "reviewer" and out["notes"][0]["direction"] is None


def test_a_pass_holds_the_film_and_sends_it(make_film, sends, monkeypatch, settings_toml):
    settings_toml('[notify]\nimessage_handle = "+15550100200"\npush_command = ["fixture-push", "{title}", "{body}"]\n')
    d = autonomous_delivered(make_film, "pass")
    filmkit.cli(d, "review", "--no-send")
    filmkit.cli(d, "review", "grade-brief")
    filmkit.cli(d, "review", "grade", "--verdict", "pass", "--notes", "holds up", "--by", "reviewer-agent")
    monkeypatch.setenv("SLATE_LIVE", "1")  # sends are allowed only outside replay; the transport is still a fake
    monkeypatch.setattr("slate.notify._openclaw", lambda cfg: None)  # no OpenClaw here: iMessage goes through Messages
    code, out = filmkit.cli_json(d, "review")
    assert code == 0, out
    sent = out["sent"]
    assert sent["held"] is True and sent["question"] == "q1" and sent["sent"] is True
    from slate.state import approvals

    q = approvals.open_questions(d, parking=True)[0]
    assert q["id"] == "q1" and "slate approve final_cut deliver/v1/masters/pass_H264.mp4" in q["text"]
    imsg = next(a for a in sends if a[0].endswith("osascript"))
    assert "+15550100200" in imsg and imsg[-1].endswith("pass_v1_phone.mp4")  # the phone cut is attached
    push = next(a for a in sends if a[0] == "fixture-push")
    assert push[1].startswith("Slate: Pass v1 held for you")
    # holding again doesn't page the owner twice
    n = len(sends)
    filmkit.cli(d, "review")
    assert len(sends) == n


def test_hold_needs_a_final_delivery(make_film):
    d = make_film("nodeliver", mode="autonomous", format="finish-reel")
    filmkit.cli(d, "review", "--no-send")
    filmkit.cli(d, "review", "grade-brief")
    filmkit.cli(d, "review", "grade", "--verdict", "pass", "--notes", "ok", "--by", "r")
    code, _o, err = filmkit.cli(d, "review", "--to", "imessage")
    assert code == 1 and "Deliver v1 before holding it" in err


def test_a_changed_packet_needs_a_new_grade(make_film):
    d = autonomous_delivered(make_film, "regrade")
    filmkit.cli(d, "review", "--no-send")
    filmkit.cli(d, "review", "grade-brief")
    filmkit.cli(d, "review", "grade", "--verdict", "pass", "--notes", "ok", "--by", "r")
    filmkit.cli(d, "review", "--no-send", "--rebuild")  # the packet is made again: its bytes change
    code, _o, err = filmkit.cli(d, "review", "--to", "imessage")
    assert code == 1 and "earlier packet" in err


def test_supervised_films_can_go_by_imessage_without_a_grade(make_film, sends, monkeypatch, settings_toml):
    settings_toml('[notify]\nimessage_handle = "someone@example.com"\n'
                  'push_command = ["fixture-push", "{title}", "{body}"]\n')
    d = make_film("sup", mode="supervised", brand="open-brand")
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr("slate.notify._openclaw", lambda cfg: None)
    code, out = filmkit.cli_json(d, "review", "--to", "imessage")
    assert code == 0 and out["sent"]["held"] is False and out["sent"]["sent"] is True


def test_grade_brief_requires_a_film_packet_version(make_film):
    d = make_film("v", mode="draft", brand="open-brand", renders=False, mix=False)
    with pytest.raises(MissingPrerequisite):
        grade.brief(d, version=3)


def test_default_run_after_a_revise_waits_instead_of_failing(make_film):
    d = autonomous_delivered(make_film, "waits")
    filmkit.cli(d, "review", "--no-send")
    filmkit.cli(d, "review", "grade-brief")
    filmkit.cli(d, "review", "grade", "--verdict", "revise", "--notes", "the logo is soft", "--by", "r")
    code, out = filmkit.cli_json(d, "review")
    assert code == 0 and out["sent"]["awaiting_grade"] is True and out["next"] == "slate notes plan"
