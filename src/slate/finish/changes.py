"""What changed since the last version: the film's content, film.lock.json, approvals and gates, compared between two
review packets.

Each packet keeps a snapshot of the film when it was made. The next packet compares its own snapshot with that one
and lists, in plain words:

- the picture and sound files that changed (act renders, the mix and its stems, captions, the shots);
- every derived file film.lock.json says was remade, added or removed, and anything newly locked;
- approvals given since, and approvals that no longer stand because what was approved changed;
- gates whose verdict moved (FAIL → PASS and so on), and gates now STALE, which are exactly the ones to re-run on what
  changed;
- the directions (the owner's notes) logged since.
"""

from __future__ import annotations

from slate import gates, jsonio
from slate.finish import deps
from slate.finish.film import Film

VOLATILE = ("made_at", "checked_at", "at")


def _lock_digest(entry: dict) -> str:
    return jsonio.sha256_obj({k: v for k, v in (entry or {}).items() if k not in VOLATILE})


def _area(rel: str) -> str:
    """A plain name for where a file belongs, for the change list."""
    parts = rel.split("/")
    head = parts[0]
    if head == "renders" and len(parts) > 1:
        stem = parts[-1].split(".")[0]
        return f"picture ({stem})" if stem.startswith("act") else "picture"
    if head == "voice":
        return f"voice ({parts[2]})" if len(parts) > 2 and parts[1] == "takes" else "voice"
    if head == "sound":
        return "sound"
    if head == "timing":
        return "timing"
    if head in ("scenes", "captures", "plates", "script", "masters", "ae", "qa", "brief"):
        return {"scenes": "scenes", "captures": "captures", "plates": "generated shots", "script": "script",
                "masters": "masters", "ae": "After Effects project", "qa": "checks", "brief": "brief"}[head]
    return head


def snapshot(film: Film) -> dict:
    """The film's state as a packet records it."""
    lock_files = deps.lock_entries(film.dir)
    appr = []
    for rec in deps.approval_records(film.dir):
        st = deps.approval_state(film.dir, rec)
        appr.append({"id": rec.get("id"), "what": rec.get("what"), "path": rec.get("path"), "sha256": rec.get("sha256"),
                     "approved_by": rec.get("approved_by"), "at": rec.get("at"), "stands": bool(st.get("approved"))})
    report = gates.load_report(film.dir)
    return {
        "content": film.content(),
        "lock": {rel: _lock_digest(e) for rel, e in sorted(lock_files.items())},
        "locked": sorted(rel for rel, e in lock_files.items() if (e or {}).get("locked")),
        "approvals": appr,
        "gates": {name: r.status for name, r in sorted(report.items())},
        "directions": [int(d.get("n")) for d in deps.directions(film.dir) if isinstance(d.get("n"), int)],
    }


def _files_diff(before: dict, after: dict) -> dict:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


def diff(prev: dict | None, cur: dict, *, film: Film | None = None, since: int | None = None) -> dict:
    """What changed from `prev` (a packet's snapshot) to `cur`. With no previous packet, everything is new."""
    first = prev is None
    prev = prev or {"content": {}, "lock": {}, "locked": [], "approvals": [], "gates": {}, "directions": []}
    content = _files_diff(prev.get("content") or {}, cur["content"])
    lock = _files_diff(prev.get("lock") or {}, cur["lock"])
    newly_locked = sorted(set(cur["locked"]) - set(prev.get("locked") or []))
    old_ids = {a["id"] for a in prev.get("approvals") or []}
    approvals_new = [a for a in cur["approvals"] if a["id"] not in old_ids]
    approvals_lapsed = [a for a in cur["approvals"] if not a["stands"]
                        and any(p["id"] == a["id"] and p.get("stands") for p in prev.get("approvals") or [])]
    moved = []
    for name, status in cur["gates"].items():
        before = (prev.get("gates") or {}).get(name)
        if before != status:
            moved.append({"gate": name, "from": before, "to": status})
    stale = sorted(n for n, s in cur["gates"].items() if s == "STALE")
    new_dirs = [n for n in cur["directions"] if n not in (prev.get("directions") or [])]
    directions = []
    if film is not None and new_dirs:
        by_n = {int(d.get("n")): d for d in deps.directions(film.dir) if isinstance(d.get("n"), int)}
        directions = [{"n": n, "text": by_n[n].get("text"), "source": by_n[n].get("source")} for n in new_dirs
                      if n in by_n]
    areas = sorted({_area(r) for r in content["changed"] + content["added"] + content["removed"]
                    + lock["changed"] + lock["added"]})
    out = {"since": since, "first": first, "content": content, "lock": lock, "newly_locked": newly_locked,
           "approvals_new": approvals_new, "approvals_lapsed": approvals_lapsed, "gates_moved": moved,
           "gates_to_rerun": stale, "directions": directions, "areas": areas}
    out["summary"] = lines(out)
    return out


def lines(ch: dict) -> list[str]:
    """The change list in plain words, most useful first."""
    if ch.get("first"):
        out = ["The first packet for this film: everything in it is new."]
    else:
        out = []
    c = ch["content"]
    touched = c["changed"] + c["added"]
    if not ch.get("first"):
        if touched or c["removed"]:
            out.append("Changed in the picture and sound: " + ", ".join(touched + [f"{r} (removed)"
                                                                                 for r in c["removed"]]) + ".")
        else:
            out.append("The picture and sound are the same as last time.")
    remade = ch["lock"]["changed"] + ch["lock"]["added"]
    if remade and not ch.get("first"):
        shown = ", ".join(remade[:12]) + (f" and {len(remade) - 12} more" if len(remade) > 12 else "")
        out.append(f"Remade since (film.lock.json): {shown}.")
    if ch["newly_locked"]:
        out.append("Newly locked (approved work): " + ", ".join(ch["newly_locked"]) + ".")
    for a in ch["approvals_new"]:
        out.append(f"Approved: {a['what']} {a['path']} by {a['approved_by']}.")
    for a in ch["approvals_lapsed"]:
        out.append(f"No longer approved (it changed since): {a['what']} {a['path']}.")
    for g in ch["gates_moved"]:
        if g["from"] is None:
            continue
        out.append(f"Gate {g['gate']}: {g['from']} → {g['to']}.")
    if ch["gates_to_rerun"]:
        out.append("Re-run on what changed: " + " · ".join(rerun_commands(ch["gates_to_rerun"])))
    for d in ch["directions"]:
        out.append(f"Direction {d['n']} ({d.get('source') or 'owner'}): “{d.get('text')}”")
    return out


def rerun_commands(names: list[str]) -> list[str]:
    """The commands that re-run these gates: film gates through slate qa, the rest through their own stage."""
    qa, own = [], []
    for n in names:
        if n.startswith("master."):
            own.append("slate master")
        elif n.startswith("sound."):
            own.append("slate verify")
        elif n.startswith("ae."):
            own.append("slate ae audit --go (you start it)")
        elif n.startswith("voice_"):
            own.append("slate voice check")
        else:
            qa.append(n)
    cmds = (["slate qa " + " ".join(qa)] if qa else []) + own
    return [c for i, c in enumerate(cmds) if c not in cmds[:i]]
