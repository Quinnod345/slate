"""The project audit (validate.jsx's checks as gate results) and the smoke test (built in the mock AE, rendered by a
fake aerender, measured in synthetic pixels)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import gates, jsonio
from slate.ae import audit, eases, project, smoke
from slate.errors import Busy, Refused

from ae_helpers import FakeMedia, MockAE, make_film, prefs_bytes


# ---- the audit: parsing a fake report -------------------------------------------------------------------------


def test_gates_from_a_fake_report(tmp_path):
    aep = tmp_path / "f.aep"
    aep.write_bytes(b"aep")
    checks = [{"check": "project.bpc", "status": "PASS", "evidence": "bpc=16"},
              {"check": "fonts", "status": "FAIL", "evidence": "1 missing or substituted"},
              {"check": "master.audio", "status": "UNCHECKED", "evidence": "the film has no mix yet"},
              {"check": "extra.thing", "status": "weird", "evidence": "?"}]
    results = audit.gates_from_report(checks, aep=aep, ae_version="26.5x89")
    by = {r.gate: r for r in results}
    assert by["ae.project.bpc"].status == "PASS" and by["ae.project.bpc"].engine == {
        "name": "After Effects validate.jsx", "version": "26.5x89"}
    assert by["ae.fonts"].status == "FAIL" and by["ae.fonts"].summary == "1 missing or substituted"
    assert by["ae.master.audio"].status == "UNCHECKED"
    assert by["ae.extra.thing"].status == "UNCHECKED"  # an unknown status is never a pass
    assert by["ae.shots.comps"].status == "UNCHECKED" and "didn't report" in by["ae.shots.comps"].summary
    assert len(results) == len(audit.CHECKS) + 1
    assert gates.exit_code(results) == 1


def test_audit_reads_a_saved_report(tmp_path):
    rep = tmp_path / "validate.json"
    rep.write_text(json.dumps({"data": {"ae_version": "26.5", "checks": [
        {"check": c, "status": "PASS", "evidence": "ok"} for c in audit.CHECKS]}}))
    results = audit.run(tmp_path / "f.aep", report=rep, write=False)
    assert gates.overall(results) == "PASS" and gates.exit_code(results) == 0
    # `slate status` files every audit gate under the master stage (ae.shots.comps isn't a timing gate)
    assert {r.details["stage"] for r in results} == {"master"}
    missing = audit.gates_from_report([], aep=tmp_path / "f.aep")
    assert {r.status for r in missing} == {"UNCHECKED"} and {r.details["stage"] for r in missing} == {"master"}


# ---- the audit: end to end through the mock AE ----------------------------------------------------------------


@pytest.fixture
def built(slate_home, live_prefs):
    d, media = make_film(slate_home["films"], slate_home["brands"])
    pf = live_prefs(data=prefs_bytes(scripting=b'"0"'))
    ae = MockAE(pf, media=media)
    project.build(d, go=True, runner=ae)
    return {"dir": d, "ae": ae, **slate_home}


def test_audit_end_to_end(built):
    results = audit.run(film_dir=built["dir"], go=True, runner=built["ae"])
    assert {r.status for r in results} == {"PASS"}, [(r.gate, r.status, r.summary) for r in results if not r.passed]
    assert {r.gate for r in results} == {f"ae.{c}" for c in audit.CHECKS}
    saved = gates.load_report(built["dir"])
    assert saved["ae.master.frames"].status == "PASS"
    assert jsonio.read(built["dir"] / "ae" / "audit.json")["data"]["checks"]


def test_audit_without_go_reports_the_last_one_and_turns_stale(built):
    none = audit.run(film_dir=built["dir"])
    assert [(r.gate, r.status) for r in none] == [("ae.audit", "UNCHECKED")]
    audit.run(film_dir=built["dir"], go=True, runner=built["ae"])
    again = audit.run(film_dir=built["dir"])
    assert {r.status for r in again} == {"PASS"}
    aep = built["dir"] / "ae" / "neutral.aep"
    aep.write_bytes(aep.read_bytes() + b" changed in AE")
    stale = audit.run(film_dir=built["dir"])
    assert {r.status for r in stale} == {"STALE"} and stale[0].details["was"] == "PASS"


def test_audit_without_go_never_starts_ae_and_autonomous_films_never_audit_live(built):
    calls = []
    audit.run(film_dir=built["dir"], runner=lambda *a, **k: calls.append(a))  # a read of the last audit
    assert calls == []
    fj = json.loads((built["dir"] / "film.json").read_text())
    (built["dir"] / "film.json").write_text(json.dumps({**fj, "mode": "autonomous"}))
    with pytest.raises(Refused, match="utonomous"):
        audit.run(film_dir=built["dir"], go=True, runner=built["ae"])


# ---- the smoke test -------------------------------------------------------------------------------------------


def _draw_frames(p: dict, *, eased: bool = True):
    """Frames as aerender would render them: the dot where the ease (or a straight line) puts it."""
    from PIL import Image, ImageDraw

    def draw(folder: Path, n: int) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        r = p["diameter"] / 2
        for i in range(n):
            u = (i - p["start_frame"]) / (p["end_frame"] - p["start_frame"])
            k = eases.value(p["ease"], u) if eased else min(1.0, max(0.0, u))
            x = p["from_x"] + (p["to_x"] - p["from_x"]) * k
            img = Image.new("RGB", (p["width"], p["height"]), (0x14, 0x14, 0x14))
            ImageDraw.Draw(img).ellipse([x - r, p["y"] - r, x + r, p["y"] + r], fill=(0xFF, 0xD6, 0x33))
            img.save(folder / f"f{i + 1:03d}.png")

    return draw


@pytest.fixture
def smoke_ae(live_prefs, tmp_path):
    fake = FakeMedia({smoke.COMP: 30})
    ae = MockAE(live_prefs(data=prefs_bytes(scripting=b'"0"')), other=fake,
                state={"prefs": {"Main Pref Section v2": {"Pref_SCRIPTING_FILE_NETWORK_SECURITY": 0}}})
    return {"ae": ae, "fake": fake, "out": tmp_path / "smoke"}


def test_smoke_without_go_starts_nothing(slate_home):
    r = smoke.run()
    assert r.status == "UNCHECKED" and "slate ae smoke --go" in r.details["next"]


def test_smoke_end_to_end_passes_in_pixels(smoke_ae, slate_home):
    p = smoke.params(**smoke.DEFAULT)
    smoke_ae["fake"].on_frames = _draw_frames(p)
    r = smoke.run(smoke_ae["out"], go=True, runner=smoke_ae["ae"])
    assert r.status == "PASS", r.summary
    assert r.details["worst_px"] < 1.0 and r.details["ae_curve_max_error_px"] < 0.05
    assert r.details["scripting_pref"] == 0
    aer = next(c for c in smoke_ae["fake"].calls if Path(c[0]).name == "aerender")
    assert aer[aer.index("-comp") + 1] == smoke.COMP and aer[aer.index("-OMtemplate") + 1] == "High Quality"
    assert (smoke_ae["out"] / "smoke.aep").is_file()
    # the doctor's view afterwards, without starting anything
    last = smoke.run()
    assert last.status == "PASS" and last.details["recorded"] is True


def test_smoke_fails_when_the_pixels_miss_the_ease(smoke_ae, slate_home):
    p = smoke.params(**smoke.DEFAULT)
    smoke_ae["fake"].on_frames = _draw_frames(p, eased=False)
    r = smoke.run(smoke_ae["out"], go=True, runner=smoke_ae["ae"])
    assert r.status == "FAIL" and "misses the ease" in r.summary


def test_smoke_fails_on_a_short_render(smoke_ae, slate_home):
    smoke_ae["fake"].comp_frames[smoke.COMP] = 12
    r = smoke.run(smoke_ae["out"], go=True, runner=smoke_ae["ae"])
    assert r.status == "FAIL" and "12 frames of 30" in r.summary


def test_smoke_never_replaces_unsaved_work(live_prefs, tmp_path, slate_home):
    ae = MockAE(live_prefs(data=prefs_bytes(scripting=b'"0"')), other=FakeMedia({smoke.COMP: 30}),
                state={"dirtyProject": True})
    with pytest.raises(Busy) as exc:
        smoke.run(tmp_path / "s", go=True, runner=ae)
    assert exc.value.exit_code == 5
    assert ae.results[-1]["saves"] == [] and ae.results[-1]["discarded"] == []


def test_smoke_refuses_autonomous_runs(monkeypatch):
    monkeypatch.setenv("SLATE_MODE", "autonomous")
    with pytest.raises(Refused):
        smoke.run(go=True, runner=lambda *a, **k: pytest.fail("nothing may start"))
