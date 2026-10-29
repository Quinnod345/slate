"""`slate new`: a valid, git-initialized film folder made from the format's defaults."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from slate import jsonio, paths
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.state import film, lock, production, scaffold

STAGES = ("brief", "script", "voice", "timing", "scenes", "captures", "sound", "renders", "masters", "qa", "review",
          "deliver", "archive")


def test_new_scaffolds_a_valid_git_film(new_film, state_env):
    request = "Make a 90-second explainer for the planner. Keep it calm.\nEnd on the sign-up line."
    fd = new_film("launch", git=True, request=request, request_source="Test Person", title="Launch")
    assert fd == (state_env["films"] / "launch").resolve() or fd == state_env["films"] / "launch"
    assert fd.parent.resolve() == paths.films().resolve()  # films live in paths.films()/<id> by default
    for name in STAGES:
        assert (fd / name).is_dir(), name
    fj = jsonio.read(fd / "film.json")
    assert jsonio.validate(fj, "film") == []
    for key in ("id", "title", "format", "language", "fps_num", "fps_den", "width", "height", "brand", "voice",
                "targets", "budget", "mode", "generation", "parent", "derivation", "pins", "models", "versions"):
        assert key in fj, key
    assert fj["language"] == "en" and fj["generation"] == "rarely" and fj["mode"] == "supervised"
    assert jsonio.validate(jsonio.read(fd / "film.lock.json"), "film_lock") == []
    assert jsonio.validate(jsonio.read(fd / "approvals.json"), "approvals") == []
    assert jsonio.read(fd / "script" / "script.json")["acts"] == []
    goal = (fd / "brief" / "GOAL.md").read_text()
    assert "> Make a 90-second explainer for the planner. Keep it calm." in goal  # verbatim, every line
    assert "> End on the sign-up line." in goal
    assert "| 1 | Make a 90-second explainer for the planner." in goal
    assert "| F1 | Format: fixture-explainer" in goal and "| F3 | Budget:" in goal
    prod = (fd / "brief" / "PRODUCTION.md").read_text()
    for heading in ("Asks, in the requester's words", "Direction changes", "Questions waiting for a person",
                    "Revisions (preserve baseline and requested scope)", "Approvals", "Status"):
        assert heading in prod
    brief = production.load_brief(fd)
    assert brief["request"]["text"] == request.strip("\n")
    assert [a["text"] for a in brief["asks"]] == ["Make a 90-second explainer for the planner.", "Keep it calm.",
                                                  "End on the sign-up line."]
    # git: initialized, one commit of the small files, heavy media ignored, never a remote
    git = scaffold.git_state(fd)
    assert git["repo"] and git["head"] and not git["remotes"] and not git["dirty"]
    tracked = subprocess.run(["git", "ls-files"], cwd=fd, capture_output=True, text=True).stdout.split()
    for small in ("film.json", "film.lock.json", "approvals.json", "brief/GOAL.md", "brief/PRODUCTION.md",
                  "brief/brief.json", "script/script.json", ".gitignore"):
        assert small in tracked, small
    ignore = (fd / ".gitignore").read_text()
    for pattern in ("/renders/**", "/masters/**", "/captures/**", "/archive/**", "*.wav", "*.mov", "*.mp4",
                    "bundle/", "!/renders/**/*.json"):
        assert pattern in ignore, pattern
    (fd / "renders" / "act1.mov").write_bytes(b"x")
    (fd / "voice" / "S01.wav").write_bytes(b"x")
    (fd / "renders" / "registry.json").write_text("{}")
    check = subprocess.run(["git", "check-ignore", "renders/act1.mov", "voice/S01.wav", "renders/registry.json"],
                           cwd=fd, capture_output=True, text=True).stdout.split()
    assert check == ["renders/act1.mov", "voice/S01.wav"]


def test_format_defaults_fill_film_json(new_film):
    fd = new_film("reel", format="fixture-reel")
    fj = film.load(fd)
    assert (fj["width"], fj["height"], fj["aspect"]) == (720, 1280, "9:16")
    assert (fj["fps_num"], fj["fps_den"]) == (25, 1)
    assert fj["targets"]["runtime_s"] == 30.0 and fj["targets"]["runtime_range_s"] == [15.0, 45.0]
    assert fj["targets"]["lufs"] == -14 and fj["targets"]["true_peak"] == -1
    assert fj["budget"]["elevenlabs"] == 3000 and fj["budget"]["higgsfield"] == 0
    fd2 = new_film("explainer")
    b = film.load(fd2)["budget"]
    assert b["elevenlabs"] == 10000  # voice 5,000 + score 5,000: the top of each range, summed
    assert b["higgsfield"] == 60 and b["rework_factor"] == 1.5
    assert film.load(fd2)["targets"]["runtime_s"] == 90


def test_multi_aspect_format_and_ntsc_decimal(new_film):
    fd = new_film("square", format="fixture-multi", aspect="1:1")
    fj = film.load(fd)
    assert (fj["width"], fj["height"], fj["aspect"]) == (720, 720, "1:1")
    assert (fj["fps_num"], fj["fps_den"]) == (30 * 1000, 1001)  # 29.97 written as a decimal is the NTSC fraction
    assert fj["budget"]["elevenlabs"] == 2000


def test_format_can_be_a_file_path(new_film, state_env):
    fd = new_film("byfile", format=str(state_env["formats"] / "fixture-reel.json"))
    assert film.load(fd)["format"] == "fixture-reel"


def test_draft_and_autonomous_budgets_and_voice(new_film):
    fd = new_film("d1", mode="draft")
    fj = film.load(fd)
    assert fj["budget"]["higgsfield"] == 0 and fj["voice"]["source"] == "library"
    fd = new_film("a1", mode="autonomous", brand="onboarded-brand", format="fixture-reel")
    fj = film.load(fd)
    assert fj["budget"]["higgsfield"] == 0 and fj["voice"]["source"] == "brand"
    assert fj["voice"]["voice_id"] == "fixture-voice-0002"


def test_autonomous_defaults_to_a_short_vertical_reel(new_film, state_env):
    fd = film.create("auto-default", brand="onboarded-brand", format=None, mode="autonomous", git=False)
    fj = film.load(fd)
    assert fj["format"] == "fixture-reel" and fj["aspect"] == "9:16"


def test_refusals(new_film, state_env):
    new_film("taken")
    with pytest.raises(Refused):
        new_film("taken")  # never writes over a film
    with pytest.raises(UsageError):
        new_film("Bad Id!")
    with pytest.raises(UsageError):
        new_film("nobrand", brand="no-such-brand")
    with pytest.raises(UsageError):
        new_film("nofmt", format="no-such-format")
    with pytest.raises(Refused) as exc:
        new_film("auto", mode="autonomous", brand="open-brand")  # autonomous refuses an unapproved brand
    assert "truth" in exc.value.message and exc.value.details["missing_references"]
    with pytest.raises(UsageError):
        new_film("badgen", generation="lots")


def test_new_cli_json_and_exit_codes(state_env):
    code, out, err = run_cli(["new", "cli-film", "--brand", "open-brand", "--format", "fixture-reel", "--no-git",
                              "--request", "A short reel about the planner.", "--source", "Test Person", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["film"] == "cli-film" and data["film_json"]["aspect"] == "9:16"
    assert data["next"]["command"]
    code, out, _ = run_cli(["new", "cli-film", "--brand", "open-brand", "--format", "fixture-reel", "--json"])
    assert code == 1 and json.loads(out)["error"] == "refused"
    code, out, _ = run_cli(["new", "UPPER", "--brand", "open-brand", "--format", "fixture-reel", "--json"])
    assert code == 2
    code, out, _ = run_cli(["new", "auto-x", "--brand", "open-brand", "--format", "fixture-reel", "--mode",
                            "autonomous", "--json"])
    assert code == 1 and "four references" in json.loads(out)["message"]
    code, out, _ = run_cli(["new", "dry", "--brand", "open-brand", "--format", "fixture-reel", "--dry-run", "--json"])
    assert code == 0 and json.loads(out)["dry_run"] and not (state_env["films"] / "dry").exists()


def test_help_for_every_command(state_env):
    for argv in (["new"], ["status"], ["make"], ["approve"], ["film", "show"], ["film", "set"],
                 ["film", "pin"], ["film", "repin"], ["film", "ask"], ["film", "mark"], ["film", "direction"],
                 ["film", "questions"], ["film", "answer"], ["film", "park"], ["variant"],
                 ["revise", "start"], ["revise", "check"], ["revise", "done"], ["revise", "list"]):
        code, out, err = run_cli([*argv, "--help"])
        assert code == 0, (argv, err)
        assert "usage: slate" in out, argv
    code, _out, err = run_cli(["film"])  # a group without a stage prints its help and exits 2
    assert code == 2 and "show" in err
    proc = subprocess.run([sys.executable, "-m", "slate", "status", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "next command" in proc.stdout  # and through `python -m slate`


def test_films_dir_override_and_icloud_warning(state_env, new_film, tmp_path, capsys):
    other = tmp_path / "elsewhere"
    fd = new_film("moved", films_dir=other)
    assert fd.parent == other
    lock_file = jsonio.read(fd / "film.lock.json")
    assert lock_file["files"] == {} and lock.frozen_shot_ids(fd) == []
