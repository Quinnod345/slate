"""Word timings for one take: what the aligner heard, snapped to the take's own onsets.

The films' vo_words.py, generalized:

- Every token the aligner returns is kept, as the films kept them: a "%" Whisper writes on its own is the word
  "percent", with its own timing. Punctuation with no letters (a dash) stays too; it simply never matches a lookup.
- Starts are snapped to the take's energy onset within ±80 ms (a clear rise of at least 3 dB on a 5 ms envelope).
- A number Whisper wrote as digits where the script spells it out ("664." for "six sixty-four") is split into its
  spoken words, as film C did by hand for C05 and C19: the words come from the number's spoken form, lower case, with
  the token's trailing punctuation on the last one; a two-word split falls 38% into the token (film C's measured
  boundary, exact on all three of its splits), longer ones by each word's share of the letters. The first word keeps
  the token's measured start; later words carry `snap: null`, which marks a start that was placed, not measured.

Times are seconds from the take's first sample. `timing/shots.json` places each take in the film.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from slate.timing import audio, spoken
from slate.timing.words import align, candidates, resolve_tokens

TWO_WORD_SHARE = 0.38  # film C: "six | sixty-four" and "six | seventy-one" split 38% into the transcribed token


def heard_tokens(result: dict) -> list[dict]:
    """The aligner's words as [{w, start, end}] from a Whisper-shaped result ({segments: [{words: [...]}]})."""
    out = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            out.append({"w": str(w.get("word", w.get("w", ""))).strip(), "start": float(w["start"]),
                        "end": float(w["end"])})
    return out


def words_for_take(result: dict, line: str, db: np.ndarray | None, step: float, lang: str = "en",
                   two_word_share: float = TWO_WORD_SHARE) -> tuple[list, str]:
    """(words, heard) for one take from an aligner result, the script line and the take's envelope."""
    tokens = heard_tokens(result)
    heard = " ".join(t["w"] for t in tokens)
    words: list[dict[str, Any]] = []
    for t in tokens:
        start, shift = (audio.snap_onset(db, step, t["start"]) if db is not None else (t["start"], 0.0))
        words.append({"w": t["w"], "start": round(start, 3), "end": round(t["end"], 3),
                      "whisper_start": round(t["start"], 3), "snap": round(shift, 3)})
    return split_spoken(words, line, lang, two_word_share), heard


def _trailing(text: str, lang: str) -> str:
    core = spoken.strip_edges(text, lang)
    if not core:
        return ""
    idx = text.rfind(core[-1])
    return text[idx + 1:] if idx >= 0 else ""


def _spoken_parts(heard_word: str, script_run: list[str], lang: str) -> list[str]:
    """The words a transcribed number splits into: the spoken form that says the script's run, lower case."""
    want = "".join(spoken.norm(spoken.strip_edges(t, lang), lang) for t in script_run)
    for form in spoken.forms(heard_word, lang):
        if len(form) == len(script_run) and "".join(spoken.norm(w, lang) for w in form) == want:
            return list(form)
    return [spoken.strip_edges(t, lang) for t in script_run]


def split_bounds(start: float, end: float, parts: list[str], lang: str, two_word_share: float) -> list[float]:
    """Inner boundaries for a split token: 38% in for two words, else each word's share of the letters."""
    if len(parts) < 2:
        return []
    if len(parts) == 2:
        return [start + two_word_share * (end - start)]
    weights = [float(len(spoken.norm(p, lang)) or 1) for p in parts]
    total, acc, out = sum(weights), 0.0, []
    for w in weights[:-1]:
        acc += w
        out.append(start + (end - start) * acc / total)
    return out


def split_spoken(words: list[dict], line: str, lang: str = "en", two_word_share: float = TWO_WORD_SHARE) -> list[dict]:
    """Split heard tokens that the script says as several words ("664." -> "six" "sixty-four.")."""
    script = [tok for tok in (line or "").split() if spoken.is_spoken(tok, lang)]
    matches = {m.h0: m for m in align(script, words, lang) if m.split}
    if not matches:
        return words
    out: list[dict] = []
    for idx, w in enumerate(words):
        m = matches.get(idx)
        if m is None or len(candidates(w["w"], lang)) < 2:
            out.append(w)
            continue
        parts = _spoken_parts(w["w"], script[m.s0:m.s1], lang)
        tail = _trailing(w["w"], lang)
        parts[-1] = parts[-1] + tail
        start, end = float(w["start"]), float(w["end"])
        edges = [start] + [round(b, 3) for b in split_bounds(start, end, parts, lang, two_word_share)] + [end]
        for k, part in enumerate(parts):
            out.append({"w": part, "start": round(edges[k], 3), "end": round(edges[k + 1], 3),
                        "whisper_start": w.get("whisper_start", w["start"]),
                        "snap": w.get("snap") if k == 0 else None})
    return out


def record_for_take(*, take_rel: str, line: str, result: dict, take_path, lang: str = "en",
                    take_seconds: float | None = None, two_word_share: float = TWO_WORD_SHARE) -> dict:
    """One shot's entry in vo_words.json: {take, line, heard, words, tokens, take_seconds}."""
    db, step = None, audio.HOP / audio.RATE
    if take_path is not None:
        db, step = audio.envelope(audio.decode_mono(take_path))
    words, heard = words_for_take(result, line, db, step, lang, two_word_share)
    span_fallback = (0.0, float(take_seconds or (words[-1]["end"] if words else 0.0)))
    rec = {"take": take_rel, "line": line, "heard": heard, "words": words,
           "tokens": resolve_tokens(line, words, span_fallback, lang)}
    if take_seconds:
        rec["take_seconds"] = round(float(take_seconds), 3)
    return rec


def unheard(record: dict) -> list[str]:
    """Script tokens the read doesn't account for (Whisper heard something else, or the word is missing)."""
    return [t["t"] for t in record.get("tokens") or [] if not t.get("exact")]
