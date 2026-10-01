"""The code and data behind every delivered render, kept: a commit of the film folder's small files at `slate deliver`,
tagged with the delivered version.

A film folder is a git repository from `slate new` on (slate.state.scaffold), and its .gitignore keeps heavy media out,
so a commit holds exactly what made the picture and sound: film.json, the script, timing, the scene code, the cue
sheets and the records. The films wished they'd kept one per delivery, so a later before-and-after comparison was
possible ("keep a snapshot of the code and data behind every delivered render").

- `commit(film, label)` runs before the set is staged: it commits whatever the film's small files are now (or keeps
  HEAD when nothing changed), and the manifest records that commit.
- `tag(film, snap, name)` runs once the set is in place: an annotated tag (`deliver/vN`, or `draft/vN.K`), so a
  delivery that failed half way never leaves a tag behind.

A film folder without git, or a git that fails, never blocks a delivery: the manifest says why there's no snapshot.
The repository never gets a remote, and nothing here pushes.
"""

from __future__ import annotations

from pathlib import Path

from slate import procs
from slate.errors import SlateError
from slate.finish.film import Film

ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def _git(film_dir: Path, *args: str, check: bool = True):
    return procs.run(["git", *args], cwd=film_dir, env=ENV, timeout=120, check=check)


def _identity(film_dir: Path) -> list[str]:
    """`-c user.name=… -c user.email=…` when the film's git has no identity of its own (as slate.state does)."""
    out: list[str] = []
    for key, fallback in (("user.name", "Slate"), ("user.email", "slate@localhost")):
        got = _git(film_dir, "config", key, check=False)
        if got.returncode != 0 or not (got.stdout or "").strip():
            out += ["-c", f"{key}={fallback}"]
    return out


def commit(film: Film, label: str) -> dict:
    """Commit the film folder's small files for a delivery. {repo, commit, committed, files, why}."""
    d = film.dir
    if not (d / ".git").exists():
        return {"repo": False, "commit": None, "why": "the film folder isn't a git repository"}
    if procs.which("git") is None:
        return {"repo": True, "commit": None, "why": "git isn't installed"}
    try:
        _git(d, "add", "-A")
        staged = [ln for ln in (_git(d, "diff", "--cached", "--name-only").stdout or "").splitlines() if ln.strip()]
        committed = False
        if staged:
            _git(d, *_identity(d), "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m",
                 f"{label}: the code and data behind it")
            committed = True
        head = (_git(d, "rev-parse", "HEAD").stdout or "").strip()
    except SlateError as exc:
        return {"repo": True, "commit": None, "why": f"git couldn't commit the film's files ({exc.message.splitlines()[0]})"}
    return {"repo": True, "commit": head or None, "committed": committed, "files": len(staged),
            "why": None if head else "the film's history has no commit yet"}


def tag(film: Film, snap: dict, name: str, message: str) -> dict:
    """Tag the snapshot's commit (annotated). An existing tag on another commit is never moved: the new one gets a
    suffix. Returns snap with `tag` set (or `tag_error`)."""
    if not snap.get("commit"):
        return snap
    d = film.dir
    try:
        candidate, n = name, 1
        while True:
            got = _git(d, "rev-parse", "-q", "--verify", f"refs/tags/{candidate}^{{commit}}", check=False)
            if got.returncode != 0:
                break
            if (got.stdout or "").strip() == snap["commit"]:
                return {**snap, "tag": candidate}
            n += 1
            candidate = f"{name}-{n}"
        _git(d, *_identity(d), "-c", "tag.gpgsign=false", "tag", "-a", candidate, "-m", message, snap["commit"])
        return {**snap, "tag": candidate}
    except SlateError as exc:
        return {**snap, "tag": None, "tag_error": exc.message.splitlines()[0]}
