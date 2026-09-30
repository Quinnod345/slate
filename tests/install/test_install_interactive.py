"""In a real Terminal the installer asks (on the terminal itself, never stdin), and only then runs doctor --install.

install.sh runs here on a pseudo-terminal, from a fake checkout whose `uv` and Python only log what they're asked,
so nothing is installed and no real environment is touched.
"""

from __future__ import annotations

import os
import pty
import select
import shutil
import time
from pathlib import Path

import pytest

from install_helpers import REPO
from slate.install import core

FAKE_PYTHON = """#!/bin/bash
# A stand-in for the checkout's Python: logs every `-m slate ...` call and says yes to the version check.
if [ "$1" = "-c" ]; then exit 0; fi
echo "python $*" >> "{log}"
exit 0
"""

FAKE_UV = """#!/bin/bash
echo "uv $*" >> "{log}"
exit 0
"""


@pytest.fixture
def fake_checkout(tmp_path):
    co = tmp_path / "checkout"
    (co / "src" / "slate").mkdir(parents=True)
    (co / "pyproject.toml").write_text("[project]\nname = 'slate'\n")
    shutil.copy(REPO / "install.sh", co / "install.sh")
    log = tmp_path / "calls.log"
    (co / ".venv" / "bin").mkdir(parents=True)
    (co / ".venv" / "bin" / "python").write_text(FAKE_PYTHON.format(log=log))
    (co / ".venv" / "bin" / "python").chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(FAKE_UV.format(log=log))
    (bin_dir / "uv").chmod(0o755)
    return co, bin_dir, log


def _run_on_a_terminal(argv: list[str], env: dict, answers: dict[str, str], timeout: float = 60) -> tuple[int, str]:
    """Run a command with a pseudo-terminal as its controlling terminal, answering questions as they appear."""
    pid, fd = pty.fork()
    if pid == 0:  # the child: become the command, and never fall back into the test runner
        try:
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)
    output = b""
    pending = dict(answers)
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:  # the child closed the terminal: it's done
                break
            if not chunk:
                break
            output += chunk
            for prompt in list(pending):
                if prompt.encode() in output:
                    os.write(fd, (pending.pop(prompt) + "\n").encode())
        elif os.waitpid(pid, os.WNOHANG) != (0, 0):
            break
    else:
        os.kill(pid, 9)
    _, status = os.waitpid(pid, 0) if pid else (0, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), output.decode(errors="replace")


def _env(home: Path, bin_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "CODEX_", "OPENCLAW")) and k not in ("SLATE_NONINTERACTIVE", "SLATE_CONTEXT")}
    env.update({"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "BROWSER": "none",
                "TERM": "dumb"})
    return env


def test_in_a_terminal_it_asks_before_doctor_install(fake_checkout, tmp_path):
    co, bin_dir, log = fake_checkout
    home = tmp_path / "home"
    home.mkdir()
    code, out = _run_on_a_terminal(["/bin/bash", str(co / "install.sh"), "--home", str(home)],
                                   _env(home, bin_dir), {"Run `slate doctor --install` now?": "y"})
    assert code == 0, out
    calls = log.read_text()
    assert "Run `slate doctor --install` now?" in out and "asks before each one" in out
    assert "-m slate install --no-doctor --interactive --home" in calls, calls
    assert "-m slate doctor --install" in calls


def test_saying_no_runs_plain_doctor_and_prints_the_next_step(fake_checkout, tmp_path):
    co, bin_dir, log = fake_checkout
    home = tmp_path / "home"
    home.mkdir()
    code, out = _run_on_a_terminal(["/bin/bash", str(co / "install.sh"), "--home", str(home)],
                                   _env(home, bin_dir), {"Run `slate doctor --install` now?": "n"})
    assert code == 0, out
    calls = log.read_text().splitlines()
    assert "python -m slate doctor" in calls and not any("doctor --install" in c for c in calls)
    assert "When you're at a Terminal, `slate doctor --install`" in out


def test_an_agent_on_a_terminal_still_isnt_asked(fake_checkout, tmp_path):
    co, bin_dir, log = fake_checkout
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home, bin_dir)
    env["CODEX_SANDBOX"] = "seatbelt"
    code, out = _run_on_a_terminal(["/bin/bash", str(co / "install.sh"), "--home", str(home)], env, {})
    assert code == 0, out
    assert "[y/N]" not in out and "--interactive" not in log.read_text()


def test_the_python_side_reads_answers_from_the_terminal(monkeypatch):
    import io

    class FakeTTY(io.StringIO):
        def __init__(self, answer):
            super().__init__(answer)
            self.asked = ""

        def write(self, text):
            self.asked += text
            return len(text)

    def fake_open(answer):
        def _open(path, mode="r", *a, **kw):
            assert path == "/dev/tty", "questions go to the terminal, never stdin"
            return FakeTTY(answer)
        return _open

    monkeypatch.setattr(core, "open", fake_open("y\n"), raising=False)
    assert core.ask("Go? [y/N] ") is True
    monkeypatch.setattr(core, "open", fake_open("\n"), raising=False)
    assert core.ask("Go? [Y/n] ", default=True) is True
    assert core.ask("Go? [y/N] ") is False
    monkeypatch.setattr(core, "open", fake_open("no\n"), raising=False)
    assert core.ask("Go? [Y/n] ", default=True) is False

    def no_tty(*a, **kw):
        raise OSError("Device not configured")

    monkeypatch.setattr(core, "open", no_tty, raising=False)
    assert core.ask("Go?", default=True) is False, "no terminal means no, never a guess"
