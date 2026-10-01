"""The job queue and the OpenClaw gateway, and cancelling a generation job (L-generate-D027).

A runner started inside the gateway would inherit its egress proxy, which turns the vendors away, so jobs that hold a
vendor's slot wait for the launchd runner, which works outside the gateway. And cancelling a generation job cancels its
queued Higgsfield requests at Higgsfield before the command is stopped: stopping a wait never stops the request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobs_helpers import PY
from slate import procs
from slate.jobs import client, model, runner, store


def _never_spawn(*a, **k):
    raise AssertionError("a runner was started inside the gateway")


def test_inside_the_gateway_vendor_jobs_wait_for_the_launchd_runner(queue_home, monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "openclaw")
    monkeypatch.delenv("SLATE_JOB_ID", raising=False)
    monkeypatch.setattr(procs, "spawn_detached", _never_spawn)
    tts = client.submit("voice-tts", [PY, "-c", "pass"], resources=("elevenlabs_tts",))  # start_runner: default
    assert store.load(tts)["state"] == "queued"
    assert client.ensure_runner() == "waiting"
    render = client.submit("render", [PY, "-c", "pass"], start_runner=False)
    launched: list[str] = []
    monkeypatch.setattr(runner, "launch", lambda job_id: launched.append(job_id) or True)
    started = runner.schedule()  # this runner is inside the gateway: it runs the render, never the vendor job
    assert started == [render] and launched == [render]
    assert "launchd runner" in store.load(tts)["waiting"] and store.load(tts)["state"] == "queued"


def test_outside_the_gateway_vendor_jobs_run_as_before(queue_home, monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    tts = client.submit("voice-tts", [PY, "-c", "pass"], resources=("elevenlabs_tts",), start_runner=False)
    monkeypatch.setattr(runner, "launch", lambda job_id: True)
    assert runner.schedule() == [tts]


# ---------------------------------------------------------------------------------------------------------------
# Cancelling a generation


def _gen_job(film_dir: Path, *, state: str = "running", attempts: int = 1) -> str:
    argv = [PY, "-m", "slate", "gen", "run", "--film", str(film_dir), "--shot", "S04", "--timeout", "1800", "--live"]
    job_id = client.submit("generation", argv, resources=("higgsfield",), start_runner=False)
    with store.update(job_id) as job:
        job.update(state=state, attempts=attempts)
        if state == "running":
            job.update(phase="running", launch="tok", pid=None, started_at=model.iso(), launched_at=model.iso())
    return job_id


@pytest.fixture
def calls(monkeypatch):
    """Record the vendor cancel and the stop, in order (nothing is really signalled or sent)."""
    seen: list = []
    from slate.gen import run as gen_run

    def cancel(film_dir, *, shot_id=None, take_id=None, request_id=None, client=None):
        seen.append(("vendor-cancel", Path(film_dir), shot_id))
        return {"shot": shot_id, "results": [{"take": "t2", "request_id": "req-2", "canceled": True}]}

    real_stop = client._stop

    def stop(job_id, *, by, grace):
        seen.append(("stop", job_id))
        return real_stop(job_id, by=by, grace=grace)

    monkeypatch.setattr(gen_run, "cancel", cancel)
    monkeypatch.setattr(client, "_stop", stop)
    return seen


def test_cancelling_a_generation_cancels_its_requests_before_the_stop(queue_home, calls, tmp_path):
    film_dir = tmp_path / "films" / "reel"
    job_id = _gen_job(film_dir)
    client.cancel(job_id, grace=0)
    assert calls[0] == ("vendor-cancel", film_dir, "S04")  # first: the queued request is cancelled at Higgsfield
    assert calls[1] == ("stop", job_id)  # then the command is stopped
    assert calls[2][0] == "vendor-cancel"  # and once more, for a request sent in between
    job = store.load(job_id)
    assert job["state"] == "cancelled"
    notes = [h for h in job["history"] if h["event"] == "vendor-cancel"]
    assert notes and "t2" in notes[0]["detail"]


def test_a_queued_generation_that_never_ran_has_nothing_to_cancel(queue_home, calls, tmp_path):
    job_id = _gen_job(tmp_path / "films" / "reel", state="queued", attempts=0)
    client.cancel(job_id)
    assert calls == [("stop", job_id)]  # nothing was ever sent, so nothing is asked of the vendor
    requeued = _gen_job(tmp_path / "films" / "reel", state="queued", attempts=1)  # it ran before, then was requeued
    client.cancel(requeued)
    assert calls[1] == ("vendor-cancel", tmp_path / "films" / "reel", "S04") and calls[2] == ("stop", requeued)


def test_other_jobs_never_call_a_vendor_when_cancelled(queue_home, calls):
    job_id = client.submit("render", [PY, "-c", "pass"], start_runner=False)
    client.cancel(job_id)
    assert calls == [("stop", job_id)]
    assert client.generation_target({"kind": "render", "argv": ["slate", "render"]}) is None
    target = client.generation_target({"kind": "x", "argv": ["slate", "gen", "run", "--shot=S09"],
                                       "film_dir": "/films/f"})
    assert target == (Path("/films/f"), "S09")


def test_a_vendor_cancel_that_fails_never_blocks_the_stop(queue_home, monkeypatch, tmp_path):
    from slate.gen import run as gen_run

    def broken(*a, **k):
        raise RuntimeError("Higgsfield didn't answer")

    monkeypatch.setattr(gen_run, "cancel", broken)
    job_id = _gen_job(tmp_path / "films" / "reel")
    client.cancel(job_id, grace=0)
    job = store.load(job_id)
    assert job["state"] == "cancelled"
    assert any("not cancelled" in h.get("detail", "") for h in job["history"] if h["event"] == "vendor-cancel")
