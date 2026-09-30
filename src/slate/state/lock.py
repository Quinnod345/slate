"""film.lock.json: what made every derived file, so stale steps rebuild and approved work is never regenerated.

Every module that writes a derived file calls `record(film_dir, rel_path, producer=..., inputs=[...])` right after
writing it. The entry keeps the command that made it (starting with the CLI command, e.g. "slate voice tts", so status
and make can place it), the tool version, the SHA-256 of every input and of the file itself, and when it was made.

- `is_stale` recomputes the hashes: a file is stale when it's missing, unrecorded, changed since it was made, when any
  input changed, or when an input is itself stale.
- Approved takes, score takes and plates are locked by content hash (`lock_asset`). `guard_regenerate` refuses to
  make a locked asset again without an explicit force and a person's spend approval id; `record` refuses to write over
  a locked entry with different content.
- The timeline locks once any act render is registered in renders/registry.json (`timeline_locked`,
  `guard_timeline`), because moving a frame after that would desync every render.
- Shot ids freeze when the storyboard passes its gates (`freeze_shot_ids`); the story stage checks renumbering against
  `frozen_shot_ids` and `check_renumber`.
- A derived film reuses its parent's files by reference: its entries carry `ref` (film, folder, path, hash) and nothing
  is copied. `resolve` finds the real file.

Inputs are film-relative or absolute paths, or named inputs that aren't film files: `brand:<name>` (the brand pack's
brand.json), `film:<dotted.key>` (one film.json value, e.g. film:targets.runtime_s, so a new pin doesn't make
everything made from the film's targets stale) and `format:<name>`. Any other `scheme:name` keeps the producer's own
marker and is reported as not checkable here, never guessed fresh or stale.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from slate import __version__, jsonio
from slate.errors import Refused, SpendRefused, UsageError
from slate.state import _util

LOCK_FILE = "film.lock.json"
SCHEMA = "film_lock"
RENDER_REGISTRY = "renders/registry.json"
TIMELINE_FILES = ("timing/shots.json", "timing/timeline.json")
GENERATED = "do not edit by hand: slate (film state); modules call slate.state.lock.record()"


def empty() -> dict:
    return {"schema": 1, "generated": GENERATED, "files": {}, "frozen": {"shot_ids": None, "timeline": None}}


def load(film_dir: Path) -> dict:
    data = jsonio.read(Path(film_dir) / LOCK_FILE, default=None)
    if not isinstance(data, dict):
        return empty()
    data.setdefault("files", {})
    frozen = data.setdefault("frozen", {})
    frozen.setdefault("shot_ids", None)
    frozen.setdefault("timeline", None)
    return data


def save(film_dir: Path, data: dict) -> None:
    data = {**data, "generated": GENERATED}
    jsonio.require_valid(data, SCHEMA, what="film.lock.json")
    with _util.state_lock(film_dir):
        jsonio.write_atomic(Path(film_dir) / LOCK_FILE, data)


def _edit(film_dir: Path, change) -> Any:
    with _util.state_lock(film_dir):
        data = load(film_dir)
        out = change(data)
        save(film_dir, data)
        return out


def entry(film_dir: Path, rel_path: str) -> dict | None:
    return load(film_dir)["files"].get(_util.film_rel(film_dir, rel_path))


def entries(film_dir: Path, *, prefix: str | None = None, producer: str | None = None) -> dict[str, dict]:
    """Lock entries, optionally under a folder prefix or made by a command (producer prefix, e.g. 'slate voice')."""
    out = {}
    for rel, e in load(film_dir)["files"].items():
        if prefix and not (rel == prefix.rstrip("/") or rel.startswith(prefix.rstrip("/") + "/")):
            continue
        if producer and not str(e.get("producer", "")).startswith(producer):
            continue
        out[rel] = e
    return out


# ---------------------------------------------------------------------------------------------------------------
# Resolving and hashing (derived films read their parent's files by reference)


def _parent_dir(ref: dict) -> Path:
    candidate = Path(ref.get("dir") or "")
    if (candidate / "film.json").is_file():
        return candidate
    from slate import paths  # noqa: PLC0415

    return paths.film_dir(ref.get("film"))


def resolve(film_dir: Path, rel_path: str) -> Path:
    """The real file for a film-relative path: the film's own file, else its parent's file when it's a reference."""
    film_dir = Path(film_dir)
    rel = _util.film_rel(film_dir, rel_path)
    own = film_dir / rel
    if own.exists():
        return own
    e = load(film_dir)["files"].get(rel)
    if e and e.get("ref"):
        try:
            parent = _parent_dir(e["ref"])
        except UsageError:
            return own
        return resolve(parent, e["ref"]["path"])
    return own


def _input_path(film_dir: Path, key: str) -> Path:
    p = Path(key)
    if p.is_absolute():
        return p
    return resolve(film_dir, key)


def _hash(hasher: _util.Hasher, path: Path) -> str | None:
    digest, _kind = hasher.any(path)
    return digest


# Named inputs that aren't film files: "brand:<name>" (the pack's brand.json), "film:<dotted.key>" (one film.json
# value, so a new pin doesn't make everything made from the film's targets stale) and "format:<name>".
SCHEME_RE = re.compile(r"^([a-z][a-z0-9_-]*):([^/].*)$")
UNVERIFIABLE = "unverifiable"


def _named_input(film_dir: Path, key: str) -> tuple[bool, str | None]:
    """(is a named input, its hash now). An unknown scheme hashes to UNVERIFIABLE: it can't be checked here."""
    m = SCHEME_RE.match(key)
    if not m or (Path(film_dir) / key).exists():
        return False, None
    scheme, name = m.groups()
    from slate.errors import SlateError  # noqa: PLC0415
    from slate.state import deps  # noqa: PLC0415

    if scheme == "brand":
        try:
            pack_dir = deps.brand_find(name)
        except SlateError:
            return True, None
        path = Path(pack_dir) / "brand.json"
        return True, (jsonio.sha256(path) if path.is_file() else None)
    if scheme == "film":
        data = jsonio.read(Path(film_dir) / "film.json", default=None)
        node: Any = data
        for part in name.split("."):
            if not isinstance(node, dict) or part not in node:
                return True, None
            node = node[part]
        return True, jsonio.sha256_obj(node)
    if scheme == "format":
        try:
            fmt, _src = deps.format_load(name)
        except SlateError:
            return True, None
        return True, jsonio.sha256_obj(fmt)
    return True, UNVERIFIABLE


def _input_hash(film_dir: Path, key: str, hasher: _util.Hasher) -> str | None:
    named, digest = _named_input(film_dir, key)
    if named:
        return digest
    return _hash(hasher, _input_path(film_dir, key))


def hash_inputs(film_dir: Path, inputs: Any, hasher: _util.Hasher | None = None) -> dict[str, str | None]:
    """{key: sha256} for a list of paths, or a dict whose None values get hashed now (given hashes are kept)."""
    own = hasher is None
    hasher = hasher or _util.Hasher(film_dir)
    out: dict[str, str | None] = {}
    if inputs is None:
        return out
    if isinstance(inputs, (str, Path)):
        inputs = [inputs]
    if isinstance(inputs, dict):
        items = list(inputs.items())
    else:
        items = [(p, None) for p in inputs]
    for path, given in items:
        key = str(path) if SCHEME_RE.match(str(path)) else _util.input_key(film_dir, path)
        out[key] = given if given else _input_hash(film_dir, key, hasher)
    if own:
        hasher.save()
    return out


# ---------------------------------------------------------------------------------------------------------------
# Recording


def record(film_dir: Path, rel_path: str, *, producer: str, inputs: Any, tool_version: str | None = None,
           note: str | None = None) -> dict:
    """Record a derived file right after writing it. Returns the film.lock.json entry.

    `inputs` is a list of paths (hashed now) or {path: sha256}. Paths are film-relative or absolute. A locked entry is
    never replaced by different content here: regenerate it through `guard_regenerate` first.
    """
    film_dir = Path(film_dir)
    rel = _util.film_rel(film_dir, rel_path)
    if not (producer or "").strip():
        raise UsageError("A lock record needs its producer: the command that made the file (e.g. 'slate timing').")
    target = film_dir / rel
    if not target.exists():
        raise UsageError(f"{rel} doesn't exist; record a file after writing it.")
    with _util.Hasher(film_dir) as hasher:
        digest, kind = hasher.any(target)
        hashed = hash_inputs(film_dir, inputs, hasher)

    def change(data: dict) -> dict:
        old = data["files"].get(rel)
        if old and old.get("locked") and old.get("sha256") != digest:
            raise Refused(f"{rel} is locked ({(old.get('lock') or {}).get('reason', 'approved')}); Slate won't record "
                          "different content over it.",
                          hint="If a person wants it redone, they approve the spend (slate approve spend "
                               f"{rel} --by NAME) and the command runs with --force --approval <id>.")
        new = {
            "producer": producer.strip(),
            "tool_version": tool_version or f"slate {__version__}",
            "inputs": hashed,
            "sha256": digest,
            "kind": kind,
            "locked": bool(old and old.get("locked")),
            "lock": (old or {}).get("lock") if old and old.get("locked") else None,
            "made_at": _util.now(),
            "ref": None,
            "note": note,
            "history": list((old or {}).get("history") or [])[-20:],
        }
        data["files"][rel] = new
        return new

    return _edit(film_dir, change)


def add_ref(film_dir: Path, rel_path: str, *, parent_id: str, parent_dir: Path, parent_rel: str,
            sha256: str | None, kind: str = "file", locked: bool = False, lock_reason: str | None = None,
            note: str | None = None) -> dict:
    """A derived film's reuse of one parent file by reference: an entry that points into the parent, no copy."""
    add_refs(film_dir, [{"path": rel_path, "parent_path": parent_rel, "sha256": sha256, "kind": kind,
                         "locked": locked, "lock_reason": lock_reason, "note": note}],
             parent_id=parent_id, parent_dir=parent_dir)
    return load(film_dir)["files"][_util.film_rel(film_dir, rel_path)]


def add_refs(film_dir: Path, refs: list[dict], *, parent_id: str, parent_dir: Path) -> int:
    """Many references at once (a derivation): each {path, parent_path, sha256, kind, locked, lock_reason}.
    One write of film.lock.json, however many files the parent has."""
    film_dir = Path(film_dir)
    parent_abs = str(Path(parent_dir).resolve())
    stamp = _util.now()

    def change(data: dict) -> int:
        for r in refs:
            rel = _util.film_rel(film_dir, r["path"])
            data["files"][rel] = {
                "producer": f"slate variant (reference to {parent_id})",
                "tool_version": f"slate {__version__}",
                "inputs": {},
                "sha256": r.get("sha256"),
                "kind": r.get("kind") or "file",
                "locked": bool(r.get("locked")),
                "lock": ({"reason": r.get("lock_reason") or f"locked in {parent_id}", "at": stamp,
                          "sha256": r.get("sha256"), "by": None} if r.get("locked") else None),
                "made_at": stamp,
                "ref": {"film": parent_id, "dir": parent_abs, "path": r.get("parent_path") or rel,
                        "sha256": r.get("sha256"), "kind": r.get("kind") or "file"},
                "note": r.get("note"),
                "history": [],
            }
        return len(refs)

    return _edit(film_dir, change)


def inherit_frozen_ids(film_dir: Path, ids: list[str], *, parent_id: str) -> None:
    """A derived film keeps its parent's frozen shot ids, so reused takes and beats still mean the same shots."""
    if not ids:
        return

    def change(data: dict) -> None:
        data["frozen"]["shot_ids"] = {"ids": list(ids), "at": _util.now(), "source": f"{parent_id}:script/storyboard.json",
                                      "sha256": None, "inherited_from": parent_id, "history": []}

    _edit(film_dir, change)


# ---------------------------------------------------------------------------------------------------------------
# Staleness


def explain(film_dir: Path, rel_path: str, *, hasher: _util.Hasher | None = None, deep: bool = True,
            _memo: dict | None = None) -> dict:
    """Why a file is fresh or not: {path, state, reasons, locked, ref}.

    States: fresh · stale · missing · unrecorded (it exists, but nothing records what made it) · locked (approved and
    unchanged; `reasons` still says when its inputs moved) · tampered (locked, but the file changed on disk).
    """
    film_dir = Path(film_dir)
    rel = _util.film_rel(film_dir, rel_path)
    memo = _memo if _memo is not None else {}
    key = (str(film_dir.resolve()), rel)
    if key in memo:
        return memo[key]
    memo[key] = {"path": rel, "state": "fresh", "reasons": ["(checking)"], "locked": False, "ref": None}
    own = hasher is None
    hasher = hasher or _util.Hasher(film_dir)
    try:
        result = _explain(film_dir, rel, hasher, deep, memo)
    finally:
        if own:
            hasher.save()
    memo[key] = result
    return result


def _explain(film_dir: Path, rel: str, hasher: _util.Hasher, deep: bool, memo: dict) -> dict:
    files = load(film_dir)["files"]
    e = files.get(rel)
    out = {"path": rel, "state": "fresh", "reasons": [], "locked": bool(e and e.get("locked")),
           "ref": (e or {}).get("ref"), "producer": (e or {}).get("producer")}
    own_file = film_dir / rel
    if e is None:
        if own_file.exists():
            out.update(state="unrecorded", reasons=["nothing records what made it"])
        else:
            out.update(state="missing", reasons=["the file isn't there"])
        return out
    if e.get("ref") and not own_file.exists():
        ref = e["ref"]
        try:
            parent = _parent_dir(ref)
        except UsageError:
            out.update(state="missing", reasons=[f"the parent film {ref.get('film')} can't be found"])
            return out
        target = resolve(parent, ref["path"])
        current = _hash(hasher, target)
        if current is None:
            out.update(state="missing", reasons=[f"the parent's {ref['path']} is gone"])
            return out
        if current != ref.get("sha256"):
            out["reasons"].append(f"the parent's {ref['path']} changed since this film was derived")
        if deep:
            upstream = explain(parent, ref["path"], deep=True, _memo=memo)
            if upstream["state"] in ("stale", "tampered"):
                out["reasons"].append(f"the parent's {ref['path']} is {upstream['state']}")
        out["state"] = "stale" if out["reasons"] else ("locked" if e.get("locked") else "fresh")
        return out
    current = _hash(hasher, own_file)
    if current is None:
        out.update(state="missing", reasons=["the file isn't there"])
        return out
    if e.get("locked"):
        lock_sha = (e.get("lock") or {}).get("sha256") or e.get("sha256")
        if lock_sha and current != lock_sha:
            out.update(state="tampered", reasons=["locked by content hash, but the file on disk changed"])
            return out
    elif current != e.get("sha256"):
        out["reasons"].append("changed since it was made (generated files aren't edited by hand)")
    for key, digest in (e.get("inputs") or {}).items():
        now = _input_hash(film_dir, key, hasher)
        if now == UNVERIFIABLE:
            out.setdefault("notes", []).append(f"input {key} can't be checked here")
            continue
        if now != digest:
            out["reasons"].append(f"input {key} {'is gone' if now is None else 'changed'}")
            continue
        if deep and not Path(key).is_absolute() and key in files and key != rel:
            upstream = explain(film_dir, key, hasher=hasher, deep=True, _memo=memo)
            if upstream["state"] in ("stale", "tampered", "missing"):
                out["reasons"].append(f"input {key} is {upstream['state']}")
    if e.get("locked"):
        out["state"] = "locked"
    else:
        out["state"] = "stale" if out["reasons"] else "fresh"
    return out


def is_stale(film_dir: Path, rel_path: str, *, deep: bool = True, hasher: _util.Hasher | None = None) -> bool:
    """True when the file must be made again: missing, unrecorded, changed since made, an input changed, or (deep) an
    input is itself stale. Recomputes hashes (cached by size and mtime). A locked asset is never stale by this test
    (it's kept on purpose); `explain` still says when its inputs moved."""
    state = explain(film_dir, rel_path, hasher=hasher, deep=deep)["state"]
    return state in ("stale", "missing", "unrecorded", "tampered")


# ---------------------------------------------------------------------------------------------------------------
# Locked assets


def lock_asset(film_dir: Path, rel_path: str, *, reason: str, by: str | None = None) -> None:
    """Lock a file by its content hash (approved takes, score takes, plates). Works on unrecorded files too."""
    film_dir = Path(film_dir)
    rel = _util.film_rel(film_dir, rel_path)
    if not (reason or "").strip():
        raise UsageError("Say why the asset is locked (e.g. 'approved take, Quinn 2026-10-02').")
    target = resolve(film_dir, rel)
    with _util.Hasher(film_dir) as hasher:
        digest, kind = hasher.any(target)
    if digest is None:
        raise UsageError(f"{rel} doesn't exist, so there's nothing to lock.")

    def change(data: dict) -> None:
        e = data["files"].get(rel)
        if e is None:
            e = {"producer": "unrecorded (locked by approval)", "tool_version": None, "inputs": {}, "sha256": digest,
                 "kind": kind, "locked": False, "lock": None, "made_at": _util.now(), "ref": None, "note": None,
                 "history": []}
            data["files"][rel] = e
        e["locked"] = True
        e["lock"] = {"reason": reason.strip(), "at": _util.now(), "sha256": digest, "by": by}
        e.setdefault("history", []).append({"event": "locked", "at": _util.now(), "sha256": digest, "by": by,
                                            "reason": reason.strip()})

    _edit(film_dir, change)


def is_locked(film_dir: Path, rel_path: str) -> bool:
    """True for an asset locked by content hash, and for the timeline's own files once the timeline is locked."""
    rel = _util.film_rel(film_dir, rel_path)
    e = load(film_dir)["files"].get(rel)
    if e and e.get("locked"):
        return True
    return rel in TIMELINE_FILES and timeline_locked(film_dir)


def locked_assets(film_dir: Path) -> list[str]:
    return sorted(rel for rel, e in load(film_dir)["files"].items() if e.get("locked"))


def tampered(film_dir: Path) -> list[str]:
    """Locked assets whose file changed on disk (an approved take overwritten): these must be put back."""
    out = []
    with _util.Hasher(film_dir) as hasher:
        for rel in locked_assets(film_dir):
            if explain(film_dir, rel, hasher=hasher, deep=False)["state"] == "tampered":
                out.append(rel)
    return out


def guard_regenerate(film_dir: Path, rel_path: str, *, force: bool = False, approval_id: str | None = None,
                     by: str | None = None) -> dict:
    """Call before making a file again. Unlocked files pass. A locked one needs force AND a person's spend approval
    that covers it; then the lock is lifted for this regeneration and the override is written into its history."""
    film_dir = Path(film_dir)
    rel = _util.film_rel(film_dir, rel_path)
    e = load(film_dir)["files"].get(rel)
    if not (e and e.get("locked")):
        return {"path": rel, "allowed": True, "locked": False}
    reason = (e.get("lock") or {}).get("reason", "approved")
    if not force:
        raise Refused(f"{rel} is locked ({reason}). Slate never regenerates approved work on its own.",
                      hint="If a person wants it redone: slate approve spend "
                           f"{rel} --by NAME, then run the command again with --force --approval <id>.")
    if not approval_id:
        raise SpendRefused(f"Regenerating {rel} spends again and replaces approved work, so it needs a person's "
                           "spend approval id as well as --force.",
                           hint=f"slate approve spend {rel} --by NAME --note \"why\" prints the id.")
    from slate.state import approvals  # noqa: PLC0415

    if not approvals.spend_covers(film_dir, approval_id, rel):
        raise SpendRefused(f"Approval {approval_id} isn't a spend approval that covers {rel}.",
                           hint=f"slate approve spend {rel} --by NAME records one; `slate film show` lists approvals.")

    def change(data: dict) -> None:
        cur = data["files"][rel]
        cur.setdefault("history", []).append({"event": "unlocked to regenerate", "at": _util.now(),
                                              "approval_id": approval_id, "by": by,
                                              "previous_sha256": cur.get("sha256"), "reason": reason})
        cur["locked"] = False
        cur["lock"] = None

    _edit(film_dir, change)
    return {"path": rel, "allowed": True, "locked": True, "approval_id": approval_id, "was": reason}


# ---------------------------------------------------------------------------------------------------------------
# The timeline lock


def registered_act_renders(film_dir: Path) -> list[dict]:
    try:
        reg = jsonio.read(Path(film_dir) / RENDER_REGISTRY, default=None)
    except UsageError:
        return []  # a registry that doesn't parse registers nothing; the render stage reports it
    if not isinstance(reg, dict):
        return []
    return [e for e in (reg.get("entries") or []) if isinstance(e, dict) and e.get("act") is not None]


def timeline_locked(film_dir: Path) -> bool:
    """True once any act render is registered. The first time it's seen, the lock is written into film.lock.json, so
    removing a render later never quietly unlocks the timeline."""
    film_dir = Path(film_dir)
    frozen = load(film_dir)["frozen"].get("timeline")
    if frozen and frozen.get("locked"):
        return True
    renders = registered_act_renders(film_dir)
    if not renders:
        return False

    def change(data: dict) -> None:
        data["frozen"]["timeline"] = {
            "locked": True, "at": _util.now(), "reason": "an act render is registered in renders/registry.json",
            "renders": [{"act": r.get("act"), "file": r.get("file"), "frames": r.get("frames")} for r in renders][:50],
            "history": [],
        }

    _edit(film_dir, change)
    return True


def guard_timeline(film_dir: Path, *, force: bool = False, approved_by: str | None = None,
                   reason: str | None = None) -> dict:
    """Call before rewriting timing/shots.json or timing/timeline.json. Once act renders exist, a change needs force,
    a person's name and a reason (and then every render is stale and gets re-rendered)."""
    if not timeline_locked(film_dir):
        return {"allowed": True, "locked": False}
    if not force:
        raise Refused("The timeline is locked: act renders are registered, and moving a frame now would desync them.",
                      hint="If a person decides to re-time and re-render, run with --force --approved-by NAME "
                           "--reason \"…\".")
    if not (approved_by or "").strip() or not (reason or "").strip():
        raise Refused("Changing a locked timeline needs the person's name (--approved-by) and a reason (--reason).")

    def change(data: dict) -> None:
        data["frozen"]["timeline"].setdefault("history", []).append(
            {"event": "changed while locked", "at": _util.now(), "approved_by": approved_by, "reason": reason})

    _edit(film_dir, change)
    return {"allowed": True, "locked": True, "approved_by": approved_by}


# ---------------------------------------------------------------------------------------------------------------
# Frozen shot ids


def freeze_shot_ids(film_dir: Path, ids: Iterable[str], *, source: str | None = "script/storyboard.json") -> dict:
    """Freeze shot ids when the storyboard passes its gates. Later freezes only add new ids; none is ever removed."""
    film_dir = Path(film_dir)
    new_ids = [str(i) for i in ids]
    if not new_ids:
        raise UsageError("No shot ids to freeze.")
    digest = None
    if source:
        digest = _util.Hasher(film_dir).sha(resolve(film_dir, source))

    def change(data: dict) -> dict:
        cur = data["frozen"].get("shot_ids")
        if not cur:
            cur = {"ids": [], "at": _util.now(), "source": source, "sha256": digest, "inherited_from": None,
                   "history": []}
            data["frozen"]["shot_ids"] = cur
        added = [i for i in new_ids if i not in cur["ids"]]
        cur["ids"].extend(added)
        cur.setdefault("history", []).append({"at": _util.now(), "added": added, "source": source, "sha256": digest})
        return cur

    return _edit(film_dir, change)


def frozen_shot_ids(film_dir: Path) -> list[str]:
    """The shot ids that froze at the storyboard gate (empty before the storyboard first passed).

    film.lock.json's record comes first; without one, a storyboard's own record counts (script/storyboard.json
    "shot_ids", written only by a passing `slate script build`)."""
    cur = load(film_dir)["frozen"].get("shot_ids")
    if cur and cur.get("ids"):
        return list(cur["ids"])
    try:
        sb = jsonio.read(resolve(film_dir, "script/storyboard.json"), default=None)
    except UsageError:
        return []
    ids = ((sb or {}).get("shot_ids") or {}).get("ids") if isinstance(sb, dict) else None
    if not isinstance(ids, list):
        return []
    return [str(i.get("id")) if isinstance(i, dict) else str(i) for i in ids if i]


def check_renumber(film_dir: Path, mapping: dict[str, str]) -> None:
    """Refuse a renumber that moves a frozen id ({old id: new id}). Cutting a shot or adding new ids is fine."""
    frozen = set(frozen_shot_ids(film_dir))
    moved = {old: new for old, new in mapping.items() if old in frozen and new != old}
    taken = {old: new for old, new in mapping.items() if old not in frozen and new in frozen and new != old}
    if moved or taken:
        pairs = ", ".join(f"{a}→{b}" for a, b in {**moved, **taken}.items())
        raise Refused(f"Shot ids are frozen (the storyboard passed its gates), and this would renumber them: {pairs}.",
                      hint="Keep every existing id; give new shots new ids, and let cut shots' ids go unused.")


def clock_frozen_reason(film_dir: Path) -> str | None:
    if timeline_locked(film_dir):
        return "the timeline is locked (act renders are registered)"
    cur = load(film_dir)["frozen"].get("shot_ids")
    if cur:
        return f"shot ids froze on {str(cur.get('at', ''))[:10]}"
    if frozen_shot_ids(film_dir):
        return "shot ids froze when the storyboard passed its gates"
    return None
