"""Build reproducible, measured effect cues from the picture's events, without writing a film.

An unreadable or missing act is a failed input, never an act declared silent. Every interaction keeps one cue;
the clicks inside a counter are that cue's notes. Musical choices and voices come from the film's palette.
"""

from __future__ import annotations

import copy
import json
import math
import random
from typing import Callable

import numpy as np
from scipy import signal

from slate import jsonio
from slate.errors import Refused, UsageError
from slate.motion import eases
from slate.sound import checks
from slate.sound.film import CUES_FILE, Film
from slate.sound.verify import anchor_offset
from slate.timing import beats

VERSION = 1
RenderCue = Callable[[dict], tuple[np.ndarray, int]]


def _hash(value: object) -> str:
    return jsonio.sha256_obj(value)


def _seed(*values: object) -> int:
    return int(_hash(values)[:16], 16)


def _finite(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True


def _fail(message: str, report: list[dict]) -> None:
    raise Refused(message, hint="Fix the reported events or declare a silent act with its reason, then run slate sfx spot.",
                  details={"input_report": report})


def _events(film: Film) -> tuple[list[dict], list[dict]]:
    shots = film.shots_by_id()
    acts = {int(a["index"]) for a in film.shots.get("acts", [])}
    acts.update(int(s["act"]) for s in shots.values())
    report, events, seen = [], [], set()
    act_span: dict[int, tuple[float, float]] = {}
    for s in shots.values():
        a0, a1 = act_span.get(int(s["act"]), (math.inf, -math.inf))
        act_span[int(s["act"])] = (min(a0, s["frame_in"] * film.frame_s), max(a1, s["frame_out"] * film.frame_s))
    if not acts:
        _fail("There are no acts in the film's timing.", report)
    for act in sorted(acts):
        rel = f"timing/visual_events/act{act}.json"
        row = {"file": rel, "act": act}
        try:
            path = film.source(rel)
            raw = path.read_bytes()
            data = json.loads(raw)
            row["sha256"] = jsonio.sha256_bytes(raw)
            problems = jsonio.validate(data, "visual_events")
            if not _finite(data):
                problems.append("Event numbers must be finite.")
            if data == []:
                problems.append('An act without events must say {"silent": true, "reason": "…"}.')
            if not problems:
                problems.extend(beats.problems(data, fps=film.fps_num / film.fps_den))
            if isinstance(data, dict) and not str(data.get("reason", "")).strip():
                problems.append("A silent act needs a nonempty reason.")
            if not problems and isinstance(data, list):
                for event in data:
                    ident = (event["shot"], event["id"])
                    shot = shots.get(event["shot"])
                    if not all(ident):
                        problems.append("Every event needs a nonempty shot and id.")
                    if ident in seen:
                        problems.append(f"Duplicate event {ident!r}.")
                    seen.add(ident)
                    if shot is None or int(shot["act"]) != act:
                        problems.append(f"{event['id']}: shot {event['shot']!r} does not belong to act {act}.")
                        continue
                    start, end = shot["frame_in"] * film.frame_s, shot["frame_out"] * film.frame_s
                    if not start - film.frame_s / 2 <= event["t"] <= end + film.frame_s / 2:
                        problems.append(f"{event['id']}: beat is outside its shot on the film clock.")
                    # A beat lands in its shot; its motion may run across the act's other shots (a shot is a
                    # narration line, and a move that starts during one line and lands in the next is one move),
                    # but never past the act, whose events file it belongs to.
                    a0, a1 = act_span.get(act, (start, end))
                    if "start" in event and (event["start"] < a0 - film.frame_s / 2 or
                                             event["start"] + event["dur"] > a1 + film.frame_s / 2):
                        problems.append(f"{event['id']}: motion span is outside its act on the film clock.")
                    if "frame" in event and abs(event["frame"] * film.frame_s - event["t"]) > film.frame_s / 2:
                        problems.append(f"{event['id']}: frame and t disagree on the film clock.")
            row.update(status="malformed" if problems else "silent" if isinstance(data, dict) else "events")
            if problems:
                row["problems"] = problems
            elif isinstance(data, dict):
                row["reason"] = data["reason"]
            else:
                row["events"] = len(data)
                events.extend(dict(e, _act=act, _source=rel) for e in data)
        except FileNotFoundError as exc:
            row.update(status="missing", problems=[str(exc)])
        except (OSError, UnicodeError, json.JSONDecodeError, Refused, UsageError) as exc:
            row.update(status="unreadable", problems=[str(exc)])
        report.append(row)
    if any(r["status"] not in ("events", "silent") for r in report):
        _fail("Effects spotting stopped: an act's visual events are missing, unreadable or malformed.", report)
    return sorted(events, key=lambda e: (e["t"], e["_act"], e["shot"], e["id"])), report


def _scale(film: Film, octave: int) -> list[str]:
    key = film.palette.get("key") or ((film.brand or {}).get("sound") or {}).get("key")
    parsed = checks.parse_key(key)
    if parsed is None:
        raise UsageError("The effects palette needs a musical key or explicit pitches.",
                         hint="Set brand.json sound.key, or set pitch/notes/scale in the kind's palette params.")
    tonic, mode = parsed
    return [checks.note_name(12 * (octave + 1) + tonic + n) for n in checks.PENTATONIC[mode]]


def _motif(film: Film, event: dict, config: dict) -> list:
    motif = config.get("motif") or film.palette.get("motif") or ((film.brand or {}).get("sound") or {}).get("motif")
    if isinstance(motif, dict):
        motif = (motif.get("gestures") or {}).get(event.get("gesture")) or motif.get("notes")
    if not isinstance(motif, list) or not motif:
        raise UsageError("A character interaction needs the brand's sound motif.",
                         hint="Set sound.motif notes (or gestures), or the character kind's explicit params.pitch.")
    return motif


def _voice_params(film: Film, event: dict, typ: str, params: dict, config: dict, variation: dict) -> dict:
    p = copy.deepcopy(params)
    p["seed"] = _seed(variation["seed"], p["seed"]) if "seed" in p else variation["seed"]
    p["cents"] = float(p.get("cents", 0.0)) + variation["cents"]
    p["take"] = int(p.get("take", 0)) + variation["take"]
    p["fps_num"], p["fps_den"], p["sample_rate"] = film.fps_num, film.fps_den, film.sr
    if "ease" in event:
        p["ease"] = event["ease"]
    if "ease" in p:
        raw = p["ease"]
        if isinstance(raw, str):
            raw = ((film.brand or {}).get("motion", {}).get("eases", {})).get(raw, raw)
        spec = eases.parse(raw)
        if spec is None:
            raise UsageError(f"Event {event['id']}: the ease {p['ease']!r} is not a curve or a named brand ease.")
        p["ease"] = spec.to_dict()
    if typ == "layered":
        p["layers"] = [dict(layer, params=_voice_params(film, event, layer["type"], layer.get("params", {}),
                                                       config, dict(variation, seed=_seed(variation["seed"], i))))
                       for i, layer in enumerate(p.get("layers", []))]
    if typ in ("wood", "snote") and "pitch" not in p:
        walk = event.get("_walk")
        if walk and event["kind"] != "character":
            # one item of a run: the run walks up the key's pentatonic, a step per item, from a seeded start low in it
            octave = int(p.pop("octave", 5))
            notes = _scale(film, octave) + _scale(film, octave + 1)
            p["pitch"] = notes[min(len(notes) - 1, walk["start"] + walk["i"])]
        else:
            notes = _motif(film, event, config) if event["kind"] == "character" else _scale(film, int(p.pop("octave", 5)))
            p["pitch"] = notes[variation["seed"] % len(notes)]
    if typ == "spad" and "notes" not in p:
        # A single held sampled note uses the brand's key without inventing its signature chord.
        p["notes"] = [_scale(film, int(p.pop("octave", 3)))[0]]
    if typ == "spad":
        # A pad's sampler may have only one take. Its seeded breath still changes the envelope between repeats.
        p["breath_phase"] = float(p.get("breath_phase", 0.0)) + random.Random(variation["seed"]).uniform(0, 2 * math.pi)
    if typ == "sub":
        detune = 2 ** (variation["cents"] / 1200)
        for key in ("f0", "f1"):
            if key in p:
                p[key] *= detune
    if typ == "countup3":
        if "scale" not in p:
            p["scale"] = _scale(film, int(p.pop("octave", 5)))
        if "from" not in event or "to" not in event:
            raise UsageError(f"Count {event['id']} needs explicit from and to values; the spotter never guesses from prose.")
        if event["from"] == event["to"]:
            raise UsageError(f"Count {event['id']} has no value change; remove it or describe its actual interaction.")
        p.update({k: event[k] for k in ("from", "to", "dur")})
        if "hold_frames" in event:
            p["hold_frames"] = event["hold_frames"]
    elif "dur" in event and typ in ("air", "whoosh", "swell", "spad"):
        p["dur"] = event["dur"]
    if typ == "sample":
        if p.get("files"):
            p["file"] = p.pop("files")[p["take"] % len(params["files"])]
        p["rate"] = float(p.get("rate", 1.0)) * variation["rate"]
    return p


def _align_layers(params: dict, film: Film, render_cue: RenderCue) -> None:
    layers = params.get("layers") or []
    if not layers or not any(layer.get("anchor") for layer in layers):
        return
    starts = []
    for i, layer in enumerate(layers):
        if layer.get("anchor"):
            hit = dict(layer, id=f"layer-{i}", beat=0.0)
            _align(hit, film, render_cue)
            starts.append(hit["t"] + float(layer.get("offset", 0)))
        else:
            starts.append(float(layer.get("offset", 0)))
    first = min(starts)
    for layer, start in zip(layers, starts):
        layer["offset"] = start - first


def _align(cue: dict, film: Film, render_cue: RenderCue) -> None:
    wave, offset = render_cue(cue)
    wave = np.asarray(wave)
    if not len(wave) or not np.isfinite(wave).all() or not np.any(wave):
        raise Refused(f"Effect {cue['id']} produced empty, silent or nonfinite audio; its anchor cannot be measured.")
    measured = anchor_offset(wave, cue["anchor"], film.sr)
    if measured is None:
        raise UsageError(f"Event-tied effect {cue['id']} needs a measurable anchor.")
    cue["t"] = float(cue["beat"]) - (int(offset) + measured) / film.sr
    cue["dur"] = len(wave) / film.sr
    cue["measurement"] = {"anchor_sample": measured, "render_offset_samples": int(offset), "samples": len(wave),
                          "sample_rate": film.sr}


def _event_overrides(film: Film, events: list[dict]) -> dict[tuple[str, str], dict]:
    """Validate authored sound choices by picture identity; timing and approval stay outside this contract."""
    from slate.sound.effects import voices

    rows = (film.data.get("sound") or {}).get("event_overrides", [])
    if not isinstance(rows, list):
        raise UsageError("sound.event_overrides must be a list of shot/event sound choices.")
    allowed = {"shot", "event", "type", "sound", "balance_family", "stem", "anchor", "params", "gain", "pan"}
    anchors = {"onset", "onset12", "onset30", "start", "onset24", "peak20", "peak50",
               "end", "end20", "end30", "end40"}
    controls = {"id", "shot", "event", "t", "beat", "beat_end", "family", "balance_family", "stem", "sound",
                "approved", "approved_source_sha256", "clip_gain"}
    by_id = {(e["shot"], e["id"]): e for e in events}
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not _finite(row) or set(row) - allowed:
            raise UsageError("An event sound override has unsupported fields or nonfinite values.")
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("shot", "event")):
            raise UsageError("An event sound override needs nonempty shot and event identities.")
        ident = (row["shot"], row["event"])
        if ident not in by_id or ident in out:
            raise UsageError(f"Event sound override {ident!r} is missing from the picture or duplicated.")
        for key, choices in (("type", voices.TYPES), ("balance_family", ("accents", "textures", "swells", "impacts")),
                             ("stem", ("ui", "fx", "tonal", "amb")), ("anchor", anchors)):
            if key in row and (not isinstance(row[key], str) or row[key] not in choices):
                raise UsageError(f"Event sound override {ident!r} has an unsupported {key}.")
        if "sound" in row and (not isinstance(row["sound"], str) or not row["sound"].strip()):
            raise UsageError(f"Event sound override {ident!r} needs a nonempty sound name.")
        for key in ("gain", "pan"):
            if key in row and (isinstance(row[key], bool) or not isinstance(row[key], (int, float))
                               or key == "pan" and not -1 <= row[key] <= 1):
                raise UsageError(f"Event sound override {ident!r} has an invalid {key}.")
        if "params" in row and (not isinstance(row["params"], dict) or controls.intersection(row["params"])):
            raise UsageError(f"Event sound override {ident!r} params must contain voice parameters only.")
        if row.get("type") == "countup3" and by_id[ident]["kind"] != "count":
            raise UsageError("The countup3 voice needs an explicit count interaction.")
        if len(row) == 2:
            raise UsageError(f"Event sound override {ident!r} has no sound choice.")
        out[ident] = copy.deepcopy({k: v for k, v in row.items() if k not in ("shot", "event")})
    return out


def _size_weight(film: Film, event: dict, config: dict, typ: str, params: dict) -> float:
    """A beat's size as the sound's weight (sound.palette.sizes.<s|m|l>): a large land is a deeper knock, not a louder
    small one. `octave` moves pitched wood and felt notes (layers included) by whole octaves, `rate` darkens or
    brightens sampled swishes, and `gain_db` is returned for the cue's gain. A kind can opt out with size_weight false.
    Without a sizes table nothing changes, so a film's approved sound stays exactly as it was."""
    weight = (film.palette.get("sizes") or {}).get(str(event.get("size") or "m")) or {}
    authored = params.pop("size_weight", None)  # an override's exact pitches (a sonic logo) keep their register
    if not weight or config.get("size_weight") is False or authored is False:
        return 0.0
    octave = float(weight.get("octave", 0.0))

    def shift(kind: str, p: dict) -> None:
        if kind in ("wood", "snote") and octave:
            p["cents"] = float(p.get("cents", 0.0)) + 1200.0 * octave
        if kind == "sample" and "rate" in weight:
            p["rate"] = float(p.get("rate", 1.0)) * float(weight["rate"])
        if kind == "layered":
            for layer in p.get("layers", []):
                shift(layer["type"], layer.setdefault("params", {}))

    shift(typ, params)
    return float(weight.get("gain_db", 0.0))


def _runs(film: Film, events: list[dict], seed: int) -> dict[tuple[str, str], dict]:
    """Bursts as gestures (sound.palette.walk): beats of one kind in one shot, each within gap_s of the last, at least
    `min` of them, are one run, and its pitched items walk up the key's pentatonic, a step per item ("a run of dots is
    one run walking up the scale, not twelve identical clicks"). Off without a walk table."""
    cfg = film.palette.get("walk") or {}
    if not cfg:
        return {}
    gap, least = float(cfg.get("gap_s", 1.3)), int(cfg.get("min", 3))
    kinds = set(cfg.get("kinds") or ("tap", "snap", "land", "data", "stamp"))
    open_runs: dict[tuple, list[dict]] = {}
    runs: list[list[dict]] = []
    for e in events:
        if e["kind"] not in kinds:
            continue
        key = (e["_act"], e["shot"], e["kind"])
        run = open_runs.get(key)
        if run and e["t"] - run[-1]["t"] <= gap:
            run.append(e)
        else:
            open_runs[key] = run = [e]
            runs.append(run)
    out = {}
    for run in runs:
        if len(run) < least:
            continue
        # a run starts on the key's tonic or the step above (seeded, `spread` steps), so it climbs from home
        start = _seed(VERSION, film.id, seed, "walk", run[0]["shot"], run[0]["id"]) % max(1, int(cfg.get("spread", 2)))
        for i, e in enumerate(run):
            out[(e["shot"], e["id"])] = {"i": i, "n": len(run), "start": start, "first": run[0]["id"]}
    return out


def _cue(film: Film, event: dict, seed: int, render_cue: RenderCue, override: dict | None = None) -> dict:
    from slate.sound.effects.palette import deep_merge

    kind = event["kind"]
    # an act's own colour (sound.palette.acts.<N>.kinds.<kind>) sits between the palette and the event's override
    act = (film.palette.get("acts") or {}).get(str(event["_act"])) or {}
    config = deep_merge(deep_merge(film.palette["kinds"].get(kind) or {}, (act.get("kinds") or {}).get(kind)), override)
    typ = config.get("type")
    if not typ:
        raise UsageError(f"The sound palette has no rendering type for {kind!r}.")
    identity = [event["_act"], event["shot"], event["id"], kind]
    rng_seed = _seed(VERSION, film.id, seed, identity)
    rng = random.Random(rng_seed)
    limits = config.get("variation") or {}
    cents = float(limits.get("cents", 4.0))
    db = float(limits.get("gain_db", 1.2))
    takes = int(limits.get("takes", 2))
    if cents <= 0 or db < 0 or takes < 1:
        raise UsageError(f"The {kind} variation needs positive cents/takes and nonnegative gain_db.")
    variation = {"seed": rng_seed, "cents": rng.uniform(-cents, cents), "gain_db": rng.uniform(-db, db),
                 "take": rng.randrange(takes), "rate": rng.uniform(0.96, 1.04)}
    params = _voice_params(film, event, typ, config.get("params") or {}, config, variation)
    weight = _size_weight(film, event, config, typ, params)
    _align_layers(params, film, render_cue)
    pan = event.get("pan")
    if override is not None and "pan" in override:
        pan = override["pan"]
    elif pan is None and "x" in event:
        width = film.shots.get("width") or film.data["width"]
        pan = max(-0.6, min(0.6, (float(event["x"]) / float(width) * 2 - 1) * 0.6))
    cue = {"id": f"act{event['_act']}:{event['shot']}:{event['id']}", "kind": kind,
           "sound": config.get("sound") or config["family"], "family": config["balance_family"], "stem": config["stem"],
           "type": typ, "shot": event["shot"], "event": event["id"], "beat": float(event["t"]),
           "t": float(event["t"]), "anchor": config["anchor"], "params": params,
           "gain": float(config.get("gain", -26.0)) + variation["gain_db"] + weight + float(act.get("gain_db", 0.0)),
           "pan": float(pan if pan is not None else config.get("pan", 0.0)),
           "provenance": {"source": event["_source"], "event_sha256": _hash({k: v for k, v in event.items() if not k.startswith("_")}),
                          "palette_kind_sha256": _hash(config), "variation": variation}}
    if event.get("_walk"):
        cue["provenance"]["walk"] = dict(event["_walk"])
    if override is not None:
        cue["provenance"]["event_override_sha256"] = _hash(override)
    if "start" in event:
        cue["span"] = {"start": event["start"], "dur": event["dur"]}
    if typ == "countup3":
        from slate.sound.effects.voices import count_ticks  # noqa: PLC0415

        cue["t"] = event["start"]
        cue["notes"] = []
        for i, tick in enumerate(count_ticks(cue, sample_rate=film.sr)):
            note = {"id": f"{cue['id']}:{i}", "type": "wood", "anchor": "onset", "t": 0.0,
                    "beat": float(event["start"]) + tick["at"], "params": tick["params"],
                    "gain": cue["gain"] + tick["gain"], "pan": cue["pan"]}
            _align(note, film, render_cue)
            cue["notes"].append(note)
        if not cue["notes"]:
            raise Refused(f"Count {event['id']} produces no clicks on the film's clock.")
        cue["dur"] = max(n["t"] + n["dur"] for n in cue["notes"]) - cue["t"]
    else:
        _align(cue, film, render_cue)
    return cue


def _spans(film: Film, old: dict) -> tuple[list[dict], list[dict], dict]:
    """Keep authored cue-sheet silences/beds and explicit film directions through re-spotting."""
    sound = film.data.get("sound") or {}
    silences, beds = [], []
    for target, key in ((silences, "silences"), (beds, "beds")):
        for source in (old, film.palette, sound):
            for span in source.get(key) or []:
                if span not in target:
                    target.append(copy.deepcopy(span))
    for span in silences + beds:
        if not isinstance(span, dict) or not _finite(span) or not isinstance(span.get("from"), (int, float)) or \
                not isinstance(span.get("to"), (int, float)) or not 0 <= span["from"] < span["to"] <= film.duration_s:
            raise UsageError("A planned sound silence or bed has invalid boundaries on the film clock.")
    return silences, beds, {"silences": silences, "beds": beds}


def _bed_cues(film: Film, beds: list[dict], seed: int) -> list[dict]:
    cues = []
    for spec in beds:
        params = copy.deepcopy(spec.get("params") or {})
        if not params.get("notes") and isinstance(spec.get("notes"), list):
            params["notes"] = copy.deepcopy(spec["notes"])
        if not isinstance(params.get("notes"), list) or not params["notes"]:
            raise UsageError("An authored sound bed needs explicit sampled chord notes in params.notes.",
                             hint="Set sound.beds[].params.notes; a bed span alone only documents a permitted bed.")
        if spec.get("type", "spad") != "spad":
            raise UsageError("An authored tonal bed must use the sampled spad voice.")
        ident = str(spec.get("id") or "bed-" + _hash(spec)[:16])
        variation_seed = _seed(VERSION, film.id, seed, ident)
        variation = {"seed": variation_seed, "cents": 0.0, "take": int(params.pop("take", 0)), "rate": 1.0}
        params.setdefault("ease", "sine")
        params.setdefault("vel", 35)
        params.setdefault("attack", 0.35)
        params.setdefault("release", 0.4)
        event = {"id": ident, "kind": "bed", "dur": spec["to"] - spec["from"]}
        params = _voice_params(film, event, "spad", params, spec, variation)
        cues.append({"id": ident, "event": ident, "kind": "bed", "p3kind": "bed", "type": "spad",
                     "sound": spec.get("sound", "sampled string bed"), "family": "score", "stem": spec.get("stem", "tonal"),
                     "t": spec["from"], "beat": spec["from"], "anchor": "bed", "params": params,
                     "dur": params["dur"],
                     "gain": float(spec.get("gain", -31)), "pan": float(spec.get("pan", 0.0)),
                     "note": spec.get("why", ""), "provenance": {"bed_sha256": _hash(spec), "seed": variation_seed}})
    if len({c["id"] for c in cues}) != len(cues):
        raise UsageError("Authored sound bed IDs must be unique.")
    return cues


def _gain_product(first: dict | None, second: dict) -> dict:
    if first is None:
        return second
    times = sorted(set(first["t"] + second["t"]))
    gain = np.ones(len(times))
    for curve in (first, second):
        gain *= np.interp(times, curve["t"], curve["g"], left=curve.get("before", 1), right=curve.get("after", 1))
    return {"t": times, "g": gain.tolist(), "before": first.get("before", 1) * second.get("before", 1),
            "after": first.get("after", 1) * second.get("after", 1), "why": "measured bed handoffs"}


def bed_handoffs(cues: list[dict], film: Film, render_cue: RenderCue) -> list[dict]:
    """The approved short same-chord handoff, measured at equal energy and kept as absolute gain knots.

    Only unapproved authored beds are changed. Long chord overlaps otherwise create beating; the measured handoff
    limits the overlap to 0.3 seconds, with the source design's capped energy compensation. Multiple joins on one
    bed compose their gain curves so the later join cannot erase its entrance.
    """
    beds = sorted((c for c in cues if c.get("kind") == "bed" and not c.get("approved")), key=lambda c: c["t"])
    done = []
    for left, right in zip(beds, beds[1:]):
        left_end = left["t"] + float(left["params"]["dur"])
        if left["params"]["notes"] != right["params"]["notes"] or right["t"] >= left_end:
            continue
        t0 = right["t"] - 0.5
        n = int(round((left_end - t0 + 0.5) * film.sr))
        energies = []
        win = max(1, int(0.05 * film.sr))
        for cue in (left, right):
            raw = {k: v for k, v in cue.items() if k != "clip_gain"}
            wave, offset = render_cue(raw)
            wave = np.asarray(wave)
            if not len(wave) or not np.isfinite(wave).all() or not np.any(wave):
                raise Refused(f"Bed {cue['id']} cannot be measured from empty, silent or nonfinite audio.")
            start = int(round((cue["t"] - t0) * film.sr)) + int(offset)
            a, b = max(0, start), min(n, start + len(wave))
            placed = np.zeros(n)
            if b > a:
                segment = wave[a - start:b - start]
                placed[a:b] = np.sum(segment ** 2, axis=1) if segment.ndim > 1 else segment ** 2
            energies.append(np.maximum(0, signal.fftconvolve(placed, np.ones(win) / win, mode="same")))
        times = t0 + np.arange(n) / film.sr
        overlap = (times > right["t"] + 0.05) & (times < left_end - 0.05)
        if not np.any(overlap):
            overlap = (times >= right["t"]) & (times < left_end)
        audible = energies[0] + energies[1]
        overlap &= audible > np.max(audible) * 1e-4
        if not np.any(overlap):
            raise Refused(f"Beds {left['id']} and {right['id']} have no audible overlap for a measured handoff.")
        candidates = np.flatnonzero(overlap)
        difference = np.abs(10 * np.log10((energies[0][candidates] + 1e-20) / (energies[1][candidates] + 1e-20)))
        mid = float(times[candidates[int(np.argmin(difference))]])
        length = min(0.3, left_end - right["t"])
        u = np.clip((times - (mid - length / 2)) / length, 0, 1)
        outgoing, incoming = np.cos(u * np.pi / 2), np.sin(u * np.pi / 2)
        lift = np.sqrt(np.clip(audible / (outgoing ** 2 * energies[0] + incoming ** 2 * energies[1] + 1e-20), 1, 2))
        lift = np.where((u > 0) & (u < 1), lift, 1)
        smoothing = max(1, int(0.01 * film.sr))
        lift = signal.fftconvolve(lift, np.ones(smoothing) / smoothing, mode="same")
        selected = np.flatnonzero((times >= mid - length / 2 - 0.02) & (times <= mid + length / 2 + 0.02))
        indices = selected[::smoothing]
        knots = times[indices].tolist()
        for cue, gain, before, after in ((left, outgoing, 1.0, 0.0), (right, incoming, 0.0, 1.0)):
            curve = {"t": knots, "g": np.clip((gain * lift)[indices], 0, math.sqrt(2)).tolist(),
                     "before": before, "after": after, "why": "measured same-chord handoff"}
            cue["clip_gain"] = _gain_product(cue.get("clip_gain"), curve)
        done.append({"from": left["id"], "to": right["id"], "at": mid, "length_s": length,
                     "sample_rate": film.sr, "curve_step_s": smoothing / film.sr})
    return done


def _approved(film: Film, events: list[dict], old: dict, load_reference: Callable | None) -> tuple[list, list, list]:
    """Preserve same-clock approved spans before generating anything inside them.

    The callback validates the reference folder's hash-bound approval. Approved legacy layers remain separate:
    regrouping their waveforms or recalibrating their starts would change what the person approved.
    """
    from slate.sound.effects.balance_mix import approved_family
    music = (film.data.get("sound") or {}).get("music") or {}
    matches = music.get("match")
    if matches is None:
        matches = (film.palette.get("balance") or {}).get("match_refs")
    if matches is None:
        matches = (old.get("music") or {}).get("match") or []
    if not isinstance(matches, list):
        raise UsageError("Approved sound matches must be a list.")
    pending = list(events)
    preserved, references, used_spans = [], [], []
    for match in matches:
        if not isinstance(match, dict) or not isinstance(match.get("clip"), str):
            raise UsageError("An approved sound match needs its clip reference.")
        if match.get("offset_s"):
            raise Refused("Approved sound spans must stay on their approved clock; relocation needs a new approval.")
        if not match.get("pin_cues"):
            continue
        if load_reference is None:
            raise Refused("Pinned sound cues require an approval-validated reference loader.")
        ref = load_reference(match["clip"])
        sheet = ref["cue_sheet"]
        window = sheet.get("window") or [match.get("from"), match.get("to")]
        try:
            start = max(float(match["from"]), float(window[0]))
            end = min(float(match["to"]), float(window[1]))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise UsageError("An approved sound match needs from/to and the reference's film-time window.") from exc
        if not math.isfinite(start + end) or not 0 <= start < end <= film.duration_s:
            raise UsageError("An approved sound match has no valid span on the film clock.")
        if any(start < b and end > a for a, b in used_spans):
            raise UsageError("Approved sound cue spans overlap; their replacement would be ambiguous.")
        used_spans.append((start, end))
        source = [copy.deepcopy(c) for c in sheet.get("cues", [])
                  if c.get("family") != "voice" and c.get("type") != "vo" and c.get("stem") not in ("vo", "voice", "score")
                  and start <= float(c.get("beat", c.get("t", -1))) < end]
        source = [c for c in source if approved_family(c, ref.get("balance_used")) != "voice"]
        source_events = {c.get("event"): c for c in source}
        if len(source_events) != len(source):
            raise Refused("Approved sound cues need unique event IDs to preserve their frozen rides.")
        current = [e for e in pending if start <= e["t"] < end]
        current_by_id = {e["id"]: e for e in current}
        for cue in source:
            # A legacy layer can name its owning picture interaction explicitly. A suffix is never a mapping.
            owner = cue.get("visual_event") or cue.get("event")
            non_event = cue.get("p3kind", cue.get("kind")) in ("bed", "landing", "vo")
            if owner not in current_by_id and not non_event:
                raise Refused(f"Approved sound event {cue.get('event')} is missing from the current picture.",
                              hint="Approve the changed span, or name a layer's owning interaction with visual_event.")
        for event in current:
            prior = source_events.get(event["id"]) or next(
                (c for c in source if c.get("visual_event") == event["id"]), None)
            if prior is None or abs(float(prior.get("beat", prior["t"])) - event["t"]) > 0.002:
                raise Refused(f"Event {event['id']} no longer matches its approved sound span.",
                              hint="Approve a new sound reference for the changed picture.")
        for i, cue in enumerate(source):
            original_hash = _hash(cue)
            family = approved_family(cue, ref.get("balance_used"))
            event = current_by_id.get(cue.get("visual_event") or cue.get("event"))
            interaction = event["kind"] if event else cue.get("p3kind") or cue.get("kind") or cue.get("type")
            params = copy.deepcopy(cue.get("params") or {})
            if cue.get("type") in ("wood", "countup3") and cue.get("kind"):
                params.setdefault("kind", cue["kind"])
            if cue.get("notes") and not isinstance(cue["notes"][0], dict):
                params.setdefault("notes", cue.pop("notes"))
            if params:
                cue["params"] = params
            cue.setdefault("id", f"approved:{match['clip']}:{cue.get('event', i)}")
            cue["kind"] = interaction
            cue["family"] = family
            if "dur" not in cue and params.get("dur") is not None:
                cue["dur"] = params["dur"]
            cue.setdefault("sound", str(cue.get("p3kind") or cue.get("type") or interaction))
            cue.setdefault("beat", cue["t"])
            cue["approved"] = match["clip"]
            cue["approved_source_sha256"] = original_hash
            preserved.append(cue)
        pending = [e for e in pending if not start <= e["t"] < end]
        references.append({"clip": match["clip"], "cue_sheet_sha256": _hash(sheet),
                           "balance_used_sha256": _hash(ref.get("balance_used")), "cues": len(source)})
    return pending, preserved, [copy.deepcopy(matches), references]


def build(film: Film, *, seed: int = 0, render_cue: RenderCue, load_reference: Callable | None = None) -> dict:
    """Validate every act and return a complete cue sheet, calibrated with a renderer bound to this film.

    The caller owns the proof approval, output lock and write. This function reads inherited inputs in place and
    changes no files. Its result is byte-reproducible for the same inputs, palette, seed and renderer version.
    """
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise UsageError("The sound seed must be an integer.")
    events, report = _events(film)
    overrides = _event_overrides(film, events)
    old = jsonio.read(film.source(CUES_FILE), default={}) or {}
    if not isinstance(old, dict):
        raise UsageError("The existing sound cue sheet is not an object; its silences cannot be preserved.")
    silences, beds, span_inputs = _spans(film, old)
    pending, preserved, (matches, references) = _approved(film, events, old, load_reference)
    pinned_overrides = set(overrides) - {(e["shot"], e["id"]) for e in pending}
    if pinned_overrides:
        raise Refused("Event sound overrides cannot change pinned approved cues.",
                      hint="Approve the revised sound before pinning its reference.")
    walks = _runs(film, pending, seed)
    cues = [_cue(film, dict(event, _walk=walks[(event["shot"], event["id"])])
                 if (event["shot"], event["id"]) in walks else event,
                 seed, render_cue, overrides.get((event["shot"], event["id"])))
            for event in pending] + preserved
    music = copy.deepcopy(old.get("music") or {})
    music.update(copy.deepcopy((film.data.get("sound") or {}).get("music") or {}))
    handoffs = []
    if music.get("kind") == "bed":
        cues.extend(_bed_cues(film, beds, seed))
        handoffs = bed_handoffs(cues, film, render_cue)
    cues.sort(key=lambda c: (c.get("beat", c["t"]), c["id"]))
    clock = {"fps_num": film.fps_num, "fps_den": film.fps_den, "sample_rate": film.sr,
             "total_frames": film.shots["total_frames"], "width": film.shots.get("width"),
             "height": film.shots.get("height")}
    sheet = {"film": film.id, "seed": seed, "sample_rate": film.sr, "fps_num": film.fps_num, "fps_den": film.fps_den,
             "cues": cues, "silences": silences, "beds": beds, "warnings": list(film.notes), "input_report": report,
             "provenance": {"spotting_version": VERSION, "clock": clock, "palette_sha256": _hash(film.palette),
                            "film_sound_sha256": _hash(film.data.get("sound")),
                            "brand_sound_sha256": _hash((film.brand or {}).get("sound")),
                            "brand_motion_sha256": _hash((film.brand or {}).get("motion")),
                            "shots_sha256": jsonio.sha256(film.source("timing/shots.json")),
                            "span_inputs_sha256": _hash(span_inputs), "approved_references": references},
             "bed_handoffs": handoffs}
    if matches:
        music["match"] = matches
    if music:
        sheet["music"] = music
    jsonio.require_valid(sheet, "sound_cues", "Spotted sound cues")
    return sheet
