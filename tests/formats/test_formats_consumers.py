"""The modules that read formats get what they expect from the real templates (not a fake): state's film defaults and
autonomous default, story's pace and runtime, the ledger's budgets, the QA gates' caption limits and safe zones, the
voice module's caption rules and the sound module's broadcast flag."""

from __future__ import annotations

import json
from types import SimpleNamespace

from slate import formats


def test_state_builds_film_defaults_from_every_format():
    from slate.state import film

    for name in formats.names():
        fmt = formats.load(name)
        d = film.format_defaults(fmt)
        assert (d["width"], d["height"]) == (fmt["width"], fmt["height"])
        assert (d["fps_num"], d["fps_den"]) == (fmt["fps_num"], fmt["fps_den"])
        assert d["runtime_s"] == fmt["runtime_s"]["target"]
        assert d["runtime_range_s"] == [fmt["runtime_s"]["min"], fmt["runtime_s"]["max"]]
        assert d["lufs"] == fmt["loudness"]["lufs"] and d["true_peak"] == fmt["loudness"]["true_peak"]
        assert d["budget"]["elevenlabs"] == sum(r[1] for r in fmt["budget"]["elevenlabs"].values())
        for aspect in fmt["aspects"]:
            other = film.format_defaults(fmt, aspect=aspect)
            assert (other["width"], other["height"]) == (fmt["aspects"][aspect]["width"], fmt["aspects"][aspect]["height"])


def test_state_picks_the_social_reel_for_autonomous_films(slate_home):
    from slate.state import modes

    assert modes.default_format("autonomous") == "social-reel"


def test_slate_new_makes_a_reel_film_from_the_real_format(slate_home):
    from slate import cli

    brand = slate_home["brands"] / "neutral"
    brand.mkdir()
    (brand / "brand.json").write_text(json.dumps({"name": "neutral", "palette": {}, "references": {}}))
    code, out, err = cli.run_cli(["new", "reel-one", "--brand", "neutral", "--format", "reel", "--json", "--no-git"])
    assert code == 0, err
    film = json.loads((slate_home["films"] / "reel-one" / "film.json").read_text())
    reel = formats.load("social-reel")
    assert film["format"] == "social-reel"
    assert (film["width"], film["height"], film["fps_num"], film["fps_den"]) == (reel["width"], reel["height"],
                                                                                reel["fps_num"], reel["fps_den"])
    assert film["targets"]["lufs"] == -14


def test_story_reads_pace_runtime_and_density():
    from slate.story import inputs

    fmt, problem = inputs.load_format("social-reel")
    assert problem is None
    assert inputs.runtime_target({}, fmt) == {"source": "format runtime", "target": 30.0, "min": 15.0, "max": 45.0}
    timing = inputs.timing_settings({}, fmt)
    assert timing["max_pace"] == 2.6 and timing["lead"] == 0.2 and timing["act_tail"] == 0.5
    assert inputs.read_time_settings({}, fmt)["max_words"] == 8


def test_the_ledger_reads_each_formats_budget():
    from slate import ledger

    reel = ledger.format_budget("social-reel")
    assert (reel["elevenlabs"], reel["higgsfield"], reel["source"]) == (5000, 0, "formats/social-reel.json")
    film = ledger.format_budget("brand-film")
    assert (film["elevenlabs"], film["higgsfield"]) == (40000, 85)
    assert ledger.format_budget("explainer")["higgsfield"] == 60
    assert ledger.format_budget("website-hero-loop")["elevenlabs"] == 0  # a silent loop spends no voice or score
    # the onboarding budget isn't a format: the ledger's own plan table and slate.formats agree on it
    onboarding = ledger.format_budget("onboarding")
    fam = formats.onboarding_budget()
    assert onboarding["elevenlabs"] == fam["elevenlabs"]["voice_design"][1]
    assert onboarding["higgsfield"] == fam["higgsfield"][1]


def test_qa_reads_caption_limits_and_uses_the_formats_zones_for_vertical_films():
    from slate.qa import access
    from slate.qa.checks import text

    rules = text._caption_rules(SimpleNamespace(film={"format": "social-reel"}))
    assert rules["max_chars"] == 32 and rules["max_cps"] == 17 and rules["min_seconds"] == 1.0

    def film(fmt: str, aspect: str) -> SimpleNamespace:  # what film.json says about a film's frame
        f = formats.load(fmt, aspect=aspect)
        return SimpleNamespace(film={"format": fmt, "aspect": aspect, "width": f["width"], "height": f["height"]},
                               size=(f["width"], f["height"]), _memo={})

    reel = film("social-reel", "9:16")
    names, why = access.platforms_for(reel)
    assert names == ["instagram_reels", "tiktok", "youtube_shorts"] and "social-reel" in why
    zone = access.zone_for(reel, "instagram_reels")
    assert zone["safe"] == [0.06, 0.14, 0.94, 0.65] and zone["source"].startswith("Meta Ads Guide")
    # a 9:16 cut of a multi-aspect format gets the 9:16 zone, never a 16:9 one
    ad_vertical = film("ad", "9:16")
    assert access.zone_for(ad_vertical, "tiktok")["keep_out"]
    # a 16:9 film has no platform zones to check (the plan's zones are the 9:16 ones)
    assert access.platforms_for(film("brand-film", "16:9"))[0] == []


def test_voice_captions_follow_the_format():
    from slate.voice import captions

    assert captions.settings_for({"format": "social-reel"}, {})["max_chars"] == 32
    assert captions.settings_for({"format": "brand-film"}, {})["max_chars"] == 42


def test_sound_reads_the_broadcast_flag(tmp_path):
    from slate.sound import film as sound_film

    assert sound_film.load_format("brand-film")["broadcast"] is False
    assert sound_film.load_format("no-such-format") is None
