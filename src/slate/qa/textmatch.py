"""Text matching for the text gates: banned words, on-screen statements found by OCR, and caption files.

The statement matcher is the films' read-time matcher (validate_film.py), kept because each of its allowances was
earned on a real master: OCR drops spaces ("Leaderwalks."), misreads a letter in five ("walk8", "DURA81LITY"), garbles
a bold first word on a dark ground, and splits a line wherever the layout does. A statement still counts only once it
is fully built: the whole line closely (≥ 0.9) and its last word, so a line that is still writing on doesn't count.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

SEPARATOR = re.compile(r"\s+·\s+")  # the storyboard joins several on-screen items with " · "
_SQUASH = re.compile(r"[^\w%]", re.UNICODE)


def squash(text: str) -> str:
    """Lower case, letters, digits and % only (OCR drops and adds spaces, so spaces can't matter)."""
    return _SQUASH.sub("", str(text).lower()).replace("_", "")


def norm_word(word: str) -> str:
    return squash(word)


# ---------------------------------------------------------------------------------------------- banned words
def banned_entry(item) -> dict | None:
    """One banned-list entry as {word, forms, instead}. A plain string is a word with no other forms; an object names
    its word (`word` or `text`), its inflections (`forms`: "verifies", "verifying", "verification") and the approved
    substitute (`instead`: a banned "verified" becomes "trusted")."""
    if isinstance(item, dict):
        word = str(item.get("word") or item.get("text") or "").strip()
        forms = [str(f).strip() for f in item.get("forms") or [] if str(f).strip()]
        instead = item.get("instead")
        instead = str(instead).strip() if isinstance(instead, str) and instead.strip() else None
    else:
        word, forms, instead = str(item or "").strip(), [], None
    if not word:
        return None
    return {"word": word, "forms": [f for f in dict.fromkeys(forms) if f.lower() != word.lower()], "instead": instead}


def banned_forms(words) -> dict[str, dict]:
    """{every form, lower case: its entry} for a banned list (strings or entries)."""
    out: dict[str, dict] = {}
    for item in words or []:
        e = banned_entry(item)
        if e is None:
            continue
        for form in (e["word"], *e["forms"]):
            out.setdefault(" ".join(form.lower().split()), e)
    return out


def banned_pattern(words) -> re.Pattern | None:
    """One case-insensitive, whole-word pattern for a banned list, every form of every entry (a phrase matches across
    any whitespace)."""
    parts = []
    for w in sorted(banned_forms(words), key=len, reverse=True):
        parts.append(r"\s+".join(re.escape(tok) for tok in w.split()))
    if not parts:
        return None
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE | re.UNICODE)


def find_banned(text: str, pattern: re.Pattern | None) -> list[str]:
    if pattern is None or not text:
        return []
    return [m.group(0) for m in pattern.finditer(str(text))]


def name_hit(hit: str, forms: dict[str, dict]) -> str:
    """A hit as a failure names it: the word found, the banned word it's a form of, and the approved substitute."""
    e = forms.get(" ".join(hit.lower().split()))
    if e is None:
        return repr(hit)
    text = repr(hit)
    if e["word"].lower() != " ".join(hit.lower().split()):
        text += f" (a form of {e['word']!r})"
    if e.get("instead"):
        text += f" (say {e['instead']!r} instead)"
    return text


# ---------------------------------------------------------------------------------------------- read time
def statement_parts(on_screen) -> list[str]:
    """The on-screen statements to time: each " · "-separated part with parentheses removed, skipping parts that
    describe rather than quote ("then → now"), logo lockups (™ / ®: recognised, not read), bare numbers and single
    words (labels are left to the contact-sheet review)."""
    items = on_screen if isinstance(on_screen, list) else [on_screen]
    out = []
    for item in items:
        if not isinstance(item, str):
            continue
        for part in SEPARATOR.split(item):
            part = re.sub(r"\s*\(.*?\)", "", part).strip()
            if len(part.split()) < 2 or "→" in part or re.search(r"[™®]", part):
                continue
            if re.fullmatch(r"[\d,.+%\s]+", part):
                continue
            out.append(part)
    return out


def need_seconds(text: str, *, floor: float = 1.5, base: float = 0.4, per_word: float = 0.3) -> float:
    """How long a statement must stay readable: max(1.5 s, 0.4 s + 0.3 s per word)."""
    return max(floor, base + per_word * len(text.split()))


def near(sub: str, text: str, thr: float) -> bool:
    if sub in text:
        return True
    sm = difflib.SequenceMatcher(None)
    sm.set_seq2(sub)  # SequenceMatcher caches what it learns about seq2, so the fixed string goes there
    for i in range(0, max(1, len(text) - len(sub) + 1)):
        sm.set_seq1(text[i:i + len(sub)])
        if sm.quick_ratio() >= thr and sm.ratio() >= thr:
            return True
    return False


def _allow(word: str) -> int:
    return max(1, len(word) // 5)


def has_word(word: str, text: str) -> bool:
    """The word, with at most one misread letter per five ("walk8" is "walks"; "conditions" is not "conviction")."""
    if word in text:
        return True
    allow = _allow(word)
    return any(sum(a != b for a, b in zip(word, text[i:i + len(word)])) <= allow
               for i in range(len(text) - len(word) + 1))


def word_at(word: str, text: str, start: int) -> list[int]:
    allow = _allow(word)
    return [i for i in range(start, len(text) - len(word) + 1)
            if sum(a != b for a, b in zip(word, text[i:i + len(word)])) <= allow]


def in_sequence(words: list[str], text: str, gap: int = 3) -> bool:
    """The words in order, each within `gap` letters of the last (so "wide… the gap" in a caption isn't "Wide gap")."""
    if not words:
        return False

    def rest(k: int, pos: int) -> bool:
        if k == len(words):
            return True
        return any(rest(k + 1, i + len(words[k])) for i in word_at(words[k], text, pos) if i - pos <= gap)

    return any(rest(1, i + len(words[0])) for i in word_at(words[0], text, 0))


def present(statement: str, frame_text: str) -> bool:
    """Is the statement fully built in this frame's OCR text (already squashed)?"""
    words = [squash(w) for w in statement.split() if len(squash(w)) >= 3]
    last = squash(statement.split()[-1]) if statement.split() else ""
    if near(squash(statement), frame_text, 0.9) and (len(last) < 3 or has_word(last, frame_text)):
        return True
    if len(words) >= 2 and in_sequence(words, frame_text):
        return True
    # a line whose bold first word OCR garbled: the rest, in order and side by side, still has to be there
    return len(words) >= 3 and in_sequence(words[1:], frame_text)


def longest_run(times: list[float], every: float) -> float:
    """The longest run of consecutive samples (each `every` apart) in a sorted list of sample times."""
    longest, run, prev = 0.0, 0.0, None
    for t in times:
        run = run + every if prev is not None and abs(t - prev - every) < 1e-6 else every
        longest = max(longest, run)
        prev = t
    return longest


# ---------------------------------------------------------------------------------------------- captions
_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")


def _secs(stamp: str) -> float:
    m = _TS.search(stamp)
    if not m:
        raise ValueError(f"not a timestamp: {stamp!r}")
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt_vtt(text: str) -> list[dict]:
    """Cues from SRT or WebVTT text: [{index, start, end, lines, text}]."""
    cues = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n")]
        k = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if k is None:
            continue
        a, _, b = lines[k].partition("-->")
        body = [ln for ln in lines[k + 1:] if ln.strip()]
        body = [re.sub(r"<[^>]+>", "", ln) for ln in body]  # VTT/SRT styling tags aren't characters on screen
        cues.append({"index": len(cues) + 1, "start": _secs(a), "end": _secs(b.split()[0] if b.split() else b),
                     "lines": body, "text": " ".join(body)})
    return cues


def load_cues(path: str | Path) -> list[dict]:
    """Caption cues from captions.json ([{text, lines, start, end}] or {"cues": […]}), .srt or .vtt."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("cues") if isinstance(data, dict) else data
        cues = []
        for i, c in enumerate(items or [], start=1):
            if not isinstance(c, dict):
                continue
            lines = c.get("lines")
            if not isinstance(lines, list) or not lines:
                lines = str(c.get("text") or "").split("\n")
            cues.append({"index": i, "start": float(c["start"]), "end": float(c["end"]),
                         "lines": [str(x) for x in lines], "text": str(c.get("text") or " ".join(lines)),
                         "shot": c.get("shot")})
        return cues
    return parse_srt_vtt(p.read_text(encoding="utf-8", errors="replace"))
