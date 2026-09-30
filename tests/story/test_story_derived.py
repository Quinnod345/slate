"""Derived films' scripts (the keys `slate variant` writes: derived_from, cut, translate) and a new film's empty one."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from storyfix import SCRIPT, write_json

from slate import jsonio
from slate.cli import run_cli
from slate.errors import MissingPrerequisite
from slate.story import derived


def _pair(tmp_path: Path, parent_script: dict, child_script: dict, *, pin_parent: bool = True) -> tuple[Path, Path]:
    parent, child = tmp_path / "parent", tmp_path / "child"
    write_json(parent / "film.json", {"id": "parent", "language": "en"})
    write_json(parent / "script" / "script.json", parent_script)
    extra = {"derived_from": {"film": "parent", "path": "script/script.json",
                              "sha256": jsonio.sha256(parent / "script" / "script.json") if pin_parent else "0" * 64}}
    write_json(child / "film.json", {"id": "child", "language": "en", "parent": {"id": "parent", "dir": str(parent)}})
    write_json(child / "script" / "script.json", {**child_script, **extra})
    return parent, child


def _load(child: Path):
    return derived.load(child, json.loads((child / "film.json").read_text()))


def test_an_ordinary_film_is_not_derived(tmp_path):
    write_json(tmp_path / "script" / "script.json", SCRIPT)
    script, info = derived.load(tmp_path, {"id": "plain"})
    assert script == SCRIPT and info.kind is None and derived.gate(info, ["S01"]) is None


def test_a_cut_of_a_renumbering_parent_keeps_the_output_ids_and_remaps_mentions(tmp_path):
    parent_script = {"title": "p", "ids": "renumber", "id_prefix": "S", "acts": [
        {"name": "A", "shots": [{"id": "a", "vo": "One.", "picture": "Opens."},
                                {"id": "b", "vo": "Two.", "picture": "Same desk as a."}]},
        {"name": "B", "shots": [{"id": "c", "vo": "Three.", "picture": "Back to b."}]}]}
    _parent, child = _pair(tmp_path, parent_script, {"title": "cut", "acts": [], "cut": {"target_s": 5, "keep": ["S03", "S02"]}})
    script, info = _load(child)
    assert info.kind == "cut" and info.parent_ids == ["S01", "S02", "S03"] and not info.problems and not info.changed
    kept = [(sh["id"], sh["picture"]) for a in script["acts"] for sh in a["shots"]]
    assert kept == [("S02", "Same desk as S01."), ("S03", "Back to S02.")]  # the parent's order, its output ids
    assert "ids" not in script and script["title"] == "cut" and script["cut"]["keep"] == ["S03", "S02"]
    assert jsonio.validate(script, "script") == []
    gate = derived.gate(info, ["S02", "S03"])
    assert gate.status == "PASS" and "2 of its 3 shots" in gate.summary


@pytest.mark.parametrize("keep, words", [([], "keeps no shots yet"), (["S01", "S07"], "doesn't have: S07")])
def test_a_cut_that_keeps_nothing_or_strangers_fails_and_says_what_to_do(tmp_path, keep, words):
    _parent, child = _pair(tmp_path, SCRIPT, {"acts": [], "cut": {"target_s": 5, "keep": keep}})
    _script, info = _load(child)
    gate = derived.gate(info, [])
    assert gate.status == "FAIL" and words in gate.summary


def test_a_translation_keeps_every_shot_and_its_id(tmp_path):
    translated = copy.deepcopy(SCRIPT)
    _parent, child = _pair(tmp_path, SCRIPT, {**translated, "translate": {"from": "en", "to": "en-GB"}})
    script, info = _load(child)
    assert info.kind == "translate" and script["acts"] == translated["acts"]  # its own acts, as written
    ids = ["S01", "S02", "S03", "S04"]
    assert derived.gate(info, ids).status == "PASS"
    stranger = derived.gate(info, ["S01", "S02", "S03", "S04", "X9"])
    assert stranger.status == "FAIL" and "X9" in stranger.summary and "keeps every shot's id" in stranger.summary
    missing = derived.gate(info, ["S01", "S02"])
    assert missing.status == "FAIL" and missing.details["missing"] == ["S03", "S04"]


def test_a_parent_whose_script_moved_on_asks_a_person(tmp_path):
    _parent, child = _pair(tmp_path, SCRIPT, {**copy.deepcopy(SCRIPT), "translate": {"from": "en", "to": "en-GB"}},
                           pin_parent=False)
    _script, info = _load(child)
    gate = derived.gate(info, ["S01", "S02", "S03", "S04"])
    assert info.changed and gate.status == "REVIEW" and "changed since this translation was made" in gate.summary


def test_a_missing_parent_is_a_missing_prerequisite(tmp_path):
    write_json(tmp_path / "film.json", {"id": "orphan"})
    write_json(tmp_path / "script" / "script.json", {"acts": [], "cut": {"keep": ["S01"]},
                                                     "derived_from": {"film": "nobody-here"}})
    with pytest.raises(MissingPrerequisite, match="can't be found"):
        derived.load(tmp_path, {"id": "orphan", "parent": {"id": "nobody-here", "dir": str(tmp_path / "gone")}})


def test_a_new_films_empty_script_is_valid_and_the_build_says_to_write_the_first_act(make_film):
    """`slate new` scaffolds script.json with no acts; the schema accepts that (so the qa schema gate doesn't fail a
    fresh film) and the build refuses it with the one next step."""
    from slate.state.scaffold import script_skeleton

    empty = script_skeleton({"title": "Morning Loaf", "language": "en"})
    assert jsonio.validate(empty, "script") == []
    film = make_film(script=empty)
    code, out, _ = run_cli(["script", "build", "--film", str(film), "--json"])
    out = json.loads(out)
    gate = next(g for g in out["gates"] if g["gate"] == "script.structure")
    assert code == 1 and gate["status"] == "FAIL" and "no acts yet: write the first act" in gate["summary"]
    assert not (film / "script" / "storyboard.json").exists()
