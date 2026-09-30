"""Disk space: reservations, the floor a job must clear to start, and the safe stop before the disk fills."""

from __future__ import annotations

import subprocess
import sys

import pytest

from jobs_helpers import job_state, wait_for, write_toml
from slate.errors import Refused
from slate.jobs import client, disk, runner, store


def test_floor_and_stop_line_come_from_config(slate_home):
    assert disk.floor_gb() == 5.0
    assert disk.stop_gb() == 4.0  # the films' watchdog stopped aerender under 4 GB free
    write_toml(slate_home["data"], "[jobs]\ndisk_reserve_gb = 20\ndisk_stop_gb = 8\n")
    assert (disk.floor_gb(), disk.stop_gb()) == (20.0, 8.0)
    write_toml(slate_home["data"], "[jobs]\ndisk_reserve_gb = 2\n")
    assert disk.stop_gb() == 2.0  # never above the floor


def test_reserve_refuses_below_the_floor(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "12")
    with disk.reserve(tmp_path, 6) as res:  # 12 - 6 = 6 >= 5
        assert res.gb == 6 and res.file.exists()
        assert disk.outstanding_gb(tmp_path) == 6
        with pytest.raises(Refused) as exc:  # 12 - 6 reserved - 2 = 4 < 5
            disk.acquire(tmp_path, 2)
        assert exc.value.exit_code == 1
        assert "already reserved" in exc.value.message and exc.value.details["reserved_gb"] == 6
    assert disk.outstanding_gb(tmp_path) == 0  # released
    with disk.reserve(tmp_path, 0):  # nothing to reserve, nothing written
        assert disk.reservations() == []


def test_a_dead_owners_reservation_lapses(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "100")
    code = ("from slate.jobs import disk\n"
            f"disk.acquire({str(tmp_path)!r}, 40, owner='crashed job')\n")  # exits without releasing
    subprocess.run([sys.executable, "-c", code], check=True)
    assert len(list((slate_home["data"] / "disk").glob("r-*.json"))) == 1
    assert disk.outstanding_gb(tmp_path) == 0  # its owner is gone, so it doesn't count
    assert list((slate_home["data"] / "disk").glob("r-*.json")) == []


def test_check_counts_pending_reservations(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "20")
    assert disk.check(tmp_path, 10)[0] is True
    ok, reason, facts = disk.check(tmp_path, 10, pending_gb=8)
    assert ok is False and facts["left_gb"] == 2.0 and "keeps 5 GB free" in reason


def test_watch_trips_under_the_stop_line(slate_home, monkeypatch, tmp_path):
    level = tmp_path / "free"
    level.write_text("50")
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", f"@{level}")
    assert disk.watch(tmp_path)[0] is True
    level.write_text("3.5")
    ok, reason, free = disk.watch(tmp_path)
    assert not ok and free == 3.5 and reason.startswith("stopped safely: disk nearly full")


def test_a_job_that_would_break_the_floor_waits(queue_home, monkeypatch):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "8")
    job_id = client.submit("master", ["true"], disk_gb=5, start_runner=False)
    started = runner.schedule()
    job = store.load(job_id)
    assert started == [] and job["state"] == "queued"
    assert job["waiting"].startswith("waiting for disk space") and "8.0 GB is free" in job["waiting"]


def test_a_running_job_is_stopped_safely_before_the_disk_fills(queue_home, monkeypatch, tmp_path):
    level = tmp_path / "free"
    level.write_text("100")
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", f"@{level}")
    marker = tmp_path / "started"
    # the job's tool runs in a session of its own, as slate.procs.run starts every child: the stop must reach it too
    code = ("import subprocess, time\n"
            "tool = subprocess.Popen(['sleep', '60'], start_new_session=True)\n"
            f"open({str(marker)!r}, 'w').write(str(tool.pid))\n"
            "time.sleep(60)\n")
    job_id = client.submit("master", [sys.executable, "-c", code], disk_gb=2, start_runner=False)
    assert runner.schedule() == [job_id]
    wait_for(lambda: marker.exists() and marker.read_text(), what="the job to start")
    tool = int(marker.read_text())
    wait_for(lambda: store.load(job_id).get("phase") == "running", what="the supervisor to report running")
    assert disk.outstanding_gb(tmp_path) == 2  # the supervisor holds the job's reservation
    level.write_text("1.5")  # the disk is filling up
    wait_for(lambda: store.load(job_id).get("finished_at") and store.load(job_id).get("exit_code") is not None,
             what="the job to be stopped")
    job = store.load(job_id)
    assert job["state"] == "failed" and job["stopped"] == "disk" and job["resumable"] is True
    assert job["reason"].startswith("stopped safely: disk nearly full")
    assert f"slate job retry {job_id}" in job["reason"]
    assert job["signal"] == "SIGTERM"
    from slate.jobs import proc

    wait_for(lambda: not proc.alive(tool), timeout=15, what="the job's own tool to be stopped")
    wait_for(lambda: disk.outstanding_gb(tmp_path) == 0, what="the reservation to be released")
    # resumable later: once there's room, a retry puts it back in the queue
    level.write_text("100")
    monkeypatch.setattr(client, "ensure_runner", lambda: "running")
    assert client.retry(job_id)["state"] == "queued"
    client.cancel(job_id)
    assert job_state(job_id) == "cancelled"


def test_every_job_clears_the_floor_even_without_a_reservation(queue_home, monkeypatch):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "3")  # already under the 5 GB floor
    job_id = client.submit("tts", ["true"], start_runner=False)
    assert runner.schedule() == []
    assert store.load(job_id)["waiting"].startswith("waiting for disk space")


def test_a_command_reserving_inside_its_own_job_does_not_count_twice(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "20")
    job_res = disk.acquire(tmp_path, 10, job="j-20260930-000000-abcd")  # what the job's supervisor holds
    try:
        with pytest.raises(Refused):
            disk.acquire(tmp_path, 10)  # someone else: 20 - 10 - 10 = 0, under the floor
        monkeypatch.setenv("SLATE_JOB_ID", "j-20260930-000000-abcd")
        with disk.reserve(tmp_path, 10) as inner:  # the job's own command: its reservation stands in for the job's
            assert inner.gb == 10
    finally:
        job_res.release()
