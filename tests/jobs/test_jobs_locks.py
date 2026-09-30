"""Resource locks: counted slots, contention between processes, holder details, re-entrancy and delegation."""

from __future__ import annotations

import threading

import pytest

from jobs_helpers import hold_in_other_process, write_toml
from slate.errors import Busy, UsageError
from slate.jobs import locks


def test_default_slot_counts(slate_home):
    assert locks.slots("render") == 1  # [render] slots in config: one heavy render at a time
    assert locks.slots("ae") == 1
    assert locks.slots("elevenlabs_tts") == 4  # Creator allows 5, Pro 10
    assert locks.slots("elevenlabs_music") == 1
    assert locks.slots("higgsfield") == 2
    assert locks.slots("whisper") == 1
    with pytest.raises(UsageError):
        locks.slots("gpu")


def test_slot_counts_come_from_slate_toml(slate_home, monkeypatch):
    write_toml(slate_home["data"], "[render]\nslots = 3\n\n[jobs.slots]\nelevenlabs_tts = 10\nae = 4\nwhisper = 0\n")
    assert locks.slots("render") == 3
    assert locks.slots("elevenlabs_tts") == 10
    assert locks.slots("ae") == 1  # After Effects can't run two scripts at once, whatever the settings say
    assert locks.slots("whisper") == 0  # paused
    monkeypatch.setenv("SLATE_RENDER_SLOTS", "2")
    assert locks.slots("render") == 2


def test_counted_slots_fill_then_refuse(slate_home):
    write_toml(slate_home["data"], "[jobs.slots]\nelevenlabs_tts = 2\n")
    got: list = []
    release = threading.Event()

    def worker():
        with locks.hold("elevenlabs_tts", timeout=5, owner="worker") as held:
            got.append(held.slot)
            release.wait(10)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    try:
        for _ in range(100):
            if len(got) == 2:
                break
            threading.Event().wait(0.05)
        assert sorted(got) == [0, 1]
        assert locks.free("elevenlabs_tts") == 0
        with pytest.raises(Busy) as exc:
            locks.acquire("elevenlabs_tts", timeout=0)
        assert exc.value.exit_code == 5
        assert len(exc.value.details["holders"]) == 2
    finally:
        release.set()
        for t in threads:
            t.join()
    assert locks.free("elevenlabs_tts") == 2


def test_contention_across_two_processes(slate_home, tmp_path):
    other = hold_in_other_process("ae", marker=tmp_path / "held")
    try:
        with pytest.raises(Busy) as exc:
            locks.acquire("ae", timeout=0.3)
        assert "other-process" in exc.value.message
        (holder,) = locks.holders("ae")
        assert holder["pid"] == other.pid and holder["owner"] == "other-process" and holder["slot"] == 0
    finally:
        other.kill()
        other.wait()
    # the OS dropped the dead holder's lock: the slot is free at once, with no cleanup step
    with locks.hold("ae", timeout=2) as held:
        assert held.slot == 0
        assert locks.holders("ae")[0]["pid"] != other.pid
    assert locks.holders("ae") == []


def test_waiting_holder_gets_the_slot_when_it_frees(slate_home, tmp_path):
    other = hold_in_other_process("whisper", marker=tmp_path / "held", seconds=1.0)
    try:
        with locks.hold("whisper", timeout=30) as held:  # waits for the other process to let go
            assert held.slot == 0
    finally:
        other.kill()
        other.wait()


def test_holds_are_reentrant_within_a_thread(slate_home):
    with locks.hold("ae", timeout=0) as outer:
        with locks.hold("ae", timeout=0) as inner:  # After Effects' driver may nest; it must not deadlock
            assert inner is outer
        assert locks.free("ae") == 0
    assert locks.free("ae") == 1


def test_a_queued_job_uses_its_own_slot(slate_home, monkeypatch, tmp_path):
    # The job's supervisor holds the render slot; the job's command (another process) must not wait on it.
    other = hold_in_other_process("render", marker=tmp_path / "held")
    try:
        monkeypatch.setenv("SLATE_JOB_HOLDS", "render,elevenlabs_tts")
        monkeypatch.setenv("SLATE_JOB_ID", "j-20260930-000000-abcd")
        with locks.hold("render", timeout=0) as held:
            assert held.delegated and held.slot is None
            # A second, concurrent hold (another thread of the job) needs a real slot, and there's only one.
            errors: list = []

            def second():
                try:
                    locks.acquire("render", timeout=0.1)
                except Busy as exc:
                    errors.append(exc)

            t = threading.Thread(target=second)
            t.start()
            t.join()
            assert errors and errors[0].exit_code == 5
    finally:
        other.kill()
        other.wait()
    assert locks._delegated_in_use.get("render", 0) == 0


def test_status_lists_every_resource(slate_home):
    with locks.hold("higgsfield", owner="plates"):
        rows = {r["resource"]: r for r in locks.status()}
        assert set(rows) == {"render", "ae", "elevenlabs_tts", "elevenlabs_music", "higgsfield", "whisper"}
        assert rows["higgsfield"]["slots"] == 2 and rows["higgsfield"]["free"] == 1
        assert rows["higgsfield"]["holders"][0]["owner"] == "plates"
