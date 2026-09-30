"""Pins: what a film used the first time, kept so week three matches week one.

`film.json` "pins" records, at first use, the TTS model, the voice id and its settings, seeds and request ids, the music
and plate model ids, and the Remotion and Chrome versions. The first value wins: pinning a key that's already pinned
returns the pinned value and changes nothing, so a registry refresh never moves a pinned film. Re-pinning is explicit
(`slate film repin KEY VALUE --approved-by NAME --reason "…"`), meant to follow an A/B test, and every pin and re-pin is
kept in "pin_log".

Model pins use the registry's role names (tts, voice_design, music, plate_video, …) with the model id as the value,
which is exactly what `slate.config.model(role, film)` reads first.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slate import config
from slate.errors import Refused, UsageError
from slate.state import _util, film

KNOWN: dict[str, str] = {
    "tts": "the TTS model every line is read with (registry role tts)",
    "voice_design": "the voice-design model (registry role voice_design)",
    "music": "the music model scores are composed on (registry role music)",
    "plate_video": "the plate video model (registry role plate_video)",
    "plate_still": "the plate still model (registry role plate_still)",
    "voice_id": "the voice id",
    "voice_settings": "the voice settings (stability, similarity, style, speed, …)",
    "seed.<line>": "the seed a take or plate was made with, per line or shot",
    "request_id.<line>": "the vendor request id behind a take or plate",
    "remotion": "the Remotion version renders use",
    "chrome": "the headless Chrome version renders use",
}


def all_pins(film_dir: Path) -> dict:
    return dict(film.load(film_dir).get("pins") or {})


def get(film_dir: Path, key: str) -> Any:
    """The pinned value, or None when nothing is pinned under `key`."""
    return (film.load(film_dir).get("pins") or {}).get(key)


def history(film_dir: Path, key: str | None = None) -> list[dict]:
    log = film.load(film_dir).get("pin_log") or []
    return [e for e in log if key is None or e.get("key") == key]


def _check_key(key: str) -> str:
    key = str(key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}", key):
        raise UsageError(f"{key!r} isn't a pin key: letters, digits and . _ : / - only (e.g. tts, voice_id, seed.S01).")
    return key


def pin(film_dir: Path, key: str, value: Any, *, repin: bool = False, approved_by: str | None = None,
        reason: str | None = None, evidence: str | None = None, source: str | None = None) -> Any:
    """Pin `key` at first use and return the value in force. First use wins: an existing pin is returned unchanged.

    With repin=True the pin moves to `value`, which needs the person who approved it and why (after an A/B test);
    the previous value stays in pin_log.
    """
    key = _check_key(key)
    if value is None:
        raise UsageError(f"Pin {key} needs a value.")
    if repin and (not (approved_by or "").strip() or not (reason or "").strip()):
        raise Refused(f"Re-pinning {key} is explicit: say who approved it (--approved-by) and why (--reason).",
                      hint="A re-pin normally follows an A/B test of the old and new value; name it with --evidence.")
    out: dict[str, Any] = {}

    def change(data: dict) -> None:
        pins = data.setdefault("pins", {})
        log = data.setdefault("pin_log", [])
        if key in pins and not repin:
            out["value"] = pins[key]
            return
        previous = pins.get(key)
        if key in pins and previous == value:
            out["value"] = previous
            return
        pins[key] = value
        event = "repin" if (repin and key in pins and previous is not None) else "pin"
        log.append({"key": key, "event": event, "at": _util.now(), "value": value, "previous": previous,
                    "approved_by": approved_by if repin else None, "reason": reason if repin else None,
                    "evidence": evidence if repin else None, "source": source})
        out["value"] = value

    film.update(film_dir, change)
    return out["value"]


def repin(film_dir: Path, key: str, value: Any, *, approved_by: str, reason: str,
          evidence: str | None = None) -> Any:
    """Move a pin on purpose. Refuses a key that was never pinned (use pin for first use)."""
    key = _check_key(key)
    if get(film_dir, key) is None:
        raise UsageError(f"{key} isn't pinned yet, so there's nothing to re-pin; `slate film pin {key} VALUE` pins it.")
    return pin(film_dir, key, value, repin=True, approved_by=approved_by, reason=reason, evidence=evidence)


def model(film_dir: Path, role: str) -> str:
    """The model for a role, pinned at first use: the pin when there is one, else today's configured model (then
    pinned). Voice, music and plate stages call this instead of config.model so the film never drifts."""
    current = get(film_dir, role)
    if isinstance(current, str) and current:
        return current
    if isinstance(current, dict) and (current.get("model") or current.get("model_id")):
        return str(current.get("model") or current.get("model_id"))
    chosen = config.model(role, film.load(film_dir))
    return str(pin(film_dir, role, chosen, source="configured model at first use"))


def tool(film_dir: Path, name: str, version: str | None = None) -> str:
    """A tool version (remotion, chrome), pinned at first use: the given version or the registry's."""
    current = get(film_dir, name)
    if current:
        return str(current)
    chosen = version or str(config.tool(name).get("version"))
    return str(pin(film_dir, name, chosen, source="tool version at first use"))
