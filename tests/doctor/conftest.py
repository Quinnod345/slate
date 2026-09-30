"""Fixtures for doctor and connect: a fake Mac per test, and nothing real ever runs.

- `denv` is a FakeEnv (doctor_helpers.py) whose defaults are a fully set-up Mac; `slate doctor` and `slate connect`
  run in-process against it (core.make_env is patched).
- The real After Effects prefs file, Apple's sound library and LaunchAgents folder are never reached: their override
  variables point at empty temp folders.
- Any real child process (slate.procs.run) or real key prompt fails the test: a doctor test that reached one would be
  touching this Mac.
"""

from __future__ import annotations

import pytest

from slate import procs, secrets
from slate.doctor import core

from doctor_helpers import FakeEnv


@pytest.fixture(autouse=True)
def doctor_isolation(tmp_path, monkeypatch):
    for var, name in (("SLATE_AE_PREFS_ROOT", "ae-prefs-root"), ("SLATE_APPLE_LIBRARY", "apple-library"),
                      ("SLATE_LAUNCH_AGENTS", "launch-agents")):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setenv(var, str(folder))
    monkeypatch.delenv("SLATE_AE_PREFS_FILE", raising=False)
    monkeypatch.delenv("SLATE_MODE", raising=False)

    def no_real_process(argv, **kw):
        raise AssertionError(f"a doctor test tried to run a real process: {list(map(str, argv))}")

    def no_real_prompt(name):
        raise AssertionError(f"a doctor test tried to prompt for the {name} key for real")

    monkeypatch.setattr(procs, "run", no_real_process)
    monkeypatch.setattr(secrets, "store_interactive", no_real_prompt)


@pytest.fixture
def denv(tmp_path, slate_home, monkeypatch):
    env = FakeEnv(tmp_path / "mac")
    monkeypatch.setattr(core, "make_env", lambda: env)
    return env
