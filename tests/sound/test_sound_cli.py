"""The sound commands exist, explain themselves, and speak JSON with the shared exit codes."""

import json

import pytest

from slate import cli
from slate.cli import run_cli
from slate.sound import audio

import sound_helpers as H

COMMANDS = [["score"], ["score", "plan"], ["score", "compose"], ["score", "assemble"], ["score", "qa"], ["mix"],
            ["verify"], ["sfx"], ["sound"], ["sound", "instruments"], ["sound", "loudness"]]


def test_the_sound_module_registers_cleanly():
    parser = cli.build_parser()
    assert "slate.sound.cli" not in cli.BROKEN
    text = parser.format_help()
    for name in ("score", "mix", "verify", "sfx", "sound"):
        assert f"    {name} " in text


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a))
def test_every_command_has_help(argv):
    code, _, err = run_cli([*argv, "--help"])
    assert code == 0, err


def test_groups_without_a_stage_show_help_and_exit_2():
    assert run_cli(["score"])[0] == 2
    assert run_cli(["sound"])[0] == 2


def test_loudness_command_measures_files(tmp_path):
    p = audio.write_wav(tmp_path / "tone.wav", H.sine(-20.0, 5.0, fade_s=0.1), 48000)
    code, out, _ = run_cli(["sound", "loudness", str(p), "--json"])
    res = json.loads(out)["results"][0]
    assert code == 0 and abs(res["integrated_lufs"] + 20.0) < 0.1
    assert res["cross_check"] is None or res["cross_check"]["agree"] in (True, None)
    code, out, _ = run_cli(["sound", "loudness", str(p), "--no-ffmpeg"])
    assert code == 0 and "LUFS integrated" in out


def test_score_qa_on_files(tmp_path):
    clean = audio.write_wav(tmp_path / "clean.wav", H.pad(8.0), 48000)
    bad = H.pad(8.0)
    bad[3 * 48000:5 * 48000] = 0
    holey = audio.write_wav(tmp_path / "holey.wav", bad, 48000)
    code, out, _ = run_cli(["score", "qa", str(clean), "--json"])
    assert code == 0 and json.loads(out)["clean"]
    code, out, _ = run_cli(["score", "qa", str(clean), str(holey), "--json"])
    assert code == 1 and json.loads(out)["results"][1]["problems"][0]["kind"] == "silence"
