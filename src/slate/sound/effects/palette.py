"""A brand's sound palette: the starting point every brand begins from, and the brand's own values over it.

The starting point is proof 2, the sound proof the owner called "perfect, intentional": event-tied wood, air, subs,
small felt notes and pads, the effects clearly audible but under the voice (UI clicks about 17 LU and the effects
about 11 LU under it, measured on proof 2's stems), every sound on its beat. It carries the owner's perfecting notes
as rules: tonal roles on real sampled instruments, no glides or slide-ups, no room-tone drone, one deliberate sound
per kind of interaction with natural variation between repeats. Every brand gets its own palette during onboarding
(`slate brand onboard`); what the brand sets in brand.json `sound.palette` wins, key by key.

This module is not gated: the mix and the checks read the palette today. Spotting and rendering effects from it is
the gated part (see slate.sound.effects).

Nothing here names a brand. Key, tempo, instrumentation and motif belong to the brand pack.
"""

from __future__ import annotations

import copy
from typing import Any

# The kinds a picture beat can be (docs/ARCHITECTURE.md §5), each with the one sound of its kind in the starting
# palette. "family" is the material a person would name; "stem" is where it sits in the mix; "anchor" is what the ear
# syncs to (onset: the strike's first sample within 12 dB of its peak; onset30: paper's first audible sound; peak20 /
# peak50: the loudest 20 or 50 ms of air or a swell; end: where a lift or a drawn line finishes).
STARTING_KINDS: dict[str, dict[str, Any]] = {
    "tap": {"family": "wood block", "stem": "ui", "anchor": "onset", "what": "a row, check, chip or button ticks in"},
    "type": {"family": "soft air", "stem": "fx", "anchor": "peak50", "what": "text writes on"},
    "land": {"family": "wood knock", "stem": "ui", "anchor": "onset", "what": "a card, sheet or panel lands"},
    "snap": {"family": "clave", "stem": "ui", "anchor": "onset", "what": "a state flips or snaps into place"},
    "count": {"family": "tuned wood clicks", "stem": "ui", "anchor": "onset", "what": "a numeral counts"},
    "draw": {"family": "air on the draw's ease", "stem": "fx", "anchor": "end", "what": "a line or chart draws itself"},
    "data": {"family": "small felt note", "stem": "tonal", "anchor": "onset", "what": "a value, bar or marker lands"},
    "page": {"family": "swish", "stem": "fx", "anchor": "peak20", "what": "a sheet slides, deals or turns"},
    "stamp": {"family": "temple block with a felt note", "stem": "ui", "anchor": "onset", "what": "a group completes or a seal sets"},
    "whoosh": {"family": "air", "stem": "fx", "anchor": "peak20", "what": "a view carries into the next"},
    "swell": {"family": "sampled string pad", "stem": "tonal", "anchor": "peak50", "what": "the picture fills or opens"},
    "character": {"family": "small felt notes (the motif)", "stem": "tonal", "anchor": "onset", "what": "the brand's character or mark gestures"},
    "impact": {"family": "sub with a hollow temple knock", "stem": "fx", "anchor": "onset", "what": "an act's one big landing"},
}

STARTING_PALETTE: dict[str, Any] = {
    "source": "proof 2, the approved starting point (every brand gets its own during onboarding)",
    "levels": {
        # The voice is levelled by the RMS of its voiced part; every other cue by its peak.
        "voice_gain_dbfs": -20.0,
        # Where each engine stem sits against the voice (integrated LU), measured on proof 2's stems.
        "lu_vs_voice": {"ui": -17.0, "fx": -11.2},
        "tolerance_lu": 2.0,
        # A fixed trim per stem, calibrated once per brand (`slate mix --calibrate` measures it); 0 until then.
        "stem_trim_db": {},
        # How far a stem ducks under the voice when the mix is asked to duck it (the UI stays crisp).
        "duck_db": {"tonal": -6.0, "amb": -6.0, "fx": -3.0, "verb": -4.0, "tverb": -4.0},
        # The composed score: this many LU under the voice, ducked a few dB under every word.
        "music": {"under_voice_lu": 15.0, "duck_db": 4.0, "attack_s": 0.08, "release_s": 0.65},
        # Optional: the effects stem never goes over this true peak in the final mix (the films used -7 dBTP).
        "effects_ceiling_dbtp": None,
        "linked_limit": True,
        "limiter": "slate",  # or "ffmpeg" for a run that must match a delivered film's alimiter exactly
    },
    "kinds": STARTING_KINDS,
    "rules": {
        "no_glides": True,         # no sustained tone slides in pitch (it reads as a siren)
        "no_slide_ups": True,      # a rising glide is the worst of them
        "no_drone": True,          # no room-tone bed under the effects
        "one_sound_per_kind": True,
        "no_sound_shared_by_kinds": True,
        "no_identical_repeats": True,  # repeats vary the way real playing does, never the same hit twice in a row
        "sync_frames": 1.0,        # every sound within a frame of its beat
        "sampled_tonal_roles": True,  # small notes and pads play on real sampled instruments; never synthesized
    },
    # What the effects are made of. The composed score never plays these (they go in its negative styles).
    "materials": ["woodblock", "temple block", "clave", "wood knock", "rim click", "marimba", "felt marimba",
                  "xylophone"],
    # Timbres the starting palette never uses, in effects or score.
    "banned_timbres": ["bells", "chimes", "glockenspiel", "celesta", "crotales", "tubular bells", "vibraphone",
                       "music box", "metallic hits", "glass", "buzzers"],
    # Tonal roles on Apple's sampled instruments (installed with GarageBand and Logic; never redistributed).
    "instruments": {
        "felt_note": {"library": "logic-sampler", "exs": "10 World/01 African/African Marimba.exs",
                      "why": "small, soft felt notes (soft velocity layers, alternate takes)"},
        "pad_low": {"library": "logic-sampler", "exs": "09 Orchestral/09 Strings/Basses Legato.exs",
                    "why": "the floor of a pad bloom"},
        "pad_mid_low": {"library": "logic-sampler", "exs": "09 Orchestral/09 Strings/Cellos Legato.exs",
                        "why": "pads, low voices"},
        "pad_mid": {"library": "logic-sampler", "exs": "09 Orchestral/09 Strings/Violas Legato.exs",
                    "why": "pads, middle voices"},
        "pad_high": {"library": "logic-sampler", "exs": "09 Orchestral/09 Strings/Violins 2 Legato.exs",
                     "why": "pads, upper voices"},
    },
    "glide": {"drift_cents": 50.0, "wander_cents": 60.0, "min_len_s": 0.4},
    "drone": {"max_continuous_s": 8.0, "floor_dbfs": -60.0, "stationary_db": 6.0, "window_s": 0.5},
    "clarity": {"band_hz": [300.0, 4000.0], "min_db": 3.0},
    "never_over": {"margin_db": 0.0},
    "score": {
        "positive_styles": ["instrumental", "warm and restrained", "sparse, leaves room for a narrator",
                            "continuous music with no silences or gaps", "steady level, no fade-in and no fade-out"],
        "negative_styles": ["vocals", "choir", "drums", "risers", "white noise", "static", "silence",
                            "fade out", "abrupt ending"],
        "seeds": [7, 21],
        "level_lufs": -16.0,
    },
}


def deep_merge(base: dict, extra: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def from_brand(brand: dict | None) -> dict:
    """The palette for a brand pack: the starting point, then brand.json `sound.palette` over it, key by key.

    The brand's own `sound.materials` and `sound.banned_timbres` are added to the starting lists (a brand extends the
    rules; it never silently drops one, so removing a starting rule needs an explicit value in its palette).
    """
    sound = (brand or {}).get("sound") or {}
    pal = deep_merge(STARTING_PALETTE, sound.get("palette") or {})
    for key in ("materials", "banned_timbres"):
        extra = sound.get(key) or []
        pal[key] = list(dict.fromkeys([*pal.get(key, []), *extra]))
    pal["brand"] = (brand or {}).get("name")
    pal["from_brand"] = bool(sound.get("palette"))
    return pal


def kind_info(pal: dict, kind: str) -> dict:
    return (pal.get("kinds") or {}).get(kind) or {}


def anchor_rule(pal: dict, kind: str, default: str = "onset") -> str:
    return kind_info(pal, kind).get("anchor", default)
