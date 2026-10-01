"""Masters: the chunk plan and exact frame counts, resume and retries, the conform and the joins (never -shortest),
AE's disk cache off for the run and always restored, the watchdog, and every refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import jsonio, procs
from slate.ae import masters, prefs, project, spec
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError

from ae_helpers import FakeMedia, MockAE, make_film, prefs_bytes


@pytest.fixture
def built(slate_home, live_prefs):
    """A film whose AE project is built (through the mock AE) and queued in 4 chunks."""
    d, media = make_film(slate_home["films"], slate_home["brands"])
    pf = live_prefs(data=prefs_bytes(scripting=b'"0"'))
    project.build(d, go=True, runner=MockAE(pf, media=media), chunks=4)
    s = spec.read_spec(d)
    fake = FakeMedia()
    fake.plan = s["masters"]["chunks"]
    fake.chunk_dir = Path(s["masters"]["dir"])
    fake.names = (s["masters"]["primary_name"], s["masters"]["alpha_name"])
    return {"dir": d, "spec": s, "fake": fake, "prefs": pf, **slate_home}


def _cache(pf: Path) -> str:
    return prefs.value_of(pf.read_bytes(), *prefs.DISK_CACHE)


def test_masters_render_every_chunk_and_join_exactly(built):
    fake = built["fake"]
    res = masters.render(built["dir"], go=True, runner=fake, sleep=lambda s: None)
    assert res["frames"] == 264
    assert [c["frames"] for c in res["chunks"]] == [66, 66, 66, 66]
    assert Path(res["h264"]).name == "neutral_H264.mp4" and Path(res["prores_422_hq"]).name == "neutral_ProRes422HQ.mov"
    assert fake.frames[res["h264"]] == 264 and fake.frames[res["prores_422_hq"]] == 264
    aer = [c for c in fake.calls if Path(c[0]).name == "aerender"]
    assert [c[c.index("-rqindex") + 1] for c in aer] == ["1", "2", "3", "4"]
    assert all(c[c.index("-mem_usage") + 1:c.index("-mem_usage") + 3] == ["25", "50"] for c in aer)
    conforms = [c for c in fake.calls if Path(c[0]).name == "ffmpeg" and "prores_ks" in c and "concat" not in c]
    assert len(conforms) == 4
    for c in conforms:
        assert c[c.index("-profile:v") + 1] == "3" and c[c.index("-pix_fmt") + 1] == "yuv422p10le"
        assert c[c.index("-color_primaries") + 1] == "bt709" and c[c.index("-vendor") + 1] == "apl0"
    joins = [c for c in fake.calls if "concat" in c]
    assert len(joins) == 2
    h264, prores = joins
    assert h264[h264.index("-c:a") + 1] == "aac" and h264[h264.index("-b:a") + 1] == "320k" and "+faststart" in h264
    assert prores[prores.index("-c:a") + 1] == "pcm_s24le" and prores[prores.index("-ar") + 1] == "48000"
    assert all("-shortest" not in c for c in fake.calls)
    # chunks cleaned up, a record written
    assert not any(Path(p).exists() for p in fake.frames if "/chunks/" in p and ("_c" in Path(p).name))
    assert jsonio.read(built["dir"] / "masters" / "masters.json")["frames"] == 264


def test_masters_turn_the_cache_off_and_always_restore_it(built):
    seen = []
    fake = built["fake"]

    def spy(argv, timeout=None, **kw):
        if Path(str(argv[0])).name == "aerender":
            seen.append(_cache(built["prefs"]))
        return fake(argv, timeout=timeout, **kw)

    res = masters.render(built["dir"], go=True, runner=spy, sleep=lambda s: None)
    assert set(seen) == {"0"}
    assert _cache(built["prefs"]) == "1"
    assert res["cache"]["toggled"] is True and res["cache"]["restored"] is True
    # and on failure too
    fake.fail_aerender = {1: 3}
    for f in list(fake.frames):
        Path(f).unlink(missing_ok=True)
    fake.frames.clear()
    with pytest.raises(SlateError, match="failed 3 times"):
        masters.render(built["dir"], go=True, runner=fake, sleep=lambda s: None)
    assert _cache(built["prefs"]) == "1"


def test_masters_resume_skips_finished_chunks(built):
    fake = built["fake"]
    d = fake.chunk_dir
    for c in fake.plan[:2]:
        fake._touch(str(d / f"h264_c{c['index']}.mp4"), c["frames"])
        fake._touch(str(d / f"hq_c{c['index']}.mov"), c["frames"])
    res = masters.render(built["dir"], go=True, runner=fake, sleep=lambda s: None)
    assert [c["resumed"] for c in res["chunks"]] == [True, True, False, False]
    aer = [c for c in fake.calls if Path(c[0]).name == "aerender"]
    assert [c[c.index("-rqindex") + 1] for c in aer] == ["3", "4"]


def test_masters_retry_a_failing_chunk(built):
    fake = built["fake"]
    fake.fail_aerender = {2: 2}
    slept = []
    res = masters.render(built["dir"], go=True, runner=fake, sleep=slept.append)
    assert res["chunks"][1]["tries"] == 3 and slept == [masters.RETRY_SLEEP_S] * 2


def test_masters_catch_a_short_conform(built):
    built["fake"].short_conform = True
    with pytest.raises(SlateError, match="conform came out short"):
        masters.render(built["dir"], go=True, runner=built["fake"], sleep=lambda s: None)


def test_frames_takes_the_first_number():
    def fake(argv, timeout=None, **kw):
        from ae_helpers import completed
        return completed(argv, 0, "1358,\n\n1358\n")

    p = Path(__file__)
    assert masters.frames(p, runner=fake) == 1358
    assert masters.frames(p.with_name("nope.mov"), runner=fake) is None


def test_masters_refusals(built, monkeypatch, slate_home):
    with pytest.raises(Refused, match="go"):
        masters.render(built["dir"], runner=built["fake"])
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    with pytest.raises(Busy) as exc:
        masters.render(built["dir"], go=True, runner=built["fake"])
    assert exc.value.exit_code == 5
    assert "save your work in After Effects and quit it, then run this again" in exc.value.message
    monkeypatch.setattr(procs, "ae_running", lambda: False)
    fj = json.loads((built["dir"] / "film.json").read_text())
    (built["dir"] / "film.json").write_text(json.dumps({**fj, "mode": "autonomous"}))
    with pytest.raises(Refused, match="utonomous"):
        masters.render(built["dir"], go=True, runner=built["fake"])
    (built["dir"] / "film.json").write_text(json.dumps(fj))
    with pytest.raises(Refused, match="queued in 4 chunks"):
        masters.render(built["dir"], go=True, runner=built["fake"], chunks=6)


def test_masters_need_the_mix_unless_silent(built):
    (built["dir"] / "sound" / "mix" / "final_mix.wav").unlink()
    s = spec.read_spec(built["dir"])
    s["audio"] = None
    spec.write_spec(built["dir"], s)
    with pytest.raises(MissingPrerequisite, match="mix"):
        masters.render(built["dir"], go=True, runner=built["fake"])
    res = masters.render(built["dir"], go=True, runner=built["fake"], allow_silent=True, sleep=lambda s: None)
    join = [c for c in built["fake"].calls if "concat" in c][0]
    assert "-c:a" not in join and res["mix"] is None


def test_the_plan_must_tile_the_film():
    masters.check_plan(spec.chunk_plan(264, 4), 264)
    gap = spec.chunk_plan(264, 4)
    gap[1]["start"] += 1
    with pytest.raises(Refused, match="tile"):
        masters.check_plan(gap, 264)
    with pytest.raises(Refused, match="covers"):
        masters.check_plan(spec.chunk_plan(200, 4), 264)


def test_watchdog_stops_only_our_aerender(tmp_path, monkeypatch):
    aep = tmp_path / "film.aep"
    killed = []
    monkeypatch.setattr(procs, "pgrep", lambda pattern, full=False: [111, 222] if pattern == str(aep) else [999])
    wd = masters.Watchdog(tmp_path, aep, min_free_gb=4, free_bytes=lambda p: 3 << 30, killer=killed.append)
    assert wd.check() is True and killed == [111, 222]
    ok = masters.Watchdog(tmp_path, aep, min_free_gb=4, free_bytes=lambda p: 10 << 30, killer=killed.append)
    assert ok.check() is False


def test_masters_stop_when_the_disk_runs_low(built):
    with pytest.raises(SlateError, match="GB free"):
        masters.render(built["dir"], go=True, runner=built["fake"], sleep=lambda s: None,
                       free_bytes=lambda p: 1 << 30)
    assert _cache(built["prefs"]) == "1"


def test_requeue_runs_queue_masters(built):
    d = built["dir"]
    rec = masters.queue(d, go=True, chunks=2, runner=_ReplayBuild(d, built["prefs"]))
    assert [c["frames"] for c in rec["chunks"]] == [132, 132]
    assert [q["frames"] for q in rec["queue"]] == [132, 132]
    assert spec.read_spec(d)["masters"]["chunks"] == rec["chunks"]
    assert jsonio.read(d / "ae" / "masters_queue.json")["aep_sha256"] == jsonio.sha256(Path(rec["aep"]))


class _ReplayBuild:
    """Replays the film's build in the mock session before the requeue (the mock can't reopen a saved AEP)."""

    def __init__(self, film_dir: Path, prefs_file: Path):
        self.film_dir = film_dir
        self.prefs_file = prefs_file
        media = {}
        for p in (film_dir / "renders").glob("*.mov"):
            media[str(p)] = {"width": 1280, "height": 720, "frameRate": 24000 / 1001, "duration": 10, "hasVideo": True}
        mix = film_dir / "sound" / "mix" / "final_mix.wav"
        if mix.is_file():
            media[str(mix)] = {"duration": 12, "hasAudio": True, "hasVideo": False}
        self.ae = MockAE(prefs_file, media=media)
        from slate.ae import driver

        build_wrapper = driver.runs_dir()
        builds = sorted(build_wrapper.glob("*/wrapper.jsx"), key=lambda p: p.stat().st_mtime)
        self.ae.history = [str(b) for b in builds if "build_film.jsx" in b.read_text()]

    def __call__(self, argv, timeout=None, **kw):
        return self.ae(argv, timeout=timeout, **kw)


def test_masters_cap_aerender_cpu_only_when_asked(built):
    """`[ae] masters_cpu_percent` in slate.toml gives every chunk's aerender `-mfr ON <percent>` (Multi-Frame Rendering
    capped at that share of the CPU) so the Mac stays usable; unset, the flag stays off the command line (L-ae-056)."""
    fake = built["fake"]
    masters.render(built["dir"], go=True, runner=fake, sleep=lambda s: None)
    assert not any("-mfr" in c for c in fake.calls if Path(c[0]).name == "aerender")
    (built["data"] / "slate.toml").write_text("[ae]\nmasters_cpu_percent = 60\n", encoding="utf-8")
    for f in list(fake.frames):
        Path(f).unlink(missing_ok=True)
    fake.frames.clear()
    fake.calls.clear()
    masters.render(built["dir"], go=True, runner=fake, sleep=lambda s: None)
    aer = [c for c in fake.calls if Path(c[0]).name == "aerender"]
    assert len(aer) == 4 and all(c[c.index("-mfr"):c.index("-mfr") + 3] == ["-mfr", "ON", "60"] for c in aer)
    assert masters.mfr_args({"ae": {"masters_cpu_percent": 25}}) == ["-mfr", "ON", "25"]
    assert masters.mfr_args({"ae": {}}) == []
    with pytest.raises(Refused, match="whole percent"):
        masters.mfr_args({"ae": {"masters_cpu_percent": 140}})
