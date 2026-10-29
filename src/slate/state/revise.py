"""Preserve the approved baseline, then make the requested revision and compare it by measurement.

    slate revise start <artifact> --notes "…" --by NAME     snapshot the approved version; notes → numbered checklist
    slate revise check                                      measure the revision against the approved version
    slate revise done                                       close it (refused while anything changed outside the list)

`start` requires the artifact to be approved and unchanged since, keeps a copy of the approved version in
archive/revisions/<id>/, writes the notes verbatim as a direction change and as a numbered checklist of changes to that
exact artifact in PRODUCTION.md, and records the hash of everything outside its declared scope. A requested redesign
is a checklist item, not an automatic question. Explicit questions are reserved for information that is missing.

`check` runs the built-in measures (did the artifact change; did anything else change that isn't downstream of it;
for JSON, which keys changed and whether they're inside the declared scope) and every registered comparison hook.
Other modules register hooks with `register_hook` in a module named `slate.<module>.revise_hooks` (sound: levels
against the voice; qa: sync and palette). A hook is `fn(film_dir, approved, current, revision) -> GateResult | dict`.
"""

from __future__ import annotations

import importlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from slate import gates
from slate.errors import Refused, UsageError
from slate.gates import GateResult
from slate.state import _util, approvals, film, lock, production

HOOK_MODULES = ("slate.sound.revise_hooks", "slate.qa.revise_hooks", "slate.voice.revise_hooks",
                "slate.render.revise_hooks")
KEEP = ("Everything else stays as it was: this revision edits {artifact} in place, and nothing else changes "
        "unless it's made from it.")


@dataclass
class Hook:
    name: str
    fn: Callable[..., Any]
    applies: Callable[[Path], bool] | None
    owner: str


HOOKS: dict[str, Hook] = {}


def register_hook(name: str, fn: Callable[..., Any], *, applies: Callable[[Path], bool] | None = None,
                  owner: str | None = None) -> None:
    """Add a comparison measure for `slate revise check` (e.g. 'level_vs_voice', 'sync', 'palette').

    `fn(film_dir, approved_path, current_path, revision)` returns a GateResult (or its dict) whose status says whether
    the revision stayed within its checklist on that measure; `applies(path)` limits it to artifacts it can measure.
    """
    if not name or not callable(fn):
        raise UsageError("A revise hook needs a name and a callable.")
    HOOKS[name] = Hook(name, fn, applies, owner or getattr(fn, "__module__", "unknown"))


def _load_hook_modules() -> None:
    for name in HOOK_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - one broken module's hooks mustn't stop the check
            from slate import log  # noqa: PLC0415

            log.warn(f"{name} failed to load its revise hooks: {exc}")


# ---------------------------------------------------------------------------------------------------------------
# Finding things


def revisions(film_dir: Path) -> list[dict]:
    return list(approvals.load(film_dir)["revisions"])


def open_revision(film_dir: Path, revision_id: str | None = None, artifact: str | None = None) -> dict | None:
    for r in reversed(revisions(film_dir)):
        if revision_id and r["id"] != revision_id:
            continue
        if artifact and r["artifact"] != artifact:
            continue
        if revision_id or r["status"] == "open":
            return r
    return None


def _require_open(film_dir: Path, revision_id: str | None) -> dict:
    rev = open_revision(film_dir, revision_id)
    if rev is None:
        raise UsageError("There's no open revision" + (f" called {revision_id}" if revision_id else "") + ".",
                         hint="slate revise start <artifact> --notes \"…\" --by NAME starts one.")
    if rev["status"] != "open":
        raise UsageError(f"{rev['id']} is already closed ({str(rev.get('closed_at', ''))[:16]}).")
    return rev


def _save_revision(film_dir: Path, rev: dict) -> None:
    def change(data: dict) -> None:
        for i, r in enumerate(data["revisions"]):
            if r["id"] == rev["id"]:
                data["revisions"][i] = rev
                return
        data["revisions"].append(rev)

    approvals.edit(film_dir, change)


# ---------------------------------------------------------------------------------------------------------------
# Start


def _baseline(film_dir: Path, artifact: str, hasher: _util.Hasher) -> dict[str, str | None]:
    """The hash of everything the revision must leave as it was: approved files, recorded files, film.json, script."""
    paths: set[str] = {"film.json", "script/script.json"}
    for rec in approvals.load(film_dir)["approvals"]:
        if rec.get("basis") != "asks":
            paths.add(rec["path"])
    for rel, e in lock.load(film_dir)["files"].items():
        if not e.get("ref"):
            paths.add(rel)
    out = {}
    for rel in sorted(paths):
        if rel == artifact or rel.startswith(artifact.rstrip("/") + "/"):
            continue
        digest, _kind = hasher.any(lock.resolve(film_dir, rel))
        out[rel] = digest
    return out


def start(film_dir: Path, artifact: str, *, notes: str, by: str, what: str | None = None,
          approved_by: str | None = None, scopes: list[str] | None = None,
          questions: list[str] | None = None) -> dict:
    """Open a revision of an approved artifact. Returns the revision record."""
    film_dir = Path(film_dir)
    if not (notes or "").strip():
        raise UsageError("A revision starts from the person's notes (--notes), quoted exactly.")
    if not (by or "").strip():
        raise UsageError("Say whose notes these are (--by NAME).")
    rel = _util.film_rel(film_dir, artifact)
    real = lock.resolve(film_dir, rel)
    if not real.exists():
        raise UsageError(f"{rel} doesn't exist.")
    if open_revision(film_dir, artifact=rel):
        raise Refused(f"{rel} already has an open revision; finish it first (slate revise done).")
    rec = None
    for cand in reversed(approvals.load(film_dir)["approvals"]):
        if (cand["path"] == rel and (what is None or cand["what"] == what or cand.get("kind") == what)
                and cand.get("kind", approvals.kind_of(cand["what"])) != "spend"):
            rec = cand
            break
    if rec is None:
        if not approved_by:
            raise Refused(f"An approved-work revision needs an approved baseline, and {rel} has no approval.",
                          hint="If the person approved it along with these notes, add --approved-by NAME --what KIND; "
                               "if it isn't approved, just edit it.")
        if not what:
            raise UsageError("Say what kind of approval it is (--what, e.g. sound_proof, script, hero).")
        rec = approvals.record(film_dir, what=what, path=rel, approved_by=approved_by,
                               note="approved with notes (slate revise start)")
    state = approvals.check(film_dir, rec["what"], rel)
    if not state["approved"]:
        raise Refused(f"{rel} changed since it was approved ({state['reason']}); a revision starts from the approved "
                      "version.", hint="Put the approved version back (git or the archive), then start again.")
    lang = film.language_of(film_dir)
    mode = film.mode_of(film_dir)
    archive = film_dir / "archive" / "revisions"
    used = [int(r["id"][2:]) for r in revisions(film_dir)]
    used += [int(p.name[2:]) for p in archive.glob("rv*") if p.name[2:].isdigit()]  # a crashed start's snapshot
    rid = f"rv{1 + max(used, default=0)}"
    snap_dir = archive / rid
    snap_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snap_dir / real.name
    if real.is_dir():
        shutil.copytree(real, snapshot, dirs_exist_ok=False)
    else:
        shutil.copy2(real, snapshot)
    with _util.Hasher(film_dir) as hasher:
        baseline = _baseline(film_dir, rel, hasher)
    items, qtexts = [], [q for q in (questions or []) if q.strip()]
    for text in _util.split_items(notes, lang):
        items.append({"n": len(items) + 1, "text": text, "status": "open", "scope": list(scopes or []),
                      "evidence": None})
    direction = production.add_direction(film_dir, notes.strip(), source=by.strip(), kind=f"revision {rid} of {rel}")
    rev = {
        "id": rid, "artifact": rel, "what": rec["what"], "approval": rec["id"], "approved_sha": rec["sha256"],
        "basis": rec.get("basis"), "snapshot": snapshot.relative_to(film_dir).as_posix(), "notes": notes.strip(),
        "by": by.strip(), "started_at": _util.now(), "direction": direction, "items": items, "questions": [],
        "baseline": baseline, "keep": KEEP.format(artifact=rel), "checks": [], "checked_at": None, "status": "open",
        "closed_at": None, "closing": None,
    }
    _save_revision(film_dir, rev)
    for text in qtexts:
        q = approvals.ask(film_dir, text, source=f"revision {rid}", evidence=[rel, rev["snapshot"]],
                          parks=(mode == "autonomous"), for_="revision", revision=rid)
        rev["questions"].append(q["id"])
    _save_revision(film_dir, rev)
    production.render(film_dir)
    from slate import history  # noqa: PLC0415

    history.record(film_dir, notes=notes.strip(), by=by.strip(), before=rev["snapshot"],
                   outcome="requested", revision=rid)
    return rev


# ---------------------------------------------------------------------------------------------------------------
# Check


def _downstream(film_dir: Path, artifact: str) -> set[str]:
    """Every recorded file made (directly or not) from the artifact: those are expected to change with it."""
    files = lock.load(film_dir)["files"]
    made_from: dict[str, set[str]] = {}
    for rel, e in files.items():
        for key in (e.get("inputs") or {}):
            made_from.setdefault(key, set()).add(rel)
    seen: set[str] = set()
    frontier = [k for k in made_from if k == artifact or k.startswith(artifact.rstrip("/") + "/")]
    while frontier:
        cur = frontier.pop()
        for nxt in made_from.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def _json_paths(a: Any, b: Any, prefix: str = "") -> list[str]:
    """JSON pointer-ish paths where two JSON values differ."""
    if type(a) is not type(b):
        return [prefix or "/"]
    if isinstance(a, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{prefix}/{key}")
            else:
                out += _json_paths(a[key], b[key], f"{prefix}/{key}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [prefix or "/"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _json_paths(x, y, f"{prefix}/{i}")
        return out
    return [] if a == b else [prefix or "/"]


def _in_scope(path: str, scopes: list[str]) -> bool:
    for s in scopes:
        s = "/" + s.strip("/").replace(".", "/") if not s.startswith("/") else s.rstrip("/")
        if path == s or path.startswith(s + "/"):
            return True
    return False


def _as_result(name: str, value: Any) -> GateResult:
    if isinstance(value, GateResult):
        value.gate = value.gate if value.gate.startswith("revise.") else f"revise.{value.gate}"
        return value
    if isinstance(value, dict) and value.get("status") in gates.STATUSES:
        data = {**value, "gate": f"revise.{value.get('gate') or name}"}
        return GateResult.from_dict({"summary": "", **data})
    return GateResult.unchecked(f"revise.{name}", f"hook {name} returned something that isn't a gate result")


def check(film_dir: Path, revision_id: str | None = None) -> dict:
    """Measure the open revision against the approved version. Returns {revision, results, status, outside}."""
    film_dir = Path(film_dir)
    rev = _require_open(film_dir, revision_id)
    rel = rev["artifact"]
    current = lock.resolve(film_dir, rel)
    snapshot = film_dir / rev["snapshot"] if rev.get("snapshot") else None
    results: list[GateResult] = []
    with _util.Hasher(film_dir) as hasher:
        now_sha, _ = (hasher.any(current) if rev.get("basis") != "asks" else
                      approvals.content_hash(film_dir, approvals.kind_of(rev["what"]), rel, hasher))
        if now_sha is None:
            results.append(GateResult.fail("revise.artifact", f"{rel} is gone"))
        elif now_sha == rev["approved_sha"]:
            results.append(GateResult.review("revise.artifact", f"{rel} hasn't changed yet: the checklist's changes "
                                                                "are still to make", evidence=[rel]))
        else:
            results.append(GateResult.pass_("revise.artifact", f"{rel} changed from the approved version "
                                                               f"({_util.short(rev['approved_sha'])} → {_util.short(now_sha)})",
                                            evidence=[rel, rev.get("snapshot") or ""]))
        downstream = _downstream(film_dir, rel)
        changed = []
        for path, digest in rev["baseline"].items():
            now, _k = hasher.any(lock.resolve(film_dir, path))
            if now != digest:
                changed.append(path)
    outside = [p for p in changed if p not in downstream]
    expected = [p for p in changed if p in downstream]
    if outside:
        results.append(GateResult.fail("revise.elsewhere", f"{len(outside)} file(s) outside the revision changed: "
                                                           + ", ".join(outside[:5]), evidence=outside[:20],
                                       details={"outside": outside, "downstream": expected}))
    else:
        results.append(GateResult.pass_("revise.elsewhere", "nothing outside the revision changed"
                                        + (f" (made from it, as expected: {', '.join(expected[:3])})" if expected else ""),
                                        details={"downstream": expected}))
    if rel.endswith(".json") and snapshot and snapshot.is_file() and current.is_file():
        try:
            before = json.loads(snapshot.read_text(encoding="utf-8"))
            after = json.loads(current.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append(GateResult.unchecked("revise.json_paths", f"couldn't compare the JSON: {exc}"))
        else:
            diff = _json_paths(before, after)
            scopes = sorted({s for i in rev["items"] for s in (i.get("scope") or [])})
            if not diff:
                results.append(GateResult.pass_("revise.json_paths", "no key changed"))
            elif scopes:
                out_of_scope = [d for d in diff if not _in_scope(d, scopes)]
                if out_of_scope:
                    results.append(GateResult.fail("revise.json_paths",
                                                   f"{len(out_of_scope)} change(s) outside the checklist's scope: "
                                                   + ", ".join(out_of_scope[:6]), evidence=out_of_scope[:20],
                                                   details={"changed": diff, "scope": scopes}))
                else:
                    results.append(GateResult.pass_("revise.json_paths", f"{len(diff)} change(s), all inside the "
                                                                         f"scope ({', '.join(scopes)})",
                                                    details={"changed": diff}))
            else:
                results.append(GateResult.review("revise.json_paths", f"{len(diff)} key(s) changed; check each is on "
                                                                      "the checklist: " + ", ".join(diff[:6]),
                                                 evidence=diff[:20], details={"changed": diff}))
    _load_hook_modules()
    for hook in HOOKS.values():
        if hook.applies is not None:
            try:
                if not hook.applies(current):
                    continue
            except Exception:  # noqa: BLE001
                continue
        try:
            value = hook.fn(film_dir, snapshot, current, rev)
        except Exception as exc:  # noqa: BLE001 - a broken measure is UNCHECKED, never a quiet pass
            results.append(GateResult.unchecked(f"revise.{hook.name}", f"{hook.owner} failed: {exc}"))
            continue
        results.append(_as_result(hook.name, value))
    rev["checks"] = [r.to_dict() for r in results]
    rev["checked_at"] = _util.now()
    _save_revision(film_dir, rev)
    production.render(film_dir)
    return {"revision": rev["id"], "artifact": rel, "results": [r.to_dict() for r in results],
            "status": gates.overall(results), "outside": outside, "measures": sorted(HOOKS)}


# ---------------------------------------------------------------------------------------------------------------
# Done


def done(film_dir: Path, revision_id: str | None = None, *, evidence: str | None = None, force: bool = False,
         reason: str | None = None, by: str | None = None) -> dict:
    """Close the revision. Refused while anything outside the checklist changed (unless forced with a reason) or when
    the artifact hasn't changed at all. The revised artifact then needs the person's approval again."""
    film_dir = Path(film_dir)
    rev = _require_open(film_dir, revision_id)
    result = check(film_dir, rev["id"])
    rev = _require_open(film_dir, rev["id"])
    failing = [r for r in result["results"] if r["status"] == "FAIL"]
    unchanged = any(r["gate"] == "revise.artifact" and r["status"] != "PASS" for r in result["results"])
    if (failing or unchanged) and not force:
        why = "; ".join(f"{r['gate']}: {r['summary']}" for r in failing) or f"{rev['artifact']} hasn't changed yet"
        hint = ("Undo what changed outside the checklist (`slate revise check` lists it), or, if a person decided it, "
                "close with --force --reason \"…\"." if failing else
                "Make the checklist's changes to that exact file first; if a person decided nothing should change, "
                "close with --force --reason \"…\".")
        raise Refused(f"{rev['id']} can't close yet: {why}.", hint=hint, details={"results": result["results"]})
    if force and not (reason or "").strip():
        raise UsageError("Closing past a failed check needs the reason (--reason).")
    for item in rev["items"]:
        item["status"] = "done"
        item["evidence"] = evidence or f"slate revise check {str(rev.get('checked_at', ''))[:16]}: {result['status']}"
    rev.update(status="done", closed_at=_util.now(),
               closing={"check": result["status"], "forced": reason if force else None, "by": by,
                        "evidence": evidence})
    _save_revision(film_dir, rev)
    production.render(film_dir)
    from slate import history  # noqa: PLC0415

    history.record(film_dir, notes=rev["notes"], by=rev["by"], before=rev["snapshot"],
                   after=rev["artifact"], outcome="measured" if result["status"] == "PASS" else "draft",
                   revision=rev["id"])
    return {**rev, "next": f"slate approve {rev['what']} {rev['artifact']} --by NAME"}
