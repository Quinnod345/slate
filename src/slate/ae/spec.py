"""The build spec: everything After Effects needs to build a film's project, resolved in Python.

`slate ae build` writes `<film>/ae/build_spec.json` and hands it to engine/ae/build_film.jsx. The spec is the only
thing the ExtendScript side reads, so AE never re-derives a frame, a colour or an ease:

- the film: id, size, rational frame rate (fps_num / fps_den from film.json; no literal rates anywhere), total frames;
- the project: 16 bpc, the colour working space, linear blending off, software rendering, motion-blur settings and
  the sample bias (a tenth of a frame);
- acts and shots from timing/shots.json; which engine made each act (the renders registry, or an AE scene in
  `<film>/scenes/ae/actN.json`, which makes the act AE-built, see slate.ae.acts);
- the act renders to import (renders/registry.json), and the mix (sound/mix/final_mix.wav);
- markers: one per shot, one per spoken word (timing/vo_words.json placed by timing/shots.json), one per timeline
  event (timing/timeline.json);
- the brand: palette tokens, fonts by role (PostScript names), the named eases (the kit's law), logos as AE shape data
  (slate.ae.svg2ae);
- the masters plan: chunk spans in frames and the stock output-module templates (slate.ae.masters).

Other modules are read through their §12 interfaces when they're installed (slate.state.film, slate.timing,
slate.render.registry, slate.brand.pack), with plain-file fallbacks so the AE lane works while they're being built.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from slate import config, jsonio, log, paths
from slate.ae import eases as ease_math
from slate.ae import svg2ae
from slate.errors import MissingPrerequisite, UsageError

SPEC = Path("ae") / "build_spec.json"
COMMAND = "slate ae build"
WORKING_SPACE = "Rec.709 Gamma 2.4"  # the profile AE assigns to BT.709-tagged renders: AE converts nothing
DEFAULT_CHUNKS = 8
OM_PRIMARY = "H.264 - Match Render Settings - 40 Mbps"
OM_ALPHA = "High Quality with Alpha"  # ProRes 4444: conformed to 422 HQ by ffmpeg (stock "High Quality" isn't HQ)
RS_TEMPLATE = "Best Settings"
MIX_CANDIDATES = ("sound/mix/final_mix.wav", "sound/final_mix.wav")
FOLDERS = {"shots": "01 Shots", "acts": "02 Acts", "master": "03 Master", "audio": "11 Audio",
           "renders": "12 Renders (code)", "brand": "20 Brand"}


# ---------------------------------------------------------------------------------------------------------------
# Reading the film (through other modules' interfaces when they exist)


def load_film(film_dir: Path) -> dict:
    try:
        from slate.state import film as state_film  # noqa: PLC0415
    except ImportError:
        state_film = None
    if state_film is not None:
        try:
            return state_film.load(film_dir)
        except Exception as exc:  # noqa: BLE001 - fall back to the file itself, and say so
            log.warn(f"slate.state couldn't load film.json ({getattr(exc, 'message', exc)}); reading it directly.")
    return jsonio.read(Path(film_dir) / "film.json")


def load_shots(film_dir: Path) -> dict:
    try:
        from slate import timing  # noqa: PLC0415
    except ImportError:
        timing = None
    if timing is not None and callable(getattr(timing, "load_shots", None)):
        return timing.load_shots(film_dir)
    path = Path(film_dir) / "timing" / "shots.json"
    if not path.is_file():
        raise MissingPrerequisite("This film has no timing yet (timing/shots.json).", hint="slate timing")
    return jsonio.read(path)


def load_renders(film_dir: Path) -> list[dict]:
    try:
        from slate.render import registry  # noqa: PLC0415
    except ImportError:
        registry = None
    if registry is not None:
        return registry.entries(film_dir)
    data = jsonio.read(Path(film_dir) / "renders" / "registry.json", default={"entries": []}) or {}
    return list(data.get("entries") or [])


def brand_pack(film: dict) -> tuple[dict, Path | None]:
    """(pack, its folder) for the film's brand, through slate.brand.pack when installed."""
    name = film.get("brand")
    if not name:
        return {}, None
    try:
        from slate.brand import pack as brand_mod  # noqa: PLC0415
    except ImportError:
        brand_mod = None
    if brand_mod is not None:
        where = brand_mod.find(name)
        return brand_mod.load(where), Path(where).parent if Path(where).is_file() else Path(where)
    for d in paths.brand_dirs():
        f = Path(d) / str(name) / "brand.json"
        if f.is_file():
            return jsonio.read(f), f.parent
    raise MissingPrerequisite(f"Brand pack {name!r} isn't in {', '.join(str(d) for d in paths.brand_dirs())}.",
                              hint=f"slate brand init {name}")


# ---------------------------------------------------------------------------------------------------------------
# The brand, as AE needs it


_HEX = re.compile(r"#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3}")


def palette_tokens(pack: dict) -> tuple[dict[str, str], list[str]]:
    """{token: "#RRGGBB"} from the pack's palette (dict or list), with warnings for values AE can't use."""
    try:
        from slate.brand import pack as brand_mod  # noqa: PLC0415
        raw: Any = brand_mod.palette(pack)
    except (ImportError, AttributeError):
        raw = pack.get("palette") or {}
    items: list[tuple[str, Any]] = list(raw.items()) if isinstance(raw, dict) else [
        (str(x.get("name") or x.get("token")), x) for x in raw if isinstance(x, dict)]
    out, warnings = {}, []
    for name, v in items:
        value = v if isinstance(v, str) else (v.get("hex") or v.get("value") or v.get("color")) if isinstance(v, dict) else None
        if isinstance(value, str) and _HEX.fullmatch(value.strip()):
            h = value.strip().upper()
            out[str(name)] = h if len(h) == 7 else "#" + "".join(c * 2 for c in h[1:])
        elif isinstance(v, dict) and isinstance(v.get("tokens"), dict):  # nested groups: {"brand": {"tokens": {...}}}
            sub, w = palette_tokens({"palette": v["tokens"]})
            out.update({f"{name}.{k}": h for k, h in sub.items()})
            warnings += w
        else:
            warnings.append(f"palette token {name!r} isn't a #RRGGBB colour; AE can't use it")
    return out, warnings


def font_roles(pack: dict) -> tuple[dict[str, dict], list[str]]:
    """{role: {"postscript", "family"}} from the pack's type. Fonts are set by name in AE, never embedded; a role with
    only a family name is kept (AE will flag it if it can't match) and named in the warnings."""
    raw: Any = pack.get("type") or {}
    if isinstance(raw, dict) and isinstance(raw.get("families"), (list, dict)):
        raw = raw["families"]
    items: list[tuple[str, Any]] = list(raw.items()) if isinstance(raw, dict) else [
        (str(x.get("role") or x.get("name") or x.get("family")), x) for x in raw if isinstance(x, dict)]
    out, warnings = {}, []
    for role, v in items:
        if isinstance(v, str):
            out[str(role)] = {"postscript": v, "family": v}
            continue
        if not isinstance(v, dict):
            continue
        ps = v.get("postscript") or v.get("postscript_name") or v.get("ps_name") or v.get("ae_font")
        fam = v.get("family") or v.get("name")
        if not ps and fam:
            warnings.append(f"type role {role!r} names only its family ({fam}); add its PostScript name to the "
                            "brand pack so After Effects sets exactly that face")
        if ps or fam:
            out[str(role)] = {"postscript": ps or fam, "family": fam or ps}
    return out, warnings


def brand_eases(pack: dict) -> tuple[dict[str, Any], list[str]]:
    """The brand's named eases, as the kit reads them (normalised; unreadable ones are named in the warnings)."""
    try:
        from slate.brand import pack as brand_mod  # noqa: PLC0415
        motion: Any = brand_mod.motion(pack)
    except (ImportError, AttributeError):
        motion = pack.get("motion") or {}
    raw = (motion or {}).get("eases") or {}
    out, warnings = {}, []
    for name, v in raw.items():
        try:
            out[str(name)] = ease_math.normalize(v)
        except UsageError as exc:
            warnings.append(f"ease {name!r}: {exc.message}")
    return out, warnings


def brand_durations(pack: dict) -> dict[str, float]:
    """The brand's duration tokens in seconds ({name: s} or [{name, seconds}] in motion.durations)."""
    try:
        from slate.brand import pack as brand_mod  # noqa: PLC0415
        motion: Any = brand_mod.motion(pack)
    except (ImportError, AttributeError):
        motion = pack.get("motion") or {}
    raw = (motion or {}).get("durations") or {}
    items = [(d.get("name"), d.get("seconds")) for d in raw if isinstance(d, dict)] if isinstance(raw, list) else \
        list(raw.items())
    out: dict[str, float] = {}
    for name, v in items:
        if isinstance(v, dict):
            v = v.get("seconds", v.get("s"))
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0 and name:
            out[str(name)] = f
    return out


def brand_section(film: dict) -> tuple[dict, list[str]]:
    pack, pack_dir = brand_pack(film)
    palette, w1 = palette_tokens(pack)
    fonts, w2 = font_roles(pack)
    eases, w3 = brand_eases(pack)
    logos = svg2ae.brand_logos(pack, pack_dir) if pack else {}
    w4 = [f"logo {n}: {w}" for n, d in logos.items() for w in d.get("warnings", [])]
    return ({"name": film.get("brand"), "palette": palette, "fonts": fonts, "eases": eases, "logos": logos,
             "durations": brand_durations(pack)},
            w1 + w2 + w3 + w4)


# ---------------------------------------------------------------------------------------------------------------
# Markers


def _fps(film: dict) -> tuple[int, int, float]:
    try:
        num, den = int(film["fps_num"]), int(film["fps_den"])
    except (KeyError, TypeError, ValueError):
        raise UsageError("film.json needs fps_num and fps_den (Slate never assumes a frame rate).") from None
    if num <= 0 or den <= 0:
        raise UsageError(f"film.json's frame rate {num}/{den} isn't a rate.")
    return num, den, num / den


def markers(film_dir: Path, shots_doc: dict, fps: float) -> dict:
    """{shots, words, events}: [{frame (film frame; words may be fractional), label}]."""
    shots = shots_doc.get("shots") or []
    out: dict[str, list] = {"shots": [], "words": [], "events": []}
    for sh in shots:
        label = sh["id"] + (f" · {sh['on_screen']}" if sh.get("on_screen") else "")
        out["shots"].append({"frame": int(sh["frame_in"]), "label": label})
    words = jsonio.read(Path(film_dir) / "timing" / "vo_words.json", default={}) or {}
    cues = {c.get("shot"): c for c in (jsonio.read(Path(film_dir) / "timing" / "vo_cues.json", default={}) or {})
            .get("cues", []) if isinstance(c, dict)}
    by_id = {sh["id"]: sh for sh in shots}
    for sid, rec in sorted((words.get("shots") or {}).items()):
        sh = by_id.get(sid)
        if not sh or not isinstance(rec, dict):
            continue
        if "vo_frame" in sh:
            take_start = int(sh["vo_frame"]) / fps
        else:
            lead = sh.get("vo_lead_sec", (cues.get(sid) or {}).get("at", 0.0)) or 0.0
            take_start = int(sh["frame_in"]) / fps + float(lead)
        for w in rec.get("words") or []:
            if "start" not in w:
                continue
            out["words"].append({"frame": round((take_start + float(w["start"])) * fps, 4),
                                 "label": f"{sid} {w.get('w', '')}".strip()})
    timeline = jsonio.read(Path(film_dir) / "timing" / "timeline.json", default={}) or {}
    for sid, lst in (timeline.get("shots") or {}).items():
        for e in lst or []:
            frame = e.get("film_frame")
            if frame is None and sid in by_id and e.get("frame") is not None:
                frame = int(by_id[sid]["frame_in"]) + int(e["frame"])
            if frame is not None:
                out["events"].append({"frame": int(frame), "label": f"{sid}/{e.get('id', '?')}"})
    out["events"].sort(key=lambda m: m["frame"])
    return out


# ---------------------------------------------------------------------------------------------------------------
# The whole spec


def act_comp_name(index: int, name: str | None) -> str:
    """"A<n> <name>", with any leading "Act <n> ·" dropped from the name (the films' act comp names)."""
    rest = re.sub(r"^Act\s+\d+\s*[·:-]?\s*", "", (name or "").strip())
    return f"A{index} {rest}" if rest else f"A{index}"


def master_name(film: dict) -> str:
    return f"MASTER {film.get('id') or 'film'}"


def aep_path(film_dir: Path, film: dict) -> Path:
    return Path(film_dir) / "ae" / f"{film.get('id') or Path(film_dir).name}.aep"


def chunk_plan(total_frames: int, chunks: int) -> list[dict]:
    """Split a film into chunks for aerender: chunk k covers frames k*step .. min((k+1)*step, total) - 1."""
    total_frames, chunks = int(total_frames), int(chunks)
    if total_frames <= 0:
        raise UsageError("A film with no frames can't be mastered.")
    if chunks <= 0:
        raise UsageError("Masters need at least one chunk.")
    step = -(-total_frames // chunks)
    plan = []
    for k in range(chunks):
        s = k * step
        if s >= total_frames:
            break
        e = min(s + step, total_frames) - 1
        plan.append({"index": k, "start": s, "end": e, "frames": e - s + 1})
    return plan


def masters_section(film_dir: Path, film: dict, total: int, chunks: int | None) -> dict:
    ae_cfg = (film.get("ae") or {}) if isinstance(film.get("ae"), dict) else {}
    n = int(chunks or ae_cfg.get("master_chunks") or (config.load().get("ae") or {}).get("master_chunks") or DEFAULT_CHUNKS)
    return {
        "chunks": chunk_plan(total, n),
        "om_primary": ae_cfg.get("om_primary", OM_PRIMARY),
        "om_alpha": ae_cfg.get("om_alpha", OM_ALPHA),
        "rs": ae_cfg.get("rs_template", RS_TEMPLATE),
        "dir": str(Path(film_dir).resolve() / "masters" / "chunks"),
        "primary_name": "h264_c{k}.mp4",
        "alpha_name": "alpha_c{k}.mov",
    }


def find_mix(film_dir: Path) -> Path | None:
    for rel in MIX_CANDIDATES:
        p = Path(film_dir) / rel
        if p.is_file():
            return p
    return None


def build_spec(film_dir: str | os.PathLike, *, chunks: int | None = None, queue: bool = True) -> dict:
    """The whole build spec for a film (pure: reads the film, writes nothing)."""
    from slate.ae import acts as ae_acts  # noqa: PLC0415 - acts imports spec for the brand eases

    film_dir = Path(film_dir).resolve()
    film = load_film(film_dir)
    num, den, fps = _fps(film)
    shots_doc = load_shots(film_dir)
    for key in ("width", "height"):
        if not film.get(key):
            raise UsageError(f"film.json needs {key} (Slate never assumes a frame size).")
    width, height = int(film["width"]), int(film["height"])
    total = int(shots_doc.get("total_frames") or 0)
    shots = [{"id": s["id"], "act": int(s["act"]), "frame_in": int(s["frame_in"]), "frame_out": int(s["frame_out"]),
              "frames": int(s.get("frames", int(s["frame_out"]) - int(s["frame_in"]))),
              "on_screen": s.get("on_screen", ""), "picture": s.get("picture", "")}
             for s in shots_doc.get("shots") or []]
    if not shots or total <= 0:
        raise MissingPrerequisite("timing/shots.json has no shots yet.", hint="slate timing")
    by_id = {s["id"]: s for s in shots}
    brand, warnings = brand_section(film)
    registry = load_renders(film_dir)
    renders = []
    for e in registry:
        if str(e.get("engine")) == "ae":
            continue  # an AE act's own render is for QA; the project holds the act itself
        fr = e.get("frames") or [0, 0]
        f = Path(e["file"])
        renders.append({"act": int(e["act"]), "file": str(f if f.is_absolute() else film_dir / f),
                        "frame_in": int(fr[0]), "frame_out": int(fr[1]),
                        "label": f"act {e['act']} ({e.get('engine', 'render')})"})
    timeline = jsonio.read(film_dir / "timing" / "timeline.json", default={}) or {}
    acts = []
    for a in shots_doc.get("acts") or []:
        ids = list(a.get("shots") or [])
        if not ids:
            continue
        f_in = min(by_id[i]["frame_in"] for i in ids)
        f_out = max(by_id[i]["frame_out"] for i in ids)
        act = {"index": int(a["index"]), "name": a.get("name") or f"Act {a['index']}",
               "comp": act_comp_name(int(a["index"]), a.get("name")),
               "shots": ids, "frame_in": f_in, "frame_out": f_out, "engine": "render",
               "markers": [{"frame": int(e["film_frame"]), "label": f"{sid}/{e.get('id')}"}
                           for sid in ids for e in (timeline.get("shots") or {}).get(sid, []) if "film_frame" in e]}
        scene = ae_acts.load_scene(film_dir, act["index"])
        if scene is not None:
            act["engine"] = "ae"
            act["scene"] = ae_acts.resolve_scene(scene, act=act, shots=by_id, timeline=timeline, fps=fps,
                                                 eases=brand["eases"], palette=brand["palette"],
                                                 fonts=brand["fonts"], logos=brand["logos"],
                                                 durations=brand["durations"], fps_num=num, fps_den=den)
            act["bg"] = scene.get("bg")
        acts.append(act)
    mix = find_mix(film_dir)
    fid = film.get("id") or film_dir.name
    spec = {
        "slate_spec": 1,
        "film": {"id": fid, "title": film.get("title") or fid, "fps_num": num, "fps_den": den,
                 "width": width, "height": height, "pixel_aspect": float(film.get("pixel_aspect") or 1.0),
                 "total_frames": total},
        "project": {"bpc": 16, "working_space": (film.get("ae") or {}).get("working_space", WORKING_SPACE)
                    if isinstance(film.get("ae"), dict) else WORKING_SPACE,
                    "linear_blending": False, "gpu": "software", "sample_bias_frames": 0.1,
                    "motion_blur": {"shutter_angle": 180, "shutter_phase": -90, "samples": 16, "adaptive_limit": 128}},
        "names": {"master": master_name(film), "brand_board": f"BRAND {film.get('brand') or 'board'}",
                  "folders": dict(FOLDERS)},
        "paths": {"aep": str(aep_path(film_dir, film)), "preserve_dir": str(paths.data() / "ae" / "preserved")},
        "acts": acts,
        "shots": shots,
        "renders": renders,
        "audio": {"file": str(mix)} if mix else None,
        "markers": markers(film_dir, shots_doc, fps),
        "brand": brand,
        "masters": masters_section(film_dir, film, total, chunks) if queue else None,
        "warnings": warnings,
    }
    return jsonio.mark_generated(spec, COMMAND)


def write_spec(film_dir: str | os.PathLike, spec: dict) -> Path:
    return jsonio.write_atomic(Path(film_dir) / SPEC, spec)


def read_spec(film_dir: str | os.PathLike) -> dict:
    path = Path(film_dir) / SPEC
    if not path.is_file():
        raise MissingPrerequisite("This film has no AE build spec yet.", hint="slate ae build --go")
    return jsonio.read(path)
