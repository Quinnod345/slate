"""Fitting a measured move, and parity with the films' own motion tables (golden data, read in place from the film
project at test time; nothing from it is copied into Slate)."""

from __future__ import annotations

import re

import numpy as np
import pytest

from slate.motion import eases as E
from slate.motion.fit import fit_bezier, normalise_move


def test_fit_recovers_a_known_curve():
    bez = [0.3, 0.1, 0.2, 1.0]
    t = np.linspace(0, 1, 61)
    # a still lead-in and tail, then the move, measured in px
    times = np.concatenate([np.linspace(-0.3, -0.01, 10), t, np.linspace(1.01, 1.3, 10)])
    values = np.array([40.0] * 10 + [40 + 300 * E.bezier_at(bez, float(u)) for u in t] + [340.0] * 10)
    fit = fit_bezier(times, values)
    assert fit["rms"] < 0.01
    assert fit["travel"] == pytest.approx(300, abs=1e-6)
    # the fit models the move between 2% and 98% of its travel (the tails are the signal's noise floor): check it
    # against that normalised move, sample by sample
    mv = normalise_move(times, values)
    got = E.parse(fit["bezier"])
    worst = max(abs(E.evaluate(got, float(u)) - float(p)) for u, p in zip(mv["t"], mv["p"]))
    assert worst < 0.01
    assert fit["start_s"] == pytest.approx(mv["start_s"], abs=1e-4) and fit["duration_s"] == pytest.approx(0.8, abs=1e-3)


def test_fit_refuses_a_signal_that_does_not_move():
    with pytest.raises(ValueError, match="doesn't move"):
        normalise_move([0, 1, 2, 3, 4], [5, 5, 5, 5, 5])
    with pytest.raises(ValueError, match="four samples"):
        normalise_move([0, 1], [0, 1])


def _film_tables(root):
    ease_ts = (root / "pipeline/r3f/src/ease.ts").read_text()
    motion_ts = (root / "pipeline/r3f/src/kit/motion.ts").read_text()
    bez = {m[0]: [float(x) for x in m[1].split(",")] for m in re.findall(r"^\s+(\w+): \[([\d.,\s]+)\],", ease_ts, re.M)}
    t90_line = re.search(r"const T90: Record<string, number> = \{([^}]*)\}", motion_ts)
    assert t90_line, "the film's T90 table moved"
    t90 = {k: float(v) for k, v in re.findall(r"(\w+): ([\d.]+)", t90_line.group(1))}
    return bez, t90


def test_t90_reproduces_the_films_hand_written_table(filmroot):
    """The films kept nine hand-computed T90 tables; the generalised T90 must reproduce them from the curves alone."""
    bez, table = _film_tables(filmroot)
    checked = 0
    rounded = []
    for name, want in table.items():
        if name in bez:
            spec = E.parse(bez[name])
        elif name == "sway":
            spec = E.EaseSpec("sine")
        elif name == "linear":
            spec = E.EaseSpec("linear")
        else:
            continue
        got = E.t90(spec)
        # within a hundredth of the duration (under a frame for any move up to 3 s at 30 fps)
        assert got == pytest.approx(want, abs=1e-2), name
        if abs(got - want) > 1.5e-3:
            rounded.append((name, want, round(got, 4)))
        checked += 1
    assert checked >= 6
    # the films' table rounded one value by hand: their exit curve reaches 90% at 0.937, not 0.93. Nothing else drifts.
    assert [r[0] for r in rounded] in ([], ["exit"]), rounded


def test_the_films_curves_now_end_exactly(filmroot):
    """Regression: the films' bisection ease left ~1e-14 before a move and one curve ended under 1, so "has it started?"
    gates opened early. Every one of the films' curves is exact at both ends here."""
    bez, _ = _film_tables(filmroot)
    assert len(bez) >= 5
    for name, b in bez.items():
        spec = E.parse(b)
        assert spec is not None, name
        assert E.evaluate(spec, 0.0) == 0.0 and E.evaluate(spec, 1.0) == 1.0, name
        assert E.evaluate(spec, 1e-9) >= 0.0
