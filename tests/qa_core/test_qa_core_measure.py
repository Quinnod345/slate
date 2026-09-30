"""PSNR maths and the video comparison behind the psnr gate and `slate qa psnr A B`."""

import math

import numpy as np
import pytest

from slate.qa import measure

from qa_core_helpers import needs_ffmpeg, moving_square


def test_psnr_known_values():
    a = np.zeros((4, 4), dtype=np.uint8)
    b = a.copy()
    assert measure.psnr(a, b) == math.inf
    b[0, 0] = 16  # one pixel off by 16 → MSE = 256 / 16 = 16
    assert measure.mse(a, b) == pytest.approx(16.0)
    assert measure.psnr(a, b) == pytest.approx(10 * math.log10(255 ** 2 / 16.0))
    # the formula, and its constant: a uniform error of 1 level on 8-bit is 48.13 dB
    c = np.full((8, 8), 101, dtype=np.uint8)
    d = np.full((8, 8), 100, dtype=np.uint8)
    assert measure.psnr(c, d) == pytest.approx(20 * math.log10(255), abs=1e-9)
    assert round(measure.psnr(c, d), 2) == 48.13


def test_psnr_peak_follows_dtype_and_can_be_given():
    a16 = np.zeros((2, 2), dtype=np.uint16)
    b16 = np.ones((2, 2), dtype=np.uint16)
    assert measure.psnr(a16, b16) == pytest.approx(20 * math.log10(65535))
    assert measure.psnr(a16, b16, peak=1023) == pytest.approx(20 * math.log10(1023))  # 10-bit in 16-bit words
    af = np.zeros((2, 2)) + 0.5
    bf = af + 0.1
    assert measure.psnr(af, bf) == pytest.approx(20.0, abs=1e-9)  # float peak 1.0: 10·log10(1 / 0.01)


def test_psnr_needs_same_shape():
    with pytest.raises(ValueError):
        measure.psnr(np.zeros((2, 2)), np.zeros((2, 3)))


def test_psnr_on_image_files(tmp_path):
    from PIL import Image

    a = np.zeros((6, 6, 3), dtype=np.uint8)
    b = a.copy()
    b[:, :, 0] = 3
    Image.fromarray(a).save(tmp_path / "a.png")
    Image.fromarray(b).save(tmp_path / "b.png")
    # MSE = 9 on one channel of three = 3
    assert measure.psnr(tmp_path / "a.png", tmp_path / "b.png") == pytest.approx(10 * math.log10(255 ** 2 / 3.0))


def test_parse_psnr_stats_and_summary():
    text = ("n:1 mse_avg:0.00 mse_y:0.00 psnr_avg:inf psnr_y:inf\n"
            "n:2 mse_avg:1.00 mse_y:1.00 psnr_avg:48.13 psnr_y:48.13\n"
            "n:3 mse_avg:9.00 mse_y:9.00 psnr_avg:38.59 psnr_y:38.59\n")
    vals = measure.parse_psnr_stats(text)
    assert vals[0] == math.inf and vals[1] == 48.13 and vals[2] == 38.59
    s = measure.summarize(vals, threshold=40.0, first_frame=100)
    assert s["frames"] == 3 and s["min_db"] == 38.59 and s["below_count"] == 1
    assert s["below"] == [{"frame": 102, "db": 38.59}]
    assert s["worst"][0] == {"frame": 102, "db": 38.59}
    assert measure.finite(math.inf) == measure.IDENTICAL_DB


@needs_ffmpeg
def test_video_psnr_identical_and_changed(film_builder):
    fb = film_builder(shots=[(1, 12)])
    frames = moving_square(12, fb.size)
    a = fb.encode("a.mkv", frames)
    b = fb.encode("b.mkv", frames)
    vals = measure.video_psnr(a, b)
    assert len(vals) == 12 and all(math.isinf(v) for v in vals)
    changed = frames.copy()
    changed[5] = 255 - changed[5]  # one frame inverted
    c = fb.encode("c.mkv", changed)
    vals = measure.video_psnr(a, c)
    assert len(vals) == 12
    assert vals[5] < 20 and all(math.isinf(v) for i, v in enumerate(vals) if i != 5)


@needs_ffmpeg
def test_video_psnr_against_joined_parts(film_builder):
    fb = film_builder(shots=[(1, 10)])
    frames = moving_square(10, fb.size)
    whole = fb.encode("whole.mkv", frames)
    p1 = fb.encode("p1.mkv", frames[:4])
    p2 = fb.encode("p2.mkv", frames[4:])
    vals = measure.video_psnr(whole, [p1, p2])
    assert len(vals) == 10 and all(math.isinf(v) for v in vals)
