"""Full-length channel variants retain their source clock without pretending to be shorter cuts."""

from __future__ import annotations

import json

import pytest

from slate import jsonio, ledger
from slate.errors import Refused, UsageError
from slate.finish.film import Film
from slate.state import approvals, derive, film, lock


def _source(new_film, *, mode="draft", brand="open-brand"):
    parent = new_film("channel-parent", mode=mode, brand=brand, generation="none")
    seconds = 862 * 1001 / 30000

    def exact(data):
        data.update(width=1080, height=1920, aspect="9:16", fps_num=30000, fps_den=1001)
        data["targets"].update(runtime_s=seconds, runtime_range_s=[seconds, seconds])

    film.update(parent, exact)
    docs = {
        "script/script.json": {"title": "Source", "acts": [{"shots": [{"id": "S01", "vo": "A neutral line."}]}]},
        "script/storyboard.json": {"acts": [{"shots": [{"id": "S01", "dur": seconds}]}]},
        "timing/shots.json": {"fps_num": 30000, "fps_den": 1001, "width": 1080, "height": 1920,
                              "total_frames": 862, "acts": [{"index": 1, "shots": ["S01"]}],
                              "shots": [{"id": "S01", "act": 1, "frame_in": 0, "frame_out": 862}]},
        "timing/captions.json": {"cues": [{"text": "A neutral line.", "start": 0.3, "end": 1.5}]},
    }
    for rel, value in docs.items():
        path = parent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(path, value)
        lock.record(parent, rel, producer="slate timing" if rel.startswith("timing/") else "slate script build",
                    inputs={})
    for ext in ("srt", "vtt"):
        rel = f"voice/captions/captions.{ext}"
        path = parent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("neutral caption sidecar")
        lock.record(parent, rel, producer="slate voice captions", inputs={})
    for rel in ("scenes/act1/index.tsx", "sound/mix/final_mix.wav", "renders/act1.mov", "masters/parent.mp4",
                "qa/gates.json"):
        path = parent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("neutral source fixture")
        lock.record(parent, rel, producer="slate fixture", inputs={})
    lock.freeze_shot_ids(parent, ["S01"])
    return parent


def test_full_length_channel_child_keeps_exact_source_timing_and_owns_outputs(new_film):
    parent = _source(new_film)
    before = {rel: (parent / rel).read_bytes() for rel in ("film.json", "timing/shots.json", "timing/captions.json")}
    child = derive.create(parent, "channel-child", channel="tiktok", aspect="9:16", git=False)
    data = film.load(child)  # also validates the public film schema
    assert data["derivation"]["kinds"] == ["channel"]
    assert data["derivation"]["cut_s"] is None
    assert data["targets"]["runtime_s"] == 862 * 1001 / 30000
    assert (data["fps_num"], data["fps_den"], data["width"], data["height"]) == (30000, 1001, 1080, 1920)
    route = data["derivation"]["route"]
    assert (route["channel"], route["route"], route["quality"], route["master"]) == (
        "tiktok", "social", "fast", "ffmpeg")
    assert route["render_samples"] == "render.draft_samples"
    assert data["mode"] == "draft" and data["generation"] == "none"
    for rel in ("script/script.json", "script/storyboard.json", "timing/shots.json", "timing/captions.json"):
        assert not (child / rel).exists()
        assert lock.resolve(child, rel) == parent / rel
        assert lock.entry(child, rel)["ref"]["sha256"] == jsonio.sha256(parent / rel)
    assert Film(child).total_frames == 862
    assert Film(child).captions() == [parent / "timing/captions.json", parent / "voice/captions/captions.srt",
                                     parent / "voice/captions/captions.vtt"]
    for rel in ("scenes/act1/index.tsx", "sound/mix/final_mix.wav", "renders/act1.mov", "masters/parent.mp4",
                "qa/gates.json"):
        assert not (child / rel).exists() and lock.entry(child, rel) is None
    assert not approvals.records(child, "final_cut")
    assert not (child / "script/script.json").is_file()  # source script is reused, not a cut skeleton
    assert all((parent / rel).read_bytes() == value for rel, value in before.items())


@pytest.mark.parametrize("kwargs", [{"aspect": "9:16"}, {"channel": "tiktok", "cut": 862 * 1001 / 30000},
                                    {"channel": "tiktok", "cut": 30}, {"channel": "unknown"}])
def test_channel_support_keeps_existing_no_change_and_cut_refusals(new_film, kwargs):
    parent = _source(new_film)
    with pytest.raises(UsageError):
        derive.create(parent, "refused-child", git=False, **kwargs)
    assert not (parent.parent / "refused-child").exists()


def test_channel_reference_timing_refuses_a_format_with_another_frame_size(new_film):
    parent = _source(new_film)
    with pytest.raises(UsageError, match="frame size"):
        derive.create(parent, "resized-child", channel="tiktok", format="fixture-reel", git=False)
    assert not (parent.parent / "resized-child").exists()


def test_channel_child_cannot_drop_its_parents_recorded_autonomous_mode(new_film, monkeypatch):
    parent = _source(new_film, mode="autonomous", brand="onboarded-brand")
    own = jsonio.read(parent / "film.json")
    own["mode"] = "draft"
    (parent / "film.json").write_text(json.dumps(own))
    child = derive.create(parent, "auto-channel", channel="tiktok", git=False)
    assert film.load(child)["mode"] == "autonomous"
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (False, "agent context"))
    with pytest.raises(Refused, match="autonomous"):
        derive.create(parent, "escaped-channel", channel="tiktok", mode="draft", git=False)
