"""Word lookups on the film clock. They never throw.

Scenes, beats and the timeline anchor moves to spoken words ("the card lands on 'companies'"). The films learned the
hard way that a lookup which throws at module load breaks every composition in a shared bundle (film B's `word()`),
so film C's `cword()` never threw: on a miss it returned an estimate from where the word sits in the script line,
spread across the read, and recorded the miss. The miss list had to be empty before a builder handed in.

This module is that behaviour for Python, on files the timing build writes:

- `align(script_tokens, heard)` lines the script up with what was heard, allowing for spoken forms ("664" heard as
  "six sixty-four", "&" heard as "and") and for different tokenisation ("sixty-four" against "sixty" "four"). It is
  positional, so the "&" in "Sam and Jo run Salt & Stone" lands on the second "and", not the first.
- `resolve_tokens(line, words)` resolves every token of a script line once, with its take-relative span, so the
  engine's TypeScript clock only reads JSON (Python resolves every frame; the kit never re-derives timing).
- `find(words, query, occ)` finds the occ-th time a query is heard (for words that aren't in the script line).
- `word_time(film_dir, shot, word, occ, which)` answers in film seconds and returns (seconds, exact). On a miss it
  estimates, records the miss in timing/word_misses.json, and still returns.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slate import jsonio
from slate.timing import spoken

MISSES_FILE = Path("timing") / "word_misses.json"
MAX_RUN = 6  # the longest run of words one written token can be said as ("a thousand nine hundred and eighty-four")
_cache: dict[str, tuple[float, Any]] = {}


# --- matching keys ----------------------------------------------------------------------------------------------------

def candidates(query: str, lang: str = "en") -> list[tuple[str, int]]:
    """Match targets for a token: (joined match key, number of spoken words), the written form first."""
    out: list[tuple[str, int]] = []
    literal = spoken.norm(spoken.strip_edges(query, lang), lang)
    if literal:
        out.append((literal, 1))
    for form in spoken.forms(query, lang):
        key = "".join(spoken.norm(w, lang) for w in form)
        if key:
            out.append((key, len(form)))
    seen, unique = set(), []
    for key, n in out:
        if key not in seen:
            seen.add(key)
            unique.append((key, n))
    return unique


def _run_matches(keys: list[str], i: int, targets: list[tuple[str, int]], limit: int) -> list[int]:
    """Lengths L such that keys[i:i+L] joined equals one of the targets."""
    lengths = []
    for target, n_words in targets:
        acc = ""
        for length in range(1, min(limit, n_words + 2) + 1):
            if i + length > len(keys):
                break
            acc += keys[i + length - 1]
            if acc == target:
                lengths.append(length)
                break
            if not target.startswith(acc):
                break
    return sorted(set(lengths))


# --- alignment --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Match:
    """Script tokens [s0, s1) were heard as words [h0, h1)."""

    s0: int
    s1: int
    h0: int
    h1: int

    @property
    def split(self) -> bool:
        """Several script tokens heard as one word ("six sixty-four" transcribed as "664")."""
        return self.s1 - self.s0 > 1 and self.h1 - self.h0 == 1

    @property
    def one_to_one(self) -> bool:
        """Each script token has its own heard word(s): one token, or as many tokens as words."""
        return self.s1 - self.s0 == 1 or self.s1 - self.s0 == self.h1 - self.h0


MAX_JOIN = 3  # a number Whisper broke into pieces ("2" ",500,"; "54" "%")


def _moves(i: int, j: int, s_keys: list[str], h_keys: list[str], s_targets: list, h_targets: list,
           joined: dict) -> list[tuple[int, int]]:
    """Ways to match from (i, j), as (script tokens used, heard words used)."""
    out: list[tuple[int, int]] = []
    if not (s_keys[i] and h_keys[j]):
        return out
    # a script token said as a run of heard words ("664" heard as "six" "sixty-four")
    for length in _run_matches(h_keys, j, s_targets[i], MAX_RUN):
        out.append((1, length))
    # script tokens said as one heard word ("twelve" heard as "12"; "six sixty-four" heard as "664")
    for length in _run_matches(s_keys, i, h_targets[j], MAX_RUN):
        out.append((length, 1))
    # a number Whisper wrote in pieces, against one or more script tokens ("twenty-five hundred" / "2" ",500,")
    for n_heard in range(2, MAX_JOIN + 1):
        targets = joined.get((j, n_heard))
        if targets:
            for length in _run_matches(s_keys, i, targets, MAX_RUN):
                out.append((length, n_heard))
    return out


def align(script_tokens: list[str], heard: list[dict], lang: str = "en") -> list[Match]:
    """The best monotonic alignment of a script line's tokens with the heard words.

    A script token matches a run of heard words whose joined keys equal one of its spoken forms; script tokens match a
    heard word when that word's spoken forms say exactly those tokens; and a number Whisper split into pieces matches
    as a whole. The alignment maximises the script tokens accounted for, so repeats ("and", "&", "and") land where
    they're actually said.
    """
    s_keys = [spoken.norm(spoken.strip_edges(t, lang), lang) for t in script_tokens]
    h_keys = [spoken.norm(str(w.get("w", "")), lang) for w in heard]
    n, m = len(script_tokens), len(heard)
    s_targets = [candidates(t, lang) for t in script_tokens]
    h_targets = [candidates(str(w.get("w", "")), lang) for w in heard]
    joined: dict[tuple[int, int], list[tuple[str, int]]] = {}
    for j in range(m):
        for n_heard in range(2, MAX_JOIN + 1):
            if j + n_heard > m:
                break
            text = "".join(str(w.get("w", "")) for w in heard[j:j + n_heard])
            reads_as_number = spoken.forms(text, lang) != [[spoken.strip_edges(text, lang)]]
            if any(ch.isdigit() for ch in text) and reads_as_number:
                joined[(j, n_heard)] = candidates(text, lang)
    # best[i][j]: most script tokens matched in script[i:] x heard[j:]
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            score = max(best[i + 1][j], best[i][j + 1])
            for used_s, used_h in _moves(i, j, s_keys, h_keys, s_targets, h_targets, joined):
                score = max(score, used_s + best[i + used_s][j + used_h])
            best[i][j] = score
    out: list[Match] = []
    i = j = 0
    while i < n and j < m:
        target = best[i][j]
        chosen = None
        for used_s, used_h in _moves(i, j, s_keys, h_keys, s_targets, h_targets, joined):
            if used_s + best[i + used_s][j + used_h] == target:
                chosen = Match(i, i + used_s, j, j + used_h)
                break
        if chosen:
            out.append(chosen)
            i, j = chosen.s1, chosen.h1
        elif best[i][j + 1] == target:
            j += 1
        else:
            i += 1
    return out


# --- lookups in a list of heard words ------------------------------------------------------------------------------------

def find(words: list[dict], query: str, occ: int = 1, lang: str = "en") -> tuple[int, int] | None:
    """Index span [i, j) of the occ-th time `query` is heard in `words` (dicts with "w"), or None."""
    if not isinstance(occ, int) or occ < 1 or not words:
        return None
    targets = candidates(query, lang)
    if not targets:
        return None
    keys = [spoken.norm(str(w.get("w", "")), lang) for w in words]
    own = targets[0][0]
    seen, i = 0, 0
    while i < len(keys):
        lengths = _run_matches(keys, i, targets, MAX_RUN) if keys[i] else []
        if not lengths and keys[i] and not keys[i].isalpha():
            # a heard number or symbol whose spoken form is the query: "12" for "twelve", "%" for "percent"
            if own in {k for k, _ in candidates(str(words[i].get("w", "")), lang)}:
                lengths = [1]
        if lengths:
            seen += 1
            if seen == occ:
                return i, i + max(lengths)
            i += max(lengths)
        else:
            i += 1
    return None


def span_time(words: list[dict], span: tuple[int, int], which: str = "start") -> float:
    i, j = span
    return float(words[i]["start"]) if which == "start" else float(words[j - 1]["end"])


def read_span(words: list[dict], fallback: tuple[float, float]) -> tuple[float, float]:
    """(first word start, last word end) of a read, or the fallback when there are no words yet."""
    if words:
        return float(words[0]["start"]), float(words[-1]["end"])
    return fallback


def estimate(line: str, query: str, occ: int, which: str, span: tuple[float, float], lang: str = "en") -> float:
    """Where a word probably is: its place in the script line, by characters, spread across the read.

    Film C's rule (cword's estimate): the fraction of the line's characters before the word (plus the word itself for
    "end") maps onto the read's span. A word that isn't in the line at all lands on the read's start (or end).
    """
    start, end = span
    wanted = {k for k, _ in candidates(query, lang)}
    total = len(line or "") or 1
    chars, seen = 0, 0
    for tok in (line or "").split():
        if wanted & {k for k, _ in candidates(tok, lang)}:
            seen += 1
            if seen == occ:
                f = (chars + (len(tok) if which == "end" else 0)) / total
                return start + f * (end - start)
        chars += len(tok) + 1
    return end if which == "end" else start


def resolve_tokens(line: str, words: list[dict], span_fallback: tuple[float, float], lang: str = "en") -> list[dict]:
    """Every spoken token of a script line with its take-relative span.

    Each entry: {t, key, occ, start, end, exact, i, j}. `occ` counts repeats of the same key in the line. A token the
    read doesn't account for gets an estimate between its heard neighbours and exact=false; the timing gate lists
    those so a person can listen.
    """
    raw = [tok for tok in (line or "").split() if spoken.is_spoken(tok, lang)]
    matches = align(raw, words, lang)
    by_token: dict[int, Match] = {}
    for mt in matches:
        for s in range(mt.s0, mt.s1):
            by_token[s] = mt
    read0, read1 = read_span(words, span_fallback)
    # character offsets of each token in the joined line, for interpolating the unheard ones
    offsets, pos = [], 0
    for tok in raw:
        offsets.append(pos)
        pos += len(tok) + 1
    out: list[dict] = []
    counts: dict[str, int] = {}
    for k, tok in enumerate(raw):
        text = spoken.strip_edges(tok, lang)
        key = spoken.norm(text, lang)
        counts[key] = counts.get(key, 0) + 1
        entry: dict[str, Any] = {"t": text, "key": key, "occ": counts[key]}
        mt = by_token.get(k)
        if mt is not None and mt.s1 - mt.s0 == 1:
            entry.update(start=round(float(words[mt.h0]["start"]), 3), end=round(float(words[mt.h1 - 1]["end"]), 3),
                         exact=True, i=mt.h0, j=mt.h1)
        elif mt is not None and mt.one_to_one:
            h = mt.h0 + (k - mt.s0)  # as many tokens as heard pieces: pair them in order
            entry.update(start=round(float(words[h]["start"]), 3), end=round(float(words[h]["end"]), 3),
                         exact=True, i=h, j=h + 1)
        elif mt is not None:
            # several script tokens inside fewer heard words: share the span by characters (approximate)
            w0, w1 = float(words[mt.h0]["start"]), float(words[mt.h1 - 1]["end"])
            run = raw[mt.s0:mt.s1]
            total = sum(len(x) for x in run) or 1
            before = sum(len(x) for x in raw[mt.s0:k])
            entry.update(start=round(w0 + (w1 - w0) * before / total, 3),
                         end=round(w0 + (w1 - w0) * (before + len(tok)) / total, 3), exact=False, i=mt.h0, j=mt.h1)
        else:
            prev = next((by_token[p] for p in range(k - 1, -1, -1) if p in by_token), None)
            nxt = next((by_token[q] for q in range(k + 1, len(raw)) if q in by_token), None)
            lo = float(words[prev.h1 - 1]["end"]) if prev else read0
            hi = float(words[nxt.h0]["start"]) if nxt else read1
            p_off = offsets[prev.s1 - 1] + len(raw[prev.s1 - 1]) + 1 if prev else 0
            q_off = offsets[nxt.s0] if nxt else pos
            width = max(q_off - p_off, 1)
            f0 = (offsets[k] - p_off) / width
            f1 = (offsets[k] + len(tok) - p_off) / width
            entry.update(start=round(lo + (hi - lo) * max(0.0, f0), 3), end=round(lo + (hi - lo) * min(1.0, f1), 3),
                         exact=False)
        out.append(entry)
    return out


def token_span(tokens: list[dict], query: str, occ: int = 1, lang: str = "en") -> dict | None:
    """The occ-th script token whose key equals the query's key (the builder's word, as written in the script)."""
    key = spoken.norm(spoken.strip_edges(query, lang), lang)
    if not key or not isinstance(occ, int) or occ < 1:
        return None
    seen = 0
    for tok in tokens or []:
        if tok.get("key") == key:
            seen += 1
            if seen == occ:
                return tok
    return None


def lookup(record: dict, query: str, occ: int = 1, which: str = "start", lang: str = "en") -> tuple[float, bool] | None:
    """Take-relative time of a word in one vo_words record: script tokens first, then anything heard. None on a miss.

    Script tokens come first because a builder writes the script's word ("&"), and the alignment knows which "and"
    that was. Heard words cover spellings the script doesn't use ("664" when the line says "six sixty-four").
    """
    tok = token_span(record.get("tokens") or [], query, occ, lang)
    if tok is not None and tok.get("exact"):
        return float(tok["start" if which == "start" else "end"]), True
    words = record.get("words") or []
    hit = find(words, query, occ, lang)
    if hit:
        return span_time(words, hit, which), True
    return None


# --- film lookups -----------------------------------------------------------------------------------------------------

def _load(path: Path) -> Any:
    """JSON, cached by modification time (word_time is called many times per build)."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    data = jsonio.read(path, default=None)
    _cache[key] = (mtime, data)
    return data


def _timing(film_dir: Path, name: str) -> Path:
    """A timing file, followed into the parent when a derived film reuses it by reference."""
    from slate.timing.takes import film_file  # noqa: PLC0415 (takes brings numpy; lookups load it only when used)

    return film_file(film_dir, Path("timing") / name)


def _film_lang(film_dir: Path) -> str:
    film = _load(film_dir / "film.json") or {}
    lang = film.get("language") or "en"
    try:
        spoken.table(lang)
    except Exception:  # noqa: BLE001 - a lookup never fails over a missing table
        return "en"
    return lang


def word_time(film_dir: str | os.PathLike, shot_id: str, word: str, occ: int = 1,
              which: str = "start") -> tuple[float, bool]:
    """Film seconds at which `word` (its occ-th time in the shot's read) starts or ends, and whether that's exact.

    Never raises. A miss returns an estimate (the word's place in the script line across the read, or across the shot
    when nothing is voiced yet) and is recorded in timing/word_misses.json, which must be empty at hand-in.
    """
    film_dir = Path(film_dir)
    which = "end" if which == "end" else "start"
    try:
        lang = _film_lang(film_dir)
        shots_doc = _load(_timing(film_dir, "shots.json")) or {}
        fps = shots_doc["fps_num"] / shots_doc["fps_den"] if shots_doc.get("fps_num") else None
        shot = next((s for s in shots_doc.get("shots", []) if s.get("id") == shot_id), None)
        if shot is None or not fps:
            _record_miss(film_dir, shot_id, word, occ, which, 0.0, "no such shot in timing/shots.json")
            return 0.0, False
        shot_start = shot["frame_in"] / fps
        cues = _load(_timing(film_dir, "vo_cues.json")) or {}
        cue = next((c for c in cues.get("cues", []) if c.get("shot") == shot_id), None)
        at = float(cue["at"]) if cue else float(shot.get("vo_lead_sec") or 0.0)
        record = ((_load(_timing(film_dir, "vo_words.json")) or {}).get("shots") or {}).get(shot_id) or {}
        n = occ if isinstance(occ, int) and occ >= 1 else 1
        hit = lookup(record, word, occ, which, lang) if isinstance(occ, int) else None
        if hit:
            return shot_start + at + hit[0], True
        words = record.get("words") or []
        tok = token_span(record.get("tokens") or [], word, n, lang)
        if tok is not None:
            est = shot_start + at + float(tok["start" if which == "start" else "end"])
            _record_miss(film_dir, shot_id, word, occ, which, est, "in the script line but not heard in the take")
            return est, False
        # Not in the line and not heard: spread the line across the read (its words, else the take, else the shot).
        if record.get("take_seconds"):
            read_len = float(record["take_seconds"])
        elif cue and cue.get("dur"):
            read_len = float(cue["dur"])
        else:
            read_len = max(0.0, shot["frames"] / fps - at)
        line = record.get("line") or shot.get("vo") or ""
        est = shot_start + at + estimate(line, word, n, which, read_span(words, (0.0, read_len)), lang)
        reason = "no word timings for this shot yet" if not words else "not in the line and not heard in the take"
        _record_miss(film_dir, shot_id, word, occ, which, est, reason)
        return est, False
    except Exception as exc:  # noqa: BLE001 - the contract is: never raise
        try:
            _record_miss(film_dir, shot_id, word, occ, which, 0.0, f"lookup failed: {type(exc).__name__}")
        except Exception:  # noqa: BLE001
            pass
        return 0.0, False


# --- the misses file ----------------------------------------------------------------------------------------------------

def misses_path(film_dir: str | os.PathLike) -> Path:
    return Path(film_dir) / MISSES_FILE


def load_misses(film_dir: str | os.PathLike) -> list[dict]:
    data = jsonio.read(misses_path(film_dir), default=None) or {}
    return list(data.get("misses") or [])


def _write_misses(film_dir: Path, misses: list[dict]) -> None:
    jsonio.write_atomic(misses_path(film_dir), jsonio.mark_generated({
        "note": "Word lookups that fell back to an estimate. This must be empty at hand-in: fix the word, the occurrence "
                "or the take, then rebuild. `slate timing misses` lists them.",
        "misses": misses,
    }, "slate timing (word lookups)"))


def _miss_key(m: dict) -> tuple:
    return (m.get("shot"), str(m.get("word")), m.get("occ"), m.get("which"))


def _record_miss(film_dir: Path, shot_id: str, word: str, occ: Any, which: str, estimate_s: float, reason: str) -> None:
    try:
        if not (film_dir / "timing").is_dir():
            return
        entry = {"shot": shot_id, "word": str(word), "occ": occ, "which": which,
                 "estimate": round(float(estimate_s), 4), "reason": reason,
                 "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
        misses = load_misses(film_dir)
        if any(_miss_key(m) == _miss_key(entry) for m in misses):
            return
        misses.append(entry)
        _write_misses(film_dir, misses)
    except Exception:  # noqa: BLE001 - recording a miss must never break the lookup
        pass


def reset_misses(film_dir: str | os.PathLike) -> None:
    """Start a fresh miss list (a timing rebuild re-resolves every lookup)."""
    _write_misses(Path(film_dir), [])


def record_misses(film_dir: str | os.PathLike, misses: list[dict]) -> None:
    """Add misses found by a build (the timeline's word anchors) in one write."""
    film_dir = Path(film_dir)
    current = load_misses(film_dir)
    keys = {_miss_key(m) for m in current}
    for m in misses:
        if _miss_key(m) not in keys:
            keys.add(_miss_key(m))
            current.append(m)
    _write_misses(film_dir, current)
