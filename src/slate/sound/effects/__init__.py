"""The effects engine: designed, event-tied sound for every beat of the picture. Gated on proof 3.

What it is for. Every beat the picture makes (a tap, a card landing, a count, a line drawing, an act's one impact)
gets one deliberate sound of its kind, with natural variation between repeats, placed within a frame of its beat and
balanced against the voice. The design is proof 2's, perfected with the owner's notes into proof 3 (tonal roles on
real sampled instruments, no glides or slide-ups, no room-tone drone, one consistent sound per kind of interaction),
and every brand gets its own palette during onboarding, starting from proof 2's.

Why it's gated. The plan starts the effects engine only once proof 3 and A's p3 mix are approved, and re-inventories
the p3 pass then, so Slate ports the approved design rather than a moving one (perfect, don't replace). Until a person
records that approval, every entry point here refuses with the next step, and films mix score and voice only, which
is all the first reel needs (`slate mix` works without effects).

What's here now, and what each part does:

- `require_proof3()`: raises Refused until a person's approval of proof 3 is recorded with its SHA-256
  (`slate approve proof3`, or a record in DATA/approvals.json; see gate.py for the exact rules).
- `status()`: the gate's state, for `slate sfx status` and doctor.
- `palette`: the starting palette (proof 2's levels, kinds, materials, rules and sampled instruments) and a brand's own
  over it. Not gated: the mix, the score planner and the checks use it today.
- `engine.spot()` / `engine.render()`: the engine's entry points. They pass the gate first; the renderer itself is
  ported from the approved p3 pass once it's approved (sound_engine.py's wood, air, sub and swell voices; the p3
  sampler for tonal roles; the p3 mix with its planned silences and split reverb returns), writing
  `sound/sound_cues.json` (schema: sound_cues) and the ui, fx, tonal and verb stems the mix already reads.

ElevenLabs' sound-effects model is never the designed sound. The plan keeps it as "learn from": at most a gap-filler a
person asks for by name, never something the engine reaches for.
"""

from __future__ import annotations

from slate.errors import Refused
from slate.sound.effects import gate
from slate.sound.effects.gate import status as _gate_status


def status() -> dict:
    """Whether proof 3 is approved (a person's record with a matching SHA-256), and what's missing if not."""
    return _gate_status()


def require_proof3() -> dict:
    """Return the approval record, or raise Refused until a person has approved proof 3 (with its SHA-256)."""
    st = gate.status()
    if st["approved"]:
        return st["record"]
    why = "; ".join(st["problems"])
    raise Refused(
        f"The effects engine waits for a person's approval of proof 3, and it isn't there yet: {why}.",
        hint=("When proof 3 is approved, record it with `slate approve proof3` (or add a record with the approved file's "
              f"path, its sha256 and who approved it to {st['approvals_file']}). Until then, films mix score and voice "
              "only: `slate mix`."),
        details={"gate": "proof3", "problems": st["problems"], "approvals_file": st["approvals_file"]},
    )


__all__ = ["require_proof3", "status"]
