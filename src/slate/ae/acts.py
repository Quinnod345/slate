"""AE-built acts: a second way to build an act, on the same film clock and through the same gates as a Remotion act.

An act is built in After Effects when its folder holds `scenes/ae/actN.json`, a scene written as data:

    {"bg": "paper",                                    optional: a palette token for the act comp's background
     "silent": "why this act has no beats",            only when no animation carries an event
     "layers": [{"id": "card", "type": "rect", "size": [400, 240], "roundness": 24, "fill": "accent",
                 "transform": {"position": [640, 360]}},
                {"id": "title", "type": "text", "text": "…", "font": "display", "font_size": 48, "color": "ink"},
                {"id": "mark", "type": "logo", "logo": "mark", "width": 200, "pos": [540, 500]}, …],
     "anims": [{"id": "card-in", "layer": "card", "prop": "position", "from": [640, 460], "to": [640, 360],
                "ease": "enter", "dur": "base", "land": "S03/card",
                "event": {"kind": "land", "size": "m", "note": "the card lands"}},
               {"id": "title-fade", "layer": "title", "prop": "opacity",
                "keys": [{"at": {"shot": "S03", "t": 0.4}, "value": 0, "ease": "move"},
                         {"at": {"shot": "S03", "t": 1.2}, "value": 100}]}]}

Times are never guessed: a key's `at` (or a move's `land` / `start`) names a timeline event ("S03/card", its landing
frame; {"event": "S03/card", "use": "start"} for its start), a moment in a shot ({"shot": "S03", "t": 0.4} or
{"shot": "S03", "frame": 12}), a moment in the act ({"act_t": 1.5}) or a film frame ({"frame": 140}). A move that
lands on an event starts T90 × its duration earlier, so it covers 90% of its travel on the event's frame, exactly as
the Remotion kit's startFor does. Eases are the brand's named eases (or linear, hold, sine, spring0), durations its
duration tokens or seconds, colours its palette tokens, fonts its roles, logos its logos.

Python resolves every frame; the ExtendScript kit only reads the resolved scene from the build spec. Because both
evaluate the same motion law (checked by the shared vectors), the act's motion trace (timing/motion/actN.json, the
§12 contract) and its visual events (timing/visual_events/actN.json) are written here, without After Effects, and
`slate qa motion` checks them like any act. After a person renders the act (`render`, aerender on their go), the
render joins the renders registry as engine "ae" and faces the same gates. A scene built through the AE bridge can
export AE's own curves instead: engine/ae/trace_export.jsx samples every animated property as AE evaluates it
(`trace_from_samples`).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from slate import clock as film_clock
from slate import jsonio, log, paths, procs
from slate.ae import driver, eases, guard, masters as ae_masters, prefs
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError

SCENE_DIR = Path("scenes") / "ae"
LAYER_TYPES = ("solid", "null", "rect", "ellipse", "path", "text", "logo", "footage")
TRANSFORM_PROPS = ("position", "scale", "rotation", "opacity", "anchor")
SHAPE_PROPS = ("size", "roundness", "fill_color", "fill_opacity", "stroke_width", "stroke_color", "trim_start",
               "trim_end", "trim_offset", "path")
COLOR_PROPS = ("fill_color", "stroke_color")
EVENT_KINDS = ("tap", "type", "land", "snap", "count", "draw", "data", "page", "stamp", "whoosh", "swell", "character",
               "impact")
EVENT_SIZES = ("s", "m", "l")
BUILTIN_EASES = ("linear", "hold", "sine", "spring0")
GENERATED = "do not edit by hand: slate ae acts export"

Runner = Callable[..., Any]


# ---------------------------------------------------------------------------------------------------------------
# Scenes


def scene_path(film_dir: Path, act: int) -> Path:
    return Path(film_dir) / SCENE_DIR / f"act{int(act)}.json"


def load_scene(film_dir: Path, act: int) -> dict | None:
    """The act's AE scene, or None when the act isn't built in AE."""
    p = scene_path(film_dir, act)
    return jsonio.read(p) if p.is_file() else None


def ae_acts(film_dir: Path) -> list[int]:
    """Acts built in AE (those with a scene file)."""
    d = Path(film_dir) / SCENE_DIR
    out = []
    for f in sorted(d.glob("act*.json")) if d.is_dir() else []:
        stem = f.stem[3:]
        if stem.isdigit():
            out.append(int(stem))
    return sorted(out)


class _Ctx:
    """What resolving a scene needs: the act, shots, timeline, clock and the brand's law."""

    def __init__(self, *, act: dict, shots: dict, timeline: dict, fps: float, eases_table: dict, palette: dict,
                 fonts: dict, logos: dict, durations: dict | None, fps_num: int | None = None,
                 fps_den: int | None = None):
        self.act, self.shots, self.timeline, self.fps = act, shots, timeline or {}, fps
        self.eases, self.palette, self.fonts, self.logos = eases_table or {}, palette or {}, fonts or {}, logos or {}
        self.durations = durations or {}
        self.clock = film_clock.Clock(fps_num, fps_den) if fps_num and fps_den else None
        self.problems: list[str] = []

    def frame_of(self, seconds: float) -> int:
        return self.clock.frame_of(seconds) if self.clock else int(round(seconds * self.fps))

    def event(self, ref: str) -> dict:
        sid, _, eid = str(ref).partition("/")
        for e in (self.timeline.get("shots") or {}).get(sid, []) or []:
            if str(e.get("id")) == eid:
                return e
        raise UsageError(f"The scene names timeline event {ref!r}, which timing/timeline.json doesn't have.",
                         hint="Events come from the script (each shot's events); run `slate timing` after adding it.")

    def film_frame(self, ref: Any, *, use: str = "land") -> int:
        """A time reference -> a film frame (see the module docstring for the forms)."""
        if isinstance(ref, str):
            ref = {"event": ref, "use": use}
        if isinstance(ref, (int, float)) and not isinstance(ref, bool):
            return int(self.act["frame_in"]) + self.frame_of(float(ref))
        if not isinstance(ref, dict):
            raise UsageError(f"Can't read the time {ref!r}: use an event (\"S03/card\"), {{\"shot\", \"t\"}}, "
                             "{\"act_t\"} or {\"frame\"}.")
        if "event" in ref:
            e = self.event(ref["event"])
            which = ref.get("use", use)
            if which == "start":
                if e.get("start_frame") is None:
                    raise UsageError(f"Timeline event {ref['event']} has no start (it isn't a 'land' event).")
                sid = str(ref["event"]).partition("/")[0]
                return int(self.shots[sid]["frame_in"]) + int(e["start_frame"])
            return int(e["film_frame"])
        if "shot" in ref:
            sh = self.shots.get(ref["shot"])
            if sh is None:
                raise UsageError(f"The scene names shot {ref['shot']!r}, which isn't in timing/shots.json.")
            if "frame" in ref:
                return int(sh["frame_in"]) + int(ref["frame"])
            return int(sh["frame_in"]) + self.frame_of(float(ref.get("t", 0.0)))
        if "act_t" in ref:
            return int(self.act["frame_in"]) + self.frame_of(float(ref["act_t"]))
        if "frame" in ref:
            return int(ref["frame"])
        raise UsageError(f"Can't read the time {ref!r}.")

    def ease(self, name: Any) -> str:
        if not isinstance(name, str) or not name:
            raise UsageError("Every move names its ease (one of the brand's named eases, or linear/hold/sine/spring0).")
        if name not in self.eases and name not in BUILTIN_EASES:
            raise UsageError(f"Ease {name!r} isn't one of the brand's named eases ({', '.join(self.eases) or 'none'}).")
        return name

    def seconds(self, dur: Any) -> float:
        if isinstance(dur, (int, float)) and not isinstance(dur, bool):
            return float(dur)
        if isinstance(dur, str) and dur in self.durations:
            return float(self.durations[dur])
        raise UsageError(f"Duration {dur!r} isn't a number of seconds or one of the brand's duration tokens "
                         f"({', '.join(self.durations) or 'none'}).")

    def color(self, v: Any) -> Any:
        if isinstance(v, str) and v in self.palette:
            return v
        if isinstance(v, str) and v.startswith("#") and len(v) in (4, 7):
            return v
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return [float(x) for x in v[:3]]
        raise UsageError(f"Colour {v!r} isn't one of the brand's palette tokens or a #RRGGBB.")

    def rgb(self, v: Any) -> list[float]:
        v = self.color(v)
        if isinstance(v, list):
            return v
        h = self.palette.get(v, v).lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _check_layer(L: dict, ctx: _Ctx, seen: set) -> dict:
    lid = L.get("id")
    if not isinstance(lid, str) or not lid:
        raise UsageError("Every scene layer needs an id.")
    if lid in seen:
        raise UsageError(f"Scene layer id {lid!r} is used twice.")
    seen.add(lid)
    t = L.get("type")
    if t not in LAYER_TYPES:
        raise UsageError(f"Layer {lid}: type {t!r} isn't one of {', '.join(LAYER_TYPES)}.")
    out = dict(L)
    for key in ("color", "fill", "stroke"):
        if out.get(key) is not None:
            out[key] = ctx.color(out[key])
    if t in ("rect", "ellipse") and not (isinstance(L.get("size"), list) and len(L["size"]) == 2):
        raise UsageError(f"Layer {lid}: a {t} needs size [w, h].")
    if t == "path" and not isinstance(L.get("verts"), list):
        raise UsageError(f"Layer {lid}: a path needs verts.")
    if t == "text":
        if L.get("font") and L["font"] not in ctx.fonts:
            raise UsageError(f"Layer {lid}: font role {L['font']!r} isn't in the brand's type "
                             f"({', '.join(ctx.fonts) or 'none'}).")
    if t == "logo" and L.get("logo") not in ctx.logos:
        raise UsageError(f"Layer {lid}: logo {L.get('logo')!r} isn't one of the brand's logos "
                         f"({', '.join(ctx.logos) or 'none'}).")
    if t == "footage":
        if not L.get("file"):
            raise UsageError(f"Layer {lid}: footage needs a file.")
    for k in ("in", "out"):
        if L.get(k) is not None:
            out[f"{k}_frame"] = ctx.film_frame(L[k]) - int(ctx.act["frame_in"])
    return out


def _value(prop: str, v: Any, ctx: _Ctx) -> Any:
    if prop in COLOR_PROPS:
        return ctx.rgb(v)
    return v


def _keys_of(A: dict, ctx: _Ctx) -> tuple[list[list], dict]:
    """(keys as [[film frame, value, ease]], facts about the move: land/start/dur) for one animation."""
    prop = A["prop"]
    if "keys" in A:
        keys = []
        for i, k in enumerate(A["keys"]):
            if "at" not in k or "value" not in k:
                raise UsageError(f"Animation {A.get('id')}: key {i + 1} needs at and value.")
            last = i == len(A["keys"]) - 1
            ease = "linear" if last and not k.get("ease") else ctx.ease(k.get("ease"))
            keys.append([ctx.film_frame(k["at"]), _value(prop, k["value"], ctx), ease])
        frames = [k[0] for k in keys]
        if len(keys) < 2 or frames != sorted(frames) or len(set(frames)) != len(frames):
            raise UsageError(f"Animation {A.get('id')}: keys need at least two distinct times, in order.")
        return keys, {"land": None}
    for k in ("from", "to", "ease", "dur"):
        if k not in A:
            raise UsageError(f"Animation {A.get('id')}: a move needs from, to, ease and dur (or keys).")
    ease = ctx.ease(A["ease"])
    dur = ctx.seconds(A["dur"])
    dur_frames = ctx.frame_of(dur)
    if dur_frames < 1:
        raise UsageError(f"Animation {A.get('id')}: {dur} s is shorter than a frame.")
    if "land" in A:
        land = ctx.film_frame(A["land"], use="land")
        t90 = eases.t90(ease, ctx.eases)
        start = ctx.frame_of(land / ctx.fps - t90 * dur)
    elif "start" in A:
        land = None
        start = ctx.film_frame(A["start"], use="start")
    else:
        raise UsageError(f"Animation {A.get('id')}: a move needs land (an event it lands on) or start.")
    keys = [[start, _value(A["prop"], A["from"], ctx), ease],
            [start + dur_frames, _value(A["prop"], A["to"], ctx), "linear"]]
    return keys, {"land": land, "dur_s": dur, "dur_token": A["dur"] if isinstance(A["dur"], str) else None}


def resolve_scene(scene: dict, *, act: dict, shots: dict, timeline: dict, fps: float, eases: dict,
                  palette: dict, fonts: dict, logos: dict, durations: dict | None = None,
                  fps_num: int | None = None, fps_den: int | None = None) -> dict:
    """An authored scene -> the build form: layers checked, every key resolved to a frame of the act comp.

    Returns {layers, anims: [{id, layer, prop, keys: [[act frame, value, ease]]}], moves: [...], events: [...]}
    where moves carry film frames for the motion trace and events are the act's visual beats.
    """
    if fps_num is None:
        from fractions import Fraction  # noqa: PLC0415

        fr = Fraction(fps).limit_denominator(10000)
        fps_num, fps_den = fr.numerator, fr.denominator
    ctx = _Ctx(act=act, shots=shots, timeline=timeline, fps=fps, eases_table=eases, palette=palette, fonts=fonts,
               logos=logos, durations=durations, fps_num=fps_num, fps_den=fps_den)
    if scene.get("bg") is not None:
        ctx.color(scene["bg"])
    seen: set = set()
    layers = [_check_layer(L, ctx, seen) for L in scene.get("layers") or []]
    by_layer = {L["id"]: L for L in layers}
    for L in layers:
        if L.get("parent") and L["parent"] not in by_layer:
            raise UsageError(f"Layer {L['id']}: parent {L['parent']!r} isn't a layer in this scene.")
    a_in, a_out = int(act["frame_in"]), int(act["frame_out"])
    anims, moves, events, ids = [], [], [], set()
    for A in scene.get("anims") or []:
        aid = A.get("id")
        if not isinstance(aid, str) or not aid or aid in ids:
            raise UsageError(f"Every animation needs a unique id (got {aid!r}).")
        ids.add(aid)
        if A.get("layer") not in by_layer:
            raise UsageError(f"Animation {aid}: layer {A.get('layer')!r} isn't in the scene.")
        prop = A.get("prop")
        L = by_layer[A["layer"]]
        allowed = TRANSFORM_PROPS + (SHAPE_PROPS if L["type"] in ("rect", "ellipse", "path") else ())
        if prop not in allowed:
            raise UsageError(f"Animation {aid}: {L['type']} layers can animate {', '.join(allowed)}, not {prop!r}.")
        keys, facts = _keys_of(A, ctx)
        for k in keys:
            if not (a_in - 1 <= k[0] <= a_out + 1):
                raise UsageError(f"Animation {aid}: a key at film frame {k[0]} is outside act {act['index']} "
                                 f"(frames {a_in}–{a_out}).")
        anims.append({"id": aid, "layer": A["layer"], "prop": prop,
                      "keys": [[k[0] - a_in, k[1], k[2]] for k in keys]})
        moves.append({"id": aid, "layer": A["layer"], "prop": prop, "keys": keys, "entrance": A.get("entrance"),
                      "size": L.get("size"), "rig": A.get("rig"), "loop_period": A.get("loop_period"),
                      "exception": A.get("exception"), **facts})
        ev = A.get("event")
        if ev:
            kind, size, note = ev.get("kind"), ev.get("size"), ev.get("note")
            if kind not in EVENT_KINDS:
                raise UsageError(f"Animation {aid}: event kind {kind!r} isn't one of {', '.join(EVENT_KINDS)}.")
            if size not in EVENT_SIZES:
                raise UsageError(f"Animation {aid}: event size {size!r} isn't s, m or l.")
            if not isinstance(note, str) or not note.strip():
                raise UsageError(f"Animation {aid}: an event needs a note saying what the viewer sees.")
            at = ev.get("at", "land")
            frame = (facts.get("land") if at == "land" and facts.get("land") is not None else
                     keys[-1][0] if at == "end" else keys[0][0])
            events.append({"id": ev.get("id", aid), "shot": shot_at(shots, act, frame), "t": round(frame / fps, 6),
                           "frame": frame, "kind": kind, "size": size, "note": note.strip()})
    if sum(1 for e in events if e["kind"] == "impact") > 1:
        raise UsageError(f"Act {act['index']} has more than one impact; an act has at most one big landing.")
    return {"layers": layers, "anims": anims, "moves": moves, "events": sorted(events, key=lambda e: e["t"]),
            "silent": scene.get("silent")}


def shot_at(shots: dict, act: dict, frame: int) -> str | None:
    """The shot of this act that contains a film frame (the last shot for a frame past the act's end)."""
    ids = list(act.get("shots") or [])
    for sid in ids:
        sh = shots.get(sid)
        if sh and int(sh["frame_in"]) <= frame < int(sh["frame_out"]):
            return sid
    return ids[-1] if ids else None


# ---------------------------------------------------------------------------------------------------------------
# Motion trace and events (no After Effects needed)


def _sample(v0: Any, v1: Any, u: float) -> Any:
    if isinstance(v0, list):
        return [round(a + (b - a) * u, 6) for a, b in zip(v0, v1)]
    if isinstance(v0, dict):
        return None
    return round(float(v0) + (float(v1) - float(v0)) * u, 6)


def trace_entries(resolved: dict, act: dict, shots: dict, *, eases_table: dict) -> list[dict]:
    """The §12 motion trace for a resolved scene, in film frames: one entry per eased segment."""
    out = []
    for m in resolved["moves"]:
        keys = m["keys"]
        segs = len(keys) - 1
        for i in range(segs):
            f0, v0, ease = keys[i]
            f1, v1 = keys[i + 1][0], keys[i + 1][1]
            if isinstance(v0, dict):
                continue  # a path morph has no single value to trace
            samples = []
            for f in range(int(f0), int(f1) + 1):
                u = (f - f0) / (f1 - f0) if f1 > f0 else 1.0
                samples.append([f, _sample(v0, v1, eases.value(ease, u, eases_table) if ease != "hold" else
                                           (0.0 if f < f1 else 1.0))])
            entrance = m.get("entrance")
            if entrance is None:
                entrance = i == 0 and m["prop"] in ("opacity", "scale") and _is_zeroish(m["prop"], v0)
            entry = {"id": m["id"] if segs == 1 else f"{m['id']}.{i + 1}", "shot": shot_at(shots, act, int(f0)),
                     "prop": m["prop"], "ease": ease, "frame_in": int(f0), "frame_out": int(f1), "from": v0, "to": v1,
                     "samples": samples, "entrance": bool(entrance), "loop_period": m.get("loop_period"),
                     "rig": m.get("rig"), "engine": "ae", "layer": m["layer"]}
            if m.get("size") and m["prop"] in ("scale", "rotation"):
                entry["size"] = m["size"]
            if m.get("exception"):
                entry["exception"] = m["exception"]
            if m.get("dur_token"):
                entry["duration_token"] = m["dur_token"]
            out.append(entry)
    return out


def _is_zeroish(prop: str, v: Any) -> bool:
    if prop == "opacity":
        return float(v) <= 0
    if prop == "scale" and isinstance(v, list):
        return min(v) < 100
    return False


def events_doc(resolved: dict, act: dict) -> Any:
    """The act's visual beats, or {"silent": true, "reason"} when it has none and says why."""
    evs = [{k: e[k] for k in ("id", "shot", "t", "kind", "size", "note")} for e in resolved["events"]]
    if evs:
        return evs
    reason = resolved.get("silent")
    if not isinstance(reason, str) or not reason.strip():
        raise Refused(f"AE-built act {act['index']} has no beats and doesn't say it's silent.",
                      hint="Give an animation an \"event\" (kind, size, note), or add \"silent\": \"why\" to the scene.")
    return {"silent": True, "reason": reason.strip()}


def export(film_dir: str | os.PathLike, act: int, *, spec: dict | None = None) -> dict:
    """Write timing/motion/actN.json and timing/visual_events/actN.json for an AE-built act (no AE needed)."""
    from slate.ae import spec as ae_spec  # noqa: PLC0415

    film_dir = Path(film_dir)
    spec = spec or ae_spec.build_spec(film_dir, queue=False)
    entry = next((a for a in spec["acts"] if int(a["index"]) == int(act)), None)
    if entry is None:
        raise UsageError(f"Act {act} isn't in this film's timing/shots.json.")
    if entry.get("engine") != "ae":
        raise UsageError(f"Act {act} isn't built in AE (no {SCENE_DIR}/act{act}.json).")
    shots = {s["id"]: s for s in spec["shots"]}
    resolved = entry["scene"]
    trace = trace_entries(resolved, entry, shots, eases_table=spec["brand"]["eases"])
    evs = events_doc(resolved, entry)
    motion_path = film_dir / "timing" / "motion" / f"act{act}.json"
    events_path = film_dir / "timing" / "visual_events" / f"act{act}.json"
    jsonio.write_atomic(motion_path, trace)
    jsonio.write_atomic(events_path, evs if isinstance(evs, list) else {**evs, "generated": GENERATED})
    return {"act": act, "motion": str(motion_path.relative_to(film_dir)), "entries": len(trace),
            "events": str(events_path.relative_to(film_dir)),
            "beats": len(evs) if isinstance(evs, list) else 0, "silent": not isinstance(evs, list)}


# ---------------------------------------------------------------------------------------------------------------
# Traces measured in After Effects (bridge-built acts)


_PROP_NAMES = {"ADBE Position": "position", "ADBE Position_0": "x", "ADBE Position_1": "y", "ADBE Position_2": "z",
               "ADBE Scale": "scale", "ADBE Rotate Z": "rotation", "ADBE Opacity": "opacity",
               "ADBE Anchor Point": "anchor"}


def trace_from_samples(raw: list[dict], act: dict, shots: dict, *, act_offset: bool = True) -> list[dict]:
    """engine/ae/trace_export.jsx samples (as AE evaluated them) -> §12 trace entries in film frames.

    The ease is recorded as "measured": the curve is whatever AE drew, and `slate qa motion` checks the samples
    against the brand's eases by shape.
    """
    a_in = int(act["frame_in"]) if act_offset else 0
    out = []
    for r in raw:
        samples = [[int(f) + a_in, v] for f, v in r.get("samples") or [] if v is not None]
        if len(samples) < 2:
            continue
        prop = _PROP_NAMES.get(r.get("match_name"), str(r.get("prop") or r.get("match_name") or "other").lower())
        f0, f1 = samples[0][0], samples[-1][0]
        out.append({"id": f"{r.get('layer')}.{prop}", "shot": shot_at(shots, act, f0), "prop": prop,
                    "ease": "measured", "frame_in": f0, "frame_out": f1, "from": samples[0][1], "to": samples[-1][1],
                    "samples": samples, "entrance": False, "loop_period": None, "rig": None, "engine": "ae",
                    "layer": r.get("layer")})
    return out


def export_measured(film_dir: str | os.PathLike, act: int, *, go: bool = False, runner: Runner | None = None) -> dict:
    """Sample an AE-built act in After Effects itself (any act, however it was built) into its motion trace."""
    from slate.ae import spec as ae_spec  # noqa: PLC0415

    film_dir = Path(film_dir)
    guard.person_start(go, "Measuring an act's motion in After Effects", film_dir=film_dir, action="ae")
    spec_path = film_dir / ae_spec.SPEC
    spec = ae_spec.read_spec(film_dir)
    entry = next((a for a in spec["acts"] if int(a["index"]) == int(act)), None)
    if entry is None:
        raise UsageError(f"Act {act} isn't in this film's build spec.")
    res = driver.expect_ok(driver.run_jsx(driver.script("trace_export.jsx"),
                                          args={"spec": str(spec_path), "acts": [int(act)]}, runner=runner),
                           "Measuring the act's motion in After Effects")
    raw = (res["report"].get("traces") or {}).get(str(act)) or []
    shots = {s["id"]: s for s in spec["shots"]}
    trace = trace_from_samples(raw, entry, shots)
    path = film_dir / "timing" / "motion" / f"act{act}.json"
    jsonio.write_atomic(path, trace)
    return {"act": act, "motion": str(path.relative_to(film_dir)), "entries": len(trace), "measured": True}


# ---------------------------------------------------------------------------------------------------------------
# Rendering an AE-built act with aerender


def render(film_dir: str | os.PathLike, act: int, *, go: bool = False, runner: Runner | None = None,
           out: str | os.PathLike | None = None, register: bool = True, cache_off: bool = True) -> dict:
    """Render an AE-built act with aerender, conform it to ProRes 422 HQ, check its frames, register it.

    Needs the person's go, After Effects closed (each aerender is a second AE, and memory grows with the GUI open)
    and the film's project built (`slate ae build --go`). AE's disk cache is switched off for the render and always
    put back (slate.ae.prefs). The render joins renders/registry.json as engine "ae", so it faces the same gates
    (glitch scan, motion checks, act joins) as a Remotion act.
    """
    from slate.ae import spec as ae_spec  # noqa: PLC0415

    film_dir = Path(film_dir)
    guard.person_start(go, "Rendering an act with aerender", film_dir=film_dir, action="ae_render")
    guard.require_ae_closed("rendering with aerender")
    spec = ae_spec.read_spec(film_dir)
    entry = next((a for a in spec["acts"] if int(a["index"]) == int(act)), None)
    if entry is None or entry.get("engine") != "ae":
        raise UsageError(f"Act {act} isn't an AE-built act in this film's build spec.")
    aep = Path(spec["paths"]["aep"])
    if not aep.is_file():
        raise MissingPrerequisite(f"The film's AE project isn't built yet ({aep}).", hint="slate ae build --go")
    run = runner or procs.run
    want = int(entry["frame_out"]) - int(entry["frame_in"])
    out_dir = paths.ensure_dir(film_dir / "renders" / "ae")
    final = Path(out) if out else out_dir / f"act{act}.mov"
    alpha = final.with_name(final.stem + "_4444.mov")
    argv = [driver.aerender_path(), "-project", str(aep), "-comp", entry["comp"],
            "-RStemplate", spec["masters"]["rs"] if spec.get("masters") else ae_spec.RS_TEMPLATE,
            "-OMtemplate", spec["masters"]["om_alpha"] if spec.get("masters") else ae_spec.OM_ALPHA,
            "-output", str(alpha), "-mem_usage", "25", "50", "-v", "ERRORS_AND_PROGRESS"]
    started = time.time()
    cm = prefs.disk_cache_off() if cache_off else _null_state()
    with cm as cache:
        with driver.ae_lock(bridge=False, owner=f"slate-ae-render-act{act}"):
            proc = run(argv, timeout=max(1800, want * 4))
    log_path = out_dir / f"act{act}.aerender.log"
    log_path.write_text((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or ""), encoding="utf-8")
    got = ae_masters.frames(alpha, runner=run)
    if getattr(proc, "returncode", 1) != 0 or got != want:
        raise SlateError(f"aerender made {got if got is not None else 'nothing'} of act {act}'s {want} frames.",
                         hint=f"The log is {log_path}.")
    ae_masters.conform_hq(alpha, final, runner=run)
    got_hq = ae_masters.frames(final, runner=run)
    if got_hq != want:
        raise SlateError(f"The ProRes 422 HQ conform of act {act} came out {got_hq} frames, not {want}.")
    alpha.unlink(missing_ok=True)
    samples = int(spec["project"]["motion_blur"]["samples"])
    result = {"act": int(act), "engine": "ae", "file": str(final.relative_to(film_dir)) if final.is_relative_to(film_dir)
              else str(final), "frames": [int(entry["frame_in"]), int(entry["frame_out"])], "samples": samples,
              "sha256": jsonio.sha256(final), "made_at": _now(), "bundle_hash": "aep:" + jsonio.sha256(aep),
              "seconds": round(time.time() - started, 1), "cache": cache, "registered": False}
    if register:
        try:
            from slate.render import registry  # noqa: PLC0415
        except ImportError:
            registry = None
        if registry is not None:
            registry.register(film_dir, {k: result[k] for k in ("act", "engine", "file", "frames", "samples", "sha256",
                                                                "made_at", "bundle_hash")})
            result["registered"] = True
        else:
            log.warn("slate.render isn't installed, so the render isn't registered yet; register it with slate render.")
    return result


class _null_state:
    def __enter__(self) -> dict:
        return {"toggled": False}

    def __exit__(self, *exc) -> None:
        return None


def _now() -> str:
    import datetime as _dt  # noqa: PLC0415

    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
