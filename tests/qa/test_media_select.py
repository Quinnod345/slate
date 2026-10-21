"""read_frames: wanted frames become short select expressions (runs as between(), a capped number per ffmpeg call),
so a long, scattered list of frames still decodes. A select of a few hundred eq() terms used to fail to allocate in
ffmpeg ("Cannot allocate memory"), which left a stability scan unchecked."""

import shutil
import subprocess

import numpy as np
import pytest

from slate.qa import media


def test_runs_and_expression():
    assert media.frame_runs([5, 3, 4, 9, 11, 12]) == [(3, 5), (9, 9), (11, 12)]
    assert media.select_expr([3, 4, 5, 9, 11, 12], base=3) == "between(n\\,0\\,2)+eq(n\\,6)+between(n\\,8\\,9)"


def test_groups_cap_runs_per_call():
    scattered = list(range(0, 600, 2))  # every other frame: 300 runs of one
    groups = media.frame_groups(scattered, span=240, frame_bytes=1)
    assert [i for g in groups for i in g] == scattered
    assert all(len(media.frame_runs(g)) <= media.MAX_SELECT_RUNS for g in groups)
    assert all(g[-1] - g[0] < 240 for g in groups)
    solid = list(range(0, 200))  # one run: one call
    assert media.frame_groups(solid, span=240, frame_bytes=1) == [solid]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_scattered_frames_decode_like_a_straight_read(tmp_path):
    clip = tmp_path / "clip.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=30:duration=10",
                    "-c:v", "ffv1", str(clip)], check=True)
    wanted = list(range(1, 300, 2))  # 150 single frames in two spans
    got = media.read_frames(clip, wanted)
    assert sorted(got) == wanted
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    every = np.frombuffer(raw, np.uint8).reshape(-1, 36, 64, 3)
    for i in (1, 149, 151, 299):
        assert np.array_equal(got[i], every[i])
