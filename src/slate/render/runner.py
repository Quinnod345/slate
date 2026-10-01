"""Running a render: chunks, the join and its proof, the registry, patches and splices, resume and the queue.

`run()` renders in this process, one act at a time (six builders and a 16-sample render once drove this Mac's load
to 293 on 14 cores, so renders take the one render slot). The CLI decides whether a render runs here or goes through
the job queue: long renders always go through the queue, and the queue's worker calls `run()` with `in_queue=True`,
because the queue already holds the render slot and the disk reservation for the job.

For each act (or range):

1. the plan is written to the target's chunk folder, so an interrupted render can be resumed;
2. each chunk is kept if it's already good (right frame count, recorded hash, same settings) or rendered, counted and
   recorded; a chunk that fails is tried again once;
3. the chunks are joined and the join is proven (joins.py); a render that fails its proof replaces nothing;
4. a whole act that replaces a registered render proves itself first (install_checks.py): the stability report
   against the act's accepted baseline, and, for a rendering-only change, the static-frame regression against the
   render it replaces; a FAIL keeps the new render aside (renders/actN.rejected.mov, with a strip) and installs nothing;
5. a whole act is registered (which locks the timeline) and then moved into place; a patch is spliced into the
   registered act render with proof (splice.py); a range goes to the file you named;
6. the chunks and the bundle are removed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
from pathlib import Path

from slate import errors, gates, jsonio, log
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, SpendRefused, UsageError
from slate.render import chunks as chunks_mod
from slate.render import clean, deps, engines, install_checks, joins, media, registry, shot_engines, splice
from slate.render.plan import CHUNK_EXT, Plan, Target, acts_from_shots, build, ceil_gb, render_settings, segments


# slate.errors.TryLater (exit 75: a rate limit, a service briefly away). Read defensively, so a checkout whose foundation
# predates it still imports.
_TRY_LATER = tuple(c for c in (getattr(errors, "TryLater", None),) if c is not None)


def _retryable(exc: SlateError) -> bool:
    """An engine that fell over is worth one more try; a missing tool, a bad argument, a busy Mac or a "try later"
    isn't (that one goes back to the queue whole, exit 75, and resumes from its kept chunks)."""
    return not isinstance(exc, (MissingPrerequisite, UsageError, Busy, SpendRefused, *_TRY_LATER))


def _resolve(film_dir: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else Path(film_dir) / path


def _registered(film_dir: Path) -> dict[int, dict]:
    return {int(e["act"]): e for e in registry.entries(film_dir)}


def _patch_defaults(film_dir: Path, shots: dict, act: int | None, frames, engine: str | None,
                    samples: int | None) -> tuple[str, int | None]:
    """A patch matches the render it goes into: same engine and sample count unless you say otherwise.

    Only partial ranges inherit; a whole act (with or without --from/--to) starts from the defaults, like any render.
    """
    if frames is None:
        return engine or "remotion", samples
    reg = _registered(film_dir)
    acts = acts_from_shots(shots)
    if act is not None:
        chosen = next((a for a in acts if a.index == act), None)
        parts = [(chosen, int(frames[0]), int(frames[1]))] if chosen is not None else []
    else:
        parts = segments(int(frames[0]), int(frames[1]), acts)
    touched = [a.index for a, lo, hi in parts if not (lo == 0 and hi == a.frames - 1)]
    entries = [reg[i] for i in touched if i in reg]
    if engine is None:
        found = {e["engine"] for e in entries}
        engine = found.pop() if len(found) == 1 else "remotion"
    if samples is None and entries:
        found_s = {e.get("samples") for e in entries if e["engine"] == engine}
        if len(found_s) == 1:
            samples = found_s.pop()
    return engine, samples


def _shot_patch_engine(film_dir: Path, shots: dict, act: int | None, frames, engine: str | None) -> tuple[str | None, dict]:
    """A patch inside a shot drawn by another engine than its act's renders in that shot's engine (and from its scene);
    a patch across such a shot's edge is refused (the seam between engines is a cut). Returns (engine, {act: segment})
    for the patch's targets that lie in such a shot."""
    if frames is None or act is None:
        return engine, {}
    reg = _registered(film_dir)
    a = next((x for x in acts_from_shots(shots) if x.index == act), None)
    if a is None:
        return engine, {}
    base = (reg.get(act) or {}).get("engine") or shot_engines.act_engine(film_dir, act) or "remotion"
    if base == "ae":
        return engine, {}
    segs = shot_engines.segments(film_dir, shots, act, a.frame_in, base)
    seg = shot_engines.segment_for(segs, int(frames[0]), int(frames[1]))
    if seg is None:
        return engine, {}
    if engine is not None and engine != seg.engine:
        raise Refused(f"Act {act} frames {frames[0]}–{frames[1]} are shot {seg.shot}, drawn in {seg.engine}; a patch "
                      f"there renders in {seg.engine}.", hint=f"Leave --engine off, or pass --engine {seg.engine}.")
    return seg.engine, {act: seg}


def prepare(film_dir: Path, *, act: int | None = None, frames: tuple[int, int] | None = None,
            engine: str | None = "remotion", samples: int | None = None, out: str | Path | None = None,
            draft: bool = False, detach: bool = False) -> tuple[dict, Plan]:
    """(film.json, plan) for a render, without doing anything."""
    film_dir = Path(film_dir).resolve()
    film = deps.load_film(film_dir)
    shots = deps.load_shots(film_dir)
    shot_engine, in_shot = _shot_patch_engine(film_dir, shots, act, frames, None if engine == "remotion" and frames
                                              else engine)
    if in_shot:
        engine = shot_engine
    engine, samples = _patch_defaults(film_dir, shots, act, frames, engine, samples)
    if in_shot and samples is None:
        entry = _registered(film_dir).get(act) or {}
        samples = entry.get("samples")
    p = build(film_dir, film, shots, act=act, frames=frames, engine=engine, samples=samples, draft=draft, out=out,
              detach=detach, registered=_registered(film_dir))
    for t in p.targets:
        seg = in_shot.get(t.act.index)
        if seg is not None and t.kind == "patch":
            t.scene = seg.to_dict(film_dir)["scene"]
    return film, p


def check_patch(film_dir: Path, p: Plan, t: Target) -> dict:
    """A patch needs a registered, intact render of its act, at the act's current frames, from the same engine and
    sample count (anything else would put a visible seam in the act). Returns the registry entry."""
    entry = registry.find(film_dir, t.act.index)
    n = t.act.index
    if entry is None:
        raise Refused(f"Act {n} has no registered render to patch yet.",
                      hint=f"Render the whole act first: slate render --act {n}")
    if not _resolve(film_dir, entry["file"]).is_file():
        raise Refused(f"Act {n}'s registered render ({entry['file']}) is missing.", hint=f"slate render --act {n}")
    if list(entry["frames"]) != [t.act.frame_in, t.act.frame_out]:
        raise Refused(f"Act {n} moved since it was rendered (registered {entry['frames']}, timing now "
                      f"{[t.act.frame_in, t.act.frame_out]}), so a patch would land on the wrong frames.",
                      hint=f"Render the whole act again: slate render --act {n}")
    if entry["engine"] != p.engine and not t.scene:
        raise Refused(f"Act {n} was rendered with {entry['engine']}; a patch has to come from the same engine.",
                      hint=f"Add --engine {entry['engine']}, or render the whole act with {p.engine}.")
    if entry.get("samples") != p.samples:
        raise Refused(f"Act {n} was rendered at {entry.get('samples')} samples; a patch at {p.samples} would show a "
                      "seam in the motion blur.",
                      hint=f"Leave --samples off (the patch then matches), or render the whole act again.")
    return entry


def _settings(p: Plan, t: Target, eng: engines.Engine) -> dict:
    """Everything that makes a chunk what it is. A chunk made with different settings is never reused.

    The sources are the engine's own bundle hash when it gives one (it covers exactly what's bundled: scenes, kit,
    timing, film.json, the lockfile), otherwise Slate's hash of the same inputs.
    """
    return {"engine": p.engine, "samples": p.samples, "codec": eng.codec, "width": p.width, "height": p.height,
            "fps_num": p.fps_num, "fps_den": p.fps_den, "act": t.act.index,
            "act_frames": [t.act.frame_in, t.act.frame_out], "sources": eng.bundle_hash or eng.sources()}


def _options(p: Plan, t: Target) -> dict:
    """What `--resume` needs to run this target again."""
    frames = None if t.kind == "act" else [t.first, t.last]
    return {"act": t.act.index, "frames": frames, "engine": p.engine, "samples": p.samples, "draft": p.draft,
            "out": t.out if t.kind == "range" else None, "kind": t.kind}


def _render_chunk(eng: engines.Engine, ws: chunks_mod.Workspace, c: chunks_mod.Chunk, t: Target, p: Plan,
                  m: media.Media, tries: int) -> dict:
    ws.discard(c)
    last: SlateError | None = None
    for attempt in range(1, tries + 1):
        try:
            result = eng.render(t.act.index, c.first, c.last, c.part, p.samples)
            if not c.part.is_file():
                raise Refused(f"The {eng.name} engine said it rendered chunk {c.index + 1} but wrote no file.")
            counted = m.count_frames(c.part)
            if counted != c.frames:
                raise Refused(f"chunk {c.index + 1} of {len(ws.chunks)} (act frames {c.first}–{c.last}) came out "
                              f"{counted} frames, not {c.frames}")
            os.replace(c.part, c.path)
            ws.record(c, counted, result)
            if result.get("bundle_hash") and not eng.bundle_hash:
                eng.bundle_hash = str(result["bundle_hash"])
            return result
        except SlateError as exc:
            c.part.unlink(missing_ok=True)
            if not _retryable(exc):
                raise
            last = exc
            if attempt < tries:
                log.warn(f"act {t.act.index} chunk {c.index + 1}/{len(ws.chunks)} failed ({exc.message}); "
                         "trying it once more")
    raise Refused(f"Act {t.act.index}, chunk {c.index + 1} of {len(ws.chunks)} (act frames {c.first}–{c.last}) "
                  f"failed {tries} times: {last.message if last else 'unknown error'}",
                  hint="The chunks that finished are kept: fix the cause, then `slate render --resume`.")


_WORST_FIRST = ("FAIL", "STALE", "UNCHECKED", "REVIEW", "PASS")


def _engine_gates(t: Target, ws: chunks_mod.Workspace) -> list[dict]:
    """The engine's own gate results for this render (the kit's font gate, the web renderer's network wall), read from
    every chunk's record so a resumed render keeps them too: one per gate, the worst across the chunks, named
    render.act<N>.<gate> so `slate status` files them under the render."""
    worst: dict[str, dict] = {}
    for c in ws.chunks:
        record = jsonio.read(c.sidecar, default=None) or {}
        for g in (record.get("engine_result") or {}).get("gates") or []:
            if not isinstance(g, dict) or not g.get("gate") or g.get("status") not in _WORST_FIRST:
                continue
            name = f"render.act{t.act.index}.{g['gate']}"
            held = worst.get(name)
            if held is None or _WORST_FIRST.index(g["status"]) < _WORST_FIRST.index(held["status"]):
                details = dict(g.get("details") or {}) if isinstance(g.get("details"), dict) else {}
                worst[name] = {**g, "gate": name, "details": {**details, "stage": "render", "chunk": c.index}}
    return [worst[k] for k in sorted(worst)]


def _trace_written_while_rendering(film_dir: Path, t: Target, ws: chunks_mod.Workspace) -> dict | None:
    """The trace an engine wrote while it rendered (the web renderer does), from the last chunk that reports one, with
    the act's scene files as what it was made from."""
    for c in reversed(ws.chunks):
        record = jsonio.read(c.sidecar, default=None) or {}
        trace = (record.get("engine_result") or {}).get("trace")
        if isinstance(trace, dict) and trace.get("written") and trace.get("file"):
            scenes = Path(film_dir) / "scenes" / f"act{t.act.index}"
            sources = sorted(p.relative_to(film_dir).as_posix() for p in scenes.rglob("*")
                             if p.is_file() and p.name != ".DS_Store") if scenes.is_dir() else []
            return {**trace, "sources": sources}
    return None


def _refresh_trace(film_dir: Path, t: Target, eng: engines.Engine, ws: chunks_mod.Workspace) -> dict | None:
    """Bring the act's motion trace up to date with the scenes just rendered (`slate qa motion` reads it), and record
    what made it. A trace that can't be written is reported, never a reason to undo a proven render."""
    try:
        res = eng.trace(t.act.index)
    except SlateError as exc:
        log.warn(f"act {t.act.index} is rendered, but its motion trace couldn't be refreshed: {exc.message}")
        return {"written": False, "reason": exc.message}
    if not res:
        res = _trace_written_while_rendering(film_dir, t, ws)
    if not res:
        return None
    trace = {k: res[k] for k in ("written", "file", "entries", "reason") if k in res}
    if trace.get("written") and trace.get("file"):
        try:
            deps.state_lock().record(film_dir, trace["file"], producer=f"slate render --act {t.act.index}",
                                     inputs=sorted(set(list(res.get("sources") or []) + ["timing/shots.json"])))
        except SlateError as exc:
            log.warn(f"couldn't record {trace['file']} in film.lock.json: {exc.message}")
    return trace


def _frames_gate(t: Target, proof: dict, m: media.Media) -> gates.GateResult:
    ok = proof["status"] == "PASS"
    name = f"render.act{t.act.index}.frames" if t.kind != "range" else "render.range.frames"
    summary = (f"act {t.act.index} ({t.act.shots}) frames {t.first}–{t.last}: {proof['frames']} frames joined from "
               f"{proof['chunks']} chunk(s), every join proven by {proof['method']}") if ok else \
        "; ".join(proof["problems"])
    try:
        version = m.version()
    except Exception:  # noqa: BLE001
        version = "unknown"
    return gates.GateResult(name, "PASS" if ok else "FAIL", summary, evidence=[t.out],
                            engine=gates.engine(getattr(m, "name", "media"), version),
                            details={"expected": proof["expected"], "counted": proof["frames"],
                                     "joins": len(proof["joins"])})


def _persist_gates(film_dir: Path, t: Target, result: dict) -> None:
    """Keep a film render's gates in qa/gates.json (slate.gates.write_report), so `slate qa`, `slate status` and
    deliver see them: the frame count, a splice's proof, and the engine's own (the kit's font gate, the web renderer's
    network wall), which were only in this command's output before. Each names the render file as its input, so it
    turns STALE when the render changes. A range written to a file of your choosing isn't the film's and stays out; a
    report that can't be written is a warning, never a reason to undo a proven render."""
    if t.kind not in ("act", "patch") or not result.get("gates"):
        return
    try:
        rendered = _resolve(film_dir, result.get("file") or t.out)
        inputs = gates.inputs_for(film_dir, [rendered]) if rendered.is_file() else {}
        keep = []
        for raw in result["gates"]:
            g = gates.GateResult.from_dict(raw) if isinstance(raw, dict) else raw
            g.inputs = {**(g.inputs or {}), **inputs}
            g.details = {**(g.details or {}), "stage": (g.details or {}).get("stage", "render")}
            keep.append(g)
        gates.write_report(film_dir, keep)
    except (OSError, ValueError, TypeError, SlateError) as exc:
        log.warn(f"act {t.act.index} is rendered, but its gates couldn't be written to qa/gates.json: "
                 f"{getattr(exc, 'message', exc)}")


def _put_in_place_and_register(film_dir: Path, joined: Path, final: Path, entry: dict,
                               ws: chunks_mod.Workspace) -> dict:
    """Move the proven render into place and register it; the previous render goes back if registering fails.

    Once the registry holds the new render, the render is done: a hiccup recording it in film.lock.json is reported
    as a warning (slate.state reads the registry itself, so the timeline is locked either way), never by undoing a
    proven render.
    """
    previous = final.with_name(f"{final.stem}.previous{final.suffix}")
    had_previous = final.exists()
    if had_previous:
        os.replace(final, previous)
    os.replace(joined, final)
    try:
        written = registry.write_entry(film_dir, entry)
    except BaseException as exc:
        os.replace(final, joined)
        if had_previous:
            os.replace(previous, final)
        joined.unlink(missing_ok=True)
        ws.write_plan("failed", problems=[getattr(exc, "message", str(exc))])
        raise
    previous.unlink(missing_ok=True)
    try:
        return registry.lock_timeline(film_dir, written)
    except SlateError as exc:
        log.warn(f"act {entry['act']} is rendered and registered, but slate.state couldn't record it: {exc.message}")
        return {"method": None, "files": [], "warning": exc.message}


def _install_checks(film_dir: Path, p: Plan, t: Target, eng: engines.Engine, joined: Path, final: Path,
                    m: media.Media, ws: chunks_mod.Workspace, result: dict, content: str, *,
                    accept_change: bool) -> None:
    """Before a whole act replaces its registered render (install_checks.py). Refuses, keeping the new render aside as
    actN.rejected and its chunks for an accepted re-run, when a check fails. A draft, an act's first render and a test
    media backend skip them."""
    prev = registry.find(film_dir, t.act.index)
    if prev is None or p.draft or getattr(m, "name", "") != "ffmpeg":
        return
    old = _resolve(film_dir, prev["file"])
    if not old.is_file():
        return
    rendering_only = (prev.get("content_hash") == content) if prev.get("content_hash") else None
    chk = install_checks.check(film_dir, act=t.act.index, frame_in=t.act.frame_in, new=joined, old=old,
                               rendering_only=rendering_only, accept_change=accept_change)
    result["gates"] += chk["gates"]
    if chk["notes"]:
        result["install_notes"] = chk["notes"]
    if not chk["refuse"]:
        return
    rejected = final.with_name(f"{final.stem}.rejected{final.suffix}")
    os.replace(joined, rejected)
    ws.write_plan("failed", problems=[chk["refuse"]])
    strips = ", ".join(chk["strips"]) or "none could be made"
    raise Refused(f"Act {t.act.index}'s new render wasn't installed: {chk['refuse']}. The registered render is "
                  f"unchanged; the new one is at {splice.registry_path(film_dir, rejected)} (strips: {strips}).",
                  hint=f"Look at the strip. Fix the cause and render again, or, if the change is meant, run "
                       f"`slate render --act {t.act.index} --accept-change` (its chunks are kept, so nothing renders "
                       "twice).",
                  details={"result": result, "rejected": splice.registry_path(film_dir, rejected),
                           "strips": chk["strips"]})


def _rollout_preview(film_dir: Path, p: Plan, t: Target, eng: engines.Engine) -> dict:
    """A roll-out's first step, before the full render is spent: the act at one sample against its reference in the
    old mode (qa/stability/references.json), where the reference holds still. A FAIL stops here."""
    refs = jsonio.read(film_dir / "qa" / "stability" / "references.json", default={}) or {}
    explicit = (refs.get("acts") or {}).get(str(t.act.index)) or {}
    reference = _resolve(film_dir, explicit["file"]) if explicit.get("file") else None
    preview = film_dir / "renders" / "previews" / f"act{t.act.index}.rollout{CHUNK_EXT[eng.codec]}"
    preview.parent.mkdir(parents=True, exist_ok=True)
    if reference is None or not reference.is_file():
        install_checks.rollout_preview(film_dir, act=t.act.index, preview=preview, reference=reference)  # refuses
    log.info(f"act {t.act.index}: the roll-out's one-sample preview first, against {reference.name}")
    eng.render(t.act.index, 0, t.act.frames - 1, preview, 1)
    gate = install_checks.rollout_preview(film_dir, act=t.act.index, preview=preview, reference=reference)
    if gate["status"] == "FAIL":
        raise Refused(f"Act {t.act.index}'s roll-out stopped at its preview: {gate['summary']}. Nothing was rendered "
                      "at full samples, and the registered render is unchanged.",
                      hint=f"Look at {splice.registry_path(film_dir, preview)} against the reference; fix the "
                           "rendering change, then roll out again.", details={"gate": gate})
    return gate


def _render_chunks(eng: engines.Engine, ws: chunks_mod.Workspace, t: Target, p: Plan, m: media.Media) -> tuple[list, list]:
    """Every chunk of a target that isn't already good, rendered (and tried again once). Returns (kept, rendered)."""
    tries = max(1, int(render_settings().get("chunk_tries", 2)))
    kept, rendered = [], []
    for c in ws.chunks:
        keep, why = ws.check(c, m)
        if keep:
            kept.append(c.index)
            continue
        if why != "missing":
            log.info(f"act {t.act.index} chunk {c.index + 1}/{len(ws.chunks)}: {why}; rendering it again")
        log.info(f"act {t.act.index} chunk {c.index + 1}/{len(ws.chunks)}: frames {c.first}–{c.last}")
        _render_chunk(eng, ws, c, t, p, m, tries)
        rendered.append(c.index)
    return kept, rendered


def _splice_shots(film_dir: Path, film: dict, p: Plan, t: Target, joined: Path, m: media.Media, *, go: bool,
                  fresh: bool) -> list[dict]:
    """The act's shots drawn by another engine (slate.render.shot_engines), each rendered over exactly its shot's
    frames at the act's sample count and spliced into the new act render with the splice's proof, before the act is
    checked and installed. Returns one record per shot, with its splice gate."""
    segs = shot_engines.segments(film_dir, deps.load_shots(film_dir), t.act.index, t.act.frame_in, p.engine)
    done: list[dict] = []
    for seg in segs:
        eng = engines.get(seg.engine, film_dir, film, go=go, scene=seg.scene)
        eng.preflight()
        pt = Target("patch", t.act, seg.first, seg.last,
                    f"renders/patches/act{t.act.index}_{seg.shot}_{seg.first:05d}-{seg.last:05d}{'.mov'}",
                    scene=seg.to_dict(film_dir)["scene"])
        p2 = dataclasses.replace(p, engine=seg.engine, targets=[pt])
        ws = chunks_mod.Workspace(film_dir, pt, _settings(p2, pt, eng), p.chunk_frames, CHUNK_EXT[eng.codec])
        if fresh:
            ws.remove()
        ws.write_plan("running", options=_options(p, Target("act", t.act, t.first, t.last, t.out)))
        log.info(f"act {t.act.index}: shot {seg.shot} in {seg.engine}, frames {seg.first}–{seg.last}")
        with contextlib.ExitStack() as stack:
            stack.callback(eng.close)
            eng.open()
            _render_chunks(eng, ws, pt, p2, m)
        patch = _resolve(film_dir, pt.out)
        patch.parent.mkdir(parents=True, exist_ok=True)
        proof = joins.assemble(ws.chunks, patch, codec=eng.codec, media=m)
        if proof["status"] != "PASS":
            raise Refused(f"Shot {seg.shot}'s {seg.engine} render didn't prove itself: " + "; ".join(proof["problems"]),
                          hint="Its chunks are kept; `slate render --act N` tries again.")
        spliced = splice.splice(film_dir, joined, patch, seg.first, seg.last)
        done.append({**seg.to_dict(film_dir), "patch_sha256": spliced["patch_sha256"], "gate": spliced["gate"]})
        patch.unlink(missing_ok=True)
        ws.write_plan("done")
        ws.remove()
    return done


def _render_target(film_dir: Path, p: Plan, t: Target, eng: engines.Engine, ws: chunks_mod.Workspace,
                   m: media.Media, *, accept_change: bool = False, go: bool = False, fresh: bool = False) -> dict:
    ws.write_plan("running")
    kept, rendered = _render_chunks(eng, ws, t, p, m)
    ws.write_plan("joining")

    final = _resolve(film_dir, t.out)
    joined = final if t.kind == "patch" else final.with_name(f"{final.stem}.new{final.suffix}")
    joined.parent.mkdir(parents=True, exist_ok=True)
    proof = joins.assemble(ws.chunks, joined, codec=eng.codec, media=m)
    frames_gate = _frames_gate(t, proof, m)
    result = {"kind": t.kind, "act": t.act.index, "shots": t.act.shots, "file": t.out, "first": t.first,
              "last": t.last, "film_frames": t.film_frames, "frame_count": proof["frames"],
              "chunks": {"total": len(ws.chunks), "kept": kept, "rendered": rendered}, "proof": proof,
              "gates": [frames_gate.to_dict(), *_engine_gates(t, ws)], "registered": False}
    if proof["status"] != "PASS":
        joined.unlink(missing_ok=True)
        ws.write_plan("failed", problems=proof["problems"])
        raise Refused(f"Act {t.act.index}'s render didn't prove itself, so nothing was replaced: "
                      + "; ".join(proof["problems"]) + ".",
                      hint="The chunks are kept; `slate render --resume` tries the join again.",
                      details={"result": result})

    if t.kind == "patch":
        entry = registry.find(film_dir, t.act.index)
        try:
            spliced = splice.splice(film_dir, entry["file"], joined, t.first, t.last)
        except SlateError as exc:
            ws.write_plan("failed", problems=[exc.message])
            raise
        result.update(splice=spliced, sha256=spliced["sha256_after"], file=entry["file"], registered=True,
                      patch_sha256=spliced["patch_sha256"])
        result["gates"].append(spliced["gate"])
        joined.unlink(missing_ok=True)
    else:
        sha = jsonio.sha256(joined)
        result["sha256"] = sha
        if t.kind == "act":
            # the act's shots in another engine go in first, so the act is checked and registered whole
            shots_done = _splice_shots(film_dir, eng.film, p, t, joined, m, go=go, fresh=fresh) \
                if p.engine != "ae" else []
            if shots_done:
                result["gates"] += [d.pop("gate") for d in shots_done]
                result["shot_engines"] = shots_done
                sha = jsonio.sha256(joined)
                result["sha256"] = sha
            content = install_checks.content_hash(film_dir, eng.film)
            _install_checks(film_dir, p, t, eng, joined, final, m, ws, result, content, accept_change=accept_change)
            entry = {"act": t.act.index, "engine": p.engine, "file": t.out, "frames": t.film_frames,
                     "samples": p.samples, "sha256": sha, "made_at": gates.now_iso(),
                     "bundle_hash": eng.bundle_hash or eng.sources(), "source_hash": eng.sources(),
                     "content_hash": content, **({"shot_engines": shots_done} if shots_done else {}),
                     "shots": t.act.shots, "width": p.width, "height": p.height, "fps_num": p.fps_num,
                     "fps_den": p.fps_den, "codec": "prores_422hq", "draft": p.draft,
                     "proof": {k: proof[k] for k in ("status", "method", "frames", "chunks")},
                     "history": [{"what": "render", "sha256": sha, "made_at": gates.now_iso(), "engine": p.engine,
                                  "samples": p.samples, "chunks": len(ws.chunks), "kept": len(kept)}]}
            registry.prepare_entry(film_dir, entry)  # checked before anything moves
            result["timeline_locked"] = _put_in_place_and_register(film_dir, joined, final, entry, ws)
            result["registered"] = True
        else:
            os.replace(joined, final)
    # A render that becomes the act's picture (a whole act, or a patch spliced into it) refreshes the act's motion
    # trace; a range written to a file of your choosing is a look, and leaves the film's files alone.
    if t.kind in ("act", "patch"):
        trace = _refresh_trace(film_dir, t, eng, ws)
        if trace is not None:
            result["trace"] = trace
    _persist_gates(film_dir, t, result)
    ws.write_plan("done")
    ws.remove()
    _prune_empty(film_dir / chunks_mod.CHUNKS, film_dir / "renders" / "patches")
    return result


def _prune_empty(*folders: Path) -> None:
    """Remove work folders a finished render left empty (never one with anything in it)."""
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            pass


def _slot_and_disk(stack: contextlib.ExitStack, film_dir: Path, p: Plan, in_queue: bool) -> None:
    """Take the render slot and reserve the disk, unless the queue already holds both for this job."""
    (film_dir / "renders").mkdir(parents=True, exist_ok=True)
    # Inside a queued job the queue's supervisor already holds the render slot and the job's disk reservation
    # (SLATE_JOB_ID says we're in one). Reserving again would count the same space twice.
    if not (in_queue and os.environ.get("SLATE_JOB_ID")):
        stack.enter_context(deps.render_lock(owner=f"slate render ({film_dir.name})"))
        stack.enter_context(deps.disk_reserve(film_dir / "renders", ceil_gb(p.disk_gb)))


def _execute_whole_acts(film_dir: Path, p: Plan, eng: engines.Engine, *, in_queue: bool) -> dict:
    """Engines that render a whole act in one pass (After Effects). The engine proves the frame count and registers
    the act; this checks the count again, registers it if the engine couldn't, and makes sure the timeline is locked."""
    results = []
    with contextlib.ExitStack() as stack:
        _slot_and_disk(stack, film_dir, p, in_queue)
        for t in p.targets:
            register = t.kind == "act"
            log.info(f"act {t.act.index}: {t.frames} frames, whole act, {p.engine}")
            res = eng.render_act(t.act.index, out=None if register else _resolve(film_dir, t.out), register=register)
            frames = [int(x) for x in (res.get("frames") or t.film_frames)]
            counted = frames[1] - frames[0]
            if counted != t.frames or frames != t.film_frames:
                raise Refused(f"The {p.engine} render of act {t.act.index} covers film frames {frames}, but the act is "
                              f"{t.film_frames} in timing/shots.json.", hint=f"slate render --engine {p.engine} "
                                                                             f"--act {t.act.index} --go")
            proof = {"status": "PASS", "method": f"{p.engine} frame count", "expected": t.frames, "frames": counted,
                     "chunks": 1, "joins": [], "problems": []}
            result = {"kind": t.kind, "act": t.act.index, "shots": t.act.shots, "file": res.get("file", t.out),
                      "first": 0, "last": t.frames - 1, "film_frames": frames, "frame_count": counted,
                      "sha256": res.get("sha256"), "chunks": {"total": 1, "kept": [], "rendered": [0]},
                      "proof": proof, "registered": False, "engine_result": res}
            if register:
                entry = registry.find(film_dir, t.act.index)
                if not res.get("registered") or entry is None or entry.get("sha256") != res.get("sha256"):
                    fields = {k: res[k] for k in registry.REQUIRED if k in res}
                    registry.write_entry(film_dir, {**fields, "shots": t.act.shots, "proof": proof})
                    entry = registry.find(film_dir, t.act.index)
                try:
                    result["timeline_locked"] = registry.lock_timeline(film_dir, entry)
                except SlateError as exc:
                    log.warn(f"act {t.act.index} is rendered and registered, but slate.state couldn't record it: "
                             f"{exc.message}")
                    result["timeline_locked"] = {"method": None, "files": [], "warning": exc.message}
                result["registered"] = True
            gate = gates.GateResult(f"render.act{t.act.index}.frames", "PASS",
                                    f"act {t.act.index} ({t.act.shots}): {counted} frames, the whole act, from "
                                    f"{p.engine}", evidence=[str(result["file"])],
                                    engine=gates.engine("aerender" if p.engine == "ae" else p.engine),
                                    details={"expected": t.frames, "counted": counted})
            result["gates"] = [gate.to_dict()]
            if register:
                _persist_gates(film_dir, t, result)
            results.append(result)
    all_gates = [g for r in results for g in r.pop("gates", [])]
    return {"ok": True, "ran": "queue" if in_queue else "here", "film": str(film_dir), "engine": p.engine,
            "samples": p.samples, "draft": p.draft, "renders": results, "gates": all_gates,
            "cleaned": {"bundles": 0, "freed_bytes": 0}}


def execute(film_dir: Path, film: dict, p: Plan, *, go: bool = False, fresh: bool = False,
            in_queue: bool = False, accept_change: bool = False, rollout: bool = False) -> dict:
    """Render a plan in this process. `accept_change` lets a rendering-only change that changed the picture replace the
    registered render (recorded as accepted); `rollout` renders each act's one-sample preview against its reference
    first."""
    film_dir = Path(film_dir).resolve()
    # a patch inside a shot drawn by another engine renders from that shot's own scene
    scenes = {t.scene for t in p.targets}
    scene = _resolve(film_dir, next(iter(scenes))) if len(scenes) == 1 and None not in scenes else None
    if len(scenes) > 1:
        raise UsageError("These ranges lie in different engines' shots: render each on its own.")
    eng = engines.get(p.engine, film_dir, film, go=go, scene=scene)
    eng.preflight()
    media.require_toolchain()
    if getattr(eng, "whole_acts_only", False):
        if any(t.kind == "act" for t in p.targets):
            deps.require_state_lock()
        return _execute_whole_acts(film_dir, p, eng, in_queue=in_queue)
    m = media.backend()
    if any(t.kind == "act" for t in p.targets):
        deps.require_state_lock()
    for t in p.targets:
        if t.kind == "patch":
            check_patch(film_dir, p, t)
    results: list[dict] = []
    swept: list[dict] = []
    with contextlib.ExitStack() as stack:
        _slot_and_disk(stack, film_dir, p, in_queue)
        # This process holds the render slot, so any bundle older than a render could use is a leak.
        swept = clean.sweep_bundles()
        stack.callback(eng.close)
        eng.open()
        workspaces = []
        for t in p.targets:
            ws = chunks_mod.Workspace(film_dir, t, _settings(p, t, eng), p.chunk_frames, CHUNK_EXT[eng.codec])
            for sibling in ws.siblings():
                log.info(f"removing chunks made with other settings: {sibling.name}")
                shutil.rmtree(sibling, ignore_errors=True)
            if fresh:
                ws.remove()
            ws.write_plan("waiting", options=_options(p, t))
            workspaces.append(ws)
        for t, ws in zip(p.targets, workspaces):
            label = f"act {t.act.index}" + ("" if t.kind == "act" else f" frames {t.first}–{t.last}")
            preview = _rollout_preview(film_dir, p, t, eng) if rollout and t.kind == "act" else None
            log.info(f"{label}: {t.frames} frames, {p.samples or 'AE'} samples, {len(ws.chunks)} chunk(s), "
                     f"{p.engine}")
            res = _render_target(film_dir, p, t, eng, ws, m, accept_change=accept_change, go=go, fresh=fresh)
            if preview is not None:
                res["gates"].insert(0, preview)
            results.append(res)
    all_gates = [g for r in results for g in r.pop("gates", [])]
    return {"ok": True, "ran": "queue" if in_queue else "here", "film": str(film_dir), "engine": p.engine,
            "samples": p.samples, "draft": p.draft, "renders": results, "gates": all_gates,
            "cleaned": {"bundles": len(swept), "freed_bytes": sum(i["bytes"] for i in swept)}}


def run(film_dir: Path, *, act: int | None = None, frames: tuple[int, int] | None = None,
        engine: str | None = "remotion", samples: int | None = None, out: str | Path | None = None,
        draft: bool = False, go: bool = False, fresh: bool = False, resume: bool = False,
        in_queue: bool = False, accept_change: bool = False, rollout: bool = False) -> dict:
    """Render in this process. See the package docstring; `engine=None` means the registered render's engine for a
    patch, otherwise remotion."""
    film_dir = Path(film_dir).resolve()
    if resume:
        return resume_run(film_dir, act=act, go=go, in_queue=in_queue)
    film, p = prepare(film_dir, act=act, frames=frames, engine=engine, samples=samples, out=out, draft=draft)
    return execute(film_dir, film, p, go=go, fresh=fresh, in_queue=in_queue, accept_change=accept_change,
                   rollout=rollout)


def resume_run(film_dir: Path, *, act: int | None = None, go: bool = False, in_queue: bool = False) -> dict:
    """Pick up every render in this film that didn't finish, with the options it was started with."""
    film_dir = Path(film_dir).resolve()
    todo = [pl for pl in chunks_mod.unfinished(film_dir) if pl.get("options")]
    if act is not None:
        todo = [pl for pl in todo if int(pl["options"]["act"]) == int(act)]
    if not todo:
        where = f" for act {act}" if act is not None else ""
        raise Refused(f"Nothing to resume{where}: every render in this film finished.",
                      hint="slate render list shows what's registered; slate render --act N starts a new render.")
    merged: dict = {"ok": True, "ran": "queue" if in_queue else "here", "film": str(film_dir), "renders": [],
                    "gates": [], "resumed": len(todo)}
    seen = set()
    for pl in sorted(todo, key=lambda d: (d["options"]["act"], (d["options"].get("frames") or [0])[0])):
        o = pl["options"]
        ident = (o["act"], tuple(o.get("frames") or ()), o.get("out"))
        if ident in seen:
            continue
        seen.add(ident)
        frames = tuple(o["frames"]) if o.get("frames") else None
        res = run(film_dir, act=o["act"], frames=frames, engine=o["engine"], samples=o.get("samples"),
                  out=o.get("out"), draft=bool(o.get("draft")), go=go, in_queue=in_queue)
        merged.update({k: res[k] for k in ("engine", "samples", "draft")})
        merged["renders"] += res["renders"]
        merged["gates"] += res["gates"]
    return merged


def job_argv(film_dir: Path, p: Plan, *, act: int | None, frames, out, go: bool, fresh: bool,
             resume: bool, accept_change: bool = False, rollout: bool = False) -> list[str]:
    """The `slate` command the queue runs (its words after `slate`): exactly this plan, rendered in the job's own
    process. Spelled out from the plan, never this process's argv, which is wrong under the MCP server."""
    argv = ["render", "--film", str(film_dir), "--json", "--in-queue", "--engine", p.engine]
    if resume:
        argv.append("--resume")
    if act is not None:
        argv += ["--act", str(act)]
    if frames is not None:
        argv += ["--from", str(frames[0]), "--to", str(frames[1])]
    if p.samples is not None:
        argv += ["--samples", str(p.samples)]
    if p.draft:
        argv.append("--draft")
    if out is not None:
        argv += ["--out", str(out)]
    if go:
        argv.append("--go")
    if fresh:
        argv.append("--fresh")
    if accept_change:
        argv.append("--accept-change")
    if rollout:
        argv.append("--rollout")
    return argv


def resume_argv(film_dir: Path, *, act: int | None, go: bool) -> list[str]:
    """The queue's command for `--resume` (its words after `slate`): each unfinished render keeps the options it was
    started with."""
    argv = ["render", "--film", str(film_dir), "--json", "--in-queue", "--resume"]
    if act is not None:
        argv += ["--act", str(act)]
    if go:
        argv.append("--go")
    return argv


def submit(film_dir: Path, film: dict, p: Plan, argv: list[str]) -> str:
    """Queue the render and return the job id at once, through the queue's --detach convention
    (slate.jobs.client.detach_current) with `argv` (job_argv or resume_argv) as the command.

    The queue holds the render slot (and After Effects, for an AE render) and the disk reservation for the job; inside
    the job, slate.ae's lock uses the job's own `ae` slot instead of waiting on itself.
    """
    priority = "autonomous" if engines._judged_mode(film_dir, film) == "autonomous" else "supervised"
    resources = ("render", "ae") if p.engine == "ae" else ("render",)
    return deps.detach("render", argv, film=str(film_dir), resources=resources, priority=priority,
                       disk_gb=ceil_gb(p.disk_gb))

