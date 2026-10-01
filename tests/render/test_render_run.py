"""Rendering against fakes: chunking, resume, retries, registration and the timeline lock, patches and splices,
draft samples, the refusals, the engines, and bundle cleanup."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from slate import jsonio
from slate.errors import Busy, MissingPrerequisite, Refused
from slate.render import chunks, registry, runner
from slate.render import media as media_mod
from render_fakes import frame_pixels, make_film, write_toml


def _renders(fakes, act=None):
    return [(a["act"], a["from"], a["to"]) for a in fakes.bridge.renders() if act is None or a["act"] == act]


def _chunk_dirs(film_dir):
    root = film_dir / "renders" / "chunks"
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def test_act_render_chunks_joins_registers_and_locks_the_timeline(fakes, film):
    result = runner.run(film, act=1)
    assert _renders(fakes) == [(1, 0, 9), (1, 10, 19), (1, 20, 29)]
    r = result["renders"][0]
    assert r["registered"] and r["frame_count"] == 30 and r["proof"]["status"] == "PASS"
    assert r["proof"]["method"] == "psnr" and fakes.media.encodes == 1  # lossless chunks, one ProRes encode
    # the act's frames, in order, as the engine drew them (within the encode's tiny error)
    out = fakes.media.load(film / "renders" / "act1.mov")
    assert out.shape[0] == 30
    assert np.abs(out[17].astype(int) - frame_pixels(1, 17).astype(int)).max() <= 1
    entry = registry.find(film, 1)
    assert entry["frames"] == [0, 30] and entry["samples"] == 16 and entry["engine"] == "remotion"
    assert entry["bundle_hash"] == fakes.bridge.source_hash(film) and entry["shots"] == "A1S1-A1S2"
    assert entry["sha256"] == r["sha256"] and entry["draft"] is False
    # registering locked the timeline (slate.state reads the registry) and recorded the render's provenance, after
    # the render was in place
    assert r["timeline_locked"] == {"method": "timeline_locked", "files": ["timing/shots.json", "timing/timeline.json"]}
    assert fakes.state.timeline_checks == 1 and fakes.state.locked == []
    assert fakes.state.records[0]["path"] == "renders/act1.mov"
    assert fakes.state.records[0]["producer"] == "slate render --act 1"
    assert fakes.state.records[0]["inputs"] == ["timing/shots.json", "timing/timeline.json"]
    # the render slot was held and the disk reserved first; the chunks and the bundle are gone
    assert fakes.jobs.held[0][0] == "render" and fakes.jobs.reserved and fakes.jobs.reserved[0][1] > 0
    assert _chunk_dirs(film) == []
    # one bundle, shared by every chunk as serve_url, and released through the engine at the end
    assert fakes.bridge.made_bundles() == 1
    assert all(a.get("serve_url") == str(fakes.bridge.bundles[0]) for a in fakes.bridge.renders())
    assert [a["serve_url"] for a in fakes.bridge.releases()] == [str(fakes.bridge.bundles[0])]
    assert not fakes.bridge.bundles[0].exists()
    assert all(a["codec"] == "lossless" and a["out"].endswith(".part.mkv") for a in fakes.bridge.renders())
    assert [g["gate"] for g in result["gates"]] == ["render.act1.frames"]
    assert result["gates"][0]["status"] == "PASS"


def test_whole_film_renders_every_act_from_one_bundle(fakes, film):
    result = runner.run(film, act=None)
    assert [r["act"] for r in result["renders"]] == [1, 2]
    assert [e["frames"] for e in registry.entries(film)] == [[0, 30], [30, 54]]
    assert fakes.bridge.made_bundles() == 1 and len(fakes.bridge.releases()) == 1
    assert _renders(fakes, act=2) == [(2, 0, 9), (2, 10, 19), (2, 20, 23)]


def test_resume_keeps_good_chunks_and_renders_the_rest(fakes, film):
    fakes.bridge.fail[(1, 20)] = 2  # the third chunk fails both tries
    with pytest.raises(Refused, match="failed 2 times"):
        runner.run(film, act=1)
    assert registry.find(film, 1) is None
    [folder] = _chunk_dirs(film)
    files = sorted(p.name for p in (film / "renders" / "chunks" / folder).glob("c*.mkv"))
    # the two good chunks, and the engine's short copy of the crashed one, kept for a look until it's rendered again
    assert files == ["c000_00000-00009.mkv", "c001_00010-00019.mkv", "c002_00020-00029.part.short.mkv"]
    plans = chunks.unfinished(film)
    assert len(plans) == 1 and plans[0]["options"]["act"] == 1
    assert not fakes.bridge.bundles[0].exists() and fakes.bridge.releases()  # released even though it failed

    fakes.bridge.calls.clear()
    result = runner.run(film, act=1)
    assert _renders(fakes) == [(1, 20, 29)]  # only the missing chunk
    assert result["renders"][0]["chunks"] == {"total": 3, "kept": [0, 1], "rendered": [2]}
    assert registry.find(film, 1)["frames"] == [0, 30]
    assert chunks.unfinished(film) == [] and _chunk_dirs(film) == []


def test_resume_rerenders_short_changed_and_unrecorded_chunks(fakes, film):
    fakes.bridge.fail[(1, 20)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=1)
    folder = film / "renders" / "chunks" / _chunk_dirs(film)[0]
    c0, c1 = folder / "c000_00000-00009.mkv", folder / "c001_00010-00019.mkv"
    # chunk 0: short, with a record that matches the short file (only the frame count can catch it)
    short = fakes.media.load(c0)[:9]
    fakes.media.save(c0, short)
    record = json.loads((folder / "c000_00000-00009.mkv.json").read_text())
    record["sha256"] = jsonio.sha256(c0)
    (folder / "c000_00000-00009.mkv.json").write_text(json.dumps(record))
    # chunk 1: its record is gone (a render killed between the rename and the record)
    (folder / "c001_00010-00019.mkv.json").unlink()
    ws = chunks.Workspace(film, runner.prepare(film, act=1)[1].targets[0], record["settings"], 10, ".mkv")
    reasons = [ws.check(c, media_mod.backend())[1] for c in ws.chunks[:2]]
    assert reasons[0] == "short: 9 of 10 frames"
    assert reasons[1].startswith("no record")

    fakes.bridge.calls.clear()
    runner.run(film, act=1)
    assert _renders(fakes) == [(1, 0, 9), (1, 10, 19), (1, 20, 29)]
    assert fakes.media.load(film / "renders" / "act1.mov").shape[0] == 30


def test_a_chunk_changed_on_disk_is_rendered_again(fakes, film):
    fakes.bridge.fail[(1, 20)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=1)
    folder = film / "renders" / "chunks" / _chunk_dirs(film)[0]
    c1 = folder / "c001_00010-00019.mkv"
    arr = fakes.media.load(c1)
    arr[3] = 0  # same frame count, different pixels
    fakes.media.save(c1, arr)
    fakes.bridge.calls.clear()
    runner.run(film, act=1)
    assert _renders(fakes) == [(1, 10, 19), (1, 20, 29)]


def test_chunks_from_other_settings_are_never_reused(fakes, film):
    fakes.bridge.fail[(1, 20)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=1, samples=4)
    old = _chunk_dirs(film)
    fakes.bridge.calls.clear()
    runner.run(film, act=1, samples=8)
    assert _renders(fakes) == [(1, 0, 9), (1, 10, 19), (1, 20, 29)]
    assert all(a["sub"] == 8 for a in fakes.bridge.renders())
    assert _chunk_dirs(film) == [] and old  # the 4-sample chunks were cleared, not left to rot


def test_changed_sources_invalidate_chunks(fakes, film):
    fakes.bridge.fail[(1, 20)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=1)
    (film / "scenes" / "act1" / "index.tsx").write_text("export const Act1 = () => 'changed';\n")
    fakes.bridge.calls.clear()
    runner.run(film, act=1)
    assert _renders(fakes) == [(1, 0, 9), (1, 10, 19), (1, 20, 29)]


def test_a_new_engine_bundle_hash_invalidates_chunks(fakes, film):
    """The film didn't change but the kit did (other code in the bundle): the engine's hash says so."""
    fakes.bridge.fail[(1, 20)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=1)
    fakes.bridge.bundle_hash = "bundle-after-a-kit-change"
    fakes.bridge.calls.clear()
    runner.run(film, act=1)
    assert _renders(fakes) == [(1, 0, 9), (1, 10, 19), (1, 20, 29)]


def test_a_chunk_that_crashes_once_is_tried_again(fakes, film):
    fakes.bridge.fail[(1, 10)] = 1
    runner.run(film, act=1)
    assert _renders(fakes) == [(1, 0, 9), (1, 10, 19), (1, 10, 19), (1, 20, 29)]
    assert registry.find(film, 1) is not None


def test_a_short_chunk_is_refused_and_nothing_is_registered(fakes, film):
    fakes.bridge.short.add((1, 10))
    with pytest.raises(Refused, match="came out 9 frames, not 10"):
        runner.run(film, act=1)
    assert registry.find(film, 1) is None
    assert fakes.state.records == [] and fakes.state.timeline_checks == 0
    assert not (film / "renders" / "act1.mov").exists()


def test_the_old_render_stays_until_the_new_one_is_proven(fakes, film, monkeypatch):
    runner.run(film, act=1)
    before = (film / "renders" / "act1.mov").read_bytes()
    from slate.render import joins

    real = joins.assemble

    def broken(chunk_list, out, *, codec, media):
        proof = real(chunk_list, out, codec=codec, media=media)
        proof["problems"].append("a join failed its proof")
        proof["status"] = "FAIL"
        return proof

    monkeypatch.setattr(joins, "assemble", broken)
    with pytest.raises(Refused, match="didn't prove itself"):
        runner.run(film, act=1, samples=4)
    assert (film / "renders" / "act1.mov").read_bytes() == before
    assert registry.find(film, 1)["samples"] == 16
    assert not (film / "renders" / "act1.new.mov").exists()


def test_draft_film_renders_with_four_samples(fakes, slate_home):
    film_dir = make_film(slate_home["films"], film_id="draft-film", mode="draft")
    runner.run(film_dir, act=1)
    assert {a["sub"] for a in fakes.bridge.renders()} == {4}
    entry = registry.find(film_dir, 1)
    assert entry["samples"] == 4 and entry["draft"] is True


def test_draft_flag_renders_with_the_configured_draft_samples(fakes, film, slate_home):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 10\ndraft_samples = 3\n")
    runner.run(film, act=2, draft=True)
    assert {a["sub"] for a in fakes.bridge.renders()} == {3}


def test_patch_is_rendered_and_spliced_with_proof(fakes, film):
    runner.run(film, act=1)
    old = registry.find(film, 1)
    before = fakes.media.load(film / "renders" / "act1.mov")
    fakes.bridge.variant[1] = {f: 1 for f in range(13, 17)}  # the fix: frames 13-16 change
    fakes.bridge.calls.clear()
    result = runner.run(film, act=1, frames=(12, 17), engine=None)
    assert _renders(fakes) == [(1, 12, 17)]
    r = result["renders"][0]
    assert r["kind"] == "patch" and r["splice"]["status"] == "PASS"
    assert [j["frame"] for j in r["splice"]["joins"]] == [12, 17, 11, 18]
    assert all(j["match"] for j in r["splice"]["joins"])
    new = registry.find(film, 1)
    assert new["sha256"] == r["sha256"] != old["sha256"]
    assert new["history"][-1]["what"] == "splice" and new["history"][-1]["frames"] == [12, 17]
    after = fakes.media.load(film / "renders" / "act1.mov")
    assert after.shape == before.shape
    # outside the patch, the act is the same bit for bit (a stream copy); inside, the fix is there
    assert np.array_equal(after[:12], before[:12]) and np.array_equal(after[18:], before[18:])
    assert np.abs(after[14].astype(int) - frame_pixels(1, 14, 1).astype(int)).max() <= 1
    assert not np.array_equal(after[14], before[14])
    assert not (film / "renders" / "patches" / "act1_00012-00017.mov").exists()
    assert [g["gate"] for g in result["gates"]] == ["render.act1.frames", "render.splice"]


def test_misnumbered_patch_is_refused_and_nothing_changes(fakes, film):
    runner.run(film, act=1)
    before = (film / "renders" / "act1.mov").read_bytes()
    fakes.bridge.shift[1] = 1  # the engine draws each frame one later than asked: a patch one frame off
    with pytest.raises(Refused, match=r"looks \+1 frame\(s\) off"):
        runner.run(film, act=1, frames=(12, 17), engine=None)
    assert (film / "renders" / "act1.mov").read_bytes() == before
    assert registry.find(film, 1)["history"][-1]["what"] == "render"


def test_a_patch_needs_a_registered_render(fakes, film):
    with pytest.raises(Refused, match="no registered render to patch"):
        runner.run(film, act=1, frames=(3, 5))
    assert fakes.bridge.renders() == []


def test_a_patch_matches_the_registered_samples(fakes, film):
    runner.run(film, act=1, samples=4)
    fakes.bridge.calls.clear()
    runner.run(film, act=1, frames=(2, 4), engine=None)  # no --samples: the patch matches the act
    assert {a["sub"] for a in fakes.bridge.renders()} == {4}
    with pytest.raises(Refused, match="seam"):
        runner.run(film, act=1, frames=(2, 4), engine=None, samples=16)


def test_a_patch_after_the_timing_moved_is_refused(fakes, film):
    runner.run(film, act=1)
    shots_path = film / "timing" / "shots.json"
    shots = json.loads(shots_path.read_text())
    shots["shots"][1]["frame_out"] += 1
    for s in shots["shots"][2:]:
        s["frame_in"] += 1
        s["frame_out"] += 1
    shots["total_frames"] += 1
    shots_path.write_text(json.dumps(shots))
    with pytest.raises(Refused, match="moved since it was rendered"):
        runner.run(film, act=1, frames=(2, 4), engine=None)


def test_range_to_a_file_is_not_registered(fakes, film, tmp_path):
    out = tmp_path / "review" / "range.mov"
    result = runner.run(film, act=2, frames=(3, 8), out=out)
    assert result["renders"][0]["kind"] == "range"
    assert fakes.media.load(out).shape[0] == 6
    assert registry.entries(film) == [] and fakes.state.records == [] and fakes.state.timeline_checks == 0


def test_render_refuses_before_starting_without_the_state_module(fakes, film, monkeypatch):
    fakes.without(monkeypatch, "slate.state.lock")
    with pytest.raises(MissingPrerequisite, match="slate.state.lock"):
        runner.run(film, act=1)
    assert fakes.bridge.calls == []


def test_disk_reservation_refusal_stops_the_render_before_it_starts(fakes, film):
    fakes.jobs.refuse_disk = Refused("Not enough disk for this render.")
    with pytest.raises(Refused, match="Not enough disk"):
        runner.run(film, act=1)
    assert fakes.bridge.calls == []


def test_disk_fallback_refuses_when_the_queue_isnt_installed(fakes, film, monkeypatch, slate_home):
    fakes.without(monkeypatch, "slate.jobs.disk", "slate.jobs.locks")
    write_toml(slate_home["data"], "[render]\nchunk_frames = 10\n[jobs]\ndisk_reserve_gb = 1e9\n")
    with pytest.raises(Refused, match="Not enough disk"):
        runner.run(film, act=1)
    assert fakes.bridge.calls == []


def test_a_busy_render_slot_is_exit_five(fakes, film):
    fakes.jobs.busy = True
    with pytest.raises(Busy):
        runner.run(film, act=1)
    assert fakes.bridge.calls == []


def test_in_queue_leaves_the_slot_and_disk_to_the_queue(fakes, film, monkeypatch):
    monkeypatch.setenv("SLATE_JOB_ID", "job-7")  # the queue's supervisor holds both for the job
    runner.run(film, act=1, in_queue=True)
    assert fakes.jobs.held == [] and fakes.jobs.reserved == []
    assert registry.find(film, 1) is not None


def test_in_queue_run_by_hand_still_takes_the_slot_and_the_disk(fakes, film, monkeypatch):
    monkeypatch.delenv("SLATE_JOB_ID", raising=False)
    runner.run(film, act=1, in_queue=True)
    assert fakes.jobs.held and fakes.jobs.reserved


def test_a_registry_failure_puts_the_previous_render_back(fakes, film, monkeypatch):
    runner.run(film, act=1)
    before = (film / "renders" / "act1.mov").read_bytes()
    from slate.errors import UsageError

    def broken(*_a, **_k):
        raise UsageError("the registry couldn't be written")

    monkeypatch.setattr(registry, "write_entry", broken)
    with pytest.raises(UsageError):
        runner.run(film, act=1, samples=4)
    assert (film / "renders" / "act1.mov").read_bytes() == before
    assert not list((film / "renders").glob("act1.*.mov"))


def test_a_state_hiccup_after_registering_is_a_warning_not_a_lost_render(fakes, film, monkeypatch):
    import sys

    def broken(*_a, **_k):
        raise Refused("film.lock.json is being rewritten")

    monkeypatch.setattr(sys.modules["slate.state.lock"], "record", broken)
    result = runner.run(film, act=1)
    r = result["renders"][0]
    assert r["registered"] and r["timeline_locked"]["warning"] == "film.lock.json is being rewritten"
    assert registry.find(film, 1)["sha256"] == r["sha256"]


def test_resume_with_nothing_unfinished_is_refused(fakes, film):
    with pytest.raises(Refused, match="Nothing to resume"):
        runner.run(film, resume=True)


def test_resume_uses_the_options_the_render_started_with(fakes, film):
    fakes.bridge.fail[(2, 10)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=2, samples=6)
    fakes.bridge.calls.clear()
    result = runner.run(film, resume=True)
    assert result["resumed"] == 1
    assert _renders(fakes) == [(2, 10, 19), (2, 20, 23)]
    assert {a["sub"] for a in fakes.bridge.renders()} == {6}
    assert registry.find(film, 2)["samples"] == 6


def test_web_engine_renders_through_web_render_without_a_bundle(fakes, film):
    runner.run(film, act=2, engine="web")
    commands = [c for c, _a in fakes.bridge.calls]
    assert "bundle" not in commands and commands.count("web-render") == 3
    assert registry.find(film, 2)["engine"] == "web"


def test_stills_use_one_bundle_and_delete_it(fakes, film):
    from slate.render import stills

    result = stills.stills(film, frames=[0, 35, 53])
    assert [(s["act"], s["frame"], s["film_frame"]) for s in result["stills"]] == [(1, 0, 0), (2, 5, 35), (2, 23, 53)]
    assert all(Path(s["file"]).is_file() for s in result["stills"])
    specs = [a["specs"] for c, a in fakes.bridge.calls if c == "stills"]
    assert specs == [["Act1:0"], ["Act2:5", "Act2:23"]]
    assert all(a["scale"] == 0.5 and a["sub"] == 1 for c, a in fakes.bridge.calls if c == "stills")
    assert fakes.bridge.made_bundles() == 1 and not fakes.bridge.bundles[0].exists()


def test_a_bundle_the_engine_cant_release_is_removed_directly(fakes, film):
    fakes.bridge.fail_release = True
    runner.run(film, act=2)
    assert not fakes.bridge.bundles[0].exists()


def test_a_bundle_another_process_leases_is_never_removed(fakes, film):
    import os

    fakes.bridge.fail_release = True
    real_call = fakes.bridge.call

    def call(command, args, *, timeout=None):
        result = real_call(command, args, timeout=timeout)
        if command == "bundle" and not args.get("release"):
            leases = Path(result["serve_url"]) / ".leases"
            leases.mkdir()
            (leases / str(os.getppid())).write_text("{}")  # a live process that isn't us
        return result

    import sys

    sys.modules["slate.engine.bridge"].call = call
    runner.run(film, act=2)
    assert fakes.bridge.bundles[0].exists()


# ---- After Effects ---------------------------------------------------------------------------------------------

@pytest.fixture
def ae_ready(monkeypatch):
    from slate import procs

    monkeypatch.setattr(procs, "ae_running", lambda: False)
    monkeypatch.setattr(procs, "aerender_running", lambda: False)
    real_which = procs.which
    monkeypatch.setattr(procs, "which", lambda tool: "/Applications/AE/aerender" if tool == "aerender"
                        else real_which(tool))
    return procs


def test_ae_needs_a_persons_go(fakes, film, ae_ready):
    with pytest.raises(Refused, match="person's go"):
        runner.run(film, act=1, engine="ae")
    assert fakes.ae.renders == [] and fakes.ae.lock_events == []


def test_ae_is_refused_for_autonomous_films_even_with_go(fakes, slate_home, ae_ready):
    film_dir = make_film(slate_home["films"], film_id="auto-film", mode="autonomous")
    with pytest.raises(Refused, match="autonomous films never touch After Effects"):
        runner.run(film_dir, act=1, engine="ae", go=True)
    assert fakes.ae.renders == []


def test_ae_open_is_busy_and_slate_never_quits_it(fakes, film, ae_ready, monkeypatch):
    monkeypatch.setattr(ae_ready, "ae_running", lambda: True)
    with pytest.raises(Busy, match="After Effects is open") as info:
        runner.run(film, act=1, engine="ae", go=True)
    assert "quit After Effects yourself" in info.value.hint and "never quits" in info.value.hint
    assert fakes.ae.renders == [] and fakes.ae.lock_events == []


def test_ae_missing_aerender_is_a_missing_prerequisite(fakes, film, monkeypatch):
    from slate import procs

    monkeypatch.setattr(procs, "which", lambda tool: None)
    with pytest.raises(MissingPrerequisite, match="aerender"):
        runner.run(film, act=1, engine="ae", go=True)
    assert fakes.ae.renders == []


def test_ae_renders_whole_acts_through_slate_ae_acts(fakes, film, ae_ready):
    result = runner.run(film, act=1, engine="ae", go=True)
    assert fakes.ae.renders == [{"act": 1, "go": True, "out": None, "register": True}]
    assert fakes.ae.lock_events == ["enter", "exit"]  # slate.ae took its own AE lock
    assert fakes.bridge.calls == []  # no Node engine, no chunks, no bundle
    r = result["renders"][0]
    assert r["registered"] and r["frame_count"] == 30 and r["proof"]["method"] == "ae frame count"
    assert r["timeline_locked"]["method"] == "timeline_locked"
    entry = registry.find(film, 1)
    assert entry["engine"] == "ae" and entry["file"] == "renders/ae/act1.mov" and entry["samples"] == 16
    assert entry["bundle_hash"].startswith("aep:")
    assert fakes.jobs.held and fakes.jobs.reserved  # the render slot and the disk, as for any render
    assert result["gates"][0]["engine"]["name"] == "aerender"


def test_ae_render_is_registered_even_if_slate_ae_couldnt(fakes, film, ae_ready):
    fakes.ae.register_itself = False
    result = runner.run(film, act=2, engine="ae", go=True)
    assert result["renders"][0]["registered"] and registry.find(film, 2)["engine"] == "ae"


def test_ae_renders_whole_acts_only(fakes, film, ae_ready):
    from slate.errors import UsageError

    with pytest.raises(UsageError, match="whole acts"):
        runner.run(film, act=1, frames=(3, 5), engine="ae", go=True)
    assert fakes.ae.renders == []


def test_an_ae_act_renderer_with_another_signature_is_a_missing_prerequisite(fakes, film, ae_ready, monkeypatch):
    import sys

    monkeypatch.setattr(sys.modules["slate.ae.acts"], "render", lambda film_dir, *, method="aerender": {})
    with pytest.raises(MissingPrerequisite, match="doesn't take"):
        runner.run(film, act=1, engine="ae", go=True)
    assert registry.find(film, 1) is None


# ---- review additions: the motion trace, the engine's own gates, web stills ------------------------------------------

def test_a_remotion_render_refreshes_the_acts_motion_trace_and_records_it(fakes, film):
    result = runner.run(film, act=1)
    assert [c for c, _a in fakes.bridge.calls if c == "trace"] == ["trace"]
    [trace_args] = [a for c, a in fakes.bridge.calls if c == "trace"]
    assert trace_args == {"film": str(film), "act": 1}
    assert result["renders"][0]["trace"] == {"written": True, "file": "timing/motion/act1.json", "entries": 0}
    row = next(r for r in fakes.state.records if r["path"] == "timing/motion/act1.json")
    assert row["producer"] == "slate render --act 1"
    assert row["inputs"] == ["scenes/act1/index.tsx", "timing/shots.json"]


def test_a_trace_that_cant_be_written_is_reported_and_the_render_still_stands(fakes, film):
    fakes.bridge.fail_trace = True
    result = runner.run(film, act=1)
    r = result["renders"][0]
    assert r["registered"] is True and registry.find(film, 1) is not None
    assert r["trace"]["written"] is False and "throws while loading" in r["trace"]["reason"]


def test_the_web_engine_writes_its_trace_while_it_renders_and_it_is_recorded(fakes, film):
    result = runner.run(film, act=2, engine="web")
    assert "trace" not in [c for c, _a in fakes.bridge.calls]  # nothing to refresh: the renderer wrote it
    assert result["renders"][0]["trace"] == {"written": True, "file": "timing/motion/act2.json", "entries": 0}
    row = next(r for r in fakes.state.records if r["path"] == "timing/motion/act2.json")
    assert row["producer"] == "slate render --act 2" and "timing/shots.json" in row["inputs"]


def test_the_engines_own_gates_come_back_as_render_gates_worst_across_chunks(fakes, film):
    def gate(name, status):
        return {"gate": name, "status": status, "summary": f"{name} {status}", "evidence": [], "engine": {"name": "kit"},
                "inputs": {}, "checked_at": "2026-09-30T00:00:00+00:00", "details": {}}

    fakes.bridge.gates[(1, 0)] = [gate("fonts", "PASS")]
    fakes.bridge.gates[(1, 10)] = [gate("fonts", "REVIEW"), gate("web.network", "PASS")]
    result = runner.run(film, act=1)
    by_name = {g["gate"]: g for g in result["gates"]}
    assert list(by_name) == ["render.act1.frames", "render.act1.fonts", "render.act1.web.network"]
    assert by_name["render.act1.fonts"]["status"] == "REVIEW"  # the worst chunk wins
    assert by_name["render.act1.fonts"]["details"] == {"stage": "render", "chunk": 1}


def test_web_stills_render_one_frame_each_through_web_render(fakes, film):
    from slate.render import stills

    result = stills.stills(film, frames=[3, 40], engine="web", scale=1, samples=2)
    assert [(s["act"], s["frame"]) for s in result["stills"]] == [(1, 3), (2, 10)]
    calls = [a for c, a in fakes.bridge.calls if c == "web-render"]
    assert [(a["act"], a["from"], a["to"], a["sub"]) for a in calls] == [(1, 3, 3, 2), (2, 10, 10, 2)]
    assert all(a["out"].endswith(".png") for a in calls)
    assert "bundle" not in [c for c, _a in fakes.bridge.calls]


def test_ae_stills_are_refused_with_the_way_to_get_them(fakes, film):
    from slate.render import stills

    with pytest.raises(Refused, match="whole acts") as err:
        stills.stills(film, frames=[3], engine="ae")
    assert "slate render --engine ae --act N --go" in err.value.hint


def test_a_film_renders_gates_are_kept_in_qa_gates_json_and_go_stale_with_the_render(fakes, film):
    """`slate qa`, `slate status` and deliver read qa/gates.json: the frame count and the engine's own gates (the kit's
    font gate, the web renderer's network wall) land there, each tied to the render file it judged."""
    from slate import gates

    def gate(name, status):
        return {"gate": name, "status": status, "summary": f"{name} {status}", "evidence": [], "engine": {"name": "kit"},
                "inputs": {}, "checked_at": "2026-09-30T00:00:00+00:00", "details": {}}

    fakes.bridge.gates[(1, 0)] = [gate("fonts", "PASS")]
    fakes.bridge.gates[(2, 0)] = [gate("web.network", "PASS"), gate("fonts", "PASS")]
    runner.run(film, act=1)
    runner.run(film, act=2, engine="web")
    report = gates.load_report(film)
    for name in ("render.act1.frames", "render.act1.fonts", "render.act2.frames", "render.act2.web.network"):
        assert report[name].status == "PASS", name
        assert report[name].details["stage"] == "render"
    entry = registry.find(film, 1)
    assert report["render.act1.fonts"].inputs == {entry["file"]: entry["sha256"]}
    # a render that changes afterwards turns its gates STALE
    (film / entry["file"]).write_bytes(b"something else")
    assert gates.load_report(film)["render.act1.fonts"].status == "STALE"


def test_a_range_to_a_file_of_your_choosing_keeps_its_gates_out_of_the_films_report(fakes, film, tmp_path):
    runner.run(film, act=1, out=tmp_path / "look.mov")
    assert not (film / "qa" / "gates.json").exists()


def test_a_social_cut_down_renders_fast_but_isnt_a_draft(fakes, slate_home):
    """formats' social route (a derived film made for a social channel) renders at render.draft_samples, so a cut-down
    takes minutes; the hero route and films that don't say keep the delivery bar."""
    film_dir = make_film(slate_home["films"], film_id="social-cut")
    film_json = json.loads((film_dir / "film.json").read_text())
    film_json["derivation"] = {"route": {"route": "social", "channel": "reels"}}
    (film_dir / "film.json").write_text(json.dumps(film_json))
    runner.run(film_dir, act=1)
    assert {a["sub"] for a in fakes.bridge.renders()} == {4}
    entry = registry.find(film_dir, 1)
    assert entry["samples"] == 4 and entry["draft"] is False


def test_a_try_later_goes_back_to_the_queue_whole_instead_of_being_retried_here(fakes, film, monkeypatch):
    from slate import errors

    try_later = getattr(errors, "TryLater", None)
    if try_later is None:
        pytest.skip("slate.errors has no TryLater yet (the foundation's)")
    calls = []
    real = fakes.bridge.call

    def limited(command, args, *, timeout=None):
        if command == "render":
            calls.append(args)
            raise try_later("rate limited")
        return real(command, args, timeout=timeout)

    monkeypatch.setattr(sys.modules["slate.engine.bridge"], "call", limited)
    with pytest.raises(try_later):
        runner.run(film, act=1)
    assert len(calls) == 1  # not tried again in this process; the queue retries the job after its backoff
