"""Platform files: a format's encode.files (an app-store preview's H.264, a hero loop's MP4 and WebM) are encoded from
the master into deliver/vN/platform/, checked against the platform's frame-rate and size limits, and listed in the
manifest like every other file. Also: a draft never offers itself for approval, the labels record names the film's
own channels, and a dry-run review names every channel it would use."""

from __future__ import annotations

from pathlib import Path

import pytest

import filmkit
from slate import jsonio
from slate.errors import UsageError
from slate.finish import deliver, deps


def mastered(make_film, film_id: str, fmt: str, **kw) -> Path:
    d = make_film(film_id, mode=kw.pop("mode", "autonomous"), format=fmt, **kw)
    code, out, err = filmkit.cli(d, "master")
    assert code == 0, err
    filmkit.pass_gates(d)
    return d


def _platform(m: dict) -> dict[str, dict]:
    return {f["kind"]: f for f in m["files"] if f["kind"].startswith("platform:")}


def test_platform_files_follow_the_formats_encode_spec(make_film):
    d = mastered(make_film, "plat", "finish-loop")
    code, plan = filmkit.cli_json(d, "deliver", "--dry-run")
    assert code == 0 and "platform/plat_v1_web-mp4.mp4 (loop, MP4)" in plan["makes"]
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0 and out["final"], out
    m = jsonio.read(d / "deliver/v1/manifest.json")
    assert jsonio.validate(m, "manifest") == []
    got = _platform(m)
    assert set(got) == {"platform:web-mp4", "platform:web-webm"}
    mp4, webm = got["platform:web-mp4"], got["platform:web-webm"]
    assert mp4["file"] == "platform/plat_v1_web-mp4.mp4" and webm["file"] == "platform/plat_v1_web-webm.webm"
    assert mp4["media"]["video"]["codec_name"] == "h264" and webm["media"]["video"]["codec_name"] == "vp9"
    assert "audio" not in mp4["media"] and "audio" not in webm["media"]  # a loop carries no audio track at all
    for f in (mp4, webm):
        assert (f["media"]["video"]["width"], f["media"]["video"]["height"]) == (90, 160)
        assert f["media"]["video"]["r_frame_rate"] == "25/1" and f["publishing"] == "not approved for publishing"
        assert jsonio.sha256(d / "deliver/v1" / f["file"]) == f["sha256"]
    raw = (d / "deliver/v1" / mp4["file"]).read_bytes()
    assert raw.index(b"moov") < raw.index(b"mdat")  # faststart: a browser can start playing at once


def test_a_platform_file_keeps_its_sound_only_when_asked(make_film):
    d = mastered(make_film, "store", "finish-preview")
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0, out
    f = _platform(jsonio.read(d / "deliver/v1/manifest.json"))["platform:store"]
    assert f["media"]["video"]["profile"] == "High"
    assert f["media"]["audio"]["codec_name"] == "aac" and f["media"]["audio"]["channels"] == 2
    assert str(f["media"]["audio"]["sample_rate"]) == "48000"


def _limits(monkeypatch, **enc):
    real = deliver.platform_specs

    def tighter(film):
        got, specs = real(film)
        return {**got, **enc}, specs

    monkeypatch.setattr(deliver, "platform_specs", tighter)


def test_a_platform_file_over_its_size_limit_refuses_a_final_delivery(make_film, monkeypatch):
    d = mastered(make_film, "big", "finish-preview")
    _limits(monkeypatch, max_bytes=1000)
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "over the platform's limit of 1,000" in err
    assert not (d / "deliver/v1").exists() and not list((d / "deliver").glob(".staging-*"))
    code, out = filmkit.cli_json(d, "deliver", "--draft")  # a draft still arrives, and names what's missing
    assert code == 0 and any("over the platform's limit" in m["expected"] for m in out["missing"])
    m = jsonio.read(d / f"{out['folder']}/manifest.json")
    assert not _platform(m)


def test_a_clock_the_platform_doesnt_take_refuses_before_anything_is_encoded(make_film, monkeypatch):
    d = mastered(make_film, "fast", "finish-preview")
    _limits(monkeypatch, max_fps=24)
    encoded = []
    monkeypatch.setattr(deliver.cuts, "platform_file", lambda *a, **k: encoded.append(a) or {})
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "at most 24 fps and the film runs at 25/1" in err and encoded == []
    code, out = filmkit.cli_json(d, "deliver", "--draft")
    assert code == 0 and any("at most 24 fps" in m["expected"] for m in out["missing"]) and encoded == []


def test_a_draft_never_offers_itself_for_approval(make_film):
    d = mastered(make_film, "drafted", "finish-tiny", mode="draft", brand="open-brand")
    code, out, err = filmkit.cli(d, "deliver", "--draft")
    assert code == 0 and "slate approve final_cut" not in out and "Nothing in a draft" in out
    (man,) = (d / "deliver/drafts").glob("v*/manifest.json")
    assert jsonio.read(man)["publishing"]["approve"] == deliver.DRAFT_APPROVE
    code, out = filmkit.cli_json(d, "master")
    assert "slate deliver --draft" in out["next"]


def test_the_film_format_is_read_at_the_films_own_aspect(monkeypatch):
    import types

    calls = []

    def load(name, aspect=None):
        calls.append((name, aspect))
        if aspect == "4:5":
            raise UsageError("Format x comes in 16:9, 9:16, not 4:5.")
        chosen = aspect or "16:9"
        return {"name": name, "aspect": chosen, "aspects": {"16:9": {"channels": ["youtube"]},
                                                            "9:16": {"channels": ["tiktok", "youtube_shorts"]}}}

    monkeypatch.setitem(__import__("sys").modules, "slate.formats", types.SimpleNamespace(load=load))
    assert deps.film_format({"format": "x", "aspect": "9:16"})["aspect"] == "9:16"
    assert deps.film_format({"format": "x", "aspect": "4:5"})["aspect"] == "16:9"  # not offered: the default
    assert calls == [("x", "9:16"), ("x", "4:5"), ("x", None)]
    assert deps.film_format({"aspect": "9:16"}) is None


def test_labels_name_the_films_own_channels(make_film, monkeypatch, tmp_path):
    import types

    d = make_film("chan", mode="draft", brand="open-brand", format="finish-tiny", renders=False, mix=False)
    fj = jsonio.read(d / "film.json")
    fake = types.SimpleNamespace(load=lambda name, aspect=None: {
        "aspect": aspect, "aspects": {aspect: {"channels": ["tiktok", "instagram_reels"]}}})
    monkeypatch.setitem(__import__("sys").modules, "slate.formats", fake)
    assert deps.format_platforms(d, fj["format"]) == ["tiktok", "instagram_reels"]
    fake.load = lambda name, aspect=None: {"aspect": aspect, "platforms": ["youtube"], "aspects": {}}
    assert deps.format_platforms(d, fj["format"]) == ["youtube"]


def test_a_dry_run_review_names_every_channel(make_film, monkeypatch, sends):
    d = make_film("chans", mode="supervised", brand="open-brand")
    filmkit.cli(d, "review", "--no-send")
    monkeypatch.setenv("SLATE_NOTIFY_IMESSAGE", "owner@example.invalid")
    code, out, err = filmkit.cli(d, "review", "--version", "1", "--to", "imessage", "--dry-run")
    assert code == 0 and sends == []
    assert "imessage to ow…@example.invalid via" in out and "chans_v1_phone.mp4: not sent: dry run" in out
    assert "not configured: No push route is set up" in out
