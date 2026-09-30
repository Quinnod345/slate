"""The After Effects project audit (`slate ae audit <aep>`): validate.jsx's checks as gate results.

engine/ae/validate.jsx opens the film's project inside AE (preserving any unsaved work in whatever was open) and
checks it against the build spec: the project's bit depth, colour working space and blending; one comp per shot at
the film's size, rate and frame count, each rendered slice on its film frame a tenth of a frame early; the act comps;
missing footage; the code renders' format; the master's frames, audio and markers; the masters queue; missing or
substituted fonts; expression errors; motion blur on animated layers. Each check becomes a gate named `ae.<check>`:
PASS, FAIL, or UNCHECKED when it couldn't run (never a quiet pass).

Running the checks needs After Effects, so it needs the person's go. Without --go the command reports the last audit
of that project, turned STALE if the project (or its build spec) changed since; with `--report FILE` it reads a saved
validate report instead, which is how the checks are tested offline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from slate import gates, jsonio
from slate.ae import driver, guard
from slate.ae import spec as ae_spec
from slate.errors import MissingPrerequisite, UsageError
from slate.gates import GateResult

CHECKS = ("project.bpc", "project.workingSpace", "project.linearBlending", "shots.comps", "shots.picture", "acts.comps",
          "footage.missing", "renders.format", "master.frames", "master.audio", "master.markers", "queue.chunks", "fonts",
          "expressions", "motionblur.native")
ENGINE = "After Effects validate.jsx"
AUDIT_FILE = Path("ae") / "audit.json"
Runner = Callable[..., Any]


def _inputs(film_dir: Path | None, aep: Path | None) -> dict:
    if film_dir is None:
        return {str(aep): jsonio.sha256(aep)} if aep and aep.is_file() else {}
    files = [p for p in (aep, film_dir / ae_spec.SPEC) if p is not None]
    return gates.inputs_for(film_dir, files)


def gates_from_report(checks: list[dict], *, aep: Path | None = None, ae_version: str | None = None,
                      film_dir: Path | None = None) -> list[GateResult]:
    """validate.jsx's checks -> one gate per check; any expected check missing from the report is UNCHECKED."""
    inputs = _inputs(film_dir, aep)
    engine = gates.engine(ENGINE, ae_version or "unknown")
    seen: dict[str, dict] = {}
    for c in checks or []:
        if isinstance(c, dict) and c.get("check"):
            seen[str(c["check"])] = c
    out = []
    for name in list(CHECKS) + [n for n in seen if n not in CHECKS]:
        c = seen.get(name)
        if c is None:
            out.append(GateResult.unchecked(f"ae.{name}", "the audit didn't report this check", engine=engine,
                                            inputs=inputs, evidence=[str(aep)] if aep else []))
            continue
        status = str(c.get("status", "UNCHECKED")).upper()
        if status not in ("PASS", "FAIL", "UNCHECKED", "REVIEW"):
            status = "UNCHECKED"
        evidence = [str(c.get("evidence") or "")] + ([str(aep)] if aep else [])
        out.append(GateResult(f"ae.{name}", status, str(c.get("evidence") or name), evidence=evidence, engine=engine,
                              inputs=inputs, details={"check": name}))
    return out


def _read_report_file(path: Path) -> tuple[list[dict], str | None, str | None]:
    doc = jsonio.read(path)
    data = doc.get("data", doc) if isinstance(doc, dict) else {}
    checks = data.get("checks") if isinstance(data, dict) else doc if isinstance(doc, list) else None
    if not isinstance(checks, list):
        raise UsageError(f"{path} isn't a validate report (no checks list).")
    return checks, data.get("ae_version"), data.get("aep")


def run(aep: str | os.PathLike | None = None, *, film_dir: str | os.PathLike | None = None, go: bool = False,
        runner: Runner | None = None, report: str | os.PathLike | None = None, write: bool = True) -> list[GateResult]:
    """Audit a film's AE project; returns gate results (and records them in the film's qa/gates.json)."""
    fdir = Path(film_dir).resolve() if film_dir else None
    aep_path = Path(aep).resolve() if aep else None
    if aep_path is None and fdir is not None:
        aep_path = Path(ae_spec.read_spec(fdir)["paths"]["aep"])
    if report is not None:
        checks, version, _aep = _read_report_file(Path(report))
        results = gates_from_report(checks, aep=aep_path, ae_version=version, film_dir=fdir)
    elif go:
        if fdir is None:
            raise UsageError("Auditing needs the film (run it inside the film folder, or pass --film).")
        guard.person_start(go, "Auditing the AE project", film_dir=fdir, action="ae")
        if aep_path is None or not aep_path.is_file():
            raise MissingPrerequisite(f"There's no AE project at {aep_path}.", hint="slate ae build --go")
        spec_path = fdir / ae_spec.SPEC
        if not spec_path.is_file():
            raise MissingPrerequisite("This film has no AE build spec to audit against.", hint="slate ae build --go")
        res = driver.expect_ok(driver.run_jsx(driver.script("validate.jsx"),
                                              args={"spec": str(spec_path), "aep": str(aep_path)}, runner=runner),
                               "Auditing the AE project")
        results = gates_from_report(res["report"].get("checks") or [], aep=aep_path,
                                    ae_version=res["report"].get("ae_version"), film_dir=fdir)
        if fdir is not None:
            jsonio.write_atomic(fdir / AUDIT_FILE, {"generated": "do not edit by hand: slate ae audit",
                                                     "audited_at": gates.now_iso(), "aep": str(aep_path),
                                                     "data": res["report"],
                                                     "gates": [r.to_dict() for r in results]})
    else:
        results = last(fdir, aep_path)
    if write and fdir is not None:
        gates.write_report(fdir, results)
    return results


def last(film_dir: Path | None, aep: Path | None) -> list[GateResult]:
    """The last audit's results, STALE where the project or spec changed since; UNCHECKED when there's none."""
    saved = jsonio.read(film_dir / AUDIT_FILE, default=None) if film_dir else None
    if not saved:
        return [GateResult.unchecked("ae.audit", "the AE project hasn't been audited yet",
                                     evidence=[str(aep)] if aep else [],
                                     details={"next": "slate ae audit --go (a person starts After Effects)"})]
    out = []
    for raw in saved.get("gates") or []:
        r = GateResult.from_dict(raw)
        for rel, digest in (r.inputs or {}).items():
            full = Path(rel) if Path(rel).is_absolute() else film_dir / rel
            now = jsonio.sha256(full) if full.is_file() else None
            if now != digest and r.status != "STALE":
                r.details = {**(r.details or {}), "was": r.status}
                r.status = "STALE"
                r.summary = f"the project changed since this was checked ({r.summary})"
                break
        out.append(r)
    return out
