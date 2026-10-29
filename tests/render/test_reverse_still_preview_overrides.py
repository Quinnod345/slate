"""Offline controls for omitted and explicit still-preview engines.

The real stills API resolves neutral shot declarations and frame clocks. Recording
engines return file names only; no render, media read, browser or real engine bridge runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate import procs
from slate.render import deps, engines, stills


def put(film, relative, value):
    path = film / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    for variable, leaf in [("SLATE_DATA", "data"), ("SLATE_FILMS", "films"), ("SLATE_BRANDS", "brands")]:
        folder = tmp_path / leaf
        folder.mkdir()
        monkeypatch.setenv(variable, str(folder))

    def forbidden(*args, **kwargs):
        pytest.fail("Still-preview controls must not start a real child or media engine.")

    monkeypatch.setattr(procs, "run", forbidden)

    def version(command, args, *, timeout=None):
        assert command == "version" and args == {}
        return {"capabilities": {"reverse_web_remotion": {"schema": 1, "enabled": True}}}

    monkeypatch.setattr(deps, "bridge_call", version)


@pytest.fixture
def recording(monkeypatch):
    state = SimpleNamespace(instances=[], draws=[])

    class Engine:
        def __init__(self, name, scene):
            self.name = name
            self.scene = Path(scene) if scene is not None else None
            self.opened = False
            self.closed = False
            state.instances.append(self)

        def preflight(self):
            pass

        def open(self):
            self.opened = True

        def stills(self, act, frames, out_dir, *, scale, samples):
            state.draws.append((self.name, self.scene, act, list(frames), Path(out_dir), scale, samples))
            return {"stills": [{"file": str(Path(out_dir) / f"Act{act}_f{frame:05d}.png")} for frame in frames]}

        def close(self):
            self.closed = True

    def get(name, film_dir, film, *, go=False, scene=None):
        return Engine(name, scene)

    monkeypatch.setattr(engines, "get", get)
    return state


def neutral(tmp_path, owner, *, mixed):
    film = (tmp_path / "neutral-preview").resolve()
    replacement = "web" if owner == "remotion" else "remotion"
    settings = {"id": "neutral-preview", "width": 640, "height": 360, "fps_num": 30000, "fps_den": 1001}
    rows = [
        {"id": "A01", "act": 1, "frame_in": 0, "frame_out": 90},
        {"id": "B01", "act": 2, "frame_in": 90, "frame_out": 102},
        {"id": "X01", "act": 2, "frame_in": 102, "frame_out": 114},
        {"id": "B02", "act": 2, "frame_in": 114, "frame_out": 126},
    ]
    if mixed:
        rows[2]["engine"] = replacement
    put(film, "film.json", settings)
    put(film, "timing/shots.json", {"shots": rows, "acts": [{"index": 1, "shots": ["A01"]},
        {"index": 2, "shots": ["B01", "X01", "B02"]}], "total_frames": 126, "fps_num": 30000, "fps_den": 1001})
    if owner == "web":
        put(film, "scenes/act2/web/index.html", "<!doctype html><title>Neutral owner</title>\n")
    else:
        put(film, "scenes/act2/index.tsx", "export default function NeutralOwner(){return null;}\n")
    scene = film / "scenes" / "shots" / "X01" / replacement
    if mixed:
        entry = "index.html" if replacement == "web" else "index.tsx"
        contents = "<!doctype html><title>Neutral shot</title>\n" if replacement == "web" else (
            "export default function NeutralShot(){return null;}\n"
        )
        put(film, str(Path("scenes") / "shots" / "X01" / replacement / entry), contents)
    return film, scene, replacement


CHOICES = ["omitted", None, "remotion", "web"]
CHOICE_IDS = ["omitted", "explicit-none", "explicit-remotion", "explicit-web"]


def preview(film, choice):
    kwargs = {} if choice == "omitted" else {"engine": choice}
    return stills.stills(film, frames=[114, 113, 90, 102, 102], **kwargs)


def assert_results(result, film):
    assert result["ok"] is True
    assert result["out_dir"] == str(film / "renders" / "stills")
    assert [(row["act"], row["frame"], row["film_frame"]) for row in result["stills"]] == [
        (2, 0, 90), (2, 12, 102), (2, 23, 113), (2, 24, 114)
    ]
    assert [Path(row["file"]).name for row in result["stills"]] == [
        "Act2_f00000.png", "Act2_f00012.png", "Act2_f00023.png", "Act2_f00024.png"
    ]


@pytest.mark.parametrize("owner", ["remotion", "web"])
@pytest.mark.parametrize("choice", CHOICES, ids=CHOICE_IDS)
def test_mixed_preview_uses_default_owner_or_explicit_base_without_relabeling_shot(tmp_path, recording, owner, choice):
    film, scene, replacement = neutral(tmp_path, owner, mixed=True)
    result = preview(film, choice)

    expected_base = owner if choice in ("omitted", None) else choice
    out = film / "renders" / "stills"
    assert recording.draws == [
        (expected_base, None, 2, [0, 24], out, 0.5, 1),
        (replacement, scene, 2, [12, 23], out, 0.5, 1),
    ]
    assert len(recording.instances) == 2
    assert all(engine.opened and engine.closed for engine in recording.instances)
    assert_results(result, film)


@pytest.mark.parametrize("choice", CHOICES, ids=CHOICE_IDS)
def test_pure_web_owner_preserves_remotion_default_and_explicit_preview_choice(tmp_path, recording, choice):
    film, _scene, _replacement = neutral(tmp_path, "web", mixed=False)
    result = preview(film, choice)

    expected = "remotion" if choice in ("omitted", None) else choice
    assert recording.draws == [(expected, None, 2, [0, 12, 23, 24], film / "renders" / "stills", 0.5, 1)]
    assert len(recording.instances) == 1
    assert recording.instances[0].opened and recording.instances[0].closed
    assert_results(result, film)
