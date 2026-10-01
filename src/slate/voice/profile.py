"""VoiceProfile: a brand's voice as data, and the cloning policy, enforced in code.

A profile lives in the brand pack (`brand.json` "voice") and can be overridden per film (`film.json` "voice"):
provider, voice id, model, settings, seed, lexicon (brand words Whisper must hear in full), tag policy, pacing,
clean-up, the studio polish tuned for the voice (`polish`), fit and the measured targets casting ranks against.

**Voices** (docs/PLAN.md, Protections):
- Designed or library voices by default.
- The owner's own voice can be a professional voice clone, which the owner verifies with ElevenLabs themselves
  (`source: clone`, `clone_of: owner`, `clone_kind: professional`, `verified_by_owner: true`). Slate never creates
  a clone and never takes part in that verification.
- Anyone else's voice only as a real recording (`source: recording`), never a clone, and never a public figure.
- Autonomous films may use the owner's clone and no other clone; they're still held for the owner's approval.

`enforce()` refuses a profile that breaks any of this before a single request is sent, and
`vendor_problems()` repeats the check against what ElevenLabs says the voice is (a "cloned" or "famous" voice id
can't slip in as a library voice).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from slate import jsonio, paths
from slate.errors import MissingPrerequisite, Refused, UsageError

OWNER = "owner"
MODES = ("supervised", "express", "draft", "autonomous")

DEFAULTS: dict[str, Any] = {
    "provider": "elevenlabs",
    "source": "library",
    "voice_id": None,
    "voice_name": None,
    "model": None,
    # ElevenLabs' documented starting point; pinned explicitly so a re-voice in week three matches week one.
    "settings": {"stability": 0.5, "similarity_boost": 0.75},
    "seed": None,
    "output_format": "pcm_48000",
    "language": "en",
    "lexicon": [],
    "tag_policy": {"banned_extra": [], "allowed": None, "max_tags_per_line": None},
    "clone_of": None,
    "clone_kind": None,
    "verified_by_owner": False,
    "public_figure": False,
    "speaker": None,
    # vo_pace.py's proven defaults: pauses over 0.25 s kept at 80 %, then Rubber Band R3 at tempo 1.06.
    "pace": {"tempo": 1.06, "pause_keep": 0.8, "min_pause": 0.25, "threshold_db": -45.0},
    "clean": {"chain": None},
    # voice_script.py: a read starts 0.4 s into its shot and ends at least 0.25 s before the cut.
    "fit": {"lead_s": 0.4, "tail_s": 0.25},
    # rank_previews.py's targets for a narrator (override per brand from its voice brief).
    "targets": {"wpm": [150, 170], "sentence_pause_s": [0.35, 0.7], "sentence_pause_min_s": 0.12,
                "f0_range_st": [9.5, 13.0], "f0_hz": None, "gap_floor_dbfs": -75.0},
    "candidates": [],
    "cast_text": None,
    # The studio polish tuned for this voice (slate.voice.polish): {} uses Slate's neutral chain at strengths A and B.
    "polish": {},
    "brand": None,
}

# What ElevenLabs' voice `category` means for the policy.
VENDOR_CATEGORY = {"premade": "library", "high_quality": "library", "generated": "designed",
                   "cloned": "clone", "professional": "clone", "famous": "public_figure"}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _brand_pack(ref: str | Path) -> dict:
    """Load a brand pack by name or path: through slate.brand when it's installed, else straight from brand.json."""
    try:
        from slate.brand import pack as brand_pack  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        if not str(exc.name or "").startswith("slate.brand"):
            raise
        brand_pack = None
    if brand_pack is not None:
        return brand_pack.load(ref)
    candidate = Path(str(ref)).expanduser()
    if candidate.name == "brand.json" and candidate.is_file():
        return jsonio.read(candidate)
    if (candidate / "brand.json").is_file():
        return jsonio.read(candidate / "brand.json")
    for folder in paths.brand_dirs():
        if (folder / str(ref) / "brand.json").is_file():
            return jsonio.read(folder / str(ref) / "brand.json")
    raise UsageError(f"No brand pack called {str(ref)!r} (looked in {', '.join(str(p) for p in paths.brand_dirs())}).",
                     hint="slate brand init <name> creates one.")


def load(brand_pack: dict | str | Path | None, *, film: dict | None = None) -> dict:
    """The VoiceProfile for a brand pack (a loaded pack, a name or a path), with a film's overrides applied.

    Returns a complete, validated dict. A missing voice id isn't an error here (casting and design run before a
    voice is chosen); `require_voice()` is what text to speech calls.
    """
    if brand_pack is None:
        pack: dict = {}
    elif isinstance(brand_pack, dict):
        pack = brand_pack
    else:
        pack = _brand_pack(brand_pack)
    brand_voice = pack.get("voice") or {}
    if not isinstance(brand_voice, dict):
        raise UsageError(f"Brand {pack.get('name')!r} has a voice entry that isn't an object.")
    profile = _merge(DEFAULTS, brand_voice)
    film_voice = (film or {}).get("voice") if film else None
    if isinstance(film_voice, dict):
        profile = _merge(profile, film_overrides(film_voice, profile))
    elif isinstance(film_voice, str) and film_voice:
        profile["voice_id"] = film_voice  # film.json "voice": "<voice id>" shorthand
    if film and film.get("language") and not (isinstance(film_voice, dict) and film_voice.get("language")):
        profile["language"] = film["language"]
    profile["brand"] = pack.get("name") or profile.get("brand")
    if profile["source"] == "recording":
        profile["provider"] = "recording"
    problems = jsonio.validate(profile, "voice_profile")
    if problems:
        raise UsageError("The voice profile doesn't match its schema:\n  " + "\n  ".join(problems[:12]),
                         hint="Fix the brand's voice entry (brand.json) or the film's (film.json \"voice\").")
    return profile


FILM_FIELDS = ("voice_id", "voice_name", "model", "settings", "seed", "output_format", "lexicon", "tag_policy",
               "pace", "clean", "fit", "targets", "polish")
CLAIMS = ("source", "provider", "clone_of", "clone_kind", "verified_by_owner", "public_figure", "speaker")


def film_overrides(film_voice: dict, brand_profile: dict) -> dict:
    """What film.json's "voice" changes.

    A film made by `slate new` carries a snapshot of its brand's voice ("source": "brand", or "unset"): that follows
    the brand and overrides nothing (pins keep a voiced film stable, not this snapshot). A film given a voice of its
    own ("source": library, designed, clone or recording) overrides the brand field by field (nulls don't count),
    and what that voice *is* (its source and clone claims) travels with it, so a note can't relabel the brand's voice.
    A "profile" object in it is a complete profile of the film's own.
    """
    out: dict = {}
    source = film_voice.get("source")
    if source in ("library", "designed", "clone", "recording"):
        out = {k: film_voice[k] for k in FILM_FIELDS if film_voice.get(k) is not None}
        own_voice = (film_voice.get("voice_id") and film_voice.get("voice_id") != brand_profile.get("voice_id")) \
            or source == "recording"
        if own_voice:
            out.update({k: film_voice.get(k) for k in CLAIMS if k in film_voice or k in ("clone_of", "clone_kind")})
            out["source"] = source
            out.setdefault("verified_by_owner", bool(film_voice.get("verified_by_owner")))
            out.setdefault("public_figure", bool(film_voice.get("public_figure")))
            if out.get("provider") is None:
                out["provider"] = "recording" if source == "recording" else "elevenlabs"
    if isinstance(film_voice.get("profile"), dict):
        out = _merge(out, film_voice["profile"])
    return out


def for_film(film_dir: Path, film_json: dict | None = None) -> dict:
    """The profile a film uses: its brand's, with the film's own "voice" overrides."""
    fj = film_json if film_json is not None else jsonio.read(Path(film_dir) / "film.json")
    brand = fj.get("brand")
    return load(brand if brand else None, film=fj)


def require_voice(profile: dict) -> str:
    """The voice id text to speech will use, or a clear refusal."""
    if profile.get("provider") == "recording":
        who = profile.get("speaker") or "the speaker"
        raise Refused(f"This voice is real recordings of {who}, so Slate doesn't synthesize it.",
                      hint="Put each recorded read at voice/takes/<shot>/<take>.wav (48 kHz WAV), then run "
                           "`slate voice clean`, `pace` and `align` as usual.")
    if not profile.get("voice_id"):
        raise MissingPrerequisite(f"Brand {profile.get('brand')!r} has no voice chosen yet.",
                                  hint="Run `slate voice cast` (library voices) or `slate voice design`, then put the "
                                       "pick in the brand's voice profile (voice_id).")
    return str(profile["voice_id"])


# ---------------------------------------------------------------------------------------------------------------
# The cloning policy


def _who(entry: dict) -> str:
    return str(entry.get("clone_of") or entry.get("speaker") or "someone else")


def policy_problems(entry: dict, *, mode: str = "supervised") -> list[str]:
    """Every way a profile (or a casting candidate) breaks the voice rules. Empty means it may be used."""
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    problems: list[str] = []
    source = entry.get("source") or "library"
    if entry.get("public_figure"):
        problems.append("Slate never uses a public figure's voice, cloned or recorded.")
    if source == "clone" or entry.get("clone_of"):
        if entry.get("clone_of") != OWNER:
            problems.append(
                f"This is a clone of {_who(entry)}. The only clone Slate ever uses is the owner's own professional "
                "voice clone; anyone else's voice can only be a real recording of them, never a clone."
                + (" Autonomous films may use the owner's clone and no other." if mode == "autonomous" else ""))
        else:
            if entry.get("clone_kind") != "professional":
                problems.append("Only a professional voice clone is allowed (clone_kind: professional); instant clones "
                                "are refused.")
            if entry.get("verified_by_owner") is not True:
                problems.append("The owner's clone isn't verified yet. The owner verifies a professional clone with "
                                "ElevenLabs themselves (Slate never does that step); then set verified_by_owner: true "
                                "in the brand's voice profile.")
    if source == "recording" and entry.get("clone_of"):
        problems.append("A recording can't also be a clone: real recordings only.")
    return problems


def enforce(entry: dict, *, mode: str = "supervised", what: str = "this voice") -> None:
    """Raise Refused when a profile or candidate breaks the voice rules."""
    problems = policy_problems(entry, mode=mode)
    if problems:
        raise Refused(f"Refusing {what}:\n  - " + "\n  - ".join(problems),
                      hint="Use a designed or library voice, the owner's verified professional clone, or a real "
                           "recording (never a clone) of anyone else.",
                      details={"problems": problems, "mode": mode})


def vendor_problems(info: dict, entry: dict, *, mode: str = "supervised") -> list[str]:
    """Check what ElevenLabs says a voice is (its `category`) against what the profile claims."""
    category = str(info.get("category") or "").lower()
    kind = VENDOR_CATEGORY.get(category)
    name = info.get("name") or info.get("voice_id") or entry.get("voice_id")
    if kind == "public_figure":
        return [f"ElevenLabs lists {name!r} as a famous voice. Slate never uses a public figure's voice."]
    if kind == "clone":
        claimed = {**entry, "source": "clone"}
        if category == "cloned" and entry.get("clone_of") == OWNER:
            claimed["clone_kind"] = "instant"  # ElevenLabs' "cloned" category is an instant clone
        problems = policy_problems(claimed, mode=mode)
        if entry.get("source") != "clone":
            problems.insert(0, f"ElevenLabs lists {name!r} as a cloned voice ({category}), but the profile calls it "
                               f"{entry.get('source') or 'library'}.")
        return problems
    return []


def summary(profile: dict) -> str:
    """One readable paragraph about a profile (never any secret)."""
    src = profile.get("source")
    who = profile.get("voice_name") or profile.get("voice_id") or "(no voice chosen)"
    lines = [f"brand {profile.get('brand')}: {src} voice {who}",
             f"model {profile.get('model') or '(registry default)'}, settings {json.dumps(profile.get('settings'))}, "
             f"seed {profile.get('seed') if profile.get('seed') is not None else '(per line)'}",
             f"lexicon: {', '.join(profile.get('lexicon') or []) or '(none)'}"]
    if src == "clone":
        lines.append(f"clone of {profile.get('clone_of')} ({profile.get('clone_kind')}), "
                     f"verified by owner: {bool(profile.get('verified_by_owner'))}")
    return "\n".join(lines)
