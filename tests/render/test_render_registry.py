"""The renders registry: the section 12 shape, the schema, replacing an act, history, status, and the timeline lock."""

import json

import jsonschema
import pytest

from slate import jsonio
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.render import registry
from slate.render.plan import acts_from_shots
from render_fakes import make_film

SHA = "a" * 64


def _entry(film_dir, act=1, frames=(0, 30), sha=SHA, **extra):
    f = film_dir / "renders" / f"act{act}.mov"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"render")
    return {"act": act, "engine": "remotion", "file": f"renders/act{act}.mov", "frames": list(frames), "samples": 16,
            "sha256": sha, "made_at": "2026-09-30T12:00:00+00:00", "bundle_hash": "b1", **extra}


def test_register_writes_the_section_12_shape_and_locks_the_timeline(fakes, film):
    registry.register(film, _entry(film))
    data = json.loads((film / "renders" / "registry.json").read_text())
    assert data["generated"].startswith("do not edit by hand")
    [e] = data["entries"]
    for key in ("act", "engine", "file", "frames", "samples", "sha256", "made_at", "bundle_hash"):
        assert key in e
    assert e["frames"] == [0, 30] and e["file"] == "renders/act1.mov"
    # slate.state saw the registered render (the timeline lock) and recorded what it was cut from
    assert fakes.state.timeline_checks == 1
    assert fakes.state.records[0]["producer"] == "slate render --act 1"
    assert fakes.state.records[0]["inputs"] == ["timing/shots.json", "timing/timeline.json"]
    assert registry.entries(film) == data["entries"]


def test_a_state_module_without_timeline_locked_gets_the_timing_files_locked(fakes, film, monkeypatch):
    import sys

    monkeypatch.delattr(sys.modules["slate.state.lock"], "timeline_locked")
    info = registry.register_and_lock(film, _entry(film))
    assert info["method"] == "lock_asset"
    assert [p for p, _r in fakes.state.locked] == ["timing/shots.json", "timing/timeline.json"]
    assert all("rendered" in reason for _p, reason in fakes.state.locked)


def test_registry_file_matches_its_schema(fakes, film):
    registry.register(film, _entry(film))
    registry.register(film, _entry(film, act=2, frames=(30, 54), engine="ae", samples=None))
    schema = json.loads((jsonio.SCHEMAS_DIR / "renders_registry.schema.json").read_text())
    jsonschema.validate(json.loads(registry.path(film).read_text()), schema)


def test_register_replaces_an_act_and_keeps_its_history(fakes, film):
    registry.register(film, _entry(film, act=2, frames=(30, 54)))
    registry.register(film, _entry(film, act=1))
    registry.register(film, _entry(film, act=1, sha="b" * 64, samples=4))
    entries = registry.entries(film)
    assert [e["act"] for e in entries] == [1, 2]
    one = registry.find(film, 1)
    assert one["sha256"] == "b" * 64 and one["samples"] == 4
    assert one["history"][-1] == {"what": "replaced", "sha256": SHA, "made_at": "2026-09-30T12:00:00+00:00",
                                  "engine": "remotion", "samples": 16, "frames": [0, 30]}


def test_absolute_paths_inside_the_film_become_relative(fakes, film, tmp_path):
    e = _entry(film)
    e["file"] = str(film / "renders" / "act1.mov")
    registry.register(film, e)
    assert registry.find(film, 1)["file"] == "renders/act1.mov"
    assert registry.find_file(film, film / "renders" / "act1.mov")["act"] == 1
    e["file"] = str(tmp_path / "elsewhere.mov")
    with pytest.raises(UsageError, match="inside the film folder"):
        registry.register(film, e)


@pytest.mark.parametrize("change,match", [
    ({"sha256": None}, "sha256"),
    ({"frames": [30, 10]}, "out after in"),
    ({"engine": "cloud"}, "engine"),
])
def test_bad_entries_are_refused(fakes, film, change, match):
    e = _entry(film)
    e.update(change)
    if change.get("sha256", 1) is None:
        del e["sha256"]
    with pytest.raises(UsageError, match=match):
        registry.register(film, e)
    assert not registry.path(film).exists()


def test_register_refuses_when_state_doesnt_see_the_render(fakes, film, monkeypatch):
    import sys

    monkeypatch.setattr(sys.modules["slate.state.lock"], "timeline_locked", lambda d: False)
    with pytest.raises(Refused, match="timeline isn't locked"):
        registry.register(film, _entry(film))


def test_register_needs_the_render_in_place(fakes, film):
    e = _entry(film)
    (film / "renders" / "act1.mov").unlink()
    with pytest.raises(UsageError, match="isn't there"):
        registry.register(film, e)
    assert not registry.path(film).exists()


def test_register_without_the_state_module_is_a_missing_prerequisite(fakes, film, monkeypatch):
    fakes.without(monkeypatch, "slate.state.lock")
    with pytest.raises(MissingPrerequisite):
        registry.register(film, _entry(film))


def test_a_hand_broken_registry_is_reported(fakes, film):
    registry.path(film).parent.mkdir(parents=True, exist_ok=True)
    registry.path(film).write_text(json.dumps({"entries": [{"act": 1}]}))
    with pytest.raises(UsageError, match="renders_registry"):
        registry.entries(film)


def test_update_records_a_splice(fakes, film):
    registry.register(film, _entry(film))
    new = registry.update(film, 1, sha256="c" * 64, history={"what": "splice", "frames": [3, 9]})
    assert new["sha256"] == "c" * 64 and new["history"][-1]["what"] == "splice"
    with pytest.raises(Refused):
        registry.update(film, 7, sha256="d" * 64)


def test_status_reports_rendered_moved_missing_and_not_rendered(fakes, slate_home):
    film = make_film(slate_home["films"], acts=((1, 30), (2, 24), (3, 10)))
    acts = acts_from_shots(json.loads((film / "timing" / "shots.json").read_text()))
    registry.register(film, _entry(film, act=1, sha=jsonio.sha256_bytes(b"render")))
    registry.register(film, _entry(film, act=2, frames=(30, 50)))  # timing has 30-54 now: moved
    registry.register(film, _entry(film, act=4, frames=(64, 70)))  # no act 4 any more
    rows = {r["act"]: r["state"] for r in registry.status(film, acts)}
    assert rows == {1: "rendered", 2: "moved", 3: "not rendered", 4: "act no longer in timing"}
    (film / "renders" / "act1.mov").write_bytes(b"changed")
    assert registry.status(film, acts)[0]["state"] == "rendered"  # without --verify, hashes aren't read
    assert registry.status(film, acts, verify=True)[0]["state"] == "changed on disk"
    (film / "renders" / "act1.mov").unlink()
    assert registry.status(film, acts)[0]["state"] == "missing file"
