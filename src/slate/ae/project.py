"""Build a film's editable After Effects project from its data (`slate ae build --go`).

The film pipeline rebuilt its AEP from saved data with build_film.jsx; this is that build for any film. Python writes
the build spec (slate.ae.spec: acts, shots, renders, markers, the brand's tokens, fonts, eases and logos, the masters
plan, all resolved on the film clock), then engine/ae/build_film.jsx runs inside After Effects through the driver and
reports back. The result is `<film>/ae/<id>.aep`: an editable project with a comp per shot and per act, the master
with the mix and every marker, a brand board, any act built in AE as native layers and keyframes, and the masters
already queued in chunks for `slate ae masters`.

Before anything replaces the open project, unsaved work in it is saved to Slate's preserve folder
(`DATA/ae/preserved/`); nothing is discarded. The build needs the person's go and never runs for autonomous films.
AE-built acts also get their motion trace and visual events written here (slate.ae.acts.export), so they face the
same gates as a Remotion act without waiting for a render.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from slate import jsonio, log
from slate.ae import acts as ae_acts
from slate.ae import driver, guard
from slate.ae import spec as ae_spec
from slate.errors import SlateError

BUILD_TIMEOUT_S = 1800
Runner = Callable[..., Any]


def build(film_dir: str | os.PathLike, *, go: bool = False, runner: Runner | None = None, chunks: int | None = None,
          queue: bool = True, timeout: int = BUILD_TIMEOUT_S) -> Path:
    """Build (or rebuild) the film's AE project and return its path. See the module docstring."""
    film_dir = Path(film_dir).resolve()
    guard.person_start(go, "Building the film's After Effects project", film_dir=film_dir, action="ae_build")
    spec = ae_spec.build_spec(film_dir, chunks=chunks, queue=queue)
    spec_path = ae_spec.write_spec(film_dir, spec)
    for w in spec.get("warnings") or []:
        log.warn(w)
    exported = [ae_acts.export(film_dir, a["index"], spec=spec) for a in spec["acts"] if a.get("engine") == "ae"]
    res = driver.run_jsx(driver.script("build_film.jsx"), args={"spec": str(spec_path)}, runner=runner,
                         timeout=timeout)
    driver.expect_ok(res, "Building the AE project")
    aep = Path(spec["paths"]["aep"])
    if not aep.is_file():
        raise SlateError(f"After Effects reported the build done, but there's no project at {aep}.")
    report = res["report"]
    record = {"built_at": _now(), "aep": str(aep), "aep_sha256": jsonio.sha256(aep), "spec": str(spec_path),
              "spec_sha256": jsonio.sha256(spec_path), "run_id": res["run_id"], "report_channel": res["report_channel"],
              "seconds": res["seconds"], "report": report, "exported": exported,
              "warnings": (spec.get("warnings") or []) + list(report.get("warnings") or [])}
    jsonio.write_atomic(film_dir / "ae" / "build_report.json", jsonio.mark_generated(record, ae_spec.COMMAND))
    if queue and spec.get("masters"):
        jsonio.write_atomic(film_dir / "ae" / "masters_queue.json",
                            {"queued_at": record["built_at"], "chunks": spec["masters"]["chunks"],
                             "queue": report.get("queue"), "aep": str(aep), "aep_sha256": record["aep_sha256"]})
    _lock_record(film_dir, aep, spec_path)
    return aep


def last_report(film_dir: str | os.PathLike) -> dict | None:
    return jsonio.read(Path(film_dir) / "ae" / "build_report.json", default=None)


def _lock_record(film_dir: Path, aep: Path, spec_path: Path) -> None:
    """Note what made the AEP in film.lock.json (through slate.state.lock when it's installed)."""
    try:
        from slate.state import lock  # noqa: PLC0415
    except ImportError:
        return
    try:
        rel = str(aep.relative_to(film_dir))
        inputs = [str(spec_path.relative_to(film_dir))]
        lock.record(film_dir, rel, producer=ae_spec.COMMAND, inputs=inputs)
    except Exception as exc:  # noqa: BLE001 - the build stands; the lock record is bookkeeping
        log.warn(f"couldn't note the AE project in film.lock.json: {getattr(exc, 'message', exc)}")


def _now() -> str:
    import datetime as _dt  # noqa: PLC0415

    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
