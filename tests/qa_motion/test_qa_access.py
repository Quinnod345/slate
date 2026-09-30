"""The access gate: WCAG flash tests on generated frames, contrast maths, caption limits and 9:16 safe zones found by
(scripted) OCR."""

from __future__ import annotations

import json
import sys
import types
from fractions import Fraction

import numpy as np
import pytest

from qa_motion_helpers import make_film, register_render, solid_frames, write_frames
from slate import procs
from slate.qa import access as A
from slate.qa import ocr

FPS = Fraction(30)


def _square_wave(period: int, n: int, on, off, size=(48, 27)):
    """Frames alternating between two colours, `period` frames per full cycle."""
    half = period // 2
    return solid_frames([on if (k // half) % 2 else off for k in range(n)], size=size)


# ---------------------------------------------------------------------------------------------------- flashes
@pytest.mark.parametrize("period,expect_pass,flashes", [(6, False, 5.0), (10, True, 3.0), (16, True, 2.0)])
def test_general_flashes_per_second(period, expect_pass, flashes):
    r = A.scan_flashes(_square_wave(period, 60, (255, 255, 255), (0, 0, 0)), FPS)
    assert r["passed"] is expect_pass
    assert r["general"]["max_flashes_per_second"] == flashes


def test_a_change_spread_over_frames_is_one_transition():
    # a slow fade up and down (0.9 s each way) is one flash a cycle, not one per frame
    levels = [int(255 * (0.5 - 0.5 * np.cos(2 * np.pi * k / 54))) for k in range(120)]
    r = A.scan_flashes(solid_frames([(v, v, v) for v in levels]), FPS)
    assert r["passed"] and 0 < r["general"]["max_flashes_per_second"] <= 1.5


def test_flashing_needs_a_quarter_of_a_10_degree_field():
    w, h = 96, 54

    def patch(frac_w, frac_h, n=60, period=4):
        frames = []
        for k in range(n):
            f = np.zeros((h, w, 3), np.uint8)
            if (k // (period // 2)) % 2:
                f[: int(h * frac_h), : int(w * frac_w)] = 255
            frames.append(f)
        return frames

    small = A.scan_flashes(patch(0.05, 0.05), FPS)  # 3% of a field: below the area threshold
    assert small["passed"]
    big = A.scan_flashes(patch(0.34, 0.34), FPS)  # a whole field
    assert not big["passed"] and big["general"]["field"] is not None


def test_changes_among_light_states_are_not_flashes():
    # WCAG: the darker state must be below 0.80 relative luminance; 235 ↔ 255 never is
    r = A.scan_flashes(_square_wave(4, 60, (255, 255, 255), (235, 235, 235)), FPS)
    assert r["passed"] and r["general"]["max_transitions_per_second"] == 0


def test_red_flashes_are_caught_even_without_a_luminance_flash():
    red = (200, 0, 0)
    y = A.relative_luminance(red)
    grey = int(round(255 * ((y ** (1 / 2.4)) * 1.055 - 0.055)))  # a grey of the same luminance
    assert abs(A.relative_luminance((grey, grey, grey)) - y) < 0.01
    r = A.scan_flashes(_square_wave(6, 60, red, (grey, grey, grey)), FPS)
    assert r["general"]["max_flashes_per_second"] == 0
    assert r["red"]["max_flashes_per_second"] == 5.0 and not r["passed"]


def test_a_fine_balanced_pattern_cancels_out():
    # WCAG exempts fine, balanced patterns; averaging to the analysis size removes a 2 px checkerboard's flicker
    yy, xx = np.mgrid[0:108, 0:192]
    board = ((yy // 2 + xx // 2) % 2).astype(bool)
    frames = []
    for k in range(60):
        f = np.where((board ^ bool(k % 2))[..., None], 255, 0).astype(np.uint8).repeat(3, axis=2)
        frames.append(f)
    from PIL import Image

    small = [np.asarray(Image.fromarray(f).resize(A.flash_size(192, 108), Image.Resampling.BOX)) for f in frames]
    assert A.scan_flashes(small, FPS)["passed"]


def test_failing_seconds_are_reported_in_film_frames():
    r = A.scan_flashes(_square_wave(6, 60, (255, 255, 255), (0, 0, 0)), FPS, first_frame=300)
    assert r["failing"] and r["failing"][0][0] >= 300


# ---------------------------------------------------------------------------------------------------- contrast
def test_contrast_maths():
    assert A.relative_luminance((255, 255, 255)) == pytest.approx(1.0)
    assert A.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert A.contrast_ratio((0x77,) * 3, (255,) * 3) == pytest.approx(4.48, abs=0.01)
    assert A.contrast_ratio((0x76,) * 3, (255,) * 3) == pytest.approx(4.54, abs=0.01)
    assert A.parse_colour("#abc") == (170.0, 187.0, 204.0, 1.0)
    assert A.parse_colour("rgba(0, 0, 0, 0.5)") == (0.0, 0.0, 0.0, 0.5)
    assert A.parse_colour([0.5, 0.5, 0.5])[:3] == (127.5, 127.5, 127.5)
    assert A.parse_colour("not a colour") is None


def test_pairs_resolve_tokens_and_hold_body_and_large_text_to_their_bars():
    palette = {"ink": "#777777", "paper": "#ffffff", "muted": "ink", "night": "#101418", "glass": "rgba(255,255,255,0.2)"}
    pairs = [{"fg": "ink", "bg": "paper", "role": "body"},
             {"fg": "muted", "bg": "paper", "role": "large"},
             {"fg": "paper", "bg": "night", "role": "body"},
             {"fg": "rgba(0,0,0,0.5)", "bg": "paper", "role": "body"},
             {"fg": "paper", "bg": "glass", "role": "large"},
             {"fg": "nope", "bg": "paper", "role": "body"}]
    rows, short = A.check_pairs(pairs, palette)
    by = {(r["fg"], r["bg"], r["role"]): r for r in rows}
    assert "problem" in by[("ink", "paper", "body")]  # 4.48 < 4.5
    assert "problem" not in by[("muted", "paper", "large")]  # the same colour passes as large text
    assert by[("paper", "night", "body")]["ratio"] > 15
    assert by[("rgba(0,0,0,0.5)", "paper", "body")]["ratio"] == pytest.approx(3.98, abs=0.02)  # composited first
    # white over a see-through white panel: over a white page it's 1:1, so the worst case fails
    assert "problem" in by[("paper", "glass", "large")]
    assert "isn't a colour or a palette token" in by[("nope", "paper", "body")]["problem"]
    assert len(short) == 4


@pytest.fixture
def no_brand_module(monkeypatch):
    monkeypatch.setattr(A, "_pack_fn", lambda name: None)


def _film(home, **kw):
    return make_film(home["films"], **kw)


def test_contrast_gate_reads_the_brand_pack(slate_home, monkeypatch, no_brand_module):
    pack = {"name": "neutral-test", "palette": {"ink": "#1a1a1a", "paper": "#fafafa", "soft": "#9a9a9a"},
            "contrast_pairs": [{"fg": "ink", "bg": "paper", "role": "body"}, {"fg": "soft", "bg": "paper",
                                                                               "role": "body"}]}
    from slate.qa import brandinfo

    monkeypatch.setattr(brandinfo, "load", lambda name: (pack, "test pack"))
    s = A.check_contrast(__import__("slate.qa.film", fromlist=["context"]).context(_film(slate_home)))
    assert s.status == "FAIL" and "soft on paper" in s.summary
    pack["contrast_pairs"] = pack["contrast_pairs"][:1]
    s = A.check_contrast(__import__("slate.qa.film", fromlist=["context"]).FilmContext(_film(slate_home,
                                                                                             film_id="f2")))
    assert s.status == "PASS"


def test_contrast_uses_the_brand_module_when_it_answers(slate_home, monkeypatch):
    fake = types.ModuleType("slate.brand.pack")
    fake.contrast_pairs = lambda pack: [{"fg": "a", "bg": "b", "role": "large"}]
    fake.palette = lambda pack: {"a": "#000000", "b": "#595959"}
    monkeypatch.setitem(sys.modules, "slate.brand.pack", fake)
    rows, short = A.check_pairs(*A.brand_pairs({"name": "x"})[:2])
    assert rows[0]["ratio"] == pytest.approx(3.0, abs=0.05) and A.brand_pairs({"name": "x"})[2].startswith("slate.")


def test_contrast_without_pairs_is_unchecked(slate_home, monkeypatch, no_brand_module):
    from slate.qa import brandinfo
    from slate.qa.film import FilmContext

    monkeypatch.setattr(brandinfo, "load", lambda name: ({"name": "x", "palette": {}}, "test pack"))
    assert A.check_contrast(FilmContext(_film(slate_home))).status == "UNCHECKED"
    monkeypatch.setattr(brandinfo, "load", lambda name: (None, "no brand pack called 'x'"))
    assert A.check_contrast(FilmContext(_film(slate_home, film_id="f3"))).status == "UNCHECKED"


# ---------------------------------------------------------------------------------------------------- captions
SRT = """1
00:00:01,000 --> 00:00:03,000
Seventeen letters

2
00:00:03,500 --> 00:00:04,500
<i>Eighteen letters!!</i>

3
00:00:05,000 --> 00:00:08,000
one
two
three

4
00:00:08,500 --> 00:00:12,000
This line is forty-three characters long...

5
00:00:12,500 --> 00:00:12,000
backwards
"""

VTT = """WEBVTT - a title

NOTE a comment block
that spans lines

cue-1
00:01.000 --> 00:02.000 align:start position:10%
<v Narrator>Hello there</v>

00:00:02.500 --> 00:00:04.000
Second cue
"""


def test_timed_text_parsing():
    cues = A.parse_timed_text(SRT)
    assert [c.index for c in cues] == [1, 2, 3, 4, 5]
    assert cues[1].lines == ["Eighteen letters!!"] and cues[1].chars == 18
    assert cues[2].lines == ["one", "two", "three"]
    vtt = A.parse_timed_text(VTT)
    assert [(c.start, c.end, c.lines) for c in vtt] == [(1.0, 2.0, ["Hello there"]), (2.5, 4.0, ["Second cue"])]


def test_caption_json_in_seconds_or_frames():
    doc = [{"shot": "S1", "text": "One two", "lines": ["One two"], "start": 1.0, "end": 2.0},
           {"text": "Three\nfour", "frame_in": 60, "frame_out": 90}]
    cues = A.parse_caption_json(doc, fps=30.0)
    assert cues[0].lines == ["One two"] and cues[1].lines == ["Three", "four"] and cues[1].start == 2.0


def test_caption_limits():
    problems = A.check_cues(A.parse_timed_text(SRT), dict(A.CAPTIONS))
    text = " | ".join(p["problem"] for p in problems)
    assert "caption 1" not in text  # 17 characters in 2 s
    assert "caption 2" in text and "18.0 characters a second" in text  # 18 in 1 s
    assert "caption 3" in text and "3 lines" in text
    assert "caption 4" in text and "43-character line" in text
    assert "caption 5" in text and "ends before it starts" in text


def test_exactly_17_characters_a_second_passes():
    cue = A.Cue(0.0, 1.0, ["x" * 17], 1)
    assert A.check_cues([cue], dict(A.CAPTIONS)) == []
    assert A.check_cues([A.Cue(0.0, 1.0, ["x" * 18], 1)], dict(A.CAPTIONS))


def _voiced(home, film_id="voiced"):
    film = _film(home, film_id=film_id)
    (film / "timing" / "vo_cues.json").write_text(json.dumps({"cues": [{"shot": "S1", "at": 0.2, "file": "a.wav"}]}))
    return film


def test_captions_gate(slate_home):
    from slate.qa.film import FilmContext

    film = _voiced(slate_home)
    s = A.check_captions(FilmContext(film))
    assert s.status == "UNCHECKED" and "slate voice captions" in s.details["hint"]
    cap = film / "voice" / "captions"
    cap.mkdir(parents=True)
    (cap / "captions.json").write_text(json.dumps([{"text": "Well paced.", "lines": ["Well paced."],
                                                     "start": 0.5, "end": 1.5}]))
    assert A.check_captions(FilmContext(film)).status == "PASS"
    (cap / "film.srt").write_text(SRT)
    s = A.check_captions(FilmContext(film))
    assert s.status == "FAIL" and s.details["problems_total"] == 4
    silent = _film(slate_home, film_id="silent")
    assert A.check_captions(FilmContext(silent)) is None  # no voice, no captions: nothing to check


def test_a_format_may_tighten_caption_limits(slate_home, monkeypatch):
    from slate.qa.film import FilmContext

    fake = types.ModuleType("slate.formats")
    fake.load = lambda name: {"captions": {"max_chars_per_line": 32, "max_cps": 20}}
    monkeypatch.setitem(sys.modules, "slate.formats", fake)
    film = _voiced(slate_home, "reel")
    (film / "voice" / "captions").mkdir(parents=True)
    (film / "voice" / "captions" / "c.srt").write_text("1\n00:00:00,000 --> 00:00:03,000\nThirty-five characters, give or tak\n")
    s = A.check_captions(FilmContext(film))
    assert s.status == "FAIL" and "the limit is 32" in s.summary
    assert s.details["limits"]["max_cps"] == 17.0  # a format can tighten, never loosen


def test_golden_captions_from_the_films_parse_and_keep_two_lines_of_42(filmroot):
    # read-only: the delivered captions of the first film, in both formats
    folder = filmroot / "build" / "captions"
    srt = A.load_captions(next(folder.glob("*.srt")))
    shots = json.loads((filmroot / "build" / "shots.json").read_text())
    js = A.load_captions(folder / "captions.json", fps=shots["fps_num"] / shots["fps_den"])
    assert len(srt) == len(js) > 50
    assert all(len(c.lines) <= 2 and all(len(line) <= 42 for line in c.lines) for c in srt)
    problems = A.check_cues(srt, dict(A.CAPTIONS))
    assert all("characters a second" in p["problem"] for p in problems)  # only pace can fail there


# ---------------------------------------------------------------------------------------------------- safe zones
def test_zone_shapes_normalise():
    reels = A.normalize_zone(A.BUILTIN_ZONES["instagram_reels"], 1080, 1920, "x")
    assert reels["safe"] == pytest.approx([0.06, 0.14, 0.94, 0.65])
    px = A.normalize_zone({"top": 160, "bottom": 480, "left": 120, "right": 120, "unit": "px", "width": 1080,
                           "height": 1920}, 540, 960, "x")
    assert px["safe"] == pytest.approx([120 / 1080, 160 / 1920, 1 - 120 / 1080, 1 - 480 / 1920])
    rect = A.normalize_zone({"safe": {"x": 54, "y": 96, "width": 972, "height": 1440}, "canvas": {"width": 1080,
                                                                                               "height": 1920}},
                            1080, 1920, "x")
    assert rect["safe"] == pytest.approx([0.05, 0.05, 0.95, 0.8])
    keep = A.normalize_zone({"keep_out": [[0.85, 0.4, 1.0, 0.9]]}, 1080, 1920, "x")
    assert keep["keep_out"] == [[0.85, 0.4, 1.0, 0.9]] and keep["safe"] == [0.0, 0.0, 1.0, 1.0]


def test_ocr_boxes_come_from_the_bottom_left():
    assert A.box_from_ocr([0.1, 0.2, 0.3, 0.1]) == pytest.approx([0.1, 0.7, 0.4, 0.8])


def test_zone_problems():
    z = A.normalize_zone(A.BUILTIN_ZONES["instagram_reels"], 1080, 1920, "x")
    assert A.zone_problems([0.2, 0.3, 0.8, 0.4], z) == []
    assert A.zone_problems([0.2, 0.7, 0.8, 0.75], z) == ["into the bottom 35.0%"]
    k = {"safe": [0, 0, 1, 1], "keep_out": [[0.85, 0.4, 1.0, 0.9]]}
    assert A.zone_problems([0.8, 0.5, 0.9, 0.55], k)[0].startswith("over a covered area")


class ScriptedOCR:
    """Stands in for Vision: returns the same text boxes for every image it's shown."""

    engine = {"name": "scripted OCR", "version": "test", "recognition_level": "accurate"}

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return [dict(x) for x in self.lines]


def _sheet(tmp, n=2, size=(270, 480)):
    from PIL import Image

    folder = tmp / "sheet"
    folder.mkdir()
    for i in range(n):
        Image.new("RGB", size, (30, 30, 30)).save(folder / f"t{i}.png")
    return folder


def _vertical(home, film_id="reel"):
    return make_film(home["films"], film_id=film_id, size=(540, 960), fmt="social-reel")


def test_text_in_a_platforms_covered_band_fails(slate_home):
    from slate.qa.film import FilmContext

    film = _vertical(slate_home)
    # "Tap to learn more" low in the frame (y from the top 0.72–0.76), where Reels puts its caption
    low = {"text": "Tap to learn more", "conf": 0.98, "box": [0.2, 0.24, 0.6, 0.04]}
    mid = {"text": "Hello", "conf": 0.99, "box": [0.3, 0.5, 0.4, 0.05]}
    with ocr.use_backend(ScriptedOCR([low, mid])):
        s = A.check_safe_zones(FilmContext(film), sheet=str(_sheet(slate_home["tmp"])))
    assert s.status == "FAIL"
    platforms = {v["platform"] for v in s.details["violations"]}
    assert platforms == {"instagram_reels", "tiktok"}  # 0.76 is past Reels' 0.65 and TikTok's 0.75
    assert all(v["text"] == "Tap to learn more" for v in s.details["violations"])


def test_text_inside_every_zone_passes(slate_home):
    from slate.qa.film import FilmContext

    film = _vertical(slate_home)
    mid = {"text": "Hello", "conf": 0.99, "box": [0.3, 0.5, 0.4, 0.05]}
    faint = {"text": "noise", "conf": 0.2, "box": [0.0, 0.0, 0.1, 0.1]}  # too unsure to count
    with ocr.use_backend(ScriptedOCR([mid, faint])):
        s = A.check_safe_zones(FilmContext(film), sheet=str(_sheet(slate_home["tmp"])))
    assert s.status == "PASS" and s.details["text_boxes"] == 2


def test_the_formats_zones_win_over_the_built_in_ones(slate_home, monkeypatch):
    from slate.qa.film import FilmContext

    fake = types.ModuleType("slate.formats")
    fake.load = lambda name: {"platforms": ["tiktok"]}
    fake.safe_zones = lambda fmt, platform: {"top": 0.02, "bottom": 0.02, "left": 0.02, "right": 0.02,
                                             "source": "the format's own"}
    monkeypatch.setitem(sys.modules, "slate.formats", fake)
    film = _vertical(slate_home)
    low = {"text": "Low text", "conf": 0.98, "box": [0.2, 0.2, 0.6, 0.04]}
    with ocr.use_backend(ScriptedOCR([low])):
        s = A.check_safe_zones(FilmContext(film), sheet=str(_sheet(slate_home["tmp"])))
    assert s.status == "PASS"
    assert s.details["platforms"]["tiktok"]["source"].startswith("the format")


def test_without_ocr_the_zones_are_unchecked(slate_home, monkeypatch):
    from slate.qa.film import FilmContext

    monkeypatch.setattr(ocr, "available", lambda refresh=False: (False, {"name": "Apple Vision (ocrmac)",
                                                                        "reason": "ocrmac isn't importable",
                                                                        "hint": "install the ocr extra"}))
    s = A.check_safe_zones(FilmContext(_vertical(slate_home)))
    assert s.status == "UNCHECKED" and "ocrmac" in s.summary and s.details["hint"] == "install the ocr extra"


def test_landscape_films_have_no_9_16_zones(slate_home):
    from slate.qa.film import FilmContext

    assert A.check_safe_zones(FilmContext(make_film(slate_home["films"], size=(640, 360)))) is None


def test_a_sheet_with_a_tiles_manifest_is_cut_into_frames(slate_home):
    from PIL import Image

    sheet = slate_home["tmp"] / "contact.png"
    Image.new("RGB", (400, 200), "black").save(sheet)
    (slate_home["tmp"] / "contact.json").write_text(json.dumps({"tiles": [{"rect": [0, 0, 100, 178], "t": 0.0},
                                                                          {"rect": [104, 0, 100, 178], "t": 0.5}]}))
    tiles = A.sheet_images(sheet)
    assert [t[1].size for t in tiles] == [(100, 178), (100, 178)] and "0.5 s" in tiles[1][0]


@pytest.mark.skipif(procs.which("ffmpeg") is None, reason="ffmpeg isn't installed")
def test_zones_read_the_picture_through_the_shared_ocr_sweep(slate_home):
    from slate.qa.film import FilmContext

    film = make_film(slate_home["films"], film_id="swept", size=(180, 320), acts={1: (0, 30)})
    pngs = write_frames(slate_home["tmp"] / "frames", [np.full((320, 180), 60, np.uint8)] * 30)
    (film / "masters").mkdir()
    procs.run(["ffmpeg", "-v", "error", "-y", "-framerate", "30", "-i", str(pngs / "f%05d.png"), "-c:v", "prores_ks",
               "-pix_fmt", "yuv422p10le", str(film / "masters" / "master.mov")], check=True, timeout=120)
    backend = ScriptedOCR([{"text": "Caption", "conf": 0.9, "box": [0.1, 0.05, 0.8, 0.05]}])
    with ocr.use_backend(backend):
        s = A.check_safe_zones(FilmContext(film))
    assert s.status == "FAIL" and backend.calls == 4  # every 0.25 s of a 1-second film
    assert s.details["engine"]["name"] == "scripted OCR" and s.details["frames_read"] == 4


# ---------------------------------------------------------------------------------------------------- the gate
def test_access_gate_on_a_film_with_flashing_picture(slate_home, monkeypatch, no_brand_module):
    from slate.qa import brandinfo

    monkeypatch.setattr(brandinfo, "load", lambda name: ({"palette": {"a": "#000", "b": "#fff"},
                                                          "contrast_pairs": [{"fg": "a", "bg": "b"}]}, "test"))
    film = make_film(slate_home["films"], size=(96, 54), acts={1: (0, 60)})
    write_frames(film / "renders" / "act1", [f for f in _square_wave(6, 60, (255, 255, 255), (0, 0, 0),
                                                                     size=(96, 54))])
    register_render(film, 1, "renders/act1", 0, 60)
    r = A.evaluate(film)
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    assert checks == {"flashes": "FAIL", "contrast": "PASS"}  # no voice, not 9:16: captions and zones don't apply
    assert r.status == "FAIL" and "flashes up to 5 times a second" in r.summary
    only = A.evaluate(film, only=["contrast"])
    assert only.status == "PASS" and [c["check"] for c in only.details["checks"]] == ["contrast"]


def test_no_picture_yet_is_unchecked(slate_home):
    from slate.qa.film import FilmContext

    s = A.check_flashes(FilmContext(make_film(slate_home["films"])))
    assert s.status == "UNCHECKED" and "no picture" in s.summary


def test_caption_pace_is_counted_the_same_way_as_the_captions_gate():
    # a line break reads as a space, as slate.qa's own captions gate counts it: 34 letters on two lines is 35
    cue = A.Cue(0.0, 2.0, ["x" * 17, "y" * 17], 1)
    assert cue.chars == 35 and cue.cps == 17.5
    assert A.check_cues([cue], dict(A.CAPTIONS))  # 17.5 a second is over the limit in both gates
