"""The finish stage's gates, and the "every film gate passes" rule deliver stands on.

Master gates (docs/PLAN.md §7 stage 11):

- `master.frames`   every master holds exactly the film's frames, at the film's size and frame rate;
- `psnr`            the mezzanine master against the act renders, frame for frame, at 40 dB or better (slate.qa's own
                    film gate, run here so the result is recorded once);
- `master.psnr_h264` the H.264 master held to the same bar ([finish] gate_h264_psnr, on by default);
- `master.finish`   the master was made the way the film's mode says: After Effects for films a person supervises
                    (unless this Mac is set to finish them with ffmpeg), ffmpeg for autonomous and draft films;
- `ae.*`            the AE project audit (slate.ae.audit), for masters made in After Effects.

Only PASS counts. A gate that couldn't run is UNCHECKED, never a quiet pass.
"""

from __future__ import annotations

from pathlib import Path

from slate import gates, jsonio, log
from slate.errors import SlateError
from slate.finish import deps, media, settings
from slate.finish.film import Film
from slate.gates import GateResult


def threshold(film: Film) -> float:
    return float(film.ctx.targets.get("master_psnr_db") or settings.load()["psnr_min_db"])


def master_frames(film: Film, masters: list[dict]) -> GateResult:
    """Every master: exactly the film's frame count, size and rate (packets counted, first number taken)."""
    total, size = film.total_frames, film.size
    want_rate = (film.clock.fps_num, film.clock.fps_den)
    lines, bad = [], []
    for m in masters:
        p = Path(m["path"])
        try:
            info = media.video_info(p)
        except SlateError as exc:
            bad.append(f"{film.rel(p)} can't be read: {exc}")
            continue
        rate = media.rate_of(info)
        got_rate = (rate.numerator, rate.denominator) if rate else None
        line = (f"{film.rel(p)}: {info.get('frames')} frames, {info.get('width')}×{info.get('height')}, "
                f"{got_rate[0]}/{got_rate[1]} fps" if got_rate else f"{film.rel(p)}: no frame rate")
        lines.append(line)
        if info.get("frames") != total:
            bad.append(f"{film.rel(p)} has {info.get('frames')} frames; the film has {total}")
        if (info.get("width"), info.get("height")) != tuple(size):
            bad.append(f"{film.rel(p)} is {info.get('width')}×{info.get('height')}; the film is {size[0]}×{size[1]}")
        if got_rate != want_rate:
            bad.append(f"{film.rel(p)} runs at {info.get('fps_num')}/{info.get('fps_den')}; the film at "
                       f"{want_rate[0]}/{want_rate[1]}")
        sound = media.audio_problem(p, film.clock.secs_of(1))
        if sound:
            bad.append(f"{film.rel(p)}: {sound}")
    inputs = film.ctx.inputs(["timing/shots.json", "film.json", *[m["path"] for m in masters]])
    engine = gates.engine("ffprobe count_packets", media.version())
    if not masters:
        return GateResult.unchecked("master.frames", "there's no master yet", evidence=["next: slate master"],
                                    engine=engine, inputs=inputs, details={"stage": "master"})
    if bad:
        return GateResult.fail("master.frames", "; ".join(bad[:3]), evidence=lines + bad, engine=engine,
                               inputs=inputs, details={"stage": "master", "problems": bad})
    return GateResult.pass_("master.frames", f"{len(masters)} master{'s' if len(masters) != 1 else ''}, each exactly "
                            f"{total} frames at {size[0]}×{size[1]}, {want_rate[0]}/{want_rate[1]} fps",
                            evidence=lines, engine=engine, inputs=inputs, details={"stage": "master"})


def master_psnr(film: Film, master: Path, renders: list[Path], *, gate: str) -> GateResult:
    """One master against the act renders it was made from, every frame, at the film's PSNR bar."""
    qa_measure = deps.module("slate.qa.measure")
    limit = threshold(film)
    engine = media.engine("psnr", pix_fmt="yuv422p10le", threshold_db=limit)
    inputs = film.ctx.inputs(["timing/shots.json", master, *renders])
    if qa_measure is None:
        return GateResult.unchecked(gate, "slate.qa isn't installed, so the master can't be measured",
                                    engine=engine, inputs=inputs, details={"stage": "master"})
    try:
        values = qa_measure.video_psnr(master, renders)
        got = media.frames(master)
    except SlateError as exc:
        return GateResult.unchecked(gate, f"ffmpeg couldn't compare the master: {exc}", engine=engine, inputs=inputs,
                                    details={"stage": "master"})
    total = film.total_frames
    s = qa_measure.summarize(values, threshold=limit)
    report = film.path(f"masters/psnr_{master.stem}.json")
    jsonio.write_atomic(report, {"generated": "do not edit by hand: slate master", "master": film.rel(master),
                                 "renders": [film.rel(r) for r in renders], "threshold_db": limit, "summary": s,
                                 "per_frame_db": [qa_measure.finite(v) for v in values]})
    line = (f"{film.rel(master)} vs {len(renders)} act render{'s' if len(renders) != 1 else ''}: {s['frames']} frames "
            f"compared, mean {s['mean_db']} dB, worst {s['min_db']} dB")
    details = {"stage": "master", "master": film.rel(master), "report": film.rel(report),
               "summary": {k: v for k, v in s.items() if k != "below"}}
    if got != total or s["frames"] != total:
        return GateResult.fail(gate, f"the master has {got} frames and {s['frames']} were compared; the film has "
                               f"{total}", evidence=[line, film.rel(report)], engine=engine, inputs=inputs,
                               details=details)
    if s.get("below_count"):
        worst = s["worst"][0] if s.get("worst") else {"frame": "?", "db": s["min_db"]}
        return GateResult.fail(gate, f"{s['below_count']} frame{'s' if s['below_count'] != 1 else ''} under "
                               f"{limit:g} dB (worst {s['min_db']} dB at film frame {worst['frame']})",
                               evidence=[line, film.rel(report)], engine=engine, inputs=inputs, details=details)
    return GateResult.pass_(gate, f"every frame matches the renders (worst {s['min_db']} dB, mean {s['mean_db']} dB)",
                            evidence=[line, film.rel(report)], engine=engine, inputs=inputs, details=details)


def primary_psnr(film: Film, master: Path, renders: list[Path]) -> GateResult:
    """The film gate `psnr` (slate.qa's own, so `slate qa` and deliver see one result); ours when qa is missing."""
    qa = deps.module("slate.qa")
    if qa is not None and callable(getattr(qa, "run", None)):
        try:
            results = [r for r in qa.run(film.dir, ["psnr"]) if r.gate == "psnr"]
            if results:
                return results[0]
        except SlateError as exc:
            log.warn(f"slate qa psnr couldn't run ({exc.message}); measuring the master here instead")
    result = master_psnr(film, master, renders, gate="psnr")
    gates.write_report(film.dir, [result])
    return result


def master_finish(film: Film, method: str) -> GateResult:
    """Was the master made the way the film's mode says?"""
    rules = deps.mode_settings(film.mode)
    configured = str(settings.load().get("supervised_master") or "ae")
    engine = gates.engine("slate finish", None)
    inputs = film.ctx.inputs(["film.json"])
    details = {"stage": "master", "mode": film.mode, "method": method, "rule": rules.get("master")}
    if film.mode in ("autonomous", "draft"):
        if method != "ffmpeg":
            return GateResult.fail("master.finish", f"{film.mode} films are mastered with ffmpeg, not {method}",
                                   engine=engine, inputs=inputs, details=details)
        held = ("held for the owner's approval of this exact file" if film.mode == "autonomous"
                else "a draft: held, and never delivered as final")
        return GateResult.pass_("master.finish", f"mastered with ffmpeg; {held}", engine=engine, inputs=inputs,
                                details=details)
    if method == "ae":
        return GateResult.pass_("master.finish", "finished in After Effects (a person started it)", engine=engine,
                                inputs=inputs, details=details)
    if configured == "ffmpeg":
        return GateResult.pass_("master.finish", "mastered with ffmpeg: this Mac finishes supervised films with "
                                "ffmpeg ([finish] supervised_master)", engine=engine, inputs=inputs, details=details)
    return GateResult.fail("master.finish", "an interim ffmpeg master: films a person supervises are finished in "
                           "After Effects", evidence=["next: slate master --ae --go (you start it)"], engine=engine,
                           inputs=inputs, details=details)


# ---------------------------------------------------------------------------------------------------------------
# Every film gate passes (deliver)


def required_gates(film: Film) -> list[str]:
    """The gates a final delivery needs: every film gate slate.qa knows, plus the master gates for this master."""
    names = list(deps.qa_gate_names() or [])
    if not names:
        names = ["psnr"]
    names += ["master.frames", "master.finish"]
    rec = film.master_record() or {}
    if settings.load().get("gate_h264_psnr", True) and any(m.get("kind") == "h264" for m in rec.get("masters") or []):
        names.append("master.psnr_h264")
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def film_gates(film: Film) -> dict:
    """{passed, required, results, blocking}: every required gate and every recorded gate must be PASS (STALE when
    its inputs changed since it ran; a gate that never ran is UNCHECKED)."""
    report = gates.load_report(film.dir)
    rec = film.master_record() or {}
    required = required_gates(film)
    blocking: list[dict] = []
    results: list[dict] = []
    for name in required:
        r = report.get(name)
        if r is None:
            blocking.append({"gate": name, "status": "UNCHECKED", "summary": "hasn't run for this film"})
            continue
        results.append(r.to_dict())
        if not r.passed:
            blocking.append({"gate": name, "status": r.status, "summary": r.summary})
    if rec.get("method") == "ae":
        ae_results = [r for n, r in report.items() if n.startswith("ae.")]
        if not ae_results:
            blocking.append({"gate": "ae.audit", "status": "UNCHECKED",
                             "summary": "the AE project hasn't been audited (slate ae audit --go)"})
    for name, r in sorted(report.items()):
        if name in required:
            continue
        results.append(r.to_dict())
        if not r.passed:
            blocking.append({"gate": name, "status": r.status, "summary": r.summary})
    return {"passed": not blocking, "required": required, "results": results, "blocking": blocking}


def next_for(blocking: list[dict]) -> str:
    """The one command that moves the most blocking gates."""
    names = [b["gate"] for b in blocking]
    if any(n.startswith("master.") for n in names) and not any(not n.startswith("master.") for n in names):
        return "slate master"
    qa_names = [n for n in names if not n.startswith(("master.", "ae.", "sound."))]
    if qa_names:
        return "slate qa " + " ".join(qa_names[:8])
    if any(n.startswith("ae.") for n in names):
        return "slate ae audit --go (you start it)"
    if any(n.startswith("sound.") for n in names):
        return "slate verify"
    return "slate qa"
