"""`slate render` through the real dispatcher: help, --json, exit codes, the queue, and every stage."""

import json
import sys

import numpy as np
import pytest

from slate.cli import run_cli
from slate.errors import Refused
from slate.render import registry, runner
from render_fakes import frame_pixels, make_film, write_toml


def cli(*argv):
    code, out, err = run_cli(list(argv))
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


@pytest.mark.parametrize("argv", [["render", "--help"], ["render", "list", "--help"], ["render", "splice", "--help"],
                                  ["render", "clean", "--help"], ["render", "stills", "--help"]])
def test_help(argv):
    code, out, _err = run_cli(argv)
    assert code == 0


def test_help_names_the_contract():
    _code, out, _err = run_cli(["render", "--help"])
    for flag in ("--act", "--from", "--to", "--engine", "--samples", "--sub", "--detach", "--resume", "--go"):
        assert flag in out
    assert "--in-queue" not in out  # the queue's own flag stays out of people's way


def test_dry_run_plans_without_rendering(fakes, film):
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--dry-run", "--json")
    assert code == 0 and out["dry_run"] is True
    plan = out["plan"]
    assert plan["samples"] == 16 and plan["chunks"]["act1:0-29"] == [[0, 9], [10, 19], [20, 29]]
    assert plan["disk_bytes"] > 0 and plan["reserve_gb"] >= 0.1 and plan["width"] == 64 and plan["fps_num"] == 25
    assert fakes.bridge.calls == [] and fakes.jobs.submitted == []


def test_foreground_render_json(fakes, film, slate_home):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    code, out, err = cli("render", "--film", str(film), "--act", "2", "--json")
    assert code == 0, err
    assert out["ran"] == "here" and out["renders"][0]["registered"] is True
    assert out["gates"][0]["status"] == "PASS"
    assert registry.find(film, 2)["frames"] == [30, 54]


def test_human_output(fakes, film, slate_home):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    code, out, _err = cli("render", "--film", str(film), "--act", "2")
    assert code == 0
    assert "act 2 (A2S1-A2S2): 24 frames joined from 1 chunk(s)" in out
    assert "timeline locked (timing/shots.json, timing/timeline.json)" in out


def test_detach_submits_a_render_job_and_returns_its_id(fakes, film):
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--detach", "--json")
    assert code == 0 and out["ran"] == "queue" and out["job_id"] == "job-1"
    [job] = fakes.jobs.submitted
    assert job["kind"] == "render" and job["resources"] == ("render",) and job["priority"] == "supervised"
    assert job["film"] == str(film.resolve()) and job["disk_gb"] > 0
    argv = job["argv"]
    assert argv[:2] == ["slate", "render"] and job["via"] == "detach_current"  # the queue's --detach convention
    assert "--in-queue" in argv and argv[argv.index("--act") + 1] == "1"
    assert argv[argv.index("--samples") + 1] == "16" and argv[argv.index("--engine") + 1] == "remotion"
    assert fakes.bridge.calls == []  # nothing rendered in the foreground


def test_long_renders_always_go_through_the_queue(fakes, film, slate_home):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 50\n")
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--json")
    assert code == 0 and out["ran"] == "queue" and "always go through the queue" in out["why"]
    assert len(fakes.jobs.submitted) == 1 and fakes.bridge.calls == []


def test_autonomous_films_queue_behind_supervised_ones(fakes, slate_home):
    film = make_film(slate_home["films"], film_id="auto", mode="autonomous")
    code, _out, _err = cli("render", "--film", str(film), "--act", "1", "--detach", "--json")
    assert code == 0 and fakes.jobs.submitted[0]["priority"] == "autonomous"


def test_the_queued_job_renders_in_its_own_process(fakes, film, monkeypatch):
    code, _out, _err = cli("render", "--film", str(film), "--act", "1", "--detach", "--json")
    monkeypatch.setenv("SLATE_JOB_ID", "job-1")  # what the queue's supervisor sets for the job's command
    argv = fakes.jobs.submitted[0]["argv"][1:]  # what the queue's worker runs, minus the leading `slate`
    code, out, err = cli(*argv)
    assert code == 0, err
    assert out["ran"] == "queue" and registry.find(film, 1) is not None
    assert fakes.jobs.held == [] and fakes.jobs.reserved == []  # the queue holds the slot and the disk


def test_wait_reports_how_the_job_ended(fakes, film):
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--detach", "--wait", "--json")
    assert code == 0 and out["status"]["state"] == "done"


def test_disk_refusal_is_exit_one(fakes, film, slate_home):
    from slate.errors import Refused

    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    fakes.jobs.refuse_disk = Refused("Not enough disk for this render.")
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--json")
    assert code == 1 and out["error"] == "refused" and "disk" in out["message"]
    assert fakes.bridge.renders() == []


def test_draft_renders_with_four_samples(fakes, film, slate_home):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--draft", "--json")
    assert code == 0 and out["samples"] == 4 and out["draft"] is True
    assert {a["sub"] for a in fakes.bridge.renders()} == {4}


@pytest.mark.parametrize("argv,code", [
    (["--samples", "4", "--sub", "8"], 2),
    (["--from", "3"], 2),
    (["--act", "9"], 2),
    (["--engine", "ae", "--samples", "4"], 2),
])
def test_usage_errors_are_exit_two(fakes, film, argv, code):
    got, _out, _err = cli("render", "--film", str(film), *argv, "--json")
    assert got == code


def test_sub_is_the_same_as_samples(fakes, film):
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--sub", "8", "--dry-run", "--json")
    assert code == 0 and out["plan"]["samples"] == 8


@pytest.fixture
def ae_ready(monkeypatch):
    from slate import procs

    monkeypatch.setattr(procs, "ae_running", lambda: False)
    monkeypatch.setattr(procs, "aerender_running", lambda: False)
    real = procs.which
    monkeypatch.setattr(procs, "which", lambda t: "/Applications/AE/aerender" if t == "aerender" else real(t))
    return procs


def test_ae_refusals_and_their_exit_codes(fakes, film, slate_home, ae_ready, monkeypatch):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--engine", "ae", "--json")
    assert code == 1 and "person's go" in out["message"]
    auto = make_film(slate_home["films"], film_id="auto", mode="autonomous")
    code, out, _err = cli("render", "--film", str(auto), "--act", "1", "--engine", "ae", "--go", "--json")
    assert code == 1 and "autonomous" in out["message"]
    monkeypatch.setattr(ae_ready, "ae_running", lambda: True)
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--engine", "ae", "--go", "--json")
    assert code == 5 and out["error"] == "busy"
    monkeypatch.setattr(ae_ready, "ae_running", lambda: False)
    monkeypatch.setattr(ae_ready, "which", lambda t: None)
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--engine", "ae", "--go", "--json")
    assert code == 3
    assert fakes.ae.renders == [] and fakes.jobs.submitted == []


def test_ae_without_go_is_refused_before_it_is_queued(fakes, film, ae_ready):
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--engine", "ae", "--detach", "--json")
    assert code == 1 and fakes.jobs.submitted == []
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--engine", "ae", "--go", "--detach", "--json")
    assert code == 0 and "--go" in fakes.jobs.submitted[0]["argv"]
    assert fakes.jobs.submitted[0]["resources"] == ("render", "ae")


def test_missing_engine_bridge_is_exit_three(fakes, film, monkeypatch, slate_home):
    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    fakes.without(monkeypatch, "slate.engine.bridge")
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--json")
    assert code == 3 and "slate.engine.bridge" in out["message"]


def test_missing_queue_for_a_long_render_is_exit_three(fakes, film, monkeypatch):
    fakes.without(monkeypatch, "slate.jobs.client")
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--detach", "--json")
    assert code == 3 and "job queue" in out["message"]


def test_list_shows_rendered_and_not_rendered(fakes, film):
    runner.run(film, act=1)
    code, out, _err = cli("render", "list", "--film", str(film), "--json")
    assert code == 0
    assert [(r["act"], r["state"]) for r in out["acts"]] == [(1, "rendered"), (2, "not rendered")]
    code, text, _err = cli("render", "list", "--film", str(film))
    assert "act 1" in text and "rendered" in text and "not rendered" in text


def test_list_shows_unfinished_renders(fakes, film):
    fakes.bridge.fail[(2, 10)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=2)
    code, out, _err = cli("render", "list", "--film", str(film), "--json")
    assert out["unfinished"] and out["unfinished"][0]["act"] == 2


def test_resume_through_the_cli(fakes, film, slate_home, monkeypatch):
    fakes.bridge.fail[(2, 10)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=2, samples=3)
    fakes.bridge.calls.clear()
    code, out, err = cli("render", "--film", str(film), "--resume", "--json")
    assert code == 0, err
    assert out["ran"] == "here" and registry.find(film, 2)["samples"] == 3
    # a long one goes through the queue, with --resume in the job
    fakes.bridge.fail[(1, 10)] = 2
    with pytest.raises(Refused):
        runner.run(film, act=1, samples=3)
    write_toml(slate_home["data"], "[render]\nchunk_frames = 10\nforeground_max_subframes = 10\n")
    code, out, err = cli("render", "--film", str(film), "--resume", "--json")
    assert code == 0, err
    assert out["ran"] == "queue" and "--resume" in fakes.jobs.submitted[-1]["argv"]
    monkeypatch.setenv("SLATE_JOB_ID", "job-2")
    code, out, err = cli(*fakes.jobs.submitted[-1]["argv"][1:])
    assert code == 0, err
    assert registry.find(film, 1)["samples"] == 3
    code, out, _err = cli("render", "--film", str(film), "--resume", "--json")
    assert code == 1 and "Nothing to resume" in out["message"]


def test_splice_stage(fakes, film):
    runner.run(film, act=1)
    patch = film / "renders" / "patches" / "mine.mov"
    fakes.media.save(patch, np.stack([frame_pixels(1, f, 1) for f in range(4, 9)]))
    code, out, err = cli("render", "splice", str(patch), "--act", "1", "--from", "4", "--to", "8",
                         "--film", str(film), "--json")
    assert code == 0, err
    assert out["status"] == "PASS" and registry.find(film, 1)["history"][-1]["what"] == "splice"
    code, out, _err = cli("render", "splice", str(patch), "--from", "4", "--to", "8", "--film", str(film), "--json")
    assert code == 2  # say where it goes


def test_splice_stage_refuses_a_short_patch(fakes, film):
    runner.run(film, act=1)
    patch = film / "renders" / "patches" / "short.mov"
    fakes.media.save(patch, np.stack([frame_pixels(1, f) for f in range(4, 8)]))
    code, out, _err = cli("render", "splice", str(patch), "--act", "1", "--from", "4", "--to", "8",
                          "--film", str(film), "--json")
    assert code == 1 and "need 5" in out["message"]


def test_clean_stage(fakes, film):
    (film / "renders").mkdir(exist_ok=True)
    (film / "renders" / "act1.new.mov").write_bytes(b"x" * 100)
    code, out, _err = cli("render", "clean", "--film", str(film), "--dry-run", "--json")
    assert code == 0 and out["dry_run"] is True and (film / "renders" / "act1.new.mov").exists()
    assert out["items"][0]["action"] == "would be removed"
    code, out, _err = cli("render", "clean", "--film", str(film), "--json")
    assert code == 0 and not (film / "renders" / "act1.new.mov").exists() and out["freed_bytes"] == 100


def test_clean_waits_for_a_running_render(fakes, film):
    fakes.jobs.busy = True
    code, out, _err = cli("render", "clean", "--film", str(film), "--json")
    assert code == 0 and out["skipped"] is True


def test_stills_stage(fakes, film):
    code, out, _err = cli("render", "stills", "--film", str(film), "--frames", "0,35", "--json")
    assert code == 0 and [(s["act"], s["frame"]) for s in out["stills"]] == [(1, 0), (2, 5)]
    code, out, _err = cli("render", "stills", "--film", str(film), "--frames", "0,x", "--json")
    assert code == 2
    # the open web renderer draws a still as one frame of its range
    code, out, _err = cli("render", "stills", "--film", str(film), "--frames", "3", "--engine", "web", "--scale", "1",
                          "--json")
    assert code == 0 and [(s["act"], s["frame"]) for s in out["stills"]] == [(1, 3)]
    # After Effects renders whole acts: refused, with the way to get the frames
    code, out, _err = cli("render", "stills", "--film", str(film), "--frames", "3", "--engine", "ae", "--json")
    assert code == 1 and "--engine ae" in out["hint"]


def test_a_film_without_timing_is_exit_three(fakes, slate_home):
    film = make_film(slate_home["films"], film_id="untimed")
    (film / "timing" / "shots.json").unlink()
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--json")
    assert code == 3 and out["hint"] == "slate timing"


def test_not_a_film_is_exit_two(fakes, tmp_path):
    code, _out, _err = cli("render", "--film", str(tmp_path / "nowhere"), "--json")
    assert code == 2


def test_web_engine_that_isnt_built_is_exit_three(fakes, film, slate_home, monkeypatch):
    from slate.errors import MissingPrerequisite

    write_toml(slate_home["data"], "[render]\nchunk_frames = 100\nforeground_max_subframes = 100000\n")
    real = fakes.bridge.call

    def call(command, args, *, timeout=None):
        if command == "web-render":
            raise MissingPrerequisite("The open web renderer isn't in this engine yet.")
        return real(command, args, timeout=timeout)

    monkeypatch.setattr(sys.modules["slate.engine.bridge"], "call", call)
    code, out, _err = cli("render", "--film", str(film), "--act", "1", "--engine", "web", "--json")
    assert code == 3 and "web renderer" in out["message"]


def test_render_queues_through_the_real_detach_convention(film, monkeypatch):
    """No fakes: runner.submit reaches slate.jobs.client.detach_current itself, which queues `slate render … --in-queue`
    holding the render slot (only the queue's own submit is caught here)."""
    from slate.jobs import client
    from slate.render import runner

    captured = []
    monkeypatch.setattr(client, "submit", lambda kind, argv, **kw: captured.append((kind, list(argv), kw)) or "job-9")
    film_json, p = runner.prepare(film, act=1)
    argv = runner.job_argv(film, p, act=1, frames=None, out=None, go=False, fresh=False, resume=False)
    assert runner.submit(film, film_json, p, argv) == "job-9"
    [(kind, queued, kw)] = captured
    assert kind == "render" and queued[:2] == ["slate", "render"] and "--in-queue" in queued and "--detach" not in queued
    assert tuple(kw["resources"]) == ("render",) and kw["film"] == str(film) and kw["resumable"] is True
