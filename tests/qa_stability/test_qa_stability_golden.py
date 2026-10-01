"""Golden data: the real faults the films kept as positive controls must still be caught.

Two clips cut from a film's renders before their fixes (pipeline/tools/controls/ in the film project, read-only):
a score that re-centres a whole pixel as it lands (a snap, act frames 1013-1015) and a count that re-centres as its
digits change width (a wobble, act frames 1212-1215). Each must be flagged in its class on at least three tiles in
its frames. They're full-HD ProRes, a few seconds of CPU each, so they run with SLATE_GOLDEN_HEAVY=1.
"""

import os

import pytest

from slate.qa import stability as S

heavy = pytest.mark.skipif(not os.environ.get("SLATE_GOLDEN_HEAVY"),
                           reason="scans full-resolution film renders: set SLATE_GOLDEN_HEAVY=1")


@heavy
def test_the_films_real_faults_are_still_caught(filmroot):
    folder = filmroot / "pipeline" / "tools" / "controls"
    items = [
        {"file": folder / "B4_numeral664_994-1029.mov", "first": 994, "class": "snap", "frames": [1013, 1015],
         "min_tiles": 3, "what": "a score re-centring as it lands"},
        {"file": folder / "B7_count671_1196-1231.mov", "first": 1196, "class": "wobble", "frames": [1212, 1215],
         "min_tiles": 3, "what": "a count re-centring as its digits change width"},
    ]
    if not all(i["file"].exists() for i in items):
        pytest.skip("the control clips aren't on this machine")
    r = S.controls(items, jobs=1)
    assert r.status == "PASS", r.summary
    assert [c["status"] for c in r.details["checks"]] == ["PASS", "PASS"]
