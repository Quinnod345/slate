"""The format templates: every one validates and loads, carries what plan §8 asks of it, and feeds the modules that
read it (state, story, ledger, QA, voice, sound) the keys they expect."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from slate import formats, paths
from slate.errors import UsageError

EXPECTED = {"brand-film", "explainer", "product-demo", "social-reel", "ad", "linkedin", "website-hero-loop",
            "app-store-preview", "launch-teaser", "feature-announcement", "kinetic-type", "logo-sting", "character-led"}
HERO = {"youtube", "website", "app_store", "broadcast_tv"}
SOCIAL = {"instagram_reels", "instagram_feed", "tiktok", "youtube_shorts", "linkedin"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_every_format_validates_and_loads():
    assert set(formats.names()) == EXPECTED
    assert formats.check_all() == {}
    for name in formats.names():
        fmt = formats.load(name)
        for key in ("runtime_s", "aspects", "fps_num", "fps_den", "pace", "structure", "loudness", "captions",
                    "zones", "budget", "delivery", "broadcast", "width", "height"):
            assert key in fmt, (name, key)
        assert fmt["name"] == name
        assert fmt["structure"]["beats"], name
        assert fmt["width"] % 2 == 0 and fmt["height"] % 2 == 0
        # every aspect has zones for every one of its channels
        for aspect, spec in fmt["aspects"].items():
            assert set(fmt["zones"][aspect]) == set(spec["channels"])


def test_each_template_sets_what_plan_section_8_asks_for():
    for name in formats.names():
        fmt = formats.load(name)
        assert fmt["pace"]["max_words_per_second"] <= 2.6, name
        rt = fmt["runtime_s"]
        assert rt["min"] <= rt["target"] <= rt["max"], name
        assert fmt["captions"]["max_cps"] <= 17 and fmt["captions"]["max_lines"] <= 2, name
        assert fmt["broadcast"] is False, name  # a film sets broadcast: true when it airs
        assert fmt["budget"]["family"] in ("reel_or_ad", "explainer", "brand_film"), name
        if fmt.get("audio") == "none":
            assert fmt["loudness"]["lufs"] is None and not fmt["captions"]["needed"]
        else:
            assert fmt["loudness"]["lufs"] == -14 and fmt["loudness"]["true_peak"] == -1, name


def test_the_formats_named_in_the_plan_have_their_shapes():
    brand = formats.load("brand-film")
    assert (brand["runtime_s"]["min"], brand["runtime_s"]["max"]) == (120, 360) and list(brand["aspects"]) == ["16:9"]
    explainer = formats.load("explainer")
    assert (explainer["runtime_s"]["min"], explainer["runtime_s"]["max"]) == (60, 120)
    demo = formats.load("product-demo")
    assert "slate capture" in demo["capture"]["source"]
    assert demo["capture"]["rebuild_in_code_when"] == ["camera moves", "restaging", "motion blur", "exact type"]
    assert demo["generation"] == "none"
    reel = formats.load("social-reel")
    assert list(reel["aspects"]) == ["9:16"] and (reel["runtime_s"]["min"], reel["runtime_s"]["max"]) == (15, 45)
    assert reel["captions"]["burn_in"] and reel["captions"]["renderer"] == "remotion" and reel["captions"]["font"] == "brand"
    assert set(reel["aspects"]["9:16"]["channels"]) == {"instagram_reels", "tiktok", "youtube_shorts"}
    assert reel["autonomous_default"] is True
    ad = formats.load("ad")
    assert ad["runtime_s"]["options"] == [6, 15, 30] and set(ad["aspects"]) == {"16:9", "1:1", "9:16"}
    assert set(formats.load("linkedin")["aspects"]) == {"1:1", "4:5"}
    character = formats.load("character-led")
    assert "motif" in character["character"]["source"]
    for name in ("launch-teaser", "feature-announcement", "kinetic-type", "logo-sting"):
        assert formats.load(name)["structure"]["beats"]


def test_hero_loop_and_app_store_preview_follow_their_platforms_with_dated_sources():
    loop = formats.load("website-hero-loop")
    assert loop["audio"] == "none" and loop["loop"]["seamless"]
    assert "muted" in loop["loop"]["attributes"] and "playsinline" in loop["loop"]["attributes"]
    assert any("2.2.2" in r for r in loop["loop"]["rules"])
    assert loop["on_screen_max_words"] == 0
    preview = formats.load("app-store-preview")
    assert (preview["runtime_s"]["min"], preview["runtime_s"]["max"]) == (15, 30)
    assert preview["fps_num"] / preview["fps_den"] <= 30 and preview["encode"]["max_fps"] == 30
    assert (preview["width"], preview["height"]) == (886, 1920)
    assert preview["generation"] == "none"
    assert any("people" in r for r in preview["content_rules"])
    for fmt in (loop, preview):
        assert fmt["sources"] and all(DATE.match(s["checked"]) and s.get("url", "").startswith("https://")
                                      for s in fmt["sources"])


def test_budgets_are_the_plans_families():
    fam = {n: formats.budget_family(n) for n in ("reel_or_ad", "explainer", "brand_film", "onboarding")}
    assert fam["reel_or_ad"]["elevenlabs"] == {"vo": [500, 3000], "score": [1000, 2000]} and fam["reel_or_ad"]["higgsfield"] == [0, 0]
    assert fam["explainer"]["elevenlabs"] == {"vo": [1000, 5000], "score": [2500, 5000]} and fam["explainer"]["higgsfield"] == [0, 60]
    assert fam["brand_film"]["elevenlabs"] == {"vo": [4000, 12000], "score": [9000, 28000]} and fam["brand_film"]["higgsfield"] == [40, 85]
    assert fam["onboarding"]["elevenlabs"] == {"voice_design": [5000, 5000]} and fam["onboarding"]["higgsfield"] == [15, 20]
    assert formats.onboarding_budget()["rework_factor"] == 1.5
    for name, family in (("brand-film", "brand_film"), ("explainer", "explainer"), ("social-reel", "reel_or_ad"),
                         ("ad", "reel_or_ad")):
        b = formats.load(name)["budget"]
        assert b["family"] == family
        assert b["elevenlabs"] == fam[family]["elevenlabs"] and b["higgsfield"] == fam[family]["higgsfield"]
    with pytest.raises(UsageError):
        formats.budget_family("nope")


def test_channels_route_hero_placements_through_the_full_pipeline_and_social_cuts_fast():
    lib = formats.channels()
    assert {k for k, v in lib.items() if v["route"] == "hero"} == HERO
    assert {k for k, v in lib.items() if v["route"] == "social"} == SOCIAL
    hero = formats.route_for("website")
    assert hero["route"] == "hero" and hero["quality"] == "master" and hero["pipeline"] == "full"
    social = formats.route_for("TikTok")
    assert social["route"] == "social" and social["quality"] == "fast"
    assert social["label"] == "TikTok" and social["route_label"] == "Social cut-down"  # the channel keeps its name
    assert social["candidate"]["engine"] == "higgsedit" and social["candidate"]["status"] == "pending"
    assert social["candidate"]["decided_by"] == "slate bench higgsedit"
    film = formats.load("brand-film")
    assert {ch: row["route"] for ch, row in film["delivery"].items()} == {"youtube": "hero", "website": "hero"}
    # decision 9's channels: Instagram and TikTok, YouTube, LinkedIn, website and App Store
    families = {v["family"] for v in lib.values()}
    assert {"Instagram", "TikTok", "YouTube", "LinkedIn", "Web", "Apple"} <= families


def test_every_zone_is_sourced_dated_and_leaves_room():
    for key, ch in formats.channels().items():
        assert ch["sources"], key
        for aspect, zone in ch["zones"].items():
            assert aspect in ch["aspects"]
            assert DATE.match(zone["verified_on"]) and zone["source"]
            assert zone["top"] + zone["bottom"] < 1 and zone["left"] + zone["right"] < 1
            if zone["basis"] == "official":
                assert zone["official"], (key, aspect)
            elif zone["basis"] in ("official-template", "geometry", "estimate"):
                assert not zone["official"], (key, aspect)  # measured, worked out or guessed: never called official


def test_safe_zones_resolve_by_channel_alias_and_aspect():
    reels = formats.safe_zones("social-reel", "Reels")
    assert (reels["top"], reels["bottom"], reels["left"], reels["right"]) == (0.14, 0.35, 0.06, 0.06)
    assert reels["official"] and reels["basis"] == "official" and reels["unit"] == "fraction"
    assert formats.safe_zones("social-reel", "IG") == reels
    tiktok = formats.safe_zones("social-reel", "tiktok")
    assert tiktok["basis"] == "official-template" and tiktok["keep_out"]
    shorts = formats.safe_zones(formats.load("social-reel"), "youtube shorts")
    assert shorts["right"] > shorts["left"]  # the button column sits on the right
    # a multi-aspect format answers a vertical channel with its 9:16 zone
    assert formats.safe_zones("ad", "tiktok")["aspect"] == "9:16"
    # a 16:9-only film has no Shorts zone
    assert formats.safe_zones("brand-film", "youtube_shorts") == {}
    assert formats.safe_zones("brand-film", "youtube")["basis"] == "standard"
    assert formats.safe_zones("linkedin", "linkedin", aspect="4:5")["aspect"] == "4:5"
    with pytest.raises(UsageError):
        formats.safe_zones("social-reel", "myspace")
    # a bare dict (another module's fake) answers from its own data
    assert formats.safe_zones({"safe_zones": {"tiktok": {"top": 0.1}}}, "tiktok") == {"top": 0.1}


def test_vertical_only_formats_expose_platforms_for_the_access_gate():
    reel = formats.load("social-reel")
    assert reel["platforms"] == ["instagram_reels", "tiktok", "youtube_shorts"]
    assert set(reel["safe_zones"]) == set(reel["platforms"])
    for name in ("brand-film", "ad", "product-demo"):  # several aspects: no top-level platform list to misread
        assert "platforms" not in formats.load(name) and "safe_zones" not in formats.load(name)


def test_captions_get_shorter_lines_on_narrower_frames():
    ad = formats.load("ad")
    assert ad["captions"]["max_chars_by_aspect"] == {"16:9": 42, "1:1": 36, "9:16": 32}
    assert formats.captions_for("ad", "9:16")["max_chars"] == 32
    assert formats.load("ad", aspect="1:1")["captions"]["max_chars"] == 36
    assert ad["captions"]["min_seconds"] == ad["captions"]["min_s"] == 1.0
    assert formats.load("app-store-preview")["captions"]["max_chars"] == 32  # portrait device class


def test_aspects_resolve_by_ratio_or_size_and_refuse_what_a_format_lacks():
    assert formats.load("ad", aspect="9x16")["aspect"] == "9:16"
    assert formats.load("ad", aspect="1080x1080")["aspect"] == "1:1"
    assert formats.load("app-store-preview", aspect="ipad-portrait")["width"] == 1200
    with pytest.raises(UsageError) as err:
        formats.load("brand-film", aspect="9:16")
    assert "slate variant" in (err.value.hint or "")


def test_aliases_and_loose_names():
    assert formats.load("reel")["name"] == "social-reel"
    assert formats.canonical("Brand film") == "brand-film"
    assert formats.canonical("hero_loop") == "website-hero-loop"
    with pytest.raises(UsageError) as err:
        formats.load("nope")
    assert "slate formats list" in (err.value.hint or "")


def test_broadcast_loudness_presets():
    reel = formats.load("social-reel")
    assert formats.loudness_for(reel)["lufs"] == -14
    ebu = formats.loudness_for(reel, broadcast=True)
    assert (ebu["lufs"], ebu["true_peak"], ebu["broadcast"]) == (-23, -1, True)
    atsc = formats.loudness_for("social-reel", broadcast=True, standard="atsc_a85")
    assert (atsc["lufs"], atsc["true_peak"]) == (-24, -2)
    with pytest.raises(UsageError):
        formats.loudness_for(reel, broadcast=True, standard="nope")


# ---------------------------------------------------------------------------------------------------------------
# Validation catches broken templates


def _scratch_formats(tmp_path: Path, monkeypatch, **overrides) -> Path:
    folder = tmp_path / "formats"
    folder.mkdir()
    for f in paths.FORMATS_DIR.glob("_*.json"):
        shutil.copy(f, folder / f.name)
    base = json.loads((paths.FORMATS_DIR / "social-reel.json").read_text())
    base.pop("autonomous_default")
    for name, changes in overrides.items():
        data = json.loads(json.dumps(base))
        data["name"] = name
        for dotted, value in changes.items():
            node = data
            *parts, last = dotted.split(".")
            for part in parts:
                node = node[part]
            node[last] = value
        (folder / f"{name}.json").write_text(json.dumps(data))
    monkeypatch.setenv("SLATE_FORMATS_DIR", str(folder))
    return folder


def test_validation_catches_what_the_schema_cannot(tmp_path, monkeypatch):
    _scratch_formats(tmp_path, monkeypatch,
                     fast={"pace.max_words_per_second": 3.0},
                     squashed={"aspects": {"9:16": {"width": 1000, "height": 1000, "channels": ["tiktok"]}}},
                     nowhere={"aspects": {"9:16": {"width": 1080, "height": 1920, "channels": ["myspace"]}}},
                     rich={"budget.elevenlabs": {"vo": [500, 9000], "score": [1000, 2000]}},
                     silent={"loudness.lufs": None},
                     spendy={"generation": "none", "budget.higgsfield": [0, 10]},
                     wrongname={"name": "other"})
    problems = formats.check_all()
    assert any("2.6" in p or "maximum" in p for p in problems["fast.json"])
    assert any("isn't 9:16" in p for p in problems["squashed.json"])
    assert any("myspace" in p for p in problems["nowhere.json"])
    assert any("above the reel_or_ad family" in p for p in problems["rich.json"])
    assert any("lufs target" in p for p in problems["silent.json"])
    assert any("generation is none" in p for p in problems["spendy.json"])
    assert any("the file is wrongname.json" in p for p in problems["wrongname.json"])
    with pytest.raises(UsageError):
        formats.load("fast")


def test_only_one_autonomous_default_and_it_must_be_a_reel(tmp_path, monkeypatch):
    _scratch_formats(tmp_path, monkeypatch, one={"autonomous_default": True}, two={"autonomous_default": True},
                     long={"autonomous_default": True, "runtime_s": {"min": 15, "max": 90, "target": 30}})
    problems = formats.check_all()
    assert any("more than one autonomous_default" in p for p in problems["formats"])
    assert any("15-45 second 9:16" in p for p in problems["long.json"])


def test_a_broken_channel_library_is_named(tmp_path, monkeypatch):
    folder = _scratch_formats(tmp_path, monkeypatch)
    lib = json.loads((folder / "_platforms.json").read_text())
    lib["channels"]["tiktok"]["zones"]["9:16"]["top"] = 0.6
    lib["aliases"]["gone"] = "nowhere"
    (folder / "_platforms.json").write_text(json.dumps(lib))
    problems = formats.check_all()
    assert "_platforms.json" in problems
    text = " ".join(problems["_platforms.json"])
    assert "0.5" in text or "maximum" in text or "no safe area" in text


def test_a_format_file_path_loads_and_is_checked(tmp_path):
    data = json.loads((paths.FORMATS_DIR / "explainer.json").read_text())
    data["name"] = "my-explainer"
    good = tmp_path / "my-explainer.json"
    good.write_text(json.dumps(data))
    assert formats.load(str(good))["name"] == "my-explainer"
    data["pace"]["max_words_per_second"] = 4
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(UsageError):
        formats.load(str(bad))


def test_the_section_12_interfaces_keep_their_signatures():
    import inspect

    from slate import analyze
    from slate.bench import higgsedit

    assert list(inspect.signature(formats.load).parameters)[:1] == ["name"]
    assert list(inspect.signature(formats.names).parameters) == []
    assert list(inspect.signature(formats.safe_zones).parameters)[:2] == ["fmt", "platform"]
    assert inspect.signature(formats.safe_zones).parameters["aspect"].default is None  # optional, never required
    assert list(inspect.signature(analyze.run).parameters)[:1] == ["video"]
    for name in ("plan", "record_run", "record_review", "decide", "report", "refuse_run"):
        assert callable(getattr(higgsedit, name))
