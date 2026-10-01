"""The access gate's text checks on every film: title-safe (L-qa-035) and body copy at 28 px at 1080 lines
(L-qa-D007). Text boxes come from a scripted OCR backend; type is measured on real pixels drawn with Pillow."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from qa_motion_helpers import make_film, register_render
from slate import procs
from slate.qa import access as A
from slate.qa import ocr
from slate.qa.film import FilmContext

HAVE_FFMPEG = shutil.which("ffmpeg", path=procs.child_env()["PATH"]) is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg isn't installed")
W, H = 1920, 1080
LINE = "Body copy reads well here"


class ScriptedOCR:
    engine = {"name": "scripted OCR", "version": "test", "recognition_level": "accurate"}

    def __init__(self, lines):
        self.lines = lines

    def recognize(self, image):
        return [dict(x) for x in self.lines]


def _vision_box(x0, y0, x1, y1, w=W, h=H):
    """A pixel box as Vision gives it: [x, y, w, h], fractions, origin bottom-left."""
    return [x0 / w, 1 - y1 / h, (x1 - x0) / w, (y1 - y0) / h]


def _sheet(tmp, size=(480, 270), shade: int = 30):
    """A one-cell contact sheet (OCR of a sheet is cached by its content, so each test's sheet is its own)."""
    folder = tmp / f"sheet{shade}"
    folder.mkdir()
    Image.new("RGB", size, (shade, shade, shade)).save(folder / "t0.png")
    return folder


# ---------------------------------------------------------------------------------------------------- title-safe
def test_a_caption_40_px_from_the_edge_is_outside_title_safe(slate_home):
    film = make_film(slate_home["films"], size=(W, H))
    near = {"text": "Find out where you stand", "conf": 0.98, "box": _vision_box(40, 900, 700, 960)}
    with ocr.use_backend(ScriptedOCR([near])):
        s = A.check_title_safe(FilmContext(film), sheet=str(_sheet(slate_home["tmp"])))
    assert s.status == "FAIL", s.summary
    v = s.details["violations"][0]
    assert v["where"] == ["left"] and v["outside_action_safe"] == ["left"]  # 40 px is inside 67 px of action-safe too
    assert s.details["title_safe_px"] == [96, 54] and "96 px from the sides" in s.summary


def test_text_inside_title_safe_passes_and_only_title_safe_is_failed(slate_home):
    film = make_film(slate_home["films"], size=(W, H))
    inside = {"text": "Hello there", "conf": 0.98, "box": _vision_box(120, 80, 900, 160)}
    between = {"text": "Edge of the frame", "conf": 0.97, "box": _vision_box(80, 400, 600, 460)}  # 80 px: action-safe
    with ocr.use_backend(ScriptedOCR([inside])):
        assert A.check_title_safe(FilmContext(film), sheet=str(_sheet(slate_home["tmp"]))).status == "PASS"
    with ocr.use_backend(ScriptedOCR([between])):
        s = A.check_title_safe(FilmContext(film), sheet=str(_sheet(slate_home["tmp"], shade=31)))
    assert s.status == "FAIL" and s.details["violations"][0]["outside_action_safe"] == []


def test_title_safe_scales_with_the_frame(slate_home):
    film = make_film(slate_home["films"], film_id="reel", size=(540, 960), fmt="social-reel")
    edge = {"text": "Swipe", "conf": 0.99, "box": [0.02, 0.5, 0.3, 0.05]}  # 2% in from the left
    with ocr.use_backend(ScriptedOCR([edge])):
        s = A.check_title_safe(FilmContext(film), sheet=str(_sheet(slate_home["tmp"], size=(270, 480))))
    assert s.status == "FAIL" and s.details["title_safe_px"] == [27, 48]


def test_without_ocr_title_safe_is_unchecked(slate_home):
    film = make_film(slate_home["films"], size=(W, H))
    s = A.check_title_safe(FilmContext(film))  # the default run has no Vision
    assert s.status == "UNCHECKED"


# ---------------------------------------------------------------------------------------------------- type size
def _render_text(film, size_px: int, text: str = LINE, frames: int = 15):
    """An act render (lossless) with one line of white text set at size_px; returns its Vision box."""
    font = ImageFont.load_default(size=size_px)
    img = Image.new("RGB", (W, H), (20, 24, 28))
    d = ImageDraw.Draw(img)
    d.text((300, 500), text, font=font, fill=(240, 240, 240))
    x0, y0, x1, y1 = d.textbbox((300, 500), text, font=font)
    raw = np.asarray(img, np.uint8).tobytes() * frames
    out = film / "renders" / "act1.mkv"
    out.parent.mkdir(parents=True, exist_ok=True)
    procs.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "30",
               "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gbrp", str(out)], input=raw, text=False, check=True)
    register_render(film, 1, "renders/act1.mkv", 0, frames)
    pad = 4
    return _vision_box(x0 - pad, y0 - pad, x1 + pad, y1 + pad)


@needs_ffmpeg
def test_body_copy_set_at_14_px_fails(slate_home):
    film = make_film(slate_home["films"], size=(W, H), acts={1: (0, 15)})
    box = _render_text(film, 14)
    with ocr.use_backend(ScriptedOCR([{"text": LINE, "conf": 0.98, "box": box}])):
        s = A.check_type_size(FilmContext(film))
    assert s.status == "FAIL", s.summary
    row = s.details["small"][0]
    assert row["type_px_at_1080"] < 20 and "under 28 px at 1080 lines" in s.summary


@needs_ffmpeg
def test_body_copy_set_at_34_px_passes(slate_home):
    film = make_film(slate_home["films"], size=(W, H), acts={1: (0, 15)})
    box = _render_text(film, 34)
    with ocr.use_backend(ScriptedOCR([{"text": LINE, "conf": 0.98, "box": box}])):
        s = A.check_type_size(FilmContext(film))
    assert s.status == "PASS", s.summary
    assert s.details["measured"] == 1


@needs_ffmpeg
def test_labels_and_capitals_arent_body_copy(slate_home):
    film = make_film(slate_home["films"], size=(W, H), acts={1: (0, 15)})
    box = _render_text(film, 14, text="AXIS LABEL IN CAPS")
    with ocr.use_backend(ScriptedOCR([{"text": "AXIS LABEL IN CAPS", "conf": 0.98, "box": box},
                                      {"text": "Two words", "conf": 0.98, "box": box}])):
        s = A.check_type_size(FilmContext(film))
    assert s.status == "PASS" and "no body copy" in s.summary


def test_cap_height_ignores_descenders():
    """Lines full of g, p, q and y measure the same as lines without them: the capitals' height, not the descenders'."""
    font = ImageFont.load_default(size=40)

    def measure(text):
        img = Image.new("L", (1200, 90), 20)
        d = ImageDraw.Draw(img)
        d.text((10, 16), text, font=font, fill=235)
        x0, y0, x1, y1 = d.textbbox((10, 16), text, font=font)
        return A.cap_height(np.asarray(img)[y0 - 3:y1 + 3, x0:x1])

    plain = measure("The banded tables hold")
    for line in ("Typography guides every page", "Body copy reads well here", "Quiet waves sway gently by"):
        assert abs(measure(line) - plain) <= 1, line
    assert 26 <= plain <= 32  # about three quarters of 40 px type
