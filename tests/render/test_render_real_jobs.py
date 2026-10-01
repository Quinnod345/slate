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
    """The real slate.jobs locks and disk modules, imported fresh over the fakes, and nothing left behind.

    Importing slate.jobs again binds the new module to the `slate` package's `jobs` attribute as well as to
    sys.modules. monkeypatch puts sys.modules back, but not the attribute, so the attribute is restored here: left on
    the orphan module, it made a later test in another folder (voice's --detach) reach the real job client through
    `slate.jobs.client` while its fake sat on the module everyone else imports.
    """
    import importlib

    import slate

    had, original = hasattr(slate, "jobs"), getattr(slate, "jobs", None)
    for name in ("slate.jobs", "slate.jobs.locks", "slate.jobs.disk"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setenv("SLATE_FAKE_DISK_FREE_GB", "500")
    monkeypatch.delenv("SLATE_JOB_ID", raising=False)
    monkeypatch.delenv("SLATE_JOB_HOLDS", raising=False)
    try:
        yield importlib.import_module("slate.jobs.locks"), importlib.import_module("slate.jobs.disk")
    finally:
        # runs before monkeypatch's own undo (it was set up first), which then restores sys.modules
        if had:
            slate.jobs = original
        elif hasattr(slate, "jobs"):
            del slate.jobs


def test_render_holds_the_real_render_slot_and_disk(real_jobs, film, monkeypatch):
    locks, disk = real_jobs
    seen = {}
    bridge = sys.modules["slate.engine.bridge"]
    real_render = bridge.call

    def watching(command, args, *, timeout=None):
        if command == "render":
            seen["holders"] = [h.get("owner") for h in locks.holders("render")]
            seen["reserved"] = disk.outstanding_gb(film)
        return real_render(command, args, timeout=timeout)

    monkeypatch.setattr(bridge, "call", watching)
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


def test_after_the_real_jobs_tests_the_slate_package_points_at_the_jobs_module_everyone_imports():
    """Runs after the tests above (file order): the package attribute and sys.modules agree again, so a later test that
    patches `slate.jobs.client` patches the module the code under test imports."""
    import slate

    mod = sys.modules.get("slate.jobs")
    if mod is None:
        assert not isinstance(getattr(slate, "jobs", None), type(sys))
    else:
        assert getattr(slate, "jobs", None) is mod
