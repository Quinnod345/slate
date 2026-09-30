"""Renders are local, always (plan point P341: the WORK render farm is dropped, and WORK is never used for renders).

The films briefly rendered on the WORK Mac through a shell script that mirrored the project over the network and ran
Remotion there. That route is retired: Quinn works on WORK. Slate keeps no remote backend at all, and this test keeps
it that way by scanning every file in the render package for the tools and names that route used.
"""

import re
from pathlib import Path

import slate.render
from slate import paths
from slate.cli import run_cli
from slate.render import engines, plan

PACKAGE = Path(slate.render.__file__).parent
FORBIDDEN = [
    (re.compile(r"\bssh\b", re.I), "ssh"),
    (re.compile(r"\brsync\b", re.I), "rsync"),
    (re.compile(r"\bscp\b", re.I), "scp"),
    (re.compile(r"work\.sh", re.I), "the films' remote render script"),
    (re.compile(r"tailscale", re.I), "tailscale"),
    (re.compile(r"mirror.*(remote|ssh)|remote.*mirror", re.I), "a remote mirror of the project"),
    (re.compile(r"--remote|render[-_]remote|remote_render", re.I), "a remote render option"),
    (re.compile(r"""["']work["']""", re.I), "a host called work"),
    (re.compile(r"paramiko|fabric|asyncssh", re.I), "an ssh library"),
]


def _files():
    files = [p for p in PACKAGE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files.append(paths.SCHEMAS_DIR / "renders_registry.schema.json")
    return files


def test_the_render_package_has_no_remote_render_code():
    hits = []
    for f in _files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for pattern, what in FORBIDDEN:
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{f.relative_to(paths.REPO)}:{line}: {what}")
    assert hits == [], "remote render code crept in:\n" + "\n".join(hits)


def test_the_scan_sees_every_module():
    names = {p.name for p in _files()}
    assert {"runner.py", "engines.py", "cli.py", "splice.py", "clean.py", "renders_registry.schema.json"} <= names


def test_the_engines_are_the_three_local_ones():
    assert set(engines.ENGINES) == {"remotion", "web", "ae"} == set(plan.ENGINES)


def test_no_remote_flag_on_the_command():
    _code, out, _err = run_cli(["render", "--help"])
    assert "remote" not in out.lower() and "work mac" not in out.lower()
