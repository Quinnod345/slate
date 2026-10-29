"""Plates: the plan of what generates, and the conform of an approved take into the film.

`plan` reads every shot's generate block and says, before anything is spent, which may generate and why, on which
model, how many takes, and what the free /estimate says they cost.

`conform` is the films' plate_conform.py, generalized: only the approved take (its SHA-256 must still match what the
person picked), conformed to a JPEG sequence at the film's frame rate and size (motion-compensated from the clip's
native rate by default, as the films did from 24 fps), its native audio kept as a 48 kHz / 24-bit ambience WAV, and
optionally a ProRes 422 HQ at the native rate for After Effects. plate.json records the provenance: request id,
model, dimensions, frame rates, duration, the estimated and actual cost, and who approved it.

Every conform of a moving plate also checks its steps (slate.gen.repair.step_check): the plate's moving band is
measured frame to frame, and no step may be over 1.3× the mean of its neighbours. Generated video can hitch in the
clip itself (the films' Seedance plates dropped or froze a frame every second), and a hitch says so here, in
plate.json `steps` and as a warning naming `slate plates repair <shot>`, instead of surfacing later as a stutter.
"""

from __future__ import annotations

from pathlib import Path

from slate import jsonio, replay
from slate.capture import media
from slate.errors import Refused, SlateError, UsageError
from slate.gen import deps, policy, run as gen_run, store
from slate.vendors import higgsfield


def plan(film_dir: Path, *, client: higgsfield.Client | None = None, write: bool = True) -> dict:
    """Every shot with a generate block: may it generate, on what, how many takes, at what estimated cost."""
    film_dir = Path(film_dir)
    film = deps.load_film(film_dir)
    client = client or higgsfield.Client()
    rows = []
    for shot in deps.load_shots(film_dir):
        if not shot.get("generate"):
            continue
        sid = str(shot.get("id"))
        ok, why = policy.allowed(film, shot)
        row = {"shot": sid, "allowed": ok, "why": why, "reason": (shot.get("generate") or {}).get("reason"),
               "role": None, "model": None, "takes": None, "duration": None, "estimate_usd_per_take": None,
               "estimate_usd_total": None, "needs_approval": policy.requires_approval(film), "uploads": [],
               "problems": [], "status": None}
        if ok:
            try:
                prep = gen_run.prepare(film_dir, sid, client=client)
                row.update(role=prep.role, model=prep.model, takes=prep.takes,
                           duration=prep.template.get("duration"), estimate_usd_per_take=prep.per_take_usd,
                           estimate_usd_total=prep.total_usd, uploads=[u["file"] for u in prep.uploads])
            except (Refused, UsageError) as exc:
                row["allowed"] = False
                row["problems"].append(exc.message)
        rec = store.load_record(film_dir, sid)
        if rec:
            picked = store.approved_take(rec)
            row["status"] = (f"approved {picked['id']}" if picked else
                             f"{len(store.completed_takes(rec))} take(s) done, {len(store.live_takes(rec))} running")
        rows.append(row)
    allowed = [r for r in rows if r["allowed"]]
    result = {"schema": 1, "film": film.get("id"), "setting": policy.setting(film), "mode": policy.mode(film),
              "lane": higgsfield.LANE, "shots": rows,
              "totals": {"shots": len(rows), "allowed": len(allowed), "takes": sum(r["takes"] or 0 for r in allowed),
                         "estimate_usd": round(sum(r["estimate_usd_total"] or 0.0 for r in allowed), 4)},
              "replay": replay.fake_vendors(), "made_at": deps.now_iso()}
    if write:
        deps.require_part(result, "plate", "plan", what="the plates plan")
        jsonio.write_atomic(store.plates_dir(film_dir) / "plan.json",
                            jsonio.mark_generated(result, "slate plates plan"))
    return result


def _approved(film_dir: Path, shot_id: str) -> tuple[dict, dict, Path]:
    """The shot's approved take, checked against the person's recorded yes. Refuses otherwise: nothing else ships."""
    rec = store.require_record(film_dir, shot_id)
    take = store.approved_take(rec)
    if take is None:
        done = store.completed_takes(rec)
        if done and all(t.get("verdict") == "rejected" for t in done):
            raise Refused(f"Every take of {shot_id} was rejected, so there's nothing to conform.",
                          hint=f"slate gen run --shot {shot_id} for new takes, or build the shot in code.")
        raise Refused(f"{shot_id} has no approved take: plates conform only from a take a person picked.",
                      hint=f"slate gen judge --shot {shot_id}, then "
                           f"slate gen pick --shot {shot_id} --take <id> --by <who>")
    path = store.take_file(film_dir, take)
    if not path.is_file():
        raise Refused(f"{shot_id}'s approved take is missing from disk ({path}).")
    digest = jsonio.sha256(path)
    if digest != take.get("sha256"):
        raise Refused(f"{shot_id}'s approved take changed on disk since it was picked, so it isn't the approved file.")
    if not deps.approvals().is_approved(film_dir, f"plate-take:{shot_id}", take["files"][0]):
        raise Refused(f"There's no recorded approval for {shot_id}'s take {take['id']}.",
                      hint=f"slate gen pick --shot {shot_id} --take {take['id']} --by <who>")
    return rec, take, path


def conform(film_dir: Path, shot_id: str, *, method: str = "mci", fit: str = "cover", frames: int | None = None,
            audio: bool = True, prores: bool = False) -> dict:
    """The approved take as a JPEG sequence at the film's frame rate and size, with plate.json."""
    film_dir = Path(film_dir)
    rec, take, src = _approved(film_dir, shot_id)
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    size = deps.film_size(film)
    out = store.shot_dir(film_dir, shot_id) / "plate"
    seq_dir = out / "frames"
    probe = take.get("probe") or {}
    warnings: list[str] = []
    if probe.get("kind") == "image" or src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        seq = _still_sequence(src, seq_dir, clk, size, fit, frames or 1)
        info = {"width": probe.get("width"), "height": probe.get("height"), "fps": None, "fps_float": None,
                "duration": round((frames or 1) / clk.fps, 6), "frames": 1, "codec": "image", "audio": False,
                "vfr": False}
    else:
        info = media.probe(src)
        count = frames or clk.frame_of(float(info["duration"]))
        seq = media.write_sequence(src, seq_dir, fps_num=clk.fps_num, fps_den=clk.fps_den, count=count, size=size,
                                   fit=fit, method=method, info=info)
        if info.get("fps_float") and info["fps_float"] > clk.fps * 1.01 and method != "dup":
            warnings.append("the take is faster than the film; frames were blended down")
    steps = None
    if info.get("codec") != "image":
        steps = _steps(seq_dir, shot_id, warnings)
    ambience = None
    if audio and info.get("audio"):
        a = media.write_audio(src, out / "ambience.wav")
        ambience = {"file": deps.rel(film_dir, Path(a["file"])), "rate": a["rate"], "bits": a["bits"],
                    "duration": a["duration"]}
    prores_block = None
    if prores and info.get("codec") != "image":
        p = media.write_prores(src, out / f"{shot_id}_native_prores422hq.mov", info=info)
        prores_block = {**p, "file": deps.rel(film_dir, Path(p["file"]))}
    covers = (rec.get("block") or {}).get("covers") or [shot_id]
    covers_frames = _covered_frames(film_dir, covers)
    if covers_frames is not None and seq["count"] < covers_frames:
        warnings.append(f"the plate is {seq['count']} frames but the shots it covers run {covers_frames}: "
                        "extend the generation rather than holding its last frame")
    quote = take.get("quote") or {}
    plate = {"schema": 1, "id": shot_id, "shot": shot_id, "covers": covers, "take": take["id"],
             "source": {"file": deps.rel(film_dir, src), "sha256": take["sha256"],
                        "width": int(info.get("width") or probe.get("width") or 1),
                        "height": int(info.get("height") or probe.get("height") or 1),
                        "fps": info.get("fps"), "fps_float": info.get("fps_float"),
                        "duration": float(info.get("duration") or 1 / clk.fps), "frames": info.get("frames"),
                        "codec": info.get("codec"), "audio": bool(info.get("audio")), "vfr": bool(info.get("vfr"))},
             "frames": {**seq, "dir": deps.rel(film_dir, seq_dir), "pattern": media.PATTERN, "first": 0},
             "duration": round(seq["count"] / clk.fps, 6), "ambience": ambience, "prores": prores_block,
             "provenance": {"lane": higgsfield.LANE, "provider": higgsfield.PROVIDER,
                            "request_id": str(take.get("request_id")), "model": rec.get("model"),
                            "role": rec.get("role"), "prompt": (rec.get("block") or {}).get("prompt"),
                            "estimated_usd": quote.get("usd"), "saving_usd": quote.get("saving_usd"),
                            "list_usd": quote.get("list_usd"),
                            "actual_usd": take.get("actual_usd"), "replay": bool(take.get("replay")),
                            "approved_by": take.get("verdict_by") or "unknown", "approved_at": take.get("verdict_at"),
                            "take_sha256": take["sha256"], "submitted_at": take.get("submitted_at")},
             "covers_frames": covers_frames, "steps": steps, "warnings": warnings, "made_at": deps.now_iso(),
             "tool": {"name": "slate plates conform", "ffmpeg": media.ffmpeg_version(), "method": seq.get("method"),
                      "fit": fit}}
    plate = jsonio.mark_generated(plate, "slate plates conform")
    deps.require_part(plate, "plate", what=f"{shot_id}'s plate record")
    path = jsonio.write_atomic(out / "plate.json", plate)
    deps.lock_record(film_dir, deps.rel(film_dir, path), producer="slate plates conform",
                     inputs={deps.rel(film_dir, src): take["sha256"]})
    return {"shot": shot_id, "plate": deps.rel(film_dir, path), "frames": plate["frames"], "ambience": ambience,
            "warnings": warnings, "provenance": plate["provenance"]}


def _steps(seq_dir: Path, shot_id: str, warnings: list[str]) -> dict | None:
    """The conform's step check (no step over 1.3× its neighbours), with a warning when the plate hitches."""
    from slate.gen import repair  # noqa: PLC0415 - repair builds on this module

    try:
        steps = repair.step_check(seq_dir)
    except (SlateError, ValueError, OSError) as exc:
        warnings.append(f"the step check couldn't run: {getattr(exc, 'message', exc)}")
        return None
    over = steps["over_1_3"]
    if over:
        where = ", ".join(f"{o['frame']} ({o['ratio']}×)" for o in over[:6]) + (" …" if len(over) > 6 else "")
        warnings.append(f"the plate hitches: {len(over)} step(s) over {repair.STEP_LIMIT}× their neighbours at "
                        f"frame(s) {where}; slate plates repair {shot_id} re-times it")
    elif steps["frozen"]:
        warnings.append(f"the plate hitches: {len(steps['frozen'])} frozen frame(s) at {steps['frozen'][:6]}; "
                        f"slate plates repair {shot_id} re-times it")
    return steps


def _still_sequence(src: Path, seq_dir: Path, clk, size: tuple[int, int], fit: str, count: int) -> dict:
    from PIL import Image  # noqa: PLC0415

    seq_dir.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(seq_dir)
    with Image.open(src) as im:
        frame = media.image_fit(im.convert("RGB"), fit, size)
    first = seq_dir / media.frame_name(0)
    media.save_jpeg(frame, first)
    for i in range(1, count):
        media.link_or_copy(first, seq_dir / media.frame_name(i))
    return {"dir": str(seq_dir), "count": count, "fps_num": clk.fps_num, "fps_den": clk.fps_den,
            "width": frame.size[0], "height": frame.size[1], "method": "exact", "fit": fit}


def _covered_frames(film_dir: Path, ids: list[str]) -> int | None:
    try:
        shots = {s.get("id"): s for s in deps.load_shots(film_dir)}
    except (SlateError, OSError):
        return None
    total = 0
    for sid in ids:
        s = shots.get(sid) or {}
        if s.get("frame_in") is None or s.get("frame_out") is None:
            return None
        total += int(s["frame_out"]) - int(s["frame_in"])
    return total
