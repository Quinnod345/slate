"""`slate formats list|show|check|channels`, with --json and the shared exit codes."""

from __future__ import annotations

import json
import shutil

from slate import cli, paths


def test_list_names_every_format_once():
    code, out, err = cli.run_cli(["formats", "list", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["count"] == 13 and len({r["name"] for r in data["formats"]}) == 13
    reel = next(r for r in data["formats"] if r["name"] == "social-reel")
    assert reel["autonomous_default"] and reel["aspects"] == ["9:16"] and reel["routes"] == ["social"]
    code, out, _ = cli.run_cli(["formats", "list"])
    assert code == 0 and "social-reel" in out and "the autonomous default" in out


def test_show_resolves_a_format_and_reads_well():
    code, out, _ = cli.run_cli(["formats", "show", "reel", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["name"] == "social-reel" and data["width"] == 1080 and set(data["safe_zones"]) == set(data["platforms"])
    code, out, _ = cli.run_cli(["formats", "show", "social-reel"])
    for piece in ("runtime", "15-45 s", "9:16", "at most 2.6 words a second", "burned in by Remotion in the brand's font",
                  "instagram_reels at 9:16: top 14%", "ElevenLabs voice 500-3,000 + score 1,000-2,000", "Higgsfield $0",
                  "social: may take the fast route", "broadcast  no", "autonomous films use this format"):
        assert piece in out, piece
    code, out, _ = cli.run_cli(["formats", "show", "ad", "--aspect", "1:1", "--json"])
    assert code == 0 and json.loads(out)["captions"]["max_chars"] == 36
    code, out, _ = cli.run_cli(["formats", "show", "app-store-preview", "--raw", "--json"])
    raw = json.loads(out)
    assert code == 0 and "zones" not in raw and raw["aspects"]["iphone-portrait"]["width"] == 886


def test_unknown_formats_and_aspects_are_usage_errors():
    code, out, _ = cli.run_cli(["formats", "show", "nope", "--json"])
    assert code == 2 and json.loads(out)["error"] == "usage" and "slate formats list" in json.loads(out)["hint"]
    code, out, _ = cli.run_cli(["formats", "show", "brand-film", "--aspect", "9:16", "--json"])
    assert code == 2 and "slate variant" in json.loads(out)["hint"]
    code, _, _ = cli.run_cli(["formats"])
    assert code == 2  # a group without a stage prints its help


def test_check_passes_on_the_repo_and_fails_on_a_broken_copy(tmp_path, monkeypatch):
    code, out, _ = cli.run_cli(["formats", "check", "--json"])
    assert code == 0 and json.loads(out)["ok"] is True
    folder = tmp_path / "formats"
    shutil.copytree(paths.FORMATS_DIR, folder)
    data = json.loads((folder / "explainer.json").read_text())
    data["pace"]["max_words_per_second"] = 3.1
    (folder / "explainer.json").write_text(json.dumps(data))
    monkeypatch.setenv("SLATE_FORMATS_DIR", str(folder))
    code, out, _ = cli.run_cli(["formats", "check", "--json"])
    result = json.loads(out)
    assert code == 1 and not result["ok"] and "explainer.json" in result["problems"]
    code, out, _ = cli.run_cli(["formats", "check"])
    assert code == 1 and "explainer.json" in out


def test_channels_lists_routes_zones_and_their_basis():
    code, out, _ = cli.run_cli(["formats", "channels", "--json"])
    assert code == 0
    data = json.loads(out)
    rows = {r["channel"]: r for r in data["channels"]}
    assert rows["youtube"]["route"] == "hero" and rows["tiktok"]["route"] == "social"
    assert rows["instagram_reels"]["zones"]["9:16"]["official"] is True
    assert data["verified_on"] == "2026-09-30" and set(data["routes"]) == {"hero", "social"}
    code, out, _ = cli.run_cli(["formats", "channels"])
    assert code == 0 and "re-check the sources before a production" in out


def test_formats_and_bench_register_once_and_never_break_analyze():
    parser = cli.build_parser()
    choices = next(a for a in parser._actions if getattr(a, "choices", None) and "analyze" in a.choices).choices
    assert {"analyze", "formats", "bench"} <= set(choices)
    assert not {k: v for k, v in cli.BROKEN.items() if k in ("slate.analyze.cli", "slate.formats", "slate.bench.cli")}
    # registering again (say, once slate.cli lists these modules itself) skips names already taken
    from slate import formats
    from slate.bench import cli as bench_cli

    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "analyze" in a.choices)
    formats.register(sub)
    bench_cli.register(sub)
