"""Picture gates on the act renders and the master.

- glitch    the one- and two-frame glitch scan on every act render, triaged: flags within ±3 frames of a planned
            beat are skipped (listed, not hidden); what's left is REVIEW for a person, one item per frame;
- boundary  the joins between act renders, for every film (the films' boundary check only ever read film A's
            renders). Each join must be contiguous (a gap or overlap FAILs), and the picture must move no more
            across it than it already does in the ten frames either side (+1.0 for encoder noise), else REVIEW;
- psnr      the master against the act renders it was built from, frame for frame: at least 40 dB on every frame
            and exactly the film's frame count.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import __version__, gates, jsonio
from slate.errors import SlateError
from slate.gates import GateResult
from slate.qa import glitch as glitchmod
from slate.qa import measure, media
from slate.qa.checks import Sub, combine, plural, review_items
from slate.qa.film import FilmContext, context

JOIN_FRAMES = 10
JOIN_SLACK = 1.0
PSNR_MIN_DB = 40.0
MEZZANINE = ("prores", "dnxhd", "ffv1", "rawvideo", "png", "qtrle", "v210", "utvideo")


def _no_renders(gate: str, ctx: FilmContext, engine: dict) -> GateResult:
    return GateResult.unchecked(gate, "no act renders are registered yet", evidence=["next: slate render"],
                                engine=engine, inputs=ctx.inputs([ctx.REGISTRY]))


# ---------------------------------------------------------------------------------------------- glitch
def _scan_cached(ctx: FilmContext, act: int, entry: dict) -> list[dict]:
    """The raw scan for one render, cached in qa/glitch/ against the render's hash and the scan's settings."""
    path = entry["path"]
    key = {"file": ctx.rel(path), "sha256": ctx.sha(path), "threshold": glitchmod.THRESHOLD,
           "size": list(media.analysis_size(*ctx.size, long_side=glitchmod.LONG_SIDE, tile=glitchmod.TILE)),
           "film_frame": entry["frame_in"], "version": __version__}
    cache = ctx.path(f"qa/glitch/act{act}.json")
    cached = jsonio.read(cache, default=None) if cache.is_file() else None
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached.get("flags") or []
    flags = glitchmod.scan(path, film_size=ctx.size, first_film_frame=entry["frame_in"],
                           count=entry["frame_out"] - entry["frame_in"])
    jsonio.write_atomic(cache, {"generated": "do not edit by hand: slate qa glitch", "key": key, "flags": flags})
    return flags


def glitch(film_dir: Path) -> GateResult:
    """One- and two-frame glitches in every act render, triaged against planned beats (±3 frames skipped)."""
    ctx = context(film_dir)
    engine = gates.engine("slate glitch scan", __version__, decoder=f"ffmpeg {media.version('ffmpeg')}",
                          threshold=glitchmod.THRESHOLD, window_frames=glitchmod.WINDOW)
    if not ctx.render_entries():
        return _no_renders("glitch", ctx, engine)
    if not media.have("ffmpeg") or ctx.size is None:
        why = "ffmpeg isn't installed" if not media.have("ffmpeg") else "film.json has no width / height"
        return GateResult.unchecked("glitch", f"{why}, so the renders can't be scanned", engine=engine,
                                    inputs=ctx.inputs([ctx.REGISTRY, "film.json"]))
    current = ctx.current_renders()
    beats = [b["frame"] for b in ctx.beats()]
    read = ["film.json", ctx.REGISTRY, ctx.SHOTS, "timing/timeline.json",
            *[ctx.events_rel(a["index"]) for a in ctx.acts]]
    all_flags, missing, problems = [], [], []
    basis: dict[int, dict] = {}
    for a in ctx.acts:
        e = current.get(a["index"])
        if e is None or not e["path"].is_file():
            missing.append(a["index"])
            continue
        read.append(e["path"])
        basis[a["index"]] = ctx.inputs([e["path"]])
        try:
            flags = _scan_cached(ctx, a["index"], e)
        except SlateError as exc:
            problems.append(f"act {a['index']}: {exc}")
            continue
        for f in flags:
            all_flags.append({**f, "act": a["index"], "shot": ctx.shot_of_frame(f["frame"])})
    remaining, skipped = glitchmod.triage(all_flags, beats)
    items = [{"key": f"frame:{f['frame']}", "what": f"film frame {f['frame']} ({f['shot']}, act {f['act']}): "
              f"{f['len']}-frame glitch, score {f['score']}, tile x{f['tile'][0]} y{f['tile'][1]}",
              "basis": basis.get(f["act"], {}), "frame": f["frame"], "score": f["score"]}
             for f in sorted(remaining, key=lambda f: -f["score"])]
    open_items, accepted = review_items(ctx, "glitch", items)
    report = ctx.path("qa/glitch.json")
    jsonio.write_atomic(report, {"generated": "do not edit by hand: slate qa glitch", "flags": len(all_flags),
                                 "skipped_near_beats": skipped, "open": open_items, "accepted": accepted,
                                 "missing_acts": missing})
    counts = (f"{plural(len(all_flags), 'flag')}: {len(skipped)} within ±{glitchmod.WINDOW} frames of a planned beat "
              f"(skipped), {len(accepted)} accepted, {len(open_items)} open")
    subs = []
    if problems:
        subs.append(Sub("unreadable", "UNCHECKED", problems[0], problems))
    if missing:
        subs.append(Sub("missing", "UNCHECKED", f"acts {', '.join(map(str, missing))} have no whole-act render to scan",
                        ["next: slate render --act N"]))
    if open_items:
        subs.append(Sub("flags", "REVIEW", f"{plural(len(open_items), 'glitch flag')} to look at ({counts})",
                        [ctx.rel(report)] + [i["what"] for i in open_items[:40]]))
    else:
        subs.append(Sub("flags", "PASS", f"no unexplained glitches ({counts})", [ctx.rel(report)]))
    return combine("glitch", subs, engine=engine, inputs=ctx.inputs(read),
                   details={"review_items": open_items[:500], "accepted": accepted, "flags": len(all_flags),
                            "skipped": len(skipped), "report": ctx.rel(report)})


# ---------------------------------------------------------------------------------------------- boundary
def _mad(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a.astype(np.float32) - b.astype(np.float32)).mean())


def _strip(ctx: FilmContext, frames: list[np.ndarray], title: str, out: Path) -> Path:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    h, w = frames[0].shape[:2]
    tw = 320
    th = max(1, int(round(tw * h / w)))
    cols = max(1, len(frames) // 2)
    sheet = Image.new("RGB", (tw * cols, th * 2 + 24), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    d.text((6, 6), title, fill=(230, 230, 230))
    for i, f in enumerate(frames):
        sheet.paste(Image.fromarray(f).resize((tw, th)), ((i % cols) * tw, 24 + (i // cols) * th))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def boundary(film_dir: Path) -> GateResult:
    """Every join between consecutive act renders: contiguous, and no bigger a jump than the picture's own motion
    either side (+1.0). Runs for any film folder."""
    ctx = context(film_dir)
    engine = gates.engine("slate act-join check (mean absolute difference, 0–255 RGB)", __version__,
                          decoder=f"ffmpeg {media.version('ffmpeg')}", frames_each_side=JOIN_FRAMES, slack=JOIN_SLACK)
    if not ctx.render_entries():
        return _no_renders("boundary", ctx, engine)
    acts = ctx.acts
    read = ["film.json", ctx.REGISTRY, ctx.SHOTS]
    if len(acts) < 2:
        return combine("boundary", [Sub("joins", "PASS", "a single act: there are no joins between renders")],
                       engine=engine, inputs=ctx.inputs(read))
    if not media.have("ffmpeg"):
        return GateResult.unchecked("boundary", "ffmpeg isn't installed, so the joins can't be read", engine=engine,
                                    inputs=ctx.inputs(read))
    current = ctx.current_renders()
    subs, rows, items, gaps, unchecked = [], [], [], [], []
    for x, y in zip(acts, acts[1:]):
        name = f"act {x['index']} → act {y['index']}"
        ex, ey = current.get(x["index"]), current.get(y["index"])
        if ex is None or ey is None or not ex["path"].is_file() or not ey["path"].is_file():
            unchecked.append(f"{name}: " + ("both renders" if ex is None and ey is None else
                                            f"act {(x if ex is None else y)['index']}'s render") + " not there yet")
            continue
        read += [ex["path"], ey["path"]]
        if ex["frame_out"] != ey["frame_in"]:
            gaps.append(f"{name}: act {x['index']} ends at frame {ex['frame_out']}, act {y['index']} starts at "
                        f"{ey['frame_in']} ({'gap' if ey['frame_in'] > ex['frame_out'] else 'overlap'})")
            continue
        na = ex["frame_out"] - ex["frame_in"]
        n_left = min(JOIN_FRAMES, na)
        try:
            left = list(media.iter_frames(ex["path"], start=na - n_left, count=n_left, pix_fmt="rgb24"))
            right = list(media.iter_frames(ey["path"], start=0, count=JOIN_FRAMES, pix_fmt="rgb24"))
        except SlateError as exc:
            unchecked.append(f"{name}: {exc}")
            continue
        if len(left) < 2 or len(right) < 2:
            unchecked.append(f"{name}: too few frames decoded ({len(left)} + {len(right)})")
            continue
        seq = left + right
        steps = [_mad(seq[i], seq[i + 1]) for i in range(len(seq) - 1)]
        k = len(left) - 1
        jump = steps[k]
        inside = steps[:k] + steps[k + 1:]
        biggest = max(inside) if inside else 0.0
        strip = _strip(ctx, seq, f"{name} film frames {ex['frame_out'] - len(left)}–{ey['frame_in'] + len(right) - 1}: "
                       f"jump {jump:.2f}, largest step either side {biggest:.2f}",
                       ctx.path(f"qa/boundaries/act{x['index']}_act{y['index']}.jpg"))
        row = {"join": name, "film_frame": ey["frame_in"], "jump": round(jump, 3), "largest_step": round(biggest, 3),
               "steps": [round(s, 3) for s in steps], "strip": ctx.rel(strip)}
        rows.append(row)
        if jump > biggest + JOIN_SLACK:
            items.append({"key": f"join:{x['index']}-{y['index']}",
                          "what": f"{name} at film frame {ey['frame_in']}: jump {jump:.2f} vs largest step either side "
                                  f"{biggest:.2f} (strip {ctx.rel(strip)})",
                          "basis": ctx.inputs([ex["path"], ey["path"]])})
    open_items, accepted = review_items(ctx, "boundary", items)
    if gaps:
        subs.append(Sub("contiguous", "FAIL", gaps[0], gaps))
    if unchecked:
        subs.append(Sub("missing", "UNCHECKED", f"{plural(len(unchecked), 'join')} can't be checked yet: {unchecked[0]}",
                        unchecked))
    if open_items:
        subs.append(Sub("jumps", "REVIEW", f"{plural(len(open_items), 'join')} "
                        f"{'moves' if len(open_items) == 1 else 'move'} more than the picture around it",
                        [i["what"] for i in open_items]))
    if rows and not open_items:
        extra = f" ({plural(len(accepted), 'join')} accepted by a person)" if accepted else ""
        verb = "holds" if len(rows) == 1 else "hold"
        subs.append(Sub("jumps", "PASS", f"{plural(len(rows), 'join')} {verb}: no jump bigger than the motion either "
                        "side" + extra,
                        [f"{r['join']}: jump {r['jump']} vs {r['largest_step']} ({r['strip']})" for r in rows]))
    return combine("boundary", subs, engine=engine, inputs=ctx.inputs(read),
                   details={"joins": rows, "review_items": open_items, "accepted": accepted})


# ---------------------------------------------------------------------------------------------- psnr
def _primary_master(masters: list[Path]) -> tuple[Path, dict] | None:
    infos = []
    for m in masters:
        try:
            infos.append((m, media.video_info(m, count=False)))
        except SlateError:
            continue
    for m, info in infos:
        if str(info.get("codec") or "") in MEZZANINE:
            return m, info
    return infos[0] if infos else None


def psnr(film_dir: Path) -> GateResult:
    """The master against the act renders, frame for frame: at least 40 dB on every frame, and every frame there."""
    ctx = context(film_dir)
    threshold = float(ctx.targets.get("master_psnr_db") or PSNR_MIN_DB)
    engine = media.engine("psnr", pix_fmt="yuv422p10le", threshold_db=threshold)
    masters = ctx.masters()
    read = ["film.json", ctx.REGISTRY, ctx.SHOTS, *ctx.master_records()]
    if not masters:
        return GateResult.unchecked("psnr", "there's no master yet to compare with the renders",
                                    evidence=["next: slate master"], engine=engine, inputs=ctx.inputs(read))
    if not media.have("ffmpeg"):
        return GateResult.unchecked("psnr", "ffmpeg isn't installed, so the master can't be compared",
                                    engine=engine, inputs=ctx.inputs(read))
    current = ctx.current_renders()
    missing = [a["index"] for a in ctx.acts if a["index"] not in current or not current[a["index"]]["path"].is_file()]
    if missing:
        return GateResult.unchecked("psnr", f"acts {', '.join(map(str, missing))} have no whole-act render to compare "
                                    "the master with", engine=engine, inputs=ctx.inputs(read + masters))
    picked = _primary_master(masters)
    if picked is None:
        return GateResult.unchecked("psnr", "none of the masters could be read", engine=engine,
                                    inputs=ctx.inputs(read + masters))
    master, minfo = picked
    renders = [current[a["index"]]["path"] for a in ctx.acts]
    read += [master, *renders]
    size = ctx.size
    if size and (minfo.get("width"), minfo.get("height")) != size:
        return combine("psnr", [Sub("size", "FAIL", f"{ctx.rel(master)} is {minfo.get('width')}×{minfo.get('height')}; "
                                    f"the renders are {size[0]}×{size[1]}")], engine=engine, inputs=ctx.inputs(read))
    try:
        master_frames = media.video_info(master).get("frames")
        values = measure.video_psnr(master, renders)
    except SlateError as exc:
        return GateResult.unchecked("psnr", f"ffmpeg couldn't compare the master: {exc}", engine=engine,
                                    inputs=ctx.inputs(read))
    total = int(ctx.total_frames or 0)
    s = measure.summarize(values, threshold=threshold)
    out = ctx.path(f"qa/psnr_{master.stem}.json")
    jsonio.write_atomic(out, {"generated": "do not edit by hand: slate qa psnr", "master": ctx.rel(master),
                              "renders": [ctx.rel(r) for r in renders], "summary": s,
                              "per_frame_db": [measure.finite(v) for v in values]})
    line = (f"{ctx.rel(master)} vs {plural(len(renders), 'act render')}: {s['frames']} frames compared, mean "
            f"{s['mean_db']} dB, worst {s['min_db']} dB")
    subs = []
    if master_frames != total:
        subs.append(Sub("count", "FAIL", f"the master has {master_frames} frames, the film has {total}", [line]))
    elif s["frames"] != total:
        subs.append(Sub("count", "FAIL", f"{s['frames']} frames compared, the film has {total}", [line]))
    if s.get("below_count"):
        subs.append(Sub("psnr", "FAIL", f"{plural(s['below_count'], 'frame')} under {threshold:g} dB (worst "
                        f"{s['min_db']} dB at film frame {s['worst'][0]['frame']})",
                        [line, ctx.rel(out)] + [f"film frame {w['frame']}: {w['db']} dB" for w in s["below"][:20]]))
    if not subs:
        subs.append(Sub("psnr", "PASS", f"every frame of the master matches the renders (worst {s['min_db']} dB, "
                        f"mean {s['mean_db']} dB)", [line, ctx.rel(out)]))
    return combine("psnr", subs, engine=engine, inputs=ctx.inputs(read),
                   details={"master": ctx.rel(master), "summary": {k: v for k, v in s.items() if k != "below"},
                            "report": ctx.rel(out)})
