"""The one-second After Effects smoke test: the toolchain proved end to end, in pixels.

`slate ae smoke --go` (and the last step of `slate connect aftereffects`) builds one tiny comp with Slate's ES3
library (engine/ae/smoke_build.jsx: a dot crossing the frame on a cubic-bezier ease written as AE speed and
influence, with motion blur), renders one second of it with aerender, pulls the frames out with ffmpeg and measures
where the dot is in each frame against where the ease says it should be. That proves the whole lane at once: osascript
reaches AE, AE runs Slate's ExtendScript and reports back, the ease conversion lands in AE's own curve, aerender
renders, and the render matches the maths. The film pipeline's smoke test measured a worst error of 4.2 px on a
1200 px move at the fastest, most blurred frame, and under 1.5 px from frame 6.

The ease is Material 3's "emphasized decelerate" (a public token), so the test belongs to no brand. The size and
rate are the test's own (960 × 540 at 30 fps by default); a film's come from its film.json.

Without --go nothing starts: `run(out_dir)` returns the last recorded result (so `slate doctor` can report it from
any agent without launching After Effects), or UNCHECKED with the command for a person to run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from slate import gates, jsonio, paths, procs
from slate.ae import driver, eases, guard
from slate.ae import masters as ae_masters
from slate.errors import SlateError
from slate.gates import GateResult

GATE = "ae.smoke"
SMOKE_EASE = [0.05, 0.7, 0.1, 1.0]  # Material 3 "emphasized decelerate": a public token, not any brand's
COMP = "SLATE_SMOKE"
BG, DOT = "#141414", "#FFD633"
DEFAULT = {"width": 960, "height": 540, "fps_num": 30, "fps_den": 1, "seconds": 1.0}
OM_TEMPLATE = "High Quality"
TOLERANCE = 0.01  # the worst frame may miss by 1% of the move's travel (the film's worst was 0.35%)
Runner = Callable[..., Any]


def params(*, width: int, height: int, fps_num: int, fps_den: int, seconds: float) -> dict:
    """The smoke comp's geometry: the dot travels from 1/8 to 7/8 of the width over the first 80% of the second."""
    fps = fps_num / fps_den
    n = max(2, int(round(seconds * fps)))
    return {"comp_name": COMP, "width": int(width), "height": int(height), "fps_num": int(fps_num),
            "fps_den": int(fps_den), "frames": n, "start_frame": 0, "end_frame": max(1, int(round(0.8 * n))),
            "from_x": width / 8, "to_x": width * 7 / 8, "y": height / 2, "diameter": max(8, round(height / 7.5)),
            "ease": list(SMOKE_EASE), "bg": BG, "dot": DOT}


def expected_x(p: dict, frame: int) -> float:
    u = (frame - p["start_frame"]) / (p["end_frame"] - p["start_frame"])
    return p["from_x"] + (p["to_x"] - p["from_x"]) * eases.value(p["ease"], u)


def centroid_x(png: str | os.PathLike, ground: float) -> float | None:
    """The dot's centre along x: brightness above the ground, weighted (a motion-blurred dot still averages right)."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    a = np.asarray(Image.open(png).convert("RGB"), dtype=float)
    w = np.clip(a.mean(axis=2) - ground - 12.0, 0, None)
    total = float(w.sum())
    if total <= 0:
        return None
    return float((w.sum(axis=0) * np.arange(a.shape[1])).sum() / total)


def measure(frames_dir: Path, p: dict) -> dict:
    """Where the dot is in each extracted frame, against the ease."""
    ground = sum(int(BG[i:i + 2], 16) for i in (1, 3, 5)) / 3
    rows, worst = [], 0.0
    files = sorted(frames_dir.glob("f*.png"))
    for i, f in enumerate(files):
        got = centroid_x(f, ground)
        want = expected_x(p, i)
        err = abs(got - want) if got is not None else float("inf")
        worst = max(worst, err)
        rows.append({"frame": i, "measured_x": None if got is None else round(got, 2), "expected_x": round(want, 2),
                     "error_px": None if got is None else round(err, 2)})
    return {"rows": rows, "worst_px": worst, "frames": len(files)}


def last_path() -> Path:
    return paths.data() / "ae" / "smoke" / "last.json"


def last() -> GateResult:
    raw = jsonio.read(last_path(), default=None)
    if raw:
        r = GateResult.from_dict(raw)
        r.details = {**(r.details or {}), "recorded": True}
        return r
    return GateResult.unchecked(GATE, "the After Effects smoke test hasn't run on this Mac yet",
                                details={"next": "slate ae smoke --go (a person starts After Effects)"})


def run(out_dir: str | os.PathLike | None = None, *, go: bool = False, runner: Runner | None = None,
        om: str = OM_TEMPLATE, **size: Any) -> GateResult:
    """Build, render and measure the smoke test (with the person's go), or report the last result."""
    if not go:
        return last()
    guard.person_start(go, "The After Effects smoke test")
    out = paths.ensure_dir(Path(out_dir) if out_dir else paths.data() / "ae" / "smoke")
    p = params(**{**DEFAULT, **size})
    run_proc = runner or procs.run
    res = driver.expect_ok(driver.run_jsx(driver.script("smoke_build.jsx"), args={**p, "out_dir": str(out)},
                                          runner=runner), "The smoke test's build")
    rep = res["report"]
    aep = out / "smoke.aep"
    if not aep.is_file():
        raise SlateError(f"After Effects reported the smoke build done, but there's no project at {aep}.")
    mov = out / "smoke.mov"
    mov.unlink(missing_ok=True)
    argv = [driver.aerender_path(), "-project", str(aep), "-comp", COMP, "-RStemplate", "Best Settings",
            "-OMtemplate", om, "-output", str(mov), "-v", "ERRORS_AND_PROGRESS"]
    with driver.ae_lock(bridge=False, owner="slate-ae-smoke"):
        proc = run_proc(argv, timeout=600)
    (out / "smoke.aerender.log").write_text((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or ""),
                                            encoding="utf-8")
    n = ae_masters.frames(mov, runner=run_proc)
    travel = abs(p["to_x"] - p["from_x"])
    engine = gates.engine("After Effects + aerender", str(rep.get("ae_version") or "unknown"))
    evidence = [str(aep), str(mov), str(out / "smoke_measure.json")]
    if getattr(proc, "returncode", 1) != 0 or n != p["frames"]:
        result = GateResult.fail(GATE, f"aerender made {n if n is not None else 'no'} frames of {p['frames']}",
                                 evidence=evidence[:2] + [str(out / "smoke.aerender.log")], engine=engine,
                                 details={"returncode": getattr(proc, "returncode", None)})
        _record(result)
        return result
    frames_dir = out / "frames"
    frames_dir.mkdir(exist_ok=True)
    for f in frames_dir.glob("f*.png"):
        f.unlink()
    ex = run_proc(["ffmpeg", "-v", "error", "-y", "-i", str(mov), str(frames_dir / "f%03d.png")], timeout=300)
    if getattr(ex, "returncode", 1) != 0:
        raise SlateError(f"ffmpeg couldn't pull frames out of {mov}.")
    m = measure(frames_dir, p)
    curve_err = float(rep.get("curve_max_error_px") or 0.0)
    jsonio.write_atomic(out / "smoke_measure.json", {"params": p, "measure": m, "ae_curve_max_error_px": curve_err,
                                                      "readback": rep.get("readback"),
                                                      "expected_ease": rep.get("expected_ease")})
    limit = TOLERANCE * travel
    ok = m["frames"] == p["frames"] and m["worst_px"] <= limit and curve_err <= limit
    summary = (f"{p['frames']} frames rendered; the dot follows the ease to {m['worst_px']:.2f} px at worst "
               f"(limit {limit:.1f} px, 1% of its {travel:.0f} px move); AE's own curve is within {curve_err:.3f} px")
    details = {"worst_px": round(m["worst_px"], 3), "limit_px": round(limit, 3), "ae_curve_max_error_px": curve_err,
               "scripting_pref": rep.get("pref_scripting_file_network_security"),
               "om_templates": rep.get("om_templates"), "rs_templates": rep.get("rs_templates")}
    result = (GateResult.pass_ if ok else GateResult.fail)(GATE, summary if ok else "the smoke render misses the ease: "
                                                            + summary, evidence=evidence, engine=engine, details=details)
    _record(result)
    for f in frames_dir.glob("f*.png"):
        f.unlink()
    return result


def _record(result: GateResult) -> None:
    jsonio.write_atomic(last_path(), result.to_dict())
