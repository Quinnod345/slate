"""The hand-in runs the motion law on the act's trace (handin.act<N>.motion): the motion gate's trace checks, no
pixels, on timing/motion/actN.json as `slate emit-events` wrote it. A live look test's act passed `slate check`
and then failed `slate qa motion` four ways (entrances, durations, acceleration, rig symmetry); a law failure now
fails the hand-in before anything renders.

The engine's own hand-in (Node) is faked here: these tests are about what Slate adds after it, so they need neither
Node nor a browser and run in a second."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qa_motion"))
from qa_motion_helpers import NEUTRAL_LAW, entry, make_film  # noqa: E402

from slate import gates  # noqa: E402
from slate.cli import run_cli  # noqa: E402
from slate.qa import motion as M  # noqa: E402

ENGINE_GATES = ("typecheck", "module", "word_misses", "banned_words", "literals", "events", "margins", "contracts")
# A trace that keeps the neutral law: a 0.5 s rise on "move", an opacity and a 90% scale entrance on "enter".
KEEPS = [entry("title", "y", 0, 15, 24, 0, ease="move", entrance=True),
         entry("title", "opacity", 0, 15, 0, 1, ease="enter"),
         entry("card", "scale", 4, 19, 0.9, 1.0, ease="enter", entrance=True)]
# One that breaks it: a card scaling up from half its size, and a 0.7 s move that's on no step of the duration scale.
BREAKS = [entry("card", "scale", 0, 15, 0.5, 1.0, ease="enter", entrance=True),
          entry("title", "y", 0, 21, 24, 0, ease="move", entrance=True)]


@pytest.fixture(autouse=True)
def law(monkeypatch):
    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))


@pytest.fixture
def engine(monkeypatch):
    """The engine's hand-in, faked: every act gets its usual gates, all passing. Records what it was asked."""
    from slate.engine import bridge

    asked = []

    def fake(command, args=None, *, timeout=None):
        assert command == "check", command
        asked.append(args)
        out = [{"gate": f"handin.act{a}.{n}", "status": "PASS", "summary": "fine", "evidence": [],
                "engine": {"name": "fake engine", "version": "0"}, "inputs": {}, "checked_at": gates.now_iso(),
                "details": {"stage": "scenes"}} for a in args["acts"] for n in ENGINE_GATES]
        return {"ok": True, "gates": out, "acts": [{"act": a} for a in args["acts"]]}

    monkeypatch.setattr(bridge, "call", fake)
    return asked


def _film(home, film_id: str, trace: list | None) -> Path:
    film = make_film(home["films"], film_id=film_id, size=(640, 360), acts={1: (0, 30)},
                     traces={1: trace} if trace is not None else None)
    (film / "scenes" / "act1").mkdir(parents=True)
    (film / "scenes" / "act1" / "index.tsx").write_text("export default function Act1() { return null; }\n")
    return film


def check(film: Path, *extra: str) -> tuple[int, dict]:
    code, out, err = run_cli(["check", "1", "--film", str(film), "--json", *extra])
    return code, json.loads(out)


def _motion(out: dict) -> dict | None:
    return next((g for g in out["gates"] if g["gate"] == "handin.act1.motion"), None)


def test_an_act_whose_trace_breaks_the_law_fails_the_hand_in(slate_home, engine):
    film = _film(slate_home, "breaks", BREAKS)
    code, out = check(film)
    g = _motion(out)
    assert code == 1 and g["status"] == "FAIL", g
    checks = {c["check"]: c["status"] for c in g["details"]["checks"]}
    assert checks["entrances"] == "FAIL" and checks["durations"] == "FAIL", checks
    assert "pixels" not in checks  # the trace alone: nothing rendered, nothing read but the trace
    assert "enters from 50%" in " ".join(g["evidence"]), g["evidence"]
    # the engine's gates are all still there, and the output keeps its shape
    assert {f"handin.act1.{n}" for n in ENGINE_GATES} <= {x["gate"] for x in out["gates"]}
    assert set(out) == {"film", "acts", "skipped", "gates", "summary"} and out["summary"]["status"] == "FAIL"
    report = gates.load_report(film)
    assert report["handin.act1.motion"].status == "FAIL" and report["handin.act1.motion"].details["stage"] == "scenes"


def test_an_act_whose_trace_keeps_the_law_passes(slate_home, engine):
    film = _film(slate_home, "keeps", KEEPS)
    code, out = check(film)
    g = _motion(out)
    assert code == 0 and g["status"] == "PASS", g
    assert "3 traced moves" in g["summary"] and "slate qa motion" in g["summary"]
    assert "timing/motion/act1.json" in g["inputs"] and "timing/shots.json" in g["inputs"]
    assert "brand:neutral-test#motion" in g["inputs"]  # the law it read, never the whole pack
    assert not (film / "qa" / "motion").exists()  # no pixel scan, no cache


def test_a_trace_the_act_has_moved_on_from_is_never_judged_as_current(slate_home, engine):
    """emit-events records the trace with the act's sources; once they change, the trace on disk isn't the act."""
    from slate.state import lock

    film = _film(slate_home, "moved", KEEPS)
    lock.record(film, "timing/motion/act1.json", producer="slate emit-events",
                inputs=["scenes/act1/index.tsx", "timing/shots.json"])
    assert check(film)[0] == 0
    (film / "scenes" / "act1" / "index.tsx").write_text("export default function Act1() { return 1; }\n")
    code, out = check(film)
    g = _motion(out)
    assert code == 1 and g["status"] == "FAIL" and "out of date with the act" in g["summary"], g
    assert "slate emit-events --act 1" in g["summary"]


def test_an_act_with_no_trace_keeps_the_hand_in_it_had(slate_home, engine):
    """An act drawn without the kit's motion registry has no trace: the hand-in has nothing to read, and says so in
    words; the film's motion gate (with its pixel cross-check) judges it."""
    film = _film(slate_home, "untraced", None)
    code, out = check(film)
    assert code == 0 and _motion(out) is None
    code, text, _ = run_cli(["check", "1", "--film", str(film)])
    assert "no motion trace" in text and "slate qa motion" in text


def test_a_dry_run_hand_in_says_what_it_would_check_and_runs_nothing(slate_home, engine):
    film = _film(slate_home, "dry", BREAKS)
    code, out, _ = run_cli(["--dry-run", "check", "1", "--film", str(film), "--json"])
    out = json.loads(out)
    assert code == 0 and out["dry_run"] is True and engine == []  # the engine was never asked
    assert out["would_check"]["motion"] == [1] and out["would_check"]["queued"] is False
    assert not (film / "qa").exists()  # nothing written


def test_an_approval_leaves_the_hand_in_motion_result_current_and_a_law_change_does_not(slate_home, engine):
    """The gate records the brand's motion law it read (brand:<name>#motion), not the whole pack."""
    from slate import jsonio

    pack = slate_home["brands"] / "neutral-test"
    jsonio.write_atomic(pack / "brand.json", {"name": "neutral-test", "motion": NEUTRAL_LAW, "references": {}})
    film = _film(slate_home, "current", KEEPS)
    assert check(film)[0] == 0
    doc = json.loads((pack / "brand.json").read_text())
    doc["references"]["look_motion"] = {"file": "references/look_motion.mp4", "approved_by": "A Person"}
    doc["palette"] = {"ink": "#111111"}
    jsonio.write_atomic(pack / "brand.json", doc)
    assert gates.load_report(film)["handin.act1.motion"].status == "PASS"
    doc["motion"] = {**doc["motion"], "durations": {**doc["motion"]["durations"], "m": 0.55}}
    jsonio.write_atomic(pack / "brand.json", doc)
    assert gates.load_report(film)["handin.act1.motion"].status == "STALE"
