"""film.json: what a film is, made once from its format and brand, then changed only on purpose.

A film is a folder, and film.json is its identity card: id, title, format, language, the clock (fps_num/fps_den), the
size, the brand and its voice, the targets (runtime, loudness), the budget, the mode, how much generation the requester
wants, where it came from (parent and derivation), what it has pinned, and its versions. Every module reads it; only
film state writes it, always validated against schemas/film.schema.json and always atomically.

Sizes and frame rates never appear as numbers in this code: they come from the film's format (`slate.formats`), and a
derived film keeps its parent's clock so reused timings stay frame-exact.
"""

from __future__ import annotations

import copy
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from slate import __version__, jsonio, ledger, log, paths
from slate.errors import Refused, UsageError
from slate.state import _util, deps

FILM_JSON = "film.json"
SCHEMA = "film"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
MODES = ("supervised", "express", "draft", "autonomous")
GENERATION = ("none", "rarely", "where-it-helps")
DEFAULT_LANGUAGE = _util.DEFAULT_LANGUAGE

# Keys `slate film set` may change, and the ones it never touches (they have their own commands).
PROTECTED = {
    "schema": "the schema version is Slate's to change",
    "id": "a film's id is its folder name; make a new film instead",
    "parent": "derived films are made with `slate variant`",
    "derivation": "derived films are made with `slate variant`",
    "pins": "pins change only with `slate film pin` (first use) or `slate film repin` (explicit, approved)",
    "pin_log": "the pin history is written by `slate film pin|repin`",
    "versions": "versions are started by the review loop",
    "created_at": "history doesn't change",
    "slate_version": "history doesn't change",
    "language": "v1 films speak one language; a new language is a derived film (`slate variant <film> <child> --language xx`)",
    "format_source": "set with the format",
    "brand_dir": "set with the brand",
}
CLOCK_KEYS = ("fps_num", "fps_den", "width", "height", "aspect")


# ---------------------------------------------------------------------------------------------------------------
# Reading and writing


def path(film_dir: Path) -> Path:
    return Path(film_dir) / FILM_JSON


def load(film_dir: Path) -> dict:
    """film.json as a dict. A folder without one is a usage error naming the folder."""
    p = path(film_dir)
    if not p.is_file():
        raise UsageError(f"{Path(film_dir)} isn't a Slate film (no film.json).",
                         hint="slate new <id> --brand <brand> --format <format> makes one.")
    data = jsonio.read(p)
    if not isinstance(data, dict):
        raise UsageError(f"{p} isn't a JSON object.")
    return data


def validate(data: dict) -> list[str]:
    return jsonio.validate(data, SCHEMA)


def save(film_dir: Path, data: dict) -> None:
    """Validate against the film schema and write atomically, under the film's state lock."""
    jsonio.require_valid(data, SCHEMA, what="film.json")
    with _util.state_lock(film_dir):
        jsonio.write_atomic(path(film_dir), data)


def update(film_dir: Path, change: Callable[[dict], Any]) -> dict:
    """Read, change and save film.json as one step under the state lock. `change` edits the dict in place."""
    with _util.state_lock(film_dir):
        data = load(film_dir)
        change(data)
        save(film_dir, data)
        return data


def film_id(film_dir: Path) -> str:
    try:
        return str(load(film_dir).get("id") or Path(film_dir).name)
    except UsageError:
        return Path(film_dir).name


def mode_of(film_dir: Path) -> str:
    return str(load(film_dir).get("mode") or "supervised")


def language_of(film_dir: Path) -> str:
    try:
        return str(load(film_dir).get("language") or DEFAULT_LANGUAGE)
    except UsageError:
        return DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------------------------------------------
# Format defaults (defensive: the format templates are another module's data)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _pair(v: Any) -> tuple[int, int] | None:
    """(width, height) from {width, height}, [w, h] or "WxH"."""
    if isinstance(v, dict):
        w, h = _num(v.get("width") or v.get("w")), _num(v.get("height") or v.get("h"))
    elif isinstance(v, (list, tuple)) and len(v) == 2:
        w, h = _num(v[0]), _num(v[1])
    elif isinstance(v, str) and re.fullmatch(r"\s*\d+\s*[x×X]\s*\d+\s*", v):
        a, b = re.split(r"[x×X]", v)
        w, h = _num(a), _num(b)
    else:
        return None
    if not w or not h:
        return None
    return int(w), int(h)


def aspect_of(width: int, height: int) -> str:
    g = math.gcd(int(width), int(height)) or 1
    return f"{int(width) // g}:{int(height) // g}"


def parse_aspect(aspect: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[:/x]\s*(\d+)\s*", str(aspect or ""))
    if not m or int(m.group(1)) == 0 or int(m.group(2)) == 0:
        raise UsageError(f"{aspect!r} isn't an aspect ratio; write it like 9:16, 1:1 or 4:5.")
    return int(m.group(1)), int(m.group(2))


def even(n: float) -> int:
    """Video sizes are even (chroma subsampling needs it)."""
    return max(2, int(round(n / 2.0)) * 2)


def size_for_aspect(aspect: str, base_width: int, base_height: int) -> tuple[int, int]:
    """A size for `aspect` that keeps the base size's short side (a 16:9 frame's short side becomes a 9:16 width)."""
    aw, ah = parse_aspect(aspect)
    short = min(base_width, base_height)
    if aw <= ah:  # portrait or square: the width is the short side
        return even(short), even(short * ah / aw)
    return even(short * aw / ah), even(short)


def _fps(fmt: dict) -> tuple[int, int] | None:
    num, den = fmt.get("fps_num"), fmt.get("fps_den")
    if isinstance(num, int) and isinstance(den, int) and num > 0 and den > 0:
        return num, den
    for key in ("fps", "frame_rate", "rate"):
        v = fmt.get(key)
        if v is None:
            continue
        if isinstance(v, dict):
            n, d = v.get("num") or v.get("fps_num"), v.get("den") or v.get("fps_den")
            if isinstance(n, int) and isinstance(d, int) and n > 0 and d > 0:
                return n, d
            continue
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) for x in v):
            return int(v[0]), int(v[1])
        if isinstance(v, str) and "/" in v:
            a, b = v.split("/", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                return int(a), int(b)
        value = _num(v)
        if value and value > 0:
            return fps_fraction(value)
    return None


# Broadcast (NTSC-family) rates run slow by this factor: a standard, not any film's rate.
PULLDOWN = Fraction(1000, 1001)


def fps_fraction(value: float) -> tuple[int, int]:
    """A frame rate as (num, den). Broadcast rates written as decimals (x.97, x.976, x.94) map to their exact
    fraction: an integer rate slowed by the pull-down factor."""
    if abs(value - round(value)) < 1e-9:
        return int(round(value)), 1
    nominal = value / float(PULLDOWN)
    if abs(nominal - round(nominal)) < 2e-3:
        exact = int(round(nominal)) * PULLDOWN
        return exact.numerator, exact.denominator
    f = Fraction(value).limit_denominator(1000)
    return f.numerator, f.denominator


def _range(v: Any) -> tuple[float | None, float | None, float | None]:
    """(default, low, high) from a number, [lo, hi] or {min, max, default|target}."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v), None, None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = _num(v[0]), _num(v[1])
        return None, lo, hi
    if isinstance(v, dict):
        default = _num(v.get("default") or v.get("target") or v.get("typical"))
        lo = _num(v.get("min") if v.get("min") is not None else v.get("lo", v.get("low")))
        hi = _num(v.get("max") if v.get("max") is not None else v.get("hi", v.get("high")))
        return default, lo, hi
    return None, None, None


_RANGE_KEYS = {"min", "max", "lo", "hi", "low", "high", "default", "target", "typical", "unit", "note"}


def _amount(v: Any) -> float | None:
    """A budget amount: a number; the top of a range; or the sum of components (e.g. voice + score)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, tuple)):
        nums = [_num(x) for x in v if _num(x) is not None]
        return max(nums) if nums else None
    if isinstance(v, dict):
        if set(v) <= _RANGE_KEYS:
            default, lo, hi = _range(v)
            return hi if hi is not None else (default if default is not None else lo)
        parts = [_amount(x) for k, x in v.items() if k not in ("unit", "note")]
        parts = [p for p in parts if p is not None]
        return sum(parts) if parts else None
    return None


def format_defaults(fmt: dict, *, aspect: str | None = None) -> dict:
    """What film.json takes from a format: size, clock, runtime, loudness and budget. Raises when size or clock is
    missing, because a film can't have a clock nobody chose."""
    name = str(fmt.get("name") or "format")
    size = None
    chosen_aspect = None
    aspects = fmt.get("aspects")
    if aspects:
        options: dict[str, tuple[int, int]] = {}
        if isinstance(aspects, dict):
            for key, val in aspects.items():
                pair = _pair(val)
                if pair:
                    options[str(key)] = pair
        elif isinstance(aspects, list):
            for val in aspects:
                pair = _pair(val) if isinstance(val, dict) else None
                if pair and isinstance(val, dict):
                    options[str(val.get("aspect") or aspect_of(*pair))] = pair
        want = aspect or fmt.get("default_aspect") or (next(iter(options)) if options else None)
        if want and want in options:
            size, chosen_aspect = options[want], want
        elif aspect and options:
            raise UsageError(f"Format {name} comes in {', '.join(options)}, not {aspect}.")
    if size is None:
        for key in ("size", "resolution", "canvas", "frame"):
            size = _pair(fmt.get(key))
            if size:
                break
    if size is None:
        size = _pair({"width": fmt.get("width"), "height": fmt.get("height")})
    video = fmt.get("video") if isinstance(fmt.get("video"), dict) else {}
    if size is None and video:
        size = _pair(video)
    if size is None:
        raise UsageError(f"Format {name} has no frame size.",
                         hint="A format needs width and height (or size: {width, height}).")
    width, height = size
    if aspect and not chosen_aspect and aspect_of(width, height) != aspect_of(*parse_aspect(aspect)):
        width, height = size_for_aspect(aspect, width, height)
    clock = _fps(fmt) or (_fps(video) if video else None)
    if clock is None:
        raise UsageError(f"Format {name} has no frame rate.",
                         hint="A format needs fps_num and fps_den (or fps: \"num/den\").")
    runtime_default, lo, hi = None, None, None
    for key in ("runtime_s", "runtime", "duration_s", "duration"):
        if fmt.get(key) is not None:
            runtime_default, lo, hi = _range(fmt[key])
            break
    runtime = runtime_default
    if runtime is None and lo is not None and hi is not None:
        runtime = float(round((lo + hi) / 2.0))
    elif runtime is None:
        runtime = hi or lo
    loud = fmt.get("loudness") if isinstance(fmt.get("loudness"), dict) else {}
    audio = fmt.get("audio") if isinstance(fmt.get("audio"), dict) else {}
    lufs = _first_num(fmt.get("lufs"), loud.get("lufs"), loud.get("integrated"), loud.get("integrated_lufs"),
                      loud.get("target"), audio.get("lufs"))
    tp = _first_num(fmt.get("true_peak"), loud.get("true_peak"), loud.get("tp"), loud.get("true_peak_dbtp"),
                    loud.get("true_peak_db"), audio.get("true_peak"))
    budget_raw = fmt.get("budget") if isinstance(fmt.get("budget"), dict) else {}
    eleven = higgs = None
    rework = None
    for key, val in budget_raw.items():
        low = key.lower()
        if "eleven" in low:
            eleven = _amount(val)
        elif "higgs" in low:
            higgs = _amount(val)
        elif "rework" in low:
            rework = _num(val)
    return {
        "width": int(width), "height": int(height), "aspect": aspect_of(width, height),
        "fps_num": int(clock[0]), "fps_den": int(clock[1]),
        "runtime_s": runtime, "runtime_range_s": [lo, hi] if lo is not None and hi is not None else None,
        "lufs": lufs, "true_peak": tp,
        "budget": {"elevenlabs": eleven, "higgsfield": higgs,
                   "rework_factor": rework or float(ledger.budget_config().get("rework_factor", 1.5))},
    }


def _first_num(*values: Any) -> float | None:
    for v in values:
        n = _num(v)
        if n is not None:
            return n
    return None


# ---------------------------------------------------------------------------------------------------------------
# Building a film.json


def check_id(film_id: str) -> str:
    fid = str(film_id or "").strip()
    if not ID_RE.match(fid):
        slug = re.sub(r"[^a-z0-9._-]+", "-", fid.lower()).strip("-._") or "my-film"
        raise UsageError(f"{film_id!r} can't be a film id: use lowercase letters, digits, dots, dashes and "
                         f"underscores (it's the folder name).", hint=f"For example: {slug[:80]}")
    return fid


def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    return mode


def check_generation(generation: str) -> str:
    if generation not in GENERATION:
        raise UsageError(f"Unknown generation setting {generation!r}; one of {', '.join(GENERATION)}.")
    return generation


def default_title(film_id: str) -> str:
    words = re.sub(r"[._-]+", " ", film_id).strip()
    return (words[:1].upper() + words[1:]) if words else film_id


def voice_for(pack: dict | None, mode: str) -> dict:
    """The film's voice at the start: the brand's voice, or a library voice in draft mode (no design spend)."""
    if mode == "draft":
        return {"source": "library", "provider": None, "voice_id": None, "model": None, "settings": None,
                "note": "draft films use a library voice: no voice design or clone spend"}
    bv = deps.brand_voice(pack or {}) if pack else None
    if bv:
        return {"source": "brand", "provider": bv.get("provider"), "voice_id": bv.get("voice_id"),
                "model": bv.get("model"), "settings": bv.get("settings"),
                "note": "from the brand pack's voice; the voice stage pins what it uses"}
    return {"source": "unset", "provider": None, "voice_id": None, "model": None, "settings": None,
            "note": "the brand pack has no voice yet: cast or design one (slate voice cast)"}


def build(film_id: str, *, brand: str, format: str, mode: str = "supervised", title: str | None = None,
          generation: str = "rarely", language: str | None = None, runtime_s: float | None = None,
          aspect: str | None = None, fmt: dict | None = None, fmt_source: str | None = None,
          pack: dict | None = None, brand_dir: Path | None = None) -> dict:
    """The film.json for a new film (not written). Format defaults, then the mode's rules, then explicit choices."""
    check_id(film_id)
    check_mode(mode)
    check_generation(generation)
    if fmt is None:
        fmt, fmt_source = deps.format_load(format)
    d = format_defaults(fmt, aspect=aspect)
    budget = dict(d["budget"])
    if mode in ("draft", "autonomous") or generation == "none":
        # Draft films have no plates; autonomous films generate nothing by default (each call needs a person's yes).
        budget["higgsfield"] = 0.0
    runtime = float(runtime_s) if runtime_s is not None else d["runtime_s"]
    if runtime is not None and runtime <= 0:
        raise UsageError("--runtime must be a number of seconds above zero.")
    data = {
        "schema": 1,
        "id": film_id,
        "title": title or default_title(film_id),
        "format": str(fmt.get("name") or format),
        "format_source": fmt_source,
        "language": language or DEFAULT_LANGUAGE,
        "fps_num": d["fps_num"],
        "fps_den": d["fps_den"],
        "width": d["width"],
        "height": d["height"],
        "aspect": d["aspect"],
        "brand": brand,
        "brand_dir": str(brand_dir) if brand_dir else None,
        "voice": voice_for(pack, mode),
        "targets": {"runtime_s": runtime, "runtime_range_s": d["runtime_range_s"], "lufs": d["lufs"],
                    "true_peak": d["true_peak"]},
        "budget": budget,
        "mode": mode,
        "generation": generation,
        "parent": None,
        "derivation": None,
        "pins": {},
        "pin_log": [],
        "models": {},
        "versions": [{"n": 1, "started_at": _util.now(), "note": "first version", "delivered_at": None}],
        "created_at": _util.now(),
        "slate_version": __version__,
    }
    return data


def create(film_id: str, *, brand: str | None, format: str | None, mode: str = "supervised",
           title: str | None = None, parent: Any = None, derivation: dict | None = None,
           generation: str = "rarely", films_dir: str | Path | None = None, request: str | None = None,
           request_source: str | None = None, runtime_s: float | None = None, aspect: str | None = None,
           language: str | None = None, git: bool | None = None) -> Path:
    """Make a film folder and return it. With `parent`, the film is derived (see `slate.state.derive`).

    The folder goes under `paths.films()` (outside iCloud) unless `films_dir` says otherwise. The mode's rules apply
    from the first moment: an autonomous film refuses a brand whose four references aren't approved.
    """
    from slate.state import derive, modes, scaffold  # noqa: PLC0415 (import cycle: they use this module)

    film_id = check_id(film_id)
    if parent is not None:
        spec = dict(derivation or {})
        parent_dir = parent if isinstance(parent, Path) else paths.film_dir(parent)
        return derive.create(parent_dir, film_id, format=format or spec.get("format"),
                             aspect=spec.get("aspect"), language=spec.get("language") or language,
                             cut=spec.get("cut_s", spec.get("cut")), title=title,
                             mode=mode if mode != "supervised" else None, films_dir=films_dir, git=git)
    if derivation:
        raise UsageError("A derivation needs a parent film (--parent).")
    if not brand:
        raise UsageError("A film needs a brand (--brand).", hint="The neutral demo brand is 'demo'.")
    check_mode(mode)
    check_generation(generation)
    if not format and mode == "autonomous":
        format = modes.default_format("autonomous")
    if not format:
        raise UsageError("A film needs a format (--format).",
                         hint=f"Known formats: {', '.join(deps.format_names()) or 'none installed yet'}.")
    brand_dir = deps.brand_find(brand)
    pack = deps.brand_load(brand)
    modes.check_new(mode, brand, pack)
    target = _target_dir(film_id, films_dir)
    fmt, fmt_source = deps.format_load(format)
    data = build(film_id, brand=brand, format=format, mode=mode, title=title, generation=generation,
                 language=language, runtime_s=runtime_s, aspect=aspect, fmt=fmt, fmt_source=fmt_source, pack=pack,
                 brand_dir=brand_dir)
    scaffold.write(target, data, request=request, request_source=request_source, git=git)
    return target


def _target_dir(film_id: str, films_dir: str | Path | None) -> Path:
    base = Path(films_dir).expanduser() if films_dir else paths.films()
    target = (base / film_id).resolve() if base.exists() else base / film_id
    if target.exists() and any(target.iterdir()):
        raise Refused(f"{target} already exists and isn't empty; Slate never writes over a film.",
                      hint=f"Pick another id, or look at it: slate status --film {target}")
    icloud = Path.home() / "Library" / "Mobile Documents"
    try:
        target.resolve().relative_to(icloud.resolve())
        log.warn(f"{target} is inside iCloud Drive. Films hold heavy media and watchers; the default "
                 f"({paths.films()}) keeps them out of sync.")
    except ValueError:
        pass
    return target


# ---------------------------------------------------------------------------------------------------------------
# Changing a film on purpose (`slate film set`)


def _get_dotted(data: dict, key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_value(film_dir: Path, key: str, value: Any) -> dict:
    """Set one key (dotted for nested: targets.runtime_s) with the rules that keep a film coherent.

    Returns {"key", "old", "new"}. Protected keys point at their own command; the clock and size refuse once shot ids
    froze or the timeline locked; a mode change re-runs the mode's rules; a format change re-applies the format's
    defaults, but only before anything was made.
    """
    from slate.state import lock, modes  # noqa: PLC0415

    key = key.strip()
    top = key.split(".")[0]
    if top in PROTECTED:
        raise Refused(f"{key} can't be set here: {PROTECTED[top]}.")
    schema_props = set(jsonio.schema(SCHEMA)["properties"])
    current = load(film_dir)
    if top not in schema_props and top not in current:
        raise UsageError(f"film.json has no key {top!r}.", hint=f"Keys: {', '.join(sorted(schema_props - set(PROTECTED)))}")
    if top in CLOCK_KEYS:
        why = lock.clock_frozen_reason(film_dir)
        if why:
            raise Refused(f"The film's clock and size are fixed now: {why}.",
                          hint="Make a derived film for a new aspect: slate variant <film> <child> --aspect W:H")
    if top == "mode":
        modes.check_new(str(value), current.get("brand"), deps.brand_load(current["brand"]))
    if top == "brand":
        from slate.state import approvals  # noqa: PLC0415

        if approvals.load(film_dir)["approvals"]:
            raise Refused("The brand can't change once anything is approved; start a new film for another brand.")
        deps.brand_find(str(value))
    if top == "format":
        if lock.load(film_dir)["files"] or lock.frozen_shot_ids(film_dir):
            raise Refused("The format sets the film's size, clock and budget, and things are already made from them.",
                          hint="Make a derived film instead: slate variant <film> <child> --format <format>")
    old = copy.deepcopy(_get_dotted(current, key))

    def change(data: dict) -> None:
        if top == "format":
            fmt, src = deps.format_load(str(value))
            d = format_defaults(fmt)
            data.update({"format": str(fmt.get("name") or value), "format_source": src, "width": d["width"],
                         "height": d["height"], "aspect": d["aspect"], "fps_num": d["fps_num"],
                         "fps_den": d["fps_den"]})
            data["targets"].update({"runtime_s": d["runtime_s"], "runtime_range_s": d["runtime_range_s"],
                                    "lufs": d["lufs"], "true_peak": d["true_peak"]})
            data["budget"].update(d["budget"])
            return
        node = data
        parts = key.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        if top in ("width", "height"):
            data["aspect"] = aspect_of(data["width"], data["height"])

    data = update(film_dir, change)
    return {"key": key, "old": old, "new": _get_dotted(data, key)}


# ---------------------------------------------------------------------------------------------------------------
# Versions


def current_version(film_dir: Path) -> int:
    versions = load(film_dir).get("versions") or [{"n": 1}]
    return int(versions[-1].get("n", 1))


def new_version(film_dir: Path, note: str | None = None) -> int:
    """Start the next version (after review notes). A delivered version is never overwritten; this only counts up."""
    out: dict[str, int] = {}

    def change(data: dict) -> None:
        versions = data.setdefault("versions", [])
        n = (versions[-1]["n"] + 1) if versions else 1
        versions.append({"n": n, "started_at": _util.now(), "note": note, "delivered_at": None})
        out["n"] = n

    update(film_dir, change)
    return out["n"]
