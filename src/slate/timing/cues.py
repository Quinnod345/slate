"""The VO cue sheet: where each approved take plays in its shot.

Same shape the films' sound engine, mix, captions and clocks read: {title, voice, lead, cues[{shot, at, type, stem,
file, gain, fade_in, fade_out, note, dur?}]}. `at` is the line's lead into its shot (from the storyboard, so a shot
with a longer lead, like film C's opening line, keeps it); `dur` appears only when a take's leaked tail was trimmed.
"""

from __future__ import annotations

from slate.timing.shots import iter_shots
from slate.timing.takes import Take

DEFAULT_GAIN_DB = -20.0  # the films' VO cue gain; the mix sets final levels
DEFAULT_FADE_IN = 0.01
DEFAULT_FADE_OUT = 0.05


def make_cues(storyboard: dict, takes: dict[str, Take], *, voice: str | None = None, gain: float = DEFAULT_GAIN_DB,
              fade_in: float = DEFAULT_FADE_IN, fade_out: float = DEFAULT_FADE_OUT) -> tuple[dict, list[str]]:
    """(vo_cues document, ids of voiced shots that have no take yet)."""
    timing = storyboard.get("timing") or {}
    lead_default = timing.get("lead")
    cues, missing = [], []
    for _i, _act, sh in iter_shots(storyboard):
        if not (sh.get("vo") or "").strip():
            continue
        take = takes.get(sh["id"])
        if take is None:
            missing.append(sh["id"])
            continue
        at = sh.get("lead", lead_default)
        cue = {"shot": sh["id"], "at": float(at if at is not None else 0.0), "type": "vo", "stem": "vo",
               "file": take.rel, "gain": gain, "fade_in": fade_in, "fade_out": fade_out,
               "note": f"VO {sh['id']}" + (f" ({take.note})" if take.note else "")}
        if take.trim:
            cue["dur"] = float(take.trim)
        cues.append(cue)
    doc = {"title": "VO cue sheet", "voice": voice, "lead": lead_default, "cues": cues}
    return doc, missing
