"""Render against the real job-queue locks and disk reservations (slate.jobs), when that module is installed.

The engine and the film state are still fakes; free space is pinned with the jobs module's own test knob, so the
results don't depend on this Mac's disk.
"""

import sys
import threading

import pytest

from slate.errors import Busy, Refused
from slate.render import deps, registry, runner

jobs_locks = pytest.importorskip("slate.jobs.locks")
jobs_disk = pytest.importorskip("slate.jobs.disk")


@pytest.fixture
def real_jobs(fakes, monkeypatch):
    for name in ("slate.jobs", "slate.jobs.locks", "slate.jobs.disk"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "500")
    monkeypatch.delenv("SLATE_JOB_ID", raising=False)
    monkeypatch.delenv("SLATE_JOB_HOLDS", raising=False)
    import importlib

    return importlib.import_module("slate.jobs.locks"), importlib.import_module("slate.jobs.disk")


def test_render_holds_the_real_render_slot_and_disk(real_jobs, film):
    locks, disk = real_jobs
    seen = {}
    real_render = sys.modules["slate.engine.bridge"].call

    def watching(command, args, *, timeout=None):
        if command == "render":
            seen["holders"] = [h.get("owner") for h in locks.holders("render")]
            seen["reserved"] = disk.outstanding_gb(film)
        return real_render(command, args, timeout=timeout)

    sys.modules["slate.engine.bridge"].call = watching
    runner.run(film, act=1)
    assert any("slate render" in (o or "") for o in seen["holders"])
    assert seen["reserved"] >= 0.1
    assert locks.holders("render") == [] and disk.outstanding_gb(film) == 0  # both given back
    assert registry.find(film, 1) is not None


def test_a_render_slot_held_elsewhere_is_busy(real_jobs, film):
    locks, _disk = real_jobs
    held = threading.Event()
    done = threading.Event()

    def other():
        with locks.hold("render", owner="another render"):
            held.set()
            done.wait(10)

    t = threading.Thread(target=other)
    t.start()
    try:
        assert held.wait(10)
        assert deps.render_busy() is True
        with pytest.raises(Busy):
            with deps.render_lock(owner="test", timeout=0):
                pass
    finally:
        done.set()
        t.join()
    assert deps.render_busy() is False


def test_real_disk_refusal_stops_the_render(real_jobs, film, monkeypatch):
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "1")  # under the 5 GB Slate always keeps free
    with pytest.raises(Refused, match="Not enough disk"):
        runner.run(film, act=1)
    assert registry.find(film, 1) is None
