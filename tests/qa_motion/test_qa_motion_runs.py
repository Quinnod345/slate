"""How the gates run: a narrowed run (some acts, one render, some checks) reports what it finds but can't be recorded
as the gate's PASS; the whole-picture scans are cached against the picture's hash, reused by --quick and never
started by it; a new render turns the result STALE; the flags reach the gates through qa-core's `slate qa` with the
add_qa_options / qa_options hook; and the gates' own commands record through slate.qa.run, so an autonomous film
parks on REVIEW."""

from __future__ import annotations

import argparse
import contextlib
import io
import json

import numpy as np
import pytest

from qa_motion_helpers import NEUTRAL_LAW, entry, make_film, register_render, textured_square_frames, write_frames
from slate import cli, gates, log, procs
from slate.errors import UsageError
from slate.qa import access as A
from slate.qa import motion as M
from slate.qa.film import FilmContext, session

SIZE = (320, 180)


@pytest.fixture
def home(slate_home, monkeypatch):
    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))
    return slate_home


def two_act_film(home, film_id="two", *, broken_act=None, velocity=(4, 0)):
    """Two 16-frame acts, each traced (a fade and a gentle travel) with a registered render of a slowly moving
    square. `broken_act` gets an entrance from 0% scale."""
    traces = {}
    for a, (f_in, f_out) in {1: (0, 16), 2: (16, 32)}.items():
        moves = [entry(f"title{a}", "opacity", f_in, f_in + 15, 0.0, 1.0, ease="enter", shot=f"S{a}"),
                 entry(f"square{a}", "x", f_in, f_in + 15, 10, 30, ease="move", shot=f"S{a}")]
        if a == broken_act:
            moves.append(entry(f"card{a}", "scale", f_in, f_in + 15, 0.0, 1.0, ease="enter", entrance=True,
                               shot=f"S{a}"))
        traces[a] = moves
    film = make_film(home["films"], film_id=film_id, size=SIZE, acts={1: (0, 16), 2: (16, 32)}, traces=traces)
    for a, f_in in ((1, 0), (2, 16)):
        write_frames(film / "renders" / f"act{a}", textured_square_frames(16, size=SIZE, velocity=velocity,
                                                                           start=(10, 60), square=48, seed=a))
        register_render(film, a, f"renders/act{a}", f_in, f_in + 16)
    return film


def checks(result) -> dict:
    return {c["check"]: c["status"] for c in result.details["checks"]}


# ---------------------------------------------------------------------------------------------------- narrowed runs
def test_the_whole_film_can_pass(home):
    r = M.evaluate(two_act_film(home))
    assert r.status == "PASS", r.summary
    assert "scope" not in checks(r) and "narrowed" not in r.details


def test_a_run_over_some_acts_reports_them_but_cannot_pass_the_gate(home):
    film = two_act_film(home, broken_act=1)
    r = M.evaluate(film, acts=[2])
    # act 2 is clean in every check it has (act 1's broken entrance wasn't looked at)...
    assert {k: v for k, v in checks(r).items() if k != "scope"} == {
        "trace": "PASS", "eases": "PASS", "durations": "PASS", "speed": "PASS", "loops": "PASS", "pixels": "PASS"}
    # ...but a run over some acts can't be recorded as the gate's PASS
    assert checks(r)["scope"] == "UNCHECKED" and r.status == "UNCHECKED"
    assert r.details["narrowed"] == ["act 2 of 1, 2 (--act)"]
    broken = M.evaluate(film, acts=[1])
    assert broken.status == "FAIL" and checks(broken)["entrances"] == "FAIL"  # a FAIL stays a FAIL
    assert M.evaluate(film, acts=[1, 2]).status == "FAIL" and "scope" not in checks(M.evaluate(film, acts=[1, 2]))


def test_only_some_checks_is_narrowed_and_unknown_checks_are_usage_errors(home):
    film = two_act_film(home)
    r = M.evaluate(film, only=["speed", "loops"])
    assert set(checks(r)) == {"speed", "loops", "scope"} and r.status == "UNCHECKED"
    assert M.evaluate(film, only=list(M.SUBCHECKS)).status == "PASS"  # every check named is the whole gate
    with pytest.raises(UsageError, match="no wobble check"):
        M.evaluate(film, only=["wobble"])


def test_an_explicit_render_is_narrowed(home):
    film = two_act_film(home)
    r = M.evaluate(film, render="renders/act2", render_act=2)
    assert checks(r)["pixels"] == "PASS" and checks(r)["scope"] == "UNCHECKED"
    assert "--render" in r.details["narrowed"][0]


def test_run_options_reach_the_gate_function(home):
    film = two_act_film(home)
    with M.run_options(acts=[2], pixels=False):
        narrowed = M.gate(film)
    assert narrowed.details["acts"] == [2] and checks(narrowed)["pixels"] == "UNCHECKED"
    assert M.gate(film).status == "PASS"  # outside the block: the whole film again
    with pytest.raises(TypeError):
        with M.run_options(speed=3):
            pass


# ---------------------------------------------------------------------------------------------------- scans
def test_the_pixel_scan_is_cached_against_the_picture(home, monkeypatch):
    film = two_act_film(home)
    calls = []
    real = M.fast_pairs
    monkeypatch.setattr(M, "fast_pairs", lambda *a, **k: calls.append(1) or real(*a, **k))
    M.evaluate(film)
    assert len(calls) == 2 and len(list((film / "qa" / "motion").glob("pixels_*.json"))) == 2
    M.evaluate(film)
    assert len(calls) == 2  # both acts reused
    frames = textured_square_frames(16, size=SIZE, velocity=(24, 0), start=(10, 60), square=48)
    write_frames(film / "renders" / "act2", frames)  # act 2 re-rendered, now with a fast untraced move
    r = M.evaluate(film)
    assert len(calls) == 3 and checks(r)["pixels"] == "REVIEW"


def test_quick_reuses_a_scan_and_never_starts_one(home, monkeypatch):
    film = two_act_film(home)
    calls = []
    real = M.fast_pairs
    monkeypatch.setattr(M, "fast_pairs", lambda *a, **k: calls.append(1) or real(*a, **k))
    with session(film) as ctx:
        ctx.quick = True
        r = M.evaluate(film)
    px = next(c for c in r.details["checks"] if c["check"] == "pixels")
    assert px["status"] == "UNCHECKED" and "--quick" in px["summary"] and not calls
    assert "without --quick" in px["hint"]
    M.evaluate(film)
    with session(film) as ctx:
        ctx.quick = True
        again = M.evaluate(film)
    assert again.status == "PASS" and len(calls) == 2


def test_the_flash_scan_is_cached_and_quick_never_starts_it(slate_home, monkeypatch):
    film = make_film(slate_home["films"], size=(96, 54), acts={1: (0, 30)})
    write_frames(film / "renders" / "act1", [np.full((54, 96, 3), 90, np.uint8)] * 30)
    register_render(film, 1, "renders/act1", 0, 30)
    calls = []
    real = A.scan_flashes
    monkeypatch.setattr(A, "scan_flashes", lambda *a, **k: calls.append(1) or real(*a, **k))
    quick = FilmContext(film, quick=True)
    s = A.check_flashes(quick)
    assert s.status == "UNCHECKED" and "--quick" in s.summary and not calls
    assert A.check_flashes(FilmContext(film)).status == "PASS" and len(calls) == 1
    assert A.check_flashes(FilmContext(film, quick=True)).status == "PASS" and len(calls) == 1


@pytest.mark.skipif(procs.which("ffmpeg") is None, reason="ffmpeg isn't installed")
def test_a_new_render_turns_both_gates_stale(home):
    film = make_film(home["films"], size=SIZE, acts={1: (0, 16)},
                     traces={1: [entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter")]})
    pngs = write_frames(home["tmp"] / "pngs", textured_square_frames(16, size=SIZE, velocity=(3, 0),
                                                                    start=(10, 60), square=48))
    mov = film / "renders" / "act1.mov"
    mov.parent.mkdir(parents=True, exist_ok=True)

    def encode(src):
        procs.run(["ffmpeg", "-v", "error", "-y", "-framerate", "30", "-i", str(src / "f%05d.png"), "-c:v",
                   "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", str(mov)], check=True, timeout=120)

    encode(pngs)
    register_render(film, 1, "renders/act1.mov", 0, 16)
    motion, access = M.evaluate(film), A.evaluate(film)
    assert "renders/act1.mov" in motion.inputs and "renders/act1.mov" in access.inputs
    gates.write_report(film, [motion, access])
    assert {g: r.status for g, r in gates.load_report(film).items()} == {"motion": motion.status,
                                                                         "access": access.status}
    other = write_frames(home["tmp"] / "pngs2", textured_square_frames(16, size=SIZE, velocity=(5, 0),
                                                                      start=(10, 60), square=48))
    encode(other)
    assert {r.status for r in gates.load_report(film).values()} == {"STALE"}


# ------------------------------------------------------------------------------------------ qa-core's slate qa
def qa_core_parser():
    """qa-core's real `slate qa` (a leaf command, gate names as positionals), which carries the motion and access
    flags through this module's hook (add_qa_options / qa_options)."""
    from slate.qa import cli as qa_cli

    parser = argparse.ArgumentParser(prog="slate", parents=[cli.COMMON])
    sub = parser.add_subparsers(dest="command")
    qa_cli.register(sub)
    return parser


def run_qa_core(argv):
    import os

    args = qa_core_parser().parse_args(argv)
    log.set_json(bool(cli.flag(args, "json", False)))
    if cli.flag(args, "film"):
        os.environ["SLATE_FILM"] = str(cli.flag(args, "film"))
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                code = args.handler(args)
            except UsageError as exc:
                code = exc.exit_code
    finally:
        log.set_json(False)
        os.environ.pop("SLATE_FILM", None)
    return code, out.getvalue()


def test_qa_cores_slate_qa_takes_the_flags_through_the_hook(home):
    film = two_act_film(home, broken_act=1)
    code, out = run_qa_core(["qa", "motion", "--act", "2", "--no-pixels", "--film", str(film), "--json"])
    doc = json.loads(out)
    motion = doc["results"][0]
    assert code == 1 and motion["gate"] == "motion" and motion["status"] == "UNCHECKED"
    assert motion["details"]["acts"] == [2] and "--act" in motion["details"]["narrowed"][0]
    code, out = run_qa_core(["qa", "motion", "--film", str(film), "--json"])
    assert code == 1 and json.loads(out)["results"][0]["status"] == "FAIL"  # no flags: the whole film


def test_the_hook_refuses_flags_that_dont_fit_the_run(home):
    film = two_act_film(home)
    for argv in (["qa", "loudness", "--act", "1"], ["qa", "--act", "1"], ["qa", "access", "--no-pixels"],
                 ["qa", "access", "--only", "speed"], ["qa", "motion", "--only", "captions"],
                 ["qa", "motion", "--act", "9"], ["qa", "motion", "--render", "nowhere.mov"],
                 ["qa", "motion", "--render-from", "3"], ["qa", "access", "--act", "1"],
                 ["qa", "motion", "--render", "renders/act1", "--act", "1", "--act", "2"]):
        code, _ = run_qa_core([*argv, "--film", str(film)])
        assert code == 2, argv


def test_both_gates_share_one_set_of_flags(home):
    film = two_act_film(home)
    code, out = run_qa_core(["qa", "motion", "access", "--only", "speed", "--only", "captions", "--film", str(film),
                             "--json"])
    results = {r["gate"]: r for r in json.loads(out)["results"]}
    assert {c["check"] for c in results["motion"]["details"]["checks"]} == {"speed", "scope"}
    assert {c["check"] for c in results["access"]["details"]["checks"]} == {"scope"}  # no voice: nothing to caption
    args = qa_core_parser().parse_args(["qa", "motion", "--act", "2", "--no-pixels", "--only", "speed"])
    assert M.qa_option_argv(args) == ["--act", "2", "--no-pixels", "--only", "speed"]


# ---------------------------------------------------------------------------------------------------- own commands
def test_the_motion_command_parks_an_autonomous_film_on_review(home):
    film = make_film(home["films"], film_id="auto", size=SIZE, acts={1: (0, 16)},
                     traces={1: [entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter")]},
                     extra={"mode": "autonomous"})
    write_frames(film / "renders" / "act1", textured_square_frames(16, size=SIZE, velocity=(24, 0),
                                                                   start=(10, 60), square=48))
    register_render(film, 1, "renders/act1", 0, 16)
    parser = argparse.ArgumentParser(prog="slate", parents=[cli.COMMON])
    stages = cli.add_group(parser.add_subparsers(dest="command"), "qa", "gates")
    M.register(stages)
    args = parser.parse_args(["qa", "motion", "--film", str(film), "--json"])
    log.set_json(True)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = args.handler(args)
    finally:
        log.set_json(False)
    doc = json.loads(out.getvalue())
    assert code == 1 and doc["gates"][0]["status"] == "REVIEW"
    assert doc["parked"] and doc["parked"]["reviews"][0]["gate"] == "motion"
    assert json.loads((film / "qa" / "parked.json").read_text())["parked"] is True
    assert (film / "qa" / "history.jsonl").read_text().count('"gate": "motion"') == 1
