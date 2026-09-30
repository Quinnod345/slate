"""Landings on a brand's eases: beziers with the films' own bisection, springs and sine through the motion law."""

from __future__ import annotations

import pytest

from slate.errors import UsageError
from slate.motion import eases as motion_eases
from slate.timing import ease

MOTION = {"eases": {
    "arrive": {"bezier": [0.2, 0.9, 0.3, 1]},
    "settle": {"kind": "spring0", "stiffness": 8},
    "sway": "sine",
    "linear": {"kind": "linear"},
    "broken": {"kind": "wobble"},
}}


def test_every_readable_ease_is_known_and_an_unreadable_one_says_why():
    eases = ease.eases_from_motion(MOTION)
    assert eases["arrive"] == (0.2, 0.9, 0.3, 1.0)
    assert eases["settle"].kind == "spring0" and eases["settle"].stiffness == 8
    assert eases["sway"].kind == "sine" and eases["broken"] is None
    with pytest.raises(UsageError, match="isn't a curve a landing can be timed with"):
        ease.parse_land("broken:0.5", eases)
    with pytest.raises(UsageError, match=r"no ease called 'bounce' \(it has: arrive, linear, settle, sway\)"):
        ease.parse_land("bounce:0.5", eases)


def test_a_spring_landing_uses_the_motion_laws_t90():
    """Regression: a brand whose settle ease is a zero-bounce spring used to fail with "the brand has no ease called
    'settle'" (only beziers were read), so any move landing on it couldn't be timed."""
    eases = ease.eases_from_motion(MOTION)
    name, dur, curve = ease.parse_land("settle:0.8", eases)
    assert name == "settle" and dur == 0.8
    assert ease.t90(curve, dur) == pytest.approx(motion_eases.t90(motion_eases.parse(MOTION["eases"]["settle"])) * 0.8)
    assert 0 < ease.t90(curve, dur) < dur
    assert ease.t90(eases["linear"], 1.0) == pytest.approx(0.9)
    assert ease.t90(eases["arrive"], 0.5) == pytest.approx(ease.t_at((0.2, 0.9, 0.3, 1.0), 0.5, 0.9))
