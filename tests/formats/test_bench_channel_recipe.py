"""A full-source benchmark emits an executable channel variant, with no shortened-cut fiction or media work."""

from __future__ import annotations

import shlex

import pytest

from slate import cli, jsonio
from slate.bench import higgsedit as bench
from slate.qa import media
from slate.state import film, lock


def _source(slate_home, monkeypatch):
    parent = film.create("source-portrait", brand="demo", format="social-reel", mode="draft", generation="none",
                         runtime_s=862 * 1001 / 30000, aspect="9:16", git=False)
    film.update(parent, lambda data: data.update(fps_num=30000, fps_den=1001))
    jsonio.write_atomic(parent / "timing/shots.json", {
        "fps_num": 30000, "fps_den": 1001, "width": 1080, "height": 1920, "total_frames": 862,
        "acts": [{"index": 1, "shots": ["S01"]}],
        "shots": [{"id": "S01", "act": 1, "frame_in": 0, "frame_out": 862}],
    })
    master = slate_home["tmp"] / "master.mov"
    master.write_bytes(b"neutral metadata-only master fixture")
    monkeypatch.setattr(media, "video_info", lambda *_a, **_k: {
        "width": 1080, "height": 1920, "fps_num": 30000, "fps_den": 1001})
    return parent, master


def test_full_source_recipe_creates_a_channel_child_on_the_exact_clock(slate_home, tmp_path, monkeypatch):
    parent, master = _source(slate_home, monkeypatch)
    protocol = bench.plan(film_dir=parent, master=master, ranges="0-862", cut_s=862 * 1001 / 30000,
                          out=tmp_path / "benchmark")
    steps = protocol["arms"]["remotion"]["steps"]
    first = shlex.split(steps[0])
    assert "--cut" not in first
    assert first[:2] == ["slate", "variant"]
    code, _out, err = cli.run_cli([*first[1:], "--no-git", "--json"])
    assert code == 0, err
    child = parent.parent / first[3]
    data = film.load(child)
    assert data["derivation"]["kinds"] == ["channel"]
    assert (data["fps_num"], data["fps_den"], data["width"], data["height"]) == (30000, 1001, 1080, 1920)
    assert lock.resolve(child, "timing/shots.json") == parent / "timing/shots.json"
    assert not any(step.startswith("slate timing ") or step.startswith("slate script build ") for step in steps)
    assert any("reference" in step and "timing" in step for step in steps)
    assert protocol["cut"]["ranges"] == [[0, 862]] and protocol["cut"]["frames"] == 862
    assert jsonio.read(tmp_path / "benchmark/measurements.json") == {"runs": [], "reviews": []}


@pytest.mark.parametrize("ranges", ["0-461", "461-862,0-461"])
def test_partial_or_reordered_selection_keeps_explicit_cut_authoring(slate_home, tmp_path, monkeypatch, ranges):
    parent, master = _source(slate_home, monkeypatch)
    protocol = bench.plan(film_dir=parent, master=master, ranges=ranges, cut_s=10, out=tmp_path / "benchmark")
    assert "--cut" in shlex.split(protocol["arms"]["remotion"]["steps"][0])
