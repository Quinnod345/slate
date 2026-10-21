"""Picture gates on the act renders and the master.

- glitch    the one- and two-frame glitch scan on every act render, triaged: flags within ±3 frames of a planned
            beat are skipped (listed, not hidden), then each is measured (its ratio r, the share of it between its
            neighbours, a block match against each): motion with r ≥ 0.55 and fades that block matching explains
            are listed, not judged; what's left is REVIEW for a person, one item per frame. Runs of blank frames
            (one flat colour, the frames either side not) are REVIEW items too;
- boundary  the joins between act renders, for every film (the films' boundary check only ever read film A's
            renders). Each join must be contiguous (a gap or overlap FAILs), and the picture must move no more
            across it than it already does in the ten frames either side (+1.0 for encoder noise), else REVIEW;
- cuts      every scene change in an act render (ffmpeg's scene score over 0.33) sits within 3 frames of a shot
            start whose declared transition allows a cut (a carry, a portal, a mask, a motion-masked whip, a UI
            state cut with the camera locked, or at most three cuts on musical hits), and no shot join crossfades
            unless it's a declared whip or portal (or a seam blend of 4 frames or less); the rest is REVIEW;
- psnr      the master against the act renders it was built from, frame for frame: at least 40 dB on every frame
            and exactly the film's frame count;
- end_hold  the end card holds perfectly still: the master's last 3 s (the format or film.json may change that; a
            seamless loop has none) identical to its last frame, compared exactly.
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
SCAN_VERSION = 2  # 2: blank frames found in the same pass


def _scan_cached(ctx: FilmContext, act: int, entry: dict) -> dict:
    """The raw scan for one render ({flags, blanks}), cached in qa/glitch/ against the render's hash and the scan's
    settings. Older caches (flags only) are scanned again."""
    path = entry["path"]
    key = {"file": ctx.rel(path), "sha256": ctx.sha(path), "threshold": glitchmod.THRESHOLD,
           "size": list(media.analysis_size(*ctx.size, long_side=glitchmod.LONG_SIDE, tile=glitchmod.TILE)),
           "film_frame": entry["frame_in"], "version": __version__, "scan": SCAN_VERSION,
           "blank_spread": glitchmod.BLANK_SPREAD}
    cache = ctx.path(f"qa/glitch/act{act}.json")
    cached = jsonio.read(cache, default=None) if cache.is_file() else None
    if isinstance(cached, dict) and cached.get("key") == key:
        return {"flags": cached.get("flags") or [], "blanks": cached.get("blanks") or [],
                "explained": cached.get("explained") or {}, "cache": cache, "key": key}
    got = glitchmod.scan_render(path, film_size=ctx.size, first_film_frame=entry["frame_in"],
                                count=entry["frame_out"] - entry["frame_in"])
    jsonio.write_atomic(cache, {"generated": "do not edit by hand: slate qa glitch", "key": key, "flags": got["flags"],
                                "blanks": got["blanks"], "explained": {}})
    return {**got, "explained": {}, "cache": cache, "key": key}


def _flag_key(f: dict) -> str:
    return f"{f['local']}:{f['tile'][0]}:{f['tile'][1]}:{f.get('len', 1)}"


def _explain(ctx: FilmContext, act: int, entry: dict, scan: dict, flags: list[dict]) -> tuple[list[dict], str | None]:
    """Each flag left after the beat triage, categorized by slate.qa.stability (block matching against its neighbours,
    its ratio r and its between share), cached with the act's scan. Returns (flags with their measurements, a problem
    when the categorization couldn't run: then every flag stays open)."""
    if not flags:
        return [], None
    known: dict = dict(scan.get("explained") or {})
    todo = [f for f in flags if _flag_key(f) not in known]
    if todo:
        if ctx.quick:
            return [dict(f) for f in flags], "--quick doesn't start the block matching; uncategorized flags stay open"
        try:
            from slate.qa import stability  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001 - its own import error: the flags stay open, nothing is dropped
            return [dict(f) for f in flags], f"the stability module couldn't categorize them ({type(exc).__name__})"
        try:
            for g in stability.explain_glitches(entry["path"], todo):
                known[_flag_key(g)] = {k: g.get(k) for k in ("category", "ratio", "between", "shift_prev",
                                                              "shift_next", "explained_prev", "explained_next")}
            for f in todo:  # a flag on the render's first or last frame has no neighbour to match: it stays open
                known.setdefault(_flag_key(f), {"category": None})
        except (SlateError, OSError, ValueError) as exc:
            return [dict(f) for f in flags], f"block matching couldn't read act {act}'s render ({exc})"
        cached = jsonio.read(scan["cache"], default=None)
        if isinstance(cached, dict) and cached.get("key") == scan["key"]:
            cached["explained"] = known
            jsonio.write_atomic(scan["cache"], cached)
    return [{**f, **known.get(_flag_key(f), {})} for f in flags], None


def _measured(f: dict) -> str:
    if f.get("category") is None:
        return ""
    return (f"; r {f.get('ratio')}, between its neighbours on {round(100 * float(f.get('between') or 0))}% of changed "
            f"pixels, block match {round(100 * float(f.get('explained_prev') or 0))}% / "
            f"{round(100 * float(f.get('explained_next') or 0))}% ({f.get('category')})")


def glitch(film_dir: Path) -> GateResult:
    """One- and two-frame glitches in every act render, triaged against planned beats (±3 frames skipped), then by
    measurement (motion and fades explained), plus runs of blank frames."""
    ctx = context(film_dir)
    engine = gates.engine("slate glitch scan", __version__, decoder=f"ffmpeg {media.version('ffmpeg')}",
                          threshold=glitchmod.THRESHOLD, window_frames=glitchmod.WINDOW,
                          motion_ratio=glitchmod.MOTION_RATIO, blank_spread=glitchmod.BLANK_SPREAD)
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
    all_flags, all_blanks, missing, problems, notes = [], [], [], [], []
    basis: dict[int, dict] = {}
    scans: dict[int, tuple[dict, dict]] = {}
    for a in ctx.acts:
        e = current.get(a["index"])
        if e is None or not e["path"].is_file():
            missing.append(a["index"])
            continue
        read.append(e["path"])
        basis[a["index"]] = ctx.inputs([e["path"]])
        try:
            scan = _scan_cached(ctx, a["index"], e)
        except SlateError as exc:
            problems.append(f"act {a['index']}: {exc}")
            continue
        scans[a["index"]] = (e, scan)
        for f in scan["flags"]:
            all_flags.append({**f, "act": a["index"], "shot": ctx.shot_of_frame(f["frame"])})
        for b in scan["blanks"]:
            all_blanks.append({**b, "act": a["index"], "shot": ctx.shot_of_frame(b["frame"])})
    remaining, skipped = glitchmod.triage(all_flags, beats)
    # the measurement triage (L-qa-D048): motion and fades that block matching explains don't go to a person
    measured: list[dict] = []
    for act, (e, scan) in scans.items():
        mine = [f for f in remaining if f["act"] == act]
        got, problem = _explain(ctx, act, e, scan, mine)
        measured += got
        if problem:
            notes.append(problem)
    explained_flags = [{**f, "explained": glitchmod.explained(f)} for f in measured if glitchmod.explained(f)]
    open_flags = [f for f in measured if not glitchmod.explained(f)]
    items = [{"key": f"frame:{f['frame']}", "what": f"film frame {f['frame']} ({f['shot']}, act {f['act']}): "
              f"{f['len']}-frame glitch, score {f['score']}, tile x{f['tile'][0]} y{f['tile'][1]}{_measured(f)}",
              "basis": basis.get(f["act"], {}), "frame": f["frame"], "len": f["len"], "score": f["score"],
              **({"ratio": f.get("ratio"), "between": f.get("between"), "category": f.get("category")}
                 if f.get("category") else {})}
             for f in sorted(open_flags, key=lambda f: -f["score"])]
    blank_open, blank_skipped = glitchmod.triage(all_blanks, beats)
    flagged = {f["frame"] + k: i for i, f in enumerate(items) for k in range(int(f.get("len", 1)))}
    for b in blank_open:
        frames_b = range(b["frame"], b["frame"] + b["len"])
        same = next((flagged[fr] for fr in frames_b if fr in flagged), None)
        if same is not None:  # one frame is judged once: the glitch item says it's blank too
            items[same]["what"] += f"; the whole picture is one flat colour ({plural(b['len'], 'blank frame')})"
            items[same]["blank"] = True
            continue
        items.append({"key": f"blank:{b['frame']}", "what": f"film frame {b['frame']} ({b['shot']}, act {b['act']}): "
                      f"{plural(b['len'], 'blank frame')} (one flat colour across the picture) between frames that "
                      "aren't", "basis": basis.get(b["act"], {}), "frame": b["frame"], "blank": True})
    open_items, accepted = review_items(ctx, "glitch", items)
    by_cat = {c: sum(1 for f in explained_flags if f["explained"] == c) for c in ("motion", "fade")}
    report = ctx.path("qa/glitch.json")
    jsonio.write_atomic(report, {"generated": "do not edit by hand: slate qa glitch", "flags": len(all_flags),
                                 "skipped_near_beats": skipped, "explained": explained_flags,
                                 "explained_by": dict(by_cat), "open": open_items, "accepted": accepted,
                                 "blanks": {"found": len(all_blanks), "skipped_near_beats": blank_skipped},
                                 "missing_acts": missing, "notes": notes})
    counts = (f"{plural(len(all_flags), 'flag')}: {len(skipped)} within ±{glitchmod.WINDOW} frames of a planned beat "
              f"(skipped), {by_cat['motion']} motion and {by_cat['fade']} {'fade' if by_cat['fade'] == 1 else 'fades'} "
              f"explained by block matching, {len(accepted)} accepted, {len(open_items)} open")
    if all_blanks:
        counts += f"; {plural(len(all_blanks), 'blank run')} ({len(blank_skipped)} on planned beats)"
    subs = []
    if problems:
        subs.append(Sub("unreadable", "UNCHECKED", problems[0], problems))
    if missing:
        subs.append(Sub("missing", "UNCHECKED", f"acts {', '.join(map(str, missing))} have no whole-act render to scan",
                        ["next: slate render --act N"]))
    if open_items:
        subs.append(Sub("flags", "REVIEW", f"{plural(len(open_items), 'glitch flag')} to look at ({counts})",
                        [ctx.rel(report)] + [i["what"] for i in open_items[:40]] + notes[:3]))
    else:
        subs.append(Sub("flags", "PASS", f"no unexplained glitches ({counts})", [ctx.rel(report)]))
    return combine("glitch", subs, engine=engine, inputs=ctx.inputs(read),
                   details={"review_items": open_items[:500], "accepted": accepted, "flags": len(all_flags),
                            "skipped": len(skipped), "explained": dict(by_cat), "blanks": len(all_blanks),
                            "report": ctx.rel(report), "notes": notes})


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


# ---------------------------------------------------------------------------------------------------------------- cuts
CUT_SCORE = 0.33  # ffmpeg's scene score over this is a scene change (L-qa-013; analyze.cuts reads reference films at 0.4)
CUT_WINDOW = 3  # a change counts as a shot's own cut within this many frames of the shot's start
MUSIC_CUTS_MAX = 3  # at most three cuts on musical hits
FADE_REACH = 12  # frames either side of a join searched for a crossfade
FADE_RESIDUAL = 0.25  # each blended frame lies this close (of the two pictures' difference) to a mix of them
FADE_CHANGED = 0.4  # the two pictures differ on at least this share of the frame (two shots, not a title fading in)
SEAM_MAX = 4  # a seam blend inside one generated shot is at most this many frames
CUTS_VERSION = 1
# A shot's `transition` (how it hands to the next), sorted into what the law allows. A scene change must sit on a
# carry, a portal, a mask, a motion-masked whip, a UI state cut with the camera locked, or (at most three) a cut on a
# musical hit; a crossfade only on a whip or a portal, or a seam blend of 4 frames or less inside one generated shot.
TRANSITIONS = {"carry": ("carry", "carried", "push", "pull"), "portal": ("portal",), "mask": ("mask", "matte", "wipe"),
               "whip": ("whip",), "ui_cut": ("ui", "state"), "music_cut": ("hit", "music", "beat"),
               "seam": ("seam",)}
CUT_ALLOWED = ("carry", "portal", "mask", "whip", "ui_cut", "music_cut")
CROSSFADE_ALLOWED = ("whip", "portal")


def transition_kind(text) -> str | None:
    """A declared transition, in the law's words: carry, portal, mask, whip, ui_cut, music_cut or seam; None for
    nothing declared, and "other" for anything the law doesn't allow (a hard cut, a crossfade, a dissolve). A script
    may name the kind itself ({kind, note}); words are sorted into the kinds."""
    if isinstance(text, dict):
        kind = str(text.get("kind") or "").strip().lower()
        return (kind if kind in TRANSITIONS else "other") if kind else None
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.strip().lower().replace("-", " ").replace("_", " ")
    if any(w in t for w in ("crossfade", "cross fade", "dissolve", "fade")) and "whip" not in t:
        return "other"
    for kind in ("portal", "whip", "seam", "mask", "carry"):  # a "motion-masked whip" is a whip
        if any(w in t for w in TRANSITIONS[kind]):
            return kind
    if "cut" in t:
        if any(w in t for w in TRANSITIONS["music_cut"]):
            return "music_cut"
        if any(w in t for w in TRANSITIONS["ui_cut"]):
            return "ui_cut"
    return "other"


def _said(declared) -> str:
    """A declared transition in words, for a message: its own words, or "<kind>: <note>"."""
    if isinstance(declared, dict):
        kind, note = str(declared.get("kind") or ""), str(declared.get("note") or "")
        return f"{kind}: {note}" if kind and note else (kind or note)
    return str(declared)


def _transitions(ctx: FilmContext) -> dict[str, str | dict | None]:
    """{shot id: its declared transition}: timing/shots.json, else the storyboard, else script/script.json."""
    from slate.qa.checks.text import script_shots  # noqa: PLC0415
    from slate.qa.film import iter_storyboard_shots  # noqa: PLC0415

    out: dict[str, str | None] = {}
    for sid, shot in ((str(s.get("id")), s) for s in ctx.shots):
        declared = shot.get("transition") or shot.get("transition_out")  # shots.json names it transition_out
        if declared:
            out[sid] = declared
    for shot in iter_storyboard_shots(ctx.storyboard or {}):
        sid = str(shot.get("id"))
        if sid not in out and shot.get("transition"):
            out[sid] = shot["transition"]
    for _where, shot in script_shots(ctx.script or {}):
        sid = str(shot.get("id"))
        if sid not in out and shot.get("transition"):
            out[sid] = shot["transition"]
    return out


def _scene_peaks(ctx: FilmContext, act: int, entry: dict) -> list[dict]:
    """The act render's scene changes (ffmpeg's scene score), cached in qa/cuts/ against the render's hash."""
    import tempfile  # noqa: PLC0415

    from slate.analyze import cuts as acuts  # noqa: PLC0415

    path = entry["path"]
    key = {"file": ctx.rel(path), "sha256": ctx.sha(path), "score": CUT_SCORE, "version": CUTS_VERSION,
           "width": acuts.SCORE_WIDTH}
    cache = ctx.path(f"qa/cuts/act{act}.json")
    cached = jsonio.read(cache, default=None) if cache.is_file() else None
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached.get("peaks") or []
    with tempfile.TemporaryDirectory(prefix="slate-cuts-") as tmp:
        scores = acuts.scene_scores(path, Path(tmp))
    fps = float(ctx.fps) if ctx.fps else 25.0
    peaks = [{"local": int(p["frame"]), "frame": int(entry["frame_in"]) + int(p["frame"]),
              "score": round(float(p["score"]), 4)} for p in acuts.peaks(scores, CUT_SCORE, merge_s=2.5 / fps)]
    jsonio.write_atomic(cache, {"generated": "do not edit by hand: slate qa cuts", "key": key, "peaks": peaks})
    return peaks


def find_crossfade(frames: dict[int, np.ndarray], join: int, *, reach: int = FADE_REACH) -> dict | None:
    """Frames around a join that blend two pictures: {start, end, blended, residual}, or None. The two pictures are
    frames `start` and `end`; every frame between them is close to a mix of the two, mixing further each frame."""
    best = None
    for s in range(join - reach, join + 1):
        if s not in frames:
            continue
        a = frames[s].astype(np.float32)
        for e in range(join + 1, join + reach + 1):
            if e not in frames or e - s < 2 or any(i not in frames for i in range(s + 1, e)):
                continue
            b = frames[e].astype(np.float32)
            diff = b - a
            big = np.abs(diff) > 12.0
            d = float(np.abs(diff).mean())
            if d < 6.0 or float(big.mean()) < FADE_CHANGED:
                continue
            denom = float((diff * diff).sum())
            alphas, worst = [], 0.0
            for i in range(s + 1, e):
                f = frames[i].astype(np.float32) - a
                al = float((f * diff).sum()) / denom
                worst = max(worst, float(np.abs(f - al * diff).mean()) / d)
                alphas.append(al)
            rising = all(y > x + 0.02 for x, y in zip(alphas, alphas[1:]))
            if worst <= FADE_RESIDUAL and rising and 0.02 < alphas[0] and alphas[-1] < 0.98:
                cand = {"start": s, "end": e, "blended": e - s - 1, "residual": round(worst, 3)}
                if best is None or cand["blended"] > best["blended"]:
                    best = cand
    return best


def cuts(film_dir: Path) -> GateResult:
    """Every scene change in every act render sits on a shot start whose declared transition allows a cut, and no
    shot join crossfades unless it's a declared whip or portal."""
    ctx = context(film_dir)
    engine = gates.engine("slate cuts (ffmpeg scene score, crossfade fit)", __version__,
                          decoder=f"ffmpeg {media.version('ffmpeg')}", cut_score=CUT_SCORE, window_frames=CUT_WINDOW)
    if not ctx.render_entries():
        return _no_renders("cuts", ctx, engine)
    if not media.have("ffmpeg") or ctx.size is None or ctx.clock is None:
        why = "ffmpeg isn't installed" if not media.have("ffmpeg") else "film.json has no frame size or rate"
        return GateResult.unchecked("cuts", f"{why}, so the renders can't be read", engine=engine,
                                    inputs=ctx.inputs([ctx.REGISTRY, "film.json"]))
    current = ctx.current_renders()
    trans = _transitions(ctx)
    read: list = ["film.json", ctx.REGISTRY, ctx.SHOTS, "script/storyboard.json", "script/script.json"]
    shots = ctx.shots
    starts = {int(s["frame_in"]): (shots[i - 1] if i else None, s) for i, s in enumerate(shots) if "frame_in" in s}
    missing, problems, items, listed = [], [], [], []
    music = 0
    size = media.analysis_size(*ctx.size, long_side=160, tile=10)
    for a in ctx.acts:
        e = current.get(a["index"])
        if e is None or not e["path"].is_file():
            missing.append(a["index"])
            continue
        read.append(e["path"])
        basis = ctx.inputs([e["path"]])
        try:
            peaks = _scene_peaks(ctx, a["index"], e)
        except (SlateError, OSError) as exc:
            problems.append(f"act {a['index']}: {exc}")
            continue
        for p in peaks:
            near = [f for f in starts if abs(f - p["frame"]) <= CUT_WINDOW and a["frame_in"] < f < a["frame_out"]]
            if not near:
                sid = ctx.shot_of_frame(p["frame"])
                into = p["frame"] - int(ctx.shot_by_id.get(sid, {}).get("frame_in", p["frame"]))
                items.append({"key": f"cut:{p['frame']}", "basis": basis, "frame": p["frame"],
                              "what": f"film frame {p['frame']} (act {a['index']}): a scene change (score "
                                      f"{p['score']}) {into} frames into {sid}, not on a shot start"})
                continue
            f = min(near, key=lambda x: abs(x - p["frame"]))
            out_shot, in_shot = starts[f]
            declared = trans.get(str(out_shot.get("id"))) if out_shot else None
            kind = transition_kind(declared)
            join = f"{out_shot.get('id') if out_shot else '?'}→{in_shot.get('id')}"
            if kind == "music_cut":
                music += 1
            if kind in CUT_ALLOWED and not (kind == "music_cut" and music > MUSIC_CUTS_MAX):
                listed.append(f"film frame {p['frame']}: {join} ({_said(declared)})")
                continue
            why = (f"its declared transition ({_said(declared)!r}) doesn't allow a cut" if declared else
                   "no transition is declared for it")
            if kind == "music_cut":
                why = f"it's music cut {music}, past the {MUSIC_CUTS_MAX} the law allows"
            items.append({"key": f"cut:{p['frame']}", "basis": basis, "frame": p["frame"],
                          "what": f"film frame {p['frame']} (act {a['index']}): a hard cut (score {p['score']}) at "
                                  f"the join {join}: {why}"})
        # joins: a crossfade across a shot start inside this render
        joins = [f for f in starts if a["frame_in"] < f < a["frame_out"]]
        if joins:
            want = {j + k - int(e["frame_in"]) for j in joins for k in range(-FADE_REACH, FADE_REACH + 1)}
            want = {i for i in want if 0 <= i < int(e["frame_out"] - e["frame_in"])}
            try:
                got = media.read_frames(e["path"], want, size=size, pix_fmt="gray")
            except SlateError as exc:
                problems.append(f"act {a['index']}: {exc}")
                continue
            for j in joins:
                local = {i + int(e["frame_in"]): arr for i, arr in got.items()}
                fade = find_crossfade(local, j)
                if fade is None:
                    continue
                out_shot, in_shot = starts[j]
                declared = trans.get(str(out_shot.get("id"))) if out_shot else None
                kind = transition_kind(declared)
                join = f"{out_shot.get('id') if out_shot else '?'}→{in_shot.get('id')}"
                if kind in CROSSFADE_ALLOWED or (kind == "seam" and fade["blended"] <= SEAM_MAX):
                    listed.append(f"film frames {fade['start']}–{fade['end']}: a {fade['blended']}-frame blend at "
                                  f"{join} ({declared})")
                    continue
                items.append({"key": f"crossfade:{j}", "basis": basis, "frame": j,
                              "what": f"film frames {fade['start']}–{fade['end']} (act {a['index']}): a "
                                      f"{fade['blended']}-frame crossfade at the join {join}"
                                      + (f" (declared {_said(declared)!r}, which isn't a whip or a portal)" if declared else
                                         " (no transition declared)")})
    open_items, accepted = review_items(ctx, "cuts", items)
    subs = []
    if problems:
        subs.append(Sub("unreadable", "UNCHECKED", problems[0], problems))
    if missing:
        subs.append(Sub("missing", "UNCHECKED", f"acts {', '.join(map(str, missing))} have no whole-act render to read",
                        ["next: slate render --act N"]))
    cut_items = [i for i in open_items if i["key"].startswith("cut:")]
    fade_items = [i for i in open_items if i["key"].startswith("crossfade:")]
    if cut_items:
        subs.append(Sub("scene_changes", "REVIEW", f"{plural(len(cut_items), 'scene change')} not on a shot start "
                        "whose transition allows a cut", [i["what"] for i in cut_items[:40]]
                        + ["declare the join's transition in script.json (carry, portal, mask, whip, ui cut, music "
                           "cut), or make it continuous"]))
    if fade_items:
        subs.append(Sub("joins", "REVIEW", f"{plural(len(fade_items), 'shot join')} crossfading (they double-expose "
                        "type and hide motion)", [i["what"] for i in fade_items[:40]]))
    if not cut_items and not fade_items and not problems and len(missing) < len(ctx.acts):
        extra = f", {plural(len(accepted), 'item')} accepted by a person" if accepted else ""
        subs.append(Sub("scene_changes", "PASS", f"every scene change sits on a declared transition "
                        f"({plural(len(listed), 'declared join')}{extra})", listed[:40]))
    return combine("cuts", subs, engine=engine, inputs=ctx.inputs(read),
                   details={"review_items": open_items[:500], "accepted": accepted, "declared": listed,
                            "music_cuts": music, "stage": "render"})


# ---------------------------------------------------------------------------------------------------------- end hold
#: After Effects dithers a 16-bpc project into the 10-bit master with a new pattern every frame (measured on the films'
#: masters: at most 5 to 7 levels of 255, a mean near 0.19), so an AE master's still frames never compare exactly. Any
#: real move (a one-pixel drift of type) changes edges by tens to hundreds of levels.
DITHER_MAX = 8
DITHER_MEAN = 0.5


def _exact_hold(frames: list) -> int:
    """How many of the frames, counted back from the last, are identical to the last."""
    hold = 0
    for f in reversed(frames):
        if not np.array_equal(f, frames[-1]):
            break
        hold += 1
    return hold


def _dither_hold(frames: list) -> int:
    """How many frames, counted back from the last, differ from it only by an output dither."""
    last, hold = frames[-1].astype(np.int16), 0
    for f in reversed(frames):
        d = np.abs(f.astype(np.int16) - last)
        if int(d.max()) > DITHER_MAX or float(d.mean()) > DITHER_MEAN:
            break
        hold += 1
    return hold


def _master_method(ctx) -> str:
    """How the master was made (masters/master.json's "method": "ae" for an After Effects finish)."""
    rec = ctx.read_json("masters/master.json", default={})
    return str(rec.get("method") or "") if isinstance(rec, dict) else ""


def end_hold(film_dir: Path) -> GateResult:
    """The end card holds perfectly still: every frame of the master's last seconds (3 by default; the format or
    film.json may say otherwise, and a seamless loop has none) identical to the last, compared exactly on the
    mezzanine master or the last act's render."""
    from fractions import Fraction  # noqa: PLC0415

    from slate import formats  # noqa: PLC0415

    ctx = context(film_dir)
    need, source = formats.end_hold_for(ctx.format(), ctx.film)
    engine = media.engine("end hold (frames compared exactly)", hold_s=need, source=source)
    read: list = ["film.json", ctx.REGISTRY, *ctx.master_records()]
    if need <= 0:
        return combine("end_hold", [Sub("hold", "PASS", f"no end hold is asked ({source})")], engine=engine,
                       inputs=ctx.inputs(read), details={"stage": "master"})
    masters = ctx.masters()
    picked = _primary_master(masters) if masters else None
    target = None
    if picked and str(picked[1].get("codec") or "") in MEZZANINE:
        target = picked[0]
    if target is None:
        current = ctx.current_renders()
        last = current.get(ctx.acts[-1]["index"]) if ctx.acts else None
        if last is not None and last["path"].is_file() and int(last["frame_out"]) == int(ctx.total_frames or -1):
            target = last["path"]
    if target is None:
        return GateResult.unchecked("end_hold", "there's no lossless or ProRes master (or last act render) to compare "
                                    "the end on", evidence=["next: slate master"], engine=engine,
                                    inputs=ctx.inputs(read + masters), details={"stage": "master"})
    if not media.have("ffmpeg"):
        return GateResult.unchecked("end_hold", "ffmpeg isn't installed, so the end can't be read", engine=engine,
                                    inputs=ctx.inputs(read + [target]), details={"stage": "master"})
    read.append(target)
    try:
        info = media.video_info(target)
    except SlateError as exc:
        return GateResult.unchecked("end_hold", f"{ctx.rel(target)} can't be read: {exc}", engine=engine,
                                    inputs=ctx.inputs(read), details={"stage": "master"})
    fps = Fraction(info["fps_num"], info["fps_den"]) if info.get("fps_num") else ctx.fps
    total = int(info.get("frames") or 0)
    n_need = int(round(need * float(fps)))
    window = min(total, n_need + int(round(float(fps))))
    if total < n_need or window < 2:
        return combine("end_hold", [Sub("hold", "FAIL", f"{ctx.rel(target)} is {total} frames, shorter than the "
                                        f"{need:g} s hold")], engine=engine, inputs=ctx.inputs(read),
                       details={"stage": "master"})
    try:
        frames = list(media.iter_frames(target, start=total - window, count=window, pix_fmt="rgb24", info=info))
    except SlateError as exc:
        return GateResult.unchecked("end_hold", f"the end of {ctx.rel(target)} couldn't be decoded: {exc}",
                                    engine=engine, inputs=ctx.inputs(read), details={"stage": "master"})
    hold = _exact_hold(frames)
    last = (ctx.current_renders() or {}).get(ctx.acts[-1]["index"]) if ctx.acts else None
    on_master = last is None or target != last["path"]
    if hold < n_need and on_master and _master_method(ctx) == "ae":
        # An After Effects master: its frames carry AE's output dither, so the end is proven on two counts. The last
        # act's render (the picture AE was given) holds exactly still, and the master differs from its last frame
        # only by that dither, never by a move.
        dither = _dither_hold(frames)
        render_ok = False
        if last is not None and last["path"].is_file() and int(last["frame_out"]) == int(ctx.total_frames or -1):
            try:
                rinfo = media.video_info(last["path"])
                rtotal = int(rinfo.get("frames") or 0)
                rwin = min(rtotal, n_need + int(round(float(fps))))
                rframes = list(media.iter_frames(last["path"], start=rtotal - rwin, count=rwin, pix_fmt="rgb24",
                                                 info=rinfo))
                render_ok = _exact_hold(rframes) >= n_need
                read.append(last["path"])
            except SlateError:
                render_ok = False
        if dither >= n_need and render_ok:
            held = dither / float(fps)
            line = (f"{ctx.rel(target)}: the last {held:.2f} s ({dither} frames) differ from the last frame only by "
                    f"After Effects' output dither (at most {DITHER_MAX} levels, mean under {DITHER_MEAN}); "
                    f"{ctx.rel(last['path'])} holds pixel-identical; {need:g} s needed ({source})")
            return combine("end_hold", [Sub("hold", "PASS", f"the end holds still for {held:.2f} s ({need:g} s needed; "
                                            "the AE master within its dither, the render exactly)", [line])],
                           engine=engine, inputs=ctx.inputs(read),
                           details={"stage": "master", "file": ctx.rel(target), "hold_frames": dither,
                                    "hold_s": round(held, 3), "needed_s": need, "exact_frames": hold,
                                    "dither_max": DITHER_MAX, "dither_mean": DITHER_MEAN,
                                    "render": ctx.rel(last["path"])})
    first_still = total - hold
    held_s = hold / float(fps)
    whole = hold == len(frames)
    line = (f"{ctx.rel(target)}: the last {'at least ' if whole else ''}{held_s:.2f} s ({hold} frames) are "
            f"identical to the last frame; {need:g} s needed ({source})")
    details = {"stage": "master", "file": ctx.rel(target), "hold_frames": hold, "hold_s": round(held_s, 3),
               "needed_s": need, "first_still_frame": first_still}
    if hold >= n_need:
        return combine("end_hold", [Sub("hold", "PASS", f"the end holds still for {'at least ' if whole else ''}"
                                        f"{held_s:.2f} s ({need:g} s needed)", [line])], engine=engine,
                       inputs=ctx.inputs(read), details=details)
    moving = total - hold - 1
    return combine("end_hold", [Sub("hold", "FAIL", f"the end card moves at film frame {moving}, "
                                    f"{(total - moving) / float(fps):.2f} s before the end: the last {need:g} s must be "
                                    "pixel-identical", [line])], engine=engine, inputs=ctx.inputs(read),
                   details={**details, "first_moving_frame": moving})
