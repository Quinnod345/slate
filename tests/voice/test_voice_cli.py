"""The `slate voice` command surface: help for every stage, JSON output, exit codes, and detached jobs."""

import json
import sys
import types

import pytest

from slate.cli import run_cli

STAGES = ["profile", "account", "cast", "design", "tags", "tts", "clean", "pace", "align", "captions", "check",
          "approve"]


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_has_help(stage):
    code, out, err = run_cli(["voice", stage, "--help"])
    assert code == 0 and "--json" in out and "--film" in out


def test_the_group_lists_its_stages():
    code, out, err = run_cli(["voice", "--help"])
    assert code == 0
    for stage in STAGES:
        assert stage in out
    code, _, _ = run_cli(["voice"])
    assert code == 2


def test_account_in_replay(slate_home):
    code, out, _ = run_cli(["voice", "account", "--json"])
    data = json.loads(out)
    assert code == 0 and data["tier"] == "pro" and data["best_voice_format"] == "pcm_48000" and data["replay"]
    assert data["key"] == "elevenlabs"
    code, out, _ = run_cli(["voice", "account", "--oneiro", "--json"])
    assert json.loads(out)["key"] == "elevenlabs_oneiro"
    code, out, _ = run_cli(["voice", "account"])
    assert "pro plan" in out and "replay" in out


def test_usage_errors_are_exit_two(voice_film):
    code, out, err = run_cli(["voice", "tts", "--film", str(voice_film), "--only", "S99"])
    assert code == 2 and "No voice line for S99" in err
    code, _, err = run_cli(["voice", "design", "--film", str(voice_film)])
    assert code == 2
    code, _, err = run_cli(["voice", "tts", "--film", "/nonexistent/film"])
    assert code == 2


def test_missing_storyboard_is_exit_three(slate_home, brand):
    from voice_helpers import make_film

    film = make_film(slate_home["films"], "no-script")
    (film / "script" / "storyboard.json").unlink()
    code, _, err = run_cli(["voice", "tts", "--film", str(film)])
    assert code == 3 and "slate script build" in err


def test_json_mode_puts_one_object_on_stdout(voice_film):
    code, out, err = run_cli(["voice", "tts", "--film", str(voice_film), "--json"])
    assert code == 0
    data = json.loads(out)  # exactly one JSON object
    assert data["film"] == "demo-reel" and len(data["takes"]) == 3
    assert "voiced S01" in err  # progress goes to stderr


def test_detach_submits_a_job_with_the_same_arguments(voice_film, monkeypatch):
    calls = []
    fake = types.ModuleType("slate.jobs.client")

    def submit(kind, argv, *, film=None, resources=(), **kw):
        calls.append({"kind": kind, "argv": argv, "film": film, "resources": tuple(resources)})
        return "job_test_1"

    fake.submit = submit
    import slate.jobs
    monkeypatch.setitem(sys.modules, "slate.jobs.client", fake)
    monkeypatch.setattr(slate.jobs, "client", fake, raising=False)
    code, out, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01", "--engine",
                              "elevenlabs", "--detach", "--json"])
    assert code == 0, err
    assert json.loads(out)["job"] == "job_test_1"
    (call,) = calls
    assert call["kind"] == "voice-align" and call["resources"] == ("whisper",)
    argv = call["argv"]
    assert argv[1:5] == ["-m", "slate", "voice", "align"] and "--detach" not in argv
    assert argv[argv.index("--film") + 1] == str(voice_film)
    assert argv[argv.index("--only") + 1] == "S01" and argv[argv.index("--engine") + 1] == "elevenlabs"
    code, out, err = run_cli(["voice", "tts", "--film", str(voice_film), "--force", "--detach"])
    assert code == 0 and calls[-1]["kind"] == "voice-tts" and "--force" in calls[-1]["argv"]
    assert calls[-1]["resources"] == ("elevenlabs_tts",)
