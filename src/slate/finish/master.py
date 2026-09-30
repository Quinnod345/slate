"""Masters (docs/PLAN.md §7 stage 11): After Effects finishes every film a person supervises; ffmpeg masters
autonomous and draft films, which are held.

**After Effects** (supervised and express films, and only on a person's `--go`). Two steps, each started by the
person, because AE has to be open for the first and closed for the second:

1. `slate master --ae --go` builds the editable AE project from the film's data (slate.ae.project.build) and audits
   it (slate.ae.audit), then asks the person to save their work and quit AE;
2. `slate master --ae --go` again renders the masters with aerender in chunks (slate.ae.masters.render: disk cache off
   and always restored, exact frame counts, the mix muxed once, never -shortest).

A project built from older renders or an older mix is rebuilt first (step 1 again), and `--rebuild` forces that.
Autonomous films never reach AE (Adobe's terms bar operations a person didn't start), whatever flags are passed.

**ffmpeg** (autonomous and draft films; a supervised film on request, as an interim master). The act renders are
joined in film order (stream copy when they're all the same ProRes, which is exact; otherwise encoded to ProRes 422
HQ 10-bit BT.709) with the final mix as 48 kHz / 24-bit PCM, and an H.264 master is made from that with the mix as
AAC. Both are written to `.part` files and only moved into place once their frame counts are exact.

**Gates**, recorded in qa/gates.json and in masters/master.json: every frame of the masters against the act renders
at 40 dB or better, exact frame counts, the finish matching the film's mode, and (for AE) the project audit.

Every master is "not approved for publishing" until the owner approves that exact file (`slate approved`).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from slate import gates, jsonio, log
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.finish import checks, deps, media, settings
from slate.finish.film import MASTER_RECORD, Film

COMMAND = "slate master"
PUBLISHING = "not approved for publishing"
AE_FINISH_RECORD = "ae/finish.json"
CHECKS_RECORD = "masters/checks.json"
METHODS = ("ae", "ffmpeg")
PSLT = ("Autonomous films never use After Effects: Adobe's terms bar operations a person didn't start. The film is "
        "mastered with ffmpeg and held for you; you can start its AE finish yourself once you approve it.")


# ---------------------------------------------------------------------------------------------------------------
# Which method


def choose(film: Film, *, ae: bool = False, ffmpeg: bool = False) -> tuple[str, str]:
    """(method, why) for this film's mode and the flags given. Refuses what the mode forbids."""
    if ae and ffmpeg:
        raise UsageError("Pick one: --ae or --ffmpeg.")
    mode = film.mode
    if mode == "autonomous":
        if ae:
            raise Refused(PSLT, hint="slate master (ffmpeg) masters it and holds it for the owner.")
        return "ffmpeg", "autonomous films are mastered with ffmpeg and held for the owner"
    if mode == "draft":
        if ae:
            raise Refused("Draft films are mastered with ffmpeg; the After Effects finish is for films a person "
                          "supervises.", hint="slate master --ffmpeg")
        return "ffmpeg", "draft films are mastered with ffmpeg and held (never delivered as final)"
    configured = str(settings.load().get("supervised_master") or "ae")
    if ffmpeg:
        if configured == "ffmpeg":
            return "ffmpeg", "this Mac finishes supervised films with ffmpeg ([finish] supervised_master)"
        return "ffmpeg", ("an interim ffmpeg master on request: films a person supervises are finished in After "
                          "Effects before they're delivered")
    if ae or configured == "ae":
        return "ae", "After Effects finishes every film a person supervises, and the person starts it"
    return "ffmpeg", "this Mac finishes supervised films with ffmpeg ([finish] supervised_master)"


def master_paths(film: Film) -> dict[str, str]:
    """Film-relative master paths (the same names the AE masters use)."""
    return {"prores_422_hq": f"masters/{film.id}_ProRes422HQ.mov", "h264": f"masters/{film.id}_H264.mp4"}


# ---------------------------------------------------------------------------------------------------------------
# Public entry


def run(film_dir: Path, *, method: str = "auto", go: bool = False, rebuild: bool = False, silent: bool = False,
        force: bool = False, approval: str | None = None) -> dict:
    """Master the film by `method` ("ae", "ffmpeg" or "auto": the film's mode decides). See the module docstring."""
    film = Film(Path(film_dir))
    method = (method or "auto").strip().lower()
    if method not in (*METHODS, "auto"):
        raise UsageError(f"Unknown master method {method!r}; ae, ffmpeg or auto.")
    chosen, why = choose(film, ae=method == "ae", ffmpeg=method == "ffmpeg")
    if chosen == "ae":
        return _run_ae(film, go=go, rebuild=rebuild, silent=silent, why=why)
    return _run_ffmpeg(film, silent=silent, force=force, approval=approval, why=why)


def plan(film_dir: Path, *, method: str = "auto", go: bool = False, rebuild: bool = False,
         silent: bool = False) -> dict:
    """What `slate master` would do now, without doing it (--dry-run)."""
    film = Film(Path(film_dir))
    out: dict = {"film": film.id, "mode": film.mode, "version": film.version(), "dry_run": True}
    try:
        chosen, why = choose(film, ae=method == "ae", ffmpeg=method == "ffmpeg")
    except SlateError as exc:
        return {**out, "would": "refuse", "why": exc.message, "hint": exc.hint}
    out.update(method=chosen, why=why)
    problems: list[str] = []
    try:
        renders = film.act_renders()
        out["renders"] = [r["file"] for r in renders]
    except SlateError as exc:
        problems.append(exc.message)
    mix = film.mix()
    out["mix"] = film.rel(mix) if mix else None
    if mix is None and not silent:
        problems.append("there's no final mix yet (slate mix)")
    if chosen == "ae":
        if not go:
            problems.append("After Effects starts only on a person's go (--go)")
        aep = deps.ae_aep(film.dir)
        rec = jsonio.read(film.path(AE_FINISH_RECORD), default=None) or {}
        stale = False
        if rec:
            try:
                stale = rec.get("fingerprint") != jsonio.sha256_obj(film.master_inputs_now())
            except SlateError:
                stale = True
        out["step"] = ("build and audit the AE project (AE opens), then save and quit AE"
                       if aep is None or rebuild or stale else "render the masters with aerender (AE must be closed)")
    else:
        out["step"] = "join the act renders with the mix (ProRes 422 HQ), then the H.264 master"
        out["writes"] = list(master_paths(film).values()) + [MASTER_RECORD]
    out["would"] = "refuse" if problems else "master"
    out["problems"] = problems
    return out


# ---------------------------------------------------------------------------------------------------------------
# ffmpeg


def _estimate_gb(renders: list[dict], mix: Path | None, seconds: float) -> float:
    size = sum(Path(r["path"]).stat().st_size for r in renders if Path(r["path"]).is_file())
    size += mix.stat().st_size if mix and mix.is_file() else 0
    return round(size * 1.2 / 1e9 + seconds * 6e6 / 1e9, 2)


def _same_prores(infos: list[dict]) -> bool:
    keys = {(i.get("codec"), i.get("width"), i.get("height"), i.get("fps_num"), i.get("fps_den"), i.get("pix_fmt"),
             i.get("profile")) for i in infos}
    return len(keys) == 1 and str(next(iter(keys))[0] or "").startswith(media.MEZZANINE)


def _make_prores(film: Film, renders: list[dict], mix: Path | None, out: Path) -> str:
    """Join the act renders (and mux the mix) into the ProRes 422 HQ master. Returns how the picture was made."""
    infos = [media.video_info(r["path"], count=False) for r in renders]
    for r, i in zip(renders, infos):
        if (i.get("width"), i.get("height")) != tuple(film.size):
            raise Refused(f"{r['file']} is {i.get('width')}×{i.get('height')}; the film is "
                          f"{film.size[0]}×{film.size[1]}.", hint=f"slate render --act {r['act']}")
    copy = _same_prores(infos)
    with tempfile.TemporaryDirectory(prefix="slate-master-") as tmp:
        lst = media.concat_list([r["path"] for r in renders], Path(tmp) / "acts.txt")
        argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
        if mix is not None:
            argv += ["-i", str(mix)]
        argv += ["-map", "0:v:0"] + (["-map", "1:a:0"] if mix is not None else [])
        if copy:
            argv += ["-c:v", "copy"]
        else:
            argv += ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le",
                     *media.BT709]
        if mix is not None:
            argv += ["-c:a", "pcm_s24le", "-ar", "48000"]  # never -shortest: it drops the picture's last frames
        argv += ["-f", "mov", str(out)]
        media.run(argv, what="Joining the act renders into the ProRes master",
                  timeout=media.timeout_for(film.total_frames))
    return "stream copy of the act renders (exact)" if copy else "encoded to ProRes 422 HQ 10-bit BT.709"


def _make_h264(film: Film, src: Path, out: Path) -> str:
    cfg = settings.load()["h264"]
    audio = media.has_audio(src)
    argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0"]
    argv += ["-map", "0:a:0"] if audio else []
    argv += media.h264_args(cfg.get("encoder", "libx264"), crf=cfg.get("crf"), preset=cfg.get("preset"),
                            bitrate=cfg.get("bitrate"), profile="high")
    if audio:
        argv += ["-c:a", "aac", "-b:a", str(cfg.get("audio_bitrate") or "320k"), "-ar", "48000"]
    argv += ["-movflags", "+faststart", "-f", "mp4", str(out)]
    media.run(argv, what="Encoding the H.264 master", timeout=media.timeout_for(film.total_frames, 1.0))
    return media.h264_encoder(cfg.get("encoder", "libx264"))


def _entry(film: Film, kind: str, path: Path, made: str) -> dict:
    info = media.video_info(path)
    return {"kind": kind, "file": film.rel(path), "sha256": jsonio.sha256(path), "bytes": path.stat().st_size,
            "frames": info.get("frames"), "codec": info.get("codec"), "profile": info.get("profile"),
            "pix_fmt": info.get("pix_fmt"), "width": info.get("width"), "height": info.get("height"),
            "fps": f"{info.get('fps_num')}/{info.get('fps_den')}", "audio": media.has_audio(path), "made": made,
            "publishing": PUBLISHING}


def _run_ffmpeg(film: Film, *, silent: bool, force: bool, approval: str | None, why: str) -> dict:
    deps.modes_check(film.dir, "master")  # a parked film waits for its person
    media.require()
    renders = film.act_renders()
    mix = film.mix()
    if mix is None and not silent:
        raise MissingPrerequisite("The film has no final mix yet, and a master without its mix isn't deliverable.",
                                  hint="slate mix (or --silent for a picture-only master)")
    if silent:
        mix = None
    names = master_paths(film)
    for rel in names.values():
        if film.path(rel).exists():
            deps.guard_regenerate(film.dir, rel, force=force, approval_id=approval)
    total = film.total_frames
    cfg = settings.load()
    folder = film.path("masters")
    folder.mkdir(parents=True, exist_ok=True)
    parts = {k: film.path(rel).with_name(f".{Path(rel).stem}.part{Path(rel).suffix}") for k, rel in names.items()}
    made: dict[str, str] = {}
    owner = f"{COMMAND} {film.id}"
    try:
        with deps.hold("render", owner=owner, timeout=float(cfg.get("lock_timeout_s") or 0)), \
                deps.reserve_disk(folder, _estimate_gb(renders, mix, film.seconds), owner=owner):
            log.info(f"master: joining {len(renders)} act render{'s' if len(renders) != 1 else ''} with "
                     f"{'the mix' if mix else 'no sound (--silent)'}")
            made["prores_422_hq"] = _make_prores(film, renders, mix, parts["prores_422_hq"])
            log.info("master: encoding the H.264 master")
            made["h264"] = f"H.264 ({_make_h264(film, parts['prores_422_hq'], parts['h264'])})"
            for kind, part in parts.items():
                got = media.frames(part)
                if got != total:
                    raise SlateError(f"The {kind} master came out at {got} frames; the film has {total}. Nothing was "
                                     "replaced.", hint="slate render list shows whether an act render is short.")
            for kind, part in parts.items():
                os.replace(part, film.path(names[kind]))
    finally:
        for part in parts.values():
            part.unlink(missing_ok=True)
    masters = [_entry(film, kind, film.path(names[kind]), made[kind]) for kind in ("prores_422_hq", "h264")]
    return _record_and_gate(film, method="ffmpeg", why=why, masters=masters, renders=renders, mix=mix)


# ---------------------------------------------------------------------------------------------------------------
# After Effects


def _run_ae(film: Film, *, go: bool, rebuild: bool, silent: bool, why: str) -> dict:
    deps.ae_person_start(go, "The After Effects finish", film.dir, "ae_finish")
    renders = film.act_renders()
    mix = film.mix()
    if mix is None and not silent:
        raise MissingPrerequisite("The film has no final mix yet, and a master without its mix isn't deliverable.",
                                  hint="slate mix (or --silent for a picture-only master)")
    fp = jsonio.sha256_obj(film.master_inputs_now())  # the project holds the act renders and the mix
    rec = jsonio.read(film.path(AE_FINISH_RECORD), default=None) or {}
    aep = deps.ae_aep(film.dir)
    stale = bool(rec) and rec.get("fingerprint") != fp
    cfg = settings.load()
    owner = f"{COMMAND} --ae {film.id}"
    if aep is None or rebuild or stale:
        reason = ("--rebuild" if rebuild else "the renders or the mix changed since the project was built" if stale
                  else "there's no AE project yet")
        log.info(f"master: building the After Effects project ({reason})")
        with deps.hold("ae", owner=owner, timeout=float(cfg.get("lock_timeout_s") or 0)):
            aep = deps.ae_build(film.dir)
            audit = deps.ae_audit_run(film.dir, aep)
        record = {"generated": f"do not edit by hand: {COMMAND} --ae", "aep": film.rel(aep),
                  "aep_sha256": jsonio.sha256(aep) if Path(aep).is_file() else None, "fingerprint": fp,
                  "inputs": film.master_inputs_now(), "built_at": gates.now_iso(), "why": reason,
                  "audit": gates.summarize(audit)}
        jsonio.write_atomic(film.path(AE_FINISH_RECORD), record)
        return {"film": film.id, "version": film.version(), "mode": film.mode, "method": "ae", "why": why,
                "status": "project_built", "aep": film.rel(aep), "audit": [r.to_dict() for r in audit],
                "audit_status": gates.overall(audit), "passed": False,
                "next": "Save your work in After Effects and quit it, then run `slate master --ae --go` again to "
                        "render the masters."}
    deps.ae_require_closed("rendering the masters")
    log.info("master: rendering the masters with aerender")
    with deps.hold("ae", owner=owner, timeout=float(cfg.get("lock_timeout_s") or 0)):
        res = deps.ae_masters(film.dir, silent=silent)
    masters = []
    for kind, key in (("prores_422_hq", "prores_422_hq"), ("h264", "h264")):
        p = Path(res[key])
        p = p if p.is_absolute() else film.path(p)
        masters.append(_entry(film, kind, p, "After Effects (aerender, chunked)"))
    return _record_and_gate(film, method="ae", why=why, masters=masters, renders=renders,
                            mix=None if silent else mix, aep=Path(aep))


# ---------------------------------------------------------------------------------------------------------------
# Record and gates


def _record_and_gate(film: Film, *, method: str, why: str, masters: list[dict], renders: list[dict],
                     mix: Path | None, aep: Path | None = None) -> dict:
    mode = film.mode
    held = mode in ("autonomous", "draft")
    record = {
        "generated": f"do not edit by hand: {COMMAND}",
        "film": film.id, "version": film.version(), "mode": mode, "method": method, "why": why,
        "made_at": gates.now_iso(),
        "fps": film.rate, "width": film.size[0], "height": film.size[1], "frames": film.total_frames,
        "inputs": film.ctx.inputs([r["path"] for r in renders] + ([mix] if mix else [])),
        "content_fingerprint": film.fingerprint(),
        "masters": masters,
        "aep": {"file": film.rel(aep), "sha256": jsonio.sha256(aep)} if aep and aep.is_file() else None,
        "held": held,
        "held_for": ("the owner's approval of this exact file; nothing posts without their yes" if mode == "autonomous"
                     else "never delivered as final (a draft)" if mode == "draft" else None),
        "publishing": PUBLISHING,
        "checks": CHECKS_RECORD,
    }
    # Written once, before the gates: slate.qa's psnr gate hashes this file, so rewriting it afterwards would turn
    # that gate STALE at once. The gate results go to qa/gates.json and masters/checks.json instead.
    jsonio.write_atomic(film.path(MASTER_RECORD), record)
    fresh = Film(film.dir)
    for m in masters:
        m["path"] = fresh.path(m["file"])
    render_paths = [Path(r["path"]) for r in renders]
    results = [checks.master_frames(fresh, masters)]
    primary = next((m for m in masters if m["kind"] == "prores_422_hq"), masters[0])
    results.append(checks.primary_psnr(fresh, primary["path"], render_paths))
    if settings.load().get("gate_h264_psnr", True):
        for m in masters:
            if m["kind"] == "h264":
                results.append(checks.master_psnr(fresh, m["path"], render_paths, gate="master.psnr_h264"))
    results.append(checks.master_finish(fresh, method))
    gates.write_report(film.dir, [r for r in results if r.gate != "psnr"])
    audit = deps.ae_audit_last(film.dir, aep) if method == "ae" else []
    everything = results + list(audit)
    for m in masters:
        m.pop("path", None)
    record["gates"] = [r.to_dict() for r in everything]
    record["passed"] = gates.overall(everything) == "PASS"
    jsonio.write_atomic(film.path(CHECKS_RECORD), {"generated": f"do not edit by hand: {COMMAND}",
                                                   "master": MASTER_RECORD, "checked_at": gates.now_iso(),
                                                   "passed": record["passed"], "gates": record["gates"]})
    producer = f"{COMMAND} --{method}"
    inputs = [film.rel(r["path"]) for r in renders] + ([film.rel(mix)] if mix else [])
    for m in masters:
        deps.lock_record(film.dir, m["file"], producer=producer, inputs=inputs, tool_version=media.version())
    deps.lock_record(film.dir, MASTER_RECORD, producer=producer, inputs=[m["file"] for m in masters])
    nxt = ("slate qa, then slate deliver" if record["passed"] else checks.next_for(
        [{"gate": r.gate} for r in everything if not r.passed]))
    return {"film": film.id, "version": record["version"], "mode": mode, "method": method, "why": why,
            "status": "mastered", "masters": masters, "held": held, "held_for": record["held_for"],
            "gates": record["gates"], "passed": record["passed"], "record": MASTER_RECORD, "next": nxt,
            "publishing": PUBLISHING}


def describe(result: dict) -> str:
    """Human text for a master result."""
    if result.get("status") == "project_built":
        lines = [f"Built the After Effects project for {result['film']}: {result['aep']}",
                 f"Audit: {result['audit_status']}", result["next"]]
        return "\n".join(lines)
    lines = [f"Mastered {result['film']} v{result['version']} with {result['method']} ({result['why']})."]
    for m in result.get("masters") or []:
        lines.append(f"  {m['file']}  {m['frames']} frames  {m['bytes'] / 1e6:.1f} MB  {m['made']}")
    lines.append(gates.format_table([gates.GateResult.from_dict(g) for g in result.get("gates") or []]))
    if result.get("held"):
        lines.append(f"Held: {result['held_for']}.")
    lines.append(f"Every master is {PUBLISHING} until its owner approves that exact file.")
    lines.append(f"next: {result['next']}")
    return "\n".join(lines)

