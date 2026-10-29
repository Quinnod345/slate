"""The CLI's surface and the foundation's contracts with the job queue.

- Every module in slate.cli.MODULES registers exactly the commands it says, once; nothing is broken; `slate --help`
  lists every command.
- With --json, a usage error is one JSON object on stdout, like every other outcome.
- SIGTERM (or SIGHUP) to a running command stops what it started: the signal unwinds the command, so
  slate.procs.run's cleanup stops the child's whole process group.
- A rate limit (429) or an unavailable service (503) is TryLater, exit 75, which the queue retries after the vendor's
  Retry-After; it's still an HttpError for vendors that map statuses themselves.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from slate import cli, http, procs
from slate.cli import run_cli
from slate.errors import SlateError, TryLater

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------------------------- the module list
def test_every_module_registers_exactly_its_commands_once():
    cli.build_parser()
    assert cli.BROKEN == {}, cli.BROKEN
    by_module: dict[str, list[str]] = {}
    for command, module in cli.REGISTERED.items():
        by_module.setdefault(module, []).append(command)
    for module, what in cli.MODULES:
        said = sorted(w.strip() for w in what.split(","))
        assert sorted(by_module.get(module, [])) == said, f"{module} adds {by_module.get(module)}, MODULES says {said}"
    names = [w.strip() for _m, what in cli.MODULES for w in what.split(",")]
    assert len(names) == len(set(names)), "a command is listed under two modules"
    assert set(names) == set(cli.REGISTERED)


def test_help_lists_every_command():
    code, out, err = run_cli(["--help"])
    assert code == 0
    listed = {line.split()[0] for line in out.splitlines() if line.startswith("    ") and line.split()}
    missing = sorted(set(cli.REGISTERED) - listed)
    assert not missing, f"`slate --help` doesn't list {missing}"


def test_finish_is_described_as_what_it_registers():
    assert dict(cli.MODULES)["slate.finish.cli"] == "master, deliver, review, notes, approved"
    assert cli.REGISTERED["variant"] == "slate.state.cli" and cli.REGISTERED["formats"] == "slate.formats"
    assert cli.REGISTERED["install"] == cli.REGISTERED["uninstall"] == "slate.install.cli"


# ---------------------------------------------------------------------------------------------- --json usage errors
@pytest.mark.parametrize("argv", [
    ["--json", "no-such-command"],
    ["qa", "--json", "--no-such-flag"],
    ["approve", "--json"],  # its arguments are missing
])
def test_usage_errors_are_one_json_object_under_json(argv):
    code, out, err = run_cli(argv)
    assert code == 2
    obj = json.loads(out)  # exactly one object on stdout: json.loads fails on anything else
    assert obj["error"] == "usage" and obj["exit_code"] == 2 and obj["message"] and obj["hint"].endswith("--help")
    assert "usage:" in obj["usage"]


def test_a_group_without_its_stage_and_no_command_at_all_are_json_too():
    code, out, _ = run_cli(["voice", "--json"])
    obj = json.loads(out)
    assert code == 2 and obj["error"] == "usage" and "tts" in obj["stages"]
    code, out, _ = run_cli(["--json"])
    assert code == 2 and json.loads(out)["message"] == "no command given"


def test_without_json_usage_errors_stay_plain_text():
    code, out, err = run_cli(["qa", "--no-such-flag"])
    assert code == 2 and out == "" and "unrecognized arguments" in err


# ---------------------------------------------------------------------------------------------- SIGTERM
CHILD = r"""
import json, os, signal, subprocess, sys, time, types
# a terminal's own state, whatever started the tests: under nohup SIGHUP arrives ignored, and Slate keeps it ignored
signal.signal(signal.SIGHUP, signal.SIG_DFL)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
sys.path.insert(0, {src!r})
from slate import cli, procs

def handler(args):
    marker = {marker!r}
    script = ("import subprocess, sys, time; "
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
              f"open({{marker!r}}, 'w').write(str(p.pid)); time.sleep(120)")
    procs.run([sys.executable, "-c", script], timeout=300)
    return 0

def register(sub):
    cli.add_command(sub, "sleepy", handler, "a long command for the signal test")

mod = types.ModuleType("slate_test_sleepy")
mod.register = register
sys.modules["slate_test_sleepy"] = mod
cli.MODULES = [("slate_test_sleepy", "sleepy")]
sys.exit(cli.main(["sleepy"]))
"""


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_stop_signal_stops_everything_the_command_started(tmp_path, signum):
    marker = tmp_path / "grandchild.pid"
    script = CHILD.format(src=str(REPO / "src"), marker=str(marker))
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, env={**os.environ, "BROWSER": "none"})
    try:
        deadline = time.monotonic() + 60
        while not (marker.exists() and marker.read_text().strip()):
            assert proc.poll() is None, proc.communicate()
            assert time.monotonic() < deadline, "the command never started its child"
            time.sleep(0.05)
        grandchild = int(marker.read_text())
        assert procs.pid_alive(grandchild)
        proc.send_signal(signum)
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 128 + signum, err
        assert f"stopped by {signal.Signals(signum).name}" in err
        for _ in range(100):
            if not procs.pid_alive(grandchild):
                break
            time.sleep(0.05)
        assert not procs.pid_alive(grandchild), "the grandchild outlived the command"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_in_process_runs_put_the_old_handlers_back():
    before = signal.getsignal(signal.SIGTERM)
    code, _, _ = run_cli(["--version"])
    assert code == 0 and signal.getsignal(signal.SIGTERM) is before


# ---------------------------------------------------------------------------------------------- try later (75)
def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.example.com/v1/x", code, "nope", headers, io.BytesIO(b'{"detail": "slow"}'))


@pytest.mark.parametrize("code", [429, 503])
def test_a_billing_call_turned_away_is_try_later_and_never_resent(monkeypatch, code):
    calls = []

    def urlopen(req, timeout=60, **kw):
        calls.append(req.get_method())
        raise _http_error(code, "17")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(http.RateLimited) as exc:
        http.request("POST", "https://api.example.com/v1/x", json_body={"a": 1})
    assert calls == ["POST"]  # a call that bills is never sent twice by slate.http
    err = exc.value
    assert isinstance(err, TryLater) and isinstance(err, http.HttpError) and isinstance(err, SlateError)
    assert err.exit_code == 75 and err.status == code and err.retry_after_s == 17.0
    assert err.to_dict()["error"] == "try_later" and err.details["retry_after_s"] == 17.0


def test_a_read_retries_then_says_try_later(monkeypatch):
    calls = []
    monkeypatch.setattr(http, "_sleep", lambda attempt, retry_after: None)

    def urlopen(req, timeout=60, **kw):
        calls.append(req.get_method())
        raise _http_error(429)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(http.RateLimited) as exc:
        http.request("GET", "https://api.example.com/v1/x", retries=2)
    assert calls == ["GET"] * 3 and exc.value.retry_after_s is None


def test_a_proxys_tunnel_refusal_fails_a_read_at_once(monkeypatch):
    """A proxy that turns the tunnel away (the OpenClaw gateway's egress proxy for a host it doesn't list) answers the
    same on every try, so a read isn't retried with backoff: one attempt, no sleep, and the proxy's words kept for
    slate.vendors.egress to read."""
    calls, slept = [], []
    monkeypatch.setattr(http, "_sleep", lambda attempt, retry_after: slept.append(attempt))

    def urlopen(req, timeout=60, **kw):
        calls.append(req.get_method())
        raise urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(SlateError) as exc:
        http.request("GET", "https://api.example.com/v1/x", retries=3)
    assert calls == ["GET"] and slept == []
    assert "Tunnel connection failed: 403" in exc.value.message and exc.value.details == {"tunnel_refused": True}
    assert "proxy" in exc.value.hint

    from slate.vendors import egress

    assert egress.tunnel_refusal(exc.value) == 403  # the egress module still recognises it


def test_a_plain_connection_error_on_a_read_is_still_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(http, "_sleep", lambda attempt, retry_after: None)

    def urlopen(req, timeout=60, **kw):
        calls.append(req.get_method())
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(SlateError) as exc:
        http.request("GET", "https://api.example.com/v1/x", retries=2)
    assert calls == ["GET"] * 3 and "tunnel_refused" not in exc.value.details


def test_other_errors_stay_plain_http_errors(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=60, **kw: (_ for _ in ()).throw(_http_error(400)))
    with pytest.raises(http.HttpError) as exc:
        http.request("POST", "https://api.example.com/v1/x", json_body={})
    assert not isinstance(exc.value, TryLater) and exc.value.exit_code == 1


def test_retry_after_reads_seconds_and_dates():
    assert http.retry_after_seconds("12") == 12.0
    assert http.retry_after_seconds(None) is None and http.retry_after_seconds("soon") is None
    assert http.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # in the past: no wait


def test_a_queued_command_that_exits_75_tells_its_job_how_long_to_wait(slate_home, monkeypatch):
    """slate.cli notes the vendor's Retry-After on the job (exit 75); the supervisor's backoff honours it."""
    import types

    from slate.jobs import client, model, shim, store

    job_id = client.submit("test", [sys.executable, "-c", "pass"], start_runner=False)
    with store.update(job_id) as job:
        job["state"] = "running"

    def handler(args):
        raise TryLater("the vendor is busy", retry_after_s=600)

    mod = types.ModuleType("slate_test_busy")
    mod.register = lambda sub: cli.add_command(sub, "busy-vendor", handler, "always rate limited")
    monkeypatch.setitem(sys.modules, "slate_test_busy", mod)
    monkeypatch.setattr(cli, "MODULES", [("slate_test_busy", "busy-vendor")])
    monkeypatch.setenv("SLATE_JOB_ID", job_id)
    code, out, err = run_cli(["busy-vendor"])
    assert code == 75 and "the vendor is busy" in err
    noted = store.load(job_id)["try_later"]
    assert noted["retry_after_s"] == 600 and noted["reason"] == "the vendor is busy"
    with store.update(job_id) as job:
        shim._outcome(job, 75)
        assert job["state"] == "queued" and "try_later" not in job
        wait = (model.parse_iso(job["not_before"]) - model.now()).total_seconds()
        assert 590 <= wait <= 601, wait  # the vendor's 600 s, not the queue's first 60 s
        assert "the vendor is busy" in job["reason"]


def test_signal_exit_codes_name_their_signal():
    from slate.jobs import proc

    assert proc.signal_name(143) == "SIGTERM" and proc.signal_name(129) == "SIGHUP" and proc.signal_name(-9) == "SIGKILL"
    assert proc.signal_name(1) is None and proc.signal_name(75) is None


def test_procs_run_is_untouched_by_the_handlers():
    """The handlers only exist while a command runs; procs.run itself still stops a group on timeout."""
    assert procs.run([sys.executable, "-c", "print('ok')"], check=True).stdout.strip() == "ok"


SEAL = r"""
import ctypes, sys
sys.path.insert(0, {src!r})
from slate import log
log.set_json(True)
libc = ctypes.CDLL(None)
libc.printf(b"native noise, buffered in C stdio before the object\n")
log.emit({{"ok": True}})
log.seal_stdout()
libc.printf(b"native noise after it\n")
"""


def test_a_json_commands_stdout_is_sealed_once_its_object_is_out():
    """Native code prints through C stdio (Apple Vision does), buffered and flushed at exit: once a --json command's
    object is out, fd 1 points at stderr, so that text lands there and stdout stays exactly one object."""
    # This case deliberately creates buffered C output. The queue runs Python unbuffered, which also makes libc's
    # stdout unbuffered: inherited here, that would print the first line before the seal can exist.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    proc = subprocess.run([sys.executable, "-c", SEAL.format(src=str(REPO / "src"))], capture_output=True, text=True,
                          env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True}
    assert "buffered in C stdio before" in proc.stderr and "after it" in proc.stderr
