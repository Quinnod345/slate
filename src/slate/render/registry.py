"""The renders registry: `renders/registry.json`, one entry per act render.

    {"entries": [{"act", "engine", "file", "frames": [in, out], "samples", "sha256", "made_at", "bundle_hash", …}]}

`frames` are film frames, `in` inclusive and `out` exclusive, the act's range in timing/shots.json when it was
rendered (the films' renders manifests used the same convention). `file` is relative to the film folder. Entries also
carry the proof the render passed, the shots it covers, its format and a `history` of every render and splice that made
the file (the films kept that history as a hand-written "made" note; here it's data).

Registering an act render locks the film's timeline (`timing/shots.json` and `timing/timeline.json`) through
`slate.state.lock`: once any act is rendered, re-timing would silently move picture against sound, so it has to be an
explicit, recorded decision (slate.state's `guard_timeline`). slate.state reads this registry to know the timeline is
locked, and `lock.record` notes what each render was cut from. The AE project build, review cuts and QA read it too.
"""

from __future__ import annotations

from pathlib import Path

from slate import gates, jsonio
from slate.errors import Refused, UsageError
from slate.render import deps
from slate.render.plan import Act

REGISTRY = Path("renders") / "registry.json"
SCHEMA = "renders_registry"
COMMAND = "slate render"
REQUIRED = ("act", "engine", "file", "frames", "samples", "sha256", "made_at", "bundle_hash")
# What "the timeline" means for the lock: the files every act render's frames were cut from.
TIMELINE_FILES = ("timing/shots.json", "timing/timeline.json")


def path(film_dir: Path) -> Path:
    return Path(film_dir) / REGISTRY


def load(film_dir: Path) -> dict:
    """The whole registry, checked against its schema. A film with no renders yet has an empty one."""
    data = jsonio.read(path(film_dir), default=None)
    if data is None:
        return {"entries": []}
    jsonio.require_valid(data, SCHEMA, what=str(path(film_dir)))
    return data


def entries(film_dir: Path) -> list[dict]:
    """Every registered act render, in act order."""
    return sorted(load(film_dir).get("entries", []), key=lambda e: (int(e["act"]), str(e.get("engine"))))


def find(film_dir: Path, act: int) -> dict | None:
    for entry in entries(film_dir):
        if int(entry["act"]) == int(act):
            return entry
    return None


def find_file(film_dir: Path, file: str | Path) -> dict | None:
    """The entry whose file is `file` (film-relative or absolute), if any."""
    film_dir = Path(film_dir)
    target = Path(file) if Path(file).is_absolute() else film_dir / file
    target = target.resolve()
    for entry in entries(film_dir):
        if (film_dir / entry["file"]).resolve() == target:
            return entry
    return None


def _normalize(film_dir: Path, entry: dict) -> dict:
    out = dict(entry)
    missing = [k for k in REQUIRED if k not in out]
    if missing:
        raise UsageError(f"A renders registry entry needs {', '.join(missing)}.")
    file = Path(out["file"])
    if file.is_absolute():
        try:
            out["file"] = file.resolve().relative_to(Path(film_dir).resolve()).as_posix()
        except ValueError as exc:
            raise UsageError(f"Registered renders live inside the film folder; {file} doesn't.") from exc
    else:
        out["file"] = file.as_posix()
    out["act"] = int(out["act"])
    out["frames"] = [int(out["frames"][0]), int(out["frames"][1])]
    if out["frames"][1] <= out["frames"][0]:
        raise UsageError(f"Registry frames must be [in, out) with out after in; got {out['frames']}.")
    out["made_at"] = out.get("made_at") or gates.now_iso()
    return out


def _write(film_dir: Path, items: list[dict]) -> None:
    data = jsonio.mark_generated({"entries": sorted(items, key=lambda e: int(e["act"]))}, COMMAND)
    jsonio.require_valid(data, SCHEMA, what="the renders registry")
    jsonio.write_atomic(path(film_dir), data)


def lock_timeline(film_dir: Path, entry: dict) -> dict:
    """Record the render's provenance and lock the timeline, through slate.state.lock. Returns what happened.

    slate.state treats the timeline as locked once any act render is registered here (`timeline_locked` reads this
    registry and writes the lock into film.lock.json the first time it sees one), so this is called after the registry
    is written. `record` notes what the render was cut from, so `slate status` can call it stale if the timing moves.
    A state module without `timeline_locked` gets the section 12 fallback: the timing files locked by content.
    """
    film_dir = Path(film_dir)
    lock = deps.state_lock()
    timing = [rel for rel in TIMELINE_FILES if (film_dir / rel).is_file()]
    lock.record(film_dir, entry["file"], producer=f"{COMMAND} --act {entry['act']}", inputs=timing,
                tool_version=f"{entry['engine']} (bundle {entry.get('bundle_hash') or 'n/a'})")
    if hasattr(lock, "timeline_locked"):
        if not lock.timeline_locked(film_dir):
            raise Refused("slate.state didn't see the registered render, so the timeline isn't locked.",
                          hint="slate status shows the film's state; run the render again once it's right.")
        return {"method": "timeline_locked", "files": timing}
    for rel in timing:
        lock.lock_asset(film_dir, rel, reason=f"act {entry['act']} is rendered ({entry['file']}); re-timing now "
                                              "moves picture against sound")
    return {"method": "lock_asset", "files": timing}


def register(film_dir: Path, entry: dict) -> None:
    """Add or replace the act's render and lock the timeline (see `register_and_lock`)."""
    register_and_lock(film_dir, entry)


def prepare_entry(film_dir: Path, entry: dict) -> dict:
    """The entry as it will be stored, checked against the schema, before any file moves."""
    new = _normalize(Path(film_dir), entry)
    jsonio.require_valid({"entries": [new]}, SCHEMA, what=f"the registry entry for act {new['act']}")
    return new


def write_entry(film_dir: Path, entry: dict) -> dict:
    """Add or replace the act's render in the registry (the render file must already be in place). Returns the entry.

    The entry replaces any earlier render of the same act; the earlier one's sha256 and date move into the new entry's
    history, so the record of what was rendered when is never lost.
    """
    film_dir = Path(film_dir)
    new = prepare_entry(film_dir, entry)
    if not (film_dir / new["file"]).is_file():
        raise UsageError(f"Register a render after it's in place; {new['file']} isn't there.")
    items = [e for e in load(film_dir).get("entries", [])]
    previous = next((e for e in items if int(e["act"]) == new["act"]), None)
    if previous is not None:
        history = list(previous.get("history") or [])
        history.append({"what": "replaced", "sha256": previous.get("sha256"), "made_at": previous.get("made_at"),
                        "engine": previous.get("engine"), "samples": previous.get("samples"),
                        "frames": previous.get("frames")})
        new["history"] = history + [h for h in (new.get("history") or []) if h not in history]
    items = [e for e in items if int(e["act"]) != new["act"]] + [new]
    _write(film_dir, items)
    return new


def register_and_lock(film_dir: Path, entry: dict) -> dict:
    """Register the act's render, then record it and lock the timeline. Returns {"method", "files"} for the lock."""
    new = write_entry(film_dir, entry)
    return lock_timeline(film_dir, new)


def update(film_dir: Path, act: int, *, history: dict | None = None, **changes) -> dict:
    """Change a registered entry in place (a splice's new sha256 and its history line). Returns the new entry."""
    film_dir = Path(film_dir)
    items = [e for e in load(film_dir).get("entries", [])]
    for i, e in enumerate(items):
        if int(e["act"]) == int(act):
            updated = {**e, **changes}
            if history is not None:
                updated["history"] = list(e.get("history") or []) + [history]
            items[i] = _normalize(film_dir, updated)
            _write(film_dir, items)
            return items[i]
    raise Refused(f"Act {act} has no registered render to update.", hint=f"slate render --act {act}")


def status(film_dir: Path, acts: list[Act], *, verify: bool = False) -> list[dict]:
    """Per act in timing/shots.json: rendered, moved (timing changed since), missing file, changed file, or not yet.

    Like the films' manifest generator, a render whose act range has moved is reported as needing a render. `verify`
    also hashes each file (slow for long acts) to catch a file changed behind the registry's back.
    """
    film_dir = Path(film_dir)
    reg = {int(e["act"]): e for e in entries(film_dir)}
    rows = []
    for a in acts:
        e = reg.pop(a.index, None)
        row = {"act": a.index, "shots": a.shots, "frames": [a.frame_in, a.frame_out]}
        if e is None:
            row.update(state="not rendered", entry=None)
        else:
            file = film_dir / e["file"]
            if not file.is_file():
                state = "missing file"
            elif list(e["frames"]) != [a.frame_in, a.frame_out]:
                state = "moved"
            elif verify and jsonio.sha256(file) != e["sha256"]:
                state = "changed on disk"
            else:
                state = "rendered"
            row.update(state=state, entry=e)
        rows.append(row)
    for act_index, e in sorted(reg.items()):
        rows.append({"act": act_index, "shots": None, "frames": e.get("frames"), "state": "act no longer in timing",
                     "entry": e})
    return rows
