"""Perfect, don't replace: notes on approved work become a checklist of changes to that exact artifact."""

from __future__ import annotations

import json

import pytest

from slate import gates
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.state import approvals, lock, modes, production, revise

NOTES = ("The S19 click is a touch loud: bring it down about 2 dB. Let the pad breathe a beat longer before the "
         "reveal. Maybe research a completely different sound design for the reveal.")


def _cues(fd, s19=-17.0, pad=1.0, reveal="wood"):
    path = fd / "sound" / "cues.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"shots": {"S19": {"click_db": s19, "pad_hold": pad}, "S20": {"reveal": reveal}}},
                               indent=1))
    return path


@pytest.fixture
def approved(new_film):
    fd = new_film(request="Make the planner film.", request_source="Test Person")
    _cues(fd)
    lock.record(fd, "sound/cues.json", producer="slate sound spot", inputs=[])
    (fd / "sound" / "mix.wav").write_text("mix made from the cues")
    lock.record(fd, "sound/mix.wav", producer="slate mix", inputs=["sound/cues.json"])
    (fd / "script" / "storyboard.json").write_text("{}")
    approvals.record(fd, what="script", path="script/storyboard.json", approved_by="Test Person")
    approvals.record(fd, what="sound_proof", path="sound/cues.json", approved_by="Test Person")
    return fd


def test_start_turns_notes_into_a_checklist_and_questions(approved):
    fd = approved
    rev = revise.start(fd, "sound/cues.json", notes=NOTES, by="Test Person", scopes=["shots.S19"])
    assert rev["id"] == "rv1" and rev["artifact"] == "sound/cues.json" and rev["what"] == "sound_proof"
    assert rev["approved_sha"] == approvals.latest(fd, "sound_proof", "sound/cues.json")["sha256"]
    assert [i["text"] for i in rev["items"]] == ["The S19 click is a touch loud: bring it down about 2 dB.",
                                                 "Let the pad breathe a beat longer before the reveal."]
    assert len(rev["questions"]) == 1  # "research a completely different sound design" is a question, not a change
    q = approvals.load(fd)["questions"][0]
    assert "new approach" in q["text"] and not q["parks"]  # supervised: asked, not parked
    assert (fd / rev["snapshot"]).read_text() == (fd / "sound" / "cues.json").read_text()
    assert "sound/mix.wav" in rev["baseline"] and "script/storyboard.json" in rev["baseline"]
    assert "sound/cues.json" not in rev["baseline"]
    brief = production.load_brief(fd)
    assert brief["directions"][-1]["text"] == NOTES and brief["directions"][-1]["n"] == rev["direction"]
    prod = (fd / "brief" / "PRODUCTION.md").read_text()
    assert "### rv1: `sound/cues.json` (open)" in prod and "1. The S19 click is a touch loud" in prod
    assert "Everything else stays as it was" in prod
    with pytest.raises(Refused):
        revise.start(fd, "sound/cues.json", notes="again", by="Test Person")  # one open revision per artifact


def test_check_flags_changes_outside_the_checklist(approved):
    fd = approved
    revise.start(fd, "sound/cues.json", notes=NOTES, by="Test Person", scopes=["shots.S19"])
    res = revise.check(fd)
    by = {r["gate"]: r for r in res["results"]}
    assert by["revise.artifact"]["status"] == "REVIEW"  # nothing changed yet
    assert by["revise.elsewhere"]["status"] == "PASS"
    _cues(fd, s19=-19.0)  # the checklist's change, inside its scope
    (fd / "sound" / "mix.wav").write_text("remixed")  # made from the cues: expected to change with them
    res = revise.check(fd)
    by = {r["gate"]: r for r in res["results"]}
    assert by["revise.artifact"]["status"] == "PASS"
    assert by["revise.json_paths"]["status"] == "PASS"
    assert by["revise.elsewhere"]["status"] == "PASS" and "sound/mix.wav" in by["revise.elsewhere"]["summary"]
    _cues(fd, s19=-19.0, reveal="synth swell")  # a change nobody asked for
    res = revise.check(fd)
    by = {r["gate"]: r for r in res["results"]}
    assert res["status"] == "FAIL" and by["revise.json_paths"]["status"] == "FAIL"
    assert "/shots/S20/reveal" in by["revise.json_paths"]["summary"]
    _cues(fd, s19=-19.0)
    (fd / "script" / "storyboard.json").write_text('{"changed": "while revising the sound"}')
    res = revise.check(fd)
    assert res["outside"] == ["script/storyboard.json"]
    with pytest.raises(Refused):
        revise.done(fd)
    (fd / "script" / "storyboard.json").write_text("{}")
    rev = revise.done(fd, evidence="S19 click measured 2 dB down")
    assert rev["status"] == "done" and all(i["status"] == "done" for i in rev["items"])
    assert rev["next"] == "slate approve sound_proof sound/cues.json --by NAME"
    assert not approvals.is_approved(fd, "sound_proof", "sound/cues.json")  # the revised version needs a new yes


def test_revise_needs_approved_unchanged_work(approved):
    fd = approved
    with pytest.raises(Refused):
        revise.start(fd, "sound/mix.wav", notes="louder", by="Test Person")  # not approved
    rev = revise.start(fd, "sound/mix.wav", notes="A little louder overall.", by="Test Person", what="sound_proof",
                       approved_by="Test Person")  # the notes came with a yes
    assert rev["what"] == "sound_proof"
    _cues(fd, s19=-30)
    with pytest.raises(Refused):
        revise.start(fd, "sound/cues.json", notes="x", by="Test Person")  # changed since it was approved
    with pytest.raises(UsageError):
        revise.start(fd, "script/storyboard.json", notes="  ", by="Test Person")


def test_hooks_are_measured_and_broken_hooks_are_unchecked(approved, monkeypatch):
    fd = approved
    monkeypatch.setattr(revise, "HOOKS", {})

    def level_vs_voice(film_dir, approved_path, current_path, rev):
        return gates.GateResult.pass_("level_vs_voice", "effects 11 LU under the voice, as approved")

    def broken(film_dir, approved_path, current_path, rev):
        raise RuntimeError("no audio")

    revise.register_hook("level_vs_voice", level_vs_voice, applies=lambda p: p.suffix == ".json", owner="sound")
    revise.register_hook("palette", broken, owner="qa")
    revise.register_hook("never", level_vs_voice, applies=lambda p: False)
    revise.start(fd, "sound/cues.json", notes="Bring S19 down 2 dB.", by="Test Person")
    res = revise.check(fd)
    by = {r["gate"]: r for r in res["results"]}
    assert by["revise.level_vs_voice"]["status"] == "PASS"
    assert by["revise.palette"]["status"] == "UNCHECKED"  # a broken measure is never a quiet pass
    assert "revise.never" not in by
    assert set(res["measures"]) == {"level_vs_voice", "palette", "never"}


def test_autonomous_new_approach_notes_park_the_film(new_film):
    fd = new_film("auto", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    (fd / "sound" / "proof.json").write_text("{}")
    approvals.record(fd, what="sound_proof", path="sound/proof.json", approved_by="Test Person")
    rev = revise.start(fd, "sound/proof.json", notes="Scrap it and redesign the whole thing.", by="Test Person")
    assert rev["items"] == [] and len(rev["questions"]) == 1
    assert modes.parked(fd)  # an autonomous film waits for the person's answer


def test_revise_cli(approved):
    fd = approved
    code, out, err = run_cli(["revise", "start", "sound/cues.json", "--notes", "Bring S19 down 2 dB.", "--by",
                              "Test Person", "--scope", "shots.S19", "--film", str(fd), "--json"])
    assert code == 0, err
    assert json.loads(out)["items"][0]["scope"] == ["shots.S19"]
    _cues(fd, reveal="changed")
    code, out, _ = run_cli(["revise", "check", "--film", str(fd), "--json"])
    assert code == 1 and json.loads(out)["status"] == "FAIL"
    code, out, _ = run_cli(["revise", "done", "--film", str(fd), "--json"])
    assert code == 1
    code, out, _ = run_cli(["revise", "done", "--force", "--film", str(fd), "--json"])
    assert code == 2  # forcing past a failed check needs the reason
    code, out, _ = run_cli(["revise", "done", "--force", "--reason", "Test Person asked for the new reveal too",
                            "--film", str(fd), "--json"])
    assert code == 0 and json.loads(out)["closing"]["forced"]
    code, out, _ = run_cli(["revise", "list", "--film", str(fd), "--json"])
    assert json.loads(out)["revisions"][0]["status"] == "done"


def test_a_crashed_start_never_reuses_its_snapshot_id(approved):
    fd = approved
    (fd / "archive" / "revisions" / "rv1").mkdir(parents=True)  # left behind by a start that died mid-way
    rev = revise.start(fd, "sound/cues.json", notes="Bring S19 down 2 dB.", by="Test Person")
    assert rev["id"] == "rv2"
