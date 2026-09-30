"""Edge tracks on synthetic plates: the land's top edge is found where it really is, from colours given as data."""

from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from gencap_helpers import run_json
from slate.capture import media
from slate.errors import UsageError
from slate.gen import deps, track

W, H = 320, 180


def ridge_y(x: float, t: float) -> float:
    """A ridge that rises in the middle (the crest) and drifts slowly with time."""
    return 0.62 * H - 0.18 * H * math.exp(-((x - W * (0.5 + 0.02 * t)) / (0.18 * W)) ** 2)


def plate_frame(t: float) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (225, 226, 228)  # a pale, unsaturated sky
    for x in range(W):
        top = int(round(ridge_y(x, t)))
        img[top:, x] = (40, 150, 70)  # saturated green land (hue about 136 degrees)
    return img


def test_the_edge_is_where_the_land_starts():
    pts = track.edge(plate_frame(0.0), (100, 170))
    assert len(pts) == track.DEFAULTS["points"]
    errors = [abs(y - ridge_y(x, 0.0)) for x, y in pts]
    assert max(errors) < 2.5 and pts[0][0] == 0 and pts[-1][0] == W - 1


def test_wrong_colours_find_no_land():
    assert track.edge(plate_frame(0.0), (200, 260)) is None
    with pytest.raises(UsageError):
        track.parse_hue("teal")
    assert track.parse_hue("340-20") == (340.0, 20.0)


def test_track_a_conformed_plate_with_its_crest(film):
    frames = film / "plates" / "S02" / "plate" / "frames"
    frames.mkdir(parents=True)
    for i in range(25):  # one second at the film's 25 fps
        Image.fromarray(plate_frame(i / 25)).save(frames / media.frame_name(i), quality=95)
    code, out, err = run_json(["plates", "track", "--film", str(film), "--shot", "S02", "--hue", "100-170",
                               "--every", "0.2"])
    assert code == 0, err
    record = __import__("json").loads((film / out["track"]).read_text())
    assert deps.validate_part(record, "plate", "track") == []
    assert [s["frame"] for s in record["samples"]] == [0, 5, 10, 15, 20]
    for sample in record["samples"]:
        errors = [abs(y - ridge_y(x, sample["t"])) for x, y in sample["pts"]]
        assert max(errors) < 3
        a, b = sample["span"]
        assert 0.1 < a < 0.5 < b < 0.9  # the crest is the rising middle, not the flat ends
