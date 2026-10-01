"""The engine's span rules (engine/remotion/events.mjs spanProblems, which validateEvents applies to every act the engine
emits or checks) say exactly what slate.timing.beats.problems() says, word for word, on the same beats.

t is always the beat: a draw and a count carry their span (start, dur) and end on t; a whoosh and a swell peak on t
inside theirs; any other kind may give a span, with its beat inside. Sound reads the spans, QA's schema gate requires
them, and every engine writes them, so the Node and Python halves must never disagree about one.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from slate import paths
from slate.timing import beats

EVENTS = paths.ENGINE_SRC / "remotion" / "events.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")

CASES = [
    # fine: a draw and a count end on t, a whoosh and a swell peak inside, a land inside its optional span
    {"id": "draw_ok", "kind": "draw", "t": 1.3, "start": 1.0, "dur": 0.3},
    {"id": "count_ok", "kind": "count", "t": 2.0, "start": 1.25, "dur": 0.75, "from": 0, "to": 64},
    {"id": "whoosh_ok", "kind": "whoosh", "t": 0.71, "start": 0.6, "dur": 0.3},
    {"id": "swell_edge", "kind": "swell", "t": 0.9, "start": 0.6, "dur": 0.3},
    {"id": "land_span", "kind": "land", "t": 3.33, "start": 3.1, "dur": 0.4},
    {"id": "tap_plain", "kind": "tap", "t": 0.4},
    # problems, one of each
    {"id": "draw_no_span", "kind": "draw", "t": 1.0},
    {"id": "count_only_dur", "kind": "count", "t": 1.0, "dur": 0.5},
    {"id": "draw_on_start", "kind": "draw", "t": 1.0, "start": 1.0, "dur": 0.37},
    {"id": "count_short", "kind": "count", "t": 2.2, "start": 1.0, "dur": 0.9},
    {"id": "tap_half_span", "kind": "tap", "t": 1.0, "start": 0.9},
    {"id": "land_text_span", "kind": "land", "t": 1.0, "start": "0.9", "dur": 0.2},
    {"id": "page_backwards", "kind": "page", "t": 1.0, "start": 1.2, "dur": -0.1},
    {"id": "stamp_negative", "kind": "stamp", "t": 0.1, "start": -0.2, "dur": 0.4},
    {"id": "whoosh_early", "kind": "whoosh", "t": 0.31, "start": 0.6, "dur": 0.3},
    {"id": "swell_late", "kind": "swell", "t": 1.17, "start": 0.6, "dur": 0.3},
    {"id": "land_outside", "kind": "land", "t": 4.21, "start": 3.1, "dur": 0.4},
    {"kind": "draw", "t": 5.0, "start": 4.0, "dur": 0.5},  # no id: "beat N" alone
    # half a frame of slack at the film's rate (two milliseconds without one)
    {"id": "draw_near", "kind": "draw", "t": 1.31, "start": 1.0, "dur": 0.3},
    {"id": "whoosh_near", "kind": "whoosh", "t": 0.913, "start": 0.6, "dur": 0.3},
]

NODE = """
import {spanProblems} from %s;
const {beats, fps} = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(spanProblems(beats, fps)));
"""


def _engine(beat_list: list, fps: float | None) -> list[str]:
    script = NODE % json.dumps(EVENTS.as_uri())
    proc = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps({"beats": beat_list, "fps": fps})],
                          capture_output=True, text=True, timeout=60, cwd=paths.ENGINE_SRC)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("fps", [None, 25.0, 30000 / 1001, 60.0])
def test_the_engine_and_slate_timing_agree_word_for_word(fps):
    expected = beats.problems(CASES, fps=fps)
    assert expected, "the cases must exercise the rules"
    assert _engine(CASES, fps) == expected


def test_a_silent_act_and_odd_entries_have_nothing_to_check():
    assert beats.problems({"silent": True, "reason": "held"}) == []
    odd = [{"id": "x", "kind": "draw", "t": 1, "start": 0.5, "dur": 0.5}, "not a beat", 7, None]
    assert _engine(odd, 25) == beats.problems(odd, fps=25) == []
