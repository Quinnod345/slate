"""Where the gates read their data: the film's format (from slate.formats, else the file film.json records, else
formats/<name>.json), the format's own safe zones, the brand's resolved colour pairs (so onboarding and the gate read
the same colour), a nested palette when the brand module isn't installed; and one REVIEW place per stretch of fast
motion however the trace covers it."""

from __future__ import annotations

import json

import pytest

from qa_motion_helpers import NEUTRAL_LAW, make_film, register_render, textured_square_frames, write_frames
from slate.qa import access as A
from slate.qa import motion as M
from slate.qa import ocr
from slate.qa.film import FilmContext


# ---------------------------------------------------------------------------------------------------- the format
def _format_file(tmp, **extra) -> str:
    path = tmp / "formats" / "reel.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "reel", "width": 540, "height": 960, "fps_num": 30, "fps_den": 1, **extra}))
    return str(path)


def test_the_format_comes_from_the_file_film_json_records(slate_home):
    src = _format_file(slate_home["tmp"], captions={"max_cps": 15, "max_chars_per_line": 32})
    film = make_film(slate_home["films"], size=(540, 960), fmt="reel", extra={"format_source": src})
    limits, where = A.caption_limits(FilmContext(film))
    assert limits == {"max_cps": 15.0, "max_lines": 2, "max_chars": 32}
    assert where == "the law, tightened by the reel format (max_cps 15, max_chars 32)"
    loose = _format_file(slate_home["tmp"], captions={"max_cps": 25})  # a format may tighten the law, never loosen it
    film2 = make_film(slate_home["films"], film_id="loose", size=(540, 960), fmt="reel", extra={"format_source": loose})
    assert A.caption_limits(FilmContext(film2))[0]["max_cps"] == 17.0


def test_no_format_data_means_the_law_alone(slate_home):
    film = make_film(slate_home["films"], size=(540, 960), fmt="no-such-format")
    assert A._format(FilmContext(film)) is None
    assert A.caption_limits(FilmContext(film))[1].startswith("the law")


def test_the_formats_own_safe_zones_count_before_the_built_in_ones(slate_home):
    zones = {"tiktok": {"top": 0.02, "bottom": 0.02, "left": 0.02, "right": 0.02}}
    src = _format_file(slate_home["tmp"], platforms=["tiktok"], safe_zones=zones)
    film = make_film(slate_home["films"], size=(540, 960), fmt="reel", extra={"format_source": src})
    ctx = FilmContext(film)
    assert A.platforms_for(ctx, None) == (["tiktok"], "the reel format")
    z = A.zone_for(ctx, "tiktok")
    assert z["safe"] == pytest.approx([0.02, 0.02, 0.98, 0.98]) and "safe_zones" in z["source"]
    assert A.zone_for(ctx, "youtube_shorts")["source"].startswith("Google's")  # built in: the format has none


def test_a_landscape_format_can_still_ask_for_zones(slate_home):
    zones = {"broadcast": {"top": 0.05, "bottom": 0.05, "left": 0.05, "right": 0.05}}
    src = _format_file(slate_home["tmp"], width=640, height=360, platforms=["broadcast"], safe_zones=zones)
    film = make_film(slate_home["films"], size=(640, 360), fmt="reel", extra={"format_source": src})
    low = {"text": "Lower third", "conf": 0.95, "box": [0.1, 0.01, 0.5, 0.05]}

    class OCR:
        engine = {"name": "scripted OCR", "version": "test"}

        def recognize(self, image):
            return [dict(low)]

    sheet = slate_home["tmp"] / "frames"
    write_frames(sheet, textured_square_frames(2, size=(640, 360)))
    with ocr.use_backend(OCR()):
        s = A.check_safe_zones(FilmContext(film), sheet=str(sheet))
    assert s.status == "FAIL" and s.details["violations"][0]["platform"].startswith("broadcast")


# ------------------------------------------------------------------------------------------ the brand's colours
def test_the_brand_modules_resolved_colours_are_used():
    # the pair's own tokens don't resolve here, but the brand module already resolved them (fg_hex / bg_hex)
    pairs = [{"fg": "text.primary", "bg": "surface.page", "role": "body", "fg_hex": "#1a1a1a", "bg_hex": "#fafafa"}]
    rows, short = A.check_pairs(pairs, {})
    assert not short and rows[0]["ratio"] == pytest.approx(16.67, abs=0.05)


def test_a_nested_palette_is_flattened_when_the_brand_module_isnt_there(monkeypatch):
    monkeypatch.setattr(A, "_pack_fn", lambda name: None)
    pack = {"palette": {"gray": {"50": "#fafafa", "900": "#1a1a1a"}, "roles": {"ink": "gray.900", "page": "gray.50"}},
            "contrast_pairs": [{"fg": "ink", "bg": "page"}, {"fg": "gray.900", "bg": "gray.50", "role": "large"}]}
    pairs, tokens, where = A.brand_pairs(pack)
    assert where == "brand.json" and tokens["gray.50"] == "#fafafa" and tokens["ink"] == "gray.900"
    rows, short = A.check_pairs(pairs, tokens)
    assert not short and [r["ratio"] for r in rows] == [pytest.approx(16.67, abs=0.05)] * 2


def test_the_gate_and_brand_onboarding_agree_on_every_ratio():
    colour = pytest.importorskip("slate.brand.colour")
    if not (hasattr(colour, "ratio") and hasattr(colour, "parse")):
        pytest.skip("slate.brand.colour doesn't offer ratio/parse yet")
    samples = ["#000000", "#ffffff", "#767676", "#1c1c1c", "#f4f1ea", "#6f6f6f", "#0a84ff", "#ff3b30", "#34c759",
               "#ffd60a", "#5e5ce6", "#8e8e93"]
    for fg in samples:
        for bg in samples:
            ours = A.contrast_ratio(A.parse_colour(fg)[:3], A.parse_colour(bg)[:3])
            theirs = colour.ratio(colour.parse(fg), colour.parse(bg))
            assert ours == pytest.approx(theirs, abs=1e-9), (fg, bg)


# ---------------------------------------------------------------------------------------------------- pixel places
def test_one_stretch_of_fast_motion_is_one_place_however_the_trace_covers_it(slate_home, monkeypatch):
    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))
    size = (640, 360)
    slow = {"id": "square", "shot": "S1", "prop": "x", "ease": "linear", "frame_in": 0, "frame_out": 7, "from": 10,
            "to": 24, "samples": [[k, 10 + 2 * k] for k in range(8)], "entrance": False, "loop_period": None,
            "rig": None}
    film = make_film(slate_home["films"], size=size, acts={1: (0, 16)}, traces={1: [slow]})
    write_frames(film / "renders" / "act1", textured_square_frames(16, size=size, velocity=(24, 0), start=(10, 132),
                                                                   square=96))
    register_render(film, 1, "renders/act1", 0, 16)
    px = next(c for c in M.evaluate(film).details["checks"] if c["check"] == "pixels")
    assert px["status"] == "REVIEW" and len(px["review_items"]) == 1
    item = px["review_items"][0]
    assert item["why"] == "untraced and under-traced" and "part of it has no traced move under way" in item["what"]


def test_the_built_in_zones_are_the_formats_librarys():
    formats = pytest.importorskip("slate.formats")
    if not hasattr(formats, "platforms"):
        pytest.skip("slate.formats has no channel library yet")
    library = formats.platforms()["channels"]
    for channel, built_in in A.BUILTIN_ZONES.items():
        zone = library[channel]["zones"]["9:16"]
        for side in ("top", "bottom", "left", "right"):
            assert built_in[side] == pytest.approx(zone[side]), (channel, side)
        assert [list(k) for k in built_in["keep_out"]] == [list(k) for k in zone.get("keep_out") or []], channel


def test_a_library_format_at_9_16_checks_its_channels_and_a_landscape_one_has_none(slate_home):
    formats = pytest.importorskip("slate.formats")
    names = formats.names() if hasattr(formats, "names") else []
    if "social-reel" not in names or "brand-film" not in names:
        pytest.skip("the formats library doesn't have social-reel and brand-film")
    reel = make_film(slate_home["films"], film_id="lib-reel", size=(1080, 1920), fmt="social-reel",
                     extra={"aspect": "9:16"})
    ctx = FilmContext(reel)
    chosen, why = A.platforms_for(ctx)
    assert chosen == ["instagram_reels", "tiktok", "youtube_shorts"] and why == "the social-reel format at 9:16"
    assert A.caption_limits(ctx)[0]["max_chars"] == 32  # a 9:16 cut gets shorter lines than 42
    zone, where = A.lookup_zone(ctx, "tiktok")
    assert zone["keep_out"] and where.startswith("slate.formats.safe_zones")
    wide = make_film(slate_home["films"], film_id="lib-wide", size=(1920, 1080), fmt="brand-film",
                     extra={"aspect": "16:9"})
    assert A.platforms_for(FilmContext(wide)) == ([], "none")
    assert A.check_safe_zones(FilmContext(wide)) is None
