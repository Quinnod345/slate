"""Review fixes for the access gate: a contact sheet that can't be read leaves the safe zones UNCHECKED without taking
the other checks down, and the captions check reads the same rule as qa-core's captions gate."""

from __future__ import annotations

import json

from qa_motion_helpers import make_film
from slate.qa import access as A
from slate.qa import ocr
from slate.qa.film import FilmContext
from test_qa_access_sheets import engine_sheet


class NoText:
    engine = {"name": "scripted OCR (tests)", "version": "0"}

    def recognize(self, image):
        return []


def test_an_unreadable_contact_sheet_leaves_the_zones_unchecked_and_the_rest_reported(slate_home, monkeypatch):
    from slate.qa import brandinfo

    monkeypatch.setattr(A, "_pack_fn", lambda name: None)
    monkeypatch.setattr(brandinfo, "load", lambda name: ({"palette": {"ink": "#101010", "paper": "#fdfdfd"},
                                                        "contrast_pairs": [{"fg": "ink", "bg": "paper"}]}, "test"))
    film = make_film(slate_home["films"], size=(540, 960), fmt="no-such-format")
    engine_sheet(film / "review", film=(960, 540))  # landscape cells: they can't have come from this 9:16 film
    with ocr.use_backend(NoText()):
        result = A.evaluate(film)
    checks = {c["check"]: c for c in result.details["checks"]}
    assert checks["safe_zones"]["status"] == "UNCHECKED" and "can't be located" in checks["safe_zones"]["summary"]
    assert checks["contrast"]["status"] == "PASS"  # the other checks still ran and still say what they found
    assert result.status == "UNCHECKED"


def test_the_captions_check_and_the_captions_gate_read_one_rule(slate_home):
    from slate.qa.checks import text

    src = slate_home["tmp"] / "formats" / "reel.json"
    src.parent.mkdir(parents=True)
    src.write_text(json.dumps({"name": "reel", "width": 540, "height": 960, "captions": {"max_cps": 14}}))
    film = make_film(slate_home["films"], size=(540, 960), fmt="reel",
                     extra={"format_source": str(src), "captions": {"max_chars": 30}})
    ctx = FilmContext(film)
    limits, where = A.caption_limits(ctx)
    rules, same_where = text.caption_rules(ctx)
    assert limits == {k: rules[k] for k in ("max_cps", "max_lines", "max_chars")} and where == same_where
    assert limits == {"max_cps": 14.0, "max_lines": 2, "max_chars": 30}
    assert where == "the law, tightened by the reel format (max_cps 14) and film.json (max_chars 30)"
