"""`slate brand …` end to end through the CLI: help, --json output and the shared exit codes."""

from __future__ import annotations

import io
import json
import sys

import pytest

from slate import paths
from slate.cli import run_cli

from brand_helpers import CSS, make_png, make_video, needs_ffmpeg, speechy, write_proof, write_wav

COMMANDS = ("list", "show", "init", "validate", "onboard", "approve", "status")


def _j(argv):
    code, out, err = run_cli(argv + ["--json"])
    return code, (json.loads(out) if out.strip() else None), err


@pytest.mark.parametrize("cmd", COMMANDS)
def test_every_command_has_help(cmd):
    code, out, err = run_cli(["brand", cmd, "--help"])
    assert code == 0 and "usage: slate brand" in out


def test_brand_group_is_registered():
    from slate import cli

    cli.build_parser()
    assert "slate.brand.cli" not in cli.BROKEN
    code, out, err = run_cli(["brand"])
    assert code == 2 and "list" in err and "onboard" in err


def test_list_show_status_on_the_neutral_packs(store):
    code, out, _ = _j(["brand", "list"])
    assert code == 0 and [r["name"] for r in out["packs"]] == ["_template", "demo"]
    code, out, _ = _j(["brand", "status", "demo"])
    assert code == 0 and out["status"] == "needs onboarding" and out["missing"] == ["truth", "look_motion", "voice",
                                                                                     "sound"]
    code, out, _ = _j(["brand", "show", "demo"])
    assert code == 0 and out["summary"]["display_name"] == "Demo Studio" and out["summary"]["skills"] == [
        "demo-studio-taste"]
    code, out, _ = _j(["brand", "show", "demo", "--raw"])
    assert out["brand_json"]["name"] == "demo" and "_dir" not in out["brand_json"]
    code, out, _ = _j(["brand", "show", "demo", "--checklists"])
    assert set(out["checklists"]) == {"truth", "look", "voice", "sound"}
    code, out, err = run_cli(["brand", "status", "demo"])
    assert "demo: needs onboarding" in out and "next: slate brand init <name> --from-pack demo" in out


def test_validate_exit_codes(store, tmp_path):
    code, out, _ = _j(["brand", "validate"])
    assert code == 0 and out["ok"] and out["packs"]["demo"]["status"] == "PASS"
    code, out, _ = _j(["brand", "init", "acme", "--from-pack", "demo"])
    assert code == 0
    (store / "acme" / "logos" / "Face.ttf").write_bytes(b"\0\1\0\0")
    code, out, _ = _j(["brand", "validate", "acme"])
    assert code == 1 and not out["ok"]
    assert any(r["gate"] == "brand.fonts" and r["status"] == "FAIL" for r in out["packs"]["acme"]["results"])


def test_init_refusals_carry_exit_codes(store):
    code, out, _ = _j(["brand", "init", "acme", "--store", str(paths.BRANDS_REPO)])
    assert code == 1 and out["error"] == "refused" and "neutral packs" in out["message"]
    code, out, _ = _j(["brand", "init", "demo"])
    assert code == 1
    code, out, _ = _j(["brand", "init", "Bad Name"])
    assert code == 2 and out["error"] == "usage"
    code, out, _ = _j(["brand", "status", "nobody"])
    assert code == 2 and "slate brand init nobody" in out["hint"]
    code, out, _ = _j(["brand", "init", "ghost", "--dry-run"])
    assert code == 0 and out["dry_run"] and not (store / "ghost").exists()


def test_init_with_work_code_prints_the_procedure(store):
    code, out, err = run_cli(["brand", "init", "corp", "--from-app", "work:~/code/corp", "--company-material",
                              "--organisation", "Corp"])
    assert code == 0 and "peer ls '~/code/corp'" in out and "read in place, never copied" in out


def test_onboard_steps_through_the_cli(store, tmp_path, monkeypatch):
    run_cli(["brand", "init", "acme", "--from-pack", "demo"])
    code, out, _ = _j(["brand", "onboard", "acme", "--step", "truth", "--source", str(store / "acme" / "README.md"),
                       "--source-id", "pack", "--authority", "Pat Lee", "--claim", "Acme makes films.",
                       "--claim-source", "pack"])
    assert code == 0 and out["ready_to_approve"] and out["step"] == "truth"
    monkeypatch.setattr(sys, "stdin", io.StringIO(CSS))
    code, out, _ = _j(["brand", "onboard", "acme", "--step", "look", "--stdin-as", "work:~/code/app/tokens.css",
                       "--source-pin", "deadbeef"])
    assert code == 0 and any(i["id"] == "product_motion" and i["done"] for i in out["items"])
    code, out, _ = _j(["brand", "onboard", "acme"])
    assert code == 0 and out["status"] == "needs onboarding" and out["steps"]["truth"]["done"] >= 4
    code, out, _ = _j(["brand", "onboard", "acme", "--step", "look", "--direction", "x"])
    assert code == 2  # a direction without moodboards
    code, out, _ = _j(["brand", "onboard", "demo", "--step", "truth", "--authority", "X"])
    assert code == 1 and "neutral pack" in out["message"]
    code, out, _ = _j(["brand", "onboard", "acme", "--step", "sound", "--claim", "x"])
    assert code == 2 and "--claim belongs to --step truth" in out["message"]


@needs_ffmpeg
def test_onboard_and_approve_all_four_through_the_cli(store, tmp_path):
    run_cli(["brand", "init", "acme", "--from-pack", "demo"])
    run_cli(["brand", "onboard", "acme", "--step", "truth", "--source", str(store / "acme" / "README.md"),
             "--source-id", "pack", "--authority", "Pat Lee", "--claim", "Acme makes films.", "--claim-source", "pack"])
    for i, d in enumerate(("one", "two")):
        run_cli(["brand", "onboard", "acme", "--step", "look", "--moodboard", str(make_png(tmp_path / f"m{i}.png")),
                 "--direction", d, "--grounded-in", "pack"])
    take = write_wav(tmp_path / "take.wav", speechy(3.0))
    run_cli(["brand", "onboard", "acme", "--step", "voice", "--model", "tts-model-x", "--take", str(take)])
    mix, stems = write_proof(tmp_path, 12.0)
    code, out, _ = _j(["brand", "onboard", "acme", "--step", "sound", "--proof", str(mix), "--stems", str(stems)])
    assert code == 0 and out["ready_to_approve"]
    files = {"truth": store / "acme" / "onboarding" / "truth" / "TRUTH.md",
             "look_motion": make_video(tmp_path / "look.mov", 12.0), "voice": take, "sound": mix}
    code, out, _ = _j(["brand", "approve", "acme", "truth", "--file", str(files["truth"]), "--by", "Someone"])
    assert code == 1 and "claim authority" in out["message"]
    for ref, f in files.items():
        code, out, _ = _j(["brand", "approve", "acme", ref, "--file", str(f), "--by", "Pat Lee"])
        assert code == 0, out
        assert out["record"]["sha256"] and out["record"]["approved_by"] == "Pat Lee"
    code, out, _ = _j(["brand", "status", "acme"])
    assert out["status"] == "onboarded" and out["onboarded"] is True
    code, out, _ = _j(["brand", "approve", "acme", "vibes", "--file", str(take), "--by", "Pat Lee"])
    assert code == 2
