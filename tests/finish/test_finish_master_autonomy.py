"""The owner's own After Effects finish of an autonomous film (GAPS-FINAL A1.3).

An autonomous film is mastered with ffmpeg and held. Its one AE path is its owner's own finish: `slate master --ae
--go`, typed by a person at their own Terminal, after a final-cut approval of its held master. An agent, a queued job,
any other AE command, or the same command before that approval, is refused. Nothing here starts After Effects: the AE
steps are faked, as in test_finish_master.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import filmkit
from slate import gates, jsonio, ledger, procs
from slate.ae import guard
from slate.errors import Refused
from slate.finish import deps, master
from slate.finish.film import Film
from slate.state import approvals, autonomy


class FakeAE:
    """Stands in for slate.ae, and checks the guard the real steps call (so the owner's finish has to let it through)."""

    def __init__(self, monkeypatch, film_dir: Path):
        self.calls: list[str] = []
        self.aep = film_dir / "ae" / f"{film_dir.name}.aep"
        monkeypatch.setattr(deps, "ae_aep", lambda fd: self.aep if self.aep.is_file() else None)
        monkeypatch.setattr(deps, "ae_build", self.build)
        monkeypatch.setattr(deps, "ae_audit_run", self.audit)
        monkeypatch.setattr(deps, "ae_audit_last", lambda fd, aep: self.results())
        monkeypatch.setattr(deps, "ae_masters", self.masters)
        monkeypatch.setattr(deps, "ae_require_closed", lambda what: None)

    def results(self):
        return [gates.GateResult.pass_("ae.project.bpc", "16 bpc", engine=gates.engine("fake ae", "0"))]

    def build(self, film_dir):
        guard.person_start(True, "Building the film's After Effects project", film_dir=film_dir, action="ae_build")
        self.calls.append("build")
        self.aep.parent.mkdir(parents=True, exist_ok=True)
        self.aep.write_bytes(b"fake aep")
        return self.aep

    def audit(self, film_dir, aep):
        guard.person_start(True, "Auditing the AE project", film_dir=film_dir, action="ae")
        self.calls.append("audit")
        return self.results()

    def masters(self, film_dir, *, silent=False):
        guard.person_start(True, "Rendering the masters with aerender", film_dir=film_dir, action="ae_master")
        self.calls.append("masters")
        film = Film(film_dir)
        names = master.master_paths(film)
        pro, h = film.path(names["prores_422_hq"]), film.path(names["h264"])
        master._make_prores(film, film.act_renders(), film.mix(), pro)
        master._make_h264(film, pro, h)
        h.write_bytes(h.read_bytes() + b"\0")  # the AE master isn't byte-identical to the held one
        return {"prores_422_hq": str(pro), "h264": str(h)}


def _as_agent(monkeypatch):
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (False, "this is running under test-agent"))
    monkeypatch.setattr(procs, "agent_context", lambda: "test-agent")


def _as_person(monkeypatch):
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (True, ""))
    monkeypatch.setattr(procs, "agent_context", lambda: None)


def _held(make_film) -> tuple[Path, str]:
    """An autonomous film made through slate.state (so it's on Slate's record), mastered with ffmpeg and held, with the
    H.264 master delivered byte for byte (as `slate deliver` copies it). Returns the delivered file, the one the owner
    approves (`slate approve final_cut deliver/v1/…_H264.mp4`)."""
    d = make_film("autofin", mode="autonomous")
    assert autonomy.is_locked(d)
    code, out = filmkit.cli_json(d, "master")
    assert code == 0 and out["method"] == "ffmpeg" and out["held"], out
    return d, _deliver_copy(d)


def _deliver_copy(d: Path) -> str:
    src = d / master.master_paths(Film(d))["h264"]
    rel = f"deliver/v1/{src.name}"
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    (d / rel).write_bytes(src.read_bytes())
    return rel


def test_the_owners_finish_waits_for_the_final_cut_approval_and_a_person(make_film, monkeypatch):
    d, held = _held(make_film)
    _as_person(monkeypatch)
    with pytest.raises(Refused, match="approved its held master"):
        master.choose(Film(d), ae=True)  # a person, but nobody has approved the held master
    approvals.record(d, what="final_cut", path=held, approved_by="Test Person", note="watched it on the phone")
    assert autonomy.held_master_approval(d)["path"] == held
    _as_agent(monkeypatch)
    with pytest.raises(Refused, match="started by a person at their own Terminal"):
        master.choose(Film(d), ae=True)  # approved, but an agent typed it
    code, out, err = filmkit.cli(d, "master", "--ae", "--go")
    assert code == 1 and "person at their own Terminal" in err
    monkeypatch.setenv("SLATE_JOB_ID", "j-20261001-000000-abcd")
    _as_person(monkeypatch)
    with pytest.raises(Refused, match="queued job"):
        master.choose(Film(d), ae=True)  # never as a queued job, even one a person submitted
    monkeypatch.delenv("SLATE_JOB_ID")
    method, why = master.choose(Film(d), ae=True)
    assert method == "ae" and "approved its held master" in why


def test_the_owners_finish_runs_its_ae_steps_and_holds_the_new_master(make_film, monkeypatch):
    d, held = _held(make_film)
    approvals.record(d, what="final_cut", path=held, approved_by="Test Person")
    ae = FakeAE(monkeypatch, d)
    _as_person(monkeypatch)
    # Outside the finish itself, After Effects still never touches the film, even for the person with the approval.
    with pytest.raises(Refused):
        guard.person_start(True, "Building", film_dir=d, action="ae_build")
    code, out = filmkit.cli_json(d, "master", "--ae", "--go")
    assert code == 0 and out["status"] == "project_built" and ae.calls == ["build", "audit"], out
    code, out = filmkit.cli_json(d, "master", "--ae", "--go")
    assert code == 0, out
    assert ae.calls == ["build", "audit", "masters"] and out["method"] == "ae" and out["held"]
    finish = next(g for g in out["gates"] if g["gate"] == "master.finish")
    assert finish["status"] == "PASS" and "owner's own finish" in finish["summary"]
    rec = jsonio.read(d / "masters/master.json")
    assert rec["method"] == "ae" and rec["mode"] == "autonomous" and rec["held"]
    # The AE master is new bytes: it's held again, and another AE run waits for the owner's approval of it.
    assert autonomy.held_master_approval(d) is None
    with pytest.raises(Refused, match="approved its held master"):
        master.choose(Film(d), ae=True)
    (d / "deliver/v2").mkdir(parents=True)
    newer = "deliver/v2/" + Path(held).name
    (d / newer).write_bytes((d / master.master_paths(Film(d))["h264"]).read_bytes())
    approvals.record(d, what="final_cut", path=newer, approved_by="Test Person")
    assert master.choose(Film(d), ae=True)[0] == "ae"


def test_an_approved_master_is_never_rendered_over_without_force(make_film, monkeypatch):
    d = make_film("autolock", mode="autonomous")
    assert filmkit.cli_json(d, "master")[0] == 0
    h264 = master.master_paths(Film(d))["h264"]
    approvals.record(d, what="final_cut", path=h264, approved_by="Test Person")  # the master itself, locked
    before = jsonio.sha256(d / h264)
    ae = FakeAE(monkeypatch, d)
    _as_person(monkeypatch)
    assert filmkit.cli_json(d, "master", "--ae", "--go")[1]["status"] == "project_built"
    code, out, err = filmkit.cli(d, "master", "--ae", "--go")
    assert code == 1 and "is locked" in err and "--force --approval" in err  # refused before aerender writes
    assert ae.calls == ["build", "audit"] and jsonio.sha256(d / h264) == before


def test_the_owners_finish_never_runs_as_a_queued_job(make_film, monkeypatch):
    d, held = _held(make_film)
    approvals.record(d, what="final_cut", path=held, approved_by="Test Person")
    FakeAE(monkeypatch, d)
    _as_person(monkeypatch)
    code, out, err = filmkit.cli(d, "master", "--ae", "--go", "--detach")
    assert code == 1 and "never touch After Effects" in err and "not as a queued job" in err  # before any queueing
    from slate.jobs import store

    assert store.all_jobs() == []


def test_a_hand_edited_held_film_takes_the_same_path(make_film, monkeypatch):
    d, held = _held(make_film)
    data = jsonio.read(d / "film.json")
    data["mode"] = "supervised"
    (d / "film.json").write_text(jsonio.dumps(data))  # an agent's edit: film.json now says supervised
    assert master.film_mode(Film(d)) == "autonomous"
    _as_agent(monkeypatch)
    with pytest.raises(Refused, match="never use After Effects"):
        master.choose(Film(d), ae=True)
    assert master.choose(Film(d)) == ("ffmpeg", "autonomous films are mastered with ffmpeg and held for the owner")
    approvals.record(d, what="final_cut", path=held, approved_by="Test Person")
    _as_person(monkeypatch)
    assert master.choose(Film(d), ae=True)[0] == "ae"
