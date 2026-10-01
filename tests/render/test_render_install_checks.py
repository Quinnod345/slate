"""Before an act render replaces the registered one, it proves itself (L-render-S001): the stability report against
the act's accepted baseline, and, for a rendering-only change, the static-frame regression against the render it
replaces. A FAIL keeps the new render aside with a strip that shows why, and installs nothing.

The checks run on real renders here (small ffmpeg files made from numpy frames: one that pops, one that doesn't), and
the runner's refusal runs against the render fakes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from slate import jsonio
from slate.errors import Refused
from slate.render import install_checks, registry, runner
from render_fakes import FakeMedia

W, H, N = 320, 180, 30
FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").is_file() else None)
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg isn't installed")


def _write(path: Path, frames: list[np.ndarray]) -> Path:
    """Frames (H x W x 3, uint8) as a lossless 4:4:4 video, as decodable as an act render."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"".join(np.ascontiguousarray(f, dtype=np.uint8).tobytes() for f in frames)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "25",
                    "-i", "-", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv444p",
                    str(path)], input=raw, check=True, timeout=120)
    return path


def _ground() -> np.ndarray:
    """A still, textured ground: it never moves, so the only change is what a test puts on it."""
    rng = np.random.default_rng(7)
    g = (rng.integers(60, 140, size=(H // 5, W // 5, 1), dtype=np.uint8)).repeat(5, 0).repeat(5, 1)
    assert g.shape[:2] == (H, W)
    return np.repeat(g, 3, axis=2)


def _render(path: Path, *, pop_at: int | None = None) -> Path:
    frames = []
    for i in range(N):
        f = _ground().copy()
        if pop_at is not None and i >= pop_at:
            f[60:120, 140:200] = 235  # a block that appears with no animation, and holds: a pop
        frames.append(f)
    return _write(path, frames)


@pytest.fixture
def act_film(tmp_path):
    film = tmp_path / "film"
    (film / "timing").mkdir(parents=True)
    jsonio.write_atomic(film / "film.json", {"id": "pops", "fps_num": 25, "fps_den": 1, "width": W, "height": H})
    return film


@needs_ffmpeg
def test_a_rendering_only_change_that_pops_is_refused_with_a_strip(act_film):
    old = _render(act_film / "renders" / "act1.mov")
    new = _render(act_film / "renders" / "act1.new.mov", pop_at=15)
    out = install_checks.check(act_film, act=1, frame_in=0, new=new, old=old, rendering_only=True)
    by = {g["gate"]: g for g in out["gates"]}
    # the stability report sees the pop: new against the act's (empty) baseline, for a person to look at
    assert by["render.act1.install.stability"]["status"] == "REVIEW"
    assert "pop" in by["render.act1.install.stability"]["summary"]
    # the regression: a rendering-only change mustn't change the picture, so it fails and nothing is installed
    assert by["render.act1.install.regression"]["status"] == "FAIL"
    assert out["refuse"] and "rendering-only change changed the picture" in out["refuse"]
    assert out["strips"] and (act_film / out["strips"][0]).is_file()


@needs_ffmpeg
def test_a_content_change_skips_the_regression_and_accept_change_lets_a_deliberate_one_in(act_film):
    old = _render(act_film / "renders" / "act1.mov")
    new = _render(act_film / "renders" / "act1.new.mov", pop_at=15)
    changed = install_checks.check(act_film, act=1, frame_in=0, new=new, old=old, rendering_only=False)
    assert changed["refuse"] is None
    assert [g["gate"] for g in changed["gates"]] == ["render.act1.install.stability"]
    assert any("isn't expected to match" in n for n in changed["notes"])
    accepted = install_checks.check(act_film, act=1, frame_in=0, new=new, old=old, rendering_only=True,
                                    accept_change=True)
    assert accepted["refuse"] is None
    reg = next(g for g in accepted["gates"] if g["gate"].endswith(".regression"))
    assert reg["status"] == "REVIEW" and reg["details"]["accepted"] is True


@needs_ffmpeg
def test_a_clean_rerender_passes_both(act_film):
    old = _render(act_film / "renders" / "act1.mov")
    new = _render(act_film / "renders" / "act1.new.mov")
    out = install_checks.check(act_film, act=1, frame_in=0, new=new, old=old, rendering_only=True)
    assert out["refuse"] is None
    assert {g["gate"]: g["status"] for g in out["gates"]} == {"render.act1.install.stability": "PASS",
                                                             "render.act1.install.regression": "PASS"}


def test_the_content_hash_ignores_the_engine_and_follows_the_scenes(fakes, film):
    a = install_checks.content_hash(film, {"brand": None})
    assert a == install_checks.content_hash(film, {"brand": None})
    (film / "scenes" / "act1" / "index.tsx").write_text("// changed\n", encoding="utf-8")
    assert install_checks.content_hash(film, {"brand": None}) != a


def test_the_runner_refuses_to_install_keeps_the_render_aside_and_accepts_on_request(fakes, film, monkeypatch):
    first = runner.run(film, act=1)["renders"][0]
    assert registry.find(film, 1)["content_hash"] == install_checks.content_hash(film, jsonio.read(film / "film.json"))
    seen = []

    def check(film_dir, **kw):
        seen.append(kw)
        if kw["accept_change"]:
            return {"gates": [], "refuse": None, "strips": [], "notes": []}
        return {"gates": [{"gate": "render.act1.install.regression", "status": "FAIL", "summary": "it moved"}],
                "refuse": "a rendering-only change changed the picture: it moved", "strips": ["qa/stability/strips/x.png"],
                "notes": []}

    monkeypatch.setattr(install_checks, "check", check)
    monkeypatch.setattr(FakeMedia, "name", "ffmpeg")  # the checks run with the real media backend only
    with pytest.raises(Refused, match="wasn't installed") as exc:
        runner.run(film, act=1)
    assert seen[0]["rendering_only"] is True  # the same scenes, timing and brand as the registered render
    assert exc.value.details["rejected"] == "renders/act1.rejected.mov"
    assert (film / "renders" / "act1.rejected.mov").is_file()
    assert registry.find(film, 1)["sha256"] == first["sha256"]  # the registered render is untouched
    assert "--accept-change" in (exc.value.hint or "")
    renders_before = len(fakes.bridge.renders())
    out = runner.run(film, act=1, accept_change=True)
    assert out["renders"][0]["registered"] and seen[-1]["accept_change"] is True
    assert len(fakes.bridge.renders()) == renders_before  # the kept chunks: nothing rendered twice


def test_a_draft_and_a_first_render_skip_the_checks(fakes, film, monkeypatch):
    calls = []
    monkeypatch.setattr(install_checks, "check", lambda *a, **k: calls.append(k) or {"gates": [], "refuse": None,
                                                                                    "strips": [], "notes": []})
    monkeypatch.setattr(FakeMedia, "name", "ffmpeg")
    runner.run(film, act=1)  # the act's first render: nothing to replace
    runner.run(film, act=1, draft=True)  # a draft is never delivered
    assert calls == []
    runner.run(film, act=1)
    assert len(calls) == 1


def test_a_rollout_needs_a_reference_and_stops_at_a_failing_preview(fakes, film, monkeypatch):
    runner.run(film, act=1)
    with pytest.raises(Refused, match="no reference render to roll out against"):
        runner.run(film, act=1, rollout=True)
    ref = film / "renders" / "reference_act1.mkv"
    FakeMedia.save(ref, FakeMedia.load(film / "renders" / "act1.mov"))
    jsonio.write_atomic(film / "qa" / "stability" / "references.json", {"acts": {"1": {"file": str(ref.relative_to(film))}}})
    monkeypatch.setattr(install_checks, "rollout_preview",
                        lambda film_dir, **kw: {"gate": "render.act1.rollout.preview", "status": "FAIL",
                                                "summary": "the type moved where it rests"})
    renders_before = len(fakes.bridge.renders())
    with pytest.raises(Refused, match="stopped at its preview"):
        runner.run(film, act=1, rollout=True)
    # only the one-sample preview was rendered, never the act at full samples
    after = fakes.bridge.renders()[renders_before:]
    assert len(after) == 1 and after[0]["sub"] == 1
