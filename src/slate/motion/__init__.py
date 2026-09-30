"""Slate's motion law in Python: the maths the Remotion kit, the After Effects kit and the QA gates share.

- `slate.motion.eases`: eases with exact endpoints, T90, landing a move on a beat, monotone splines, staggers, a
  brand's law read from its pack, and a cubic-bezier's After Effects speed and influence.
- `slate.motion.fit`: fit a cubic-bezier to a move measured in a reference film (brand onboarding's motion study).
- `slate.motion.vectors`: the shared cross-language vectors (tests/vectors/eases.json, t90.json), regenerated and
  checked with `python -m slate.motion.vectors --check` (or `--write`).
"""

from slate.motion.eases import (  # noqa: F401
    AE_MIN_INFLUENCE,
    EaseSpec,
    MotionLaw,
    ae_keyframe_ease,
    bezier_at,
    evaluate,
    lands_on,
    law_from,
    mono_spline,
    parse,
    sine_at,
    spring0_at,
    start_for,
    t90,
    t_at,
)
