"""film.lock.json: staleness by hash, locked assets and the regenerate guard, the timeline lock, frozen shot ids."""

from __future__ import annotations

import json

import pytest

from slate.errors import Refused, SpendRefused, UsageError
from slate.state import approvals, film, lock


def _write(fd, rel, text):
    p = fd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_record_and_staleness_follow_hashes(new_film):
    fd = new_film()
    _write(fd, "script/script.json", '{"acts": []}')
    _write(fd, "script/storyboard.json", "{}")
    e = lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
    for key in ("producer", "tool_version", "inputs", "sha256", "locked", "made_at"):
        assert key in e
    assert e["inputs"]["script/script.json"] and e["locked"] is False and e["tool_version"].startswith("slate ")
    assert not lock.is_stale(fd, "script/storyboard.json")
    _write(fd, "script/script.json", '{"acts": [1]}')
    assert lock.is_stale(fd, "script/storyboard.json")
    assert "changed" in lock.explain(fd, "script/storyboard.json")["reasons"][0]
    # a hand edit of a generated file is stale too
    lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
    assert not lock.is_stale(fd, "script/storyboard.json")
    _write(fd, "script/storyboard.json", '{"edited": true}')
    assert lock.is_stale(fd, "script/storyboard.json")


def test_staleness_is_transitive_and_honest_about_unknowns(new_film):
    fd = new_film()
    _write(fd, "a.json", "1")
    _write(fd, "b.json", "2")
    _write(fd, "c.json", "3")
    lock.record(fd, "b.json", producer="slate step-b", inputs=["a.json"])
    lock.record(fd, "c.json", producer="slate step-c", inputs=["b.json"])
    assert not lock.is_stale(fd, "c.json")
    _write(fd, "a.json", "changed")
    assert lock.is_stale(fd, "b.json")
    assert lock.is_stale(fd, "c.json")  # b.json's own hash is unchanged, but it's made from something that moved
    assert not lock.is_stale(fd, "c.json", deep=False)
    _write(fd, "unrecorded.json", "{}")
    assert lock.explain(fd, "unrecorded.json")["state"] == "unrecorded" and lock.is_stale(fd, "unrecorded.json")
    assert lock.explain(fd, "nope.json")["state"] == "missing" and lock.is_stale(fd, "nope.json")
    with pytest.raises(UsageError):
        lock.record(fd, "missing.json", producer="slate x", inputs=[])
    with pytest.raises(UsageError):
        lock.record(fd, "/etc/hosts", producer="slate x", inputs=[])  # outside the film


def test_inputs_given_as_hashes_and_absolute_paths(new_film, tmp_path):
    fd = new_film()
    outside = tmp_path / "brand-ref.txt"
    outside.write_text("ref")
    _write(fd, "out.json", "{}")
    e = lock.record(fd, "out.json", producer="slate x", inputs={str(outside): None, "given.json": "0" * 64})
    assert e["inputs"][str(outside.resolve())] and e["inputs"]["given.json"] == "0" * 64
    assert lock.is_stale(fd, "out.json")  # given.json doesn't exist, so its recorded hash can't match
    outside.write_text("moved")
    assert "input" in " ".join(lock.explain(fd, "out.json")["reasons"])


def test_locked_asset_refuses_regeneration_without_force_and_approval(new_film):
    fd = new_film()
    _write(fd, "voice/takes/S01.wav", "take-1")
    lock.record(fd, "voice/takes/S01.wav", producer="slate voice tts", inputs=[])
    lock.lock_asset(fd, "voice/takes/S01.wav", reason="approved take", by="Test Person")
    assert lock.is_locked(fd, "voice/takes/S01.wav")
    assert lock.explain(fd, "voice/takes/S01.wav")["state"] == "locked"
    assert not lock.is_stale(fd, "voice/takes/S01.wav")
    with pytest.raises(Refused):
        lock.guard_regenerate(fd, "voice/takes/S01.wav")
    with pytest.raises(SpendRefused):
        lock.guard_regenerate(fd, "voice/takes/S01.wav", force=True)
    with pytest.raises(SpendRefused):
        lock.guard_regenerate(fd, "voice/takes/S01.wav", force=True, approval_id="ap_0000000000")
    other = approvals.record(fd, what="spend", path="script/script.json", approved_by="Test Person")
    with pytest.raises(SpendRefused):  # a spend approval for another path doesn't cover this one
        lock.guard_regenerate(fd, "voice/takes/S01.wav", force=True, approval_id=other["id"])
    # recording different content over a locked entry is refused too
    _write(fd, "voice/takes/S01.wav", "take-2")
    with pytest.raises(Refused):
        lock.record(fd, "voice/takes/S01.wav", producer="slate voice tts", inputs=[])
    assert lock.explain(fd, "voice/takes/S01.wav")["state"] == "tampered"
    assert lock.tampered(fd) == ["voice/takes/S01.wav"]
    _write(fd, "voice/takes/S01.wav", "take-1")  # put the approved take back
    yes = approvals.record(fd, what="spend", path="voice/takes", approved_by="Test Person", note="re-voice S01")
    out = lock.guard_regenerate(fd, "voice/takes/S01.wav", force=True, approval_id=yes["id"], by="agent")
    assert out["allowed"] and out["locked"] and out["approval_id"] == yes["id"]
    assert not lock.is_locked(fd, "voice/takes/S01.wav")
    hist = lock.entry(fd, "voice/takes/S01.wav")["history"]
    assert hist[-1]["event"] == "unlocked to regenerate" and hist[-1]["approval_id"] == yes["id"]
    _write(fd, "voice/takes/S01.wav", "take-3")
    lock.record(fd, "voice/takes/S01.wav", producer="slate voice tts", inputs=[])  # now allowed
    assert lock.guard_regenerate(fd, "voice/takes/S01.wav") == {"path": "voice/takes/S01.wav", "allowed": True,
                                                                 "locked": False}


def test_approving_a_take_locks_it_by_content(new_film):
    fd = new_film()
    _write(fd, "voice/takes/S02.wav", "x")
    approvals.record(fd, what="take", path="voice/takes/S02.wav", approved_by="Test Person")
    assert lock.is_locked(fd, "voice/takes/S02.wav")
    _write(fd, "sound/score/take1.wav", "y")
    approvals.record(fd, what="score_take", path="sound/score/take1.wav", approved_by="Test Person")
    _write(fd, "scenes/plates/S05.mov", "z")
    approvals.record(fd, what="plate-take:S05", path="scenes/plates/S05.mov", approved_by="Test Person")
    assert set(lock.locked_assets(fd)) == {"voice/takes/S02.wav", "sound/score/take1.wav", "scenes/plates/S05.mov"}


def test_timeline_locks_once_an_act_render_is_registered(new_film):
    fd = new_film()
    _write(fd, "timing/shots.json", "{}")
    assert not lock.timeline_locked(fd)
    assert lock.guard_timeline(fd) == {"allowed": True, "locked": False}
    _write(fd, "renders/registry.json", json.dumps({"entries": [
        {"act": 1, "engine": "remotion", "file": "renders/act1.mov", "frames": [0, 48]}]}))
    assert lock.timeline_locked(fd)
    assert lock.is_locked(fd, "timing/shots.json") and lock.is_locked(fd, "timing/timeline.json")
    (fd / "renders" / "registry.json").unlink()
    assert lock.timeline_locked(fd)  # recorded the first time it was seen: removing a render never unlocks it
    with pytest.raises(Refused):
        lock.guard_timeline(fd)
    with pytest.raises(Refused):
        lock.guard_timeline(fd, force=True)
    ok = lock.guard_timeline(fd, force=True, approved_by="Test Person", reason="re-time act 2 after the new take")
    assert ok["allowed"] and ok["locked"]
    assert lock.load(fd)["frozen"]["timeline"]["history"][-1]["approved_by"] == "Test Person"
    assert lock.clock_frozen_reason(fd)
    with pytest.raises(Refused):
        film.set_value(fd, "width", 640)


def test_shot_ids_freeze_and_a_renumber_is_refused_once_a_scene_uses_them(new_film):
    """docs/PLAN.md §3: shot ids freeze at the build gate, and a renumber is refused once any scene uses them."""
    fd = new_film()
    assert lock.frozen_shot_ids(fd) == []
    assert lock.check_renumber(fd, {"S01": "S02"})["allowed"]  # nothing frozen yet: fine
    _write(fd, "script/storyboard.json", "{}")
    rec = lock.freeze_shot_ids(fd, ["S01", "S02", "S03"])
    assert rec["ids"] == ["S01", "S02", "S03"] and rec["sha256"]
    assert lock.frozen_shot_ids(fd) == ["S01", "S02", "S03"]
    from slate.state import frozen_shot_ids

    assert frozen_shot_ids(fd) == ["S01", "S02", "S03"]  # the helper the story stage calls
    # frozen, but no scene uses the ids yet: a renumber is allowed, and says what it moves
    early = lock.check_renumber(fd, {"S02": "S03", "S03": "S04"})
    assert early["allowed"] and early["renumber"] == ["S02→S03", "S03→S04"] and early["scene_refs"] == {}
    assert lock.check_renumber(fd, {"S01": "S01", "S04": "S04"})["renumber"] == []  # keeping ids, adding new ones
    # once a scene names a frozen id, any renumber is refused, and the refusal names the scene
    _write(fd, "scenes/act1/index.tsx", "export const beats = [{shot: 'S02', id: 'door'}];\n")
    with pytest.raises(Refused, match="scenes use them") as moved:
        lock.check_renumber(fd, {"S02": "S03", "S03": "S04"})
    assert "scenes/act1/index.tsx" in moved.value.message
    with pytest.raises(Refused):
        lock.check_renumber(fd, {"S09": "S03"})  # a new shot can't take a frozen id either
    assert lock.check_renumber(fd, {"S01": "S01", "S04": "S04"})["allowed"]  # adding new ids stays fine
    # cutting a shot is fine unless a scene still uses its id
    assert lock.check_renumber(fd, {"S03": None})["cut"] == ["S03"]
    with pytest.raises(Refused, match="would be cut, but scenes still use it"):
        lock.check_renumber(fd, {"S02": None})
    lock.freeze_shot_ids(fd, ["S01", "S04"])
    assert lock.frozen_shot_ids(fd) == ["S01", "S02", "S03", "S04"]  # ids are only ever added
    with pytest.raises(Refused):
        film.set_value(fd, "fps_num", 50)  # the clock is fixed once ids froze


def test_scene_references_are_whole_ids_in_text_files_only(new_film):
    fd = new_film()
    _write(fd, "scenes/act1/index.tsx", "word('S02', 'lands'); const S020 = 1; // S02b isn't S02 either\n")
    _write(fd, "scenes/act1/notes.md", "S03 opens the act.\n")
    (fd / "scenes" / "act1" / "still.png").write_bytes(b"\x89PNG\0S01 inside a binary")
    _write(fd, "scenes/node_modules/pkg/index.js", "S01")
    _write(fd, "scenes/.cache/x.json", "S01")
    refs = lock.scene_references(fd, ["S01", "S02", "S03"])
    assert refs == {"S02": ["scenes/act1/index.tsx"], "S03": ["scenes/act1/notes.md"]}
    assert lock.scene_references(fd, []) == {}


def test_lock_file_is_valid_and_concurrent_records_all_land(new_film):
    import threading

    from slate import jsonio

    fd = new_film()
    for i in range(12):
        _write(fd, f"timing/visual_events/act{i}.json", str(i))

    def rec(i):
        lock.record(fd, f"timing/visual_events/act{i}.json", producer="slate emit-events", inputs=[])

    threads = [threading.Thread(target=rec, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = jsonio.read(fd / "film.lock.json")
    assert jsonio.validate(data, "film_lock") == []
    assert len(lock.entries(fd, prefix="timing/visual_events")) == 12
    assert len(lock.entries(fd, producer="slate emit")) == 12


def test_frozen_ids_fall_back_to_the_storyboards_own_record(new_film):
    fd = new_film()
    (fd / "script" / "storyboard.json").write_text(json.dumps(
        {"shot_ids": {"ids": [{"id": "S01", "source_id": "S01"}, {"id": "S02", "source_id": "S02"}]}}))
    assert lock.frozen_shot_ids(fd) == ["S01", "S02"]  # a passing `slate script build` wrote it
    assert lock.clock_frozen_reason(fd)
    assert lock.check_renumber(fd, {"S01": "S05"})["renumber"] == ["S01→S05"]  # no scene uses them yet
    _write(fd, "scenes/act1/index.tsx", "shot('S01')\n")
    with pytest.raises(Refused):
        lock.check_renumber(fd, {"S01": "S05"})


def test_named_inputs_brand_film_and_format(new_film, state_env):
    """Regression: the story stage records `brand:<name>` (the pack's brand.json hash) as an input; film state must
    resolve it, not look for a file called that and call the storyboard stale forever."""
    from slate import jsonio

    fd = new_film()
    brand_json = state_env["brands"] / "open-brand" / "brand.json"
    (fd / "script" / "storyboard.json").write_text("{}")
    lock.record(fd, "script/storyboard.json", producer="slate script build",
                inputs={"script/script.json": None, "brand:open-brand": jsonio.sha256(brand_json),
                        "film:targets.runtime_s": None, "format:fixture-explainer": None, "vendor:thing": "x"})
    x = lock.explain(fd, "script/storyboard.json")
    assert x["state"] == "fresh", x
    assert any("can't be checked" in n for n in x.get("notes", []))  # unknown schemes are said, never guessed
    from slate.state import pins

    pins.pin(fd, "tts", "some-model")  # film.json changes, but not targets.runtime_s
    assert lock.explain(fd, "script/storyboard.json")["state"] == "fresh"
    film.set_value(fd, "targets.runtime_s", 45)
    assert "input film:targets.runtime_s changed" in lock.explain(fd, "script/storyboard.json")["reasons"]
    lock.record(fd, "script/storyboard.json", producer="slate script build",
                inputs=["script/script.json", "brand:open-brand"])
    assert not lock.is_stale(fd, "script/storyboard.json")
    brand_json.write_text(brand_json.read_text().replace("#222222", "#333333"))
    assert "input brand:open-brand changed" in lock.explain(fd, "script/storyboard.json")["reasons"]
