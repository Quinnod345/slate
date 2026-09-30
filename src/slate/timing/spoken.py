"""How numbers and symbols are said, so the clock can find them in a read.

A script says "664"; the voice says "six sixty-four"; Whisper may write either. A script says "Salt & Stone"; the voice
says "and". `forms(token)` lists the ways a token is spoken, most usual first, and the word lookup (`timing.words`)
accepts any of them. Film C's clock did this for three-digit scores in English only; this module does it for any
integer, decimals, percentages, currency, ordinals, ranges and symbols, in whatever language has a table.

Every word lives in the language table (`lang/<lang>.json`). This file holds only structure (groups of three digits,
tens and ones, where a scale word goes), so a second language is a second table, not new code. The same rules are
pinned in `tests/vectors/spoken.json`, which pytest and the engine's vitest both check, so Python and TypeScript say
numbers the same way.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from slate.errors import UsageError

LANG_DIR = Path(__file__).resolve().parent / "lang"
MAX_FORMS = 16  # enough alternatives for any real read; keeps a range of ranges from exploding


@lru_cache(maxsize=None)
def table(lang: str = "en") -> dict:
    """The language table for `lang` (an ISO code such as "en")."""
    path = LANG_DIR / f"{lang}.json"
    if not path.is_file():
        known = ", ".join(languages()) or "none"
        raise UsageError(f"Slate has no language table for {lang!r} (known: {known}).",
                         hint=f"Add {path} with the same keys as lang/en.json.")
    return json.loads(path.read_text(encoding="utf-8"))


def languages() -> list[str]:
    return sorted(p.stem for p in LANG_DIR.glob("*.json"))


def norm(text: str, lang: str = "en") -> str:
    """A word's match key: NFKC, lower case, letters and digits plus the table's kept symbols ("Sixty-four." -> "sixtyfour")."""
    keep = table(lang)["normalize"]["keep"]
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in folded if ch.isalnum() or ch in keep)


def _symbol_chars(lang: str) -> set[str]:
    tab = table(lang)
    chars = set(tab["normalize"]["keep"])
    for key in list(tab["symbols"]) + list(tab["suffixes"]) + list(tab["currency"]):
        chars.update(key)
    return chars


def strip_edges(token: str, lang: str = "en") -> str:
    """Drop punctuation around a token ("(664)." -> "664"), keeping symbols that are spoken ("54%", "$15", "&")."""
    keep = _symbol_chars(lang)
    negatives = set(_num(lang)["negative_marks"])
    text = unicodedata.normalize("NFKC", token or "").strip()
    start, end = 0, len(text)
    while start < end and not (text[start].isalnum() or text[start] in keep):
        if text[start] in negatives and start + 1 < end and text[start + 1].isdigit():
            break  # "-5" keeps its sign
        start += 1
    while end > start and not (text[end - 1].isalnum() or text[end - 1] in keep):
        end -= 1
    return text[start:end]


def is_spoken(token: str, lang: str = "en") -> bool:
    """True when a token is heard as a word: it has a letter or digit, or it's a symbol the table says aloud ("&")."""
    t = strip_edges(token, lang)
    if not t:
        return False
    return any(ch.isalnum() for ch in t) or bool(symbol_forms(t, lang))


def word_count(text: str, lang: str = "en") -> int:
    """Words a person hears in a line, counted the way the films counted pace (one per spoken token)."""
    return sum(1 for tok in (text or "").split() if is_spoken(tok, lang))


# --- saying integers -----------------------------------------------------------------------------------------------

def _num(lang: str) -> dict:
    return table(lang)["numbers"]


def below_hundred(n: int, lang: str = "en") -> str:
    """0-99 as one word ("sixty-four"); a table can override any of them in numbers.words."""
    num = _num(lang)
    special = num.get("words") or {}
    if str(n) in special:
        return special[str(n)]
    ones, tens = num["ones"], num["tens"]
    if n < len(ones):
        return ones[n]
    t, o = divmod(n, 10)
    return tens[t] + (num["tens_joiner"] + ones[o] if o else "")


def _group(n: int, lang: str, conj: bool) -> list[str]:
    """1-999 as words: [six, hundred, sixty-four] or, with conj, [six, hundred, and, sixty-four]."""
    num = _num(lang)
    h, r = divmod(n, 100)
    out: list[str] = []
    if h:
        out += [below_hundred(h, lang), num["hundred"]]
    if r:
        if h and conj:
            out.append(num["conjunction"])
        out.append(below_hundred(r, lang))
    return out


def cardinal(n: int, lang: str = "en", conj: bool = False) -> list[str]:
    """A non-negative integer as words, by groups of three digits with the table's scale words."""
    num = _num(lang)
    if n == 0:
        return [num["ones"][0]]
    groups: list[int] = []
    rest = n
    while rest:
        rest, g = divmod(rest, 1000)
        groups.append(g)
    scales = num["scales"]
    if len(groups) - 1 > len(scales):
        return digit_words(n, lang)
    out: list[str] = []
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if not g:
            continue
        words = _group(g, lang, conj)
        if idx == 0 and conj and len(groups) > 1 and g < 100:
            words = [num["conjunction"]] + words
        out += words
        if idx:
            out.append(scales[idx - 1])
    return out


def pairs(n: int, lang: str = "en") -> list[str] | None:
    """Scores and years said in pairs: 664 -> [six, sixty-four], 1905 -> [nineteen, oh, five], 2500 -> [twenty-five, hundred]."""
    if not 100 <= n <= 9999:
        return None
    num = _num(lang)
    head, tail = divmod(n, 100)
    if tail == 0:
        if n % 1000 == 0:
            return None  # "two thousand" is the cardinal; there's no pair reading
        return [below_hundred(head, lang), num["hundred"]]
    if tail < 10:
        return [below_hundred(head, lang), num["zero_letter"], below_hundred(tail, lang)]
    return [below_hundred(head, lang), below_hundred(tail, lang)]


def digit_words(n: int | str, lang: str = "en", zero_letter: bool = False) -> list[str]:
    """Digit by digit: 664 -> [six, six, four]; with zero_letter, 204 -> [two, oh, four]."""
    num = _num(lang)
    out = []
    for ch in str(n):
        d = int(ch)
        out.append(num["zero_letter"] if (d == 0 and zero_letter) else num["ones"][d])
    return out


def ordinalize(words: list[str], lang: str = "en") -> list[str]:
    """Make the last word ordinal: [twenty-two] -> [twenty-second], [one, hundred] -> [one, hundredth]."""
    if not words:
        return words
    rules = _num(lang)["ordinal"]
    joiner = _num(lang)["tens_joiner"]
    parts = words[-1].split(joiner) if joiner else [words[-1]]
    last = parts[-1]
    if last in rules["irregular"]:
        last = rules["irregular"][last]
    else:
        ending, replacement = rules["y_ending"]
        if ending and last.endswith(ending):
            last = last[: -len(ending)] + replacement
        else:
            last = last + rules["suffix"]
    parts[-1] = last
    return words[:-1] + [joiner.join(parts) if joiner else last]


def _indefinite_variants(forms_: list[list[str]], lang: str) -> list[list[str]]:
    """[one, hundred, fifty] also reads as [a, hundred, fifty] and, where the table allows, [hundred, fifty]."""
    num = _num(lang)
    heads = {num["hundred"], *num["scales"]}
    out = []
    for f in forms_:
        if len(f) > 1 and f[0] == num["ones"][1] and f[1] in heads:
            out.append([num["indefinite"]] + f[1:])
            if num.get("bare_leading_one") and f[1] == num["hundred"]:
                out.append(f[1:])  # "every hundred and eighty days"
    return out


def integer_forms(n: int, lang: str = "en", *, plain: bool = True) -> list[list[str]]:
    """Every usual reading of an integer. `plain` means it was written without group separators (1984, not 1,984)."""
    card = cardinal(n, lang)
    forms_: list[list[str]] = [card]
    conj = cardinal(n, lang, conj=True)
    if conj != card:
        forms_.append(conj)
    forms_ += _indefinite_variants(list(forms_), lang)
    pr = pairs(n, lang)
    if pr and (plain or pr[-1] == _num(lang)["hundred"]):
        if plain and 1000 <= n <= 9999:
            forms_.insert(0, pr)  # four plain digits read as a year first: nineteen eighty-four
        else:
            forms_.append(pr)
    if plain and n >= 100:
        forms_.append(digit_words(n, lang))
        if "0" in str(n):
            forms_.append(digit_words(n, lang, zero_letter=True))
    return _dedupe(forms_)


# --- parsing written numbers -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Number:
    """A token that reads as a number, split into the parts that change how it's said."""

    text: str
    integer: int
    fraction: str | None = None  # digits after the decimal mark, as written
    currency: str | None = None
    suffix: str | None = None  # "%", "x", "°" ...
    multiplier: str | None = None  # "k", "bn" ...
    ordinal: bool = False
    negative: bool = False
    plain: bool = True  # written without group separators

    @property
    def value(self) -> float:
        v = float(f"{self.integer}.{self.fraction}") if self.fraction else float(self.integer)
        return -v if self.negative else v


@lru_cache(maxsize=None)
def _number_re(lang: str) -> re.Pattern:
    tab = table(lang)
    num = tab["numbers"]

    def alt(items) -> str:
        items = sorted((i for i in items if i), key=len, reverse=True)
        return "|".join(re.escape(i) for i in items) or "(?!)"

    groups = "".join(re.escape(s) for s in num["group_separators"]) or ","
    return re.compile(
        rf"^(?P<neg>{alt(num['negative_marks'])})?"
        rf"(?P<cur>{alt(tab['currency'])})?"
        rf"(?P<int>\d{{1,3}}(?:[{groups}]\d{{3}})+|\d+)"
        rf"(?:(?:{alt(num['decimal_separators'])})(?P<frac>\d+))?"
        rf"(?P<mult>{alt(num['multipliers'])})?"
        rf"(?P<ord>{alt(num['ordinal']['markers'])})?"
        rf"(?P<suf>{alt(tab['suffixes'])})?$",
        re.IGNORECASE,
    )


def parse_number(token: str, lang: str = "en") -> Number | None:
    """The number a token spells with digits ("2,500", "54%", "$15", "1st", "2.5bn"), or None."""
    t = strip_edges(token, lang)
    if not t or not any(ch.isdigit() for ch in t):
        return None
    m = _number_re(lang).match(t)
    if not m:
        return None
    raw = m.group("int")
    digits = "".join(ch for ch in raw if ch.isdigit())
    mult = m.group("mult")
    ordinal = bool(m.group("ord"))
    if ordinal and (m.group("frac") or mult):
        return None
    table_mults = {k.lower(): k for k in _num(lang)["multipliers"]}
    suffix = m.group("suf")
    suffixes = table(lang)["suffixes"]
    if suffix and suffix not in suffixes:
        suffix = next((k for k in suffixes if k.lower() == suffix.lower()), suffix)
    return Number(
        text=t, integer=int(digits), fraction=m.group("frac"), currency=m.group("cur"), suffix=suffix,
        multiplier=table_mults.get(mult.lower()) if mult else None, ordinal=ordinal, negative=bool(m.group("neg")),
        plain=(raw == digits),
    )


def _parse_range(token: str, lang: str) -> tuple[Number, Number, str] | None:
    t = strip_edges(token, lang)
    for mark in _num(lang)["range_marks"]:
        idx = t.find(mark, 1)
        if idx <= 0:
            continue
        a, b = parse_number(t[:idx], lang), parse_number(t[idx + len(mark):], lang)
        if a and b:
            return a, b, mark
    return None


# --- forms ----------------------------------------------------------------------------------------------------------

def _dedupe(items: list[list[str]]) -> list[list[str]]:
    seen, out = set(), []
    for f in items:
        key = tuple(f)
        if f and key not in seen:
            seen.add(key)
            out.append(list(f))
    return out


def number_forms(num_: Number, lang: str = "en") -> list[list[str]]:
    """The spoken forms of a parsed number."""
    tab = table(lang)
    num = tab["numbers"]
    if num_.fraction is not None:
        frac = digit_words(num_.fraction, lang)
        ints = [f for f in integer_forms(num_.integer, lang, plain=False)][:2]
        base = [i + [num["point"]] + frac for i in ints]
        if num_.integer == 0:
            base.append([num["point"]] + frac)
    elif num_.ordinal:
        base = [ordinalize(f, lang) for f in integer_forms(num_.integer, lang, plain=False)]
    else:
        base = integer_forms(num_.integer, lang, plain=num_.plain and not num_.currency and not num_.suffix)
    if num_.multiplier:
        exponent = int(num["multipliers"][num_.multiplier])
        scale = num["scales"][exponent // 3 - 1]
        base = [b + [scale] for b in base]
    if num_.suffix:
        base = [b + list(s) for b in base for s in tab["suffixes"][num_.suffix]]
    if num_.currency:
        cur = tab["currency"][num_.currency]
        one = num_.fraction is None and num_.integer == 1 and not num_.multiplier
        base = [b + list(cur["singular"] if one else cur["plural"]) for b in base]
    if num_.negative:
        base = [[num["negative"]] + b for b in base]
    return _dedupe(base)[:MAX_FORMS]


def symbol_forms(text: str, lang: str = "en") -> list[list[str]]:
    """A symbol said on its own: "&" -> [and]; a unit sign Whisper returns as its own word, "%" -> [percent]."""
    tab = table(lang)
    if text in tab["symbols"]:
        return [list(f) for f in tab["symbols"][text]]
    if text in tab["suffixes"] and not any(ch.isalnum() for ch in text):
        return [list(f) for f in tab["suffixes"][text]]
    return []


def forms(token: str, lang: str = "en") -> list[list[str]]:
    """How a token is said: a list of word sequences, most usual first.

    Numbers and symbols get every usual reading ("664" -> [six, hundred, sixty-four], [six, sixty-four], ...; "&" ->
    [and]). A plain word is said as itself (["Hello"]). Punctuation alone says nothing ([]).
    """
    t = strip_edges(token, lang)
    if not t:
        return []
    tab = table(lang)
    said = symbol_forms(t, lang)
    if said:
        return said
    rng = _parse_range(t, lang)
    if rng:
        a, b, _mark = rng
        joiner = tab["numbers"]["range"]
        combos = [fa + [joiner] + fb for fa in number_forms(a, lang)[:3] for fb in number_forms(b, lang)[:3]]
        return _dedupe(combos)[:MAX_FORMS]
    parsed = parse_number(t, lang)
    if parsed is None:
        return [[t]]
    return number_forms(parsed, lang)


def forms_in(texts, lang: str = "en") -> dict[str, list[list[str]]]:
    """{token: forms} for every number or symbol in some lines ("664" -> [[six, sixty-four], ...], "&" -> [[and]]).

    timing/vo_words.json carries this for every VO line as "spoken", so an engine's word() can match "664" against
    what was heard without its own number tables. Plain words are said as themselves and aren't listed.
    """
    out: dict[str, list[list[str]]] = {}
    for text in texts:
        for token in str(text or "").split():
            core = strip_edges(token, lang)
            if not core or core in out:
                continue
            said = forms(token, lang)
            if said and said != [[core]]:
                out[core] = said
    return out


# --- reading spelled-out numbers (for the script's number gate) ---------------------------------------------------------

@lru_cache(maxsize=None)
def _word_values(lang: str) -> dict[str, tuple[str, int]]:
    """match key -> (kind, value) for every number word the table can produce: units 0-99, the hundreds word, scales."""
    num = _num(lang)
    out: dict[str, tuple[str, int]] = {}
    for n in range(100):
        out[norm(below_hundred(n, lang), lang)] = ("unit", n)
    out[norm(num["hundred"], lang)] = ("hundreds", 100)
    for i, scale in enumerate(num["scales"]):
        out[norm(scale, lang)] = ("scale", 1000 ** (i + 1))
    out[norm(num["zero_letter"], lang)] = ("zero_letter", 0)
    return out


def spelled_values(words: list[str], lang: str = "en") -> list[int]:
    """The values a run of number words can mean: [six, sixty-four] -> [664, 70]; [two, thousand, five, hundred] -> [2500].

    Pair readings (scores, years) and cardinal readings are both returned, because a run like "one fifty" is ambiguous.
    """
    vals = _word_values(lang)
    num = _num(lang)
    skip = {norm(num["conjunction"], lang)}
    keys = [norm(w, lang) for w in words]
    keys = [k for k in keys if k and k not in skip]
    indefinite = norm(num["indefinite"], lang)
    if not keys:
        return []
    out: list[int] = []
    # Cardinal: units accumulate, the hundreds word multiplies the current group, scales close a group.
    total, current, ok = 0, 0, True
    for i, k in enumerate(keys):
        if k == indefinite:
            current = 1
            continue
        kind, v = vals.get(k, ("", -1))
        if kind == "unit":
            current += v
        elif kind == "hundreds":
            current = (current or 1) * 100
        elif kind == "scale":
            total += (current or 1) * v
            current = 0
        else:
            ok = False
            break
    if ok:
        out.append(total + current)
    # Pairs: two sub-hundred numbers side by side, optionally "oh" between ([nineteen, oh, five] -> 1905).
    units = [vals.get(k, ("", -1)) for k in keys]
    if len(units) == 2 and all(kind == "unit" for kind, _ in units) and 0 < units[0][1] and 10 <= units[1][1]:
        out.append(units[0][1] * 100 + units[1][1])
    if len(units) == 3 and units[0][0] == "unit" and units[1][0] == "zero_letter" and units[2][0] == "unit" \
            and units[2][1] < 10:
        out.append(units[0][1] * 100 + units[2][1])
    return sorted(set(out), key=out.index)


def number_word(token: str, lang: str = "en") -> str | None:
    """The kind of number word a token is ('unit', 'hundreds', 'scale', 'zero_letter'), or None."""
    return _word_values(lang).get(norm(token, lang), (None, 0))[0]
