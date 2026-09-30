"""Fixtures for the After Effects lane.

Every test here runs fully isolated from the Mac it runs on:

- AE's real prefs file is never read or written: `SLATE_AE_PREFS_ROOT` points at a temp folder (empty unless a test
  builds a synthetic prefs file there), so any accidental "live" lookup finds a stand-in or nothing.
- The AE bridge's real mailbox is never touched: TMPDIR, AE_MCP_RUNTIME_DIR and the macOS per-user temp dir all point
  into the test's temp folder.
- Whether AE or aerender is "running" is decided by the test (both default to not running).
- No osascript, aerender, ffmpeg or npm ever runs: tests pass fake runners.

The synthetic prefs files follow the format the film pipeline's ae_pref.py and read_ae_report.py parse (CR line
endings, `["Section"]` headers, TAB-quoted entries, TAB-TAB continuation lines, quoted runs and hex bytes). They are
written from that description, never copied from a real prefs file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slate import procs
from slate.ae import bridgelock

from ae_helpers import prefs_bytes

@pytest.fixture(autouse=True)
def ae_isolation(tmp_path, monkeypatch):
    """Keep every AE test away from the real prefs file, the real bridge mailbox and the real AE process."""
    root = tmp_path / "ae-prefs-root"
    root.mkdir()
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    apps = tmp_path / "Applications"
    (apps / "Adobe After Effects 2099" / "Adobe After Effects 2099.app").mkdir(parents=True)
    aer = apps / "Adobe After Effects 2099" / "aerender"
    aer.write_text("#!/bin/sh\nexit 0\n")
    aer.chmod(0o755)
    monkeypatch.setenv("SLATE_AE_PREFS_ROOT", str(root))
    monkeypatch.delenv("SLATE_AE_PREFS_FILE", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmpdir))
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.delenv("AE_MCP_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("AE_APP_NAME", raising=False)
    monkeypatch.delenv("SLATE_MODE", raising=False)
    monkeypatch.delenv("SLATE_HELD_LOCKS", raising=False)
    monkeypatch.setattr(bridgelock, "darwin_user_temp", lambda: None)
    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: False)
    monkeypatch.setattr(procs, "ae_running", lambda: False)
    monkeypatch.setattr(procs, "aerender_running", lambda: False)
    real_run = procs.run

    def guarded_run(argv, *args, **kw):
        # A safety net: nothing in these tests may reach the real AE, aerender, ffmpeg or npm, even by mistake.
        name = Path(str(argv[0])).name
        if name in ("osascript", "aerender", "ffmpeg", "ffprobe", "npm", "fnf-after-effects", "claude", "codex",
                    "open"):
            raise AssertionError(f"a test tried to run {name} for real")
        return real_run(argv, *args, **kw)

    monkeypatch.setattr(procs, "run", guarded_run)
    from slate.ae import driver  # noqa: PLC0415
    monkeypatch.setattr(driver, "APPS_DIR", apps)
    return {"prefs_root": root, "tmpdir": tmpdir, "apps": apps}


@pytest.fixture
def live_prefs(ae_isolation):
    """A stand-in for AE's live prefs file: <root>/26.5/Adobe After Effects 26.5 Prefs.txt (plus older versions)."""
    root: Path = ae_isolation["prefs_root"]

    def make(version: str = "26.5", data: bytes | None = None) -> Path:
        d = root / version
        d.mkdir(exist_ok=True)
        f = d / f"Adobe After Effects {version} Prefs.txt"
        f.write_bytes(prefs_bytes() if data is None else data)
        return f

    return make
