"""Format templates: the kinds of film Slate makes, and the channels each is made for.

"Any video", in practice, is one of these templates (plan §8). A format (`formats/<name>.json`) sets a film's runtime,
its frames (aspects and sizes), its clock, the voice-over pace, the structure, the loudness target, the caption
defaults, the default budget and whether it's for broadcast. Each aspect lists the channels it's made for, and the
channel library (`formats/_platforms.json`) holds every platform's sizes, limits and safe zones, each with its source and
the date it was checked. `formats/_shared.json` holds Slate's own ceilings (2.6 words a second, 17 caption characters a
second), the budget families from plan §8, the two routes a derived film can take and the format aliases.

    load(name, aspect=None) -> dict          the validated format, with zones, routes and captions resolved
    names() -> list[str]                     every format, sorted
    safe_zones(fmt, platform, aspect=None)   one channel's zone as fractions of the frame ({} when it has none)

Routes (plan §8, "Variants by channel"): hero placements (website, App Store, YouTube masters) are derived films at
master quality through the full pipeline; social cut-downs (Instagram, TikTok, Shorts, LinkedIn) may take the fast
route. Higgsedit becomes that fast route only if `slate bench higgsedit` says it wins (decision 9).

A few resolved keys exist for the modules that read formats directly: `width`/`height` (the chosen aspect's size),
`captions.min_seconds` (the QA gate's name for `min_s`), and, for formats that only come in 9:16, top-level
`platforms` and `safe_zones` (the access gate reads those to decide which platform zones to check). Every aspect's
zones are always in `zones`.
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import re
from pathlib import Path
from typing import Any

from slate import jsonio, log, paths
from slate.errors import UsageError

PLATFORMS_FILE = "_platforms.json"
SHARED_FILE = "_shared.json"
SCHEMA = "format"
VERTICAL = "9:16"

_CACHE: dict[str, tuple[int, Any]] = {}


# ---------------------------------------------------------------------------------------------------------------
# Files


def formats_dir() -> Path:
    """The folder of format templates: the repo's `formats/`, or `SLATE_FORMATS_DIR` (tests point it elsewhere)."""
    env = os.environ.get("SLATE_FORMATS_DIR")
    return Path(env).expanduser() if env else paths.FORMATS_DIR


def _read(path: Path) -> Any:
    """Read a JSON file once per change: QA and state call load() many times in one command."""
    key = str(path.resolve())
    try:
        stamp = path.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise UsageError(f"Missing file: {path}") from exc
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    data = jsonio.read(path)
    _CACHE[key] = (stamp, data)
    return data


def _schema_problems(obj: Any, definition: str | None = None) -> list[str]:
    """Problems against format.schema.json, or one of its $defs (the channel library and the shared file)."""
    if definition is None:
        return jsonio.validate(obj, SCHEMA)
    import jsonschema  # noqa: PLC0415 - jsonio imports it the same way, lazily

    root = jsonio.schema(SCHEMA)
    sub = {"$schema": root["$schema"], "$defs": root["$defs"], "$ref": f"#/$defs/{definition}"}
    validator = jsonschema.validators.validator_for(sub)(sub)
    out = []
    for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(x) for x in err.absolute_path) or "(top level)"
        out.append(f"{where}: {err.message}")
    return out


def platforms() -> dict:
    """The channel library (formats/_platforms.json), validated. Raises UsageError when it's broken."""
    path = formats_dir() / PLATFORMS_FILE
    data = _read(path)
    problems = _schema_problems(data, "platforms_file")
    if problems:
        raise UsageError(f"{path} doesn't match the channel library schema:\n  " + "\n  ".join(problems[:20]),
                         hint="Fix the file, then run `slate formats check`.")
    return data


def shared() -> dict:
    """Slate's shared format defaults (formats/_shared.json), validated."""
    path = formats_dir() / SHARED_FILE
    data = _read(path)
    problems = _schema_problems(data, "shared_file")
    if problems:
        raise UsageError(f"{path} doesn't match the shared defaults schema:\n  " + "\n  ".join(problems[:20]),
                         hint="Fix the file, then run `slate formats check`.")
    return data


def channels() -> dict[str, dict]:
    """Every channel in the library, by key (a copy you can change)."""
    return copy.deepcopy(platforms()["channels"])


# ---------------------------------------------------------------------------------------------------------------
# Names


def _slug(text: str, sep: str) -> str:
    return re.sub(r"[^a-z0-9]+", sep, str(text).strip().lower()).strip(sep)


def channel_key(name: str) -> str:
    """A channel's key from any spelling or alias ("Reels", "IG", "YouTube Shorts" → instagram_reels, youtube_shorts)."""
    lib = platforms()
    key = _slug(name, "_")
    if key in lib["channels"]:
        return key
    aliases = lib.get("aliases") or {}
    for candidate in (key, key.replace("_", "")):
        if candidate in aliases:
            return aliases[candidate]
    raise UsageError(f"No channel called {name!r}.",
                     hint=f"Known channels: {', '.join(sorted(lib['channels']))} (slate formats channels).")


def names() -> list[str]:
    """Every format template, sorted (files starting with _ are Slate's shared data, not formats)."""
    folder = formats_dir()
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if not p.name.startswith(("_", ".")))


def canonical(name: str) -> str:
    """A format's canonical name from its name, an alias or a loose spelling ("reel", "Brand film", "hero_loop")."""
    raw = str(name).strip()
    known = names()
    if raw in known:
        return raw
    key = _slug(raw, "-")
    if key in known:
        return key
    aliases = shared().get("aliases") or {}
    if key in aliases and aliases[key] in known:
        return aliases[key]
    raise UsageError(f"No format called {raw!r}.",
                     hint=f"Known formats: {', '.join(known) or 'none installed'} (slate formats list).")


# ---------------------------------------------------------------------------------------------------------------
# Validation


def _ratio(width: int, height: int) -> tuple[int, int]:
    g = math.gcd(int(width), int(height)) or 1
    return int(width) // g, int(height) // g


def _is_ratio_key(key: str) -> bool:
    return bool(re.fullmatch(r"[0-9]+:[0-9]+", key))


def _high(rng: Any) -> float:
    return float(max(rng)) if isinstance(rng, (list, tuple)) and rng else 0.0


def semantic_problems(fmt: dict, *, name: str | None = None) -> list[str]:
    """What the schema can't see: sizes that don't match their aspect, channels that don't exist or don't take that
    aspect, ceilings from _shared.json, budgets above their family, runtimes outside a channel's limits."""
    out: list[str] = []
    lib = platforms()["channels"]
    cfg = shared()
    if name and fmt.get("name") != name:
        out.append(f"name is {fmt.get('name')!r} but the file is {name}.json")
    rt = fmt.get("runtime_s") or {}
    lo, hi, target = rt.get("min"), rt.get("max"), rt.get("target")
    if None not in (lo, hi, target) and not lo <= target <= hi:
        out.append(f"runtime_s: target {target} s is outside {lo}-{hi} s")
    for opt in rt.get("options") or []:
        if None not in (lo, hi) and not lo <= opt <= hi:
            out.append(f"runtime_s: option {opt} s is outside {lo}-{hi} s")
    aspects = fmt.get("aspects") or {}
    if fmt.get("default_aspect") not in aspects:
        out.append(f"default_aspect {fmt.get('default_aspect')!r} isn't one of the aspects ({', '.join(aspects)})")
    fps = (fmt.get("fps_num") or 0) / (fmt.get("fps_den") or 1)
    for key, spec in aspects.items():
        w, h = spec.get("width"), spec.get("height")
        if _is_ratio_key(key) and w and h and _ratio(w, h) != _ratio(*map(int, key.split(":"))):
            out.append(f"aspects/{key}: {w} x {h} isn't {key}")
        for ch in spec.get("channels") or []:
            if ch not in lib:
                out.append(f"aspects/{key}: no channel called {ch!r} in {PLATFORMS_FILE}")
                continue
            entry = lib[ch]
            ch_aspect = entry["aspects"].get(key)
            if ch_aspect is None:
                out.append(f"aspects/{key}: channel {ch} doesn't take {key} (it takes {', '.join(entry['aspects'])})")
            elif w and h and _ratio(ch_aspect["width"], ch_aspect["height"]) != _ratio(w, h):
                out.append(f"aspects/{key}: {w} x {h} doesn't match {ch}'s {ch_aspect['width']} x {ch_aspect['height']}")
            if entry.get("max_fps") and fps > float(entry["max_fps"]) + 1e-9:
                out.append(f"fps {fps:.3f} is faster than {ch} takes ({entry['max_fps']})")
            dur = entry.get("duration_s") or {}
            if dur.get("min") is not None and lo is not None and lo < dur["min"]:
                out.append(f"runtime_s: min {lo} s is shorter than {ch} takes ({dur['min']} s)")
            if dur.get("max") is not None and hi is not None and hi > dur["max"]:
                out.append(f"runtime_s: max {hi} s is longer than {ch} takes ({dur['max']} s)")
    ceiling = cfg["pace"]["max_words_per_second"]
    pace = (fmt.get("pace") or {}).get("max_words_per_second")
    if pace is not None and pace > ceiling:
        out.append(f"pace: {pace} words/s is above Slate's ceiling of {ceiling}")
    cap = fmt.get("captions") or {}
    if cap.get("max_cps") is not None and cap["max_cps"] > cfg["captions"]["max_cps"]:
        out.append(f"captions: max_cps {cap['max_cps']} is above {cfg['captions']['max_cps']}")
    if cap.get("max_lines") is not None and cap["max_lines"] > cfg["captions"]["max_lines"]:
        out.append(f"captions: max_lines {cap['max_lines']} is above {cfg['captions']['max_lines']}")
    audio = fmt.get("audio", "required")
    loud = fmt.get("loudness") or {}
    if audio != "none" and loud.get("lufs") is None:
        out.append("loudness: a film with sound needs a lufs target")
    if audio == "none" and loud.get("lufs") is not None:
        out.append("loudness: a film with no audio track can't have a lufs target")
    if audio == "none" and cap.get("needed"):
        out.append("captions: a film with no audio track has nothing to caption (needed: false)")
    out += _budget_problems(fmt.get("budget") or {}, cfg["budgets"])
    if fmt.get("generation") == "none" and _high((fmt.get("budget") or {}).get("higgsfield")) > 0:
        out.append("budget: generation is none, so the Higgsfield budget must be [0, 0]")
    structure = fmt.get("structure") or {}
    acts = structure.get("acts") or {}
    if acts.get("min") and acts.get("max") and acts["min"] > acts["max"]:
        out.append("structure: acts min is above max")
    options = set(rt.get("options") or [])
    for beat in structure.get("beats") or []:
        for length in beat.get("for") or []:
            if options and length not in options:
                out.append(f"structure: beat {beat.get('name')!r} is for {length} s, which isn't a runtime option")
    for ch, per_aspect in (fmt.get("zone_overrides") or {}).items():
        if ch not in lib:
            out.append(f"zone_overrides: no channel called {ch!r}")
        for key in per_aspect:
            if key not in aspects:
                out.append(f"zone_overrides/{ch}: the format has no {key} aspect")
    return out


def _budget_problems(budget: dict, families: dict) -> list[str]:
    out = []
    fam_name = budget.get("family")
    fam = families.get(fam_name) if fam_name else None
    if not isinstance(fam, dict):
        return [f"budget: no budget family called {fam_name!r} in {SHARED_FILE}"] if fam_name else []
    for part, rng in (budget.get("elevenlabs") or {}).items():
        if rng[0] > rng[1]:
            out.append(f"budget: elevenlabs.{part} {rng} runs backwards")
        cap = (fam.get("elevenlabs") or {}).get(part)
        if cap is None:
            out.append(f"budget: the {fam_name} family has no elevenlabs.{part}")
        elif rng[1] > cap[1]:
            out.append(f"budget: elevenlabs.{part} tops out at {rng[1]}, above the {fam_name} family's {cap[1]}")
    hf = budget.get("higgsfield")
    if hf:
        if hf[0] > hf[1]:
            out.append(f"budget: higgsfield {hf} runs backwards")
        if hf[1] > _high(fam.get("higgsfield")):
            out.append(f"budget: higgsfield tops out at ${hf[1]}, above the {fam_name} family's ${_high(fam.get('higgsfield'))}")
    return out


def validate(fmt: dict, *, name: str | None = None) -> list[str]:
    """Every problem with a format: schema first, then the checks the schema can't express."""
    problems = _schema_problems(fmt)
    if problems:
        return problems
    return semantic_problems(fmt, name=name)


def _library_problems() -> tuple[dict[str, list[str]], bool]:
    """Problems in the channel library and the shared file themselves (zones that make no sense, dangling aliases),
    and whether both files are sound enough to check the formats against (they exist and match their schema)."""
    out: dict[str, list[str]] = {}
    usable = True
    folder = formats_dir()
    for fname, definition in ((PLATFORMS_FILE, "platforms_file"), (SHARED_FILE, "shared_file")):
        path = folder / fname
        try:
            data = _read(path)
        except UsageError as exc:
            out[fname] = [exc.message]
            usable = False
            continue
        problems = _schema_problems(data, definition)
        if problems:
            usable = False
        if not problems and fname == PLATFORMS_FILE:
            for key, ch in data["channels"].items():
                for aspect, zone in ch["zones"].items():
                    if aspect not in ch["aspects"]:
                        problems.append(f"channels/{key}/zones/{aspect}: the channel has no {aspect} aspect")
                    if zone["top"] + zone["bottom"] >= 1 or zone["left"] + zone["right"] >= 1:
                        problems.append(f"channels/{key}/zones/{aspect}: the margins leave no safe area")
                    for rect in zone.get("keep_out") or []:
                        if rect[0] >= rect[2] or rect[1] >= rect[3]:
                            problems.append(f"channels/{key}/zones/{aspect}: keep-out {rect} has no area")
            for alias, target in data["aliases"].items():
                if target not in data["channels"]:
                    problems.append(f"aliases/{alias}: points at {target!r}, which isn't a channel")
        if not problems and fname == SHARED_FILE:
            for alias, target in data["aliases"].items():
                if target not in names():
                    problems.append(f"aliases/{alias}: points at {target!r}, which isn't a format")
        if problems:
            out[fname] = problems
    return out, usable


def check_all() -> dict[str, list[str]]:
    """{file: problems} for the shared files and every format; empty when everything is sound."""
    out, usable = _library_problems()
    if not usable:
        return out  # the formats can't be checked against a library that doesn't match its schema
    defaults = []
    for n in names():
        path = formats_dir() / f"{n}.json"
        try:
            data = _read(path)
        except UsageError as exc:
            out[f"{n}.json"] = [exc.message]
            continue
        problems = validate(data, name=n)
        if not problems and data.get("autonomous_default"):
            defaults.append(n)
            rt = data["runtime_s"]
            if VERTICAL not in data["aspects"] or rt["min"] < 15 or rt["max"] > 45:
                problems.append("autonomous_default: the autonomous default must be a 15-45 second 9:16 reel (plan §7)")
        if problems:
            out[f"{n}.json"] = problems
    if len(defaults) > 1:
        out.setdefault("formats", []).append(f"more than one autonomous_default: {', '.join(defaults)}")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Loading and resolving


def raw(name: str) -> dict:
    """The format file itself, validated, without anything resolved (a copy)."""
    n = canonical(name)
    path = formats_dir() / f"{n}.json"
    data = _read(path)
    problems = validate(data, name=n)
    if problems:
        raise UsageError(f"formats/{n}.json has problems:\n  " + "\n  ".join(problems[:20]),
                         hint="Fix the file, then run `slate formats check`.")
    return copy.deepcopy(data)


def load(name: str, *, aspect: str | None = None) -> dict:
    """A format by name (or alias, or a path to a format file), validated and resolved for one aspect.

    The result is a copy: the file's own keys, plus `name`, `path`, `aspect`, `width`, `height`, `type_scale`,
    `zones` ({aspect: {channel: zone}}), `delivery` ({channel: route}), captions resolved for the aspect, and for
    9:16-only formats `platforms` and `safe_zones`. Raises UsageError for an unknown format or aspect.
    """
    text = str(name)
    candidate = Path(text).expanduser()
    if candidate.suffix == ".json" and candidate.is_file():
        data = copy.deepcopy(_read(candidate))
        if not isinstance(data, dict):
            raise UsageError(f"{candidate} isn't a format (expected a JSON object).")
        problems = validate(data)
        if problems:
            raise UsageError(f"{candidate} has problems:\n  " + "\n  ".join(problems[:20]),
                             hint="Check it against src/slate/schemas/format.schema.json.")
        path = candidate.resolve()
    else:
        data = raw(text)
        path = formats_dir() / f"{data['name']}.json"
    return _resolve(data, aspect, path)


def aspect_key(fmt: dict, aspect: str | None) -> str:
    """The format's own key for an aspect ("9:16", "9x16", a size like "720x1280", or a device class); UsageError if it has none."""
    aspects = fmt.get("aspects") or {}
    if aspect is None:
        return str(fmt.get("default_aspect"))
    text = str(aspect).strip().lower()
    if text in aspects:
        return text
    m = re.fullmatch(r"\s*(\d+)\s*[:x/×]\s*(\d+)\s*", text)
    if m:
        want = _ratio(int(m.group(1)), int(m.group(2)))
        for key, spec in aspects.items():
            if _ratio(spec["width"], spec["height"]) == want:
                return key
    raise UsageError(f"Format {fmt.get('name')} comes in {', '.join(aspects)}, not {aspect}.",
                     hint="Make a derived film for another aspect: slate variant <film> <child> --aspect <aspect>")


def _max_chars_for(key: str, width: int, height: int, fmt_max: int) -> int:
    table = shared()["captions"]["max_chars_by_aspect"]
    if key in table:
        base = table[key]
    else:
        ratio = "%d:%d" % _ratio(width, height)
        base = table.get(ratio) or (table.get("portrait") if height > width else table.get("landscape")) or fmt_max
    return min(int(fmt_max), int(base))


def _zone(fmt: dict, channel: str, aspect: str, lib: dict) -> dict:
    """One channel's zone at one aspect, the format's override first. {} when the channel has none there."""
    override = ((fmt.get("zone_overrides") or {}).get(channel) or {}).get(aspect)
    zone = override or (lib.get(channel) or {}).get("zones", {}).get(aspect)
    if not zone:
        return {}
    out = copy.deepcopy(zone)
    out.update({"platform": channel, "aspect": aspect, "unit": "fraction"})
    if override:
        out["override"] = f"formats/{fmt.get('name')}.json zone_overrides"
    return out


def _resolve(data: dict, aspect: str | None, path: Path) -> dict:
    out = copy.deepcopy(data)
    lib = platforms()["channels"]
    cfg = shared()
    chosen = aspect_key(out, aspect)
    spec = out["aspects"][chosen]
    out["path"] = str(path)
    out["aspect"] = chosen
    out["width"], out["height"] = int(spec["width"]), int(spec["height"])
    out["type_scale"] = spec.get("type_scale", out.get("type_scale", 1.0))
    out["zones"] = {key: {ch: _zone(out, ch, key, lib) for ch in a["channels"]} for key, a in out["aspects"].items()}
    routes = cfg["routes"]
    delivery: dict[str, dict] = {}
    for key, a in out["aspects"].items():
        for ch in a["channels"]:
            entry = lib[ch]
            row = delivery.setdefault(ch, {"label": entry["label"], "route": entry["route"],
                                           "quality": routes[entry["route"]]["quality"],
                                           "pipeline": routes[entry["route"]]["pipeline"], "aspects": []})
            row["aspects"].append(key)
            if entry.get("broadcast"):
                row["broadcast"] = True
    out["delivery"] = delivery
    cap = out["captions"]
    by_aspect = {key: _max_chars_for(key, a["width"], a["height"], cap["max_chars"]) for key, a in out["aspects"].items()}
    cap["max_chars_by_aspect"] = by_aspect
    cap["max_chars"] = by_aspect[chosen]
    cap["min_seconds"] = cap["min_s"]
    if set(out["aspects"]) == {VERTICAL}:
        vertical = out["aspects"][VERTICAL]["channels"]
        out["platforms"] = list(vertical)
        out["safe_zones"] = {ch: z for ch, z in out["zones"][VERTICAL].items() if z}
    return out


# ---------------------------------------------------------------------------------------------------------------
# Zones, captions, routes, budgets, loudness


def safe_zones(fmt: dict | str, platform: str, aspect: str | None = None) -> dict:
    """Where text mustn't go for one channel: {top, bottom, left, right, keep_out, basis, official, source,
    verified_on, platform, aspect, unit: "fraction"}.

    `fmt` is a format name or a loaded format. Without an aspect, the format's own aspect is tried first, then its
    other aspects in order, so a vertical channel answers with its 9:16 zone. Returns {} when the channel has no zone
    at any of the format's aspects (or at the aspect asked for). An unknown channel is a UsageError.
    """
    if isinstance(fmt, str):
        fmt = load(fmt)
    if not isinstance(fmt, dict):
        raise UsageError("safe_zones needs a format name or a loaded format.")
    if not isinstance(fmt.get("aspects"), dict):  # a bare dict from elsewhere: answer from its own data
        zones = fmt.get("safe_zones") or {}
        return dict(zones.get(platform) or {}) if isinstance(zones, dict) else {}
    ch = channel_key(platform)
    lib = platforms()["channels"]
    if aspect is not None:
        try:
            key = aspect_key(fmt, aspect)
        except UsageError:
            key = str(aspect).strip()
        return _zone(fmt, ch, key, lib)
    order = [fmt.get("aspect"), fmt.get("default_aspect"), *fmt["aspects"]]
    for key in dict.fromkeys(k for k in order if k):
        zone = _zone(fmt, ch, key, lib)
        if zone:
            return zone
    return {}


def zones_for(fmt: dict | str, aspect: str | None = None) -> dict[str, dict]:
    """{channel: zone} for every channel of one aspect (the format's own aspect when none is given)."""
    f = load(fmt, aspect=aspect) if isinstance(fmt, str) else fmt
    key = aspect_key(f, aspect) if aspect else f.get("aspect") or f.get("default_aspect")
    lib = platforms()["channels"]
    return {ch: _zone(f, ch, key, lib) for ch in f["aspects"][key]["channels"]}


def captions_for(fmt: dict | str, aspect: str | None = None) -> dict:
    """The caption rules for one aspect: a 9:16 cut gets shorter lines than the 16:9 film it came from."""
    f = load(fmt, aspect=aspect) if isinstance(fmt, str) else fmt
    cap = dict(f["captions"])
    key = aspect_key(f, aspect) if aspect else f.get("aspect") or f.get("default_aspect")
    by_aspect = cap.get("max_chars_by_aspect") or {}
    if key in by_aspect:
        cap["max_chars"] = by_aspect[key]
    cap.setdefault("min_seconds", cap.get("min_s"))
    return cap


def route_for(channel: str) -> dict:
    """How a film for this channel is made: {channel, route: hero|social, ...the route's rules from _shared.json}."""
    ch = channel_key(channel)
    entry = platforms()["channels"][ch]
    rules = copy.deepcopy(shared()["routes"][entry["route"]])
    rules["route_label"] = rules.pop("label")
    return {**rules, "channel": ch, "label": entry["label"], "route": entry["route"]}


def delivery(fmt: dict | str) -> dict:
    """{channel: {label, route, quality, pipeline, aspects}} for every channel the format is made for."""
    f = load(fmt) if isinstance(fmt, str) else fmt
    return copy.deepcopy(f.get("delivery") or _resolve(f, None, Path(f.get("path") or "")).get("delivery"))


def budget_family(name: str) -> dict:
    """A budget family from plan §8 ("reel_or_ad", "explainer", "brand_film" or "onboarding")."""
    fams = shared()["budgets"]
    if name not in fams or not isinstance(fams[name], dict):
        known = [k for k, v in fams.items() if isinstance(v, dict)]
        raise UsageError(f"No budget family called {name!r}.", hint=f"Known families: {', '.join(known)}.")
    return {**copy.deepcopy(fams[name]), "rework_factor": fams["rework_factor"], "source": fams["source"]}


def onboarding_budget() -> dict:
    """What onboarding one brand may spend, once: Voice Design about 5,000 credits and $15-20 of look tests."""
    return budget_family("onboarding")


def loudness_for(fmt: dict | str, *, broadcast: bool | None = None, standard: str = "ebu_r128") -> dict:
    """The loudness target: the format's own, or a broadcast standard (EBU R 128 or ATSC A/85) for broadcast use."""
    f = load(fmt) if isinstance(fmt, str) else fmt
    is_broadcast = f.get("broadcast", False) if broadcast is None else broadcast
    if not is_broadcast:
        return dict(f["loudness"])
    presets = shared()["loudness"]["broadcast"]
    if standard not in presets:
        raise UsageError(f"No broadcast loudness standard called {standard!r}.",
                         hint=f"Known standards: {', '.join(presets)}.")
    return {**presets[standard], "broadcast": True}


# ---------------------------------------------------------------------------------------------------------------
# Describing a format for people


def fps_label(num: int, den: int) -> str:
    if den == 1:
        return f"{num} fps"
    return f"{num / den:.3f}".rstrip("0").rstrip(".") + f" fps ({num}/{den})"


def _range_label(rng: list, unit: str = "") -> str:
    lo, hi = rng
    if lo == hi:
        return f"{unit}{lo:,g}"
    return f"{unit}{lo:,g}-{unit}{hi:,g}"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}".rstrip("0").rstrip(".") + "%"


def zone_label(zone: dict) -> str:
    if not zone:
        return "no zone"
    sides = f"top {_pct(zone['top'])}, bottom {_pct(zone['bottom'])}, left {_pct(zone['left'])}, right {_pct(zone['right'])}"
    extra = f", {len(zone['keep_out'])} keep-out" if zone.get("keep_out") else ""
    return f"{sides}{extra} ({zone['basis']}, checked {zone['verified_on']})"


def describe(fmt: dict) -> str:
    """A format as a person reads it (slate formats show)."""
    rt = fmt["runtime_s"]
    runtime = f"{rt['min']:g}-{rt['max']:g} s, target {rt['target']:g} s"
    if rt.get("options"):
        runtime += f" (comes in {', '.join(f'{o:g}' for o in rt['options'])} s)"
    lines = [f"{fmt['name']}: {fmt['title']}", f"  {fmt['summary']}"]
    if fmt.get("use_when"):
        lines.append(f"  Use it when: {fmt['use_when']}")
    lines.append(f"  runtime    {runtime}")
    for key, a in fmt["aspects"].items():
        mark = " (default)" if key == fmt["default_aspect"] else ""
        label = f" {a['label']}" if a.get("label") else ""
        lines.append(f"  frame      {key}{label}: {a['width']} x {a['height']}{mark}, for "
                     + ", ".join(fmt["delivery"][ch]["label"] for ch in a["channels"]))
    lines.append(f"  clock      {fps_label(fmt['fps_num'], fmt['fps_den'])}")
    pace = fmt["pace"]
    placement = ", ".join(f"{k.replace('_', ' ')} {pace[k]:g} s" for k in ("lead", "tail", "act_tail") if k in pace)
    lines.append(f"  pace       at most {pace['max_words_per_second']:g} words a second" + (f"; {placement}" if placement else ""))
    st = fmt["structure"]
    lines.append(f"  structure  {st['acts']['min']}-{st['acts']['max']} acts: " + " → ".join(b["name"] for b in st["beats"]))
    loud = fmt["loudness"]
    if loud.get("lufs") is None:
        lines.append(f"  loudness   none ({loud.get('note', 'no audio track')})")
    else:
        tol = f" ±{loud['tolerance_lu']:g}" if loud.get("tolerance_lu") is not None else ""
        lines.append(f"  loudness   {loud['lufs']:g} LUFS{tol}, no louder than {loud['true_peak']:g} dBTP")
    cap = fmt["captions"]
    if not cap["needed"]:
        lines.append("  captions   none needed (nothing is spoken)")
    else:
        if cap["burn_in"]:
            how = "burned in"
            if cap.get("renderer"):
                how += f" by {cap['renderer'].title()}"
            if cap.get("font") == "brand":
                how += " in the brand's font"
        else:
            how = "sidecar files"
        widths = ", ".join(f"{k} {v}" for k, v in cap["max_chars_by_aspect"].items())
        lines.append(f"  captions   {how}; {cap['max_lines']} lines ({widths} characters), at most {cap['max_cps']:g} a second")
    for ch, row in fmt["delivery"].items():
        for key in row["aspects"]:
            lines.append(f"  zone       {ch} at {key}: {zone_label(fmt['zones'][key][ch])}")
    b = fmt["budget"]
    el = b["elevenlabs"]
    lines.append(f"  budget     ElevenLabs voice {_range_label(el['vo'])} + score {_range_label(el['score'])} credits; "
                 f"Higgsfield {_range_label(b['higgsfield'], '$')} ({b['family'].replace('_', ' ')} family)")
    routes = sorted({row["route"] for row in fmt["delivery"].values()})
    route_text = {"hero": "hero: derived films at master quality through the full pipeline",
                  "social": "social: may take the fast route (Higgsedit only if the benchmark keeps it)"}
    lines.append("  routes     " + "; ".join(route_text[r] for r in routes))
    lines.append(f"  broadcast  {'yes' if fmt['broadcast'] else 'no'}"
                 + ("" if fmt["broadcast"] else " (set broadcast: true in film.json for film, TV or radio: Eleven Music Enterprise and broadcast loudness)"))
    lines.append(f"  generation {fmt['generation']}; engines {', '.join(fmt['engines'])}")
    if fmt.get("autonomous_default"):
        lines.append("  default    autonomous films use this format when none is named")
    return "\n".join(lines)


def summary_row(fmt: dict) -> dict:
    """The one-line facts `slate formats list --json` gives per format."""
    return {
        "name": fmt["name"], "title": fmt["title"], "summary": fmt["summary"],
        "runtime_s": fmt["runtime_s"], "aspects": list(fmt["aspects"]), "default_aspect": fmt["default_aspect"],
        "fps_num": fmt["fps_num"], "fps_den": fmt["fps_den"],
        "channels": sorted(fmt["delivery"]), "routes": sorted({r["route"] for r in fmt["delivery"].values()}),
        "budget_family": fmt["budget"]["family"], "broadcast": fmt["broadcast"],
        "autonomous_default": bool(fmt.get("autonomous_default")),
    }


# ---------------------------------------------------------------------------------------------------------------
# slate formats


def cmd_list(args: argparse.Namespace) -> int:
    rows = [summary_row(load(n)) for n in names()]
    if log.json_mode():
        log.emit({"formats": rows, "count": len(rows)})
        return 0
    width = max((len(r["name"]) for r in rows), default=4)
    lines = [f"{'format':<{width}}  {'runtime':<16} {'aspects':<34} {'clock':<22} routes"]
    for r in rows:
        rt = r["runtime_s"]
        runtime = f"{rt['min']:g}-{rt['max']:g} s"
        mark = " *" if r["autonomous_default"] else ""
        aspects = ", ".join(r["aspects"])
        if len(aspects) > 34:  # device classes (the App Store's) run long: name the default and count the rest
            aspects = f"{r['default_aspect']} +{len(r['aspects']) - 1} sizes"
        lines.append(f"{r['name']:<{width}}  {runtime:<16} {aspects:<34} "
                     f"{fps_label(r['fps_num'], r['fps_den']):<22} {', '.join(r['routes'])}{mark}")
    lines.append("* the autonomous default.  `slate formats show <name>` for the whole template.")
    log.say("\n".join(lines))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    fmt = load(args.name, aspect=args.aspect)
    if args.raw:
        data = raw(fmt["name"])
        log.result(data, jsonio.dumps(data).rstrip())
        return 0
    log.result(fmt, describe(fmt))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems = check_all()
    ok = not problems
    checked = [PLATFORMS_FILE, SHARED_FILE] + [f"{n}.json" for n in names()]
    if ok:
        human = f"All {len(names())} formats, the channel library and the shared defaults check out."
    else:
        parts = [f"{f}:\n  " + "\n  ".join(p) for f, p in problems.items()]
        human = "Problems:\n" + "\n".join(parts)
    log.result({"ok": ok, "checked": checked, "problems": problems}, human)
    return 0 if ok else 1


def cmd_channels(args: argparse.Namespace) -> int:
    lib = platforms()
    routes = shared()["routes"]
    rows = []
    for key, ch in lib["channels"].items():
        rows.append({"channel": key, "label": ch["label"], "route": ch["route"], "aspects": list(ch["aspects"]),
                     "zones": {a: {k: z[k] for k in ("top", "bottom", "left", "right", "basis", "official")}
                               for a, z in ch["zones"].items()},
                     "sources": len(ch["sources"])})
    if log.json_mode():
        log.emit({"verified_on": lib["verified_on"], "channels": rows, "routes": routes})
        return 0
    lines = [f"Channels (checked {lib['verified_on']}; re-check the sources before a production):"]
    for r in rows:
        lines.append(f"  {r['channel']:<16} {r['label']:<28} {r['route']:<7} {', '.join(r['aspects'])}")
        for a, z in lib["channels"][r["channel"]]["zones"].items():
            lines.append(f"  {'':<16} zone {a}: {zone_label(z)}")
    lines.append("Routes: hero = a derived film at master quality through the full pipeline; "
                 "social = may take the fast route.")
    log.say("\n".join(lines))
    return 0


def register(sub) -> None:
    """Add `slate formats list|show|check|channels` (skipped when another module already added `formats`)."""
    from slate import cli  # noqa: PLC0415 - avoid an import cycle at module load

    if "formats" in getattr(sub, "choices", {}):
        return
    group = cli.add_group(sub, "formats", "the format templates: list, show, check, channels",
                          description="Format templates: the kinds of film Slate makes (formats/<name>.json), each with "
                                      "its runtime, frame, clock, pace, structure, loudness, captions, safe zones, "
                                      "budget and routes.")
    cli.add_command(group, "list", cmd_list, "every format, one line each")
    p = cli.add_command(group, "show", cmd_show, "one format in full (with --json, the resolved template)")
    p.add_argument("name", help="a format name or alias (reel, explainer, brand-film, ...)")
    p.add_argument("--aspect", help="resolve for this aspect (e.g. 9:16) instead of the default")
    p.add_argument("--raw", action="store_true", help="the file exactly as written, nothing resolved")
    cli.add_command(group, "check", cmd_check, "validate every format, the channel library and the shared defaults")
    cli.add_command(group, "channels", cmd_channels, "the channel library: routes, aspects and safe zones with sources")
