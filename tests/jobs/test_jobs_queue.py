"""The queue's records and rules: job files, priority order, requeueing a job whose supervisor died."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from slate import jsonio
from slate.errors import Refused, UsageError
from slate.jobs import client, model, runner, shim, store


def _film(films_dir, film_id, mode="supervised", **extra):
    folder = films_dir / film_id
    folder.mkdir()
    (folder / "film.json").write_text(json.dumps({"id": film_id, "mode": mode, **extra}))
    return folder


def _dead_pid() -> int:
    proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    return int(proc.stdout)


def test_priority_order_supervised_express_draft_autonomous_then_age():
    jobs = [
        {"id": "a", "priority": "autonomous", "created_at": "2026-09-30T10:00:00+00:00"},
        {"id": "b", "priority": "draft", "created_at": "2026-09-30T10:01:00+00:00"},
        {"id": "c", "priority": "supervised", "created_at": "2026-09-30T10:05:00+00:00"},
        {"id": "d", "priority": "express", "created_at": "2026-09-30T10:02:00+00:00"},
        {"id": "e", "priority": "supervised", "created_at": "2026-09-30T10:03:00+00:00"},
        {"id": "f", "priority": "autonomous", "created_at": "2026-09-30T09:00:00+00:00"},
    ]
    assert [j["id"] for j in model.order(jobs)] == ["e", "c", "d", "b", "f", "a"]


def test_submit_writes_a_valid_job_file_without_secrets(queue_home, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-should-never-be-stored")
    monkeypatch.setenv("SLATE_MODEL_TTS", "eleven_v4")
    job_id = client.submit("render", ["slate", "render", "--act", "2"], resources=("render",), disk_gb=1.5,
                           start_runner=False)
    path = queue_home["data"] / "jobs" / f"{job_id}.json"
    job = jsonio.read(path)
    assert jsonio.validate(job, "job") == []
    assert job["state"] == "queued" and job["attempts"] == 0 and job["resumable"] is True
    assert job["argv"] == ["slate", "render", "--act", "2"] and job["resources"] == ["render"]
    assert job["log"].endswith(f"logs/jobs/{job_id}.log")
    assert job["env"]["SLATE_DATA"] == str(queue_home["data"])
    assert job["env"]["SLATE_FAKE_VENDORS"] == "1" and job["live"] is False
    assert job["env"]["SLATE_MODEL_TTS"] == "eleven_v4"
    assert "sk-should-never-be-stored" not in path.read_text()
    assert model.JOB_ID.match(job_id)


def test_a_job_renders_with_the_engine_its_caller_chose(queue_home, monkeypatch, tmp_path):
    """SLATE_ENGINE_DIR travels with the job, so a queued render uses the engine the caller pointed at (a checkout's
    engine, proved through the queue before the installed one is updated), never quietly the installed one."""
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(tmp_path / "engine"))
    job_id = client.submit("qa", ["slate", "qa", "stability", "conformance", "--render"], resources=("render",),
                           start_runner=False)
    job = jsonio.read(queue_home["data"] / "jobs" / f"{job_id}.json")
    assert job["env"]["SLATE_ENGINE_DIR"] == str(tmp_path / "engine")
    monkeypatch.delenv("SLATE_ENGINE_DIR")
    other = jsonio.read(queue_home["data"] / "jobs" / f"{client.submit('qa', ['slate', 'qa'], start_runner=False)}.json")
    assert "SLATE_ENGINE_DIR" not in other["env"]


def test_submit_refuses_bad_input(queue_home):
    with pytest.raises(UsageError):
        client.submit("render", "slate render --act 2", start_runner=False)  # a shell string
    with pytest.raises(UsageError):
        client.submit("render", ["x"], resources=("gpu",), start_runner=False)
    with pytest.raises(UsageError):
        client.submit("Render Job", ["x"], start_runner=False)
    with pytest.raises(UsageError):
        client.submit("render", ["x"], disk_gb=-1, start_runner=False)
    with pytest.raises(UsageError):
        client.status("../../etc/passwd")


def test_a_job_never_outranks_its_film(queue_home):
    auto = _film(queue_home["films"], "reel", mode="autonomous")
    job_id = client.submit("render", ["true"], film=auto, priority="supervised", start_runner=False)
    job = store.load(job_id)
    assert job["priority"] == "autonomous" and job["priority_requested"] == "supervised"
    assert job["film"] == "reel" and job["film_dir"] == str(auto.resolve())
    sup = _film(queue_home["films"], "promo", mode="supervised")
    lower = client.submit("render", ["true"], film=sup, priority="draft", start_runner=False)
    assert store.load(lower)["priority"] == "draft"


def _running_job(pid, *, resumable=True, attempts=1, max_attempts=3, proc_start=None):
    job_id = client.submit("render", ["true"], start_runner=False)
    with store.update(job_id) as job:
        job.update(state="running", phase="running", launch="tok", pid=pid, pgid=pid, attempts=attempts,
                   max_attempts=max_attempts, resumable=resumable, proc_start=proc_start,
                   started_at=model.iso(), launched_at=model.iso())
    return job_id


def test_dead_supervisor_is_requeued_when_resumable(queue_home):
    job_id = _running_job(_dead_pid())
    result = runner.reconcile()
    job = store.load(job_id)
    assert result["requeued"] == [job_id]
    assert job["state"] == "queued" and job["pid"] is None
    assert "stopped unexpectedly" in job["reason"] and "requeued to resume" in job["reason"]
    assert job["history"][-1]["event"] == "requeued"


def test_dead_supervisor_fails_when_not_resumable(queue_home):
    job_id = _running_job(_dead_pid(), resumable=False)
    runner.reconcile()
    job = store.load(job_id)
    assert job["state"] == "failed" and "isn't resumable" in job["reason"]


def test_dead_supervisor_fails_after_max_attempts(queue_home):
    job_id = _running_job(_dead_pid(), attempts=3, max_attempts=3)
    runner.reconcile()
    job = store.load(job_id)
    assert job["state"] == "failed" and "gave up after 3 attempts" in job["reason"]


def test_reused_pid_is_not_mistaken_for_the_supervisor(queue_home):
    # Our own pid is alive, but it didn't start at this time: after a reboot the number belongs to someone else.
    job_id = _running_job(os.getpid(), proc_start="Mon Jan  1 00:00:00 2001")
    assert runner.reconcile()["requeued"] == [job_id]


def test_live_supervisor_is_left_alone(queue_home):
    from slate.jobs import proc

    job_id = _running_job(os.getpid(), proc_start=proc.own_stamp())
    assert runner.reconcile() == {"requeued": [], "failed": []}
    assert store.load(job_id)["state"] == "running"


def test_status_reconciles_a_dead_job(queue_home, monkeypatch):
    monkeypatch.setattr(client, "ensure_runner", lambda: "running")
    job_id = _running_job(_dead_pid())
    assert client.status(job_id)["state"] == "queued"


@pytest.mark.parametrize("rc,state,words", [
    (0, "done", None),
    (5, "queued", "busy"),
    (75, "queued", "temporary failure"),
    (4, "failed", "spend refused"),
    (-15, "failed", "SIGTERM"),
    (3, "failed", "exited with 3"),
])
def test_exit_codes_decide_the_outcome(rc, state, words):
    job = {"id": "j-20260930-000000-abcd", "attempts": 1, "max_attempts": 3, "history": []}
    shim._outcome(job, rc)
    assert job["state"] == state
    if words:
        assert words in job["reason"]
    if state == "queued":
        assert job["not_before"]
    if state in ("done", "failed"):
        assert job["finished_at"]


def test_retry_later_codes_give_up_after_max_attempts():
    job = {"id": "j-20260930-000000-abcd", "attempts": 3, "max_attempts": 3, "history": []}
    shim._outcome(job, 75)
    assert job["state"] == "failed"


def test_retry_and_cancel_of_a_queued_job(queue_home, monkeypatch):
    monkeypatch.setattr(client, "ensure_runner", lambda: "running")
    job_id = client.submit("render", ["true"], start_runner=False)
    client.cancel(job_id)
    job = store.load(job_id)
    assert job["state"] == "cancelled" and job["stopped"] == "cancel"
    client.cancel(job_id)  # cancelling a finished job is a no-op
    again = client.retry(job_id)
    assert again["state"] == "queued" and again["retries"] == 1 and again["attempts"] == 0
    with store.update(job_id) as j:
        j["state"] = "done"
    with pytest.raises(UsageError):
        client.retry(job_id)


def test_list_puts_running_and_queued_first(queue_home):
    a = client.submit("render", ["true"], priority="autonomous", start_runner=False)
    b = client.submit("render", ["true"], priority="supervised", start_runner=False)
    c = client.submit("render", ["true"], start_runner=False)
    with store.update(c) as j:
        j.update(state="done", finished_at=model.iso())
    ids = [j["id"] for j in client.list_jobs()]
    assert ids == [b, a, c]
    assert [j["id"] for j in client.list_jobs(states=["done"])] == [c]


def test_stale_launch_token_never_runs(queue_home):
    job_id = client.submit("render", ["true"], start_runner=False)
    with store.update(job_id) as job:
        job.update(state="running", launch="current")
    assert shim.main([job_id, "old-token"]) == 0
    assert store.load(job_id)["pid"] is None  # the stale supervisor wrote nothing


def test_run_argv_wraps_caffeinate_and_nice_and_resolves_slate(queue_home):
    argv = shim.run_argv(["slate", "render", "--act", "1"])
    assert argv[:2] == ["/usr/bin/caffeinate", "-i"]
    assert "nice" in argv[2] and argv[3:5] == ["-n", "5"]
    assert argv[5:8] == [sys.executable, "-m", "slate"] and argv[-3:] == ["render", "--act", "1"]


def test_autonomous_jobs_never_get_after_effects(queue_home):
    auto = _film(queue_home["films"], "reel", mode="autonomous")
    with pytest.raises(Refused) as exc:
        client.submit("master", ["slate", "master", "--ae"], film=auto, resources=("ae",), start_runner=False)
    assert "Adobe" in exc.value.message and "ffmpeg" in exc.value.hint
    with pytest.raises(Refused):
        client.submit("master", ["true"], resources=("ae",), priority="autonomous", start_runner=False)
    assert client.submit("master", ["true"], resources=("ae",), start_runner=False)  # supervised may queue AE work


def test_a_strangers_process_group_is_never_signalled(queue_home):
    # An unrelated process that happens to lead a group whose number a dead supervisor once had.
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        job_id = _running_job(stranger.pid, proc_start="Mon Jan  1 00:00:00 2001")  # not the supervisor we started
        with store.update(job_id) as job:
            job["run_argv"] = ["/usr/bin/caffeinate", "-i", "/usr/bin/nice", "-n", "5", "slate", "render", "--act", "9"]
        assert runner.reconcile()["requeued"] == [job_id]
        assert stranger.poll() is None  # still running: its group wasn't ours, so nothing was sent to it
    finally:
        stranger.kill()
        stranger.wait()


def test_group_identity_checks(queue_home):
    from slate.jobs import proc

    marker = "slate-identity-check-7f3a"
    member = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"],
                              start_new_session=True)
    try:
        job = {"id": "j-20260930-000000-abcd", "launched_at": model.iso(),
               "run_argv": ["/usr/bin/caffeinate", "-i", "/usr/bin/nice", "-n", "5", sys.executable, "-c",
                            f"import time; time.sleep(30)  # {marker}"]}
        assert proc.group_is_job(member.pid, job)
        assert not proc.group_is_job(member.pid, {**job, "run_argv": ["something", "else"]})
        assert not proc.group_is_job(member.pid, {**job, "launched_at": "2000-01-01T00:00:00+00:00"})  # pre-boot
    finally:
        member.kill()
        member.wait()
