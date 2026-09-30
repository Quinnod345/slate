"""Derived films: a cut-down, a new aspect or a new language that references its parent without copying."""

from __future__ import annotations

import json

import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.errors import UsageError
from slate.state import approvals, derive, film, lock, status, _util


def _parent(new_film, script_writer):
    """A parent film with a built storyboard, approved script, voice takes (one approved), timing, beats, a plate and
    a score take, as the stages would have recorded them."""
    fd = new_film("parent", request="Make the planner film.", request_source="Test Person")
    script_writer(fd, shots=3)
    files = {
        "script/storyboard.json": ("slate script build", ["script/script.json"]),
        "script/SCRIPT.md": ("slate script build", ["script/script.json"]),
        "voice/takes/S01.wav": ("slate voice tts", ["script/storyboard.json"]),
        "voice/takes/S02.wav": ("slate voice tts", ["script/storyboard.json"]),
        "voice/takes.json": ("slate voice tts", ["script/storyboard.json"]),
        "timing/shots.json": ("slate timing", ["script/storyboard.json"]),
        "timing/timeline.json": ("slate timing", ["timing/shots.json"]),
        "timing/vo_cues.json": ("slate timing", ["voice/takes.json"]),
        "timing/vo_words.json": ("slate timing", ["voice/takes.json"]),
        "timing/visual_events/act1.json": ("slate emit-events", ["timing/shots.json"]),
        "timing/motion/act1.json": ("slate engine motion", ["timing/shots.json"]),
        "scenes/act1/Act1.tsx": ("slate scenes", []),
        "scenes/plates/S02.mov": ("slate plates conform", []),
        "sound/score/act1_take2.wav": ("slate score compose", []),
        "sound/mix/final_mix.wav": ("slate mix", ["timing/vo_cues.json"]),
        "renders/act1.mov": ("slate render --act 1", ["timing/shots.json"]),
    }
    for rel, (producer, inputs) in files.items():
        p = fd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"content of {rel}")
        lock.record(fd, rel, producer=producer, inputs=inputs)
    lock.freeze_shot_ids(fd, ["S01", "S02", "S03"])
    approvals.record(fd, what="script", path="script/storyboard.json", approved_by="Test Person")
    approvals.record(fd, what="claims", path="script/script.json", approved_by="Test Person")
    approvals.record(fd, what="take", path="voice/takes/S01.wav", approved_by="Test Person")
    approvals.record(fd, what="final_cut", path="renders/act1.mov", approved_by="Test Person")
    pinned = film.load(fd)
    from slate.state import pins

    pins.pin(fd, "tts", "fixture-tts-model")
    pins.pin(fd, "voice_id", "fixture-voice-0001")
    return fd, pinned


def _own_files(fd):
    return sorted(p.relative_to(fd).as_posix() for p in _util.walk_files(fd))


def test_aspect_variant_references_the_parent_without_copying(new_film, script_writer, state_env):
    parent, _ = _parent(new_film, script_writer)
    child = derive.create(parent, "parent-vertical", aspect="9:16", git=True)
    cj = film.load(child)
    pj = film.load(parent)
    assert cj["parent"] == {"id": "parent", "dir": str(parent.resolve())}
    assert cj["derivation"]["kind"] == "aspect" and cj["derivation"]["kinds"] == ["aspect"]
    assert (cj["width"], cj["height"], cj["aspect"]) == (720, 1280, "9:16")  # the parent's short side, turned
    assert (cj["fps_num"], cj["fps_den"]) == (pj["fps_num"], pj["fps_den"])  # the parent's clock
    assert cj["pins"] == pj["pins"] and all(e["event"] == "inherited" for e in cj["pin_log"])
    # nothing was copied: the child holds only its own small files, and the parent's work by reference
    own = _own_files(child)
    for rel in ("voice/takes/S01.wav", "timing/shots.json", "script/storyboard.json", "scenes/plates/S02.mov",
                "sound/score/act1_take2.wav"):
        assert rel not in own
        e = lock.entry(child, rel)
        assert e["ref"]["film"] == "parent" and e["ref"]["path"] == rel and e["ref"]["sha256"]
        assert lock.resolve(child, rel) == parent / rel
    assert lock.entry(child, "voice/takes/S01.wav")["locked"]  # the approved take stays locked by reference
    for rel in ("scenes/act1/Act1.tsx", "sound/mix/final_mix.wav", "renders/act1.mov", "timing/motion/act1.json"):
        assert lock.entry(child, rel) is None  # re-staged, re-planned, re-rendered: never reused
    assert cj["derivation"]["reuse"]["count"] >= 10
    assert lock.frozen_shot_ids(child) == ["S01", "S02", "S03"]
    # its own approval: the final cut never carries over; pre-production items it reuses unchanged do
    assert approvals.is_approved(child, "script", "script/storyboard.json")
    assert approvals.is_approved(child, "claims", "script/script.json")
    assert not approvals.records(child, "final_cut")
    st = status.compute(child)
    s = {x["stage"]: x for x in st["stages"]}
    assert s["scenes"]["state"] == "stale" and "re-stage" in s["scenes"]["reasons"][0]
    assert s["sound"]["state"] == "stale" and "re-plan" in s["sound"]["reasons"][0]
    assert s["timing"]["state"] == "fresh" and s["voice"]["state"] in ("fresh", "locked")
    assert s["qa"]["state"] == "missing"  # every gate runs again on the child
    assert st["sittings"]["final_cut"]["pending"] == ["final_cut"]
    assert st["derivation"]["reused"] == cj["derivation"]["reuse"]["count"]
    goal = (child / "brief" / "GOAL.md").read_text()
    assert "Re-stage the layout" in goal and "Re-plan the sound" in goal and "own final-cut approval" in goal
    # the parent changes an approved file: the child's reference goes stale and its approval stops counting
    (parent / "script" / "storyboard.json").write_text("edited in the parent")
    assert lock.is_stale(child, "script/storyboard.json")
    assert not approvals.is_approved(child, "script", "script/storyboard.json")


def test_cutdown_reuses_takes_and_beats_but_recuts_the_script(new_film, script_writer):
    parent, _ = _parent(new_film, script_writer)
    child = derive.create(parent, "parent-15", cut=15)
    cj = film.load(child)
    assert cj["derivation"]["kinds"] == ["cutdown"] and cj["targets"]["runtime_s"] == 15
    assert (cj["width"], cj["height"]) == (1280, 720)
    script = jsonio.read(child / "script" / "script.json")
    assert script["cut"] == {"target_s": 15.0, "keep": []} and script["derived_from"]["film"] == "parent"
    assert lock.entry(child, "voice/takes/S02.wav")["ref"]
    assert lock.entry(child, "timing/visual_events/act1.json")["ref"]
    assert lock.entry(child, "timing/motion/act1.json")["ref"]  # same layout, same motion traces
    assert lock.entry(child, "script/storyboard.json") is None and lock.entry(child, "timing/shots.json") is None
    assert not approvals.is_approved(child, "script", "script/storyboard.json")  # the cut needs its own
    # claims were approved on the parent's script.json, and the cut has its own script.json: approve them again
    assert not approvals.records(child, "claims")
    s = {x["stage"]: x for x in status.compute(child)["stages"]}
    assert s["script"]["state"] == "missing"
    assert any("cut" in r for r in cj["derivation"]["redo"])


def test_language_variant_and_combined_kinds(new_film, script_writer, capsys):
    parent, _ = _parent(new_film, script_writer)
    child = derive.create(parent, "parent-fr", language="fr")
    cj = film.load(child)
    assert cj["language"] == "fr" and cj["derivation"]["language_supported"] is False
    assert "v1 speaks English" in capsys.readouterr().err
    assert lock.entry(child, "voice/takes/S01.wav") is None  # a new language is voiced again
    assert lock.entry(child, "scenes/plates/S02.mov")["ref"]  # plates and score takes still carry
    assert lock.entry(child, "sound/score/act1_take2.wav")["ref"]
    assert jsonio.read(child / "script" / "script.json")["translate"] == {"from": "en", "to": "fr"}
    both = derive.create(parent, "parent-15-vertical", cut=15, aspect="9:16", format="fixture-reel")
    bj = film.load(both)
    assert bj["derivation"]["kinds"] == ["cutdown", "aspect"] and (bj["width"], bj["height"]) == (720, 1280)
    assert bj["derivation"]["restage"]["safe_zones"]["tiktok"]["bottom"] == 0.2
    assert bj["derivation"]["restage"]["type_scale"] == 1.25
    assert lock.entry(both, "timing/motion/act1.json") is None  # a cut reuses motion, a new aspect doesn't: no
    assert lock.entry(both, "voice/takes/S01.wav")["ref"]  # both kinds reuse the takes


def test_derivation_refusals(new_film, script_writer):
    parent, _ = _parent(new_film, script_writer)
    with pytest.raises(UsageError):
        derive.create(parent, "nothing")
    with pytest.raises(UsageError):
        derive.create(parent, "too-long", cut=600)
    with pytest.raises(UsageError):
        derive.create(parent, "same", aspect="16:9")
    with pytest.raises(UsageError):
        derive.create(parent, "parent", aspect="1:1")
    with pytest.raises(UsageError):
        derive.create(parent, "bad-aspect", aspect="tall")


def test_variant_cli_and_new_with_parent(new_film, script_writer):
    parent, _ = _parent(new_film, script_writer)
    code, out, err = run_cli(["variant", "parent", "parent-square", "--aspect", "1:1", "--no-git", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["derivation"]["aspect"] == "1:1" and data["parent"]["id"] == "parent"
    code, out, _ = run_cli(["variant", "parent", "nope", "--json"])
    assert code == 2
    code, out, err = run_cli(["new", "parent-fourfive", "--parent", "parent", "--derivation", '{"aspect": "4:5"}',
                              "--no-git", "--json"])
    assert code == 0, err
    fj = json.loads(out)["film_json"]
    assert (fj["width"], fj["height"]) == (720, 900) and fj["derivation"]["kind"] == "aspect"
    assert derive.children(parent)[0]["id"] in ("parent-square", "parent-fourfive")
