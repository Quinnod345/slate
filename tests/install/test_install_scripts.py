"""install.sh and friends: bash 3.2 syntax, dry runs that write nothing, and the refusals."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from install_helpers import REPO, run_script, snapshot

SCRIPTS = ["install.sh", "uninstall.sh", "scripts/setup_quinn_skills.sh", "scripts/fresh_home_test.sh"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_parse_with_macos_own_bash(script):
    """macOS's /bin/bash is 3.2, and `curl | bash` often gets it."""
    proc = subprocess.run(["/bin/bash", "-n", str(REPO / script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert os.access(REPO / script, os.X_OK), f"{script} should be executable"


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_never_open_a_browser_or_use_a_forbidden_route(script):
    text = (REPO / script).read_text()
    assert "export BROWSER=none" in text
    for forbidden in ("open http", "open -a", "osascript", "Pref_SCRIPTING", "npm install", "pip install --user"):
        assert forbidden not in text, f"{script} mentions {forbidden!r}"


def test_install_sh_dry_run_from_this_checkout_writes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    venv_before = (REPO / ".venv" / "pyvenv.cfg").stat().st_mtime
    proc = run_script(REPO / "install.sh", "--dry-run", "--home", str(home), "--agents", "claude,codex", home=home,
                      timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"Slate checkout: {REPO}" in proc.stdout
    assert "would run:" in proc.stdout and "pip install" in proc.stdout
    assert "Would install Slate" in proc.stdout and "then: slate doctor" in proc.stdout
    assert snapshot(home) == {}, "a dry run writes nothing into the home"
    assert (REPO / ".venv" / "pyvenv.cfg").stat().st_mtime == venv_before, "and nothing into the checkout's env"


def test_install_sh_piped_dry_run_only_says_it_would_clone(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    script = (REPO / "install.sh").read_text()
    proc = run_script_stdin(script, "--dry-run", home=home, env={"SLATE_REPO": f"file://{tmp_path}/nowhere"})
    assert proc.returncode == 0, proc.stderr
    assert "would run: git clone" in proc.stdout and "slate install --dry-run" in proc.stdout
    assert snapshot(home) == {}


def test_install_sh_leaves_a_folder_that_isnt_a_checkout_alone(tmp_path):
    home = tmp_path / "home"
    (home / "code" / "slate").mkdir(parents=True)
    (home / "code" / "slate" / "notes.txt").write_text("mine\n")
    proc = run_script_stdin((REPO / "install.sh").read_text(), home=home,
                            env={"SLATE_REPO": f"file://{tmp_path}/nowhere"})
    assert proc.returncode == 1
    assert "isn't a git checkout" in proc.stderr and "won't touch it" in proc.stderr
    assert (home / "code" / "slate" / "notes.txt").read_text() == "mine\n"


def test_install_sh_never_asks_in_an_agent(tmp_path):
    """With an agent's markers set and stdin a pipe, the installer runs straight through (here: a dry run)."""
    home = tmp_path / "home"
    home.mkdir()
    proc = run_script(REPO / "install.sh", "--dry-run", "--home", str(home), home=home, timeout=300,
                      env={"CLAUDECODE": "1", "SLATE_NONINTERACTIVE": ""}, stdin="y\ny\ny\n")
    assert proc.returncode == 0, proc.stderr
    assert "[y/N]" not in proc.stdout + proc.stderr and "[Y/n]" not in proc.stdout + proc.stderr


def run_script_stdin(script_text: str, *args: str, home, env=None) -> subprocess.CompletedProcess:
    """`curl ... | bash -s -- args`, with the script on stdin, macOS's bash, and a clean environment."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX_", "OPENCLAW"))}
    base.update({"HOME": str(home), "BROWSER": "none", "SLATE_NONINTERACTIVE": "1", "GIT_TERMINAL_PROMPT": "0"})
    base.update(env or {})
    return subprocess.run(["/bin/bash", "-s", "--", *args], input=script_text, capture_output=True, text=True,
                          env=base, timeout=120)


def test_fresh_home_script_documents_its_exit_codes():
    text = (REPO / "scripts/fresh_home_test.sh").read_text()
    assert "UNCHECKED is never a pass" in text and "exit 3" in text


@pytest.mark.skipif(shutil.which("git") is None, reason="git isn't installed")
def test_setup_script_help_is_there():
    proc = subprocess.run(["/bin/bash", str(REPO / "scripts/setup_quinn_skills.sh"), "--help"], capture_output=True,
                          text=True)
    assert proc.returncode == 0 and "--exclude-agent" in proc.stdout and "owner's yes" in proc.stdout


def test_the_fresh_home_checker_expects_the_commands_the_installer_shows():
    """The fresh-home test is slow and opt-in, so its embedded MCP checker is held to mcp_register here, every run.

    Its expected commands once said `openclaw mcp set` while the installer moved to OpenClaw 2026.9.4's `mcp add`;
    this keeps the two from drifting apart again.
    """
    import json as _json
    import re as _re

    from slate.install import mcp_register

    text = (REPO / "scripts/fresh_home_test.sh").read_text()
    block = text[text.index("want = {"):text.index("got = {")]
    for client in mcp_register.CLIENTS:
        m = _re.search(rf'"{client}": (\[.*?\])', block, _re.S)
        assert m, f"the fresh-home checker has no expected command for {client}"
        expected = _json.loads(m.group(1).replace("shim", '"SHIM"'))
        assert expected == mcp_register.add_argv(client, "SHIM"), (client, expected)
    assert '"set"' not in block, "OpenClaw registers with `mcp add`, not the older `mcp set`"
