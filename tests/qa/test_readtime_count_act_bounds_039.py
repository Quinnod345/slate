"""Count timing respects the renderer's half-frame tolerance at a shifted act boundary."""

import pytest

from slate.qa.checks import text
from tests.qa.test_readtime_count_holds_039 import _event, count_film


@pytest.mark.parametrize("frames_early,status", [(0.49, "PASS"), (0.51, "UNCHECKED")])
def test_count_landing_uses_the_renderer_act_boundary(count_film, frames_early, status):
    landing = 8.0 - frames_early / 30.0
    film, _sweep = count_film(frame_in=240, events=[_event(landing=landing)], end=4.0)
    result = text.readtime(film)
    assert result.status == status, result.to_dict()
    if status == "UNCHECKED":
        assert result.details["count_checks"] == []
        assert "act" in " ".join(result.evidence).lower()
