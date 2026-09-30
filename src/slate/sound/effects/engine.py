"""The effects engine's entry points: spotting (beats to a cue sheet) and rendering (cues to stems).

Both pass the proof-3 gate first. Past the gate, they refuse on purpose with the next step: the engine is ported from
the approved p3 pass, not written ahead of the approval (the plan re-inventories the p3 sound once it's approved, so
Slate carries the design the owner signed off, not an earlier draft of it).

What the port brings, so the next wave knows exactly what to do (sources in the film project, read-only):

1. Spotting: every beat in timing/visual_events/actN.json (a list, or {"events"|"beats": [...]}; unreadable files
   skipped and reported; `{"silent": true}` acts skipped) becomes one cue of its kind's one sound
   (palette["kinds"]), placed so its anchor lands on the beat, with seeded variation between repeats (a few cents of
   detune, about a decibel of velocity, alternate takes where the library has them) and never the same hit twice in
   a row. Written to sound/sound_cues.json (schema: sound_cues), every cue carrying its beat, anchor rule and the
   file or parameters that make it, so `slate verify` can measure each one on its own.
2. Rendering: proof 2's voices for wood, air, sub, swell and count-ups (sound_engine.py), the p3 sampler for the tonal
   roles (small felt notes and pads on the installed sampled instruments; slate.sound.instruments fails when one is
   missing, with no synthesized stand-in), the shared hall split into effects and tonal returns, ducking under the
   voice by stem, planned silences, and each stem trimmed to the palette's level against the voice. Written to
   sound/stems/{ui,fx,tonal,verb,tverb}.wav with sound/stems/stems.json, which `slate mix` already reads.
"""

from __future__ import annotations

from pathlib import Path

from slate.errors import Refused
from slate.sound.effects import require_proof3

NEXT_WAVE = ("Proof 3 is approved, so the effects engine is next: it's ported from the approved p3 pass (proof 3, and A's "
             "p3 mix once that's approved too) after a fresh inventory of it, which is the plan's order, and it isn't in "
             "this build yet.")
NEXT_HINT = ("Mix without effects meanwhile (`slate mix`), or hand-make effect stems in sound/stems/ with a "
             "stems.json, and `slate mix` and `slate verify` take them as they are.")


def spot(film_dir: Path) -> Path:
    """Beats to sound/sound_cues.json. Gated on proof 3."""
    require_proof3()
    raise Refused(NEXT_WAVE, hint=NEXT_HINT, details={"stage": "spot", "film": str(film_dir)})


def render(film_dir: Path) -> dict:
    """Cues to effect stems. Gated on proof 3."""
    require_proof3()
    raise Refused(NEXT_WAVE, hint=NEXT_HINT, details={"stage": "render", "film": str(film_dir)})


def render_cue(cue: dict):
    """One cue rendered on its own (what `slate verify` needs for cues without their own file). Gated on proof 3."""
    require_proof3()
    raise Refused(NEXT_WAVE, hint=NEXT_HINT, details={"stage": "render_cue", "cue": cue.get("id")})
