"""The sync gate: every sound on its beat, every take where its cue puts it, every word-anchored beat on its word.

Four checks, each measured, none assumed:

1. coverage      every act has beats (see events_complete). Film C's sync check passed with two acts that had no
                 beats at all, because it only measured the cues that existed. An act without beats fails here too;
2. effects       every effect's audible anchor within one frame of its beat. The sound module measures this on its
                 own render of each cue (`slate verify` records `sound.cue_sync` in sound/mix/verify.json); this gate
                 takes that verdict unless the files it measured have changed since (then UNCHECKED), and it reads
                 the films' older record shape too, checking it was made on this film's clock;
3. voice         every VO take found in the dialogue stem by cross-correlation within ±50 ms of where its cue puts
                 it: exact means under half a millisecond off, and the stem must correlate with the approved take
                 (r ≥ 0.95, the sound module's floor too), or it isn't that take;
4. word landings every beat that names a word (`"word": "…"`, optional `occ` and `offset`) lands within two frames
                 of that word in the take. A beat that lands off its word by design says so with
                 `"sync_exception": "<why>"`: it becomes a REVIEW item for a person, never a silent pass.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np

from slate import __version__, gates
from slate.errors import SlateError
from slate.gates import GateResult
from slate.qa import media, wav
from slate.qa.checks import Sub, combine, plural, review_items
from slate.qa.film import FilmContext, context
from slate.qa.textmatch import norm_word

SEARCH_S = 0.05  # ±50 ms around the cue's time
EXACT_MS = 0.5
MIN_CORR = 0.95  # the sound module's voice check uses the same floor, so the two never disagree on one measurement


# ---------------------------------------------------------------------------------------------- 1. coverage
def _coverage(ctx: FilmContext) -> tuple[Sub, list]:
    missing, invalid, read = [], [], []
    for a in ctx.acts:
        ev = ctx.act_events(a)
        read.append(ev.path)
        if not ev.exists:
            missing.append(a["index"])
        elif ev.problems:
            invalid.append(a["index"])
    if missing or invalid:
        parts = []
        if missing:
            parts.append(f"act{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} "
                         f"ha{'s' if len(missing) == 1 else 've'} no beats")
        if invalid:
            parts.append(f"act{'s' if len(invalid) > 1 else ''} {', '.join(map(str, invalid))} "
                         f"ha{'s' if len(invalid) == 1 else 've'} an invalid events file")
        return Sub("coverage", "FAIL", " and ".join(parts) + ", so their sound can't be checked against the picture",
                   ["see the events_complete gate"], {"missing": missing, "invalid": invalid}), read
    return Sub("coverage", "PASS", f"every act has beats to sync against ({plural(len(ctx.acts), 'act')})"), read


# ---------------------------------------------------------------------------------------------- 2. effects
def _record_is_stale(ctx: FilmContext, rel: str, doc: dict) -> str | None:
    recorded = doc.get("inputs")
    if isinstance(recorded, dict) and recorded:
        for p, digest in recorded.items():
            if ctx.sha(p) != digest:
                return f"{p} changed since {rel} was written"
        return None
    mix = ctx.mix_path()
    rec_path = ctx.path(rel)
    if mix is not None and mix.is_file() and rec_path.stat().st_mtime < mix.stat().st_mtime:
        return f"{rel} is older than the mix ({ctx.rel(mix)})"
    return None


def _effects_from_record(ctx: FilmContext, rel: str, doc: dict) -> Sub | None:
    s = doc.get("sync")
    if not isinstance(s, dict):
        return None
    frame_ms = (ctx.frame_seconds or 0.0) * 1000.0
    stale = _record_is_stale(ctx, rel, doc)
    if stale:
        return Sub("effects", "UNCHECKED", f"the sound engine's verification is out of date: {stale}",
                   ["next: slate verify"])
    rec_frame = s.get("frame_ms")
    if rec_frame is not None and abs(float(rec_frame) - frame_ms) > 0.05:
        return Sub("effects", "FAIL", f"{rel} measured against a {rec_frame} ms frame; this film's frame is "
                   f"{frame_ms:.2f} ms (a different clock)", [f"{rel}: frame_ms {rec_frame}"])
    anchors = int(s.get("anchors") or 0)
    over = [o for o in s.get("over_one_frame") or [] if isinstance(o, dict)]
    worst = float(s.get("max_abs_delta_ms") or 0.0)
    line = f"{rel}: {anchors} sound anchors against their beats, worst {worst:.2f} ms (limit ±{frame_ms:.2f} ms)"
    if anchors == 0:
        return Sub("effects", "FAIL", "the sound engine measured no anchors, so no sound is proven on its beat", [line])
    if over or worst > frame_ms + 1e-6:
        ev = [line] + [f"{o.get('family', '')} beat {o.get('beat_s')} s → {o.get('delta_ms')} ms "
                       f"{o.get('note', '')}".strip() for o in over[:30]]
        count = plural(len(over), "sound anchor") if over else "some sound anchors"
        beats = "its beat" if len(over) == 1 else "their beats"
        return Sub("effects", "FAIL", f"{count} more than a frame off {beats} (worst {worst:.1f} ms)", ev)
    return Sub("effects", "PASS", f"{anchors} sound anchors within a frame of their beats (worst {worst:.2f} ms)",
               [line])


def _effects_from_markers(ctx: FilmContext) -> Sub | None:
    doc = ctx.read_json("sound/markers.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("markers"), list):
        return None
    beats = {(b["shot"], b["id"]): b["frame"] for b in ctx.beats()}
    frame = ctx.frame_seconds or 0.0
    bad, n, worst = [], 0, 0.0
    for m in doc["markers"]:
        if not isinstance(m, dict) or not m.get("event"):
            continue
        key = (str(m.get("shot")), str(m["event"]))
        if key not in beats:
            bad.append(f"{key[0]}/{key[1]}: placed at {m.get('t')} s but there's no such beat")
            continue
        err = abs(float(m["t"]) - ctx.secs_of(beats[key]))
        n += 1
        worst = max(worst, err)
        if err > frame + 1e-6:
            bad.append(f"{key[0]}/{key[1]} placed {err * 1000:.1f} ms from its beat")
    line = f"sound/markers.json: {n} placed cues, worst {worst * 1000:.2f} ms from the beat (limit {frame * 1000:.2f} ms)"
    if bad:
        return Sub("effects", "FAIL", f"{plural(len(bad), 'cue')} off or orphaned: {bad[0]}", [line] + bad[:30])
    if n == 0:
        return None
    return Sub("effects", "PASS", f"{n} cues placed within a frame of their beats (placement log; the audible anchors "
               "aren't measured until `slate verify` runs)", [line])


def _from_cue_sync(ctx: FilmContext, raw: dict, where: str) -> Sub:
    """The sound module's own `sound.cue_sync` verdict (it measures each effect rendered on its own), unless the files
    it measured have changed since."""
    r = GateResult.from_dict(raw)
    changed = [p for p, digest in (r.inputs or {}).items() if ctx.sha(p) != digest]
    if changed or r.status == "STALE":
        what = changed[0] if changed else "its inputs"
        return Sub("effects", "UNCHECKED", f"the sound engine's cue check is out of date ({what} changed since)",
                   ["next: slate verify"], {"from": where})
    return Sub("effects", r.status, f"sound.cue_sync: {r.summary}", [where] + list(r.evidence)[:30],
               {"from": where, **{k: v for k, v in (r.details or {}).items() if k in ("frame_ms", "anchors",
                                                                                    "max_abs_delta_ms")}})


def _effects_from_sound_module(film_dir: Path) -> Sub | None:
    try:
        verify = importlib.import_module("slate.sound.verify")
    except ImportError:
        return None
    run = getattr(verify, "run", None)
    if not callable(run):
        return None
    import inspect  # noqa: PLC0415

    try:
        if "write" not in inspect.signature(run).parameters:
            return None  # a run that writes its own records isn't ours to start from here
        results = run(film_dir, ["sound.cue_sync"], write=False) or []
    except SlateError as exc:
        return Sub("effects", "UNCHECKED", f"the sound module's verification couldn't run: {exc.message}",
                   [f"next: {exc.hint}"] if exc.hint else [])
    except Exception as exc:  # noqa: BLE001 - a bug there leaves this check unchecked, not the whole sync gate
        return Sub("effects", "UNCHECKED", f"the sound module's cue check failed: {type(exc).__name__}: {exc}")
    picked = [r for r in results if isinstance(r, GateResult) and any(k in r.gate for k in ("sync", "anchor", "cue"))]
    if not picked:
        return None
    status = max((r.status for r in picked), key=lambda s: {"FAIL": 4, "STALE": 3, "UNCHECKED": 2, "REVIEW": 1,
                                                          "PASS": 0}[s])
    return Sub("effects", status, "; ".join(f"{r.gate}: {r.summary}" for r in picked),
               [e for r in picked for e in r.evidence][:30], {"from": "slate.sound.verify"})


def _effects(ctx: FilmContext) -> tuple[Sub | None, list]:
    rel, doc = ctx.verify_record()
    from slate.qa import film as filmmod  # noqa: PLC0415

    # (qa/gates.json is never an input: every write of the report would make this result stale. The sound verdict
    # read from it is checked against its own inputs instead.)
    read: list = list(filmmod.VERIFY_RECORDS) + ["sound/markers.json"]
    if doc:
        cue_sync = (doc.get("gates") or {}).get("sound.cue_sync") if isinstance(doc.get("gates"), dict) else None
        sub = _from_cue_sync(ctx, cue_sync, rel) if isinstance(cue_sync, dict) else _effects_from_record(ctx, rel, doc)
        if sub:
            return sub, read
    latest = (ctx.read_json("qa/gates.json") or {}).get("gates", {}).get("sound.cue_sync")
    if isinstance(latest, dict):
        return _from_cue_sync(ctx, latest, "qa/gates.json"), read
    sub = _effects_from_markers(ctx) or _effects_from_sound_module(ctx.dir)
    if sub:
        return sub, read
    if not ctx.beats():
        return None, read
    return Sub("effects", "UNCHECKED", "the sound engine hasn't verified where its cues landed",
               ["next: slate verify (writes sound/mix/verify.json)"]), read


# ---------------------------------------------------------------------------------------------- 3. voice placement
def _decode_mono(path: Path, rate: int) -> np.ndarray:
    try:
        x, wi = wav.read(path)
        if wi.sample_rate == rate:
            return x.mean(axis=1)
    except wav.WavError:
        pass
    raw = media._run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                      "-ar", str(rate), "-f", "f64le", "-"], text=False).stdout or b""
    return np.frombuffer(raw[: len(raw) - len(raw) % 8], dtype="<f8").copy()


def locate(stem: Path, take: np.ndarray, expected_s: float, rate: int, search_s: float = SEARCH_S) -> dict | None:
    """Find a take in a stem near where it should start: {offset_ms, corr} or None if the stem is too short."""
    from scipy import signal  # noqa: PLC0415

    n = len(take)
    w = int(round(search_s * rate))
    e = int(round(expected_s * rate))
    lo = max(0, e - w)
    seg, _ = wav.read(stem, lo, n + (e - lo) + w)
    seg = seg.mean(axis=1) if seg.ndim == 2 else seg
    if len(seg) < n or n == 0:
        return None
    c = signal.correlate(seg, take, mode="valid", method="fft")
    j = int(np.argmax(c))
    window = seg[j:j + n]
    corr = float(np.corrcoef(window, take)[0, 1]) if np.std(window) > 0 and np.std(take) > 0 else 0.0
    return {"offset_ms": round((lo + j - e) / rate * 1000.0, 3), "corr": round(corr, 4)}


def _voice(ctx: FilmContext) -> tuple[Sub | None, list]:
    cues = ctx.vo_cues
    read: list = ["timing/vo_cues.json"]
    if not cues:
        return None, read
    stem = ctx.dialogue_stem()
    if stem is None or not stem.is_file():
        rel, doc = ctx.verify_record()
        v = (doc or {}).get("voice")
        if isinstance(v, dict) and v.get("takes"):
            ok = bool(v.get("all_exact")) and int(v.get("takes")) == len(cues)
            return Sub("voice", "PASS" if ok else "FAIL",
                       f"{rel}: {v.get('takes')} of {len(cues)} takes, max offset {v.get('max_abs_offset_ms')} ms, "
                       f"lowest correlation {v.get('min_corr')} (the engine's own measurement)"), read
        return Sub("voice", "UNCHECKED", "there's no dialogue stem to find the takes in",
                   ["next: slate mix (writes the stems)"]), read
    read.append(stem)
    try:
        rate = wav.info(stem).sample_rate
    except wav.WavError as exc:
        return Sub("voice", "UNCHECKED", f"the dialogue stem isn't readable: {exc}"), read
    shots = ctx.shot_by_id
    rows, off, weak, problems = [], [], [], []
    for c in cues:
        sid = str(c.get("shot"))
        take = ctx.path(str(c.get("file") or ""))
        read.append(take)
        if sid not in shots:
            problems.append(f"{sid}: the cue's shot isn't in shots.json")
            continue
        if not take.is_file():
            problems.append(f"{sid}: take {c.get('file')} is missing")
            continue
        expected = ctx.secs_of(int(shots[sid]["frame_in"])) + float(c.get("at") or 0.0)
        try:
            y = _decode_mono(take, rate)
        except SlateError as exc:
            problems.append(f"{sid}: couldn't decode {c.get('file')} ({exc})")
            continue
        found = locate(stem, y, expected, rate)
        if found is None:
            problems.append(f"{sid}: the dialogue stem ends before this take")
            continue
        rows.append({"shot": sid, "expected_s": round(expected, 4), **found})
        if abs(found["offset_ms"]) >= EXACT_MS:
            off.append(rows[-1])
        elif found["corr"] < MIN_CORR:
            weak.append(rows[-1])
    ev = [f"{r['shot']}: {r['offset_ms']:+.2f} ms, r = {r['corr']}" for r in rows]
    if problems:
        return Sub("voice", "FAIL", problems[0], problems + ev, {"takes": rows}), read
    if off:
        return Sub("voice", "FAIL", f"{plural(len(off), 'take')} not where the cue puts it (worst "
                   f"{max(abs(r['offset_ms']) for r in off):.1f} ms)", ev, {"takes": rows}), read
    if weak:
        return Sub("voice", "FAIL", f"{plural(len(weak), 'take')} placed exactly but not matching the approved take "
                   f"(lowest r = {min(r['corr'] for r in weak)}, needs {MIN_CORR}): a different take or processing is "
                   "in the mix", ev, {"takes": rows}), read
    return Sub("voice", "PASS", f"all {plural(len(rows), 'take')} exactly where their cues put them (lowest "
               f"r = {min(r['corr'] for r in rows) if rows else 'n/a'})", ev, {"takes": rows}), read


# ---------------------------------------------------------------------------------------------- 4. word landings
def word_time(ctx: FilmContext, shot: str, word: str, occ: int = 1) -> float | None:
    """Film seconds where a word starts in a shot's take (shot start + cue offset + the word's start), or None.

    The timing module's pure lookup comes first: it knows how numbers and symbols are spoken ("664" said as words).
    Its film-time helper isn't used here, because on a miss it returns an estimate and records the miss in timing/,
    and a gate neither measures against estimates nor writes into timing/.
    """
    record = ctx.vo_words.get(shot) or {}
    cue = next((c for c in ctx.vo_cues if str(c.get("shot")) == shot), None)
    s = ctx.shot_by_id.get(shot)
    if cue is None or s is None:
        return None
    base = ctx.secs_of(int(s["frame_in"])) + float(cue.get("at") or 0.0)
    try:
        timing_words = importlib.import_module("slate.timing.words")
        hit = timing_words.lookup(record, word, occ, "start", str(ctx.film.get("language") or "en"))
        return base + float(hit[0]) if hit is not None else None
    except (ImportError, AttributeError):
        pass
    except Exception:  # noqa: BLE001 - a failing lookup falls back to the exact match below
        pass
    want = norm_word(word)
    hits = [w for w in record.get("words") or [] if norm_word(str(w.get("w", ""))) == want]
    return base + float(hits[occ - 1]["start"]) if len(hits) >= occ else None


def _landings(ctx: FilmContext) -> tuple[Sub | None, list]:
    anchored = []
    for a in ctx.acts:
        ev = ctx.act_events(a)
        anchored += [(ev.path, e) for e in ev.events if isinstance(e.get("word"), str) and e["word"].strip()]
    read = ["timing/vo_words.json", "timing/vo_cues.json"]
    if not anchored:
        return None, read
    limit = 2 * (ctx.frame_seconds or 0.0) + 1e-6
    bad, excepted, n, worst = [], [], 0, 0.0
    for path, e in anchored:
        sid = str(e["shot"])
        t_word = word_time(ctx, sid, e["word"], int(e.get("occ") or 1))
        if t_word is None:
            bad.append(f"{sid}/{e['id']}: the word {e['word']!r} isn't in the take's words")
            continue
        err = float(e["t"]) - (t_word + float(e.get("offset") or 0.0))
        n += 1
        if abs(err) > limit:
            if e.get("sync_exception"):
                excepted.append({"key": f"word:{sid}/{e['id']}", "what": f"{sid}/{e['id']} lands {err * 1000:+.0f} ms "
                                 f"from {e['word']!r}: {e['sync_exception']}", "basis": ctx.inputs([path])})
            else:
                bad.append(f"{sid}/{e['id']} lands {err * 1000:+.0f} ms from {e['word']!r}")
        else:
            worst = max(worst, abs(err))
    line = f"{n} word-anchored beats, worst on-time landing {worst * 1000:.1f} ms (limit ±2 frames = {limit * 1000:.1f} ms)"
    open_items, accepted = review_items(ctx, "sync", excepted)
    details = {"review_items": open_items, "accepted": accepted}
    if bad:
        return Sub("word_landings", "FAIL", f"{plural(len(bad), 'beat')} off {'its' if len(bad) == 1 else 'their'} "
                   f"word: {bad[0]}", [line] + bad[:30], details), read
    if open_items:
        whose = "its" if len(open_items) == 1 else "their"
        return Sub("word_landings", "REVIEW", f"{plural(len(open_items), 'beat')} off {whose} word by design, waiting "
                   "for a person", [line] + [i["what"] for i in open_items], details), read
    extra = f"; {plural(len(accepted), 'exception')} accepted by a person" if accepted else ""
    return Sub("word_landings", "PASS", f"every word-anchored beat lands on its word{extra}", [line], details), read


# ---------------------------------------------------------------------------------------------- the gate
def sync(film_dir: Path) -> GateResult:
    """Cues within a frame of their beats, VO takes exactly at their cues, word-anchored beats on their words, and
    every act covered by beats."""
    ctx = context(film_dir)
    engine = gates.engine("slate sync", __version__, voice="numpy/scipy cross-correlation",
                          effects="the sound engine's verification record (sound/verify.json)")
    if ctx.shots_doc is None or ctx.clock is None:
        return GateResult.unchecked("sync", "there's no film clock yet (timing/shots.json and film.json fps)",
                                    evidence=["next: slate timing"], engine=engine,
                                    inputs=ctx.inputs(["film.json", ctx.SHOTS]))
    subs, read = [], ["film.json", ctx.SHOTS, "timing/timeline.json"]
    cov, r = _coverage(ctx)
    subs.append(cov)
    read += r
    for fn in (_effects, _voice, _landings):
        sub, r = fn(ctx)
        read += r
        if sub is not None:
            subs.append(sub)
    review = next((s.details.get("review_items") for s in subs if s.details.get("review_items")), [])
    details = {"review_items": review} if review else {}
    return combine("sync", subs, engine=engine, inputs=ctx.inputs(read), details=details)
