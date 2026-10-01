"""Structure gates: every film file matches its schema, every frame count is exact, every act has its beats.

- schema          each film data file validates against its schema (src/slate/schemas/<name>.schema.json);
- frames          shots.json is contiguous and on the film's one clock; every act render and master has exactly the
                  frames, size and rate it should; the mix is as long as the picture, to within a frame;
- events_complete every act in timing/shots.json has a valid timing/visual_events/actN.json, or says
                  {"silent": true, "reason": "…"}. Version C of the films shipped with acts 1 and 2 missing their
                  beats while its sync check passed, because sync only measured the beats that existed.
"""

from __future__ import annotations

import fnmatch
import json
from fractions import Fraction
from pathlib import Path

from slate import gates, jsonio, paths
from slate.errors import SlateError
from slate.gates import GateResult
from slate.qa import media, wav
from slate.qa.checks import Sub, combine, plural, slate_engine
from slate.qa.film import FilmContext, context

# Film data files and the schema names they may use (the first that exists wins). A schema can also claim files
# itself with "x-slate-files": ["timing/shots.json", …] (globs relative to the film folder).
SCHEMA_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("film.json", ("film",)),
    ("film.lock.json", ("film.lock", "film_lock", "lock")),
    ("script/script.json", ("script",)),
    ("script/storyboard.json", ("storyboard",)),
    ("timing/shots.json", ("shots",)),
    ("timing/vo_cues.json", ("vo_cues",)),
    ("timing/vo_words.json", ("vo_words",)),
    ("timing/timeline.json", ("timeline",)),
    ("timing/events.json", ("events",)),
    ("timing/visual_events/act*.json", ("visual_events", "visual_events_act")),
    ("timing/motion/act*.json", ("motion_trace", "motion")),
    ("renders/registry.json", ("renders_registry", "render_registry", "registry")),
    ("timing/captions.json", ("captions",)),
    ("brief/truth.json", ("truth",)),
    ("approvals.json", ("approvals",)),
    ("sound/sound_cues.json", ("sound_cues",)),
    ("sound/mix/final_mix.json", ("mix",)),
    ("sound/score/plan.json", ("score",)),
    ("sound/score/score.json", ("score",)),
    ("sound/score/takes/*.json", ("score",)),  # a take's record, and the composition plan it was composed from
    ("sound/score/plans/*.json", ("score",)),
    ("voice/takes/*/*.json", ("voice_take",)),
    ("plates/*/generation.json", ("generation",)),
    ("plates/*/plate/plate.json", ("plate",)),
    ("captures/*/capture.json", ("capture",)),
    ("qa/gates.json", ("gates",)),
)


# Bookkeeping files the stages write as they go: the schema gate validates them as they are, but doesn't record them
# as its inputs, or it would go stale the moment the next command recorded its work (qa/gates.json is the very report
# this result is written into, and every stage adds to film.lock.json).
BOOKKEEPING = ("qa/gates.json", "film.lock.json", "approvals.json")


# ---------------------------------------------------------------------------------------------- schema
def _declared_schemas() -> list[tuple[str, str]]:
    """(glob, schema name) pairs from schemas that claim their files with x-slate-files."""
    out = []
    d = paths.SCHEMAS_DIR
    for p in sorted(d.glob("*.schema.json")) if d.is_dir() else []:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for pattern in doc.get("x-slate-files") or []:
            out.append((str(pattern), p.name[: -len(".schema.json")]))
    return out


def _schema_for(rel: str, declared: list[tuple[str, str]]) -> str | None:
    for pattern, name in declared:
        if fnmatch.fnmatch(rel, pattern):
            return name
    for pattern, names in SCHEMA_MAP:
        if fnmatch.fnmatch(rel, pattern):
            for name in names:
                if (paths.SCHEMAS_DIR / f"{name}.schema.json").is_file():
                    return name
            return None
    return None


def _film_files(ctx: FilmContext, declared: list[tuple[str, str]]) -> list[str]:
    patterns = [p for p, _ in declared] + [p for p, _ in SCHEMA_MAP]
    found = set()
    for pattern in patterns:
        for p in ctx.dir.glob(pattern):
            if p.is_file() and not p.name.startswith("."):
                found.add(ctx.rel(p))
    return sorted(found)


def schema(film_dir: Path) -> GateResult:
    """Every film data file validates against its schema; files with no schema yet are UNCHECKED, not passed."""
    ctx = context(film_dir)
    try:
        from importlib import metadata  # noqa: PLC0415

        version = metadata.version("jsonschema")
    except Exception:  # noqa: BLE001
        version = "unknown"
    engine = gates.engine("jsonschema", version)
    declared = _declared_schemas()
    files = _film_files(ctx, declared)
    valid, invalid, unschemaed, broken, used = [], [], [], [], set()
    for rel in files:
        name = _schema_for(rel, declared)
        if name is None:
            unschemaed.append(rel)
            continue
        used.add(name)
        try:
            data = jsonio.read(ctx.path(rel))
        except SlateError as exc:
            invalid.append({"file": rel, "schema": name, "problems": [str(exc)]})
            continue
        try:
            problems = jsonio.validate(data, name)
        except Exception as exc:  # noqa: BLE001 - a broken schema can't validate anything: unchecked, not raised
            broken.append({"file": rel, "schema": name, "problem": f"{type(exc).__name__}: {exc}"})
            continue
        (invalid if problems else valid).append({"file": rel, "schema": name, "problems": problems[:20]})
    subs: list[Sub] = []
    if broken:
        subs.append(Sub("broken_schema", "UNCHECKED", f"{plural(len(broken), 'schema')} couldn't be used: "
                        + ", ".join(sorted({b['schema'] for b in broken})),
                        [f"{b['file']} ({b['schema']}): {b['problem']}" for b in broken]))
    if invalid:
        one = len(invalid) == 1
        subs.append(Sub("invalid", "FAIL", f"{plural(len(invalid), 'file')} {'doesn' if one else 'don'}'t match "
                        f"{'its' if one else 'their'} schema: " + ", ".join(i["file"] for i in invalid[:8]),
                        [f"{i['file']} ({i['schema']}): {p}" for i in invalid for p in i["problems"][:5]]))
    if unschemaed:
        one = len(unschemaed) == 1
        subs.append(Sub("no_schema", "UNCHECKED", f"{plural(len(unschemaed), 'file')} {'has' if one else 'have'} no "
                        f"schema yet, so {'it' if one else 'they'} can't be validated: " + ", ".join(unschemaed[:8]),
                        [f"no schema: {u}" for u in unschemaed]))
    if valid and not invalid and not unschemaed:
        subs.append(Sub("valid", "PASS", "the one film file matches its schema" if len(valid) == 1 else
                        f"all {len(valid)} film files match their schemas",
                        [f"{v['file']} ✓ {v['schema']}" for v in valid]))
    elif valid:
        matches = "matches its schema" if len(valid) == 1 else "match their schemas"
        subs.append(Sub("valid", "PASS", f"{plural(len(valid), 'file')} {matches}",
                        [f"{v['file']} ✓ {v['schema']}" for v in valid]))
    schema_files = [paths.SCHEMAS_DIR / f"{n}.schema.json" for n in sorted(used)]
    recorded = [f for f in files if f not in BOOKKEEPING]
    return combine("schema", subs, engine=engine, inputs=ctx.inputs([*recorded, *schema_files]),
                   details={"valid": valid, "invalid": invalid, "no_schema": unschemaed},
                   empty_summary="no film data files found to validate")


# ---------------------------------------------------------------------------------------------- frames
def _clock_sub(ctx: FilmContext) -> Sub | None:
    film, doc = ctx.film, ctx.shots_doc or {}
    problems = []
    if not (film.get("fps_num") and film.get("fps_den")):
        problems.append("film.json has no fps_num / fps_den")
    if not (film.get("width") and film.get("height")):
        problems.append("film.json has no width / height")
    for key in ("fps_num", "fps_den", "width", "height"):
        if key in doc and key in film and int(doc[key]) != int(film[key]):
            problems.append(f"shots.json {key} = {doc[key]}, film.json says {film[key]} (one clock, one size)")
    if problems:
        return Sub("clock", "FAIL", problems[0], problems)
    return None


def _shots_sub(ctx: FilmContext) -> Sub:
    shots, total, clk = ctx.shots, ctx.total_frames, ctx.clock
    problems = []
    if not shots:
        return Sub("shots", "FAIL", "timing/shots.json lists no shots")
    expect = 0
    for s in shots:
        sid, fin, fout = s.get("id"), s.get("frame_in"), s.get("frame_out")
        if fin is None or fout is None:
            problems.append(f"{sid}: no frame_in / frame_out")
            continue
        if fin != expect:
            problems.append(f"{sid} starts at frame {fin}, expected {expect} ({'gap' if fin > expect else 'overlap'})")
        if fout <= fin:
            problems.append(f"{sid} is {fout - fin} frames long")
        if s.get("frames") is not None and int(s["frames"]) != fout - fin:
            problems.append(f"{sid}: frames = {s['frames']} but frame_out − frame_in = {fout - fin}")
        if clk is not None and s.get("sec_in") is not None and abs(float(s["sec_in"]) - clk.secs_of(fin)) > 1e-4:
            problems.append(f"{sid}: sec_in {s['sec_in']} isn't frame {fin} on the film clock ({clk.secs_of(fin):.6f})")
        expect = fout
    if total is not None and expect != total:
        problems.append(f"the shots end at frame {expect} but total_frames is {total}")
    for a in ctx.acts:
        members = [ctx.shot_by_id[i] for i in a["shots"] if i in ctx.shot_by_id]
        if len(members) != len(a["shots"]):
            problems.append(f"act {a['index']} names shots that aren't in shots.json")
        frames = sorted((int(m["frame_in"]), int(m["frame_out"])) for m in members)
        for (a_in, a_out), (b_in, _b_out) in zip(frames, frames[1:]):
            if a_out != b_in:
                problems.append(f"act {a['index']}'s shots aren't contiguous (frame {a_out} → {b_in})")
    acts = ctx.acts
    for x, y in zip(acts, acts[1:]):
        if x["frame_out"] != y["frame_in"]:
            problems.append(f"act {x['index']} ends at {x['frame_out']} but act {y['index']} starts at {y['frame_in']}")
    if problems:
        return Sub("shots", "FAIL", f"shots.json isn't frame-exact: {problems[0]}", problems[:40])
    return Sub("shots", "PASS", f"{plural(len(shots), 'shot')} in {plural(len(acts), 'act')}, contiguous from 0 to "
               f"{total} frames")


def _media_problems(label: str, info: dict, want_frames: int, size: tuple[int, int] | None,
                    fps: Fraction | None) -> list[str]:
    problems = []
    if info.get("frames") != want_frames:
        problems.append(f"{label} has {info.get('frames')} frames, should have {want_frames}")
    if size and (info.get("width"), info.get("height")) != size:
        problems.append(f"{label} is {info.get('width')}×{info.get('height')}, the film is {size[0]}×{size[1]}")
    if fps and info.get("fps_num") and Fraction(info["fps_num"], info["fps_den"]) != fps:
        problems.append(f"{label} runs at {info['fps_num']}/{info['fps_den']} fps, the film at "
                        f"{fps.numerator}/{fps.denominator}")
    return problems


def _renders_sub(ctx: FilmContext) -> tuple[Sub, list]:
    entries = ctx.render_entries()
    current = ctx.current_renders()
    read = [ctx.REGISTRY]
    if not entries:
        return Sub("renders", "UNCHECKED", "no act renders are registered yet (renders/registry.json)",
                   ["next: slate render"]), read
    if not media.have("ffprobe"):
        return Sub("renders", "UNCHECKED", "ffprobe isn't installed, so render frame counts can't be read",
                   ["next: slate doctor --install"]), read
    problems, rows, missing = [], [], []
    for a in ctx.acts:
        e = current.get(a["index"])
        if e is None:
            missing.append(a["index"])
            continue
        path = e["path"]
        read.append(path)
        label = f"act {a['index']} render {ctx.rel(path)}"
        if not path.is_file():
            problems.append(f"{label} is registered but missing on disk")
            continue
        if e.get("sha256") and ctx.sha(path) != e["sha256"]:
            problems.append(f"{label} isn't the file that was registered (sha256 differs)")
        try:
            info = media.video_info(path)
        except SlateError as exc:
            problems.append(f"{label}: {exc}")
            continue
        want = a["frame_out"] - a["frame_in"]
        problems += _media_problems(label, info, want, ctx.size, ctx.fps)
        rows.append(f"act {a['index']}: {ctx.rel(path)} {info.get('frames')} frames (want {want}) "
                    f"{info.get('width')}×{info.get('height')} {info.get('codec')}")
    for e in entries:
        fin, fout = e.get("frame_in"), e.get("frame_out")
        total = ctx.total_frames
        if fin is None or fout is None or (total is not None and not (0 <= fin < fout <= total)):
            problems.append(f"registry entry {e.get('file')} has frames {fin}–{fout}, outside the film (0–{total})")
    if problems:
        return Sub("renders", "FAIL", problems[0], problems + rows), read
    if missing:
        return Sub("renders", "UNCHECKED", f"acts {', '.join(map(str, missing))} have no whole-act render yet",
                   rows + ["next: slate render --act N"], {"missing_acts": missing}), read
    return Sub("renders", "PASS", f"every act render has exactly its frames ({plural(len(rows), 'render')})",
               rows), read


def _masters_sub(ctx: FilmContext) -> tuple[Sub | None, list]:
    masters = ctx.masters()
    read: list = [*ctx.master_records()]
    if not masters:
        return None, read
    if not media.have("ffprobe"):
        return Sub("masters", "UNCHECKED", "ffprobe isn't installed, so master frame counts can't be read"), read
    total = ctx.total_frames
    has_mix = ctx.mix_path() is not None
    problems, rows = [], []
    for m in masters:
        read.append(m)
        try:
            info = media.video_info(m)
            audio_streams = media.audio_streams(m) if has_mix else []
        except SlateError as exc:
            problems.append(f"{ctx.rel(m)}: {exc}")
            continue
        problems += _media_problems(f"master {ctx.rel(m)}", info, int(total or 0), ctx.size, ctx.fps)
        if has_mix and not audio_streams:
            problems.append(f"master {ctx.rel(m)} carries no audio, but the film has a mix")
        sound = f", audio {audio_streams[0].get('codec_name')}" if audio_streams else ""
        rows.append(f"{ctx.rel(m)}: {info.get('frames')} frames {info.get('codec')} "
                    f"{info.get('profile') or ''}{sound}".replace("  ", " ").strip())
    if problems:
        return Sub("masters", "FAIL", problems[0], problems + rows), read
    return Sub("masters", "PASS", f"{plural(len(masters), 'master')} with exactly {total} frames", rows), read


def _mix_length_sub(ctx: FilmContext) -> tuple[Sub | None, list]:
    mix = ctx.mix_path()
    if mix is None or ctx.clock is None or not ctx.total_frames:
        return None, []
    try:
        wi = wav.info(mix)
        seconds = wi.seconds
    except wav.WavError:
        try:
            seconds = float(media.probe(mix).get("format", {}).get("duration") or 0)
        except SlateError as exc:
            return Sub("mix_length", "UNCHECKED", f"couldn't read the mix's length: {exc}"), [mix]
    film_s = ctx.secs_of(ctx.total_frames)
    frame = ctx.frame_seconds or 0.0
    diff = seconds - film_s
    line = f"{ctx.rel(mix)} is {seconds:.6f} s; the picture is {film_s:.6f} s ({diff * 1000:+.2f} ms)"
    if abs(diff) > frame:
        return Sub("mix_length", "FAIL", f"the mix is {abs(diff):.3f} s {'longer' if diff > 0 else 'shorter'} "
                   "than the picture (more than a frame)", [line]), [mix]
    return Sub("mix_length", "PASS", "the mix is as long as the picture, to within a frame", [line]), [mix]


def frames(film_dir: Path) -> GateResult:
    """Exact frame counts: shots contiguous on the film clock, every act render and master exactly as long as it
    should be, at the film's size and rate, and the mix as long as the picture."""
    ctx = context(film_dir)
    engine = media.engine("probe (count_packets)", tool="ffprobe")
    if ctx.shots_doc is None:
        return GateResult.unchecked("frames", "there's no timing/shots.json yet, so there are no frames to count",
                                    evidence=["next: slate timing"], engine=engine,
                                    inputs=ctx.inputs(["film.json", ctx.SHOTS]))
    subs: list[Sub] = []
    clock_problem = _clock_sub(ctx)
    if clock_problem:
        subs.append(clock_problem)
    subs.append(_shots_sub(ctx))
    renders, read_r = _renders_sub(ctx)
    subs.append(renders)
    masters, read_m = _masters_sub(ctx)
    if masters:
        subs.append(masters)
    mix, read_x = _mix_length_sub(ctx)
    if mix:
        subs.append(mix)
    return combine("frames", subs, engine=engine,
                   inputs=ctx.inputs(["film.json", ctx.SHOTS, *read_r, *read_m, *read_x]))


# ---------------------------------------------------------------------------------------------- events_complete
def events_complete(film_dir: Path) -> GateResult:
    """Every act in timing/shots.json has a valid timing/visual_events/actN.json, or an explicit
    {"silent": true, "reason": "…"}. A missing act is a FAIL: its sound can't be checked against beats that
    don't exist."""
    ctx = context(film_dir)
    engine = slate_engine("visual-events contract")
    if ctx.shots_doc is None:
        return GateResult.unchecked("events_complete", "there's no timing/shots.json yet, so there are no acts",
                                    evidence=["next: slate timing"], engine=engine, inputs=ctx.inputs([ctx.SHOTS]))
    rows, missing, invalid, silent, read = [], [], [], [], [ctx.SHOTS]
    for a in ctx.acts:
        ev = ctx.act_events(a)
        read.append(ev.path)
        rel = ctx.rel(ev.path)
        if not ev.exists:
            missing.append(a["index"])
            rows.append({"act": a["index"], "file": rel, "status": "missing"})
        elif ev.problems:
            invalid.append(a["index"])
            rows.append({"act": a["index"], "file": rel, "status": "invalid", "problems": ev.problems[:20]})
        elif ev.silent:
            silent.append(a["index"])
            rows.append({"act": a["index"], "file": rel, "status": "silent", "reason": ev.reason})
        else:
            rows.append({"act": a["index"], "file": rel, "status": "ok", "beats": len(ev.events)})
    known = {ctx.events_rel(a["index"]) for a in ctx.acts}
    folder = ctx.path("timing/visual_events")
    orphans = sorted(ctx.rel(p) for p in folder.glob("act*.json")) if folder.is_dir() else []
    orphans = [o for o in orphans if o not in known]
    subs: list[Sub] = []
    if missing:
        subs.append(Sub("missing", "FAIL", f"{plural(len(missing), 'act')} ha{'s' if len(missing) == 1 else 've'} no "
                        f"visual events file: act{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))}",
                        [f"missing: {ctx.events_rel(i)} (write its beats with `slate emit-events --act {i}`, or "
                         f'{{"silent": true, "reason": "…"}} if the act truly has none)' for i in missing]))
    if invalid:
        subs.append(Sub("invalid", "FAIL", f"act{'s' if len(invalid) > 1 else ''} {', '.join(map(str, invalid))}: "
                        "events file doesn't meet the contract",
                        [f"{r['file']}: {p}" for r in rows if r["status"] == "invalid" for p in r["problems"][:10]]))
    if orphans:
        subs.append(Sub("orphans", "FAIL", f"events files for acts that aren't in shots.json: {', '.join(orphans)}",
                        [f"orphan: {o}" for o in orphans]))
    if not subs:
        n_beats = sum(r.get("beats", 0) for r in rows)
        subs.append(Sub("complete", "PASS", ("the one act has its beats" if len(rows) == 1 else
                                             f"all {len(rows)} acts have their beats") + f" ({n_beats} beats"
                        + (f"; {plural(len(silent), 'act')} silent with a reason" if silent else "") + ")",
                        [f"{r['file']}: " + (f"{r['beats']} beats" if r["status"] == "ok" else f"silent ({r['reason']})")
                         for r in rows]))
    return combine("events_complete", subs, engine=engine, inputs=ctx.inputs(read + orphans),
                   details={"acts": rows, "missing": missing, "invalid": invalid, "silent": silent,
                            "orphans": orphans})
