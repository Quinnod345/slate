"""Render status must describe registered pictures and the inputs that produced them.

The movie fixtures are deliberately plain bytes. These tests prove status and dependency behavior without rendering,
media analysis, an engine process, or a vendor call; they do not prove any movie's visual or technical quality.
"""
from __future__ import annotations

import json

import pytest

from slate import gates, jsonio, procs
from slate.render import deps as render_deps
from slate.render import engines, install_checks, registry
from slate.state import status


@pytest.fixture
def render_film(new_film, tmp_path, monkeypatch):
    fd = new_film(request="Make a short film.", request_source="Test Person")
    engine = tmp_path / "neutral-engine"
    (engine / "remotion").mkdir(parents=True)
    (engine / "remotion" / "kit.ts").write_text("export const neutralKit = 1;\n")
    monkeypatch.setattr(render_deps, "engine_dir", lambda: engine)

    def no_process(*args, **kwargs):
        raise AssertionError("Render status must not launch a process or engine bridge command.")

    monkeypatch.setattr(procs, "run", no_process)
    monkeypatch.setattr(render_deps, "bridge_call", no_process)
    (fd / "scenes" / "act1").mkdir(exist_ok=True)
    (fd / "scenes" / "act1" / "index.tsx").write_text("export const neutralScene = 1;\n")
    timing = {
        "total_frames": 60,
        "acts": [
            {"index": 1, "shots": ["S01"]},
            {"index": 2, "shots": ["S02"]},
        ],
        "shots": [
            {"id": "S01", "act": 1, "frame_in": 0, "frame_out": 30},
            {"id": "S02", "act": 2, "frame_in": 30, "frame_out": 60},
        ],
    }
    (fd / "timing" / "shots.json").write_text(json.dumps(timing))
    (fd / "timing" / "timeline.json").write_text("{}")
    return fd, engine


def _stage(fd):
    return next(s for s in status.compute(fd)["stages"] if s["stage"] == "render")


def _register(fd, act, *, legacy=()):
    path = fd / "renders" / f"act{act}.mov"
    path.write_bytes(f"neutral render {act}".encode())
    film = jsonio.read(fd / "film.json")
    entry = {
        "act": act, "engine": "remotion", "file": f"renders/act{act}.mov",
        "frames": [(act - 1) * 30, act * 30], "samples": 4,
        "sha256": jsonio.sha256(path), "made_at": "2026-10-06T00:00:00+00:00",
        "bundle_hash": "neutral-bundle",
        "source_hash": engines.sources_hash(fd, film, "remotion"),
        "content_hash": install_checks.content_hash(fd, film),
    }
    for key in legacy:
        entry.pop(key)
    registry.register(fd, entry)
    return entry


@pytest.mark.parametrize("artifact", ["none", "empty-registry", "unregistered-movie"])
def test_timing_pass_does_not_prove_an_act_was_rendered(render_film, artifact):
    fd, _ = render_film
    if artifact == "empty-registry":
        (fd / "renders" / "registry.json").write_text('{"entries": []}')
    elif artifact == "unregistered-movie":
        (fd / "renders" / "act1.mov").write_bytes(b"unregistered neutral picture")
    gates.write_report(fd, [gates.GateResult.pass_("timing.frames", "the timeline has its expected frames")])
    rendered = _stage(fd)
    assert rendered["state"] == "missing" and not rendered["ok"]
    assert rendered["summary"] == "nothing rendered yet"
    assert rendered["gates"]["timing.frames"] == "PASS"


def test_unchanged_registered_acts_are_fresh(render_film):
    fd, _ = render_film
    _register(fd, 1)
    _register(fd, 2)
    rendered = _stage(fd)
    assert rendered["state"] == "fresh" and rendered["ok"]
    assert rendered["summary"] == "2 act renders registered"
    assert all(a["source_freshness"] == "current" for a in rendered["acts"])


def test_partial_registration_cannot_substitute_for_all_expected_acts(render_film):
    fd, _ = render_film
    _register(fd, 1)
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert "act 2 is not rendered" in rendered["reasons"]
    assert {a["act"]: a["state"] for a in rendered["acts"]} == {1: "rendered", 2: "not rendered"}


def test_scene_edit_invalidates_unchanged_registered_movie_and_timing(render_film):
    fd, _ = render_film
    entry = _register(fd, 1)
    _register(fd, 2)
    before_timing = {p.name: p.read_bytes() for p in (fd / "timing").glob("*.json")}
    (fd / "scenes" / "act1" / "index.tsx").write_text("export const neutralScene = 2;\n")
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert jsonio.sha256(fd / entry["file"]) == entry["sha256"]
    assert {p.name: p.read_bytes() for p in (fd / "timing").glob("*.json")} == before_timing
    row = rendered["acts"][0]
    assert row["source_freshness"] == "changed"
    assert all(v["recorded"] != v["current"] for v in row["hashes"].values())


def test_engine_edit_invalidates_source_without_claiming_changed_content(render_film):
    fd, engine = render_film
    _register(fd, 1)
    _register(fd, 2)
    (engine / "remotion" / "kit.ts").write_text("export const neutralKit = 2;\n")
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    row = rendered["acts"][0]
    assert row["hashes"]["source_hash"]["recorded"] != row["hashes"]["source_hash"]["current"]
    assert row["hashes"]["content_hash"]["recorded"] == row["hashes"]["content_hash"]["current"]


@pytest.mark.parametrize("missing", [
    ("source_hash",), ("content_hash",), ("source_hash", "content_hash"),
])
def test_legacy_hash_gaps_remain_explicitly_unproved(render_film, missing):
    fd, _ = render_film
    _register(fd, 1, legacy=missing)
    _register(fd, 2)
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert rendered["acts"][0]["source_freshness"] == "unproved"
    reasons = " ".join(rendered["reasons"])
    assert "unproved" in reasons and all(key in reasons for key in missing)
    assert "changed since" not in reasons


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_movie_bytes_still_need_to_match_the_registry(render_film, change):
    fd, _ = render_film
    entry = _register(fd, 1)
    _register(fd, 2)
    path = fd / entry["file"]
    if change == "changed":
        path.write_bytes(b"changed neutral picture")
    else:
        path.unlink()
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert rendered["acts"][0]["state"] == ("changed on disk" if change == "changed" else "missing file")


def test_registered_range_must_match_current_timing(render_film):
    fd, _ = render_film
    _register(fd, 1)
    _register(fd, 2)
    timing = jsonio.read(fd / "timing" / "shots.json")
    timing["shots"][0]["frame_out"] = 31
    timing["shots"][1]["frame_in"] = 31
    (fd / "timing" / "shots.json").write_text(json.dumps(timing))
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert all(a["state"] == "moved" for a in rendered["acts"])


@pytest.mark.parametrize("body", ["{broken", "{}"])
def test_existing_movies_without_valid_expected_timing_are_unproved(render_film, body):
    fd, _ = render_film
    _register(fd, 1)
    _register(fd, 2)
    (fd / "timing" / "shots.json").write_text(body)
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert "expected acts cannot be checked" in " ".join(rendered["reasons"])


@pytest.mark.parametrize("body", ["{broken", '{"entries": [{"act": 1}]}'])
def test_broken_registration_is_not_fresh_and_does_not_crash_status(render_film, body):
    fd, _ = render_film
    (fd / "renders" / "registry.json").write_text(body)
    rendered = _stage(fd)
    assert rendered["state"] != "fresh" and not rendered["ok"]
    assert "unproved" in " ".join(rendered["reasons"])
    assert rendered["problems"]


def test_unreadable_current_inputs_are_unproved_not_fresh(render_film, monkeypatch):
    fd, _ = render_film
    _register(fd, 1)
    _register(fd, 2)

    def unreadable(*args, **kwargs):
        raise OSError("neutral source read failure")

    monkeypatch.setattr(install_checks, "content_hash", unreadable)
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert all(a["source_freshness"] == "unproved" for a in rendered["acts"])
    assert "neutral source read failure" in " ".join(rendered["reasons"])


def test_current_hash_functions_are_reused_once_per_content_and_engine(render_film, monkeypatch):
    fd, _ = render_film
    _register(fd, 1)
    _register(fd, 2)
    counts = {"content": 0, "source": 0}
    content_fn, source_fn = install_checks.content_hash, engines.sources_hash

    def content(*args, **kwargs):
        counts["content"] += 1
        return content_fn(*args, **kwargs)

    def source(*args, **kwargs):
        counts["source"] += 1
        return source_fn(*args, **kwargs)

    monkeypatch.setattr(install_checks, "content_hash", content)
    monkeypatch.setattr(engines, "sources_hash", source)
    assert _stage(fd)["ok"]
    assert counts == {"content": 1, "source": 1}


def test_status_read_keeps_registry_and_movie_bytes(render_film):
    fd, _ = render_film
    _register(fd, 1)
    _register(fd, 2)
    protected = [fd / "renders" / "registry.json", fd / "renders" / "act1.mov", fd / "renders" / "act2.mov"]
    before = {str(p): p.read_bytes() for p in protected}
    _stage(fd)
    assert {str(p): p.read_bytes() for p in protected} == before


@pytest.mark.parametrize("shadow", ["missing", "tampered"])
def test_duplicate_registration_cannot_hide_a_bad_shadow_movie(render_film, shadow):
    fd, _ = render_film
    entry = _register(fd, 1)
    _register(fd, 2)
    duplicate = {**entry, "file": "renders/act1-shadow.mov"}
    if shadow == "tampered":
        (fd / duplicate["file"]).write_bytes(b"changed neutral shadow picture")
    record = jsonio.read(fd / "renders" / "registry.json")
    # The bad duplicate comes first. registry.status would otherwise hide it behind the valid original row.
    record["entries"].insert(0, duplicate)
    (fd / "renders" / "registry.json").write_text(json.dumps(record))
    protected = (fd / "renders" / "registry.json").read_bytes()
    rendered = _stage(fd)
    assert rendered["state"] == "stale" and not rendered["ok"]
    assert "2 registry entries share this act" in " ".join(rendered["reasons"])
    duplicates = [row for row in rendered["acts"] if row["act"] == 1]
    assert {row["file"] for row in duplicates} == {entry["file"], duplicate["file"]}
    assert all(row["state"] == "unproved" and row["source_freshness"] == "unproved"
               and row["duplicate_registration"] for row in duplicates)
    assert (fd / "renders" / "registry.json").read_bytes() == protected
