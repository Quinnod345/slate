"""A whole-shot decorative-surface exemption retains its existing meaning when a count is present."""

import pytest

from slate.qa.checks import text
from tests.qa.test_readtime_count_holds_039 import BINDING, _change, count_film


@pytest.mark.parametrize("missing_export", [False, True])
def test_whole_nonreading_count_does_not_need_a_readable_count_binding(count_film, missing_export):
    film, _sweep = count_film(reading={
        "not_for_reading": True,
        "reason": "This miniature app preview is a decorative surface, not audience reading copy.",
    })
    if missing_export:
        (film / "timing/visual_events/act1.json").unlink()
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert len(result.details["exempt"]) == 1
    assert result.details["count_checks"] == []


def test_whole_nonreading_authored_count_does_not_require_a_visual_export(count_film):
    film, _sweep = count_film(reading={
        "not_for_reading": True,
        "reason": "This miniature app preview is intentionally decorative.",
    }, events=None)
    _change(film, "script/script.json", lambda script: script["acts"][0]["shots"][0].update(
        events=[{"id": "progress-count", "kind": "count", "t": 1.0}],
    ))
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.inputs["timing/visual_events/act1.json"] is None


def test_valid_count_metadata_does_not_cancel_the_whole_nonreading_exemption(count_film):
    film, _sweep = count_film(reading={
        **BINDING,
        "not_for_reading": True,
        "reason": "The preview statistic is decorative, not audience copy.",
    }, events=None)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert len(result.details["exempt"]) == 1
