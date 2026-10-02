"""`slate qa` is the one command for every gate: the motion and access gates' own flags live on it (registered once),
`slate qa motion` and `slate qa access` are gate names, and flags that don't fit the run are refused before anything
runs or is queued."""

from __future__ import annotations

import argparse
import json

import pytest

from slate import cli
from slate.cli import run_cli

from qa_core_helpers import write_json


def _subcommands(parser: argparse.ArgumentParser) -> dict:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices


def test_qa_is_registered_once_and_motion_and_access_are_its_gates():
    parser = cli.build_parser()
    commands = _subcommands(parser)
    assert "qa" in commands and "motion" not in commands and "access" not in commands
    assert "slate.qa.cli" not in cli.BROKEN
    args = parser.parse_args(["qa", "motion", "access", "--act", "2", "--no-pixels", "--only", "speed",
                              "--platform", "tiktok"])
    assert args.targets == ["motion", "access"] and args.act == [2] and args.no_pixels is True
    assert args.only == ["speed"] and args.platform == ["tiktok"]
    assert args.handler.__module__ == "slate.qa.cli"
    cli.build_parser()  # building it again doesn't collide with itself


def test_help_shows_the_motion_and_access_flags():
    code, out, _ = run_cli(["qa", "--help"])
    assert code == 0
    for flag in ("--act", "--render", "--no-pixels", "--only", "--captions", "--sheet", "--platform"):
        assert flag in out, flag


def _traced_film(film_builder):
    fb = film_builder(shots=[(1, 20), (2, 20)])
    entry = {"id": "card", "shot": "S01", "prop": "y", "ease": "move", "frame_in": 0, "frame_out": 10, "from": 10,
             "to": 0, "samples": [[f, 10 - f] for f in range(11)], "entrance": False, "loop_period": None,
             "rig": None}
    write_json(fb.path / "timing" / "motion" / "act1.json", [entry])
    return fb


def test_a_narrowed_motion_run_reports_but_cannot_pass(film_builder):
    fb = _traced_film(film_builder)
    code, out, _ = run_cli(["qa", "--film", str(fb.path), "motion", "--act", "1", "--no-pixels", "--json"])
    doc = json.loads(out)
    motion = doc["results"][0]
    assert code == 1 and motion["gate"] == "motion" and motion["status"] != "PASS"
    assert motion["details"]["acts"] == [1] and "--act" in motion["details"]["narrowed"][0]


@pytest.mark.parametrize("argv, words", [
    (["triage", "--act", "1"], "slate qa triage"),
    (["--list", "--only", "speed"], "slate qa list"),
    (["unpark", "--by", "Quinn", "--platform", "tiktok"], "slate qa unpark"),
    (["loudness", "--act", "1"], "on their own"),
    (["--act", "1"], "on their own"),
    (["motion", "--only", "captions"], "no captions check in motion"),
    (["motion", "--act", "9"], "no act 9"),
    (["unpark", "extra"], "takes no gate names"),
])
def test_flags_that_dont_fit_are_a_usage_error(film_builder, argv, words):
    fb = _traced_film(film_builder)
    code, out, err = run_cli(["qa", "--film", str(fb.path), *argv])
    assert code == 2, (argv, out, err)
    assert words in err, err


def test_psnr_alone_is_the_gate_and_psnr_with_two_files_compares_them(film_builder, tmp_path):
    fb = film_builder()
    code, out, _ = run_cli(["qa", "--film", str(fb.path), "psnr", "--json"])
    doc = json.loads(out)
    assert code == 1 and doc["results"][0]["gate"] == "psnr" and doc["results"][0]["status"] == "UNCHECKED"
    code, _, err = run_cli(["qa", "psnr", str(tmp_path / "a.png"), str(tmp_path / "b.png"), "--act", "1"])
    assert code == 2 and "slate qa psnr" in err


def test_a_detached_run_carries_the_flags_with_absolute_paths(film_builder, monkeypatch, tmp_path):
    client = pytest.importorskip("slate.jobs.client")
    seen = {}

    def fake_submit(kind, argv, **kw):
        seen.update(kind=kind, argv=argv, **kw)
        return "job-7"

    monkeypatch.setattr(client, "submit", fake_submit)
    fb = _traced_film(film_builder)
    take = tmp_path / "take3"
    take.mkdir()
    monkeypatch.chdir(tmp_path)
    code, out, _ = run_cli(["qa", "--film", str(fb.path), "motion", "--act", "1", "--render", "take3",
                            "--detach", "--json"])
    assert code == 0 and json.loads(out)["job"] == "job-7"
    argv = seen["argv"]
    assert argv[argv.index("--render") + 1] == str(take.resolve())
    assert argv[argv.index("--act") + 1] == "1" and "motion" in argv
    code, _, err = run_cli(["qa", "--film", str(fb.path), "glitch", "--act", "1", "--detach"])
    assert code == 2 and not seen.get("refused")  # refused before anything is queued


def test_the_motion_access_and_stability_gates_merge_once_however_often_the_merge_runs():
    from slate import qa

    for _ in range(3):
        status = qa.load_external(refresh=True)
    assert status["slate.qa.motion"] == "merged: motion" and status["slate.qa.access"] == "merged: access"
    # the stability gates come from their own module once it's installed; until then the guard says so
    assert status["slate.qa.stability"] in ("merged: stability", "not installed yet"), status
    assert set(status) == set(qa.EXTERNAL)
    assert [g for g in qa.GATES if g.endswith("motion")] == ["motion"]
    assert [g for g in qa.GATES if g.endswith("access")] == ["access"]
    assert qa.SOURCES["motion"] == "slate.qa.motion" and qa.SOURCES["access"] == "slate.qa.access"
    assert list(qa.GATES)[:len(qa.CORE)] == list(qa.CORE)  # the core gates keep their names and their order


def test_a_missing_gate_module_is_named_not_fatal(monkeypatch):
    """The import guard: a gate module that isn't installed (or fails to import) leaves the core gates running and
    `slate qa <its name>` saying why."""
    import sys

    from slate import qa
    from slate.errors import MissingPrerequisite

    monkeypatch.setitem(sys.modules, "slate.qa.stability", None)  # import raises ModuleNotFoundError
    try:
        status = qa.load_external(refresh=True)
        assert status["slate.qa.stability"] == "not installed yet" and "stability" not in qa.GATES
        assert list(qa.GATES)[:len(qa.CORE)] == list(qa.CORE)
        with pytest.raises(MissingPrerequisite):
            qa.resolve(["stability"])
    finally:
        monkeypatch.undo()
        qa.load_external(refresh=True)


def test_psnr_holds_a_shot_against_its_frames_in_a_whole_act(film_builder):
    """`--b-frames` compares a shot render with the same frames of a whole delivered act, no cut file needed."""
    from qa_core_helpers import moving_square, needs_ffmpeg  # noqa: PLC0415

    if needs_ffmpeg.args[0]:
        pytest.skip("ffmpeg isn't installed")
    fb = film_builder()
    whole = moving_square(30, (64, 48))
    act = fb.encode("cmp/act.mkv", whole)
    shot = fb.encode("cmp/shot.mkv", whole[10:20])
    code, out, err = run_cli(["qa", "psnr", str(shot), str(act), "--b-frames", "10-19", "--json"])
    doc = json.loads(out)
    assert code == 0, err
    assert doc["passed"] and doc["summary"]["frames"] == 10 and doc["b_frames"] == [10, 19] and not doc["problems"]
    # the same frames one late: a 1 px shift of the square, far under the bar
    code, out, _ = run_cli(["qa", "psnr", str(shot), str(act), "--b-frames", "11-20", "--json"])
    assert code == 1 and json.loads(out)["summary"]["below_count"] == 10
    # a range on each side; frames are reported in A's own numbering
    code, out, _ = run_cli(["qa", "psnr", str(act), str(shot), "--a-frames", "12-15", "--b-frames", "2-5", "--json"])
    doc = json.loads(out)
    assert code == 0 and doc["summary"]["frames"] == 4 and all(w["frame"] >= 12 for w in doc["summary"]["worst"])
    # without a range the whole act is longer than the shot: refused, saying so
    code, out, _ = run_cli(["qa", "psnr", str(shot), str(act), "--json"])
    assert code == 1 and any("frame counts differ" in p for p in json.loads(out)["problems"])


def test_psnr_ranges_are_checked_before_anything_runs(film_builder, tmp_path):
    from qa_core_helpers import moving_square, needs_ffmpeg  # noqa: PLC0415

    if needs_ffmpeg.args[0]:
        pytest.skip("ffmpeg isn't installed")
    fb = film_builder()
    act = fb.encode("cmp/act.mkv", moving_square(12, (64, 48)))
    for rng, why in (("5", "like 644-1212"), ("9-3", "comes before"), ("4-12", "past the file's last frame"),
                     ("a-b", "like 644-1212")):
        code, _, err = run_cli(["qa", "psnr", str(act), str(act), "--b-frames", rng])
        assert code == 2 and why in err, (rng, err)
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    from PIL import Image  # noqa: PLC0415

    for p in (a, b):
        Image.new("RGB", (8, 8)).save(p)
    code, _, err = run_cli(["qa", "psnr", str(a), str(b), "--a-frames", "0-0"])
    assert code == 2 and "for videos" in err


def test_psnr_pairs_frames_across_containers_that_round_time_differently(film_builder):
    """Matroska keeps frame times to the millisecond and QuickTime to 1/30000 s at 30 fps: ffmpeg's default pairing
    matched a third of the frames with their neighbours (27.98 dB on identical pictures). Pairing by the nearest
    timestamp holds every frame to its own."""
    from qa_core_helpers import moving_square, needs_ffmpeg  # noqa: PLC0415

    if needs_ffmpeg.args[0]:
        pytest.skip("ffmpeg isn't installed")
    fb = film_builder()
    frames = moving_square(30, (64, 48))
    mkv = fb.encode("cmp/same.mkv", frames)
    mov = fb.encode("cmp/same.mov", frames, codec="prores")
    code, out, err = run_cli(["qa", "psnr", str(mkv), str(mov), "--json"])
    doc = json.loads(out)
    assert code == 0, err
    assert doc["summary"]["frames"] == 30 and doc["summary"]["min_db"] > 60  # ProRes against lossless, frame for frame
