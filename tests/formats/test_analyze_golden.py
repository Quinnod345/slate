"""Golden data: the films' fit_ease.py, rerun through `slate analyze`'s described moves, must give the films' own
measured eases (FILMROOT/analysis/references/measured_eases.json).

The film project is read, never written: the moves are measured in memory. The four moves are fit_ease.py's own,
written as a moves file: a region, a colour range and a measurement. Three of them reproduce the films' results
exactly. The band wipe's orange sits close to its colour thresholds, so a different ffmpeg build's colour conversion
moves a few pixels across them; it's held to 3% of travel on the curve and one frame on its length.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from slate.analyze import eases, library, probe
from slate.motion.eases import bezier_at

WHITE = {"rgb_min": [201, 201, 201], "rgb_max": [255, 255, 255]}
MOVES = {
    "ref-2.mp4": [
        {"name": "r2_lockup_push", "t0": 1.15, "t1": 1.95, "roi": [0, 180, 1280, 240], "mask": WHITE, "metric": "left"},
        {"name": "r2_lockup_rise", "t0": 1.85, "t1": 2.45, "roi": [200, 0, 880, 720], "mask": WHITE, "metric": "top"},
        {"name": "r2_window_pullback", "t0": 5.85, "t1": 7.6, "roi": [0, 300, 1280, 120],
         "mask": {"rgb_min": [239, 239, 239], "rgb_max": [255, 255, 255]}, "metric": "filled_width", "fill": 0.9},
    ],
    "ref-1.mp4": [
        {"name": "r1_band_wipe", "t0": 75.3, "t1": 76.6, "mask": {"rgb_min": [201, 0, 0], "rgb_max": [255, 139, 79]},
         "metric": "filled_centroid_x", "fill": 0.25},
    ],
}
TOLERANCE = {"r1_band_wipe": 0.03}  # of travel, on the curve; the others match to rounding


def _curve(b):
    return np.array([bezier_at(b, u) for u in np.linspace(0, 1, 201)])


@pytest.mark.parametrize("video", sorted(MOVES))
def test_fit_ease_moves_reproduce_the_films_measured_eases(filmroot, video):
    source = filmroot / "inputs" / "references" / video
    golden_path = filmroot / "analysis" / "references" / "measured_eases.json"
    if not source.is_file() or not golden_path.is_file():
        pytest.skip("the film's reference clips or measured eases aren't here")
    golden = json.loads(golden_path.read_text())
    meta = probe.probe(source)
    specs = [dict(s, fill=s.get("fill", eases.DEFAULT_FILL)) for s in MOVES[video]]
    moves, skipped = eases.described_moves(source, meta["video"], specs, cands=library.candidates(), video_meta=meta)
    assert not skipped
    fps = meta["video"]["fps_num"] / meta["video"]["fps_den"]
    for m in moves:
        g = golden[m["name"]]
        tol = TOLERANCE.get(m["name"], 0.002)
        assert float(np.abs(_curve(m["bezier"]) - _curve(g["bezier"])).max()) <= tol, m["name"]
        assert abs(m["duration_s"] - g["duration_s"]) <= 1.0 / fps + 1e-6, m["name"]
        assert abs(m["samples"] - g["samples"]) <= 1, m["name"]
        assert abs(abs(m["travel"]) - abs(g["travel"])) <= 0.02 * abs(g["travel"]), m["name"]
        if m["name"] not in TOLERANCE:
            assert m["bezier"] == g["bezier"] and m["rms"] == pytest.approx(g["rms"], abs=1e-4)
