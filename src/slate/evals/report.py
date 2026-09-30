"""Eval results: the case × configuration matrix, saved with each run and shown by `slate evals report`.

A pair is PASS only when every check it's graded on passed. FAIL beats UNCHECKED, UNCHECKED beats PASS, and a pending
case (its precondition isn't met yet) is listed apart and left out of the overall status. Synthetic fixtures prove the
graders; a synthetic run's status says whether every verdict matched the fixture's expected one, and it never counts
toward the eval matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

from slate import jsonio, paths
from slate.errors import UsageError

_SEVERITY = {"FAIL": 3, "UNCHECKED": 2, "PENDING": 1, "PASS": 0}


def summarize(results: list[dict], *, run_id: str, mode: str, fixtures: str | None, started: str,
              finished: str) -> dict:
    counts: dict[str, int] = {s: 0 for s in ("PASS", "FAIL", "UNCHECKED", "PENDING")}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    matrix: dict[str, dict[str, str]] = {}
    for r in results:
        key = r["case"] if not r.get("fixture") else r["fixture"]
        matrix.setdefault(key, {})[r["config"]] = r["status"]
    if fixtures == "synthetic":
        disagree = [r for r in results if not r.get("agrees")]
        overall = "UNCHECKED" if not results else "FAIL" if disagree else "PASS"
        headline = (f"{len(results) - len(disagree)} of {len(results)} synthetic transcripts graded as expected"
                    if results else "no synthetic fixtures matched the selection")
    else:
        counted = [r for r in results if r["status"] != "PENDING" and not r.get("synthetic")]
        overall = max((r["status"] for r in counted), key=lambda s: _SEVERITY[s]) if counted else "UNCHECKED"
        headline = ", ".join(f"{counts[s]} {s}" for s in ("PASS", "FAIL", "UNCHECKED", "PENDING") if counts[s]) \
            or "nothing selected"
    pending = sorted({r["case"] for r in results if r["status"] == "PENDING"})
    return {"generated": "do not edit by hand: slate evals run", "run_id": run_id, "mode": mode,
            "fixtures": fixtures, "started_at": started, "finished_at": finished, "overall": overall,
            "headline": headline, "counts": counts, "pending": pending, "matrix": matrix, "results": results}


def save(run_dir: Path, summary: dict) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.json"
    jsonio.write_atomic(path, {**summary, "run_dir": str(run_dir)})
    return path


def load(ref: str | None = None, root: Path | None = None) -> dict:
    """A run's report: by id, by path, or the newest one."""
    base = Path(root) if root else paths.data() / "evals" / "runs"
    if ref:
        candidate = Path(ref).expanduser()
        for path in (candidate / "report.json", candidate, base / ref / "report.json"):
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise UsageError(f"No eval run called {ref!r} (looked in {base}).", hint="`slate evals report` shows the latest.")
    reports = sorted(base.glob("*/report.json"), key=lambda p: p.stat().st_mtime) if base.exists() else []
    if not reports:
        raise UsageError(f"No eval runs yet in {base}.",
                         hint="`slate evals run` grades the recorded transcripts; `slate evals run --dry-run` shows "
                              "the commands.")
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def format_text(summary: dict, *, details: bool = True) -> str:
    """The matrix, then what didn't pass and why."""
    lines = [f"Slate evals · {summary['mode']}" + (f" ({summary['fixtures']})" if summary.get("fixtures") else "")
             + f" · run {summary['run_id']}"]
    configs: list[str] = []
    for row in summary["matrix"].values():
        for cid in row:
            if cid not in configs:
                configs.append(cid)
    width = max([len(k) for k in summary["matrix"]] + [12])
    col = max([len(c) for c in configs] + [9]) + 2
    lines.append(" " * (width + 2) + "".join(c.ljust(col) for c in configs))
    for key, row in summary["matrix"].items():
        lines.append(key.ljust(width + 2) + "".join(row.get(c, "·").ljust(col) for c in configs))
    lines.append(f"overall: {summary['overall']} ({summary['headline']})")
    if summary.get("pending"):
        lines.append(f"pending: {', '.join(summary['pending'])} (left out of the overall status until their "
                     "preconditions hold)")
    if not details:
        return "\n".join(lines)
    results = summary["results"]
    if summary.get("fixtures") == "synthetic":
        off = [r for r in results if not r.get("agrees")]
        for r in off:
            got = sorted(c["gate"] for c in r["checks"] if c["status"] == "FAIL")
            lines.append(f"\n{r['fixture']}: expected {r['expected']['status']} with {r['expected']['failed']} "
                         f"failing, got {r['status']} with {got} failing")
            lines += [f"  {c['status']:<9} {c['gate']}: {c['summary']}" for c in r["checks"] if c["status"] != "PASS"]
        return "\n".join(lines)
    missing = [r for r in results if r["status"] == "UNCHECKED" and r.get("ended") == "not run"]
    rest = [r for r in results if r["status"] in ("FAIL", "UNCHECKED") and r not in missing]
    if missing:
        lines.append(f"\n{len(missing)} pair(s) not run: {missing[0]['summary']}")
    for r in sorted(rest, key=lambda r: (-_SEVERITY[r["status"]], r["case"], r["config"])):
        lines.append(f"\n{r['status']}  {r['case']} · {r['config']}  ({r['source']})")
        for c in r["checks"]:
            if c["status"] == "PASS":
                continue
            lines.append(f"  {c['status']:<9} {c['gate']}: {c['summary']}")
            lines += [f"            {e}" for e in (c.get("evidence") or [])[:4]]
        for name, why in (r.get("not_applicable") or {}).items():
            lines.append(f"  n/a       {name}: {why}")
    return "\n".join(lines)
