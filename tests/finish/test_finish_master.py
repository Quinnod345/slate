"""slate master: the method by mode and --go, the ffmpeg master and its gates, and the After Effects finish (faked)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import filmkit
from slate import gates, jsonio
from slate.errors import Busy, Refused, UsageError
from slate.finish import checks, deps, master
from slate.finish.film import Film


# ---------------------------------------------------------------------------------------------------------------
# Which method


@pytest.mark.parametrize("mode,ae,ffmpeg,want", [
    ("supervised", False, False, "ae"),
    ("supervised", True, False, "ae"),
    ("supervised", False, True, "ffmpeg"),
    ("express", False, False, "ae"),
    ("draft", False, False, "ffmpeg"),
    ("autonomous", False, False, "ffmpeg"),
])
def test_method_by_mode(make_film, mode, ae, ffmpeg, want):
    d = make_film("m", mode=mode, renders=False, mix=False)
    method, why = master.choose(Film(d), ae=ae, ffmpeg=ffmpeg)
    assert method == want and why


def test_autonomous_never_ae_even_with_go(make_film):
    d = make_film("auto", mode="autonomous", renders=False, mix=False)
    with pytest.raises(Refused, match="never use After Effects"):
        master.choose(Film(d), ae=True)
    code, out, err = filmkit.cli(d, "master", "--ae", "--go")
    assert code == 1 and "never use After Effects" in err


def test_draft_refuses_ae(make_film):
    d = make_film("dr", mode="draft", renders=False, mix=False)
    with pytest.raises(Refused, match="Draft films are mastered with ffmpeg"):
        master.choose(Film(d), ae=True)


def test_both_flags_is_usage(make_film):
    d = make_film("u", renders=False, mix=False)
    with pytest.raises(UsageError):
        master.choose(Film(d), ae=True, ffmpeg=True)


def test_supervised_ae_waits_for_a_persons_go(make_film, monkeypatch):
    d = make_film("sup")
    reached = []
    monkeypatch.setattr(deps, "ae_build", lambda film_dir: reached.append("build"))
    code, out, err = filmkit.cli(d, "master")  # the mode says AE, and nobody said go
    assert code == 1 and "waits for a person's go" in err and "--go" in err
    assert reached == []


def test_configured_ffmpeg_for_supervised(make_film, settings_toml):
    settings_toml('[finish]\nsupervised_master = "ffmpeg"\n')
    d = make_film("cfg")
    assert master.choose(Film(d))[0] == "ffmpeg"


def test_dry_run_changes_nothing(make_film):
    d = make_film("dry", mode="draft")
    code, p = filmkit.cli_json(d, "master", "--dry-run")
    assert code == 0 and p["method"] == "ffmpeg" and p["would"] == "master"
    assert not [p for p in (d / "masters").glob("*") if p.is_file() and not p.name.startswith(".")]


# ---------------------------------------------------------------------------------------------------------------
# The ffmpeg master and its gates


def test_ffmpeg_master_passes_its_gates(make_film):
    d = make_film("ff", mode="draft")
    code, out = filmkit.cli_json(d, "master")
    assert code == 0, out
    assert out["method"] == "ffmpeg" and out["passed"] and out["held"]
    names = {g["gate"]: g["status"] for g in out["gates"]}
    assert names == {"master.frames": "PASS", "psnr": "PASS", "master.psnr_h264": "PASS", "master.finish": "PASS"}
    rec = jsonio.read(d / "masters/master.json")
    assert {m["kind"] for m in rec["masters"]} == {"prores_422_hq", "h264"}
    for m in rec["masters"]:
        assert m["frames"] == 40 and m["publishing"] == "not approved for publishing"
        assert jsonio.sha256(d / m["file"]) == m["sha256"]
    # the ProRes master is the act renders exactly (stream copy), with the mix as 24-bit PCM
    pro = next(m for m in rec["masters"] if m["kind"] == "prores_422_hq")
    assert "stream copy" in pro["made"] and pro["audio"]
    # the gates are recorded for slate qa and deliver, and none is STALE the moment master finishes
    report = gates.load_report(d)
    for name in ("master.frames", "psnr", "master.psnr_h264", "master.finish"):
        assert report[name].status == "PASS", (name, report[name].status, report[name].summary)
    assert jsonio.read(d / "masters/checks.json")["passed"] is True
    # no .part files are left behind
    assert not list((d / "masters").glob(".*part*"))


def test_ffmpeg_master_needs_the_mix(make_film):
    d = make_film("nomix", mode="draft", mix=False)
    code, out, err = filmkit.cli(d, "master")
    assert code == 3 and "no final mix" in err
    code, out, err = filmkit.cli(d, "master", "--silent")
    assert code == 0, err


def test_missing_act_render_is_named(make_film):
    d = make_film("norender", mode="draft")
    reg = jsonio.read(d / "renders/registry.json")
    reg["entries"] = reg["entries"][:1]
    jsonio.write_atomic(d / "renders/registry.json", reg)
    code, out, err = filmkit.cli(d, "master")
    assert code == 3 and "Act 2" in err and "slate render --act 2" in err


def test_interim_master_for_supervised_fails_the_finish_gate(make_film):
    d = make_film("interim")
    code, out = filmkit.cli_json(d, "master", "--ffmpeg")
    assert code == 1
    finish = next(g for g in out["gates"] if g["gate"] == "master.finish")
    assert finish["status"] == "FAIL" and "After Effects" in finish["summary"]


def test_psnr_gate_fails_a_master_that_doesnt_match(make_film, tmp_path):
    d = make_film("psnr", mode="draft")
    film = Film(d)
    renders = [r["path"] for r in film.act_renders()]
    other = tmp_path / "other.mov"
    filmkit.render_act(other, film.total_frames, film.size, film.rate, seed=9, source="testsrc2")
    r = checks.master_psnr(film, other, renders, gate="master.psnr_test")
    assert r.status == "FAIL" and "under 40 dB" in r.summary
    assert r.details["summary"]["below_count"] > 0
    # and the same renders joined pass at the bar
    same = tmp_path / "same.mov"
    lst = tmp_path / "l.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in renders))
    filmkit.ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(same))
    assert checks.master_psnr(film, same, renders, gate="master.psnr_test").status == "PASS"


def test_psnr_threshold_comes_from_the_film(make_film):
    d = make_film("thr", mode="draft")
    fj = json.loads((d / "film.json").read_text())
    fj.setdefault("targets", {})["master_psnr_db"] = 60.0
    (d / "film.json").write_text(json.dumps(fj))
    assert checks.threshold(Film(d)) == 60.0


def test_frame_count_gate_fails_a_short_master(make_film, tmp_path):
    d = make_film("short", mode="draft")
    film = Film(d)
    short = tmp_path / "short.mov"
    filmkit.render_act(short, film.total_frames - 3, film.size, film.rate, seed=1)
    r = checks.master_frames(film, [{"path": short, "file": str(short)}])
    assert r.status == "FAIL" and "37 frames" in r.summary


def test_h264_below_the_bar_fails_master(make_film, fin):
    (fin["data"] / "slate.toml").write_text('[finish.review]\nencoder = "libx264"\n'
                                            '[finish.h264]\nencoder = "libx264"\ncrf = 51\npreset = "ultrafast"\n')
    d = make_film("crf", mode="draft")
    code, out = filmkit.cli_json(d, "master")
    assert code == 1
    h = next(g for g in out["gates"] if g["gate"] == "master.psnr_h264")
    assert h["status"] == "FAIL"


def test_approved_master_is_never_remade_without_force(make_film):
    d = make_film("locked", mode="supervised")
    code, out = filmkit.cli_json(d, "master", "--ffmpeg")
    from slate.state import approvals, lock

    rel = "masters/locked_H264.mp4"
    approvals.record(d, what="final_cut", path=rel, approved_by="Test Person")
    assert lock.is_locked(d, rel)
    before = jsonio.sha256(d / rel)
    code, out, err = filmkit.cli(d, "master", "--ffmpeg")
    assert code == 1 and "locked" in err
    assert jsonio.sha256(d / rel) == before


# ---------------------------------------------------------------------------------------------------------------
# The After Effects finish (fakes: nothing here starts After Effects)


class FakeAE:
    """Stands in for slate.ae: build writes an .aep, audit passes, masters are made the ffmpeg way."""

    def __init__(self, monkeypatch, film_dir: Path):
        self.calls: list[str] = []
        self.dir = film_dir
        self.aep = film_dir / "ae" / f"{film_dir.name}.aep"
        self.open = False
        monkeypatch.setattr(deps, "ae_aep", lambda fd: self.aep if self.aep.is_file() else None)
        monkeypatch.setattr(deps, "ae_build", self.build)
        monkeypatch.setattr(deps, "ae_audit_run", self.audit)
        monkeypatch.setattr(deps, "ae_audit_last", lambda fd, aep: self.results())
        monkeypatch.setattr(deps, "ae_masters", self.masters)
        monkeypatch.setattr(deps, "ae_require_closed", self.closed)

    def results(self):
        return [gates.GateResult.pass_("ae.project.bpc", "16 bpc", engine=gates.engine("fake ae", "0"))]

    def build(self, film_dir):
        self.calls.append("build")
        self.aep.parent.mkdir(parents=True, exist_ok=True)
        self.aep.write_bytes(b"fake aep " + str(len(self.calls)).encode())
        return self.aep

    def audit(self, film_dir, aep):
        self.calls.append("audit")
        return self.results()

    def closed(self, what):
        if self.open:
            raise Busy(f"After Effects is open, and {what} needs it closed.")

    def masters(self, film_dir, *, silent=False):
        self.calls.append("masters")
        film = Film(film_dir)
        names = master.master_paths(film)
        pro, h = film.path(names["prores_422_hq"]), film.path(names["h264"])
        pro.parent.mkdir(parents=True, exist_ok=True)
        master._make_prores(film, film.act_renders(), None if silent else film.mix(), pro)
        master._make_h264(film, pro, h)
        return {"prores_422_hq": str(pro), "h264": str(h)}


def test_ae_finish_two_steps(make_film, monkeypatch):
    d = make_film("aefin", mode="supervised")
    ae = FakeAE(monkeypatch, d)
    code, out = filmkit.cli_json(d, "master", "--go")
    assert code == 0 and out["status"] == "project_built" and ae.calls == ["build", "audit"]
    assert "save your work" in out["next"].lower() and "quit" in out["next"]
    assert jsonio.read(d / "ae/finish.json")["fingerprint"] == jsonio.sha256_obj(Film(d).master_inputs_now())
    # second run: AE is open, so the masters wait (exit 5)
    ae.open = True
    code, out2, err = filmkit.cli(d, "master", "--go")
    assert code == 5 and "open" in err
    ae.open = False
    code, out3 = filmkit.cli_json(d, "master", "--go")
    assert code == 0, out3
    assert ae.calls == ["build", "audit", "masters"] and out3["method"] == "ae" and out3["passed"]
    gate_names = {g["gate"] for g in out3["gates"]}
    assert {"master.frames", "psnr", "master.psnr_h264", "master.finish", "ae.project.bpc"} <= gate_names
    rec = jsonio.read(d / "masters/master.json")
    assert rec["method"] == "ae" and rec["aep"]["file"].endswith(".aep")


def test_ae_rebuilds_when_the_renders_changed(make_film, monkeypatch):
    d = make_film("aestale", mode="supervised")
    ae = FakeAE(monkeypatch, d)
    filmkit.cli_json(d, "master", "--go")
    film = Film(d)
    filmkit.render_act(d / "renders/act2.mov", 15, film.size, film.rate, seed=7)
    reg = jsonio.read(d / "renders/registry.json")
    reg["entries"][1]["sha256"] = jsonio.sha256(d / "renders/act2.mov")
    jsonio.write_atomic(d / "renders/registry.json", reg)
    code, out = filmkit.cli_json(d, "master", "--go")
    assert out["status"] == "project_built" and ae.calls == ["build", "audit", "build", "audit"]


def test_ae_steps_never_reach_real_ae_in_tests(make_film):
    with pytest.raises(RuntimeError, match="real After Effects"):
        deps.ae_build(Path("/nonexistent"))


def test_captions_never_make_a_master_stale(make_film):
    d = make_film("capstale", mode="draft", brand="open-brand")
    filmkit.cli(d, "master")
    (d / "voice/captions/capstale.srt").write_text("1\n00:00:00,000 --> 00:00:00,500\nNew words.\n")
    assert Film(d).master_current() == (True, "")
    filmkit.write_wav24(d / "sound/mix/final_mix.wav", filmkit.np.zeros(76800))
    current, why = Film(d).master_current()
    assert not current and "older" in why
