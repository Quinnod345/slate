"""Cross-module calls for generation and capture, each behind one small function.

Slate's modules are built in parallel, so generation and capture reach the others only through the interfaces in
docs/ARCHITECTURE.md §12 (state, timing, brand, notify, jobs). Every call goes through a function here, which means:

- a module that isn't built yet degrades to a documented fallback, or to a clear refusal that names what's missing,
  never to a silent pass;
- tests swap any one of these with a small fake (monkeypatch the function), so nothing here needs the real module.

The fallbacks are deliberately narrow: they read the documented file shapes (film.json, timing/shots.json, brand.json)
and write only what the architecture says a film folder holds (film.json "pins"). Approvals fall back to a small store
in the film folder with the same contract as slate.state.approvals (the approved file's SHA-256 must still match), so
a person's yes is never assumed, only recorded. Word timings have no fallback: beat mapping refuses without them.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import importlib
import os
from pathlib import Path
from typing import Any, Iterator

from slate import clock, jsonio, log, paths
from slate.errors import MissingPrerequisite, UsageError


def _module(name: str):
    """Import a sibling module, or None when it isn't built yet (a broken module still raises: that's a bug)."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (name == exc.name or name.startswith(exc.name + ".") or exc.name.startswith(name)):
            return None
        raise


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def validate_part(obj: Any, schema_name: str, part: str | None = None) -> list[str]:
    """Validate against a whole schema, or one of its $defs (a spec, a plan, an approval request)."""
    if part is None:
        return jsonio.validate(obj, schema_name)
    import jsonschema  # noqa: PLC0415

    whole = jsonio.schema(schema_name)
    if part not in whole.get("$defs", {}):
        raise UsageError(f"Schema {schema_name} has no part called {part!r}.")
    sub = {"$schema": whole.get("$schema"), "$defs": whole["$defs"], "$ref": f"#/$defs/{part}"}
    validator = jsonschema.validators.validator_for(whole)(sub)
    problems = []
    for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(x) for x in err.absolute_path) or "(top level)"
        problems.append(f"{where}: {err.message}")
    return problems


def require_part(obj: Any, schema_name: str, part: str | None = None, what: str = "") -> None:
    problems = validate_part(obj, schema_name, part)
    if problems:
        listed = "\n  ".join(problems[:20])
        more = f"\n  …and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise UsageError(f"{what or schema_name} isn't valid:\n  {listed}{more}")


# ---------------------------------------------------------------------------------------------------------------
# The film


def load_film(film_dir: Path) -> dict:
    """film.json, through the state module when it's there."""
    film_mod = _module("slate.state.film")
    if film_mod is not None and hasattr(film_mod, "load"):
        return film_mod.load(film_dir)
    return jsonio.read(Path(film_dir) / "film.json")


def film_clock(film: dict) -> clock.Clock:
    """The film's clock. The frame rate must be in film.json: generation and capture never guess one."""
    if not film.get("fps_num") or not film.get("fps_den"):
        raise UsageError("film.json has no fps_num/fps_den, so there's no frame rate to conform to.",
                         hint="Set the film's frame rate in film.json (slate new does it from the format).")
    return clock.from_film(film)


def film_size(film: dict) -> tuple[int, int]:
    width, height = film.get("width"), film.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise UsageError("film.json has no width/height, so there's no frame size to conform to.",
                         hint="Set the film's size in film.json (slate new does it from the format).")
    return width, height


def script_blocks(film_dir: Path, key: str = "generate") -> dict[str, dict]:
    """{shot id: block} from script/script.json, the one hand-edited source (acts of shots, or of lines)."""
    script = jsonio.read(Path(film_dir) / "script" / "script.json", default=None)
    out: dict[str, dict] = {}
    if not isinstance(script, dict):
        return out
    for act in script.get("acts") or []:
        for shot in (act.get("shots") or act.get("lines") or []) if isinstance(act, dict) else []:
            if isinstance(shot, dict) and shot.get("id") and isinstance(shot.get(key), dict):
                out[str(shot["id"])] = shot[key]
    return out


def load_shots(film_dir: Path) -> list[dict]:
    """The film's shots: timing's shots.json, else the storyboard. A shot's `generate` block comes with it, or from
    script/script.json by shot id (where a person writes it) when the timing files don't carry it."""
    shots: list[dict] | None = None
    timing = _module("slate.timing")
    if timing is not None and hasattr(timing, "load_shots"):
        try:
            data = timing.load_shots(film_dir)
            shots = list((data or {}).get("shots") or [])
        except (UsageError, MissingPrerequisite):
            shots = None
    film_dir = Path(film_dir)
    if shots is None:
        for rel in ("timing/shots.json", "script/storyboard.json"):
            data = jsonio.read(film_dir / rel, default=None)
            if isinstance(data, dict) and isinstance(data.get("shots"), list):
                shots = list(data["shots"])
                break
    if shots is None:
        raise MissingPrerequisite(
            "This film has no shots yet (no timing/shots.json or script/storyboard.json).",
            hint="Run `slate script build`, then `slate timing`. Generate blocks live on shots in script/script.json.")
    blocks = script_blocks(film_dir)
    return [({**s, "generate": blocks[s.get("id")]} if not s.get("generate") and s.get("id") in blocks else s)
            for s in shots]


def find_shot(film_dir: Path, shot_id: str) -> dict:
    for shot in load_shots(film_dir):
        if shot.get("id") == shot_id:
            return shot
    raise UsageError(f"No shot {shot_id!r} in this film.", hint="`slate plates plan` lists the shots that generate.")


def rel(film_dir: Path, path: Path) -> str:
    """A film-relative path when the file is inside the film folder, else the absolute path."""
    try:
        return str(Path(path).resolve().relative_to(Path(film_dir).resolve()))
    except ValueError:
        return str(Path(path).resolve())


# ---------------------------------------------------------------------------------------------------------------
# Pins, locks, approvals (state)


def pin(film_dir: Path, key: str, value: Any) -> Any:
    """Pin a value at first use (the first pin wins). Returns what's pinned."""
    pins = _module("slate.state.pins")
    if pins is not None and hasattr(pins, "pin"):
        return pins.pin(film_dir, key, value)
    # Fallback before the state module lands: film.json "pins" is where config.model() reads pins from.
    path = Path(film_dir) / "film.json"
    data = jsonio.read(path)
    current = (data.get("pins") or {}).get(key)
    if current is not None:
        return current
    data.setdefault("pins", {})[key] = value
    jsonio.write_atomic(path, data)
    return value


def lock_record(film_dir: Path, rel_path: str, *, producer: str, inputs: dict) -> None:
    """Record what made a derived file (film.lock.json), when the state module is there."""
    lock = _module("slate.state.lock")
    if lock is not None and hasattr(lock, "record"):
        lock.record(film_dir, rel_path, producer=producer, inputs=inputs)


def lock_asset(film_dir: Path, rel_path: str, *, reason: str) -> bool:
    """Lock an approved asset by content hash, when the state module is there. True when it was locked."""
    lock = _module("slate.state.lock")
    if lock is not None and hasattr(lock, "lock_asset"):
        lock.lock_asset(film_dir, rel_path, reason=reason)
        return True
    return False


class _FileApprovals:
    """Before the state module lands: approvals kept in the film folder, with the approved file's SHA-256.

    Same contract as slate.state.approvals (record / is_approved / latest / spend_approval / spend_covers; the hash
    must still match), so a change of store later changes nothing for callers. Marked "store": "gen-fallback"."""

    name = "gen-fallback"

    @staticmethod
    def _file(film_dir: Path) -> Path:
        return Path(film_dir) / "plates" / "approvals.json"

    def _all(self, film_dir: Path) -> list[dict]:
        return list((jsonio.read(self._file(film_dir), default={"approvals": []}) or {}).get("approvals", []))

    def record(self, film_dir: Path, *, what: str, path: str, approved_by: str, note: str | None = None) -> dict:
        import secrets as _secrets  # noqa: PLC0415

        full = Path(path) if Path(path).is_absolute() else Path(film_dir) / path
        if not full.is_file():
            raise UsageError(f"Can't approve {path}: no such file.")
        entry = {"id": f"ap_{_secrets.token_hex(5)}", "what": what,
                 "kind": "spend" if what.startswith("generation") else what.split(":", 1)[0], "path": path,
                 "sha256": jsonio.sha256(full), "approved_by": approved_by, "at": now_iso(), "note": note,
                 "store": self.name}
        data = {"approvals": self._all(film_dir) + [entry]}
        jsonio.write_atomic(self._file(film_dir), data)
        return entry

    def latest(self, film_dir: Path, what: str, path: str | None = None) -> dict | None:
        for rec in reversed(self._all(film_dir)):
            if rec.get("what") == what and (path is None or rec.get("path") == path):
                return rec
        return None

    def is_approved(self, film_dir: Path, what: str, path: str) -> bool:
        rec = self.latest(film_dir, what, path)
        full = Path(path) if Path(path).is_absolute() else Path(film_dir) / path
        return bool(rec and full.is_file() and rec.get("sha256") == jsonio.sha256(full))

    def spend_approval(self, film_dir: Path, approval_id: str) -> dict | None:
        return next((r for r in self._all(film_dir) if r.get("id") == approval_id and r.get("kind") == "spend"), None)

    def spend_covers(self, film_dir: Path, approval_id: str, rel_path: str) -> bool:
        rec = self.spend_approval(film_dir, approval_id)
        return bool(rec and rec.get("path") == rel_path)


def approvals():
    """The approvals store: slate.state.approvals when present, else the film-folder fallback."""
    mod = _module("slate.state.approvals")
    if mod is not None and all(hasattr(mod, n) for n in ("record", "is_approved", "latest", "spend_approval",
                                                          "spend_covers")):
        return mod
    return _FileApprovals()


def mode_check(film_dir: Path, action: str, **ctx: Any) -> dict | None:
    """The state module's mode rules for an action (parked films, drafts, autonomous approvals). None without it."""
    modes = _module("slate.state.modes")
    if modes is None or not hasattr(modes, "check"):
        return None
    return modes.check(film_dir, action, **ctx)


def is_locked(film_dir: Path, rel_path: str) -> bool:
    lock = _module("slate.state.lock")
    return bool(lock is not None and hasattr(lock, "is_locked") and lock.is_locked(film_dir, rel_path))


# ---------------------------------------------------------------------------------------------------------------
# Brand packs


def brand_pack(film: dict) -> dict:
    """The film's brand pack (brand.json), through slate.brand when it's there. {} when the film names no brand."""
    name = film.get("brand")
    if not name:
        return {}
    pack_mod = _module("slate.brand.pack")
    if pack_mod is not None and hasattr(pack_mod, "load"):
        try:
            return pack_mod.load(name)
        except (UsageError, MissingPrerequisite):
            return {}
    for folder in paths.brand_dirs():
        candidate = Path(folder) / str(name) / "brand.json"
        if candidate.is_file():
            data = jsonio.read(candidate)
            data.setdefault("_dir", str(candidate.parent))
            return data
    return {}


def brand_dir(film: dict) -> Path | None:
    name = film.get("brand")
    if not name:
        return None
    pack_mod = _module("slate.brand.pack")
    if pack_mod is not None and hasattr(pack_mod, "find"):
        try:
            return Path(pack_mod.find(name))
        except (UsageError, MissingPrerequisite):
            return None
    for folder in paths.brand_dirs():
        candidate = Path(folder) / str(name)
        if (candidate / "brand.json").is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------------------------------------------
# Timing (word times for beat maps)


def word_time(film_dir: Path, shot_id: str, word: str, occ: int = 1, which: str = "start") -> tuple[float, bool]:
    """Film seconds at which the voice says `word` in a shot (slate.timing.words.word_time). Never raises on a miss:
    the bool says whether the time is exact."""
    words = _module("slate.timing.words")
    if words is None or not hasattr(words, "word_time"):
        raise MissingPrerequisite("Beat mapping needs the film's word timings (slate.timing), which aren't built yet.",
                                  hint="Run `slate voice align` and `slate timing` first.")
    return words.word_time(film_dir, shot_id, word, occ=occ, which=which)


# ---------------------------------------------------------------------------------------------------------------
# Notify and jobs


def notify_module():
    """slate.notify when it's built (iMessage and push), else None."""
    return _module("slate.notify")


def notify_allowed() -> bool:
    """Never send a real message from tests, replay runs or evals."""
    from slate import replay  # noqa: PLC0415

    if replay.fake_vendors() or os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("SLATE_NO_NOTIFY") == "1":
        return False
    return True


def jobs_client():
    return _module("slate.jobs.client")


@contextlib.contextmanager
def hold(resource: str, *, timeout: float | None = None) -> Iterator[None]:
    """The machine-wide lock for a resource (slate.jobs.locks) when the queue is built; otherwise a no-op."""
    locks = _module("slate.jobs.locks")
    if locks is not None and hasattr(locks, "hold"):
        with locks.hold(resource, timeout=timeout, owner="slate gen"):
            yield
        return
    log.info(f"(the job queue isn't installed, so the {resource} lock is skipped)")
    yield
