"""The kit's speed limits are the motion gate's, and the kit measures a move's travel the way the gate does.

engine/remotion/kit/core/motion.ts keeps a copy of the gate's limits (SPEED_LIMITS, read with the class names the gate
reads), so a scene can size a move to the law before anything renders: `maxTravel()` gives the longest travel a move of
a given length and ease can make inside its class's limits at the film's size, and the web template sizes its moves
with it. This test holds that copy to slate.qa.motion.SPEED_CLASSES and CLASS_ALIASES, and holds the measurement
maxTravel() makes (the ease sampled on every whole frame of the move, at rest the frame before and the frame after,
speed the step from one frame to the next, acceleration the change in that step) to the gate's own check_speed: a
move at that travel passes, and one a percent further fails. engine/tests/kit/motion.test.ts holds maxTravel() to the
same measurement in TypeScript.
"""

from __future__ import annotations

import json
import re
from fractions import Fraction

import pytest

from slate import paths
from slate.motion.eases import evaluate, parse
from slate.qa import motion as M

KIT_MOTION = paths.ENGINE_SRC / "remotion" / "kit" / "core" / "motion.ts"
KIT_BRAND = paths.ENGINE_SRC / "tests" / "core" / "fixtures" / "kit-brand" / "brand.json"


def _fraction(text: str) -> Fraction:
    num, _, den = text.partition("/")
    return Fraction(num.strip()) / (Fraction(den.strip()) if den.strip() else 1)


def _kit_limits() -> dict[str, dict[str, Fraction]]:
    src = KIT_MOTION.read_text(encoding="utf-8")
    block = re.search(r"export const SPEED_LIMITS\b[^=]*= \{(.*?)\n\};", src, re.S)
    assert block, "the kit's SPEED_LIMITS table moved: update this test with it"
    rows = re.findall(r"(\w+): \{speed: ([\d\s/.]+), accel: ([\d\s/.]+)\}", block.group(1))
    return {cls: {"speed": _fraction(s), "accel": _fraction(a)} for cls, s, a in rows}


def test_the_kits_speed_limits_are_the_gates():
    kit = _kit_limits()
    gate = {c: row for c, row in M.SPEED_CLASSES.items() if row["speed"] is not None}
    assert set(kit) == set(gate) == {"rig", "ui", "carry"}  # a whip has no limits of its own in either
    for cls, row in kit.items():
        for k in ("speed", "accel"):
            assert float(row[k]) == pytest.approx(gate[cls][k], rel=1e-12), (cls, k)


def test_the_kit_reads_the_class_names_the_gate_reads():
    src = KIT_MOTION.read_text(encoding="utf-8")
    table = re.search(r"const CLASS_ALIASES: Record<string, MotionClass> = \{([^}]*)\}", src)
    assert table, "the kit's CLASS_ALIASES table moved: update this test with it"
    assert dict(re.findall(r"(\w+): '(\w+)'", table.group(1))) == M.CLASS_ALIASES


def _max_travel(spec, frames: int, speed: float, accel: float) -> float:
    """maxTravel()'s measurement, step for step (engine/remotion/kit/core/motion.ts)."""
    at = [0.0] + [evaluate(spec, k / frames) for k in range(frames + 1)] + [1.0]
    steps = [b - a for a, b in zip(at, at[1:])]
    fastest = max(abs(s) for s in steps)
    hardest = max(abs(b - a) for a, b in zip([0.0, *steps], steps))
    return min(speed / fastest, accel / hardest) * (1 - 1e-4)


@pytest.mark.parametrize("ease, frames, cls, size", [
    ("enter", 5, "ui", (320, 180)),  # the web template's line in the end-to-end test's 10-frame act
    ("glide", 5, "ui", (320, 180)),  # the kit fixture's dot
    ("enter", 13, "ui", (1920, 1080)),  # a base entrance at 1080p
    ("glide", 8, "carry", (1080, 1920)),  # a carry on a 9:16 reel
    ("commit", 20, "rig", (1280, 720)),  # a rig part on a steep in-out
])
def test_a_move_at_the_kits_travel_keeps_the_gates_limits_and_one_further_breaks_them(ease, frames, cls, size):
    motion = json.loads(KIT_BRAND.read_text(encoding="utf-8"))["motion"]
    law = M.law_from(motion, source="the kit fixture brand", fps=25.0)
    assert law is not None
    limits = M.class_limits(*size, law)[cls]
    most = _max_travel(parse(motion["eases"][ease]), frames, limits["speed"], limits["accel"])

    def verdict(travel: float) -> str:
        spec = parse(motion["eases"][ease])
        # sampled on whole frames, rounded to six places, as the kit writes a trace
        samples = [[f, round(travel * (1 - evaluate(spec, f / frames)), 6)] for f in range(frames + 1)]
        e = {"id": "card", "shot": "S1", "prop": "y", "ease": ease, "frame_in": 0, "frame_out": frames,
             "from": travel, "to": 0, "samples": samples, "entrance": False, "loop_period": None, "rig": None,
             **({} if cls == "ui" else {"class": cls})}
        data = M.MotionData(fps=Fraction(25), size=size, law=law)
        data.moves = [M.move_from(e, 1, 0)]
        data.acts, data.traced = [1], [1]
        return M.check_speed(data).status

    assert verdict(most) == "PASS"
    assert verdict(most * 1.01) == "FAIL"
