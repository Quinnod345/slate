"""How Slate tells whether aerender is running."""

from __future__ import annotations


def test_adobes_ipc_broker_isnt_taken_for_a_running_aerender(monkeypatch):
    """The broker stays up after a render with ".../aerendercore.app/Contents/MacOS/aerendercore" in its arguments;
    only a process named aerender or aerendercore counts."""
    import subprocess
    from types import SimpleNamespace

    from slate import procs

    running = {"names": set()}
    broker = ("AdobeIPCBroker -launchedbyvulcan /Applications/Adobe After Effects 2026/Adobe After Effects 2026.app/"
              "Contents/aerendercore.app/Contents/MacOS/aerendercore")

    def fake_run(args, **kw):
        flag, pattern = args[1], args[2]
        hit = (pattern in broker) if flag == "-f" else (pattern in running["names"])
        return SimpleNamespace(returncode=0 if hit else 1, stdout="4242\n" if hit else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert procs.aerender_running() is False  # only the broker is up
    running["names"] = {"aerendercore"}
    assert procs.aerender_running() is True
    running["names"] = {"aerender"}
    assert procs.aerender_running() is True
