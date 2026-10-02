"""`slate script build` and `slate script lint` end to end (plan points P080, P113, P114, P148)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from storyfix import SCRIPT, voice_takes, write_json

from slate import jsonio
from slate.cli import run_cli


NO_TARGET = {"targets": {"runtime_s": None, "lufs": -14, "true_peak": -1}}  # edits change the runtime; judge the rest


def cli(*argv) -> tuple[int, dict | str, str]:
    code, out, err = run_cli(list(argv))
    try:
        return code, json.loads(out), err
    except json.JSONDecodeError:
        return code, out, err


def shot(script, sid):
    return next(s for a in script["acts"] for s in a["shots"] if s["id"] == sid)


def script_of(film: Path) -> dict:
    return json.loads((film / "script" / "script.json").read_text())


def save_script(film: Path, script: dict) -> None:
    write_json(film / "script" / "script.json", script)


# --- writing and refusing ---------------------------------------------------------------------------------------------------

def test_build_writes_the_storyboard_and_script_stamped_generated(make_film):
    film = make_film()
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    assert code == 0 and out["ok"] and out["status"] == "PASS"
    assert out["written"] == ["script/storyboard.json", "script/SCRIPT.md"]
    sb = jsonio.read(film / "script" / "storyboard.json")
    assert sb["generated"] == "do not edit by hand: slate script build"
    assert jsonio.validate(sb, "storyboard") == []
    md = (film / "script" / "SCRIPT.md").read_text()
    assert md.startswith("<!-- generated: do not edit by hand.")
    assert "## Script for the booth" in md and "`S02 0:02.3` Every morning at six" in md
    shots = [s for a in sb["acts"] for s in a["shots"]]
    assert [s["id"] for s in shots] == ["S01", "S02", "S03", "S04"]
    # planning lengths: lead 0.3 + words / 2.7 + tail (0.9 at an act break) + hold, rounded to 0.01 s
    assert [s["dur"] for s in shots] == [2.0, round(0.3 + 8 / 2.7 + 0.9 + 0.5, 2), round(0.3 + 8 / 2.7 + 0.3 + 0.8, 2),
                                         round(0.3 + 2 / 2.7 + 0.9 + 1.5, 2)]
    assert sb["timing"]["source"] == "planning"
    assert (film / "qa" / "gates.json").is_file()


def test_build_refuses_to_write_anything_on_a_fail(make_film):
    film = make_film()
    s = script_of(film)
    shot(s, "S04")["vo"] = "The cheapest bread in town."
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    assert code == 1 and out["refused"] and out["written"] == []
    assert sorted(p.name for p in (film / "script").iterdir()) == ["script.json"]
    assert not (film / "qa").exists()
    assert [g["gate"] for g in out["gates"] if g["status"] == "FAIL"] == ["script.banned_words"]


def test_a_failing_rebuild_leaves_the_last_good_storyboard_untouched(make_film):
    film = make_film()
    assert cli("script", "build", "--film", str(film))[0] == 0
    before = {p.name: p.read_bytes() for p in (film / "script").iterdir()}
    s = script_of(film)
    shot(s, "S04")["claims"] = ["Best bread in the county"]
    save_script(film, s)
    code, text, _ = cli("script", "build", "--film", str(film))
    assert code == 1 and "Nothing written" in text
    after = {p.name: p.read_bytes() for p in (film / "script").iterdir() if p.name != "script.json"}
    assert after == {k: v for k, v in before.items() if k != "script.json"}


def test_check_and_dry_run_write_nothing(make_film):
    film = make_film()
    for flag in ("--check", "--dry-run"):
        code, out, _ = cli("script", "build", flag, "--film", str(film), "--json")
        assert code == 0 and out["checked_only"] and out["written"] == []
    assert not (film / "script" / "storyboard.json").exists()


def test_lint_checks_without_writing(make_film):
    film = make_film(film=NO_TARGET)
    code, out, _ = cli("script", "lint", "--film", str(film), "--json")
    assert code == 0 and out["lint"] and out["status"] == "UNCHECKED"  # no runtime target: never a quiet pass
    s = script_of(film)
    shot(s, "S02")["vo"] = "Join 4,000 regulars at the counter every morning."
    save_script(film, s)
    code, out, _ = cli("script", "lint", "--film", str(film), "--json")
    assert code == 1 and [g["gate"] for g in out["gates"] if g["status"] == "FAIL"] == ["script.numbers"]
    assert not (film / "script" / "storyboard.json").exists()


def test_schema_problems_fail_the_build(make_film):
    film = make_film()
    s = script_of(film)
    shot(s, "S02")["hold"] = "half a second"
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    assert code == 1 and any(g["gate"] == "script.schema" and g["status"] == "FAIL" for g in out["gates"])


def test_missing_prerequisites_exit_three(make_film, slate_home):
    film = make_film()
    (film / "script" / "script.json").unlink()
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    assert code == 3 and out["error"] == "missing_prerequisite" and "script.json" in out["message"]
    film2 = make_film("other", film={"brand": "nobody"})
    code, out, _ = cli("script", "build", "--film", str(film2), "--json")
    assert code == 3 and "nobody" in out["message"]


def test_no_film_is_a_usage_error(slate_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = cli("script", "build", "--json")
    assert code == 2 and out["error"] == "usage"


# --- shot ids freeze at the build gate ---------------------------------------------------------------------------------------

def test_ids_freeze_at_the_first_passing_build(make_film):
    film = make_film()
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    gate = next(g for g in out["gates"] if g["gate"] == "script.shot_ids")
    assert gate["status"] == "PASS" and "freeze at this build" in gate["summary"]
    sb = jsonio.read(film / "script" / "storyboard.json")
    assert [x["id"] for x in sb["shot_ids"]["ids"]] == ["S01", "S02", "S03", "S04"]
    lock = jsonio.read(film / "film.lock.json", default=None)
    if lock is not None:  # recorded in film.lock.json when the state module is installed
        assert lock["frozen"]["shot_ids"]["ids"] == ["S01", "S02", "S03", "S04"]


def test_adding_and_cutting_shots_keeps_the_frozen_ids(make_film):
    film = make_film(film=NO_TARGET)
    cli("script", "build", "--film", str(film))
    s = script_of(film)
    s["acts"][1]["shots"].insert(1, {"id": "S05", "vo": "Crust first.", "picture": "A knife.", "hold": 1.0})
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    gate = next(g for g in out["gates"] if g["gate"] == "script.shot_ids")
    assert code == 0 and gate["status"] == "PASS" and gate["details"]["added"] == ["S05"]


def renumber_script() -> dict:
    s = copy.deepcopy(SCRIPT)
    s["ids"] = "renumber"
    s["id_prefix"] = "S"
    shot(s, "S04")["picture"] = "The door opens; the same oven as S01, now bright."
    return s


def test_renumber_mode_numbers_in_order_and_remaps_mentions(make_film):
    film = make_film(script=renumber_script(), film=NO_TARGET)
    s = script_of(film)
    s["acts"][0]["shots"].insert(0, {"id": "S00", "vo": "", "picture": "Black.", "dur": 1.0})
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    assert code == 0
    assert out["ids"] == {"S00": "S01", "S01": "S02", "S02": "S03", "S03": "S04", "S04": "S05"}
    sb = jsonio.read(film / "script" / "storyboard.json")
    last = sb["acts"][1]["shots"][-1]
    assert last["id"] == "S05" and last["source_id"] == "S04" and "same oven as S02" in last["picture"]


def test_a_renumber_is_allowed_until_scenes_use_the_ids(make_film):
    film = make_film(script=renumber_script(), film=NO_TARGET)
    assert cli("script", "build", "--film", str(film))[0] == 0
    s = script_of(film)
    s["acts"][0]["shots"].insert(0, {"id": "S00", "vo": "", "picture": "Black.", "dur": 1.0})
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    gate = next(g for g in out["gates"] if g["gate"] == "script.shot_ids")
    assert code == 0 and gate["status"] == "PASS" and "renumbered before any scene used the ids" in gate["summary"]


def test_a_renumber_is_refused_once_a_scene_uses_the_ids(make_film):
    film = make_film(script=renumber_script(), film=NO_TARGET)
    assert cli("script", "build", "--film", str(film))[0] == 0
    scene = film / "scenes" / "act1" / "beats.ts"
    scene.parent.mkdir(parents=True)
    scene.write_text("export const door = word('S02', 'ovens');\n")
    before = (film / "script" / "storyboard.json").read_bytes()
    s = script_of(film)
    s["acts"][0]["shots"].insert(0, {"id": "S00", "vo": "", "picture": "Black.", "dur": 1.0})
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    gate = next(g for g in out["gates"] if g["gate"] == "script.shot_ids")
    assert code == 1 and out["refused"] and gate["status"] == "FAIL"
    assert "renumber is refused" in gate["summary"] and "S02: scenes/act1/beats.ts" in gate["evidence"][0]
    assert (film / "script" / "storyboard.json").read_bytes() == before


def test_cutting_a_shot_a_scene_still_uses_is_refused(make_film):
    film = make_film(film=NO_TARGET)
    cli("script", "build", "--film", str(film))
    (film / "scenes").mkdir()
    (film / "scenes" / "act2.tsx").write_text("const s = shot('S04');\n")
    s = script_of(film)
    s["acts"][1]["shots"] = [x for x in s["acts"][1]["shots"] if x["id"] != "S04"]
    s["acts"][1]["shots"][0]["hold"] = 3.0
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    gate = next(g for g in out["gates"] if g["gate"] == "script.shot_ids")
    assert code == 1 and gate["status"] == "FAIL" and "S04 would be cut" in gate["summary"]


def test_story_and_film_state_apply_the_same_renumber_rule(make_film):
    """One rule in both places (docs/PLAN.md §3): the build gate script.shot_ids and slate.state.lock.check_renumber
    agree on the same renumber, before and after a scene uses the ids."""
    import pytest

    from slate.errors import Refused
    from slate.state import lock

    film = make_film(script=renumber_script(), film=NO_TARGET)
    assert cli("script", "build", "--film", str(film))[0] == 0
    frozen = {x["source_id"]: x["id"] for x in jsonio.read(film / "script" / "storyboard.json")["shot_ids"]["ids"]}
    s = script_of(film)
    s["acts"][0]["shots"].insert(0, {"id": "S00", "vo": "", "picture": "Black.", "dur": 1.0})
    save_script(film, s)

    def verdicts():
        code, out, _ = cli("script", "build", "--film", str(film), "--check", "--json")
        gate = next(g for g in out["gates"] if g["gate"] == "script.shot_ids")
        mapping = {frozen.get(src, src): new for src, new in out["ids"].items()}  # old id -> new id, per shot
        try:
            state = lock.check_renumber(film, mapping)["allowed"]
        except Refused:
            state = False
        return gate["status"] == "PASS", state

    assert verdicts() == (True, True)  # frozen, but no scene uses them: both allow it
    scene = film / "scenes" / "act1" / "beats.ts"
    scene.parent.mkdir(parents=True)
    scene.write_text("export const door = word('S02', 'ovens');\n")
    assert verdicts() == (False, False)  # a scene uses them: both refuse
    with pytest.raises(Refused, match="scenes/act1/beats.ts"):
        lock.check_renumber(film, {"S02": "S03"})


def test_scene_scan_ignores_binaries_and_dependencies(make_film):
    film = make_film(script=renumber_script(), film=NO_TARGET)
    cli("script", "build", "--film", str(film))
    (film / "scenes" / "node_modules" / "x").mkdir(parents=True)
    (film / "scenes" / "node_modules" / "x" / "index.js").write_text("S02")
    (film / "scenes" / "still.png").write_bytes(b"\x89PNG\0\0S02")
    s = script_of(film)
    s["acts"][0]["shots"].insert(0, {"id": "S00", "vo": "", "picture": "Black.", "dur": 1.0})
    save_script(film, s)
    assert cli("script", "build", "--film", str(film))[0] == 0


# --- retiming from the real takes ---------------------------------------------------------------------------------------------

def test_from_takes_retimes_every_voiced_shot_so_each_read_fits(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    code, out, _ = cli("script", "build", "--from-takes", "--film", str(film), "--json")
    assert code == 0 and out["timing"] == "takes"
    sb = jsonio.read(film / "script" / "storyboard.json")
    durs = {s["id"]: s["dur"] for a in sb["acts"] for s in a["shots"]}
    # film C's rule: lead + take + tail (act tail at an act break) + hold
    assert durs == {"S01": 2.0, "S02": round(0.3 + 3.1 + 0.9 + 0.5, 2), "S03": round(0.3 + 3.4 + 0.3 + 0.8, 2),
                    "S04": round(0.3 + 1.0 + 0.9 + 1.5, 2)}
    assert sb["timing"]["source"] == "takes"
    assert sb["timing"]["takes"]["S02"] == {"file": "voice/takes/S02.wav", "read_seconds": 3.1}
    assert all(s["dur_source"] == "take" for a in sb["acts"] for s in a["shots"] if s["vo"])


def test_retiming_keeps_the_pace_ceiling(make_film):
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04").update(vo="Bring a friend and take a warm loaf home tonight.", hold=0)
    film = make_film(script=s, takes={"S02": 3.1, "S03": 3.4, "S04": 1.0}, film=NO_TARGET)
    code, out, _ = cli("script", "build", "--from-takes", "--film", str(film), "--json")
    assert code == 0
    sb = jsonio.read(film / "script" / "storyboard.json")
    s04 = next(x for a in sb["acts"] for x in a["shots"] if x["id"] == "S04")
    assert s04["dur"] == round(10 / 2.6 * 1.04, 2)  # the read is short; the words set the floor


def test_from_takes_needs_every_voiced_line(make_film):
    film = make_film(takes={"S02": 3.1})
    code, out, _ = cli("script", "build", "--from-takes", "--film", str(film), "--json")
    assert code == 3 and "S03, S04" in out["message"] and "slate voice tts" in out["hint"]
    assert not (film / "script" / "storyboard.json").exists()


def test_take_timing_sticks_and_new_lines_are_planned(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0}, film=NO_TARGET)
    cli("script", "build", "--from-takes", "--film", str(film))
    s = script_of(film)
    s["acts"][1]["shots"].append({"id": "S05", "vo": "See you tomorrow.", "picture": "Lights out.", "hold": 0.5})
    save_script(film, s)
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    sb = jsonio.read(film / "script" / "storyboard.json")
    durs = {x["id"]: (x["dur"], x["dur_source"]) for a in sb["acts"] for x in a["shots"]}
    assert code == 0 and sb["timing"]["source"] == "takes" and sb["timing"]["planned_lines"] == ["S05"]
    assert durs["S02"] == (4.8, "take") and durs["S05"][1] == "planned"
    assert durs["S04"] == (round(0.3 + 1.0 + 0.3 + 1.5, 2), "take")  # no longer the act's last shot


def test_retime_follows_the_voice_modules_selected_take(make_film):
    film = make_film()
    voice_takes(film, {"S02": 2.5})
    folder = film / "voice" / "takes" / "S02"
    folder.mkdir(parents=True)
    (film / "voice" / "takes" / "S02.wav").rename(folder / "t02.paced.wav")
    write_json(film / "voice" / "takes.json", {"shots": {"S02": {"selected": "t02", "approved": None, "takes": {
        "t01": {"raw": "voice/takes/S02/t01.wav"}, "t02": {"raw": "voice/takes/S02/t02.wav",
                                                           "paced": "voice/takes/S02/t02.paced.wav"}}}}})
    from slate.timing.takes import find_takes

    takes = find_takes(film)
    assert list(takes) == ["S02"] and takes["S02"].rel == "voice/takes/S02/t02.paced.wav"
    assert takes["S02"].stage == "paced" and round(takes["S02"].read_seconds, 3) == 2.5


def test_a_polished_take_comes_before_the_stage_it_was_polished_from(make_film):
    """The voice module's own order (polished, paced, clean, raw): the studio polish is what the film plays."""
    film = make_film()
    voice_takes(film, {"S02": 2.5})
    folder = film / "voice" / "takes" / "S02"
    folder.mkdir(parents=True)
    (film / "voice" / "takes" / "S02.wav").rename(folder / "t01.paced.wav")
    (folder / "t01.polished.wav").write_bytes((folder / "t01.paced.wav").read_bytes())
    write_json(film / "voice" / "takes.json", {"shots": {"S02": {"selected": "t01", "approved": None, "takes": {
        "t01": {"raw": "voice/takes/S02/t01.wav", "paced": "voice/takes/S02/t01.paced.wav",
                "polished": "voice/takes/S02/t01.polished.wav"}}}}})
    from slate.timing.takes import STAGES, find_takes

    assert STAGES[0] == "polished"
    takes = find_takes(film)
    assert takes["S02"].rel == "voice/takes/S02/t01.polished.wav" and takes["S02"].stage == "polished"
    assert round(takes["S02"].read_seconds, 3) == 2.5


def test_a_locked_storyboard_is_refused(make_film, monkeypatch):
    from slate.story import builder

    film = make_film()
    monkeypatch.setattr(builder, "_locked", lambda film_dir: "script/storyboard.json is locked in film.lock.json")
    code, out, _ = cli("script", "build", "--film", str(film), "--json")
    assert code == 1 and out["error"] == "refused" and "locked" in out["message"]


def test_a_pin_written_into_film_json_leaves_the_storyboard_and_its_gates_fresh(make_film):
    """The storyboard is made from a few film.json keys (brand, format, mode, targets, language, title), recorded as
    film:<key> inputs, so a pin or a new version written into film.json doesn't make it (or its gates) stale; a change
    to one of those keys does."""
    from slate import gates
    from slate.state import lock

    film = make_film(film=NO_TARGET)
    assert cli("script", "build", "--film", str(film))[0] == 0
    inputs = lock.entry(film, "script/storyboard.json")["inputs"]
    assert {"film:brand", "film:format", "film:mode", "film:targets", "brand:crumb#truth"} <= set(inputs)
    assert "film.json" not in inputs and "brand:crumb" not in inputs  # the blocks the gates read, not the whole pack
    fj = json.loads((film / "film.json").read_text())
    fj["pins"] = {"remotion": "4.0.521"}
    write_json(film / "film.json", fj)
    assert not lock.is_stale(film, "script/storyboard.json")
    assert all(r.status != "STALE" for name, r in gates.load_report(film).items() if name.startswith("script."))
    fj["targets"]["runtime_s"] = 20
    write_json(film / "film.json", fj)
    assert lock.is_stale(film, "script/storyboard.json")
    assert "input film:targets changed" in lock.explain(film, "script/storyboard.json")["reasons"]


def test_python_interface_returns_gate_results(make_film):
    from slate import story
    from slate.gates import GateResult

    film = make_film()
    results = story.build(film)
    assert results and all(isinstance(r, GateResult) for r in results)
    assert (film / "script" / "storyboard.json").is_file()
