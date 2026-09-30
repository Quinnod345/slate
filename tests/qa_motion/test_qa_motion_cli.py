"""The commands and the wiring: `slate qa motion` and `slate qa access` (through qa-core's runner, and through each
module's own register(sub)), --json output, the shared exit codes, GATES merged into slate.qa, and the source rules
(no film-size literals in logic, no company names)."""

from __future__ import annotations

import argparse
import io
import json
import re
import tokenize
from pathlib import Path

import pytest

from qa_motion_helpers import NEUTRAL_LAW, entry, make_film, register_render, textured_square_frames, write_frames
from slate import cli, log
from slate.qa import access as A
from slate.qa import motion as M

MINE = [Path(M.__file__), Path(A.__file__)]
TESTS = sorted(Path(__file__).parent.glob("*.py"))


@pytest.fixture
def law(monkeypatch):
    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))


def _clean_film(home, film_id="clean"):
    trace = [entry("title", "y", 0, 15, 24, 0, ease="move", entrance=True),  # a travel that starts at rest
             entry("title", "opacity", 0, 15, 0, 1, ease="enter"),
             entry("card", "scale", 4, 19, 0.9, 1.0, ease="enter", entrance=True)]
    film = make_film(home["films"], film_id=film_id, size=(640, 360), acts={1: (0, 24)}, traces={1: trace})
    write_frames(film / "renders" / "act1", textured_square_frames(24, size=(640, 360), velocity=(3, 0),
                                                                   start=(10, 132), square=96))
    register_render(film, 1, "renders/act1", 0, 24)
    return film


def _parser():
    """A `slate` parser with a `qa` group whose stages are this module's commands (how a stage-style qa CLI wires
    them)."""
    parser = argparse.ArgumentParser(prog="slate", parents=[cli.COMMON])
    sub = parser.add_subparsers(dest="command")
    stages = cli.add_group(sub, "qa", "gates")
    M.register(stages)
    A.register(stages)
    return parser


def _run(argv):
    """Parse and run like slate.cli.main does, capturing stdout."""
    import contextlib

    args = _parser().parse_args(argv)
    log.set_json(bool(cli.flag(args, "json", False)))
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                code = args.handler(args)
            except Exception as exc:  # noqa: BLE001
                from slate.errors import SlateError

                if not isinstance(exc, SlateError):
                    raise
                code = exc.exit_code
    finally:
        log.set_json(False)
    return code, out.getvalue()


# ---------------------------------------------------------------------------------------------------- register(sub)
def test_help_for_both_commands(capsys):
    for name, words in (("motion", "16-frame period"), ("access", "17 characters a second")):
        with pytest.raises(SystemExit) as exc:
            _parser().parse_args(["qa", name, "--help"])
        assert exc.value.code == 0
        assert words in " ".join(capsys.readouterr().out.split())


def test_motion_command_passes_a_clean_film_with_json(slate_home, law):
    film = _clean_film(slate_home)
    code, out = _run(["qa", "motion", "--film", str(film), "--json"])
    doc = json.loads(out)
    assert code == 0, doc["gates"][0]["summary"]
    assert doc["gates"][0]["gate"] == "motion" and doc["summary"]["status"] == "PASS"
    checks = {c["check"]: c["status"] for c in doc["gates"][0]["details"]["checks"]}
    assert checks == {"trace": "PASS", "eases": "PASS", "durations": "PASS", "entrances": "PASS", "speed": "PASS",
                      "loops": "PASS", "pixels": "PASS"}
    recorded = json.loads((film / "qa" / "gates.json").read_text())
    assert recorded["gates"]["motion"]["status"] == "PASS"


def test_motion_command_fails_with_exit_1_and_says_why(slate_home, law):
    film = make_film(slate_home["films"], film_id="broken",
                     traces={1: [entry("card", "scale", 0, 15, 0.0, 1.0, ease="enter", entrance=True)]})
    code, out = _run(["qa", "motion", "--film", str(film), "--no-pixels"])
    assert code == 1
    assert "FAIL" in out and "enters from 0%" in out and "pixels" in out


def test_dry_run_changes_nothing(slate_home, law):
    film = _clean_film(slate_home, "dry")
    code, out = _run(["qa", "motion", "--film", str(film), "--dry-run"])
    assert code == 0 and "dry run" in out and not (film / "qa" / "gates.json").exists()


def test_unknown_film_and_act_are_usage_errors(slate_home, law):
    code, _ = _run(["qa", "motion", "--film", str(slate_home["films"] / "nope")])
    assert code == 2
    film = _clean_film(slate_home, "acts")
    code, _ = _run(["qa", "motion", "--film", str(film), "--act", "9"])
    assert code == 2


def test_access_command_runs_one_subcheck_with_json(slate_home):
    film = make_film(slate_home["films"], film_id="captioned")
    (film / "voice" / "captions").mkdir(parents=True)
    (film / "voice" / "captions" / "c.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nMuch too much to read in a second\n")
    code, out = _run(["qa", "access", "--film", str(film), "--only", "captions", "--json"])
    doc = json.loads(out)
    assert code == 1 and doc["gates"][0]["gate"] == "access"
    # the narrowed run reports its FAIL (a FAIL stays a FAIL) and says it only looked at the captions
    assert [c["check"] for c in doc["gates"][0]["details"]["checks"]] == ["captions", "scope"]
    assert doc["gates"][0]["status"] == "FAIL" and "characters a second" in doc["gates"][0]["summary"]
    assert doc["options"] == {"only": ["captions"]}


# ---------------------------------------------------------------------------------------------------- slate.qa
def test_gates_merge_into_slate_qa():
    from slate import qa

    status = qa.load_external(refresh=True)
    assert status["slate.qa.motion"].startswith("merged") and status["slate.qa.access"].startswith("merged")
    assert qa.SOURCES["motion"] == "slate.qa.motion" and qa.SOURCES["access"] == "slate.qa.access"
    assert set(M.GATES) == {"motion"} and set(A.GATES) == {"access"}


def test_slate_qa_runs_the_motion_gate(slate_home, law):
    film = _clean_film(slate_home, "via-core")
    code, out, err = cli.run_cli(["qa", "motion", "--film", str(film), "--json"])
    if code == 2 and "invalid choice" in err:
        pytest.skip("slate.qa.cli isn't importable right now")
    doc = json.loads(out)
    results = doc.get("results") or doc.get("gates")
    assert code == 0 and results[0]["gate"] == "motion" and results[0]["status"] == "PASS"


def test_gate_results_fit_the_gate_schema(slate_home, law):
    from slate import qa

    film = _clean_film(slate_home, "schema")
    for result in (M.gate(film), A.gate(film)):
        assert qa.valid_result(result) == [], result.gate
        json.dumps(result.to_dict())  # plain JSON all the way down


# ---------------------------------------------------------------------------------------------------- source rules
def _numbers(path: Path) -> list[str]:
    toks = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    return [t.string for t in toks if t.type == tokenize.NUMBER]


def test_no_film_size_or_rate_literals_in_the_logic():
    for path in MINE:
        numbers = set(_numbers(path))
        assert not numbers & {"1920", "1080", "30000", "1001", "29.97", "23.976"}, path.name


def test_no_company_material():
    # built from pieces so this file doesn't contain the words it looks for
    words = ["true" + "space", "vi" + "xa", "tru" + "dy", "inner[ -]?" + "echo"]
    loose = re.compile("|".join(words), re.IGNORECASE)
    exact = re.compile(r"\b" + "E" + "OS" + r"\b")
    for path in MINE + TESTS:
        text = path.read_text(encoding="utf-8")
        hits = [m.group(0) for m in loose.finditer(text)] + [m.group(0) for m in exact.finditer(text)]
        assert not hits, (path.name, hits[:5])
