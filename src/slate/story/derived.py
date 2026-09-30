"""Derived films' scripts: a cut-down keeps some of its parent's shots, a translation keeps all of them.

`slate variant` (film state) writes the child's script/script.json with the keys honoured here:

- "derived_from": {"film", "path", "sha256"}: the parent's script as it was when the child was made;
- "cut": {"target_s", "keep": [shot ids]}: a cut-down. Until the child writes acts of its own, its script is the
  parent's shots named in `keep`, in the parent's order, each keeping its id: the ids froze in the parent, and the
  reused takes, beats and plates name shots by id;
- "translate": {"from", "to"}: a translation. The child writes its own acts, and every shot keeps its parent's id for
  the same reason.

An aspect variant has no script of its own: it follows its parent's script and storyboard by reference, so building
here would only fork them. The build says so and points at the parent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from slate import jsonio, paths
from slate.errors import MissingPrerequisite, Refused, SlateError
from slate.gates import GateResult
from slate.story.model import SCRIPT, act_shots, load_script, remap_text, renumber_map


@dataclass
class Derived:
    kind: str | None = None  # "cut" | "translate" | None
    parent_id: str | None = None
    parent_dir: Path | None = None
    parent_script: Path | None = None
    parent_ids: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    changed: bool = False  # the parent's script changed since the child was made

    @property
    def label(self) -> str:
        return {"cut": "cut-down", "translate": "translation"}.get(self.kind or "", "derived film")


def _parent_dir(film_dir: Path, film: dict, script: dict) -> tuple[str | None, Path | None]:
    parent = film.get("parent") if isinstance(film.get("parent"), dict) else {}
    pid = parent.get("id") or (script.get("derived_from") or {}).get("film")
    candidate = Path(parent.get("dir") or "")
    if parent.get("dir") and (candidate / "film.json").is_file():
        return pid, candidate
    if pid:
        try:
            found = paths.film_dir(pid)
        except SlateError:
            return pid, None
        if (Path(found) / "film.json").is_file():
            return pid, Path(found)
    return pid, None


def _resolve(film_dir: Path, rel: str) -> Path:
    from slate.timing.takes import film_file  # noqa: PLC0415 (one resolver for every film-relative read)

    return film_file(film_dir, rel)


def _output_acts(script: dict) -> list[dict]:
    """The parent's acts with the ids its storyboard uses: a script that renumbers ("ids": "renumber") writes draft
    ids, so they're mapped to the output ids first, mentions included, as the parent's own build does."""
    acts = copy.deepcopy(script.get("acts") or [])
    if script.get("ids") == "renumber":
        acts = remap_text(acts, renumber_map(script))
    return acts


def _ids(acts: list[dict]) -> list[str]:
    return [str(sh.get("id")) for act in acts for sh in act_shots(act) if sh.get("id")]


def load(film_dir: Path, film: dict) -> tuple[dict, Derived]:
    """The script to build, and what kind of derived film this is (kind None for an ordinary film)."""
    film_dir = Path(film_dir)
    own = film_dir / SCRIPT
    if not own.is_file():
        real = _resolve(film_dir, SCRIPT.as_posix())
        if real.is_file() and real.resolve() != own.resolve():
            parent = (film.get("parent") or {}).get("id") or "its parent"
            raise Refused(f"{film.get('id') or film_dir.name} follows {parent}'s script and storyboard by reference "
                          "(a new aspect of the same film), so there's nothing to build here.",
                          hint=f"Edit {parent}'s script/script.json and run `slate script build --film {parent}`; "
                               "this film reads the result.")
        return load_script(film_dir), Derived()  # raises the plain "there's no script yet"
    script = load_script(film_dir)
    if not isinstance(script, dict) or not (script.get("cut") or script.get("translate")):
        return script, Derived()
    info = Derived(kind="cut" if script.get("cut") else "translate")
    info.parent_id, info.parent_dir = _parent_dir(film_dir, film, script)
    if info.parent_dir is None:
        raise MissingPrerequisite(f"This {info.label}'s parent film {info.parent_id or '(unnamed)'} can't be found.",
                                  hint="Check film.json's parent (id and dir); the parent's folder has to be here.")
    rel = str((script.get("derived_from") or {}).get("path") or SCRIPT.as_posix())
    info.parent_script = _resolve(info.parent_dir, rel)
    if not info.parent_script.is_file():
        raise MissingPrerequisite(f"{info.parent_id}'s script ({rel}) isn't there, and this {info.label} is made "
                                  "from it.", hint=f"Restore {info.parent_id}'s script, or build this film's own acts.")
    parent_script = jsonio.read(info.parent_script)
    parent_acts = _output_acts(parent_script if isinstance(parent_script, dict) else {})
    info.parent_ids = _ids(parent_acts)
    pinned = (script.get("derived_from") or {}).get("sha256")
    info.changed = bool(pinned) and jsonio.sha256(info.parent_script) != pinned
    if info.kind == "cut" and not script.get("acts"):
        script = _cut(script, parent_script, parent_acts, info)
    return script, info


def _cut(script: dict, parent_script: dict, parent_acts: list[dict], info: Derived) -> dict:
    """The parent's acts reduced to the shots in cut.keep (parent order, output ids unchanged)."""
    cut = script.get("cut") or {}
    keep = cut.get("keep") if isinstance(cut, dict) else None
    info.keep = [str(k) for k in keep] if isinstance(keep, list) else []
    if not info.keep:
        info.problems.append(f"this cut-down keeps no shots yet: list the {info.parent_id} shot ids to keep in "
                             "script/script.json cut.keep (in any order; the parent's order is kept)")
        return script
    unknown = [k for k in info.keep if k not in info.parent_ids]
    if unknown:
        info.problems.append(f"cut.keep names shots {info.parent_id} doesn't have: {', '.join(unknown)} (its shots: "
                             f"{', '.join(info.parent_ids[:40])})")
        return script
    wanted = set(info.keep)
    acts = []
    for act in parent_acts:
        shots = [sh for sh in act_shots(act) if str(sh.get("id")) in wanted]
        if shots:
            kept = {k: v for k, v in act.items() if k not in ("shots", "lines")}
            kept["shots"] = shots
            acts.append(kept)
    out = {k: copy.deepcopy(v) for k, v in parent_script.items() if k not in ("acts", "ids", "id_prefix")}
    out.update({k: copy.deepcopy(v) for k, v in script.items() if k != "acts"})
    out["acts"] = acts  # ids as written: they are the parent's output ids, frozen there
    return out


def gate(info: Derived, plan_ids: list[str]) -> GateResult | None:
    """script.derived: the cut or the translation still lines up with its parent. None for an ordinary film."""
    if info.kind is None:
        return None
    from slate.story.gates import ENGINE  # noqa: PLC0415

    name = "script.derived"
    evidence = [f"parent: {info.parent_id} ({info.parent_script})"]
    if info.problems:
        return GateResult.fail(name, info.problems[0], evidence=evidence + info.problems, engine=dict(ENGINE),
                               details={"kind": info.kind})
    changed = [f"{info.parent_id}'s script changed since this {info.label} was made: check that it still matches"] \
        if info.changed else []
    if info.kind == "cut":
        summary = f"a cut of {info.parent_id}: {len(plan_ids)} of its {len(info.parent_ids)} shots"
        details = {"kind": "cut", "kept": plan_ids}
    else:
        strangers = [i for i in plan_ids if i not in info.parent_ids]
        missing = [i for i in info.parent_ids if i not in plan_ids]
        if strangers or missing:
            problems = ([f"not in {info.parent_id}'s script: {', '.join(strangers)} (a translation keeps every shot's "
                         "id, so reused beats and plates still mean the same shots)"] if strangers else []) + \
                       ([f"not translated yet: {', '.join(missing)}"] if missing else [])
            return GateResult.fail(name, f"the translation's shots don't match {info.parent_id}'s: {problems[0]}",
                                   evidence=evidence + problems, engine=dict(ENGINE),
                                   details={"kind": "translate", "strangers": strangers, "missing": missing})
        summary = f"a translation of {info.parent_id}: all {len(plan_ids)} shots keep their ids"
        details = {"kind": "translate"}
    if changed:
        return GateResult.review(name, changed[0], evidence=evidence + [summary], engine=dict(ENGINE), details=details)
    return GateResult.pass_(name, summary, evidence=evidence, engine=dict(ENGINE), details=details)
