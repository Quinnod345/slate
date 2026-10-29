"""Exact counted displays keep numeric token boundaries while allowing ordinary OCR spacing."""

import pytest

from slate.qa.checks import text
from tests.qa.test_readtime_count_holds_039 import count_film


@pytest.mark.parametrize("display,observed", [("12%", "1 2%"), ("12% complete", "1 2% complete")])
def test_separate_ocr_digit_tokens_cannot_prove_the_final_count(count_film, display, observed):
    film, sweep = count_film(display)
    for sample in sweep.samples:
        if sample["lines"]:
            sample["lines"] = [{"text": observed}]
    result = text.readtime(film)
    assert result.status in {"FAIL", "UNCHECKED"}, result.to_dict()


def test_ordinary_unit_and_word_spacing_still_proves_the_exact_count(count_film):
    film, sweep = count_film("12% complete")
    for sample in sweep.samples:
        if sample["lines"]:
            sample["lines"] = [{"text": "12 %  complete"}]
    assert text.readtime(film).status == "PASS"
