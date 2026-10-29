"""slate notes: the owner's words verbatim as numbered directions, timecodes mapped to shot, beat and stage, and the
targeted fix plan (perfect, don't replace)."""

from __future__ import annotations

import pytest

import filmkit
from slate.errors import UsageError
from slate.finish import notes
from slate.finish.film import Film


@pytest.fixture
def film(make_film):
    return Film(make_film("nt", mode="draft", brand="open-brand", renders=False, mix=False))


# ---------------------------------------------------------------------------------------------------------------
# Timecodes


@pytest.mark.parametrize("at,want", [
    ("00:00:00:18", (18, None)),      # the phone cut's HH:MM:SS:FF on the film's clock (25 fps)
    ("00:01:05", (30, None)),         # MM:SS:FF
    ("0:00.5", (12, None)),           # the contact sheet's m:ss.s (the nearest frame, as the clock rounds)
    ("1.2", (30, None)),              # plain seconds
    ("f33", (33, None)),              # a film frame
    ("frame 7", (7, None)),
    ("0:00.4-0:01.0", (10, 25)),      # a range
    ("f30 to f20", (20, 30)),         # a range, either way round
])
def test_timecode_forms(film, at, want):
    assert notes.parse_at(film, at) == want


@pytest.mark.parametrize("bad", ["00:00:05:00", "f40", "banana", "", "-3"])
def test_timecodes_outside_the_film_or_unreadable(film, bad):
    with pytest.raises(UsageError):
        notes.parse_at(film, bad)


def test_timecode_maps_to_shot_beat_and_word(film):
    w = notes.locate(film, 18)
    assert w["shot"] == "S02" and w["act"] == 1 and w["shots"] == ["S02"]
    assert w["beat"]["id"] == "card-lands" and w["beat"]["kind"] == "land" and w["beat"]["offset_frames"] == 0
    assert w["timecode"] == "00:00:00:18" and w["seconds"] == 0.72
    w = notes.locate(film, 30)
    assert w["shot"] == "S03" and w["act"] == 2 and w["beat"]["id"] == "logo-stamp"
    assert w["voice_shot"] == "S03" and w["word"] in ("Try", "it", "today.")
    # S02 has no line: a moment in it is about the line being spoken there (S01's, still finishing)
    w = notes.locate(film, 12, text="she rushes minutes")
    assert w["shot"] == "S02" and w["voice_shot"] == "S01" and w["word"] == "minutes."
    w = notes.locate(film, 10, 30)
    assert w["shots"] == ["S01", "S02", "S03"]


# ---------------------------------------------------------------------------------------------------------------
# Stages


@pytest.mark.parametrize("text,stage", [
    ("the voice rushes the last word", "voice"),
    ("mispronounced the name", "voice"),
    ("the click is too loud", "sound"),
    ("the music drops out", "sound"),
    ("the captions overlap the logo", "captions"),
    ("the logo pops in", "picture"),
    ("hard to read at this size", "picture"),
    ("linger on the chart", "timing"),
    ("could we start over from scratch", "new_approach"),
    ("hmm", None),
])
def test_stage_from_the_notes_own_words(text, stage):
    assert notes.infer_stage(text, "en")[0] == stage


def test_phrases_match_whole_words():
    assert notes.infer_stage("the reader will miss it", "en")[0] is None  # "the read" isn't in "the reader"
    assert notes.infer_stage("keep the reread", "en")[0] == "voice"


def test_a_language_without_a_table_guesses_nothing():
    assert notes.infer_stage("the voice rushes", "xx") == (None, None)


# ---------------------------------------------------------------------------------------------------------------
# Logging notes


def test_note_is_logged_verbatim_as_a_numbered_direction(film):
    text = "The card lands a beat late.\nKeep everything else!"
    code, rec = filmkit.cli_json(film.dir, "notes", "add", text, "--at", "00:00:00:18", "--by", "Test Person")
    assert code == 0
    assert rec["text"] == text and rec["direction"] == 1 and rec["stage"] == "picture" and rec["version"] == 1
    assert rec["where"]["shot"] == "S02" and rec["where"]["beat"]["id"] == "card-lands"
    from slate.state import production

    d = production.load_brief(film.dir)["directions"][-1]
    assert d["text"] == text and d["source"] == "Test Person" and "00:00:00:18" in d["kind"]
    assert "The card lands a beat late." in (film.dir / "brief/PRODUCTION.md").read_text()
    code, rec2 = filmkit.cli_json(film.dir, "notes", "add", "the voice is rushed here", "--at", "f30")
    assert rec2["direction"] == 2 and rec2["id"] == "n2"


def test_stage_can_be_given(film):
    code, rec = filmkit.cli_json(film.dir, "notes", "add", "fix this", "--at", "f5", "--stage", "sound")
    assert rec["stage"] == "sound" and rec["stage_how"] == "given"


def test_note_without_words_or_timecode_is_unsorted(film):
    code, rec = filmkit.cli_json(film.dir, "notes", "add", "hmm")
    assert rec["stage"] == "unsorted" and rec["where"] is None


def test_reviewer_notes_are_not_the_owners_directions(film):
    rec = notes.add(film.dir, "the swell is too loud", by="reviewer-agent", source_kind="reviewer")
    assert rec["direction"] is None and rec["source_kind"] == "reviewer"
    from slate.state import production

    assert production.load_brief(film.dir)["directions"] == []


def test_list_and_dry_run(film):
    code, out = filmkit.cli_json(film.dir, "notes", "add", "the logo pops", "--at", "f30", "--dry-run")
    assert code == 0 and out["dry_run"] and out["stage"] == "picture"
    assert not (film.dir / "review/notes.json").exists()
    filmkit.cli(film.dir, "notes", "add", "the logo pops", "--at", "f30")
    code, out = filmkit.cli_json(film.dir, "notes", "list")
    assert [n["text"] for n in out["notes"]] == ["the logo pops"]


# ---------------------------------------------------------------------------------------------------------------
# The fix plan


def test_fix_plan_targets_each_stage(film):
    filmkit.cli(film.dir, "notes", "add", "the voice rushes minutes", "--at", "0:00.5")
    filmkit.cli(film.dir, "notes", "add", "the card lands a beat late", "--at", "00:00:00:18")
    filmkit.cli(film.dir, "notes", "add", "the click is too loud", "--at", "f30")
    filmkit.cli(film.dir, "notes", "add", "the captions overlap", "--at", "f31")
    code, p = filmkit.cli_json(film.dir, "notes", "plan")
    assert code == 0 and p["notes"] == 4
    by = {s["stage"]: s for s in p["steps"]}
    v = by["voice"]
    assert v["shots"] == ["S01"] and "neighbour context: S03" in v["title"]
    assert "slate voice tts --only S01 --force" in v["commands"]
    pic = by["picture"]
    assert pic["frames"] == [12, 24] and "slate render --from 12 --to 24" in " ".join(pic["commands"])
    assert "splices" in " ".join(pic["commands"])
    assert by["sound"]["commands"][:2] == ["slate mix", "slate verify"]
    assert by["captions"]["commands"] == ["slate voice captions"]
    assert set(p["gates"]) >= {"sync", "captions", "loudness", "frames", "glitch", "psnr"}
    assert p["then"][0] == "slate master" and p["then"][1].startswith("slate qa ")
    assert "Preserve the approved baseline" in p["rule"]
    assert (film.dir / "review/v1/fix_plan.json").is_file()
    assert "Re-voice S01 only" in notes.plan_text(p)


def test_an_explicitly_requested_new_approach_becomes_work_without_an_extra_question(film):
    from slate.state import approvals

    filmkit.cli(film.dir, "notes", "add", "honestly, start over with a whole new look", "--at", "f20")
    code, p = filmkit.cli_json(film.dir, "notes", "plan")
    assert p["steps"] and p["questions"] == []
    qs = approvals.open_questions(film.dir)
    assert qs == []
    filmkit.cli(film.dir, "notes", "plan")
    assert approvals.open_questions(film.dir) == []


def test_retime_after_renders_is_a_question(make_film):
    d = make_film("rt", mode="draft", brand="open-brand")
    from slate.state import lock

    lock.lock_asset(d, "timing/shots.json", reason="fixture: act renders registered")
    assert lock.timeline_locked(d)
    filmkit.cli(d, "notes", "add", "linger on the logo", "--at", "f30")
    code, p = filmkit.cli_json(d, "notes", "plan")
    assert p["questions"] and "unlocks the timeline" in p["questions"][0]["why"]


def test_plan_edits_approved_work_in_place(make_film):
    from slate.state import approvals

    d = make_film("appr", mode="draft", brand="open-brand")
    approvals.record(d, what="take:S01", path="voice/takes/S01/t01.json", approved_by="Test Person")
    filmkit.cli(d, "notes", "add", "the voice rushes minutes", "--at", "0:00.5")
    code, p = filmkit.cli_json(d, "notes", "plan")
    v = next(s for s in p["steps"] if s["stage"] == "voice")
    assert v["edits_approved"][0]["path"] == "voice/takes/S01/t01.json"
    cmds = " ".join(v["commands"])
    assert "slate approve spend voice/takes/S01" in cmds and "--approval" in cmds
    assert "slate revise start voice/takes/S01/t01.json" in cmds


def test_plan_dry_run_asks_and_writes_nothing(film):
    from slate.state import approvals

    filmkit.cli(film.dir, "notes", "add", "start over from scratch", "--at", "f20")
    code, p = filmkit.cli_json(film.dir, "notes", "plan", "--dry-run")
    assert code == 0 and p["questions"] == [] and p["steps"]
    assert approvals.open_questions(film.dir) == []
    assert not (film.dir / "review/v1/fix_plan.json").exists()


def test_no_notes_says_how_to_add_one(film):
    code, out, err = filmkit.cli(film.dir, "notes", "plan")
    assert code == 0 and "slate notes add" in out
