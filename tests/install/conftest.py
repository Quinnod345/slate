"""Install tests run against temp homes only. Nothing here may touch this Mac's real home, launchd or agent CLIs.

Every test gets:
- HOME pointing at a temp folder (so Path.home() is never the real one, and every layout is a sandbox);
- a guard that fails the test if anything tries to run launchctl, brew, or a real claude/codex/openclaw;
- the job agent's LaunchAgents folder redirected into the temp folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from slate import procs

SLOW_ENV = "SLATE_TEST_SLOW"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", f"slow: builds a real environment or runs a whole install (a minute or more); runs only with "
                   f"{SLOW_ENV}=1")


def pytest_collection_modifyitems(config, items):
    """Slow tests are opt-in, so the default run stays fast: `SLATE_TEST_SLOW=1 pytest …` runs them too.

    pytest hands this hook every collected item, whichever folder it's in, so it only touches items in this folder:
    another module's tests keep their own rules.
    """
    if os.environ.get(SLOW_ENV) == "1":
        return
    here = Path(__file__).resolve().parent
    skip = pytest.mark.skip(reason=f"slow (a real install into a fresh home); run with {SLOW_ENV}=1")
    for item in items:
        if "slow" in item.keywords and here in Path(str(item.fspath)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def sandboxed(monkeypatch, tmp_path):
    fake_home = tmp_path / "session-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("SLATE_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))
    monkeypatch.setenv("SLATE_NONINTERACTIVE", "1")
    for var in ("CODEX_HOME", "SLATE_ENGINE_DIR"):
        monkeypatch.delenv(var, raising=False)
    real_run = procs.run

    def guarded(argv, **kw):
        exe = str(argv[0])
        name = Path(exe).name
        if name in ("launchctl", "brew"):
            raise AssertionError(f"an install test tried to run {name} {list(argv[1:])}")
        if name in ("claude", "codex", "openclaw") and not exe.startswith(str(tmp_path)):
            raise AssertionError(f"an install test tried to run the real {name}: {exe}")
        return real_run(argv, **kw)

    monkeypatch.setattr(procs, "run", guarded)
    from slate.jobs import agent as job_agent

    def no_launchctl(*args):
        raise AssertionError(f"an install test tried to run launchctl {args}")

    monkeypatch.setattr(job_agent, "_launchctl", no_launchctl)
    return fake_home


@pytest.fixture
def fakebin(tmp_path):
    """A folder of fake agent CLIs, and a PATH holding only it and the system folders."""
    from install_helpers import install_fake_clients

    bin_dir = install_fake_clients(tmp_path / "fakebin")
    return bin_dir


@pytest.fixture
def bare_path(monkeypatch, tmp_path):
    """A PATH with no agent CLIs at all (so detection and MCP lookups find nothing real)."""
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", f"{empty}:/usr/bin:/bin:/usr/sbin:/sbin")
    return empty
