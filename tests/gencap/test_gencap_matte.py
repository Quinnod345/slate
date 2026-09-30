"""Bridge mattes on synthetic frames: the generation's shape reveals real frames; its pixels never become the picture."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from gencap_helpers import run_json
from slate.capture import media
from slate.errors import Refused
from slate.gen import matte

W, H = 64, 36
BASE = np.full((H, W, 3), 235.0, dtype=np.float32)  # the film's own paper
REAL = (40, 90, 60)  # the real plate


def _bridge(n: int = 8) -> list[np.ndarray]:
    """A dark blot growing from the centre of a light frame: what a generated ink bleed looks like to the matte."""
    yy, xx = np.mgrid[0:H, 0:W]
    frames = []
    for k in range(n):
        radius = 2 + 3.2 * k
        blot = ((xx - W / 2) ** 2 + (yy - H / 2) ** 2) <= radius ** 2
        f = np.full((H, W, 3), 230.0, dtype=np.float32)
        f[blot] = 80.0
        frames.append(f)
    return frames


def _real(tmp_path, count: int = 3) -> list:
    paths = []
    for i in range(count):
        p = tmp_path / f"real{i}.jpg"
        Image.new("RGB", (W, H), REAL).save(p, quality=100)
        paths.append(p)
    return paths


def test_the_matte_reveals_real_frames_and_hands_over_exactly(tmp_path):
    out = tmp_path / "frames"
    written = matte.composite(_bridge(), _real(tmp_path), BASE, size=(W, H), fps=10, bridge_fps=8, count=10,
                              params={}, out_dir=out, preview_dir=tmp_path / "preview")
    assert len(written) == 10 == media.count_frames(out)
    frames = [np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) for p in written]
    assert np.abs(frames[0] - BASE).mean() < 3  # the first frame is the film's own base
    assert np.abs(frames[-1] - np.array(REAL, dtype=np.float32)).mean() < 3  # the hand-over is the real frame
    shown = [float(np.abs(f - BASE).mean()) for f in frames]
    assert all(b >= a - 1.0 for a, b in zip(shown, shown[1:]))  # the ink never retreats
    centre, corner = frames[4][H // 2, W // 2], frames[4][1, 1]
    assert np.abs(centre - np.array(REAL)).mean() < np.abs(corner - np.array(REAL)).mean()  # it grows from the blot
    assert sorted(p.name for p in (tmp_path / "preview").glob("m*.png"))


def test_sizes_are_fractions_of_the_frame(tmp_path):
    small = matte.composite(_bridge(), _real(tmp_path), BASE, size=(W, H), fps=10, bridge_fps=8, count=4,
                            params={"tint": "#606060"}, out_dir=tmp_path / "a")
    assert len(small) == 4
    assert 0 < matte.DEFAULTS["blur"] < 1 and 0 < matte.DEFAULTS["reach"] < 2  # fractions, not pixels
    with pytest.raises(Exception):
        matte.composite(_bridge(1), _real(tmp_path), BASE, size=(W, H), fps=10, bridge_fps=8, count=4, params={},
                        out_dir=tmp_path / "b")


def test_a_matte_needs_an_approved_bridge(film):
    args = ["plates", "matte", "--film", str(film), "--shot", "S02", "--base", "refs/start.png", "--real", "S04"]
    code, out, _ = run_json(args)
    assert code == 2 and "no generations" in out["message"]  # nothing generated yet
    run_json(["gen", "run", "--film", str(film), "--shot", "S02"])
    code, out, _ = run_json(args)
    assert code == 1 and "no approved take" in out["message"]  # takes exist, but nobody picked one
    with pytest.raises(Refused):
        matte.build(film, "S02", base="refs/start.png", real="S04")


def test_the_bridge_matte_end_to_end(film):
    run_json(["gen", "run", "--film", str(film), "--shot", "S02"])
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--take", "t1", "--by", "Quinn"])
    run_json(["gen", "run", "--film", str(film), "--shot", "S04"])
    run_json(["gen", "judge", "--film", str(film), "--shot", "S04"])
    run_json(["gen", "pick", "--film", str(film), "--shot", "S04", "--take", "t1", "--by", "Quinn"])
    run_json(["plates", "conform", "--film", str(film), "--shot", "S04", "--frames", "2"])
    Image.new("RGB", (320, 180), (240, 238, 232)).save(film / "refs" / "base.png")
    code, out, err = run_json(["plates", "matte", "--film", str(film), "--shot", "S02", "--base", "refs/base.png",
                               "--real", "S04", "--duration", "1.0"])
    assert code == 0, err
    assert out["frames"]["count"] == 25 and (out["frames"]["width"], out["frames"]["height"]) == (320, 180)
    from slate import jsonio
    from slate.gen import deps

    record = jsonio.read(film / out["matte"])
    assert deps.validate_part(record, "plate", "matte") == []
    assert record["bridge"]["request_id"] and record["base"]["sha256"]
