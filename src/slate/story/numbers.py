"""Finding the numbers a script says, written ("2,500", "54%") or spelled out ("six sixty-four", "fifty-four percent").

The script gate "no unsourced numbers" needs every number in the VO and on screen. Digits are easy. Spelled-out numbers
are found as runs of number words from the language table; a lone small number ("one number", "four problems") is
ordinary speech rather than a claim, so a spelled run only counts when it has two or more number words, a tens word, a
hundred or scale word, or a percent after it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from slate.timing import spoken


@dataclass
class Mention:
    text: str
    values: list[float] = field(default_factory=list)
    spelled: bool = False
    counts: bool = True  # False for a lone small spelled number: ordinary speech, not a claim


def _trailing_punct(token: str, lang: str) -> bool:
    core = spoken.strip_edges(token, lang)
    return bool(core) and not token.rstrip().endswith(core[-1])


def _percent_after(tokens: list[str], j: int, lang: str) -> int:
    """How many tokens from j say the percent sign (0 when they don't)."""
    keys = [spoken.norm(spoken.strip_edges(t, lang), lang) for t in tokens[j:j + 3]]
    for form in spoken.table(lang)["suffixes"].get("%", []):
        want = [spoken.norm(w, lang) for w in form]
        if keys[:len(want)] == want:
            return len(want)
    return 0


def mentions(text: str, lang: str = "en") -> list[Mention]:
    """Every number said in a piece of text, in order."""
    num = spoken.table(lang)["numbers"]
    conj = spoken.norm(num["conjunction"], lang)
    indefinite = spoken.norm(num["indefinite"], lang)
    tokens = (text or "").split()
    out: list[Mention] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        parsed = spoken.parse_number(tok, lang)
        if parsed is not None:
            out.append(Mention(text=spoken.strip_edges(tok, lang), values=[abs(parsed.value)]))
            i += 1
            continue
        if any(ch.isdigit() for ch in tok) and spoken.forms(tok, lang) != [[spoken.strip_edges(tok, lang)]]:
            # a range such as "400–900": both ends are numbers
            core = spoken.strip_edges(tok, lang)
            values = []
            for mark in num["range_marks"]:
                idx = core.find(mark, 1)
                if idx > 0:
                    a, b = spoken.parse_number(core[:idx], lang), spoken.parse_number(core[idx + len(mark):], lang)
                    if a and b:
                        values = [abs(a.value), abs(b.value)]
                        break
            if values:
                out.append(Mention(text=core, values=values))
                i += 1
                continue
        run: list[str] = []
        j = i
        while j < len(tokens):
            kind = spoken.number_word(tokens[j], lang)
            key = spoken.norm(spoken.strip_edges(tokens[j], lang), lang)
            nxt = spoken.number_word(tokens[j + 1], lang) if j + 1 < len(tokens) else None
            if kind and not (kind == "zero_letter" and not run):
                run.append(tokens[j])
            elif key == indefinite and not run and nxt in ("hundreds", "scale"):
                run.append(tokens[j])
            elif key == conj and run and nxt:
                run.append(tokens[j])
            else:
                break
            j += 1
            if _trailing_punct(tokens[j - 1], lang):
                break  # a sentence or clause ends inside the run: "four. Twenty…" are two numbers
        if not run:
            i += 1
            continue
        kinds = [spoken.number_word(t, lang) for t in run]
        units = [t for t, k in zip(run, kinds) if k == "unit"]
        values = [float(v) for v in spoken.spelled_values(run, lang)]
        big = any(k in ("hundreds", "scale") for k in kinds) or any(
            (spoken.spelled_values([u], lang) or [0])[0] >= 20 for u in units)
        pct = _percent_after(tokens, j, lang) if not _trailing_punct(tokens[j - 1], lang) else 0
        counts = len([k for k in kinds if k in ("unit", "hundreds", "scale")]) >= 2 or big or bool(pct)
        out.append(Mention(text=" ".join(spoken.strip_edges(t, lang) for t in run + tokens[j:j + pct]),
                           values=values, spelled=True, counts=counts))
        i = j + pct
    return out


def values_in(text: str, lang: str = "en") -> set[float]:
    """Every value a piece of text says (claims, sources and sample data allow these)."""
    out: set[float] = set()
    for m in mentions(text, lang):
        out.update(m.values)
    return out


def values_in_data(value: Any, lang: str = "en") -> set[float]:
    """Numbers anywhere in a JSON value (sample data): numbers as-is, strings read for numbers."""
    out: set[float] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(abs(float(value)))
    elif isinstance(value, str):
        out |= values_in(value, lang)
    elif isinstance(value, dict):
        for v in value.values():
            out |= values_in_data(v, lang)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= values_in_data(v, lang)
    return out
