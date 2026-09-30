"""Slate's Python environment: the checkout's own `.venv`, made and kept current with uv.

install.sh builds it the first time (it has to, before any Python can run); `slate update` calls `sync` again when a
pull changed the package's dependencies. The `ocr` extra (ocrmac, Apple Vision) is part of a normal install, because
the one OCR engine Slate's gates trust lives in this environment; when it can't be installed, the base package still
is, and the OCR gates report UNCHECKED rather than passing quietly.
"""

from __future__ import annotations

from pathlib import Path

from slate import procs
from slate.errors import MissingPrerequisite

PYTHON_REQUEST = ">=3.11"
EXTRAS = ("ocr",)


def venv_python(repo: Path) -> Path:
    return Path(repo) / ".venv" / "bin" / "python"


def commands(repo: Path, *, fresh: bool | None = None) -> list[list[str]]:
    """The uv commands that make (or refresh) the environment, as a person would type them."""
    repo = Path(repo)
    fresh = (not venv_python(repo).exists()) if fresh is None else fresh
    cmds = []
    if fresh:
        cmds.append(["uv", "venv", "--python", PYTHON_REQUEST, str(repo / ".venv")])
    spec = f"{repo}[{','.join(EXTRAS)}]" if EXTRAS else str(repo)
    cmds.append(["uv", "pip", "install", "--python", str(venv_python(repo)), "-e", spec])
    return cmds


def sync(repo: Path, *, dry_run: bool = False) -> dict:
    """Make sure the environment exists and has this checkout's package (editable) and its dependencies."""
    repo = Path(repo)
    cmds = commands(repo)
    out: dict = {"python": str(venv_python(repo)), "commands": [" ".join(c) for c in cmds], "dry_run": dry_run}
    if dry_run:
        out["did"] = "shown"
        return out
    uv = procs.which("uv")
    if not uv:
        raise MissingPrerequisite("uv isn't installed, and Slate's environment is made with it.",
                                  hint="Install it with `brew install uv` (or see docs.astral.sh/uv), then run "
                                       "`slate update` again.")
    for cmd in cmds[:-1]:
        procs.run([uv, *cmd[1:]], cwd=repo, timeout=900, check=True)
    install = cmds[-1]
    proc = procs.run([uv, *install[1:]], cwd=repo, timeout=900)
    if proc.returncode != 0 and EXTRAS:
        # The extra (ocrmac and its pyobjc frameworks) is the likeliest thing to be missing offline; the base package
        # must still install.
        base = [uv, "pip", "install", "--python", str(venv_python(repo)), "-e", str(repo)]
        procs.run(base, cwd=repo, timeout=900, check=True)
        out["did"] = "installed without the ocr extra"
        out["warning"] = ("The ocr extra (ocrmac, Apple Vision) didn't install, so OCR gates will report UNCHECKED. "
                          f"Try again with: uv pip install --python {venv_python(repo)} -e '{repo}[ocr]'")
        return out
    if proc.returncode != 0:
        raise MissingPrerequisite("uv couldn't install Slate into its environment.",
                                  hint=((proc.stderr or proc.stdout or "").strip()[-400:] or "Run install.sh again."))
    out["did"] = "synced"
    return out
