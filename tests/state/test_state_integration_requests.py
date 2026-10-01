"""Requests other modules made of film state, applied in the integration pass (docs/build_reports/NEEDS.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate.errors import Refused, UsageError
from slate.state import approvals, deps

REPO = Path(__file__).resolve().parents[2]


def test_a_draft_delivery_is_never_the_approved_final_cut(new_film):
    """finish's ask: `slate approved` already refuses a draft's bytes; the approval is refused where it's recorded."""
    fd = new_film("f1")
    draft = fd / "deliver" / "drafts" / "v1" / "f1_v1.mp4"
    draft.parent.mkdir(parents=True)
    draft.write_bytes(b"a draft")
    with pytest.raises(Refused) as exc:
        approvals.record(fd, what="final_cut", path="deliver/drafts/v1/f1_v1.mp4", approved_by="Test Person")
    assert "drafts are never final" in exc.value.message
    assert not [r for r in approvals.load(fd)["approvals"] if r["kind"] == "final_cut"]


def test_a_format_file_goes_through_the_formats_validator(tmp_path):
    """formats' ask: `slate new --format some.json` used to read the file raw; now slate.formats.load validates it, so
    a file `slate formats check` would refuse is refused here too."""
    good = json.loads((REPO / "formats" / "social-reel.json").read_text())
    ok = tmp_path / "my-reel.json"
    ok.write_text(json.dumps({**good, "name": "my-reel"}))
    fmt, source = deps.format_load(str(ok))
    assert source == str(ok.resolve()) and fmt["width"] == 1080 and fmt["height"] == 1920
    bad = tmp_path / "broken.json"
    bad.write_text(json.dumps({**good, "runtime_s": 0.5}))
    with pytest.raises(UsageError) as exc:
        deps.format_load(str(bad))
    assert "runtime_s" in exc.value.message


def test_a_format_loads_at_the_aspect_asked_for():
    fmt, _ = deps.format_load("ad", aspect="9:16")
    assert (fmt["width"], fmt["height"]) == (1080, 1920) and fmt["aspect"] == "9:16"


def test_a_variant_records_the_route_of_the_channel_it_serves(new_film):
    """formats' ask (P252, P369): a variant made for a channel takes that channel's route, recorded in its derivation,
    and the master follows it (a social cut-down masters with ffmpeg)."""
    from slate.state import derive, film

    parent = new_film("hero-film", format="fixture-explainer")
    child = derive.create(parent, "hero-film-tiktok", aspect="9:16", channel="tiktok", git=False)
    d = film.load(child)["derivation"]
    assert d["channel"] == "tiktok" and d["route"]["route"] == "social" and d["route"]["master"] == "ffmpeg"
    plain = derive.create(parent, "hero-film-square", aspect="1:1", git=False)
    assert film.load(plain)["derivation"]["route"] is None
    with pytest.raises(UsageError):
        derive.create(parent, "hero-film-x", aspect="4:5", channel="no-such-channel", git=False)


def test_a_social_route_masters_with_ffmpeg_even_when_supervised():
    from types import SimpleNamespace

    from slate.finish import master

    social = SimpleNamespace(mode="supervised", data={"derivation": {"route": {"master": "ffmpeg", "channel": "tiktok",
                                                                              "label": "Social cut-down"}}})
    method, why = master.choose(social)
    assert method == "ffmpeg" and "tiktok" in why
    hero = SimpleNamespace(mode="supervised", data={"derivation": {"route": {"master": "After Effects …"}}})
    assert master.choose(hero, ae=True)[0] == "ae"
