"""The queue with real processes: detached jobs, cancel, exit codes, slots and priority in the scheduler."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from jobs_helpers import PY, hold_in_other_process, job_state, wait_for, write_toml
from slate import jsonio, procs
from slate.cli import run_cli
from slate.jobs import client, proc, runner, store


def _group_commands(pgid: int) -> str:
    return subprocess.run(["/bin/ps", "-o", "command=", "-g", str(pgid)], capture_output=True, text=True).stdout


def test_real_detached_job_moves_through_its_states(queue_home, tmp_path):
    marker = tmp_path / "running"
    code = (f"import sys, time; open({str(marker)!r}, 'w').write('x'); "
            "print('stdin:', repr(sys.stdin.read()), flush=True); time.sleep(1); print('finished')")
    # Submit from another process that exits at once, the way a research slice would: the job must outlive it.
    submitter = (f"from slate.jobs import client; print(client.submit('test', [{PY!r}, '-c', {code!r}]))")
    out = subprocess.run([PY, "-c", submitter], capture_output=True, text=True, check=True, env=dict(os.environ))
    job_id = out.stdout.strip()
    assert job_state(job_id) in ("queued", "running")
    wait_for(marker.exists, what="the job to start")
    job = wait_for(lambda: (j := store.load(job_id)).get("phase") == "running" and j, what="phase running")
    assert job["state"] == "running" and job["attempts"] == 1
    supervisor = job["pid"]
    assert os.getsid(supervisor) == supervisor  # its own session: nothing that dies upstream takes it down
    assert job["pgid"] == supervisor
    assert "caffeinate -i" in _group_commands(supervisor)  # the Mac doesn't idle-sleep under it
    wait_for(lambda: job_state(job_id) == "done", what="the job to finish")
    job = store.load(job_id)
    assert job["exit_code"] == 0 and job["finished_at"] and job["phase"] is None and job["holds"] == []
    log_text = client.tail(job_id, 50)
    assert "stdin: ''" in log_text  # stdin is closed: nothing can wait on a prompt
    assert "finished" in log_text and f"{job_id} done" in log_text
    events = [h["event"] for h in job["history"]]
    assert events[:3] == ["queued", "launched", "running"] and events[-1] == "done"
    # the one-shot runner drains the queue and exits by itself
    status = jsonio.read(queue_home["data"] / "jobs" / "runner.json")
    wait_for(lambda: not proc.alive(status["pid"]), what="the one-shot runner to exit")
    assert not runner.runner_alive()


def test_cancel_kills_the_whole_process_group(queue_home, tmp_path):
    child_pid_file = tmp_path / "child.pid"
    code = ("import subprocess, time\n"
            "p = subprocess.Popen(['sleep', '60'])\n"
            f"open({str(child_pid_file)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)\n")
    job_id = client.submit("test", [PY, "-c", code], start_runner=False)
    assert runner.schedule() == [job_id]
    wait_for(child_pid_file.exists, what="the job's own child to start")
    grandchild = int(child_pid_file.read_text())
    supervisor = store.load(job_id)["pid"]
    assert procs.pid_alive(grandchild)
    client.cancel(job_id, grace=2)
    wait_for(lambda: not proc.alive(grandchild), timeout=10, what="the job's child to die")
    wait_for(lambda: store.load(job_id).get("exit_code") is not None, what="the supervisor to record the exit")
    job = store.load(job_id)
    assert job["state"] == "cancelled" and job["stopped"] == "cancel" and job["signal"] == "SIGTERM"
    proc.reap()
    wait_for(lambda: not proc.alive(supervisor), what="the supervisor to exit")
    assert not proc.group_alive(supervisor)


def test_failure_records_the_exit_code_and_reason(queue_home):
    job_id = client.submit("test", [PY, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"],
                           start_runner=False)
    runner.schedule()
    wait_for(lambda: job_state(job_id) == "failed", what="the job to fail")
    job = store.load(job_id)
    assert job["exit_code"] == 3 and "exited with 3" in job["reason"]
    assert "boom" in client.tail(job_id)


def test_try_later_exit_goes_back_in_the_queue(queue_home):
    job_id = client.submit("test", [PY, "-c", "import sys; sys.exit(75)"], start_runner=False)
    runner.schedule()
    wait_for(lambda: job_state(job_id) == "queued" and store.load(job_id)["attempts"] == 1, what="the requeue")
    job = store.load(job_id)
    assert job["not_before"] and "try again" in job["reason"]
    assert runner.schedule() == []  # not before its backoff
    assert store.load(job_id)["waiting"].startswith("trying again after")
    client.cancel(job_id)


def test_slots_limit_how_many_jobs_run_at_once(queue_home, tmp_path):
    write_toml(queue_home["data"], "[jobs]\npoll_seconds = 0.1\nkill_grace_s = 2\nmax_load_per_core = 0\n"
                                   "[jobs.slots]\nelevenlabs_tts = 2\n")
    gate = tmp_path / "go"
    code = f"import os, time\nwhile not os.path.exists({str(gate)!r}): time.sleep(0.05)\n"
    ids = [client.submit("tts", [PY, "-c", code], resources=("elevenlabs_tts",), start_runner=False)
           for _ in range(3)]
    started = runner.schedule()
    assert started == ids[:2]
    assert store.load(ids[2])["waiting"].startswith("every elevenlabs_tts slot is busy")
    wait_for(lambda: all(store.load(i).get("phase") == "running" for i in ids[:2]), what="two jobs running")
    assert runner.schedule() == []  # still full
    gate.write_text("go")
    wait_for(lambda: all(job_state(i) == "done" for i in ids[:2]), what="the first two to finish")
    assert runner.schedule() == [ids[2]]
    wait_for(lambda: job_state(ids[2]) == "done", what="the third to finish")


def test_supervised_gets_the_freed_slot_before_an_older_autonomous_job(queue_home, tmp_path):
    other = hold_in_other_process("render", marker=tmp_path / "held")
    try:
        auto = client.submit("render", [PY, "-c", "pass"], resources=("render",), priority="autonomous",
                             start_runner=False)
        time.sleep(1.1)  # older by a clear second
        sup = client.submit("render", [PY, "-c", "pass"], resources=("render",), priority="supervised",
                            start_runner=False)
        assert runner.schedule() == []
        assert "other-process" in store.load(sup)["waiting"]
    finally:
        other.kill()
        other.wait()
    assert runner.schedule() == [sup]
    assert job_state(auto) == "queued"
    wait_for(lambda: job_state(sup) == "done", what="the supervised render")
    assert runner.schedule() == [auto]
    wait_for(lambda: job_state(auto) == "done", what="the autonomous render")


def test_a_waiting_higher_priority_job_keeps_its_slots(queue_home, tmp_path):
    other = hold_in_other_process("whisper", marker=tmp_path / "held")
    try:
        sup = client.submit("render", [PY, "-c", "pass"], resources=("render", "whisper"), start_runner=False)
        auto = client.submit("render", [PY, "-c", "pass"], resources=("render",), priority="autonomous",
                             start_runner=False)
        assert runner.schedule() == []
        assert store.load(sup)["waiting"].startswith("every whisper slot is busy")
        assert store.load(auto)["waiting"] == "waiting behind a higher-priority job for render"
    finally:
        other.kill()
        other.wait()
        for jid in (sup, auto):
            client.cancel(jid)


def test_renders_wait_while_the_mac_is_overloaded(queue_home, monkeypatch):
    write_toml(queue_home["data"], "[jobs]\nmax_load_per_core = 0.0001\n")
    monkeypatch.setattr(os, "getloadavg", lambda: (500.0, 400.0, 300.0))
    job_id = client.submit("render", [PY, "-c", "pass"], resources=("render",), start_runner=False)
    tts = client.submit("tts", [PY, "-c", "pass"], resources=("elevenlabs_tts",), start_runner=False)
    assert runner.schedule() == [tts]  # only renders wait for the load to settle
    assert "overloaded (load 500" in store.load(job_id)["waiting"]
    wait_for(lambda: job_state(tts) == "done", what="the tts job")
    client.cancel(job_id)


def test_only_one_runner_serves_at_a_time(queue_home):
    with runner._runner_lock(block=False) as mine:
        assert mine and runner.runner_alive()
        assert client.ensure_runner() == "running"
        out = subprocess.run([PY, "-m", "slate", "jobs", "serve", "--once"], capture_output=True, text=True,
                             env=dict(os.environ), timeout=60)
        assert out.returncode == 0 and "another runner is already serving" in out.stderr
    assert not runner.runner_alive()


def test_cli_submit_status_wait_list_and_log(queue_home):
    code, out, err = run_cli(["--json", "job", "submit", "--kind", "test", "--", PY, "-c", "print('via cli')"])
    assert code == 0, err
    submitted = json.loads(out)
    job_id = submitted["id"]
    assert submitted["state"] == "queued" and submitted["runner"] in ("started", "running")
    code, out, err = run_cli(["--json", "job", "wait", job_id, "--timeout", "60"])
    assert code == 0, err
    assert json.loads(out)["state"] == "done"
    code, out, _ = run_cli(["--json", "job", "status", job_id])
    assert code == 0 and json.loads(out)["exit_code"] == 0
    code, out, _ = run_cli(["--json", "job", "log", job_id])
    assert code == 0 and "via cli" in json.loads(out)["text"]
    code, out, _ = run_cli(["--json", "job", "list"])
    assert code == 0 and json.loads(out)["jobs"][0]["id"] == job_id
    code, out, _ = run_cli(["job", "status", job_id])
    assert code == 0 and "done" in out
    # waiting on a job that can't finish in time is exit 5 (busy), and the job keeps going
    blocker = client.submit("test", [PY, "-c", "import time; time.sleep(30)"], start_runner=False)
    code, out, _ = run_cli(["--json", "job", "wait", blocker, "--timeout", "0.2"])
    assert code == 5 and json.loads(out)["error"] == "busy"
    code, out, _ = run_cli(["--json", "job", "cancel", blocker])
    assert code == 0 and json.loads(out)["state"] == "cancelled"
    code, _, _ = run_cli(["job", "submit", "--kind", "test"])
    assert code == 2  # no command given
    code, _, _ = run_cli(["job", "status", "j-20200101-000000-0000"])
    assert code == 2  # no such job


def test_jobs_status_locks_disk_and_prune(queue_home, monkeypatch):
    monkeypatch.setattr("slate.jobs.agent._launchctl", lambda *a: (113, "not loaded"))
    code, out, _ = run_cli(["--json", "jobs", "status"])
    assert code == 0 and set(json.loads(out)["counts"]) == {"queued", "running", "done", "failed", "cancelled"}
    code, out, _ = run_cli(["--json", "jobs", "locks"])
    assert code == 0 and len(json.loads(out)["resources"]) == 6
    code, out, _ = run_cli(["--json", "jobs", "disk"])
    assert code == 0 and json.loads(out)["floor_gb"] == 5.0
    old = client.submit("test", ["true"], start_runner=False)
    with store.update(old) as j:
        j.update(state="done", finished_at="2020-01-01T00:00:00+00:00")
    code, out, _ = run_cli(["--json", "--dry-run", "jobs", "prune", "--days", "30"])
    assert code == 0 and json.loads(out)["removed"] == [old] and store.exists(old)
    code, out, _ = run_cli(["--json", "jobs", "prune", "--days", "30"])
    assert code == 0 and not store.exists(old)


def test_submit_never_waits_on_stdin(queue_home):
    # Oneiro's research slices run with stdin as a pipe that never closes, and are killed after ten minutes.
    proc_ = subprocess.Popen([PY, "-m", "slate", "--json", "job", "submit", "--kind", "test", "--", PY, "-c", "pass"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
    try:
        out, err = proc_.communicate(timeout=60)  # communicate closes stdin only after the process is done writing
    finally:
        if proc_.poll() is None:
            proc_.kill()
    assert proc_.returncode == 0, err
    job_id = json.loads(out)["id"]
    wait_for(lambda: job_state(job_id) == "done", what="the job")


def test_the_command_knows_it_is_a_queued_job(queue_home):
    code = ("import os; print('env', os.environ['SLATE_JOB_ID'], os.environ['SLATE_JOB_ATTEMPT'], "
            "os.environ['SLATE_JOB_RESUME'], os.environ['SLATE_JOB_HOLDS'], os.environ['BROWSER'], "
            "os.environ['SLATE_DATA'])")
    job_id = client.submit("tts", [PY, "-c", code], resources=("elevenlabs_tts", "whisper"), start_runner=False)
    assert runner.schedule() == [job_id]
    wait_for(lambda: job_state(job_id) == "done", what="the job")
    line = [ln for ln in client.tail(job_id, 50).splitlines() if ln.startswith("env ")][0]
    assert line.split() == ["env", job_id, "1", "0", "elevenlabs_tts,whisper", "none",
                            str(queue_home["data"])]
