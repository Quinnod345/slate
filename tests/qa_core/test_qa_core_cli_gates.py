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
