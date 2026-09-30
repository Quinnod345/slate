"""qa.run(), the gate registry and its merge, parking, acceptance, triage and the `slate qa` command."""

import json
import sys
import types

import numpy as np
import pytest

from slate import gates, jsonio, procs
from slate import qa
from slate.cli import run_cli
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.gates import GateResult
from slate.qa import accept, park, triage

from qa_core_helpers import moving_square, needs_ffmpeg, write_brand, write_json


@pytest.fixture
def fresh_registry():
    """Put the gate registry back exactly as it was after a test plays with it."""
    saved_gates, saved_sources, saved_ext = dict(qa.GATES), dict(qa.SOURCES), qa.EXTERNAL
    yield
    qa.GATES.clear()
    qa.GATES.update(saved_gates)
    qa.SOURCES.clear()
    qa.SOURCES.update(saved_sources)
    qa.EXTERNAL = saved_ext
    qa.load_external(refresh=True)


def _review_gate(film_dir):
    """A stand-in gate that always needs a person."""
    return GateResult.review("reviewer", "the hero moment needs a person's eye",
                             evidence=["review/contact_sheet.jpg"], engine={"name": "stand-in", "version": "1"})


def _autonomous_review_film(film_builder, brand_store=None, *, mode="autonomous"):
    """A film (autonomous by default) with a stand-in `reviewer` gate that returns REVIEW."""
    qa.GATES["reviewer"] = _review_gate
    return film_builder(mode=mode)


# ---------------------------------------------------------------------------------------------- run()
def test_run_writes_a_valid_report_and_every_result_names_its_engine(film_builder):
    fb = film_builder()
    results = qa.run(fb.path)
    assert [r.gate for r in results] == list(qa.GATES)
    report = json.loads((fb.path / "qa" / "gates.json").read_text())
    assert jsonio.validate(report, "gates") == []
    for r in results:
        assert r.engine.get("name") and r.engine.get("version"), r.gate
        assert qa.valid_result(r) == [], r.gate
    # a film with nothing built yet: nothing unchecked is reported as passed
    assert gates.overall(results) != "PASS"
    assert all(r.status != "PASS" for r in results if r.gate in ("frames", "glitch", "psnr", "ocr", "loudness"))
    history = (fb.path / "qa" / "history.jsonl").read_text().strip().splitlines()
    assert len(history) == len(results)


def test_run_names_and_errors(film_builder, fresh_registry):
    fb = film_builder()
    assert [r.gate for r in qa.run(fb.path, ["events_complete", "schema"])] == ["schema", "events_complete"]
    with pytest.raises(UsageError):
        qa.run(fb.path, ["no_such_gate"])
    if "motion" not in qa.GATES:
        with pytest.raises(MissingPrerequisite):
            qa.run(fb.path, ["motion"])
    with pytest.raises(UsageError):
        qa.run(fb.path.parent / "not-a-film")


def test_a_crashing_gate_is_unchecked_not_a_crash(film_builder, fresh_registry):
    fb = film_builder()

    def broken(film_dir):
        raise ZeroDivisionError("oops")

    def refusing(film_dir):
        raise MissingPrerequisite("the thing isn't installed", hint="install the thing")

    def sloppy(film_dir):
        return {"status": "FAIL", "summary": "found a problem", "evidence": [1, 2]}

    qa.GATES.update(broken=broken, refusing=refusing, sloppy=sloppy)
    out = {r.gate: r for r in qa.run(fb.path, ["broken", "refusing", "sloppy"])}
    assert out["broken"].status == "UNCHECKED" and "ZeroDivisionError" in out["broken"].summary
    assert "traceback" in out["broken"].details
    assert out["refusing"].status == "UNCHECKED" and out["refusing"].evidence == ["next: install the thing"]
    assert out["sloppy"].status == "FAIL" and out["sloppy"].evidence == ["1", "2"]  # coerced, verdict kept
    assert out["sloppy"].engine["name"].endswith("sloppy")


def test_external_gates_merge_without_replacing_core_gates(fresh_registry, monkeypatch, tmp_path):
    def motion_gate(film_dir):
        return GateResult.pass_("motion", "fine", engine={"name": "motion", "version": "1"})

    def their_captions(film_dir):
        return GateResult.pass_("captions", "theirs", engine={"name": "access", "version": "1"})

    fake = types.ModuleType("slate.qa.motion")
    fake.GATES = {"motion": motion_gate, "captions": their_captions}
    monkeypatch.setitem(sys.modules, "slate.qa.motion", fake)
    status = qa.load_external(refresh=True)
    assert qa.GATES["motion"] is motion_gate
    assert qa.GATES["captions"] is qa.CORE["captions"]
    assert qa.GATES["motion.captions"] is their_captions
    assert qa.SOURCES["motion"] == "slate.qa.motion"
    assert status["slate.qa.motion"].startswith("merged")
    # a module that writes into GATES when it's imported gets the same treatment
    pkg_dir = tmp_path / "ext"
    pkg_dir.mkdir()
    (pkg_dir / "ext_mutating.py").write_text(
        "from slate.qa import GATES\n"
        "from slate.gates import GateResult\n"
        "def frames(film_dir):\n    return GateResult.pass_('frames', 'not really')\n"
        "def contrast(film_dir):\n    return GateResult.pass_('contrast', 'ok')\n"
        "GATES['frames'] = frames\nGATES['contrast'] = contrast\n")
    monkeypatch.syspath_prepend(str(pkg_dir))
    import slate.qa as package

    monkeypatch.setattr(package, "__path__", [*package.__path__, str(pkg_dir)])
    qa.EXTERNAL = ("slate.qa.ext_mutating",)
    sys.modules.pop("slate.qa.ext_mutating", None)
    qa.load_external(refresh=True)
    assert qa.GATES["frames"] is qa.CORE["frames"]
    assert "ext_mutating.frames" in qa.GATES and "contrast" in qa.GATES
    sys.modules.pop("slate.qa.ext_mutating", None)


# ---------------------------------------------------------------------------------------------- parking
def test_review_parks_an_autonomous_film_with_its_evidence(film_builder, fresh_registry):
    fb = _autonomous_review_film(film_builder)
    results = qa.run(fb.path, ["reviewer"])
    assert results[0].status == "REVIEW"
    assert gates.exit_code(results) == 1  # never a pass
    held = park.status(fb.path)
    assert held and held["reviews"][0]["gate"] == "reviewer"
    assert held["reviews"][0]["evidence"] == ["review/contact_sheet.jpg"]
    since = held["since"]
    qa.run(fb.path, ["reviewer"])
    assert park.status(fb.path)["since"] == since  # still the same hold


def test_supervised_films_are_not_parked(film_builder, fresh_registry):
    fb = _autonomous_review_film(film_builder, mode="supervised")
    assert qa.run(fb.path, ["reviewer"])[0].status == "REVIEW"
    assert park.status(fb.path) is None


def test_park_tells_the_state_module_when_it_can(film_builder, monkeypatch, fresh_registry):
    calls = []
    fake = types.ModuleType("slate.state.park")
    fake.park = lambda film_dir, *, reason, evidence, source: calls.append((reason, source))
    monkeypatch.setitem(sys.modules, "slate.state.park", fake)
    fb = _autonomous_review_film(film_builder)
    qa.run(fb.path, ["reviewer"])
    assert calls and calls[0][1] == "qa" and "reviewer" in calls[0][0]
    assert park.status(fb.path)["state_hook"] == "slate.state.park.park"


def test_parking_round_trip_through_the_real_state_module(film_builder, monkeypatch, fresh_registry):
    state_park = pytest.importorskip("slate.state.park")
    approvals = pytest.importorskip("slate.state.approvals")
    fb = _autonomous_review_film(film_builder)
    qa.run(fb.path, ["reviewer"])
    (q,) = [q for q in approvals.open_questions(fb.path, parking=True) if q.get("source") == "qa"]
    assert "reviewer" in q["text"]
    monkeypatch.setattr(procs, "agent_context", lambda: None)
    record = park.unpark(fb.path, by="Quinn", note="seen it", mode="autonomous")
    assert record.get("state_questions_answered") == 1
    assert not [q for q in approvals.open_questions(fb.path, parking=True) if q.get("source") == "qa"]
    assert state_park is not None


def test_only_a_person_unparks_an_autonomous_film(film_builder, monkeypatch, fresh_registry):
    fb = _autonomous_review_film(film_builder)
    qa.run(fb.path, ["reviewer"])
    monkeypatch.setenv("SLATE_CONTEXT", "openclaw")
    with pytest.raises(Refused):
        park.unpark(fb.path, by="the agent itself", mode="autonomous")
    with pytest.raises(Refused):
        accept.accept(fb.path, "claims:x", by="an agent", reason="looks fine", mode="autonomous")
    monkeypatch.delenv("SLATE_CONTEXT")
    monkeypatch.setattr(procs, "agent_context", lambda: None)
    with pytest.raises(UsageError):
        park.unpark(fb.path, by="", mode="autonomous")
    record = park.unpark(fb.path, by="Quinn", note="looked at it", mode="autonomous")
    assert record["released_by"] == "Quinn" and park.status(fb.path) is None


# ---------------------------------------------------------------------------------------------- acceptance
def test_accept_needs_a_current_result_and_a_real_item(film_builder, monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    fb = film_builder()
    with pytest.raises(UsageError):
        accept.accept(fb.path, "glitch", by="Q", reason="r")
    with pytest.raises(UsageError):
        accept.accept(fb.path, "glitch:frame:1", by="Q", reason="r")  # no result yet
    item = {"key": "frame:5", "what": "a flag", "basis": {"renders/act1.mkv": "a" * 64}}
    r = GateResult.review("glitch", "1 flag", details={"review_items": [item]},
                          inputs={"film.json": jsonio.sha256(fb.path / "film.json")})
    gates.write_report(fb.path, [r])
    with pytest.raises(UsageError):
        accept.accept(fb.path, "glitch:frame:5", by="Q", reason="")
    with pytest.raises(UsageError):
        accept.accept(fb.path, "glitch:frame:6", by="Q", reason="r")
    rec = accept.accept(fb.path, "glitch:frame:5", by="Quinn", reason="a blink by design")
    assert rec["basis"] == item["basis"] and accept.match(fb.path, "glitch", "frame:5", item["basis"])
    assert accept.match(fb.path, "glitch", "frame:5", {"renders/act1.mkv": "b" * 64}) is None
    # a stale result can't be accepted: its evidence is out of date
    write_json(fb.path / "film.json", {**fb.film, "title": "changed"})
    with pytest.raises(Refused):
        accept.accept(fb.path, "glitch:frame:5", by="Quinn", reason="again")


# ---------------------------------------------------------------------------------------------- triage
def test_triage_sorts_what_is_not_passing(film_builder):
    fb = film_builder()
    fb.all_events()
    qa.run(fb.path, ["events_complete", "frames"])
    rep = triage.report(fb.path)
    assert "events_complete" in rep["passed"]
    assert [e["gate"] for e in rep["groups"]["UNCHECKED"]] == ["frames"]
    assert rep["groups"]["UNCHECKED"][0]["next"] == "slate render"
    assert "psnr" in rep["not_run"] and rep["status"] == "UNCHECKED"
    # change an input: the passed gate turns STALE
    fb.events(1, [fb.beat("S01", "moved", 5)])
    rep = triage.report(fb.path)
    assert [e["gate"] for e in rep["groups"]["STALE"]] == ["events_complete"]
    text = triage.format_report(rep)
    assert "Stale" in text and "Never run" in text


# ---------------------------------------------------------------------------------------------- the command
def test_cli_runs_gates_with_json_and_exit_codes(film_builder):
    fb = film_builder()
    fb.all_events()
    code, out, err = run_cli(["qa", "--film", str(fb.path), "events_complete", "--json"])
    obj = json.loads(out)
    assert code == 0 and obj["status"] == "PASS" and obj["results"][0]["gate"] == "events_complete"
    (fb.path / "timing" / "visual_events" / "act2.json").unlink()
    code, out, err = run_cli(["qa", "--film", str(fb.path), "events_complete"])
    assert code == 1 and "FAIL" in out and "slate qa triage" in out
    code, out, err = run_cli(["qa", "--film", str(fb.path), "no_such_gate"])
    assert code == 2 and "No gate called" in err
    code, out, err = run_cli(["qa", "--list", "--json"])
    assert code == 0 and {g["gate"] for g in json.loads(out)["gates"]} >= set(qa.CORE)


def test_cli_triage_accept_and_unpark(film_builder, monkeypatch, fresh_registry):
    fb = _autonomous_review_film(film_builder)
    code, out, _ = run_cli(["qa", "--film", str(fb.path), "reviewer", "--json"])
    assert code == 1 and json.loads(out)["parked"]["parked"] is True
    code, out, _ = run_cli(["qa", "--film", str(fb.path), "triage", "--json"])
    rep = json.loads(out)
    assert code == 0 and rep["parked"] and rep["groups"]["REVIEW"][0]["gate"] == "reviewer"
    code, out, err = run_cli(["qa", "--film", str(fb.path), "unpark"])
    assert code == 2
    monkeypatch.setattr(procs, "agent_context", lambda: None)
    code, out, err = run_cli(["qa", "--film", str(fb.path), "unpark", "--by", "Quinn", "--json"])
    assert code == 0 and json.loads(out)["unparked"]["released_by"] == "Quinn"


def test_cli_psnr_images(tmp_path):
    from PIL import Image

    a = np.zeros((8, 8, 3), dtype=np.uint8)
    b = a.copy()
    b[0, 0, 0] = 20  # MSE 400/192: 44.9 dB
    Image.fromarray(a).save(tmp_path / "a.png")
    Image.fromarray(b).save(tmp_path / "b.png")
    code, out, _ = run_cli(["qa", "psnr", str(tmp_path / "a.png"), str(tmp_path / "b.png"), "--json"])
    obj = json.loads(out)
    assert code == 0 and obj["kind"] == "image" and obj["psnr_db"] > 40
    code, out, _ = run_cli(["qa", "psnr", str(tmp_path / "a.png"), str(tmp_path / "b.png"), "--min", "60"])
    assert code == 1 and out.startswith("FAIL")
    code, out, err = run_cli(["qa", "psnr", str(tmp_path / "a.png"), str(tmp_path / "missing.png")])
    assert code == 2


@needs_ffmpeg
def test_cli_psnr_videos(film_builder):
    fb = film_builder(shots=[(1, 10)])
    frames = moving_square(10, fb.size)
    a = fb.encode("a.mkv", frames)
    b = fb.encode("b.mkv", frames[:9])
    code, out, _ = run_cli(["qa", "psnr", str(a), str(a), "--json"])
    assert code == 0 and json.loads(out)["summary"]["frames"] == 10
    code, out, _ = run_cli(["qa", "psnr", str(a), str(b), "--json"])
    obj = json.loads(out)
    assert code == 1 and obj["problems"] and obj["summary"]["frames"] == 9


def test_cli_detach_queues_the_run(film_builder, monkeypatch):
    client = pytest.importorskip("slate.jobs.client")
    seen = {}

    def fake_submit(kind, argv, **kw):
        seen.update(kind=kind, argv=argv, **kw)
        return "job-123"

    monkeypatch.setattr(client, "submit", fake_submit)
    fb = film_builder()
    code, out, _ = run_cli(["qa", "--film", str(fb.path), "glitch", "psnr", "--detach", "--json"])
    obj = json.loads(out)
    assert code == 0 and obj["job"] == "job-123" and obj["next"] == "slate job status job-123"
    assert seen["kind"] == "qa" and seen["resources"] == ("render",) and seen["resumable"] is True
    assert seen["argv"][-4:] == ["--film", str(fb.path), "glitch", "psnr"]
    code, out, err = run_cli(["qa", "--film", str(fb.path), "nope", "--detach"])
    assert code == 2  # refused before anything is queued


def test_cli_motion_before_its_module_exists_is_a_missing_prerequisite(film_builder, fresh_registry):
    for name in [g for g, src in qa.SOURCES.items() if src == "slate.qa.motion"]:
        qa.GATES.pop(name)
        qa.SOURCES.pop(name)
    qa.EXTERNAL_STATUS["slate.qa.motion"] = "not installed yet"
    fb = film_builder()
    code, out, err = run_cli(["qa", "--film", str(fb.path), "motion", "--json"])
    obj = json.loads(out)
    assert code == 3 and obj["error"] == "missing_prerequisite" and "slate.qa.motion" in obj["message"]


def test_cli_group_selects_every_gate_a_module_provides(film_builder, fresh_registry):
    def one(film_dir):
        return GateResult.pass_("extra.one", "ok", engine={"name": "t", "version": "1"})

    def two(film_dir):
        return GateResult.pass_("extra.two", "ok", engine={"name": "t", "version": "1"})

    qa.GATES.update({"extra.one": one, "extra.two": two})
    assert qa.resolve(["extra"]) == ["extra.one", "extra.two"]
