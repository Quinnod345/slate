"""What the script gates read besides the script: the brand's truth, the format's pace and runtime, the pinned sources.

The brand, state and formats modules own these files. This module reads them through their interfaces when those are
installed (docs/ARCHITECTURE.md §12) and falls back to reading the files directly, so the script build works on any
checkout. It only reads: it never validates or rewrites another module's data.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from slate import jsonio, log, paths
from slate.errors import MissingPrerequisite

# Craft defaults from the films (docs/PLAN.md §7, method stages 3–5). They are method, not brand: a format or a
# script's own "timing" block overrides any of them.
DEFAULT_TIMING = {
    "lead": 0.3,  # a line starts this far into its shot (B and C; A used 0.4)
    "tail": 0.3,  # the next shot starts this long after the line ends
    "act_tail": 0.9,  # ... and this long at an act break
    "min_tail": 0.25,  # a read must end at least this long before its cut
    "speech_rate": 2.7,  # planning words per second of speech until the real takes exist
    "max_pace": 2.6,  # the ceiling: VO words per second of shot
    "pace_headroom": 1.04,  # a derived shot keeps a little room under the ceiling
}
DEFAULT_READ_TIME = {
    "floor": 1.5,  # hold >= max(floor, base + per_word x words) once the text has settled (MOTION.md §7)
    "base": 0.4,
    "per_word": 0.3,
    "exit_factor": 0.75,  # exits take 0.75x their entrance
    "default_entrance": 0.5,  # seconds, when the brand's motion has no duration for a shot's entrance token
    "max_words": 12,  # on-screen words per shot (headline plus labels)
}
RUNTIME_TOLERANCE = 0.10


# --- film --------------------------------------------------------------------------------------------------------------

def load_film(film_dir: Path) -> dict:
    path = Path(film_dir) / "film.json"
    if not path.is_file():
        raise MissingPrerequisite(f"{film_dir} has no film.json.",
                                  hint="slate new <id> --brand <brand> --format <format>")
    return jsonio.read(path)


# --- brand ---------------------------------------------------------------------------------------------------------------

def _brand_module():
    try:
        from slate.brand import pack  # noqa: PLC0415
    except ImportError:
        return None
    return pack


def _find_brand(name: str) -> Path:
    candidate = Path(name).expanduser()
    if (candidate / "brand.json").is_file():
        return candidate
    if candidate.name == "brand.json" and candidate.is_file():
        return candidate.parent
    pack = _brand_module()
    if pack is not None and hasattr(pack, "find"):
        try:
            found = Path(pack.find(name))
            if (found / "brand.json").is_file():
                return found
            if found.name == "brand.json" and found.is_file():
                return found.parent
        except Exception:  # noqa: BLE001 - fall back to searching the brand folders ourselves
            pass
    for folder in paths.brand_dirs():
        if (Path(folder) / name / "brand.json").is_file():
            return Path(folder) / name
    raise MissingPrerequisite(f"The brand pack {name!r} isn't installed (looked in "
                              f"{', '.join(str(p) for p in paths.brand_dirs())}).",
                              hint=f"slate brand init {name}, or fix the brand in film.json.")


def load_brand(name_or_path: str | None) -> dict:
    """The brand pack as a dict (read-only). A film without a brand gets an empty pack."""
    if not name_or_path:
        return {}
    folder = _find_brand(str(name_or_path))
    data = jsonio.read(folder / "brand.json")
    data = copy.deepcopy(data)
    data.setdefault("_dir", str(folder))
    return data


def _truth(brand: dict) -> dict:
    return brand.get("truth") or {}


def _accessor(name: str, brand: dict):
    pack = _brand_module()
    fn = getattr(pack, name, None) if pack is not None else None
    if callable(fn):
        try:
            return fn(brand)
        except Exception:  # noqa: BLE001 - their accessor may expect a validated pack; read the keys ourselves
            return None
    return None


def banned_words(brand: dict) -> list[str]:
    got = _accessor("banned_words", brand)
    if got is not None:
        return [str(w) for w in got]
    raw = _truth(brand).get("banned_words") or []
    return [str(w if not isinstance(w, dict) else w.get("word") or w.get("text")) for w in raw if w]


def approved_claims(brand: dict) -> list[dict]:
    got = _accessor("approved_claims", brand)
    raw = got if got is not None else (_truth(brand).get("approved_claims") or [])
    out = []
    for i, c in enumerate(raw):
        if isinstance(c, str):
            out.append({"id": f"claim-{i + 1}", "text": c})
        elif isinstance(c, dict):
            out.append({"id": str(c.get("id") or f"claim-{i + 1}"), "text": str(c.get("text") or c.get("claim") or ""),
                        **{k: v for k, v in c.items() if k not in ("id", "text", "claim")}})
    return out


def never_claim(brand: dict) -> list[str]:
    raw = _truth(brand).get("never_claim") or []
    return [str(x if not isinstance(x, dict) else x.get("text") or x.get("claim")) for x in raw if x]


def brand_sources(brand: dict) -> list[dict]:
    raw = _truth(brand).get("sources") or []
    return [s if isinstance(s, dict) else {"id": str(s)} for s in raw]


def sample_data(brand: dict) -> Any:
    return _truth(brand).get("sample_data")


def motion(brand: dict) -> dict:
    got = _accessor("motion", brand)
    return got if isinstance(got, dict) else (brand.get("motion") or {})


def durations(brand: dict) -> dict[str, float]:
    """The brand's duration scale, token -> seconds ({"T3": 0.53, …}), from whichever shape the pack uses."""
    mot = motion(brand)
    raw = mot.get("durations") or mot.get("duration_scale") or mot.get("tokens") or {}
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                out[str(k)] = float(v)
            elif isinstance(v, dict) and isinstance(v.get("seconds", v.get("s")), (int, float)):
                out[str(k)] = float(v.get("seconds", v.get("s")))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = item.get("token") or item.get("name") or item.get("id")
                val = item.get("seconds", item.get("s"))
                if key and isinstance(val, (int, float)):
                    out[str(key)] = float(val)
    return out


def brand_motion(film: dict) -> dict:
    """The film's brand motion block (named eases for landings); empty when the film has no brand pack yet."""
    name = film.get("brand")
    if not name:
        return {}
    try:
        return motion(load_brand(name))
    except MissingPrerequisite:
        log.warn(f"brand pack {name!r} isn't installed; landings can only use the linear ease")
        return {}


# --- format --------------------------------------------------------------------------------------------------------------

def load_format(name: str | None) -> tuple[dict, str | None]:
    """(format dict, problem or None). An unknown format falls back to Slate's defaults and says so."""
    if not name:
        return {}, None
    try:
        from slate import formats  # noqa: PLC0415
    except ImportError:
        formats = None
    if formats is not None and hasattr(formats, "load"):
        try:
            return dict(formats.load(name)), None
        except Exception as exc:  # noqa: BLE001
            return {}, f"format {name!r} couldn't be loaded ({getattr(exc, 'message', exc)}); used Slate's defaults"
    path = paths.FORMATS_DIR / f"{name}.json"
    if path.is_file():
        return jsonio.read(path), None
    return {}, f"format {name!r} isn't installed; used Slate's default pace and read time"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def runtime_target(film: dict, fmt: dict) -> dict:
    """{target, min, max, source}: the requester's runtime from film.json first, then the format's."""
    targets = film.get("targets") or {}
    t = _num(targets.get("runtime_s"))
    if t:
        return {"target": t, "source": "film.json targets.runtime_s"}
    rt = fmt.get("runtime_s", fmt.get("runtime"))
    if isinstance(rt, (int, float)):
        return {"target": float(rt), "source": "format runtime_s"}
    if isinstance(rt, (list, tuple)) and len(rt) == 2:
        return {"min": float(rt[0]), "max": float(rt[1]), "source": "format runtime range"}
    if isinstance(rt, dict):
        target = _num(rt.get("target") or rt.get("target_s"))
        lo, hi = _num(rt.get("min") or rt.get("min_s")), _num(rt.get("max") or rt.get("max_s"))
        out = {"source": "format runtime"}
        if target:
            out["target"] = target
        if lo is not None and hi is not None:
            out.update(min=lo, max=hi)
        return out if len(out) > 1 else {}
    return {}


def timing_settings(script: dict, fmt: dict) -> dict:
    """Pace and placement: Slate's defaults, then the format's pace, then the script's own timing block."""
    out = dict(DEFAULT_TIMING)
    pace = fmt.get("pace")
    if isinstance(pace, dict):
        aliases = {"max_words_per_second": "max_pace", "max_wps": "max_pace", "words_per_second": "speech_rate",
                   "planning_rate": "speech_rate"}
        for k, v in pace.items():
            key = aliases.get(k, k)
            if key in out and _num(v) is not None:
                out[key] = float(v)
    elif _num(pace):
        out["max_pace"] = float(pace)
    for k, v in (script.get("timing") or {}).items():
        if k in out and _num(v) is not None:
            out[k] = float(v)
    return out


def read_time_settings(script: dict, fmt: dict) -> dict:
    out = dict(DEFAULT_READ_TIME)
    for block in (fmt.get("read_time"), (script.get("rules") or {}).get("read_time")):
        if isinstance(block, dict):
            for k, v in block.items():
                if k in out and _num(v) is not None:
                    out[k] = float(v)
    density = fmt.get("on_screen_max_words") or (script.get("rules") or {}).get("on_screen_max_words")
    if _num(density):
        out["max_words"] = float(density)
    return out


# --- truth ------------------------------------------------------------------------------------------------------------------

def load_truth(film_dir: Path) -> dict:
    """brief/truth.json (the film's pinned sources and researched claims), or an empty truth pack."""
    return jsonio.read(Path(film_dir) / "brief" / "truth.json", default=None) or {}
