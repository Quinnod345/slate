"""timing.words: a word the aligner didn't hear is a REVIEW item a person can listen to and accept on that exact take.

The investor film's "Execution" (merged into "How") and "a" (heard as "of") are spoken as written; with no keyed items
the gate could never pass, and every recorded gate must pass for a delivery.
"""

from __future__ import annotations

from slate import jsonio
from slate.qa import accept
from slate.timing.builder import check_words
from slate.timing.takes import Take

BOARD = {"acts": [{"name": "One", "shots": [{"id": "S01", "vo": "Execution: how it runs."}]}]}
WORDS = {"shots": {"S01": {"tokens": [{"t": "Execution", "exact": False}, {"t": "how", "exact": True},
                                      {"t": "it", "exact": True}, {"t": "runs.", "exact": True}]}}}


def _take(film, data=b"take one"):
    path = film / "voice/takes/S01/t01.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"S01": Take(shot="S01", path=path, rel="voice/takes/S01/t01.wav")}


def _accept(film, item):
    jsonio.write_atomic(accept._path(film), {"accepted": [  # noqa: SLF001 - the file accept() writes
        {"gate": "timing.words", "key": item["key"], "basis": item["basis"], "by": "A person",
         "reason": "Listened: the word is spoken as written.", "at": "2026-10-29T12:00:00-05:00"}]})


def test_an_unheard_word_is_a_keyed_review_item_bound_to_its_take(tmp_path):
    takes = _take(tmp_path)
    result = check_words(BOARD, WORDS, takes, tmp_path)
    assert result.status == "REVIEW"
    (item,) = result.details["review_items"]
    assert item["key"] == "S01/Execution/1"
    assert item["basis"] == {"voice/takes/S01/t01.wav": jsonio.sha256(takes["S01"].path)}


def test_a_person_accepting_it_on_that_take_passes_the_gate(tmp_path):
    takes = _take(tmp_path)
    (item,) = check_words(BOARD, WORDS, takes, tmp_path).details["review_items"]
    _accept(tmp_path, item)
    result = check_words(BOARD, WORDS, takes, tmp_path)
    assert result.status == "PASS", result.to_dict()
    assert result.details["accepted"][0]["accepted_by"] == "A person"


def test_a_new_take_reopens_the_word(tmp_path):
    takes = _take(tmp_path)
    (item,) = check_words(BOARD, WORDS, takes, tmp_path).details["review_items"]
    _accept(tmp_path, item)
    takes = _take(tmp_path, b"take two")
    assert check_words(BOARD, WORDS, takes, tmp_path).status == "REVIEW"


def test_without_a_film_folder_the_gate_still_asks_for_a_listen(tmp_path):
    assert check_words(BOARD, WORDS, _take(tmp_path)).status == "REVIEW"
