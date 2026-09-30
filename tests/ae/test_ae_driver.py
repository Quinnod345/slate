"""The AE driver: osascript command construction (with a fake osascript), the report round trip, error mapping,
and the one lock shared with the AE bridge."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

from slate.ae import bridgelock, driver, report
from slate.errors import Busy, MissingPrerequisite, SlateError, UsageError

from ae_helpers import completed, prefs_bytes, settings_entry


def _jsx(tmp_path, name="probe.jsx"):
    p = tmp_path / name
    p.write_text("(function () { return 'ok'; }());\n")
    return p


class FakeAE:
    """Stands in for osascript + After Effects: reads the wrapper, 'runs' it, writes a report into the prefs file."""

    def __init__(self, prefs_file: Path, *, status="OK", data=None, returncode=0, stderr="", write=True):
        self.prefs_file, self.status, self.data = prefs_file, status, data
        self.returncode, self.stderr, self.write = returncode, stderr, write
        self.calls: list[dict] = []

    def __call__(self, argv, timeout=None, **kw):
        tell = argv[4]
        wrapper = Path(re.search(r'DoScriptFile "(.*)"$', tell).group(1))
        src = wrapper.read_text()
        args = json.loads(re.search(r"SLATE_ARGS = (.*);\n", src).group(1))
        run = json.loads(re.search(r"SLATE_RUN = (.*);\n", src).group(1))
        target = json.loads(re.search(r"evalFile\(new File\((.*)\)\);", src).group(1))
        self.calls.append({"argv": argv, "timeout": timeout, "args": args, "run": run, "target": target,
                           "locks": [p for p in Path(os.environ["TMPDIR"]).rglob("dispatcher-busy.lock")]})
        if self.write:
            entries = report.encode_for_settings(self.data if self.data is not None else {"echo": args},
                                                 run_id=run["run_id"], status=self.status, channel=run["channel"])
            self.prefs_file.write_bytes(prefs_bytes(report=[settings_entry(k, v) for k, v in entries.items()]))
        return completed(argv, self.returncode, "0\n", self.stderr)


@pytest.fixture
def ae(live_prefs):
    return FakeAE(live_prefs())


# ---- command construction ------------------------------------------------------------------------------------


def test_osascript_argv_is_doscriptfile_in_a_timeout_block(tmp_path):
    argv = driver.osascript_argv(tmp_path / 'we"ird\\name.jsx', app="Adobe After Effects 2099", timeout_s=900)
    assert argv[0] == "/usr/bin/osascript"
    assert argv[1:3] == ["-e", "with timeout of 900 seconds"]
    assert argv[4] == ('tell application "Adobe After Effects 2099" to DoScriptFile "'
                       + str(tmp_path).replace("\\", "\\\\") + '/we\\"ird\\\\name.jsx"')
    assert argv[5:] == ["-e", "end timeout"]


def test_app_name_comes_from_env_then_config_then_the_newest_install(monkeypatch, ae_isolation):
    (ae_isolation["apps"] / "Adobe After Effects 2025" / "Adobe After Effects 2025.app").mkdir(parents=True)
    assert driver.app_name() == "Adobe After Effects 2099"
    assert driver.aerender_path().endswith("Adobe After Effects 2099/aerender")
    monkeypatch.setenv("AE_APP_NAME", "Adobe After Effects (Beta)")
    assert driver.app_name() == "Adobe After Effects (Beta)"


def test_no_after_effects_is_a_missing_prerequisite(monkeypatch, tmp_path):
    monkeypatch.setattr(driver, "APPS_DIR", tmp_path / "nothing")
    with pytest.raises(MissingPrerequisite) as exc:
        driver.app_name()
    assert exc.value.exit_code == 3


def test_wrapper_pins_args_and_evaluates_the_target(tmp_path):
    src = driver.wrapper_source(tmp_path / "x.jsx", {"spec": "/a b/c.json", "n": " "}, {"run_id": "r1"})
    assert "$.global.SLATE_ARGS = " in src and "\\u2028" in src
    assert src.strip().endswith(f'$.evalFile(new File("{tmp_path}/x.jsx"));')


# ---- running ---------------------------------------------------------------------------------------------------


def test_run_jsx_round_trip_through_the_settings_channel(tmp_path, live_prefs):
    ae = FakeAE(live_prefs(data=prefs_bytes(scripting=b'"0"')))
    res = driver.run_jsx(_jsx(tmp_path), args={"spec": "s.json"}, runner=ae)
    assert res["ok"] and res["status"] == "OK" and res["report_channel"] == "settings"
    assert res["report"] == {"echo": {"spec": "s.json"}}
    call = ae.calls[0]
    assert call["target"] == str(_jsx(tmp_path).resolve())
    assert call["run"]["section"] == "Slate" and call["run"]["report_file"] is None  # scripting pref off -> no file
    assert call["timeout"] == driver.DEFAULT_TIMEOUT_S + 30
    assert (Path(res["run_dir"]) / "wrapper.jsx").is_file()


def test_run_jsx_asks_for_a_file_report_only_when_the_pref_is_on(tmp_path, live_prefs):
    f = live_prefs(data=prefs_bytes(scripting=b'"1"'))
    fake = FakeAE(f)
    driver.run_jsx(_jsx(tmp_path), runner=fake)
    assert fake.calls[0]["run"]["report_file"].endswith("report.json")
    f.write_bytes(prefs_bytes(scripting=b'"0"'))
    driver.run_jsx(_jsx(tmp_path), runner=fake)
    assert fake.calls[1]["run"]["report_file"] is None


def test_run_jsx_reports_missing_when_nothing_came_back(tmp_path, live_prefs):
    fake = FakeAE(live_prefs(), write=False)
    res = driver.run_jsx(_jsx(tmp_path), runner=fake)
    assert res["status"] == "MISSING" and not res["ok"]
    with pytest.raises(SlateError, match="didn't report back"):
        driver.expect_ok(res, "Smoke")


def test_run_jsx_maps_a_denied_automation_permission(tmp_path, live_prefs):
    fake = FakeAE(live_prefs(), returncode=1, stderr="execution error: Not authorized to send Apple events (-1743)")
    with pytest.raises(MissingPrerequisite) as exc:
        driver.run_jsx(_jsx(tmp_path), runner=fake)
    assert "Automation" in exc.value.hint


def test_run_jsx_maps_a_timeout(tmp_path, live_prefs):
    fake = FakeAE(live_prefs(), returncode=1, stderr="AppleEvent timed out. (-1712)")
    with pytest.raises(SlateError, match="didn't answer in time"):
        driver.run_jsx(_jsx(tmp_path), runner=fake)


def test_run_jsx_needs_a_real_file(tmp_path):
    with pytest.raises(UsageError):
        driver.run_jsx(tmp_path / "nope.jsx", runner=lambda *a, **k: None)


def test_expect_ok_turns_an_unsaved_project_abort_into_busy():
    with pytest.raises(Busy) as exc:
        driver.expect_ok({"status": "ABORT open project has unsaved changes"}, "The smoke test")
    assert exc.value.exit_code == 5
    with pytest.raises(SlateError, match="ERROR boom"):
        driver.expect_ok({"status": "ERROR boom"}, "The build")


def test_run_jsx_holds_the_bridge_lock_while_ae_runs(tmp_path, ae, monkeypatch):
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    driver.run_jsx(_jsx(tmp_path), runner=ae)
    held = ae.calls[0]["locks"]
    assert [p.name for p in held] == ["dispatcher-busy.lock"]
    assert not held[0].exists()  # released afterwards


def test_small_operations_use_slate_scripts(tmp_path, ae):
    ae.data = {"file": "(untitled)", "dirty": False}
    assert driver.open_project_state(runner=ae) == {"file": "(untitled)", "dirty": False}
    assert ae.calls[-1]["target"].endswith("engine/ae/open_project.jsx")
    driver.purge(runner=ae)
    assert ae.calls[-1]["target"].endswith("engine/ae/purge.jsx")
    driver.set_software_gpu(tmp_path / "f.aep", runner=ae)
    assert ae.calls[-1]["args"]["aep"].endswith("f.aep")
    driver.font_check(["Inter", "Noto Sans"], runner=ae)
    assert ae.calls[-1]["args"]["pattern"] == "^(Inter|Noto\\ Sans)$"


# ---- the lock ---------------------------------------------------------------------------------------------------


def test_slate_side_lock_is_exclusive(slate_home):
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with driver.ae_lock(bridge=False):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert entered.wait(5)
    try:
        with pytest.raises(Busy) as exc:
            with driver.ae_lock(timeout=0.3, bridge=False):
                pass
        assert exc.value.exit_code == 5
    finally:
        release.set()
        t.join(5)
    with driver.ae_lock(timeout=1, bridge=False) as info:
        assert "ae" in info["slate"]


def test_local_fallback_lock_when_jobs_is_missing(monkeypatch, slate_home):
    import sys

    monkeypatch.setitem(sys.modules, "slate.jobs", None)  # import fails: the jobs module isn't installed
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with driver.ae_lock(bridge=False):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert entered.wait(5)
    try:
        with pytest.raises(Busy):
            with driver.ae_lock(timeout=0.3, bridge=False):
                pass
    finally:
        release.set()
        t.join(5)
    with driver.ae_lock(timeout=1, bridge=False) as info:
        assert info["slate"].endswith("ae.lock")
    monkeypatch.setenv("SLATE_JOB_HOLDS", "render,ae")
    with driver.ae_lock(bridge=False) as info:
        assert info["slate"] == "held-by-job"


def test_a_job_that_already_holds_ae_is_not_blocked(monkeypatch, slate_home):
    monkeypatch.setenv("SLATE_JOB_HOLDS", "render,ae")
    with driver.ae_lock(bridge=False) as info:
        with driver.ae_lock(bridge=False, timeout=0.2) as inner:
            assert "delegated=True" in info["slate"] and "delegated=True" in inner["slate"]


def test_the_jobs_lock_is_used_when_the_jobs_module_exists(monkeypatch, slate_home):
    import contextlib
    import sys
    import types

    seen = []

    @contextlib.contextmanager
    def hold(resource, *, timeout=None, owner=None):
        seen.append((resource, owner))
        yield "job-token"

    locks = types.ModuleType("slate.jobs.locks")
    locks.hold = hold
    jobs = types.ModuleType("slate.jobs")
    jobs.locks = locks
    monkeypatch.setitem(sys.modules, "slate.jobs", jobs)
    monkeypatch.setitem(sys.modules, "slate.jobs.locks", locks)
    with driver.ae_lock(bridge=False, owner="job-7") as info:
        assert info["slate"] == "job-token"
    assert seen == [("ae", "job-7")]


def _mailbox():
    return Path(os.environ["TMPDIR"]) / "mcp-aftereffects" / "runtime"


def test_bridge_lock_follows_the_bridge_protocol(monkeypatch):
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    with bridgelock.hold(timeout=1) as held:
        assert held == [_mailbox() / "dispatcher-busy.lock"]
        info = json.loads(held[0].read_text())
        assert info["pid"] == os.getpid() and info["id"].startswith("slate-") and info["startedAt"].endswith("Z")
        assert oct(_mailbox().stat().st_mode & 0o777) == "0o700"
    assert not held[0].exists()


def test_bridge_lock_waits_for_the_bridge_then_is_busy(monkeypatch):
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    _mailbox().mkdir(parents=True)
    lock = _mailbox() / "dispatcher-busy.lock"
    lock.write_text(json.dumps({"pid": 1, "id": "bridge-call", "startedAt": "now"}))
    started = time.monotonic()
    with pytest.raises(Busy) as exc:
        with bridgelock.hold(timeout=0.3):
            pass
    assert time.monotonic() - started >= 0.3
    assert "AE bridge" in exc.value.message
    assert json.loads(lock.read_text())["id"] == "bridge-call"  # never broken while fresh


def test_bridge_lock_breaks_a_stale_lock(monkeypatch):
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    _mailbox().mkdir(parents=True)
    lock = _mailbox() / "dispatcher-busy.lock"
    lock.write_text(json.dumps({"pid": 1, "id": "crashed", "startedAt": "then"}))
    old = time.time() - 120
    os.utime(lock, (old, old))
    with bridgelock.hold(timeout=1) as held:
        assert json.loads(held[0].read_text())["id"].startswith("slate-")


def test_bridge_lock_releases_only_its_own_lock(monkeypatch):
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    with bridgelock.hold(timeout=1) as held:
        held[0].write_text(json.dumps({"pid": 2, "id": "someone-else"}))  # our lock was broken and retaken
    assert json.loads(held[0].read_text())["id"] == "someone-else"


def test_bridge_lock_refreshes_while_held(monkeypatch):
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    with bridgelock.hold(timeout=1, refresh_s=0.05) as held:
        old = time.time() - 60
        os.utime(held[0], (old, old))
        time.sleep(0.3)
        assert time.time() - held[0].stat().st_mtime < 5


def test_no_bridge_no_mailbox_nothing_created():
    with bridgelock.hold(timeout=1) as held:
        assert held == []
    assert not (Path(os.environ["TMPDIR"]) / "mcp-aftereffects").exists()


def test_runtime_dirs_cover_env_pointer_and_temp_dirs(monkeypatch, tmp_path):
    custom = tmp_path / "custom-mailbox"
    pointed = tmp_path / "pointed-mailbox"
    base = Path(os.environ["TMPDIR"]) / "mcp-aftereffects"
    base.mkdir(parents=True)
    (base / "runtime-dir.txt").write_text(str(pointed) + "\n")
    monkeypatch.setenv("AE_MCP_RUNTIME_DIR", str(custom))
    other = tmp_path / "per-user-T"
    monkeypatch.setattr(bridgelock, "darwin_user_temp", lambda: other)
    dirs = bridgelock.runtime_dirs()
    assert dirs == [custom, pointed, base / "runtime", other / "mcp-aftereffects" / "runtime"]


def test_node_tmpdir_matches_node(monkeypatch):
    monkeypatch.setenv("TMPDIR", "/var/folders/xy/T/")
    assert str(bridgelock.node_tmpdir()) == "/var/folders/xy/T"
    monkeypatch.delenv("TMPDIR")
    assert str(bridgelock.node_tmpdir()) == "/tmp"
