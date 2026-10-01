"""Text gates: what the audience reads and hears.

- ocr           Apple Vision reads the whole picture every 0.25 s (the one OCR engine; UNCHECKED when it can't run);
- banned_words  the brand's one banned-word list (brand pack `truth.banned_words`) against the script, the captions,
                what Whisper heard in every take in use (timing/vo_words.json) and the OCR of the picture. The films
                kept three copies of their list and two drifted apart. An entry may carry its inflections (`forms`) and
                an approved substitute (`instead`): every form is matched and a failure names the substitute. A brand
                that forbids self-commentary (`truth.self_commentary: false`) has "Illustration" and "Example"
                captions banned on screen too;
- readtime      every on-screen statement in the storyboard stays readable long enough once it is fully built:
                max(1.5 s, 0.4 s + 0.3 s per word), timed from the OCR, within its own shot (±0.5 s). Where the act's
                motion trace says which element shows the statement (its `text`), a moment counts only while the
                element is at least 90% opaque and moving under 8 px a frame at 1080p (scaled to the film). A shot may
                declare exemptions in script.json (`reading`, with a reason): a voice-matched definition needs only
                the 1.5 s floor, and a surface not meant to be read isn't timed (reading_declarations);
- captions      at most 2 lines of 42 characters, at most 17 characters a second (the format and film.json may
                tighten these, never loosen them), at least 1 s each, in order and never overlapping, and word for
                word the booth script (timing/captions.json, the voice module's cues, first);
- claims        every claim approved or sourced (the story module's check when it's installed).
"""

from __future__ import annotations

import difflib
import importlib
import re
from pathlib import Path
from typing import Any, Iterator

from slate import __version__, gates
from slate.errors import MissingPrerequisite, SlateError
from slate.gates import GateResult
from slate.qa import brandinfo, ocr as ocrmod, textmatch
from slate.qa.checks import Sub, combine, plural
from slate.qa.film import FilmContext, context, iter_storyboard_shots

# The script's text fields (src/slate/schemas/script.schema.json): what a shot says (vo), shows (on_screen) and
# depicts (picture). The script gate counts all three for banned words, and so does this gate, so they never disagree.
SHOT_TEXT = ("vo", "on_screen", "picture")
SPOKEN_OR_SHOWN = ("vo", "on_screen")
CAPTION_RULES = {"max_lines": 2, "max_chars": 42, "max_cps": 17.0, "min_seconds": 1.0}


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


def script_shots(script: dict) -> Iterator[tuple[str, dict]]:
    """(where, shot) for every shot (or line) of every act, in script order."""
    for ai, act in enumerate(script.get("acts") or [] if isinstance(script, dict) else []):
        if not isinstance(act, dict):
            continue
        for coll in ("shots", "lines"):
            for si, shot in enumerate(act.get(coll) or []):
                if isinstance(shot, dict):
                    label = f" {shot['id']}" if shot.get("id") else ""
                    yield f"acts[{ai}].{coll}[{si}]{label}", shot


def _claim_text(c: Any) -> str | None:
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for key in ("text", "claim", "approved"):
            if isinstance(c.get(key), str):
                return c[key]
    return None


def script_strings(script: dict, fields: tuple[str, ...] = SHOT_TEXT) -> Iterator[tuple[str, str]]:
    """(where, text) for the script's title, each shot's text fields, its claims and its quotes."""
    if isinstance(script, dict) and isinstance(script.get("title"), str):
        yield "title", script["title"]
    for where, shot in script_shots(script):
        for f in fields:
            for t in _texts(shot.get(f)):
                yield f"{where}.{f}", t
        for i, c in enumerate(shot.get("claims") or []):
            t = _claim_text(c)
            if t:
                yield f"{where}.claims[{i}]", t
        for i, q in enumerate(shot.get("quotes") or []):
            if isinstance(q, dict) and isinstance(q.get("text"), str):
                yield f"{where}.quotes[{i}]", q["text"]


def _ocr_unchecked(gate: str, ctx: FilmContext, exc: Exception, inputs: dict) -> GateResult:
    engine = ocrmod.unavailable_engine(exc)
    hint = getattr(exc, "hint", None)
    return GateResult.unchecked(gate, f"{str(exc).splitlines()[0]}", evidence=[f"next: {hint}"] if hint else [],
                                engine=engine, inputs=inputs)


def _sweep_inputs(ctx: FilmContext, sw) -> list:
    return [ctx.REGISTRY, *ctx.master_records(), *(sw.files if sw else [])]


# ---------------------------------------------------------------------------------------------- ocr
def ocr(film_dir: Path) -> GateResult:
    """Apple Vision reads the whole picture every 0.25 s at full width; UNCHECKED (never another engine) when it can't."""
    ctx = context(film_dir)
    base_inputs = ctx.inputs([ctx.REGISTRY, *ctx.master_records()])
    ok, info = ocrmod.available()
    if not ok:
        return GateResult.unchecked("ocr", f"{ocrmod.ENGINE_NAME} isn't available here: {info.get('reason')}",
                                    evidence=[f"next: {info.get('hint')}"], engine=info, inputs=base_inputs)
    try:
        sw = ocrmod.sweep(ctx)
    except (ocrmod.OcrUnavailable, MissingPrerequisite) as exc:
        return _ocr_unchecked("ocr", ctx, exc, base_inputs)
    except SlateError as exc:
        return GateResult.unchecked("ocr", f"the OCR sweep couldn't read the picture: {exc}", engine=info,
                                    inputs=base_inputs)
    with_text = sum(1 for s in sw.samples if s.get("lines"))
    subs = []
    if sw.missing_acts:
        subs.append(Sub("coverage", "UNCHECKED", f"acts {', '.join(map(str, sw.missing_acts))} have no render to read",
                        ["next: slate render"]))
    if len(sw.samples) < sw.expected:
        subs.append(Sub("frames", "UNCHECKED", f"only {len(sw.samples)} of {sw.expected} sampled frames could be read",
                        [sw.cache]))
    if not subs:
        subs.append(Sub("sweep", "PASS", f"read {len(sw.samples)} frames every {sw.every} s from the {sw.source}; "
                        f"{with_text} carry text", [sw.cache]))
    return combine("ocr", subs, engine=sw.engine, inputs=ctx.inputs(_sweep_inputs(ctx, sw)),
                   details={"source": sw.source, "files": sw.files, "every_s": sw.every, "samples": len(sw.samples),
                            "with_text": with_text, "cache": sw.cache})


# ---------------------------------------------------------------------------------------------- banned words
ON_SCREEN_FIELDS = ("on_screen", "picture")  # what a shot shows (a self-commentary caption is one of these)


def _heard(record: dict) -> str:
    """What Whisper heard in a take: its transcript, else its words joined."""
    text = record.get("heard")
    if isinstance(text, str) and text.strip():
        return text
    return " ".join(str(w.get("w") or "") for w in record.get("words") or [] if isinstance(w, dict))


def banned_words(film_dir: Path) -> GateResult:
    """The brand's one banned-word list (every form of every entry) against the script, the captions, what the voice
    really said and the OCR of the picture."""
    ctx = context(film_dir)
    ok, ocr_info = ocrmod.available()
    engine = gates.engine("slate banned-word match (whole words, every form, any case)", __version__, ocr=ocr_info)
    pack, where = brandinfo.load(ctx.brand)
    read: list = ["film.json", "script/script.json", brandinfo.pack_file(ctx.brand)]
    if pack is None:
        return GateResult.unchecked("banned_words", f"the brand's banned words can't be read: {where}",
                                    evidence=["next: slate brand validate"], engine=engine, inputs=ctx.inputs(read))
    entries = brandinfo.banned_entries(pack)
    if entries is None:
        words = brandinfo.banned_words(pack)
        entries = None if words is None else [e for e in (textmatch.banned_entry(w) for w in words) if e]
    if entries is None:
        return GateResult.unchecked("banned_words", "the brand pack doesn't say which words are banned "
                                    "(truth.banned_words)", evidence=[where], engine=engine, inputs=ctx.inputs(read))
    self_comment = brandinfo.self_commentary(pack)
    screen_entries = entries + ([] if self_comment else list(brandinfo.SELF_COMMENTARY))
    forms_n = sum(1 + len(e["forms"]) for e in entries)
    source = (f"{ctx.brand} brand pack truth.banned_words ({plural(len(entries), 'word')}, {forms_n} forms, {where})"
              + ("" if self_comment else "; self-commentary captions banned on screen (truth.self_commentary: false)"))
    pattern, forms = textmatch.banned_pattern(entries), textmatch.banned_forms(entries)
    screen, screen_forms = textmatch.banned_pattern(screen_entries), textmatch.banned_forms(screen_entries)
    hits: list[str] = []
    subs: list[Sub] = []
    # the script
    script = ctx.script
    if script is None:
        subs.append(Sub("script", "UNCHECKED", "there's no script/script.json to check", ["next: slate script build"]))
    else:
        n = 0
        for path, text in script_strings(script):
            n += 1
            shown = path.rsplit(".", 1)[-1] in ON_SCREEN_FIELDS
            pat, fm = (screen, screen_forms) if shown else (pattern, forms)
            hits += [f"script {path}: {textmatch.name_hit(w, fm)} in “{text[:80]}”"
                     for w in textmatch.find_banned(text, pat)]
        subs.append(Sub("script", "PASS", f"script: {n} texts checked (VO, on screen, picture, claims, quotes)"))
    # the captions
    cap = ctx.captions_path()
    spoken = ctx.spoken_lines()
    from slate.qa import film as filmmod  # noqa: PLC0415

    read += [cap] if cap else list(filmmod.CAPTION_CANDIDATES)
    if cap is not None:
        try:
            cues = textmatch.load_cues(cap)
            for c in cues:
                hits += [f"captions cue {c['index']} ({c['start']:.2f} s): {textmatch.name_hit(w, forms)}" for w in
                         textmatch.find_banned(c["text"], pattern)]
            subs.append(Sub("captions", "PASS", f"captions: {plural(len(cues), 'cue')} checked"))
        except (OSError, ValueError, KeyError) as exc:
            subs.append(Sub("captions", "UNCHECKED", f"couldn't read {ctx.rel(cap)}: {exc}"))
    elif spoken:
        subs.append(Sub("captions", "UNCHECKED", "the film has spoken lines but no captions yet",
                        ["next: slate voice captions"]))
    # what the voice really said: Whisper's words for every take in use
    read.append("timing/vo_words.json")
    heard = ctx.vo_words
    if heard:
        n = 0
        for sid, rec in heard.items():
            text = _heard(rec)
            if not text.strip():
                continue
            n += 1
            hits += [f"speech {sid} (take {rec.get('take') or '?'}): {textmatch.name_hit(w, forms)} in what Whisper "
                     f"heard, “{text[:80]}”" for w in textmatch.find_banned(text, pattern)]
        if n:
            subs.append(Sub("speech", "PASS", f"speech: what Whisper heard in {plural(n, 'take')} checked"))
        else:
            subs.append(Sub("speech", "UNCHECKED", "timing/vo_words.json has no words Whisper heard yet",
                            ["next: slate timing words"]))
    elif spoken or any(_texts(shot.get("vo")) for _w, shot in script_shots(script or {})):
        subs.append(Sub("speech", "UNCHECKED", "the takes haven't been transcribed, so what the voice said isn't "
                        "checked yet", ["next: slate timing words"]))
    # the picture
    sw = None
    if not ok:
        subs.append(Sub("picture", "UNCHECKED", f"the picture isn't read: {ocr_info.get('reason')}",
                        [f"next: {ocr_info.get('hint')}"]))
    else:
        try:
            sw = ocrmod.sweep(ctx)
            for s in sw.samples:
                for ln in s.get("lines") or []:
                    hits += [f"picture at {s['t']:.2f} s (film frame {s['frame']}): {textmatch.name_hit(w, screen_forms)}"
                             f" in “{ln.get('text')}”" for w in textmatch.find_banned(ln.get("text", ""), screen)]
            if sw.missing_acts:
                subs.append(Sub("picture", "UNCHECKED", f"acts {', '.join(map(str, sw.missing_acts))} have no render "
                                "to read", ["next: slate render"]))
            else:
                subs.append(Sub("picture", "PASS", f"picture: {len(sw.samples)} frames read every {sw.every} s",
                                [sw.cache]))
        except (ocrmod.OcrUnavailable, MissingPrerequisite, SlateError) as exc:
            subs.append(Sub("picture", "UNCHECKED", f"the picture isn't read: {str(exc).splitlines()[0]}",
                            [f"next: {getattr(exc, 'hint', '') or 'slate render'}"]))
    if hits:
        subs.insert(0, Sub("hits", "FAIL", f"{plural(len(hits), 'banned word')} found: {hits[0]}", hits[:60]))
    return combine("banned_words", subs, engine=engine, inputs=ctx.inputs(read + _sweep_inputs(ctx, sw)),
                   details={"list": source, "hits": hits[:200], "self_commentary": self_comment})


# ---------------------------------------------------------------------------------------------- read time
READING_KEY = "reading"
READING_FLAGS = ("not_for_reading", "voice_matched")


def _statements(ctx: FilmContext) -> tuple[list[tuple[str, str]], str | None]:
    """(shot, statement) pairs to time, and where they came from."""
    for label, shots in (("script/storyboard.json", list(iter_storyboard_shots(ctx.storyboard or {}))),
                         ("timing/shots.json", ctx.shots)):
        # a source counts only if it says what's on screen (an empty on_screen says "nothing"; no key says nothing)
        if any("on_screen" in s for s in shots):
            items = [(str(s.get("id")), part) for s in shots for part in textmatch.statement_parts(s.get("on_screen"))]
            return items, label
    return [], None


def reading_declarations(ctx: FilmContext) -> tuple[dict[str, dict], list[str]]:
    """The read-time exemptions the film declares, by shot id, and what's wrong with any of them.

    A shot says `"reading": {"not_for_reading": true | [statements], "voice_matched": true | [statements],
    "reason": "…"}` in script/script.json (the one hand-edited file; the storyboard's copy wins when it carries one):

    - not_for_reading: a surface shown too small to read on purpose (a report at 0.6 scale as a closing object). Its
      small print isn't timed; only its large elements must read, so a list names exactly which statements are exempt;
    - voice_matched: a definition the voice reads along as it lands. It may run past the on-screen word budget, and
      must stay complete for 1.5 s (the floor) rather than 0.4 s + 0.3 s a word.

    An exemption says why (`reason`), and names statements the shot really shows: otherwise it's a problem, never a
    quiet pass. Returns ({shot: {flag: True | set of squashed statements, "reason": str}}, problems).
    """
    out: dict[str, dict] = {}
    problems: list[str] = []
    try:
        script = ctx.script or {}
    except SlateError as exc:
        script = {}
        problems.append(f"script/script.json can't be read, so its read-time exemptions can't be either: {exc}")
    script_shots_by_id = {str(shot.get("id")): shot for _w, shot in script_shots(script)}
    board = {str(s.get("id")): s for s in iter_storyboard_shots(ctx.storyboard or {})}
    # a renumbered storyboard names its script shot in source_id: that script shot is read through it, not again
    through_board = {str(s.get("source_id") or sid) for sid, s in board.items()}
    others = [sid for sid in script_shots_by_id if sid not in through_board and sid not in board]
    for sid in dict.fromkeys([*board, *others, *(str(s.get("id")) for s in ctx.shots)]):
        shot = board.get(sid) or {}
        raw = shot.get(READING_KEY)
        if raw is None:
            src = script_shots_by_id.get(str(shot.get("source_id") or sid)) or {}
            raw = src.get(READING_KEY)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            problems.append(f"{sid}: \"{READING_KEY}\" should be an object like "
                            '{"not_for_reading": true, "reason": "…"}')
            continue
        unknown = sorted(set(raw) - {*READING_FLAGS, "reason"})
        if unknown:
            problems.append(f"{sid}: \"{READING_KEY}\" has {', '.join(unknown)}; it takes not_for_reading, "
                            "voice_matched and reason")
        decl: dict = {}
        for flag in READING_FLAGS:
            v = raw.get(flag)
            if v is None or v is False:
                continue
            if v is True:
                decl[flag] = True
            elif isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v):
                decl[flag] = {textmatch.squash(p) for x in v for p in (textmatch.statement_parts(x) or [x])}
            else:
                problems.append(f"{sid}: {flag} should be true or a list of the statements it covers")
        if not decl:
            continue
        reason = raw.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{sid}: declares {' and '.join(decl)} without a reason; an exemption says why")
            continue
        decl["reason"] = reason.strip()
        out[sid] = decl
    return out, problems


LEGIBLE_OPACITY = 0.9  # a statement reads only where it's at least 90% opaque…
LEGIBLE_SPEED = 1 / 135  # …and moving under 8 px a frame at 1080 lines: a share of the film's short side


def _at(m, frame: float):
    """A traced move's value at a film frame: its start before it, its end after it, its samples in between."""
    import numpy as np  # noqa: PLC0415

    if m.values is None or len(m.values) == 0:
        return None
    if frame <= m.frame_in:
        return np.asarray(m.start if m.start is not None else m.values[0], dtype=float)
    if frame >= m.frame_out:
        return np.asarray(m.end if m.end is not None else m.values[-1], dtype=float)
    return np.array([np.interp(frame, m.frames, m.values[:, k]) for k in range(m.values.shape[1])])


def legibility(ctx: FilmContext, statements: list[tuple[str, str]]):
    """{(shot, statement): a frame -> bool test} for the statements an act's motion trace names (`text` on the moves
    of the element that shows it), and how many it doesn't. Legible: every opacity channel at least 90% (a 0–100
    scale is read as percent) and the element's position moving under 8 px a frame at 1080 lines."""
    try:
        from slate.qa import motion  # noqa: PLC0415

        data = motion.load_data(ctx.dir, ctx=ctx)
    except Exception:  # noqa: BLE001 - no trace, or a trace the motion gate will report: OCR alone times it
        return {}, len(statements)
    traced = [m for m in data.moves if m.text]
    size = ctx.size
    if not size:
        return {}, len(statements)  # no frame size: the speed can't be scaled, so OCR alone times them
    limit = LEGIBLE_SPEED * min(size)
    tests, untraced = {}, 0
    for sid, text in statements:
        key = textmatch.squash(text)
        moves = [m for m in traced if key and key in textmatch.squash(m.text)]
        if not moves:
            untraced += 1
            continue
        opacity = [m for m in moves if m.kind == "opacity"]
        position = [m for m in moves if m.kind in ("x", "y", "vector", "px")]
        scale = {id(m): (100.0 if max(abs(float(v)) for v in m.values.ravel()) > 1.5 else 1.0) for m in opacity}

        def test(frame: float, opacity=opacity, position=position, scale=scale) -> bool:
            for m in opacity:
                v = _at(m, frame)
                if v is not None and float(v.min()) / scale[id(m)] < LEGIBLE_OPACITY - 1e-9:
                    return False
            speed2 = 0.0
            for m in position:
                a, b = _at(m, frame - 1), _at(m, frame)
                if a is not None and b is not None:
                    speed2 += float(((b - a) ** 2).sum())
            return speed2 ** 0.5 < limit

        tests[(sid, text)] = test
    return tests, untraced


def _declared(decl: dict | None, flag: str, text: str) -> bool:
    v = (decl or {}).get(flag)
    return v is True or (isinstance(v, set) and textmatch.squash(text) in v)


def readtime(film_dir: Path) -> GateResult:
    """Every on-screen statement stays readable for max(1.5 s, 0.4 s + 0.3 s per word) once fully built (a declared
    voice-matched definition for 1.5 s; a surface declared not for reading isn't timed)."""
    ctx = context(film_dir)
    ok, info = ocrmod.available()
    engine = gates.engine("slate read-time matcher", __version__, ocr=info)
    items, source = _statements(ctx)
    read = ["script/storyboard.json", ctx.SHOTS, "script/script.json"]
    if source is None:
        return GateResult.unchecked("readtime", "there's no storyboard or shot list with on-screen text yet",
                                    evidence=["next: slate script build"], engine=engine, inputs=ctx.inputs(read))
    decls, decl_problems = reading_declarations(ctx)
    exempt = [(sid, text) for sid, text in items if _declared(decls.get(sid), "not_for_reading", text)]
    timed = [(sid, text) for sid, text in items if (sid, text) not in exempt]
    for sid, decl in decls.items():
        for flag in READING_FLAGS:
            listed = decl.get(flag)
            if isinstance(listed, set):
                shown = {textmatch.squash(t) for s, t in items if s == sid}
                for stray in sorted(listed - shown):
                    decl_problems.append(f"{sid}: {flag} names a statement the shot doesn't show ({stray!r})")
    subs: list[Sub] = []
    if decl_problems:
        subs.append(Sub("declarations", "FAIL", f"{plural(len(decl_problems), 'read-time exemption')} can't be "
                        f"honoured: {decl_problems[0]}", decl_problems))
    exempt_rows = [f"{sid} '{text}': not for reading ({decls[sid]['reason']})" for sid, text in exempt]
    if exempt_rows:
        subs.append(Sub("exempt", "PASS", f"{plural(len(exempt_rows), 'statement')} declared not for reading, so not "
                        "timed", exempt_rows))
    details = {"statements": len(items), "source": source, "exempt": exempt_rows,
               "voice_matched": sorted({sid for sid, d in decls.items() if "voice_matched" in d})}
    if not items:
        subs.append(Sub("statements", "PASS", f"{source} has no on-screen statements to time"))
        return combine("readtime", subs, engine=engine, inputs=ctx.inputs(read), details=details)
    if not timed:
        return combine("readtime", subs, engine=engine, inputs=ctx.inputs(read), details=details)
    if not ok:
        subs.append(Sub("ocr", "UNCHECKED", f"{ocrmod.ENGINE_NAME} isn't available here: {info.get('reason')}",
                        [f"next: {info.get('hint')}"]))
        return combine("readtime", subs, engine=info, inputs=ctx.inputs(read), details=details)
    try:
        sw = ocrmod.sweep(ctx)
    except (ocrmod.OcrUnavailable, MissingPrerequisite, SlateError) as exc:
        hint = getattr(exc, "hint", None)
        subs.append(Sub("ocr", "UNCHECKED", str(exc).splitlines()[0], [f"next: {hint}"] if hint else []))
        return combine("readtime", subs, engine=ocrmod.unavailable_engine(exc), inputs=ctx.inputs(read),
                       details=details)
    every = sw.every
    texts = [(s["t"], textmatch.squash(sw.text_at(s))) for s in sw.samples]
    shots = ctx.shot_by_id
    legible, untraced = legibility(ctx, timed)
    rows, short, unplaced, missing_picture = [], [], [], []
    rendered = {a["index"] for a in ctx.acts} - set(sw.missing_acts)
    for sid, text in timed:
        s = shots.get(sid)
        if s is None:
            unplaced.append(f"{sid}: '{text}' (the shot isn't in shots.json)")
            continue
        if ctx.act_of_frame(int(s["frame_in"])) not in rendered:
            missing_picture.append(sid)
            continue
        t0, t1 = ctx.secs_of(int(s["frame_in"])), ctx.secs_of(int(s["frame_out"]))
        hit_times = [t for t, ft in texts if t0 - 0.5 <= t <= t1 + 0.5 and textmatch.present(text, ft)]
        test = legible.get((sid, text))
        if test is not None:  # the trace says when it's opaque and still enough to read
            hit_times = [t for t in hit_times if test(ctx.frame_of(t))]
        longest = textmatch.longest_run(hit_times, every)
        matched = _declared(decls.get(sid), "voice_matched", text)
        need = textmatch.need_seconds(text, per_word=0.0, base=0.0) if matched else textmatch.need_seconds(text)
        since = f" from {hit_times[0]:.2f} s" if hit_times else ""
        row = (f"{sid} '{text}' {longest:.2f} s{since} (needs {need:.2f} s{', voice-matched' if matched else ''}"
               + (", counted only while ≥ 90% opaque and under 8 px a frame" if test is not None else "") + ")")
        rows.append(row)
        if longest + every < need:  # one OCR interval of slack: the run is sampled, not exact
            short.append(row)
    if unplaced:
        subs.append(Sub("unplaced", "FAIL", f"statements in shots that don't exist: {unplaced[0]}", unplaced))
    if short:
        subs.append(Sub("short", "FAIL", f"{plural(len(short), 'statement')} not readable long enough: {short[0]}",
                        short + ["—"] + rows))
    if missing_picture:
        subs.append(Sub("picture", "UNCHECKED", f"shots {', '.join(sorted(set(missing_picture)))} aren't rendered yet",
                        ["next: slate render"]))
    if not short and not unplaced and rows:
        subs.append(Sub("statements", "PASS", f"all {plural(len(rows), 'statement')} stay readable long enough "
                        f"(OCR every {every} s)", rows))
    trace_files = [f"timing/motion/act{a['index']}.json" for a in ctx.acts]
    if untraced and legible:
        details["untraced"] = untraced
    return combine("readtime", subs, engine=gates.engine("slate read-time matcher", __version__, ocr=sw.engine),
                   inputs=ctx.inputs(read + trace_files + _sweep_inputs(ctx, sw)),
                   details={**details, "short": short, "rows": rows, "traced": len(legible),
                            "legible_rule": {"opacity": LEGIBLE_OPACITY, "speed_share_of_short_side": LEGIBLE_SPEED}})


# ---------------------------------------------------------------------------------------------- captions
#: What each caption limit may be called in a format's or film.json's "captions" (first name found wins).
CAPTION_NAMES = {"max_cps": ("max_cps", "cps", "max_chars_per_second"), "max_lines": ("max_lines", "lines"),
                 "max_chars": ("max_chars", "max_chars_per_line", "max_line_chars", "line_length", "chars_per_line")}


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def caption_rules(ctx: FilmContext) -> tuple[dict, str]:
    """The caption rules both caption gates hold a film to (this module's `captions` and the access gate's captions
    check), so the two never disagree about one cue: ({max_cps, max_lines, max_chars, min_seconds}, where from).

    The plan's law (at most 17 characters a second, 2 lines of 42) is tightened, never loosened, by the film's format
    at the film's aspect (a 9:16 cut gets shorter lines) and then by film.json's own "captions". The shortest a cue may
    stay up isn't the plan's law: 1 s, unless the format or film.json says otherwise (min_seconds or min_s).
    """
    rules: dict[str, Any] = {**CAPTION_RULES}
    parts = []
    fmt = _film_format(ctx)
    for label, cap in ((f"the {ctx.film.get('format')} format", (fmt or {}).get("captions")),
                       ("film.json", ctx.film.get("captions"))):
        if not isinstance(cap, dict):
            continue
        tightened = []
        for key, names in CAPTION_NAMES.items():
            given = [v for v in (_number(cap.get(n)) for n in names) if v is not None and v > 0]
            if given and min(given) < rules[key]:
                v = min(given)
                rules[key] = v if key == "max_cps" else int(v)
                tightened.append(f"{key} {v:g}")
        low = next((_number(cap.get(n)) for n in ("min_seconds", "min_s") if _number(cap.get(n)) is not None), None)
        if low is not None and low >= 0:
            rules["min_seconds"] = low
        if tightened:
            parts.append(f"{label} ({', '.join(tightened)})")
    rules["max_cps"] = float(rules["max_cps"])
    source = ("the law (17 characters a second, 2 lines of 42)" if not parts else
              "the law, tightened by " + " and ".join(parts))
    return rules, source


def _caption_rules(ctx) -> dict:
    """caption_rules without where they came from (kept for callers that read the rules alone)."""
    return caption_rules(ctx)[0]


def _film_format(ctx) -> dict | None:
    """The film's format: the run's FilmContext reads it once; anything else that carries film.json is read here."""
    fmt_of = getattr(ctx, "format", None)
    if callable(fmt_of):
        return fmt_of()
    from slate.qa.film import read_format  # noqa: PLC0415

    return read_format(getattr(ctx, "film", None) or {})


def _format_file(ctx: FilmContext) -> str | None:
    """The file the film's format was read from, for the caption gates' inputs (a changed format makes them STALE)."""
    fmt = _film_format(ctx) or {}
    src = ctx.film.get("format_source")
    path = fmt.get("path") or (src if isinstance(src, str) else None)
    return str(path) if path and Path(str(path)).is_file() else None


def captions(film_dir: Path) -> GateResult:
    """At most 2 lines of 42 characters and 17 characters a second, at least 1 s each, ordered, never overlapping,
    and word for word the booth script."""
    ctx = context(film_dir)
    engine = gates.engine("slate captions check", __version__)
    cap = ctx.captions_path()
    spoken = ctx.spoken_lines()
    from slate.qa import film as filmmod  # noqa: PLC0415

    read = ["film.json", "timing/vo_words.json", "script/storyboard.json",
            *([cap] if cap else filmmod.CAPTION_CANDIDATES), _format_file(ctx)]
    if cap is None:
        if spoken:
            return GateResult.unchecked("captions", "the film has spoken lines but no captions yet",
                                        evidence=["next: slate voice captions"], engine=engine, inputs=ctx.inputs(read))
        if ctx.storyboard is None and not ctx.vo_words:
            return GateResult.unchecked("captions", "there's no script or voice timing yet to caption",
                                        evidence=["next: slate script build"], engine=engine, inputs=ctx.inputs(read))
        return combine("captions", [Sub("none", "PASS", "the film has no spoken lines, so it needs no captions")],
                       engine=engine, inputs=ctx.inputs(read))
    rules, rules_from = caption_rules(ctx)
    try:
        cues = textmatch.load_cues(cap)
    except (OSError, ValueError, KeyError) as exc:
        return GateResult.unchecked("captions", f"couldn't read {ctx.rel(cap)}: {exc}", engine=engine,
                                    inputs=ctx.inputs(read))
    problems = []
    for k, c in enumerate(cues):
        dur = c["end"] - c["start"]
        label = f"cue {c['index']} ({c['start']:.2f} s) “{c['text'][:48]}”"
        if len(c["lines"]) > rules["max_lines"]:
            problems.append(f"{label}: {len(c['lines'])} lines (max {rules['max_lines']})")
        long = [ln for ln in c["lines"] if len(ln) > rules["max_chars"]]
        if long:
            problems.append(f"{label}: a line of {max(len(x) for x in long)} characters (max {rules['max_chars']})")
        if dur <= 0:
            problems.append(f"{label}: ends before it starts")
            continue
        cps = len(" ".join(ln.strip() for ln in c["lines"])) / dur
        if cps > rules["max_cps"] + 1e-9:
            problems.append(f"{label}: {cps:.1f} characters a second (max {rules['max_cps']:g})")
        if dur + 1e-6 < rules["min_seconds"]:
            problems.append(f"{label}: on screen {dur:.2f} s (min {rules['min_seconds']:g} s)")
        if k + 1 < len(cues) and cues[k + 1]["start"] < c["end"] - 1e-6:
            problems.append(f"{label}: overlaps the next cue")
        if k and c["start"] < cues[k - 1]["start"]:
            problems.append(f"{label}: out of order")
    subs = []
    if problems:
        subs.append(Sub("rules", "FAIL", f"{plural(len(problems), 'caption problem')}: {problems[0]}", problems[:60]))
    else:
        subs.append(Sub("rules", "PASS", f"{plural(len(cues), 'cue')} within {rules['max_lines']} lines of "
                        f"{rules['max_chars']}, ≤ {rules['max_cps']:g} characters a second, "
                        f"≥ {rules['min_seconds']:g} s"))
    if spoken:
        script_words = " ".join(line for _sid, line in spoken).split()
        caption_words = " ".join(c["text"] for c in cues).split()
        if script_words != caption_words:
            diff = next((f"script “{' '.join(script_words[i1:i2])}” / captions “{' '.join(caption_words[j1:j2])}”"
                         for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=script_words, b=caption_words,
                                                                           autojunk=False).get_opcodes()
                         if op != "equal"), "different")
            subs.append(Sub("script", "FAIL", f"the captions aren't the script word for word: {diff}", [diff]))
        else:
            subs.append(Sub("script", "PASS", f"word for word the script ({plural(len(script_words), 'word')})"))
    else:
        subs.append(Sub("script", "UNCHECKED", "there's no booth script (vo_words.json or storyboard lines) to compare "
                        "the captions with", ["next: slate timing"]))
    return combine("captions", subs, engine=engine, inputs=ctx.inputs(read), details={"file": ctx.rel(cap),
                   "cues": len(cues), "rules": rules, "rules_from": rules_from, "stage": "voice"})


# ---------------------------------------------------------------------------------------------- claims
_DIGIT = re.compile(r"\d")
STORY_CLAIM_GATES = ("script.claims", "script.numbers", "script.quotes")


def _story_claims(ctx: FilmContext, script: dict, pack: dict | None) -> list[GateResult] | None:
    """The story module's own claim, number and quote gates, through its §12 entry point
    check_script(script, *, brand, fmt, truth=None, film=None). None when it isn't installed."""
    try:
        story_gates = importlib.import_module("slate.story.gates")
    except ImportError:
        return None
    fn = getattr(story_gates, "check_script", None)
    if not callable(fn):
        return None
    fmt = ctx.format() or {}  # the film's own format, at its aspect (no format: the story gates use their defaults)
    import inspect  # noqa: PLC0415

    params = inspect.signature(fn).parameters
    kwargs: dict[str, Any] = {"brand": pack or {}, "fmt": fmt}
    if "truth" in params:
        kwargs["truth"] = ctx.read_json("brief/truth.json")
    if "film" in params:
        kwargs["film"] = ctx.film
    engine = gates.engine("slate.story.gates check_script", "unknown")
    try:
        results = [r if isinstance(r, GateResult) else GateResult.from_dict(r) for r in fn(script, **kwargs) or []]
    except Exception as exc:  # noqa: BLE001 - the story module failing is an unchecked claim check, not a crash
        return [GateResult.unchecked("script.claims", f"the story module's claim check couldn't run: "
                                     f"{type(exc).__name__}: {exc}", engine=engine)]
    picked = [r for r in results if r.gate in STORY_CLAIM_GATES]
    if picked:
        return picked
    stopped = "; ".join(f"{r.gate} {r.status}: {r.summary}" for r in results[:3]) or "no results"
    return [GateResult.unchecked("script.claims", f"the story module's checks stopped before the claims ({stopped})",
                                 evidence=[e for r in results for e in r.evidence][:20],
                                 engine=results[0].engine if results else engine)]


def _sources_index(ctx: FilmContext, script: dict) -> set[str]:
    """Pinned source ids: brief/truth.json's sources (and a script-level "sources" list, for older scripts)."""
    ids: set[str] = set()
    truth = ctx.read_json("brief/truth.json") or {}
    for src in (truth.get("sources"), script.get("sources")):
        if isinstance(src, dict):
            ids |= {str(k) for k in src}
        elif isinstance(src, list):
            ids |= {str(x.get("id")) for x in src if isinstance(x, dict) and x.get("id")}
            ids |= {str(x) for x in src if isinstance(x, str)}
    return ids


def _refs(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    out = []
    for r in items:
        ref = r.get("ref") if isinstance(r, dict) else r
        if isinstance(ref, str) and ref.strip():
            out.append(ref.split(":", 1)[0] if not ref.startswith(("http://", "https://")) else ref)
    return out


def claims(film_dir: Path) -> GateResult:
    """Every claim approved or sourced: the story module's claim, number and quote gates when it's installed;
    otherwise every explicit claim and every number said or shown must cite a pinned source (brief/truth.json) or
    match the brand's approved claims. Autonomous films use approved claims only: a sourced but unapproved claim
    parks the film for a person."""
    ctx = context(film_dir)
    read = ["script/script.json", "film.json", "brief/truth.json"]
    script = ctx.script
    engine = gates.engine("slate claims check", __version__)
    if script is None:
        return GateResult.unchecked("claims", "there's no script/script.json to check",
                                    evidence=["next: slate script build"], engine=engine, inputs=ctx.inputs(read))
    pack, where = brandinfo.load(ctx.brand)
    read.append(brandinfo.pack_file(ctx.brand))
    story = _story_claims(ctx, script, pack)
    if story:
        subs = [Sub(r.gate, r.status, f"{r.gate}: {r.summary}", list(r.evidence)) for r in story]
        if pack is None:
            subs.append(Sub("brand", "UNCHECKED", f"the brand pack couldn't be read ({where}), so approved claims and "
                            "the never-claim list weren't part of the check"))
        return combine("claims", subs, engine={**(story[0].engine or {}), "via": "slate.story.gates"},
                       inputs=ctx.inputs(read), details={"brand": where, "mode": ctx.mode})
    truth = ctx.read_json("brief/truth.json") or {}
    approved = {textmatch.squash(t) for c in (brandinfo.approved_claims(pack) if pack else [])
                for t in (brandinfo.claim_text(c), str(c.get("id") or "")) if t}
    approved.discard("")
    truth_claims = {str(c.get("id")): c for c in truth.get("claims") or [] if isinstance(c, dict) and c.get("id")}
    never = [n for n in (brandinfo.never_claim(pack) if pack else []) if n.strip()]
    never += [str(x.get("text") if isinstance(x, dict) else x) for x in truth.get("never_claim") or [] if x]
    index = _sources_index(ctx, script)
    unsourced, bad_refs, sourced_only, ok_rows, never_hits = [], [], [], [], []

    def judge(where_: str, text: str, refs: list[str], explicit_approved: str | None = None) -> None:
        squashed = textmatch.squash(explicit_approved or text)
        if squashed and any(a == squashed or (len(a) > 8 and (a in squashed or squashed in a)) for a in approved):
            ok_rows.append(f"{where_}: approved claim “{text[:70]}”")
            return
        unknown = [r for r in refs if r not in index]
        if unknown:
            bad_refs.append(f"{where_}: cites {', '.join(unknown)}, which brief/truth.json doesn't pin")
            return
        if refs:
            (sourced_only if ctx.mode == "autonomous" else ok_rows).append(f"{where_}: sourced “{text[:70]}”")
            return
        unsourced.append(f"{where_}: “{text[:80]}” has no source and isn't an approved claim")

    for where_, shot in script_shots(script):
        shot_refs = _refs(shot.get("sources")) + _refs(shot.get("source"))
        explicit = list(enumerate(shot.get("claims") or []))
        for i, c in explicit:
            text = _claim_text(c) or ""
            if isinstance(c, str) and c in truth_claims:  # a researched claim by id: sourced by the truth pack
                judge(f"{where_}.claims[{i}]", truth_claims[c].get("text") or c, [str(truth_claims[c].get("source"))])
                continue
            refs = _refs(c.get("source")) if isinstance(c, dict) else []
            judge(f"{where_}.claims[{i}]", text, refs or shot_refs,
                  explicit_approved=c.get("approved") if isinstance(c, dict) else None)
        if explicit or shot.get("sample") or shot.get("sample_data"):
            continue  # the shot names its claims (or is sample data): its numbers are those claims'
        for f in SPOKEN_OR_SHOWN:
            for t in _texts(shot.get(f)):
                if _DIGIT.search(t):
                    judge(f"{where_}.{f}", t, shot_refs)
    for path, text in script_strings(script, SPOKEN_OR_SHOWN):
        for n in never:
            if textmatch.squash(n) and textmatch.squash(n) in textmatch.squash(text):
                never_hits.append(f"{path}: says “{n}”, which the brand never claims")
    subs = []
    if never_hits:
        subs.append(Sub("never_claim", "FAIL", never_hits[0], never_hits))
    if unsourced and pack is None:
        # without the pack, an unsourced claim might still be on its approved list: unknown, not failed
        subs.append(Sub("unsourced", "UNCHECKED", f"{plural(len(unsourced), 'claim')} without a source, and the "
                        f"brand's approved claims can't be read ({where})", unsourced[:40]))
    elif unsourced:
        subs.append(Sub("unsourced", "FAIL", f"{plural(len(unsourced), 'claim')} without a source: {unsourced[0]}",
                        unsourced[:40]))
    if bad_refs:
        subs.append(Sub("sources", "FAIL", bad_refs[0], bad_refs))
    if sourced_only and pack is None:
        subs.append(Sub("approval", "UNCHECKED", "an autonomous film uses approved claims only, and the brand's "
                        f"approved claims can't be read ({where})", sourced_only))
    elif sourced_only:
        subs.append(Sub("approval", "REVIEW", f"{plural(len(sourced_only), 'claim')} sourced but not on the brand's "
                        "approved list: an autonomous film uses approved claims only", sourced_only))
    if not subs:
        subs.append(Sub("claims", "PASS", f"{plural(len(ok_rows), 'claim')} approved or sourced" if ok_rows else
                        "the script makes no claims and states no numbers", ok_rows[:40]))
    return combine("claims", subs, engine=engine, inputs=ctx.inputs(read),
                   details={"brand": where, "approved_claims": len(approved), "mode": ctx.mode})
