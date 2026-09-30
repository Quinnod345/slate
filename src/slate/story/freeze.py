"""Shot ids freeze at the build gate.

The first `slate script build` that passes freezes the film's shot ids: the storyboard records each output id with the
id it was written as in the script (its `source_id`; the same unless the script renumbers). Scenes, beats, events,
sound cues and review notes all name shots by id, so a later build may add and cut shots, but it may not move an id to
another shot or give a shot a new id once any file under scenes/ uses the ids. That refusal is the gate
`script.shot_ids`. A cut shot whose id a scene still uses is refused too, because the scene would point at nothing.

The storyboard's own record (script/storyboard.json "shot_ids") is the reference, because it keeps each id's
source id and storyboard.json is only ever written by a passing build. The freeze is also recorded in film.lock.json
through the state module (`slate.state.lock.freeze_shot_ids`), whose `frozen_shot_ids` is the fallback reader. Both
modules apply the plan's one rule, a renumber is refused once any scene uses the ids (docs/PLAN.md §3), with one
scanner for "a scene uses it" (`slate.state.lock.scene_references`); `slate.state.lock.check_renumber` is the same
rule for callers that hold an {old id: new id} map rather than a planned script.
"""

from __future__ import annotations

from pathlib import Path

from slate import jsonio
from slate.gates import GateResult
from slate.state.lock import scene_references
from slate.story.model import Plan

STORYBOARD = Path("script") / "storyboard.json"


def frozen(film_dir: Path) -> list[dict] | None:
    """The frozen ids, [{id, source_id}], or None before the first passing build."""
    sb = jsonio.read(Path(film_dir) / STORYBOARD, default=None)
    if isinstance(sb, dict) and isinstance(sb.get("shot_ids"), dict) and sb["shot_ids"].get("ids"):
        return [dict(x) for x in sb["shot_ids"]["ids"]]
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        return None
    try:
        ids = state_lock.frozen_shot_ids(Path(film_dir))
    except Exception:  # noqa: BLE001 - no state record is the same as no freeze
        return None
    return [{"id": str(i), "source_id": str(i)} for i in ids] or None


def record_freeze(film_dir: Path, ids: list[str]) -> None:
    """Record the freeze in film.lock.json through the state module (it keeps every id that ever froze)."""
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        return
    try:
        state_lock.freeze_shot_ids(Path(film_dir), ids, source=str(STORYBOARD))
    except Exception as exc:  # noqa: BLE001 - the storyboard's own record still holds the freeze
        from slate import log  # noqa: PLC0415

        log.warn(f"couldn't record the frozen shot ids in film.lock.json: {getattr(exc, 'message', exc)}")


def check(film_dir: Path, plan: Plan) -> GateResult:
    """script.shot_ids: is this build allowed to change the ids it would change?"""
    before = frozen(film_dir)
    new_ids = [{"id": s.id, "source_id": s.source_id} for s in plan.shots]
    if not before:
        return GateResult.pass_("script.shot_ids", f"{len(new_ids)} shot ids freeze at this build",
                                details={"frozen": False})
    by_source_new = {x["source_id"]: x["id"] for x in new_ids}
    by_id_new = {x["id"]: x["source_id"] for x in new_ids}
    moved, reassigned, cut = [], [], []
    for old in before:
        oid, osrc = old["id"], old.get("source_id", old["id"])
        if osrc in by_source_new and by_source_new[osrc] != oid:
            moved.append(f"{oid}→{by_source_new[osrc]}")
        elif oid in by_id_new and by_id_new[oid] != osrc:
            reassigned.append(f"{oid} now names {by_id_new[oid]} (was {osrc})")
        elif osrc not in by_source_new and oid not in by_id_new:
            cut.append(oid)
    frozen_ids = [x["id"] for x in before]
    refs = scene_references(film_dir, frozen_ids)
    renumber = moved + reassigned
    if renumber and refs:
        users = [f"{i}: {', '.join(refs[i][:3])}" for i in sorted(refs)]
        return GateResult.fail(
            "script.shot_ids",
            f"shot ids are frozen and scenes use them, so this renumber is refused ({', '.join(renumber[:6])})",
            evidence=users[:30] + ["keep the frozen ids (write them as-written in script.json and give new shots new "
                                   "ids), or change the scenes first"],
            details={"renumber": renumber, "scene_refs": refs})
    dangling = [c for c in cut if c in refs]
    if dangling:
        return GateResult.fail(
            "script.shot_ids", f"{', '.join(dangling)} would be cut but scenes still use {'it' if len(dangling) == 1 else 'them'}",
            evidence=[f"{i}: {', '.join(refs[i][:3])}" for i in dangling] + ["remove the scene code for the cut shot first"],
            details={"cut": cut, "scene_refs": refs})
    added = [x["id"] for x in new_ids if x["id"] not in set(frozen_ids)]
    note = []
    if renumber:
        note.append(f"renumbered before any scene used the ids: {', '.join(renumber[:12])}")
    if added:
        note.append(f"new shots: {', '.join(added[:12])}")
    if cut:
        note.append(f"cut: {', '.join(cut[:12])}")
    return GateResult.pass_("script.shot_ids", "shot ids hold" + (f" ({'; '.join(note)})" if note else ""),
                            evidence=note, details={"renumber": renumber, "added": added, "cut": cut, "frozen": True})
