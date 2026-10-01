"""Triage: what isn't passing yet, most severe first, each with the one next step.

`slate qa triage` reads the film's latest gate results (a result whose inputs changed since is STALE) and sorts
everything that isn't PASS: what failed, what went stale, what couldn't be checked and why, and what's waiting for a
person. Gates that have never run count as unchecked: nothing unchecked is ever reported as passed.

It also triages the glitch scan. Flags within ±3 frames of a planned beat are the film's own moves (film B had 1,442
flags, mostly smooth fast moves); they're listed as skipped, grouped by act, so the open ones stand out. The render
stability scan gets the same per act: open flags by class, how many the planned beats, the reference render and the
baseline took out, and the strips to look at.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from slate import gates, jsonio
from slate.gates import GateResult

ORDER = ("FAIL", "STALE", "UNCHECKED", "REVIEW")


def _next(r: GateResult) -> str:
    hinted = [e[len("next: "):] for e in r.evidence or [] if isinstance(e, str) and e.startswith("next: ")]
    if r.status == "STALE":
        return f"slate qa {r.gate}   (its inputs changed since it ran)"
    if r.status == "REVIEW":
        return (f"a person looks at the evidence, then fixes it or accepts each item: slate qa triage --accept "
                f"{r.gate}:<item> --by NAME --reason TEXT")
    if hinted:
        return hinted[0]
    if r.status == "FAIL":
        return f"fix what the evidence names, then slate qa {r.gate}"
    return "slate doctor   (then slate qa " + r.gate + ")"


def _entry(r: GateResult) -> dict:
    out = {"gate": r.gate, "status": r.status, "summary": r.summary,
           "evidence": [e for e in r.evidence or [] if not str(e).startswith("next: ")][:8], "next": _next(r),
           "checked_at": r.checked_at, "engine": r.engine}
    items = (r.details or {}).get("review_items") or []
    if r.status == "REVIEW" and items:
        out["review_items"] = [{"ref": f"{r.gate}:{i.get('key')}", "what": i.get("what")} for i in items[:50]]
    return out


def _glitch_summary(film_dir: Path) -> dict | None:
    data = jsonio.read(film_dir / "qa" / "glitch.json", default=None)
    if not isinstance(data, dict):
        return None
    skipped = data.get("skipped_near_beats") or []
    by_act = Counter(str(f.get("act")) for f in skipped)
    return {"flags": data.get("flags", 0), "skipped_near_beats": len(skipped), "skipped_by_act": dict(by_act),
            "open": [{"ref": f"glitch:{i.get('key')}", "what": i.get("what")} for i in data.get("open") or []][:50],
            "open_count": len(data.get("open") or []), "accepted": len(data.get("accepted") or []),
            "missing_acts": data.get("missing_acts") or []}


def _stability_summary(film_dir: Path) -> dict | None:
    """Each act's render-stability triage from its last scan (qa/stability/act<N>.json), shown the way the glitch
    scan's is: what's open by class, what the planned beats, the reference render and the baseline took out, and the
    strips to look at."""
    folder = film_dir / "qa" / "stability"
    reports = [p for p in folder.glob("act*.json") if re.fullmatch(r"act\d+\.json", p.name)] if folder.is_dir() else []
    acts = []
    for path in sorted(reports, key=lambda p: int(p.stem[3:])):
        data = jsonio.read(path, default=None)
        if not isinstance(data, dict):
            continue
        act = data.get("act", int(path.stem[3:]))
        # the strips this scan made: one per open class and frame (older renders' strips may still be on disk)
        named = {f"act{act}_{f.get('class')}_{int(f['frame'])}.png" for f in data.get("open") or []
                 if isinstance(f, dict) and f.get("frame") is not None}
        strips = sorted(n for n in named if (folder / "strips" / n).is_file())
        acts.append({"act": act, "render": data.get("render"),
                     "open_by_class": {k: v for k, v in sorted((data.get("counts") or {}).items()) if v},
                     "over_limits": list(data.get("over") or []),
                     "near_beats": len(data.get("near_beats") or []),
                     "shared_with_reference": len(data.get("shared_with_reference") or []),
                     "accepted_by_baseline": len(data.get("accepted_by_baseline") or []),
                     "reference": (data.get("reference") or {}).get("file"), "baseline": data.get("baseline"),
                     "strips": [f"qa/stability/strips/{n}" for n in strips][:20]})
    return {"acts": acts} if acts else None


def report(film_dir: Path, gate_names: list[str] | None = None) -> dict:
    """Everything that isn't passing, grouped by status, with the next step for each."""
    from slate.qa import GATES, park  # noqa: PLC0415

    film_dir = Path(film_dir)
    latest = gates.load_report(film_dir)
    names = gate_names if gate_names is not None else list(GATES)
    groups: dict[str, list[dict]] = {s: [] for s in ORDER}
    passed = []
    for name, r in sorted(latest.items(), key=lambda kv: kv[0]):
        if r.status == "PASS":
            passed.append(name)
        else:
            groups[r.status].append(_entry(r))
    not_run = [n for n in names if n not in latest]
    statuses = [r.status for r in latest.values()] + ["UNCHECKED"] * len(not_run)
    film = jsonio.read(film_dir / "film.json", default={}) or {}
    return {
        "film": film.get("id") or film_dir.name,
        "mode": film.get("mode") or "supervised",
        "status": gates.overall([GateResult("x", s, "x") for s in statuses]),
        "groups": groups,
        "passed": passed,
        "not_run": not_run,
        "parked": park.status(film_dir),
        "glitch": _glitch_summary(film_dir),
        "stability": _stability_summary(film_dir),
    }


def format_report(rep: dict) -> str:
    lines = [f"{rep['film']} ({rep['mode']}): {rep['status']}"]
    if rep.get("parked"):
        p = rep["parked"]
        lines += ["", f"PARKED since {p.get('since')}: {p.get('why')}", f"  release: {p.get('release')}"]
    titles = {"FAIL": "Failed", "STALE": "Stale (inputs changed since the check)", "UNCHECKED": "Not checked",
              "REVIEW": "Waiting for a person"}
    for status in ORDER:
        items = rep["groups"][status]
        if not items:
            continue
        lines += ["", f"{titles[status]}:"]
        for e in items:
            lines.append(f"  {e['gate']}: {e['summary']}")
            for ev in e["evidence"][:3]:
                lines.append(f"      {ev}")
            for it in (e.get("review_items") or [])[:10]:
                lines.append(f"      [{it['ref']}] {it['what']}")
            lines.append(f"      next: {e['next']}")
    if rep["not_run"]:
        lines += ["", "Never run: " + ", ".join(rep["not_run"]), "      next: slate qa " + " ".join(rep["not_run"])]
    g = rep.get("glitch")
    if g:
        acts = ", ".join(f"act {a}: {n}" for a, n in sorted(g["skipped_by_act"].items())) or "none"
        lines += ["", f"Glitch triage: {g['flags']} flags; {g['skipped_near_beats']} within ±3 frames of a planned beat "
                      f"(skipped; {acts}); {g['accepted']} accepted; {g['open_count']} open"]
        for it in g["open"][:15]:
            lines.append(f"  [{it['ref']}] {it['what']}")
    s = rep.get("stability")
    if s:
        lines += ["", "Stability triage (each act's last scan):"]
        for a in s["acts"]:
            opened = ", ".join(f"{n} {cls}" for cls, n in a["open_by_class"].items()) or "nothing open"
            lines.append(f"  act {a['act']}: {opened}; {a['near_beats']} near a planned beat, "
                         f"{a['shared_with_reference']} shared with the reference, {a['accepted_by_baseline']} in the "
                         f"baseline" + (f"; over the limits: {', '.join(a['over_limits'])}" if a["over_limits"] else ""))
            for strip in a["strips"][:5]:
                lines.append(f"      strip: {strip}")
    if rep["passed"]:
        lines += ["", "Passed: " + ", ".join(rep["passed"])]
    return "\n".join(lines)
