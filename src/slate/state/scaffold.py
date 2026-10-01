"""A new film folder: every stage folder, the brief, the script skeleton, and git for the small files.

```
<film>/film.json  film.lock.json  approvals.json  .gitignore
<film>/brief/GOAL.md  brief/PRODUCTION.md  brief/brief.json
<film>/script/script.json          the only hand-edited script file
<film>/voice/ timing/ scenes/ captures/ sound/ renders/ masters/ qa/ review/ deliver/ archive/
```

The folder is a git repository from the first minute, so every change to the film's data has history. Git keeps the
small files (JSON, Markdown, code); heavy media stays out (renders, masters, captures, the archive, audio and video
files, bundles). A film repo never gets a remote: nothing here pushes anywhere.
"""

from __future__ import annotations

from pathlib import Path

from slate import config, jsonio, log, procs
from slate.errors import SlateError

STAGE_DIRS = ("brief", "script", "voice", "timing", "scenes", "captures", "sound", "renders", "masters", "qa",
              "review", "deliver", "archive")
SCRIPT = "script/script.json"

GITIGNORE = """\
# Slate film. Git keeps the small files: film.json, the brief, the script, timing, state and scene code.
# Heavy media stays out: renders, masters, captures, the archive, audio and video files, image sequences, bundles.
# In the four heavy folders only their small records (JSON and Markdown, e.g. renders/registry.json) are kept.
/renders/**
/masters/**
/captures/**
/archive/**
!/renders/**/
!/masters/**/
!/captures/**/
!/archive/**/
!/renders/**/*.json
!/masters/**/*.json
!/captures/**/*.json
!/archive/**/*.json
!/renders/**/*.md
!/masters/**/*.md
!/captures/**/*.md
!/archive/**/*.md
!**/.gitkeep
/.slate/
frames/
bundle/
bundles/
*.bundle/
.remotion/
node_modules/
# audio
*.wav
*.aif
*.aiff
*.aifc
*.caf
*.flac
*.mp3
*.m4a
*.aac
*.ogg
*.opus
# video
*.mov
*.mp4
*.m4v
*.mkv
*.webm
*.avi
*.mxf
*.y4m
# images and projects
*.exr
*.dpx
*.tif
*.tiff
*.png
*.jpg
*.jpeg
*.webp
*.heic
*.psd
*.aep
*.aepx
*.prproj
# scratch
*.part
*.tmp
.DS_Store
# keys never live in a film: the ElevenLabs CLI (and others) load a .env from the folder they run in
.env
.env.*
"""


def script_skeleton(fj: dict, **extra) -> dict:
    """script/script.json before anyone writes it: the title, the language and no acts yet."""
    out = {"title": fj.get("title"), "version": "draft-0 (not written yet)", "language": fj.get("language"),
           "acts": []}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def write(target: Path, fj: dict, *, request: str | None = None, request_source: str | None = None,
          asks: list[tuple[str, str]] | None = None, script: dict | bool | None = True,
          git: bool | None = None, request_asks: bool = True) -> Path:
    """Write a complete new film folder at `target` (which must be empty or missing) and return it."""
    from slate.state import approvals, film, lock, production  # noqa: PLC0415

    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in STAGE_DIRS:
        folder = target / name
        folder.mkdir(exist_ok=True)
        (folder / ".gitkeep").touch()
    (target / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    film.save(target, fj)
    lock.save(target, lock.empty())
    approvals.save(target, approvals.empty())
    if script:
        body = script if isinstance(script, dict) else script_skeleton(fj)
        jsonio.write_atomic(target / SCRIPT, body)
    production.init(target, request=request, request_source=request_source, asks=asks, request_asks=request_asks)
    production.render(target)
    use_git = config.load().get("films", {}).get("git_init", True) if git is None else git
    if use_git:
        git_init(target, f"Start film {fj['id']}: {fj.get('title')}")
    return target


def git_init(film_dir: Path, message: str) -> dict:
    """`git init` plus one commit of the small files. Never adds a remote. A missing git is a warning, not a failure."""
    film_dir = Path(film_dir)
    if procs.which("git") is None:
        log.warn("git isn't installed, so this film has no history yet; `slate doctor` says how to install it.")
        return {"initialized": False, "reason": "git isn't installed"}
    env = {"GIT_TERMINAL_PROMPT": "0"}
    try:
        procs.run(["git", "-c", "init.defaultBranch=main", "init", "-q"], cwd=film_dir, env=env, timeout=60, check=True)
        procs.run(["git", "add", "-A"], cwd=film_dir, env=env, timeout=60, check=True)
        ident: list[str] = []
        for key, fallback in (("user.name", "Slate"), ("user.email", "slate@localhost")):
            got = procs.run(["git", "config", key], cwd=film_dir, env=env, timeout=30)
            if got.returncode != 0 or not got.stdout.strip():
                ident += ["-c", f"{key}={fallback}"]
        procs.run(["git", *ident, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message],
                  cwd=film_dir, env=env, timeout=60, check=True)
        head = procs.run(["git", "rev-parse", "HEAD"], cwd=film_dir, env=env, timeout=30, check=True).stdout.strip()
    except SlateError as exc:
        log.warn(f"git couldn't start this film's history ({exc.message}); the film itself is fine.")
        return {"initialized": (film_dir / ".git").exists(), "reason": exc.message}
    return {"initialized": True, "commit": head}


def git_state(film_dir: Path) -> dict:
    """{repo, head, dirty, remotes} for status and tests (read-only git calls)."""
    film_dir = Path(film_dir)
    if not (film_dir / ".git").exists() or procs.which("git") is None:
        return {"repo": False}
    env = {"GIT_TERMINAL_PROMPT": "0"}
    head = procs.run(["git", "rev-parse", "HEAD"], cwd=film_dir, env=env, timeout=30)
    dirty = procs.run(["git", "status", "--porcelain"], cwd=film_dir, env=env, timeout=30)
    remotes = procs.run(["git", "remote"], cwd=film_dir, env=env, timeout=30)
    return {"repo": True, "head": head.stdout.strip() if head.returncode == 0 else None,
            "dirty": bool(dirty.stdout.strip()), "remotes": remotes.stdout.split()}
