"""Spoken forms: the shared vectors, the language table, and no English in the matching code (P084)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from slate import paths
from slate.timing import spoken
from slate.timing.words import candidates, find

VECTORS = json.loads((paths.REPO / "tests" / "vectors" / "spoken.json").read_text(encoding="utf-8"))
TIMING_SRC = paths.PACKAGE / "timing"


@pytest.mark.parametrize("case", VECTORS["normalize"], ids=lambda c: c["in"])
def test_normalize_vectors(case):
    assert spoken.norm(case["in"], VECTORS["lang"]) == case["key"]


@pytest.mark.parametrize("case", VECTORS["forms"], ids=lambda c: c["token"])
def test_forms_vectors(case):
    assert spoken.forms(case["token"], VECTORS["lang"]) == case["forms"]


@pytest.mark.parametrize("case", VECTORS["match"], ids=lambda c: f"{c['query']}#{c['occ']}")
def test_match_vectors(case):
    words = [{"w": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(case["words"])]
    span = find(words, case["query"], case["occ"], VECTORS["lang"])
    assert (list(span) if span else None) == case["span"]
    assert [{"key": k, "words": n} for k, n in candidates(case["query"], VECTORS["lang"])] == case["targets"]


def test_vectors_cover_the_films_cases():
    tokens = {c["token"] for c in VECTORS["forms"]}
    assert {"664", "&", "45%", "2,500", "150"} <= tokens  # the films' kinds of case: a score read in pairs, an ampersand,
    # a percentage, grouped thousands, a count said with "hundred and"
    queries = {c["query"] for c in VECTORS["match"]}
    assert {"664", "&", "12", "twelve", "2,500", "45%"} <= queries


def test_the_forms_interface():
    assert ["six", "sixty-four"] in spoken.forms("664")
    assert spoken.forms("&") == [["and"]]
    assert spoken.forms("Hello") == [["Hello"]]
    assert spoken.forms("—") == []


def test_numbers_read_back_to_their_values():
    assert spoken.parse_number("2,500").value == 2500 and not spoken.parse_number("2,500").plain
    assert spoken.parse_number("$1.50").value == 1.5 and spoken.parse_number("$1.50").currency == "$"
    assert spoken.parse_number("B41") is None and spoken.parse_number("hello") is None
    assert 664 in spoken.spelled_values(["six", "sixty-four"])
    assert spoken.spelled_values(["a", "hundred", "and", "twenty"]) == [120]
    assert spoken.spelled_values(["twenty-five", "hundred"]) == [2500]
    assert 1905 in spoken.spelled_values(["nineteen", "oh", "five"])


def test_word_count_counts_spoken_symbols_not_punctuation():
    assert spoken.word_count("Salt & Stone — six sixty-four.") == 5
    assert spoken.word_count("Capability · Stable · Alignment") == 3


def test_an_unknown_language_is_a_clear_usage_error():
    from slate.errors import UsageError

    with pytest.raises(UsageError, match="no language table for 'xx'"):
        spoken.table("xx")


# --- no English in the clock's matching code -------------------------------------------------------------------------------

def vocabulary(lang: str = "en") -> set[str]:
    tab = spoken.table(lang)
    n = tab["numbers"]
    words = set(n["ones"]) | {t for t in n["tens"] if t} | {n["hundred"], n["conjunction"], n["indefinite"],
                                                             n["zero_letter"], n["point"], n["negative"], n["range"]}
    words |= set(n["scales"]) | set(n["ordinal"]["irregular"].values()) | set(n["ordinal"]["markers"])
    for group in (tab["symbols"], tab["suffixes"]):
        for forms_ in group.values():
            words |= {w for f in forms_ for w in f}
    for cur in tab["currency"].values():
        words |= set(cur["plural"]) | set(cur["singular"])
    return {w.lower() for w in words if w}


def string_literals(path: Path) -> list[str]:
    """String constants that could carry language: not docstrings, and not field names.

    A field name is the name of a slot in a data file (the cue sheet's `at`, the language table's `hundred`): the
    table maps that slot to a word in each language, so the name itself is schema, not English. Subscript keys,
    dict keys and the first argument of .get/.setdefault/.pop are field names.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            skip.add(id(node.slice))
        if isinstance(node, ast.Dict):
            skip.update(id(k) for k in node.keys if isinstance(k, ast.Constant))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "setdefault", "pop") and node.args
                and isinstance(node.args[0], ast.Constant)):
            skip.add(id(node.args[0]))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


def test_no_english_words_in_the_clock_and_transcript_code():
    vocab = vocabulary("en")
    offenders = []
    for path in sorted(TIMING_SRC.glob("*.py")):
        for lit in string_literals(path):
            if lit.strip().lower() in vocab:
                offenders.append(f"{path.name}: {lit!r}")
    # the matching core holds no vocabulary word at all, even inside a longer string or pattern
    for name in ("spoken.py", "words.py"):
        for lit in string_literals(TIMING_SRC / name):
            tokens = {t.strip(".,:;!?()[]{}'\"").lower() for t in lit.replace("|", " ").split()}
            hits = tokens & (vocab - {"a", "and", "to", "at", "per", "over", "number"})
            if hits:
                offenders.append(f"{name}: {lit!r} ({', '.join(sorted(hits))})")
    assert offenders == []


def test_the_table_is_the_only_place_english_lives():
    table = spoken.table("en")
    assert table["lang"] == "en" and "six" in table["numbers"]["ones"]
    assert spoken.languages() == sorted(p.stem for p in (TIMING_SRC / "lang").glob("*.json"))
