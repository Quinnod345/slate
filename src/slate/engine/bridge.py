"""The only door between Python and Node: `node <engine>/cli.mjs <command> --json '<args>'`, one JSON object back.

The Python CLI never imports Node. Every engine call goes through `call()`, which:

- finds the engine (`engine_dir()`): the installed copy for the pinned Remotion version under Slate's data folder, the
  repo's `engine/` in development, or `SLATE_ENGINE_DIR` when set;
- runs node through `slate.procs` (no shell, `BROWSER=none`, a timeout, stdin closed);
- prepares the film first, so every bundle, still, check and render sees the same `@film/data`: the brand pack's path
  and `.slate/spoken.json` (how each number and symbol in the reads is said, from `slate.timing.spoken`);
- maps an engine error onto Slate's errors, so the exit codes match (1 refused, 2 usage, 3 missing prerequisite,
  5 busy, 75 try later);
- keeps the engine's temp folders (Remotion's headless Chrome profiles among them) under Slate's data folder, never in
  $TMPDIR, where another tool's temp cleanup once deleted a live render's Chrome profile;
- gives the engine a deadline a little inside the call's timeout (SLATE_ENGINE_DEADLINE_S), so a slow render stops
  itself cleanly (its Chrome, bundle and partial file gone) instead of being killed mid-write.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from slate import config, errors, jsonio, log, paths, procs
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, SpendRefused, UsageError

# slate.errors.TryLater is exit 75 (try later: the queue retries). Read defensively: a foundation without it maps the
# engine's "try_later" to Busy (exit 5), the nearest meaning.
TryLater: type[SlateError] = getattr(errors, "TryLater", Busy)

PROTOCOL = 1
ARGS_FILE_OVER = 200_000  # bytes of JSON args; above this they travel in a file, not argv
DEADLINE_GRACE_S = 10.0  # the engine stops itself up to this long before a call's timeout, so its cleanup runs


class EngineError(SlateError):
    """The engine failed in a way it didn't classify (a crash, a bug): exit 1, with its message and stack."""

    kind = "engine_error"


class Stopped(SlateError):
    """The engine stopped before finishing: its deadline passed, or a signal reached it. It cleaned up after itself."""

    kind = "stopped"


_KINDS: dict[str, type[SlateError]] = {
    "refused": Refused,
    "usage": UsageError,
    "missing_prerequisite": MissingPrerequisite,
    "spend_refused": SpendRefused,
    "busy": Busy,
    "try_later": TryLater,
    "stopped": Stopped,
}

# Args that name a file or folder: each is resolved from where the caller stands (the engine runs from its own folder).
PATH_ARGS = ("film", "out", "out_dir", "entry", "serve_url", "brand_path", "browser", "scene", "frames_dir")


def engine_tmp_dir() -> Path:
    """The engine's temp folder: <Slate data>/cache/tmp (engine/remotion/lib/common.mjs engineTmpDir)."""
    return paths.cache_dir() / "tmp"


def deadline_for(timeout: float | None) -> float | None:
    """When the engine should stop itself for a call with this timeout (seconds), or None for no deadline.

    slate.procs stops a late child with SIGKILL, which skips every cleanup: headless Chrome runs in its own process
    group and would be orphaned, and a 290 MB bundle or a half-written .part file would stay. So the engine gets its
    own deadline a little earlier, stops itself the clean way, and the timeout is only the backstop.
    """
    if not timeout or timeout <= 0:
        return None
    grace = min(DEADLINE_GRACE_S, max(0.25, timeout * 0.1))
    return max(0.1, round(timeout - grace, 2))


def _complete(folder: Path) -> bool:
    from slate.engine import install  # noqa: PLC0415

    return install.is_complete(folder)


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
        from slate.engine import install  # noqa: PLC0415

        if install.marker(folder).get("packages_ready", True) is False:
            raise MissingPrerequisite(f"The dependency installation in SLATE_ENGINE_DIR ({folder}) did not finish.",
                                      hint="Run `slate engine install --npm-ci` again, or unset SLATE_ENGINE_DIR.")
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


def film_file(film_dir: Path, rel: str) -> Path:
    """A film's file: its own copy, else (a derived film) the parent's file its film.lock.json points to."""
    own = Path(film_dir) / rel
    if own.exists():
        return own
    try:
        from slate.state import lock  # noqa: PLC0415 (the state module is optional here)

        return Path(lock.resolve(Path(film_dir), rel))
    except Exception:  # noqa: BLE001 - no state module, or no reference: the film's own (missing) path
        return own


def _heard_words(film_dir: Path) -> list[str]:
    """Every word the reads were heard as (timing/vo_words.json), so a heard "12" can answer a lookup for "twelve"."""
    doc = jsonio.read(film_file(film_dir, "timing/vo_words.json"), default=None)
    shots = doc.get("shots") if isinstance(doc, dict) else None
    if not isinstance(shots, dict):
        return []
    return [str(w.get("w", "")) for rec in shots.values() if isinstance(rec, dict)
            for w in rec.get("words") or [] if isinstance(w, dict)]


def spoken_map(film_dir: Path) -> dict[str, list[list[str]]] | None:
    """How each number and symbol in the film's reads is said ({"64": [["sixty-four"], …]}), or None when unknown.

    It covers the script's words (each shot's VO line) and what the reads were heard as, so the kit's clock can match a
    script word to a heard number either way round, as slate.timing.words does.
    """
    shots = jsonio.read(film_file(film_dir, "timing/shots.json"), default=None)
    if not shots:
        return None
    try:
        from slate.timing import spoken  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - the timing module is optional for the engine
        return None
    lang = _film_json(film_dir).get("language") or "en"
    out: dict[str, list[list[str]]] = {}
    tokens = [token for shot in shots.get("shots", []) for token in str(shot.get("vo") or "").split()]
    try:
        for token in tokens + _heard_words(film_dir):
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


def lang_rules(film_dir: Path) -> dict | None:
    """How the film's language turns a word into its match key, for the kit's clock ('@film/data' `lang`).

    The same rules slate.timing.words matches with: NFKC, lower case, letters and digits plus the table's kept symbols;
    the symbols a token keeps at its edges; the marks that make a number negative; the longest run of heard words one
    written token can be said as. None when the timing module or the language's table isn't there.
    """
    try:
        from slate.timing import spoken, words  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - the timing module is optional for the engine
        return None
    lang = _film_json(film_dir).get("language") or "en"
    try:
        tab = spoken.table(lang)
    except SlateError as exc:
        log.warn(f"word-matching rules skipped: {exc.message}")
        return None
    keep = str(tab["normalize"]["keep"])
    edge = set(keep)
    for group in ("symbols", "suffixes", "currency"):
        for key in tab.get(group) or {}:
            edge.update(key)
    return {"lang": lang, "keep": keep, "strip_keep": "".join(sorted(edge)),
            "negative_marks": "".join((tab.get("numbers") or {}).get("negative_marks") or []),
            "max_run": int(words.MAX_RUN)}


def _write_if_changed(target: Path, obj: Any) -> None:
    text = jsonio.dumps(obj)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        jsonio.write_text_atomic(target, text)


# What the engine reads from a film folder that a derived film (slate.state.derive) may hold only by reference: the
# timing files '@film/data' is made from, and the media folders scenes read as image sequences.
REF_FILES = ("timing/shots.json", "timing/timeline.json", "timing/vo_words.json", "timing/vo_cues.json",
             "timing/events.json", "timing/captions.json")
REF_DIRS = ("captures", "plates")


def refs_map(film_dir: Path) -> dict[str, str]:
    """Where a derived film's reused files really are: {film-relative path: absolute path in the parent film}.

    A derived film reuses its parent's timings and captures by reference (its film.lock.json points into the parent;
    nothing is copied), so the engine can't find them in the film's own folder. slate.state.lock.resolve knows where
    they are. A film with its own copies (every film that isn't derived) gets an empty map.
    """
    try:
        from slate.state import lock  # noqa: PLC0415 (the state module is optional here)
    except Exception:  # noqa: BLE001
        return {}
    film_dir = Path(film_dir)
    out: dict[str, str] = {}
    for rel in REF_FILES:
        own = film_dir / rel
        real = film_file(film_dir, rel)
        if real != own and real.is_file():
            out[rel] = str(real.resolve())
    try:
        entries = lock.load(film_dir).get("files") or {}
    except Exception:  # noqa: BLE001
        entries = {}
    for folder in REF_DIRS:
        if (film_dir / folder).exists():
            continue
        for rel, entry in sorted(entries.items()):
            parts = Path(rel).parts
            if len(parts) < 2 or parts[0] != folder or not (entry or {}).get("ref"):
                continue
            try:
                real = Path(lock.resolve(film_dir, rel))
            except Exception:  # noqa: BLE001
                continue
            root = real.parents[len(parts) - 2] if len(real.parents) >= len(parts) - 1 else None
            if root is not None and root.name == folder and root.is_dir():
                out[folder] = str(root.resolve())
                break
    return out


def prepare(film_dir: Path) -> None:
    """Write what '@film/data' and the bundle need that only Python knows, when it changes: .slate/spoken.json (how
    each number and symbol is said), .slate/lang.json (the language's match rules) and, for a derived film,
    .slate/refs.json (where its reused files really are)."""
    film_dir = Path(film_dir)
    if not (film_dir / "film.json").is_file():
        return
    forms = spoken_map(film_dir)
    if forms is not None:
        _write_if_changed(film_dir / ".slate" / "spoken.json", forms)
    rules = lang_rules(film_dir)
    if rules is not None:
        _write_if_changed(film_dir / ".slate" / "lang.json", rules)
    refs = refs_map(film_dir)
    target = film_dir / ".slate" / "refs.json"
    if refs:
        _write_if_changed(target, refs)
    elif target.is_file():
        target.unlink()  # a film that now has its own copies must not keep reading its parent's


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


def _raise(result: dict, command: str, code: int, timeout: float | None = None) -> None:
    kind = result.get("kind") or "error"
    message = result.get("error") or f"the engine's {command} failed (exit {code})"
    cls = _KINDS.get(kind, EngineError)
    details = {"engine_command": command, "engine_exit": code}
    if result.get("details"):
        details["engine_details"] = result["details"]
    if cls is Stopped:
        if message.startswith("took longer than") and timeout:
            message = (f"The engine's {command} took longer than its {timeout:g} s timeout and stopped itself; "
                       "nothing half-made was left behind.")
        else:
            message = f"The engine's {command} was {message}; nothing half-made was left behind."
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
    # Paths mean "from where I'm standing": the engine runs from its own folder, so they're made absolute here.
    for key in PATH_ARGS:
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
    # Temp folders under Slate's data folder (cli.mjs sets the same itself; this covers every child from the start).
    env = {"SLATE_DATA": str(paths.data()), "TMPDIR": str(paths.ensure_dir(engine_tmp_dir()))}
    deadline = deadline_for(timeout)
    if deadline is not None:
        env["SLATE_ENGINE_DEADLINE_S"] = f"{deadline:g}"
    try:
        proc = procs.run(argv, cwd=engine, timeout=timeout, env=env)
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
        _raise(result, command, proc.returncode, timeout)
    if result.get("protocol") != PROTOCOL:
        raise MissingPrerequisite(
            f"The engine at {engine} speaks protocol {result.get('protocol')}, but this Slate speaks {PROTOCOL}.",
            hint="Run `slate engine install` so the engine matches this checkout.",
        )
    return result
