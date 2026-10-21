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

Fast narration (a read near 20 characters a second) can't be held to 17 by holding alone: a quick clause needs
time from a slower neighbour. With `fit_pace` on (film.json "captions"), a line the clause chunks can't hold is
re-broken where the reading pace balances (still preferring sentence, then clause, then joining-word breaks), and
every cue may appear up to `lead_s` before its first word or `lag_s` after it, so the reading time is shared
without rewriting a word. Off by default; a cue still too fast is reported, never hidden.

Language rules (sentence and clause punctuation, joining words) come from `slate/voice/languages/<code>.json`;
the frame rate comes from the film's timing; formats and film.json can override the reading limits ("captions").
With `parity=True` the films' own rules apply unchanged (no reading-speed hold; short cues flagged under 0.8 s),
which is how the golden tests reproduce the delivered captions. Files: timing/captions.json (every cue, the rules
and the problems) and voice/captions/<film>.srt and .vtt.
"""

from __future__ import annotations

import difflib
import math
from pathlib import Path

from slate import jsonio
from slate.errors import MissingPrerequisite
from slate.voice import lang

DEFAULTS = {"max_chars": 42, "max_lines": 2, "max_cps": 17.0, "min_s": 1.0, "warn_s": 1.0, "hold_s": 0.35,
            "gap_s": 0.04, "merge_short": True, "short_words": 2, "fit_pace": False, "lead_s": 0.0, "lag_s": 0.0}
PARITY = {"max_cps": None, "warn_s": 0.8}  # the films' own rules: no reading-speed hold, short cues flagged under 0.8 s
#: SRT and WebVTT keep milliseconds, so a cue held to exactly its reading time can round a hair under it (17.02 cps
#: against a 17 limit); the hold carries this much over to stay inside the limit once written.
ROUND_S = 0.002


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
        elif len(ws) >= 4:
            # Too long for two chunks (a clause longer than two full cues, with no comma to break on): split near the
            # middle, preferring a joining word, and split each half again until every chunk fits its lines.
            mid = len(ws) // 2
            ks = sorted(range(2, len(ws) - 1), key=lambda k: abs(k - mid) - (2 if ws[k].casefold() in joins else 0))
            k = ks[0]
            fixed += _long_clause([" ".join(ws[:k]), " ".join(ws[k:])], st, table)
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


# ---------------------------------------------------------------------------------------------------------------
# fit_pace: sharing reading time between neighbouring cues


def _need(text: str, st: dict) -> float:
    """The time a cue needs on screen: its characters at max_cps, never under min_s."""
    return max(len(text) / float(st["max_cps"]) + ROUND_S, float(st["min_s"]))


def _schedule(firsts: list[float], needs: list[float], st: dict, start_at: float = -math.inf,
              end_by: float = math.inf) -> tuple[list[float], list[int], float]:
    """Cue starts as near each cue's first word as the reading time allows.

    Each cue may start up to lead_s before its first word or lag_s after it, never before `start_at`, and runs at
    least its need (gap_s before the next cue); the last one is read by `end_by`. A cue that has to move comes in
    early rather than late: it lags its word only when leading can't make the room. Returns the starts, the cues
    that can't be held (they still keep to their lead and lag, so the post-pass reports the one left short), and when
    the earliest schedule has finished reading."""
    lead, lag, gap = float(st["lead_s"]), float(st["lag_s"]), float(st["gap_s"])
    n = len(firsts)
    early, pull, up = [0.0] * n, [0.0] * n, [0.0] * n
    for k in range(n):
        lo = firsts[k] - lead if k else max(firsts[0] - lead, start_at)
        early[k] = lo if k == 0 else max(lo, early[k - 1] + needs[k - 1] + gap)
    nxt_up = nxt_pull = end_by + gap
    for k in range(n - 1, -1, -1):
        up[k] = min(firsts[k] + lag, nxt_up - needs[k] - gap)
        pull[k] = min(firsts[k], nxt_pull - needs[k] - gap)
        nxt_up, nxt_pull = up[k], pull[k]
    bad = [k for k in range(n) if early[k] > up[k] + 1e-9]
    # Where the room can't be made, every cue still keeps to its lead and lag: the one left short reads too fast,
    # and the post-pass reports it.
    starts = [max(min(max(pull[k], early[k]), up[k]), firsts[k] - lead) for k in range(n)]
    done = early[-1] + needs[-1] if n else start_at
    return starts, bad, done


def _break_cost(toks: list[str], i: int, j: int, st: dict, table: dict) -> int:
    """How badly the chunk toks[i:j] reads: where it breaks (after a sentence 0, a clause 1, before a joining word 3,
    anywhere else 6), each sentence it runs across (8), and a one- or two-word flash (4)."""
    sentence, clause = table.get("sentence_end") or [], table.get("clause_end") or []
    joins = {w.casefold() for w in table.get("join_words") or []}
    cost = 0
    if j < len(toks):
        prev = toks[j - 1]
        cost += (0 if lang.ends_with(prev, sentence) else 1 if lang.ends_with(prev, clause)
                 else 3 if toks[j].casefold() in joins else 6)
    cost += 8 * sum(1 for k in range(i, j - 1) if lang.ends_with(toks[k], sentence))
    short = int(st.get("short_words", 2))
    if st.get("merge_short") and j - i <= short < len(toks):
        cost += 4
    return cost


def _fit_line(toks: list[str], s: list[float], start_at: float, end_by: float, st: dict,
              table: dict) -> list[tuple[int, int]] | None:
    """Word spans for a line whose clause chunks can't be held to max_cps: the best-reading breaks (_break_cost)
    among those that leave every cue its reading time inside the line's window, or None when no breaks can."""
    lead, lag, gap = float(st["lead_s"]), float(st["lag_s"]), float(st["gap_s"])
    n = len(toks)
    if not n or start_at > s[0] + lag + 1e-9:
        return None
    # states[j]: the Pareto front of (earliest start of the cue at word j, cost so far, back-pointer)
    states: dict[int, list[tuple[float, int, tuple | None]]] = {0: [(start_at, 0, None)]}
    for j in range(1, n + 1):
        cands = []
        for i in range(j):
            text = " ".join(toks[i:j])
            if i not in states or not wrap(text, st):
                continue
            for si, (t, cost, _back) in enumerate(states[i]):
                done = t + _need(text, st)
                c = cost + _break_cost(toks, i, j, st, table)
                if j < n:
                    nt = max(s[j] - lead, done + gap)
                    if nt <= s[j] + lag + 1e-9:
                        cands.append((nt, c, (i, si)))
                elif done <= end_by + 1e-9:
                    cands.append((done, c, (i, si)))
        cands.sort(key=lambda x: (x[0], x[1]))
        front, best = [], math.inf
        for x in cands:
            if x[1] < best:
                front.append(x)
                best = x[1]
        if front:
            states[j] = front
    if n not in states:
        return None
    spans, j, node = [], n, min(states[n], key=lambda x: (x[1], x[0]))
    while node[2] is not None:
        i, si = node[2]
        spans.append((i, j))
        j, node = i, states[i][si]
    return spans[::-1]


def _fit_pace(rows: list, st: dict, table: dict) -> list[list[float]]:
    """fit_pace for every line in order: a line whose clause chunks can't be held inside its window (from when the
    line before has been read to just before the next line's first word) is re-broken (_fit_line), then every cue
    is placed (_schedule). Replaces rows' spans in place; returns each row's cue starts."""
    gap = float(st["gap_s"])
    done = -math.inf
    for r, (sid, toks, s, e, spans) in enumerate(rows):
        start_at = max(s[0] - float(st["lead_s"]), done + gap)
        end_by = rows[r + 1][2][0] - gap if r + 1 < len(rows) else math.inf
        needs = [_need(" ".join(toks[i:j]), st) for i, j in spans]
        _starts, bad, done = _schedule([s[i] for i, _j in spans], needs, st, start_at, end_by)
        if bad:
            better = _fit_line(toks, s, start_at, end_by, st, table)
            if better:
                rows[r] = (sid, toks, s, e, better)
                needs = [_need(" ".join(toks[i:j]), st) for i, j in better]
                _starts, _bad, done = _schedule([s[i] for i, _j in better], needs, st, start_at, end_by)
    firsts, needs, owner = [], [], []
    for r, (_sid, toks, s, _e, spans) in enumerate(rows):
        for i, j in spans:
            firsts.append(s[i])
            needs.append(_need(" ".join(toks[i:j]), st))
            owner.append(r)
    starts, _bad, _done = _schedule(firsts, needs, st)
    out: list[list[float]] = [[] for _ in rows]
    for k, r in enumerate(owner):
        out[r].append(starts[k])
    return out


def generate(shots: dict, words: dict, cue_at: dict, fps: float, st: dict, table: dict,
             default_lead: float = 0.4) -> tuple[list[dict], list[str]]:
    """Cues and problems from the film's shots {id: {frame_in}}, words {id: {line, words[{w, start, end}]}}, and
    each read's start inside its shot {id: at}."""
    order = sorted(words, key=lambda k: shots[k]["frame_in"])
    cues: list[dict] = []
    problems: list[str] = []
    rows = []  # per line: (shot, tokens, each token's start and end in film time, word spans [(i, j)])
    for sid in order:
        rec = words[sid]
        line, ws = rec["line"], rec["words"]
        if not ws:
            problems.append(f"{sid}: no word timings")
            continue
        base = shots[sid]["frame_in"] / fps + cue_at.get(sid, default_lead)
        toks = line.split()
        idx = align_map(toks, [w["w"] for w in ws])
        s = [base + ws[idx[k]]["start"] for k in range(len(toks))]
        e = [base + ws[idx[k]]["end"] for k in range(len(toks))]
        spans, i = [], 0
        for ch in chunks_for(line, st, table):
            n = len(ch.split())
            spans.append((i, i + n))
            i += n
        rows.append((sid, toks, s, e, spans))
    max_cps = st.get("max_cps")
    starts = None
    if st.get("fit_pace") and max_cps:
        starts = _fit_pace(rows, st, table)
    for r, (sid, toks, s, e, spans) in enumerate(rows):
        for q, (i, j) in enumerate(spans):
            ch = " ".join(toks[i:j])
            cues.append({"shot": sid, "text": ch, "lines": wrap(ch, st), "start": starts[r][q] if starts else s[i],
                         "end": e[j - 1] + float(st["hold_s"])})
    for k, c in enumerate(cues):
        nxt = cues[k + 1]["start"] if k + 1 < len(cues) else c["end"] + 5
        need = len(c["text"]) / float(max_cps) + ROUND_S if max_cps else 0.0
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


def format_captions(film_json: dict) -> dict:
    """The film's format's caption rules at the film's own aspect: a 9:16 cut of a 16:9 format is captioned with the
    9:16 line length (32 characters, not 42), which is what the QA gate holds it to.

    The format is loaded from where the film got it (film.json "format_source" when that's a format file, else by
    name). A film whose aspect the format doesn't list (a derived cut at a new aspect) takes the shared table's line
    length for its aspect, or for portrait or landscape. Returns {} when there's no format to read."""
    name = film_json.get("format")
    if not name:
        return {}
    try:
        from slate import formats  # noqa: PLC0415
    except ImportError:  # pragma: no cover - the formats module is part of every install
        return {}
    source = film_json.get("format_source")
    ref = source if source and str(source).endswith(".json") and Path(str(source)).is_file() else name
    aspect = film_json.get("aspect")
    try:
        fmt = formats.load(ref, aspect=aspect) if aspect else formats.load(ref)
        return dict(fmt.get("captions") or {})
    except Exception:  # noqa: BLE001 - the format doesn't come in this aspect, or can't be read: try without it
        pass
    try:
        fmt = formats.load(ref)
    except Exception:  # noqa: BLE001 - no such format: the defaults stand
        return {}
    cap = dict(fmt.get("captions") or {})
    width, height = film_json.get("width"), film_json.get("height")
    if aspect or (width and height):
        try:
            table = formats.shared()["captions"]["max_chars_by_aspect"]
            key = str(aspect) if aspect in table else None
            if key is None and width and height:
                g = math.gcd(int(width), int(height))
                key = f"{int(width) // g}:{int(height) // g}"
                if key not in table:
                    key = "portrait" if int(height) > int(width) else "landscape"
            if key in table:
                cap["max_chars"] = min(int(cap.get("max_chars") or table[key]), int(table[key]))
        except Exception:  # noqa: BLE001 - no shared table: the format's own length stands
            pass
    return cap


def settings_for(film_json: dict, table: dict, *, parity: bool = False) -> dict:
    """Reading rules: defaults < the language table < the film's format at its aspect < film.json "captions"."""
    st = dict(DEFAULTS)
    st.update({k: v for k, v in (table.get("reading") or {}).items() if k in DEFAULTS})
    st.update({k: v for k, v in format_captions(film_json).items() if k in DEFAULTS})
    st.update({k: v for k, v in (film_json.get("captions") or {}).items() if k in DEFAULTS})
    if parity:
        st.update(PARITY)
    return st


def gather(film_dir: Path) -> dict:
    """The inputs: timing/shots.json, timing/vo_words.json (or the voice alignments), timing/vo_cues.json."""
    from slate.voice.film import VoiceFilm  # noqa: PLC0415
    from slate.voice.measure import whisper_words  # noqa: PLC0415

    from slate.voice.film import source as film_source  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    timing = {name: film_source(vf.dir, f"timing/{name}") for name in ("shots.json", "vo_words.json", "vo_cues.json")}
    if not timing["shots.json"].is_file():
        raise MissingPrerequisite("Captions need the film's timing (timing/shots.json).", hint="Run `slate timing`.")
    shots_json = jsonio.read(timing["shots.json"])
    shots = {s["id"]: s for s in shots_json.get("shots") or []}
    source = "timing/vo_words.json"
    if timing["vo_words.json"].is_file():
        words = jsonio.read(timing["vo_words.json"]).get("shots") or {}
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
    cue_at = {c["shot"]: float(c["at"]) for c in (jsonio.read(timing["vo_cues.json"], default={}) or {}).get("cues", [])
              if c.get("shot") and c.get("at") is not None}
    for sid in words:
        cue_at.setdefault(sid, vf.lead(sid))
    fps_num = int(shots_json.get("fps_num") or vf.data.get("fps_num"))
    fps_den = int(shots_json.get("fps_den") or vf.data.get("fps_den") or 1)
    return {"film": vf, "shots": shots, "words": words, "cue_at": cue_at, "fps_num": fps_num, "fps_den": fps_den,
            "source": source, "inputs": [p for p in timing.values() if p.is_file()]}


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
    from slate.voice.film import record  # noqa: PLC0415

    inputs = list(g["inputs"])
    if g["source"] == "voice/align":
        inputs += sorted((vf.dir / "voice" / "align").glob("*.json"))
    for path in (out, srt, vtt):
        try:
            record(vf.dir, vf.rel(path), producer="slate voice captions", inputs=[str(p) for p in inputs])
        except Exception as exc:  # noqa: BLE001 - the captions are written either way; say what couldn't be recorded
            from slate import log  # noqa: PLC0415

            log.warn(f"couldn't record {vf.rel(path)} in film.lock.json: {getattr(exc, 'message', exc)}")
    return [out, srt, vtt]
