"""Captions timed to the actual words: SRT, WebVTT and timing/captions.json.

Generalized from the films' `captions.py`. Text comes from the script (each shot's line in timing/vo_words.json);
timing comes from the same word timings picture and sound are cut to, placed in film time by timing/vo_cues.json
and timing/shots.json. Each cue is a sentence or clause that fits the reading rules:

- at most 2 lines of 42 characters (balanced, breaking before a joining word where it can);
- at most 17 characters a second: a cue is held longer into the free time before the next one, and any cue still
  faster than that is reported as a problem;
- shown from its first word to its last word plus a short hold, never shorter than 1.0 s, never overlapping the next;
- one- or two-word flashes fold into a neighbour (merge_short);
- the captions reproduce the script word for word.

Language rules (sentence and clause punctuation, joining words) come from `slate/voice/languages/<code>.json`;
the frame rate comes from the film's timing; formats and film.json can override the reading limits ("captions").
With `parity=True` the films' own rules apply unchanged (no reading-speed hold; short cues flagged under 0.8 s),
which is how the golden tests reproduce the delivered captions. Files: timing/captions.json (every cue, the rules
and the problems) and voice/captions/<film>.srt and .vtt.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from slate import jsonio
from slate.errors import MissingPrerequisite
from slate.voice import lang

DEFAULTS = {"max_chars": 42, "max_lines": 2, "max_cps": 17.0, "min_s": 1.0, "warn_s": 1.0, "hold_s": 0.35,
            "gap_s": 0.04, "merge_short": True, "short_words": 2}
PARITY = {"max_cps": None, "warn_s": 0.8}  # the films' own rules: no reading-speed hold, short cues flagged under 0.8 s


# ---------------------------------------------------------------------------------------------------------------
# The rules (a faithful port: the golden tests compare against the films' delivered captions)


def wrap(text: str, st: dict) -> list[str] | None:
    """Split a chunk into at most `max_lines` balanced lines of at most `max_chars`; None if it can't fit."""
    maxc, lines = int(st["max_chars"]), int(st["max_lines"])
    if len(text) <= maxc:
        return [text]
    if lines <= 1:
        return None
    ws = text.split()
    best = None
    for k in range(1, len(ws)):
        a, b = " ".join(ws[:k]), " ".join(ws[k:])
        if lines == 2:
            if len(a) <= maxc and len(b) <= maxc:
                score = abs(len(a) - len(b))
                if best is None or score < best[0]:
                    best = (score, [a, b])
        elif len(a) <= maxc:
            rest = wrap(b, {**st, "max_lines": lines - 1})
            if rest:
                cand = [a, *rest]
                score = max(len(x) for x in cand) - min(len(x) for x in cand)
                if best is None or score < best[0]:
                    best = (score, cand)
    return best[1] if best else None


def align_map(script: list[str], heard: list[str]) -> list[int]:
    """Map each script token to a timed word. Equal runs map one to one; where the transcript tokenized differently
    ("mid-sized" vs "mid" "sized", "twenty-five hundred" vs "2,500") tokens spread proportionally across the
    differing run, so a chunk still starts and ends on the right words."""
    a = [lang.norm_token(x) for x in script]
    b = [lang.norm_token(x) for x in heard]
    m = [0] * len(a)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                m[i1 + k] = j1 + k
        elif i2 > i1:
            span = max(j2 - j1, 1)
            for k in range(i2 - i1):
                m[i1 + k] = min(len(b) - 1, max(0, j1 + int(round(k * (span - 1) / max(i2 - i1 - 1, 1)))
                                                if j2 > j1 else j1 - 1 if k == 0 else j1))
    for k in range(1, len(m)):
        m[k] = max(m[k], m[k - 1])
    return m


def _long_clause(out: list[str], st: dict, table: dict) -> list[str]:
    # A clause still too long for its lines (no comma to break on) splits at the word boundary that best balances the
    # halves, preferring a break before a joining word so each half reads whole.
    joins = {w.casefold() for w in table.get("join_words") or []}
    bonus = int(table.get("join_bonus", 25))
    fixed: list[str] = []
    for c in out:
        if wrap(c, st):
            fixed.append(c)
            continue
        ws = c.split()
        cands = []
        for k in range(2, len(ws) - 1):
            a, b = " ".join(ws[:k]), " ".join(ws[k:])
            if wrap(a, st) and wrap(b, st):
                cands.append((abs(len(a) - len(b)) - (bonus if ws[k].casefold() in joins else 0), a, b))
        if cands:
            _, a, b = min(cands)
            fixed += [a, b]
        else:
            fixed.append(c)
    return fixed


def _chunks(line: str, st: dict, table: dict) -> list[str]:
    """Break a line into chunks at sentence, then clause, boundaries so each fits its lines."""
    out: list[str] = []
    for s in lang.split_after(line, table.get("sentence_end") or []):
        if wrap(s, st):
            out.append(s)
            continue
        cur = ""
        for p in lang.split_after(s, table.get("clause_end") or []):
            cand = (cur + " " + p).strip()
            if wrap(cand, st):
                cur = cand
            else:
                if cur:
                    out.append(cur)
                cur = p
        if cur:
            out.append(cur)
    return _long_clause(out, st, table)


def _chunks_split(text: str, st: dict, table: dict) -> list[str]:
    return [text] if wrap(text, st) else _long_clause([text], st, table)


def chunks_for(line: str, st: dict, table: dict) -> list[str]:
    """A line's caption chunks. With merge_short there's no one- or two-word flash: a very short chunk joins the
    chunk after it (or before it, at the end of a line), re-split when it no longer fits."""
    out = _chunks(line, st, table)
    if not st.get("merge_short"):
        return out
    short = int(st.get("short_words", 2))
    k = 0
    while k < len(out):
        if len(out[k].split()) <= short and len(out) > 1:
            if k + 1 < len(out):
                merged = _chunks_split(out[k] + " " + out[k + 1], st, table)
                out[k:k + 2] = merged
            else:
                merged = _chunks_split(out[k - 1] + " " + out[k], st, table)
                out[k - 1:k + 1] = merged
                k -= 1
            if len(merged) > 1 and len(merged[0].split()) <= short:
                k += 1  # it couldn't be absorbed without breaking the line rule; leave it
                continue
        k += 1
    return out


def generate(shots: dict, words: dict, cue_at: dict, fps: float, st: dict, table: dict,
             default_lead: float = 0.4) -> tuple[list[dict], list[str]]:
    """Cues and problems from the film's shots {id: {frame_in}}, words {id: {line, words[{w, start, end}]}}, and
    each read's start inside its shot {id: at}."""
    order = sorted(words, key=lambda k: shots[k]["frame_in"])
    cues: list[dict] = []
    problems: list[str] = []
    for sid in order:
        rec = words[sid]
        line, ws = rec["line"], rec["words"]
        if not ws:
            problems.append(f"{sid}: no word timings")
            continue
        base = shots[sid]["frame_in"] / fps + cue_at.get(sid, default_lead)
        idx = align_map(line.split(), [w["w"] for w in ws])
        i = 0
        for ch in chunks_for(line, st, table):
            n = len(ch.split())
            j0, j1 = idx[i], idx[i + n - 1]
            i += n
            cues.append({"shot": sid, "text": ch, "lines": wrap(ch, st), "start": base + ws[j0]["start"],
                         "end": base + ws[j1]["end"] + float(st["hold_s"])})
    max_cps = st.get("max_cps")
    for k, c in enumerate(cues):
        nxt = cues[k + 1]["start"] if k + 1 < len(cues) else c["end"] + 5
        need = len(c["text"]) / float(max_cps) if max_cps else 0.0
        c["end"] = min(max(c["end"], c["start"] + float(st["min_s"]), c["start"] + need), nxt - float(st["gap_s"]))
        dur = c["end"] - c["start"]
        c["cps"] = round(len(c["text"]) / dur, 2) if dur > 0 else None
        if dur < float(st["warn_s"]) - 1e-9:
            problems.append(f"{c['shot']}: '{c['text']}' only {dur:.2f} s")
        if c["lines"] is None:
            problems.append(f"{c['shot']}: '{c['text']}' doesn't fit {st['max_lines']} lines of {st['max_chars']}")
        if max_cps and dur > 0 and len(c["text"]) / dur > float(max_cps) + 1e-9:
            problems.append(f"{c['shot']}: '{c['text']}' reads at {len(c['text']) / dur:.1f} characters a second "
                            f"(limit {max_cps:g}; the next caption leaves no time to hold it)")
    script = " ".join(words[s]["line"] for s in order)
    if script.split() != " ".join(c["text"] for c in cues).split():
        problems.append("caption text differs from the script")
    return cues, problems


def timestamp(sec: float, vtt: bool = False) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{'.' if vtt else ','}{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    return "".join(f"{k}\n{timestamp(c['start'])} --> {timestamp(c['end'])}\n" + "\n".join(c["lines"] or [c["text"]])
                   + "\n\n" for k, c in enumerate(cues, 1))


def to_vtt(cues: list[dict]) -> str:
    return "WEBVTT\n\n" + "".join(f"{k}\n{timestamp(c['start'], True)} --> {timestamp(c['end'], True)}\n"
                                  + "\n".join(c["lines"] or [c["text"]]) + "\n\n" for k, c in enumerate(cues, 1))


# ---------------------------------------------------------------------------------------------------------------
# A film's captions


def settings_for(film_json: dict, table: dict, *, parity: bool = False) -> dict:
    """Reading rules: defaults < the language table < the film's format < film.json "captions"."""
    st = dict(DEFAULTS)
    st.update({k: v for k, v in (table.get("reading") or {}).items() if k in DEFAULTS})
    fmt_name = film_json.get("format")
    if fmt_name:
        try:
            from slate import formats  # noqa: PLC0415
            fmt = formats.load(fmt_name) if hasattr(formats, "load") else {}
        except Exception:  # noqa: BLE001 - formats not installed yet, or no such format: the defaults stand
            fmt = {}
        st.update({k: v for k, v in ((fmt or {}).get("captions") or {}).items() if k in DEFAULTS})
    st.update({k: v for k, v in (film_json.get("captions") or {}).items() if k in DEFAULTS})
    if parity:
        st.update(PARITY)
    return st


def gather(film_dir: Path) -> dict:
    """The inputs: timing/shots.json, timing/vo_words.json (or the voice alignments), timing/vo_cues.json."""
    from slate.voice.film import VoiceFilm  # noqa: PLC0415
    from slate.voice.measure import whisper_words  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    timing = vf.dir / "timing"
    if not (timing / "shots.json").is_file():
        raise MissingPrerequisite("Captions need the film's timing (timing/shots.json).", hint="Run `slate timing`.")
    shots_json = jsonio.read(timing / "shots.json")
    shots = {s["id"]: s for s in shots_json.get("shots") or []}
    source = "timing/vo_words.json"
    if (timing / "vo_words.json").is_file():
        words = jsonio.read(timing / "vo_words.json").get("shots") or {}
    else:
        source = "voice/align"
        words = {}
        for ln in vf.lines:
            doc = jsonio.read(vf.dir / "voice" / "align" / f"{ln.shot}.json", default=None)
            if doc:
                words[ln.shot] = {"line": ln.text, "words": [{"w": w["word"], "start": w["start"], "end": w["end"]}
                                                             for w in whisper_words(doc)]}
    if not words:
        raise MissingPrerequisite("No word timings yet, so there's nothing to caption.",
                                  hint="Run `slate voice align`, then `slate timing`.")
    missing = [s for s in words if s not in shots]
    if missing:
        raise MissingPrerequisite(f"Word timings for shots the timing doesn't have: {', '.join(missing)}.",
                                  hint="Run `slate timing` again.")
    cue_at = {c["shot"]: float(c["at"]) for c in (jsonio.read(timing / "vo_cues.json", default={}) or {}).get("cues", [])
              if c.get("shot") and c.get("at") is not None}
    for sid in words:
        cue_at.setdefault(sid, vf.lead(sid))
    fps_num = int(shots_json.get("fps_num") or vf.data.get("fps_num"))
    fps_den = int(shots_json.get("fps_den") or vf.data.get("fps_den") or 1)
    return {"film": vf, "shots": shots, "words": words, "cue_at": cue_at, "fps_num": fps_num, "fps_den": fps_den,
            "source": source}


def build(film_dir: str | Path, *, parity: bool = False) -> list[Path]:
    """Write timing/captions.json, voice/captions/<film>.srt and .vtt; returns their paths.
    Problems (a cue too fast, too long or too short, text off the script) are listed in captions.json."""
    g = gather(Path(film_dir))
    vf = g["film"]
    table = lang.load(vf.language)
    st = settings_for(vf.data, table, parity=parity)
    fps = g["fps_num"] / g["fps_den"]
    cues, problems = generate(g["shots"], g["words"], g["cue_at"], fps, st, table,
                              default_lead=float(vf.profile["fit"]["lead_s"]))
    folder = vf.dir / "timing"
    # The machine file sits with the film's timing; the subtitle files sit with the voice, where QA and delivery look.
    srt = jsonio.write_text_atomic(vf.dir / "voice" / "captions" / f"{vf.id}.srt", to_srt(cues))
    vtt = jsonio.write_text_atomic(vf.dir / "voice" / "captions" / f"{vf.id}.vtt", to_vtt(cues))
    data = {"generated": "do not edit by hand: slate voice captions", "film": vf.id, "language": vf.language,
            "fps_num": g["fps_num"], "fps_den": g["fps_den"], "settings": st, "source": g["source"],
            "files": {"srt": vf.rel(srt), "vtt": vf.rel(vtt)}, "cues": cues, "problems": problems,
            "script_exact": "caption text differs from the script" not in problems}
    problems_schema = jsonio.validate(data, "captions")
    if problems_schema:  # pragma: no cover - a bug here, not in the film
        raise MissingPrerequisite("captions.json doesn't match its schema: " + "; ".join(problems_schema[:5]))
    out = jsonio.write_atomic(folder / "captions.json", data)
    return [out, srt, vtt]
