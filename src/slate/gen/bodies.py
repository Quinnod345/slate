"""From a shot's generate block to a Cloud API request body.

The block says what the shot needs in words and files; this module turns it into the model's own fields:

    {"reason": "real-scene", "why": "a real coastline at dawn", "prompt": "…", "duration": 5,
     "image": "refs/start.png", "end_image": "take:S04", "audio": true, "params": {"bitrate_mode": "high"}}

- The role (which kind of model) follows from the inputs: a `video` extends, `reference_images` guide, an `image`
  animates, and no inputs at all makes a still.
- The model is the best one for that role (registry.json) unless the film overrides it (film.json `models`, a
  pin, slate.toml or SLATE_MODEL_*), or the shot names its own. `slate gen run` pins it at first use.
- File inputs are local: a film-relative path, `take:<shot>` (another shot's approved take) or `brand:<path>` (a
  file in the brand pack). They're uploaded only at run time, and only when the brand pack allows uploads of brand
  material; estimates use a placeholder URL, since the price never depends on the picture.
- Aspect comes from film.json (the frame's ratio). Resolution: a video plate renders at its role's default in
  registry.json (720p) unless slate.toml `[gen] video_resolution` or the shot's own `resolution` says otherwise; the
  full-HD option costs 2.46x as much, so it is a person's choice (registry.json questions: plate_video_resolution). A
  still uses the film's short side, within what the model offers.
"""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

from slate import config, jsonio
from slate.errors import Refused, UsageError
from slate.gen import deps, store

PLACEHOLDER_URL = "https://example.com/slate-estimate-placeholder.png"
KNOWN_ASPECTS = ("16:9", "9:16", "1:1", "4:5", "5:4", "4:3", "3:4", "21:9", "2:3", "3:2")
# How each file input reaches the API, by block key: (API field, is a list).
FILE_FIELDS = {"image": ("image_url", False), "end_image": ("end_image_url", False),
               "reference_images": ("image_urls", True), "video": ("video_url", False)}
VIDEO_ROLES = frozenset({"plate_video", "plate_video_reference", "plate_video_extend"})


def role_for(block: dict) -> str:
    if block.get("role"):
        return str(block["role"])
    if block.get("video"):
        return "plate_video_extend"
    if block.get("reference_images"):
        return "plate_video_reference"
    if block.get("image") or block.get("end_image") or block.get("duration"):
        return "plate_video"
    return "plate_still"


def resolve_model(film_dir: Path, film: dict, block: dict) -> tuple[str, str, str]:
    """(role, model, where it came from): shot, pin, film, config (slate.toml or env) or registry."""
    role = role_for(block)
    if block.get("model"):
        return role, str(block["model"]), "shot"
    model = config.model(role, film)
    if (film.get("pins") or {}).get(role):
        source = "pin"
    elif (film.get("models") or {}).get(role):
        source = "film"
    elif model != config.registry().get("roles", {}).get(role, {}).get("default"):
        source = "config"
    else:
        source = "registry"
    return role, model, source


def resolve_input(film_dir: Path, film: dict, ref: str) -> Path:
    """A block's file reference → a local file."""
    if ref.startswith("take:"):
        other = ref[len("take:"):]
        rec = store.load_record(film_dir, other)
        take = store.approved_take(rec) if rec else None
        if take is None:
            raise Refused(f"{ref} needs an approved take of {other}, and it has none.",
                          hint=f"slate gen judge --shot {other}, then slate gen pick --shot {other} --take <id>")
        return store.take_file(film_dir, take)
    if ref.startswith("brand:"):
        folder = deps.brand_dir(film)
        if folder is None:
            raise UsageError(f"{ref}: this film's brand pack wasn't found.")
        path = folder / ref[len("brand:"):]
    else:
        path = Path(ref).expanduser()
        path = path if path.is_absolute() else Path(film_dir) / path
    if not path.is_file():
        raise UsageError(f"The generate block's file {ref!r} isn't there ({path}).")
    return path


def file_inputs(film_dir: Path, film: dict, block: dict) -> list[tuple[str, Path]]:
    """[(block key, file)] in a stable order; reference lists keep their order."""
    out: list[tuple[str, Path]] = []
    for key in ("image", "end_image", "reference_images", "video"):
        value = block.get(key)
        if not value:
            continue
        for ref in (value if isinstance(value, list) else [value]):
            out.append((key, resolve_input(film_dir, film, str(ref))))
    return out


def aspect_of(width: int, height: int) -> str | None:
    ratio = Fraction(width, height)
    text = f"{ratio.numerator}:{ratio.denominator}"
    return text if text in KNOWN_ASPECTS else None


def covered_seconds(film_dir: Path, shot: dict, block: dict) -> float | None:
    """How long the shots this generation covers last, from shots.json."""
    ids = block.get("covers") or [shot.get("id")]
    try:
        shots = {s.get("id"): s for s in deps.load_shots(film_dir)}
    except Exception:  # noqa: BLE001 - only a default; an explicit duration always works
        return None
    total = 0.0
    for sid in ids:
        s = shots.get(sid) or {}
        if s.get("sec_in") is not None and s.get("sec_out") is not None:
            total += float(s["sec_out"]) - float(s["sec_in"])
        else:
            return None
    return total or None


def build(role: str, model: str, block: dict, film: dict, *, urls: dict[str, str | list[str]],
          duration: float | None = None) -> dict:
    """The request body. `urls` maps block keys (image, end_image, reference_images, video) to uploaded URLs."""
    prompt = str(block.get("prompt") or "").strip()
    if not prompt:
        raise UsageError("The generate block needs a prompt (what the shot shows, in plain words).")
    width, height = deps.film_size(film)
    if "image-to-video" in model and "image" not in urls:
        raise UsageError(f"{model} animates a start image: add `image` to the generate block (a still of your own, "
                         "or take:<shot> for an approved generated still).")
    body: dict = {"prompt": prompt}
    for key, (field, is_list) in FILE_FIELDS.items():
        if key in urls:
            body[field] = urls[key] if is_list else (urls[key][0] if isinstance(urls[key], list) else urls[key])
    if role in VIDEO_ROLES:
        seconds = block.get("duration") or duration
        if not seconds:
            raise UsageError("A generated video needs a duration: add `duration` (seconds) to the generate block.")
        body["duration"] = int(math.ceil(float(seconds) - 1e-9))  # the models take whole seconds; never cut short
    body["resolution"] = str(block.get("resolution") or default_resolution(role, width, height))
    aspect = block.get("aspect_ratio") or (aspect_of(width, height) if role != "plate_video_extend" else None)
    if aspect:
        body["aspect_ratio"] = aspect
    if "audio" in block:
        if model.startswith("kling-video/"):
            body["sound"] = "on" if block["audio"] else "off"
        else:
            body["generate_audio"] = bool(block["audio"])
    for key, value in (block.get("params") or {}).items():
        body[key] = value
    return body


def _role_options(role: str) -> dict:
    return dict((config.registry().get("roles", {}).get(role) or {}).get("options") or {})


def default_resolution(role: str, width: int, height: int) -> str:
    """The resolution a shot gets when its block doesn't name one.

    Video plates: slate.toml `[gen] video_resolution`, else the role's `resolution_default` in registry.json (720p).
    Best quality would say full HD, but it costs 2.46x as much, so moving up is a person's call, never a default.
    Stills: the film's short side, rounded down to a resolution the model offers (the price doesn't change with it).
    """
    opts = _role_options(role)
    offered = [str(r) for r in opts.get("resolution") or []]
    if role in VIDEO_ROLES:
        chosen = (config.load().get("gen") or {}).get("video_resolution") or opts.get("resolution_default") or "720p"
        return str(chosen)
    short = f"{min(width, height)}p"
    if not offered or short in offered:
        return short
    fits = [r for r in offered if r.rstrip("p").isdigit() and int(r.rstrip("p")) <= min(width, height)]
    return max(fits, key=lambda r: int(r.rstrip("p"))) if fits else offered[0]


def placeholders(inputs: list[tuple[str, Path]]) -> dict[str, str | list[str]]:
    out: dict[str, str | list[str]] = {}
    for key, _ in inputs:
        if FILE_FIELDS[key][1]:
            out.setdefault(key, [])
            out[key].append(PLACEHOLDER_URL)  # type: ignore[union-attr]
        else:
            out[key] = PLACEHOLDER_URL
    return out


def shown(film_dir: Path, inputs: list[tuple[str, Path]], body: dict) -> dict:
    """The body as it's recorded: every uploaded URL replaced by <upload:path> (no URLs are kept)."""
    names: dict[str, list[str]] = {}
    for key, path in inputs:
        names.setdefault(key, []).append(f"<upload:{deps.rel(film_dir, path)}>")
    out = dict(body)
    for key, (field, is_list) in FILE_FIELDS.items():
        if key in names and field in out:
            out[field] = names[key] if is_list else names[key][0]
    return out


def uploads_block(film_dir: Path, inputs: list[tuple[str, Path]]) -> list[dict]:
    from slate.gen import policy  # noqa: PLC0415

    return [{"field": FILE_FIELDS[key][0], "file": deps.rel(film_dir, path), "sha256": jsonio.sha256(path),
             "generator_output": policy.is_generator_output(film_dir, path)} for key, path in inputs]
