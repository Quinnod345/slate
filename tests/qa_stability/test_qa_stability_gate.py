"""The stability gate on a small film: renders are lossless 16-bit grey files, so the scan reads exactly what the test
drew. Act 1 has a card that pops in and holds; act 2 has a card that steps whole pixels forward and one back (layout
rounding under a move). Each test checks one rule of the triage, the baseline, the reference or the command."""

from __future__ import annotations

import argparse
import json

import numpy as np
import pytest

from qa_stability_helpers import needs_ffmpeg
from slate import gates, jsonio
from slate.errors import Refused, UsageError
from slate.qa import stability as S

pytestmark = needs_ffmpeg

POP_AT = 16  # local frame where act 1's card pops in
REV = {10: (1, 0), 11: (2, 0), 13: (3, 0), 17: (2, 0)}  # +1, +1, 0, +1, 0, 0, 0, -1: the step back at local 17


def _pop_frames(helpers, film, *, at: int = POP_AT, seed: int = 3) -> np.ndarray:
    still = helpers.textured(film.size, seed=seed)
    frames = np.repeat(still[None], film.n, axis=0)
    frames[at:, 60:120, 200:260] = 245.0
    return frames


def _reversal_frames(helpers, film, *, rev: bool = True) -> np.ndarray:
    still = helpers.textured(film.size, seed=4)
    return helpers.card_frames(still, film.n, (60, 30, 150, 120), REV if rev else {})


def _film(stab_film, helpers, *, rev: bool = True, **kw):
    kw.setdefault("frames", 26)
    f = stab_film(**kw)
    f.render(1, _pop_frames(helpers, f))
    f.render(2, _reversal_frames(helpers, f, rev=rev))
    return f


def _gate(film, **opts):
    with S.run_options(jobs=1, **opts):
        return S.gate(film.path)


def test_new_pops_wait_for_a_person_and_a_reversal_fails(stab_film, helpers):
    f = _film(stab_film, helpers)
    r = _gate(f)
    assert r.status == "FAIL", r.summary
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    assert checks == {"reversal": "FAIL", "flags": "REVIEW"}
    act1 = [i for i in r.details["review_items"] if i["act"] == 1]
    assert [i["key"] for i in act1] == [f"pop:frame:{POP_AT}"]
    assert act1[0]["strip"] and (f.path / act1[0]["strip"]).is_file()
    # act 2's card stepping whole pixels also snaps and wobbles, in the reversal's own frames and nowhere else
    act2 = [i for i in r.details["review_items"] if i["act"] == 2]
    assert act2 and {i["frame"] - f.act_in(2) for i in act2} <= set(range(10, 18))
    assert {i["class"] for i in act2} <= {"snap", "relative", "wobble", "shake"}
    assert any("whole-pixel step back" in c["summary"] for c in r.details["checks"])
    rep2 = jsonio.read(f.path / "qa/stability/act2.json")
    assert {fl["class"] for fl in rep2["open"]} >= {"reversal"}
    assert rep2["sha256"] == jsonio.sha256(f.path / "renders/act2.mkv")
    assert r.engine["name"] == "slate stability scan" and "ffmpeg" in r.engine["decoder"]
    assert "renders/act1.mkv" in r.inputs and "timing/visual_events/act1.json" in r.inputs


def test_a_pop_on_a_planned_beat_is_the_films_own(stab_film, helpers):
    f = _film(stab_film, helpers, rev=False)
    f.events(1, [f.act_in(1) + POP_AT + 2])
    r = _gate(f)
    assert r.status == "PASS", r.summary
    assert r.details["acts"]["1"]["near_beats"] == 1
    near = jsonio.read(f.path / "qa/stability/act1.json")["near_beats"]
    assert near[0]["class"] == "pop" and near[0]["distance"] == 2


def test_a_planned_beat_never_excuses_a_reversal(stab_film, helpers):
    f = _film(stab_film, helpers)
    f.events(2, [f.act_in(2) + 17])
    r = _gate(f)
    assert r.status == "FAIL"
    assert any(c["check"] == "reversal" and c["status"] == "FAIL" for c in r.details["checks"])


def test_the_baseline_accepts_reviewed_flags_but_never_a_reversal(stab_film, helpers):
    f = _film(stab_film, helpers)
    _gate(f)
    with pytest.raises(UsageError):
        S.accept_baseline(f.path, 1, by="", reason="")
    rec = S.accept_baseline(f.path, 1, by="Quinn", reason="the card lands by design; see NOTES.md")
    assert rec["added"] >= 1 and rec["reversals_left_out"] == 0
    rec2 = S.accept_baseline(f.path, 2, by="Quinn", reason="trying to accept a reversal")
    assert rec2["reversals_left_out"] >= 1
    r = _gate(f)
    assert r.status == "FAIL"  # act 2's reversal is still there
    assert r.details["acts"]["1"]["accepted_by_baseline"] >= 1
    assert r.details["review_items"] == []
    data = jsonio.read(f.path / "qa/stability/baseline_act1.json")
    assert data["accepted"][0]["by"] == "Quinn" and all(row[0] != "reversal" for row in data["flags"])
    # the baseline belongs to the act, not one render: a re-render of the same content still counts as accepted
    f.render(1, _pop_frames(helpers, f), name="act1_v2")
    f.render(2, _reversal_frames(helpers, f, rev=False), name="act2_v2")
    assert _gate(f).status == "PASS"


def test_the_baseline_waits_for_a_current_report_and_for_a_person(stab_film, helpers, monkeypatch):
    f = _film(stab_film, helpers)
    _gate(f)
    f.encode("renders/act1.mkv", _pop_frames(helpers, f, at=POP_AT + 4))  # re-rendered since the report
    with pytest.raises(Refused, match="doesn't describe its current render"):
        S.accept_baseline(f.path, 1, by="Quinn", reason="looked")
    monkeypatch.setattr("slate.procs.agent_context", lambda: "an agent")
    with pytest.raises(Refused, match="autonomous"):
        S.accept_baseline(f.path, 1, by="agent", reason="looks fine", mode="autonomous")


def test_a_reference_render_that_shares_a_flag_marks_it_as_design(stab_film, helpers):
    f = stab_film(frames=26)
    f.render(1, _pop_frames(helpers, f), name="act1_v1")  # the previous render of act 1 had the same pop
    f.render(2, _reversal_frames(helpers, f, rev=False))
    v2 = _pop_frames(helpers, f)
    v2[:, 130:170, 10:50] += 2.0  # the new render changed something else, nowhere near the pop
    f.render(1, v2, name="act1_v2")
    r = _gate(f)
    assert r.status == "PASS", r.summary
    a1 = r.details["acts"]["1"]
    assert a1["shared_with_reference"] >= 1 and a1["reference"]["file"] == "renders/act1_v1.mkv"
    # a reference a person sets wins over the previous render; one without the pop leaves the pop open
    clean = f.encode("renders/clean_reference.mkv", np.repeat(helpers.textured(f.size, seed=3)[None], f.n, axis=0))
    rec = S.set_reference(f.path, 1, clean, by="Quinn", why="the approved render")
    assert rec["file"] == "renders/clean_reference.mkv"
    r2 = _gate(f)
    assert r2.status == "REVIEW" and [i["key"] for i in r2.details["review_items"]] == [f"pop:frame:{POP_AT}"]


def test_quick_never_starts_a_scan_and_a_narrowed_run_cant_pass(stab_film, helpers):
    from slate.qa.film import session

    f = _film(stab_film, helpers, rev=False)
    with session(f.path) as ctx, S.run_options(jobs=1):
        ctx.quick = True
        r = S.gate(f.path)
    assert r.status == "UNCHECKED" and "haven't been scanned" in r.summary
    full = _gate(f)
    with session(f.path) as ctx, S.run_options(jobs=1):
        ctx.quick = True
        again = S.gate(f.path)
    assert again.status == full.status == "REVIEW"
    narrowed = _gate(f, acts=[2])
    assert narrowed.status == "UNCHECKED"
    assert any(c["check"] == "scope" for c in narrowed.details["checks"])
    with pytest.raises(UsageError):
        _gate(f, acts=[9])


def test_no_renders_is_unchecked_with_the_next_step(stab_film):
    f = stab_film(frames=26)
    r = _gate(f)
    assert r.status == "UNCHECKED" and "next: slate render" in r.evidence


def test_through_slate_qa_run_and_triage_acceptance(stab_film, helpers, monkeypatch):
    """The way the integrator wires it: the gate in slate.qa.GATES, recorded in qa/gates.json, and a person
    accepting one REVIEW item with `slate qa triage --accept stability:pop:frame:N`."""
    from slate import qa
    from slate.qa import accept

    f = _film(stab_film, helpers, rev=False)
    monkeypatch.setitem(qa.GATES, "stability", S.gate)
    monkeypatch.setitem(qa.SOURCES, "stability", "slate.qa.stability")
    with S.run_options(jobs=1):
        results = qa.run(f.path, ["stability"])
    assert [r.status for r in results] == ["REVIEW"]
    assert qa.valid_result(results[0]) == []
    rec = accept.accept(f.path, f"stability:pop:frame:{POP_AT}", by="Quinn", reason="lands by design")
    assert rec["gate"] == "stability"
    with S.run_options(jobs=1):
        again = qa.run(f.path, ["stability"])
    assert again[0].status == "PASS" and again[0].details["accepted"][0]["accepted_by"] == "Quinn"
    latest = gates.load_report(f.path)["stability"]
    assert latest.status == "PASS"


def test_the_command(stab_film, helpers, slate_home, capsys):
    f = _film(stab_film, helpers, rev=False)
    parser = argparse.ArgumentParser(prog="slate qa")
    sub = parser.add_subparsers(dest="stage")
    S.register(sub)
    args = parser.parse_args(["stability", "--film", str(f.path), "--jobs", "1", "--json"])
    from slate import log

    log.set_json(True)
    try:
        assert args.handler(args) == 1  # a REVIEW isn't a pass
        out = json.loads(capsys.readouterr().out)
        assert out["gates"][0]["status"] == "REVIEW"
        args = parser.parse_args(["stability", "baseline", "--act", "1", "--by", "Quinn", "--reason", "by design",
                                  "--film", str(f.path), "--json"])
        assert args.handler(args) == 0
        assert json.loads(capsys.readouterr().out)["baseline"]["added"] >= 1
        args = parser.parse_args(["stability", "strip", "--act", "1", "--frame", str(f.act_in(1) + POP_AT),
                                  "--box", "180,40,100,100", "--film", str(f.path), "--json"])
        assert args.handler(args) == 0
        assert (f.path / f"qa/stability/strips/act1_frame{f.act_in(1) + POP_AT}.png").is_file()
        args = parser.parse_args(["stability", "--film", str(f.path), "--jobs", "1", "--json"])
        assert args.handler(args) == 0
        capsys.readouterr()
        args = parser.parse_args(["stability", "compare", "--act", "1", str(f.path / "renders/act1.mkv"),
                                  "--film", str(f.path), "--json"])
        assert args.handler(args) == 0  # a render against itself: nothing moved, nothing softer
        assert json.loads(capsys.readouterr().out)["result"]["gate"] == "stability.regression"
        args = parser.parse_args(["stability", "compare", "--act", "1", "--film", str(f.path), "--json"])
        with pytest.raises(UsageError, match="nothing to compare"):
            args.handler(args)
    finally:
        log.set_json(False)
    command = parser._subparsers._group_actions[0].choices["stability"]  # noqa: SLF001
    text = S.DESCRIPTION + S.HELP + "".join(a.help or "" for a in command._actions)  # noqa: SLF001
    for tool in ("Bash", "TodoWrite", "WebFetch", "ToolSearch", "Read tool", "Edit tool"):
        assert tool not in text


def test_strips_show_each_render_as_a_row(tmp_path, helpers):
    from PIL import Image

    a = helpers.drift(helpers.textured((200, 120), seed=2), 8, vx=0.5)
    out = S.strip(tmp_path / "s.png", [("one", a, 0), ("two", a, 1)], 0, 5, (40, 30, 60, 40), zoom=3, guides=[10.5],
                  title="fixture")
    im = Image.open(out)
    assert im.size[0] > 5 * 60 * 3 and im.size[1] > 2 * 40 * 3


def _parser():
    parser = argparse.ArgumentParser(prog="slate qa")
    S.register(parser.add_subparsers(dest="stage"))
    return parser


def test_detach_queues_the_scan_as_a_job_and_quick_never_starts_one(stab_film, helpers, monkeypatch, capsys):
    from slate import log
    from slate.jobs import client

    f = _film(stab_film, helpers, rev=False)
    seen = {}

    def fake_submit(kind, argv, **kw):
        seen.update(kind=kind, argv=argv, **kw)
        return "job-7"

    monkeypatch.setattr(client, "submit", fake_submit)
    log.set_json(True)
    try:
        args = _parser().parse_args(["stability", "--detach", "--act", "1", "--jobs", "2", "--film", str(f.path), "--json"])
        assert args.handler(args) == 0
        assert json.loads(capsys.readouterr().out)["job"] == "job-7"
        assert seen["kind"] == "qa" and seen["resources"] == ("render",)
        assert seen["argv"][-6:] == ["--film", str(f.path), "--act", "1", "--jobs", "2"]
        args = _parser().parse_args(["stability", "--quick", "--film", str(f.path), "--json"])
        assert args.handler(args) == 1
        out = json.loads(capsys.readouterr().out)
        assert out["gates"][0]["status"] == "UNCHECKED" and "haven't been scanned" in out["gates"][0]["summary"]
    finally:
        log.set_json(False)
