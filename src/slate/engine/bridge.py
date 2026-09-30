"""The only door between Python and Node: `node <engine>/cli.mjs <command> --json '<args>'`, one JSON object back.

The Python CLI never imports Node. Every engine call goes through `call()`, which:

- finds the engine (`engine_dir()`): the installed copy for the pinned Remotion version under Slate's data folder, the
  repo's `engine/` in development, or `SLATE_ENGINE_DIR` when set;
- runs node through `slate.procs` (no shell, `BROWSER=none`, a timeout, stdin closed);
- prepares the film first, so every bundle, still, check and render sees the same `@film/data`: the brand pack's path
  and `.slate/spoken.json` (how each number and symbol in the reads is said, from `slate.timing.spoken`);
- maps an engine error onto Slate's errors, so the exit codes match (1 refused, 2 usage, 3 missing prerequisite,
  5 busy).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from slate import config, jsonio, log, paths, procs
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, SpendRefused, UsageError

PROTOCOL = 1
ARGS_FILE_OVER = 200_000  # bytes of JSON args; above this they travel in a file, not argv

_KINDS: dict[str, type[SlateError]] = {
    "refused": Refused,
    "usage": UsageError,
    "missing_prerequisite": MissingPrerequisite,
    "spend_refused": SpendRefused,
    "busy": Busy,
}


class EngineError(SlateError):
    """The engine failed in a way it didn't classify (a crash, a bug): exit 1, with its message and stack."""

    kind = "engine_error"


def _complete(folder: Path) -> bool:
    return (folder / "cli.mjs").is_file() and (folder / "node_modules" / "remotion" / "package.json").is_file()


def engine_dir() -> Path:
    """The Node engine to run: SLATE_ENGINE_DIR, else the installed engine for the pinned version, else the repo's.

    The installed engine (`DATA/engine/<remotion version>`, made by `slate engine install`) wins when it's complete,
    so a film always renders with the engine it was pinned to. The repo's `engine/` is the fallback for development.
    """
    override = os.environ.get("SLATE_ENGINE_DIR")
    if override:
        folder = Path(override).expanduser().resolve()
        if not (folder / "cli.mjs").is_file():
            raise MissingPrerequisite(f"SLATE_ENGINE_DIR points at {folder}, which has no cli.mjs.",
                                      hint="Unset SLATE_ENGINE_DIR, or point it at an engine folder.")
        return folder
    installed = paths.engine_home(config.engine_version())
    if _complete(installed):
        return installed
    if _complete(paths.ENGINE_SRC):
        return paths.ENGINE_SRC
    raise MissingPrerequisite(
        "Slate's Node engine isn't installed yet.",
        hint="Run `slate engine install --npm-ci` (it copies the engine and installs its pinned packages).",
    )


def engine_source() -> str:
    """Where engine_dir() found the engine: 'env', 'installed' or 'repo'."""
    if os.environ.get("SLATE_ENGINE_DIR"):
        return "env"
    folder = engine_dir()
    return "repo" if folder == paths.ENGINE_SRC else "installed"


# --- preparing a film -----------------------------------------------------------------------------------------------

def _film_json(film_dir: Path) -> dict:
    return jsonio.read(film_dir / "film.json", default={}) or {}


def brand_path(film_dir: Path) -> Path | None:
    """The folder of the film's brand pack (slate.brand.pack.find, else the brand folders), or None."""
    name = _film_json(film_dir).get("brand")
    if not isinstance(name, str) or not name:
        return None
    try:
        from slate.brand import pack  # noqa: PLC0415 (the brand module is optional here)

        found = Path(pack.find(name))
        return found.parent if found.is_file() else found
    except Exception:  # noqa: BLE001 - a missing or half-built brand module falls back to the folders
        for folder in paths.brand_dirs():
            if (folder / name / "brand.json").is_file():
                return folder / name
    return None


def spoken_map(film_dir: Path) -> dict[str, list[list[str]]] | None:
    """How each number and symbol in the film's reads is said ({"64": [["sixty-four"], …]}), or None when unknown."""
    shots = jsonio.read(film_dir / "timing" / "shots.json", default=None)
    if not shots:
        return None
    try:
        from slate.timing import spoken  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - the timing module is optional for the engine
        return None
    lang = _film_json(film_dir).get("language") or "en"
    out: dict[str, list[list[str]]] = {}
    try:
        for shot in shots.get("shots", []):
            for token in str(shot.get("vo") or "").split():
                key = spoken.strip_edges(token, lang)
                if not key or key in out or not spoken.is_spoken(key, lang):
                    continue
                forms = spoken.forms(key, lang)
                if forms and forms != [[key]]:
                    out[key] = forms
    except SlateError as exc:
        log.warn(f"spoken forms skipped: {exc.message}")
        return None
    return out


def prepare(film_dir: Path) -> None:
    """Write .slate/spoken.json when it changes (the engine inlines it into '@film/data')."""
    film_dir = Path(film_dir)
    if not (film_dir / "film.json").is_file():
        return
    forms = spoken_map(film_dir)
    if forms is None:
        return
    target = film_dir / ".slate" / "spoken.json"
    text = jsonio.dumps(forms)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        jsonio.write_text_atomic(target, text)


# --- calling the engine ---------------------------------------------------------------------------------------------

def _parse(stdout: str) -> dict | None:
    for line in reversed([ln for ln in (stdout or "").splitlines() if ln.strip()]):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _forward(stderr: str) -> None:
    """The engine's progress lines, passed on to this command's stderr (never stdout)."""
    if not stderr or log._state.get("quiet"):  # noqa: SLF001 - the one place progress is re-emitted
        return
    sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")
    sys.stderr.flush()


def _raise(result: dict, command: str, code: int) -> None:
    kind = result.get("kind") or "error"
    message = result.get("error") or f"the engine's {command} failed (exit {code})"
    cls = _KINDS.get(kind, EngineError)
    details = {"engine_command": command, "engine_exit": code}
    if result.get("details"):
        details["engine_details"] = result["details"]
    raise cls(message, hint=result.get("hint"), details=details)


def call(command: str, args: dict | None = None, *, timeout: float | None = None) -> dict:
    """Run one engine command and return its JSON result (`ok` is always true here: failures raise).

    `args` may name a film ("film": a folder); it's resolved to an absolute path, prepared (brand pack, spoken forms)
    and passed on. Raises the SlateError matching the engine's `kind`, MissingPrerequisite when node or the engine
    isn't there, and SlateError when the engine gives no answer.
    """
    engine = engine_dir()
    node = procs.require("node", hint="Install Node 24 or newer (`slate doctor --install` offers it).")
    payload: dict[str, Any] = {k: v for k, v in (args or {}).items() if v is not None}
    for key in ("film", "out", "out_dir", "entry", "serve_url", "brand_path"):
        if isinstance(payload.get(key), (str, os.PathLike)):
            payload[key] = str(Path(payload[key]).expanduser().resolve())
    if payload.get("film"):
        film_dir = Path(payload["film"])
        prepare(film_dir)
        if "brand_path" not in payload:
            found = brand_path(film_dir)
            if found:
                payload["brand_path"] = str(found)
    text = json.dumps(payload, ensure_ascii=False, default=str)
    argv: list[str] = [node, str(engine / "cli.mjs"), command]
    args_file: Path | None = None
    if len(text.encode("utf-8")) > ARGS_FILE_OVER:
        args_file = paths.ensure_dir(paths.cache_dir() / "engine-args") / f"{command}-{os.getpid()}.json"
        args_file.write_text(text, encoding="utf-8")
        argv += ["--args-file", str(args_file)]
    else:
        argv += ["--json", text]
    if log._state.get("quiet"):  # noqa: SLF001
        argv.append("--quiet")
    try:
        proc = procs.run(argv, cwd=engine, timeout=timeout, env={"SLATE_DATA": str(paths.data())})
    finally:
        if args_file:
            args_file.unlink(missing_ok=True)
    _forward(proc.stderr)
    result = _parse(proc.stdout)
    if result is None:
        from slate.secrets import redact  # noqa: PLC0415

        tail = redact((proc.stderr or proc.stdout or "").strip()[-1200:])
        raise EngineError(f"The engine's {command} gave no answer (exit {proc.returncode}).",
                          hint="Run `slate engine status`; the engine may need `slate engine install --npm-ci`.",
                          details={"output_tail": tail})
    if not result.get("ok"):
        _raise(result, command, proc.returncode)
    if result.get("protocol") != PROTOCOL:
        raise MissingPrerequisite(
            f"The engine at {engine} speaks protocol {result.get('protocol')}, but this Slate speaks {PROTOCOL}.",
            hint="Run `slate engine install` so the engine matches this checkout.",
        )
    return result
