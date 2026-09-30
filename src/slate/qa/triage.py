"""Triage: what isn't passing yet, most severe first, each with the one next step.

`slate qa triage` reads the film's latest gate results (a result whose inputs changed since is STALE) and sorts
everything that isn't PASS: what failed, what went stale, what couldn't be checked and why, and what's waiting for a
person. Gates that have never run count as unchecked: nothing unchecked is ever reported as passed.

It also triages the glitch scan. Flags within ±3 frames of a planned beat are the film's own moves (film B had 1,442
flags, mostly smooth fast moves); they're listed as skipped, grouped by act, so the open ones stand out.
"""

from __future__ import annotations

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
    if rep["passed"]:
        lines += ["", "Passed: " + ", ".join(rep["passed"])]
    return "\n".join(lines)
