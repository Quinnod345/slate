"""Beat maps: a capture's taps land on the voice's words, and each tap becomes a visual event that makes a sound.

A capture is recorded in real time, so its taps never fall exactly where the script wants them. The beat map places
the capture on the film clock and retimes it, one frame at a time:

1. Placement: the spec's `place` (a shot and an offset), else the first anchored tap lands exactly on its word.
2. Each anchored step (a tap, click, mark or typing, with a `word`) must appear on the film frame where
   `slate.timing.words.word_time` says its word is spoken (plus the word's optional offset). The difference is taken
   out of the idle time before the tap: a frame is held when the word comes later, frames are cut when it comes
   sooner. Edits go where the screen is stillest, and never inside the app's response to the previous tap (the
   `response` window, 0.6 s by default), so what the app does after a tap always plays at its real speed.
3. Every tap, click, mark and typing step becomes a visual event in `beats.json`, in the shape of
   timing/visual_events/actN.json ({shot, id, t, kind, size, note, …}), so the sound design gives it a sound on the
   frame it happens.

The result is gated (`capture_beats:<name>`): PASS only when every anchored word was found exactly and every step
lands within the spec's tolerance (one frame by default). A word the timing can't find is a miss, and misses fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from slate import clock, gates, jsonio, log
from slate.capture import media, spec as spec_mod, store
from slate.errors import UsageError
from slate.gen import deps

STATIC_DIFF = 0.6  # mean absolute difference (0–255, small greyscale) below which two frames look identical
GATE_PREFIX = "capture_beats:"

WordTime = Callable[..., tuple[float, bool]]


@dataclass
class Anchor:
    step: dict
    raw_frame: int
    target_frame: int | None = None
    target_t: float | None = None
    exact: bool | None = None


@dataclass
class Plan:
    mapping: list[int]
    film_in: int | None
    edits: list[dict] = field(default_factory=list)
    residuals: dict[str, int] = field(default_factory=dict)


def frame_diffs(raw_dir: Path, count: int, width: int = 64) -> list[float]:
    """diffs[j] = how much frame j+1 differs from frame j (mean absolute difference of small greyscale copies)."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    prev = None
    out: list[float] = []
    for i in range(count):
        with Image.open(Path(raw_dir) / media.frame_name(i)) as im:
            im.draft("L", (max(1, im.size[0] // 8), max(1, im.size[1] // 8)))
            small = im.convert("L")
            height = max(1, round(small.size[1] * width / max(1, small.size[0])))
            arr = np.asarray(small.resize((width, height)), dtype=np.float32)
        if prev is not None:
            out.append(float(np.abs(arr - prev).mean()))
        prev = arr
    return out


def _hold_point(lo: int, hi: int, diffs: list[float]) -> int:
    """The stillest frame in [lo, hi] to hold (motion on either side of it counts)."""
    def cost(h: int) -> float:
        before = diffs[h - 1] if 0 < h <= len(diffs) else 0.0
        after = diffs[h] if 0 <= h < len(diffs) else 0.0
        return before + after
    return min(range(lo, hi + 1), key=lambda h: (cost(h), -h))


def _cut_window(lo: int, hi: int, n: int, diffs: list[float]) -> int:
    """The start c of the stillest window [c, c+n) inside [lo, hi): the jump from c-1 to c+n is what shows."""
    def cost(c: int) -> float:
        return sum(diffs[j] for j in range(max(0, c - 1), min(len(diffs), c + n)))
    return min(range(lo, hi - n + 1), key=lambda c: (cost(c), -c))


def _is_static(total: float, frames: int) -> bool:
    return total <= STATIC_DIFF * max(1, frames)


def plan(raw_count: int, anchors: list[Anchor], *, film_in: int | None, response_frames: int,
         diffs: list[float]) -> Plan:
    """Build the frame mapping (output frame → raw frame) that lands every anchored step on its target frame."""
    exact = [a for a in anchors if a.exact and a.target_frame is not None]
    exact.sort(key=lambda a: a.raw_frame)
    if film_in is None and exact:
        film_in = exact[0].target_frame - exact[0].raw_frame
    result = Plan(mapping=[], film_in=film_in)
    if film_in is None:
        result.mapping = list(range(raw_count))
        return result
    seg_start = 0
    if film_in < 0:  # the capture would start before the film does: cut its head
        seg_start = min(raw_count, -film_in)
        result.edits.append({"kind": "cut", "at_frame": 0, "frames": seg_start, "static": False,
                             "reason": "the capture starts before the film's first frame"})
        result.film_in = film_in = 0
    editable_from = seg_start
    out = result.mapping
    for anchor in exact:
        r = anchor.raw_frame
        if r < seg_start:
            result.residuals[anchor.step.get("id", "?")] = 0
            continue
        target_index = anchor.target_frame - film_in
        natural = len(out) + (r - seg_start)
        delta = target_index - natural
        lo, hi = max(seg_start, editable_from), r  # edits live in [lo, hi): idle time before this step
        segment = list(range(seg_start, r))
        label = anchor.step.get("id", "?")
        if delta > 0:
            if hi > lo:
                h = _hold_point(lo, hi - 1, diffs)
                i = h - seg_start
                segment[i + 1:i + 1] = [h] * delta
                around = (diffs[h - 1] if 0 < h <= len(diffs) else 0.0) + (diffs[h] if h < len(diffs) else 0.0)
                result.edits.append({"kind": "hold", "at_frame": len(out) + i + 1, "frames": delta,
                                     "static": _is_static(around, 1),
                                     "reason": f"{label}'s word comes {delta} frame(s) later than the recording's tap"})
            else:
                result.residuals[label] = -delta  # lands early: no idle time before it to hold
        elif delta < 0:
            need = -delta
            n = min(need, max(0, hi - lo))
            if n > 0:
                c = _cut_window(lo, hi, n, diffs)
                total = sum(diffs[j] for j in range(max(0, c - 1), min(len(diffs), c + n)))
                segment = [f for f in segment if not (c <= f < c + n)]
                result.edits.append({"kind": "cut", "at_frame": len(out) + (c - seg_start), "frames": n,
                                     "static": _is_static(total, n + 1),
                                     "reason": f"{label}'s word comes {need} frame(s) sooner than the recording's tap"})
            if need > n:
                result.residuals[label] = need - n  # lands late: not enough idle time before it to cut
        out.extend(segment)
        seg_start = r
        editable_from = r + response_frames
    out.extend(range(seg_start, raw_count))
    return result


def _shot_at(frame: int, shots: list[dict]) -> str | None:
    for shot in shots:
        if shot.get("frame_in") is not None and shot.get("frame_out") is not None:
            if shot["frame_in"] <= frame < shot["frame_out"]:
                return shot.get("id")
    return None


def _shot_start(film_dir: Path, shot_id: str, clk: clock.Clock) -> float:
    shot = deps.find_shot(film_dir, shot_id)
    if shot.get("sec_in") is not None:
        return float(shot["sec_in"])
    if shot.get("frame_in") is not None:
        return clk.secs_of(int(shot["frame_in"]))
    raise UsageError(f"Shot {shot_id} has no frame_in in shots.json, so the capture can't be placed on it.")


def run(film_dir: Path, name: str, *, word_time: WordTime | None = None) -> dict:
    """Beat-map a conformed capture: write frames/, beats.json, update capture.json and record the gate."""
    film_dir = Path(film_dir)
    rec = store.require(film_dir, name)
    spec = rec["spec"]
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    cap = store.capture_dir(film_dir, name)
    raw_dir = cap / "raw"
    raw_count = media.count_frames(raw_dir)
    if raw_count < 1:
        raise UsageError(f"Capture {name} hasn't been conformed yet.", hint=f"slate capture conform --name {name}")
    lookup = word_time or deps.word_time
    taps = []
    anchors: list[Anchor] = []
    misses: list[str] = []
    for index, step in enumerate(spec.get("steps") or []):
        actual = next((s.get("actual") for s in rec.get("steps") or [] if s.get("index") == index), None)
        at = actual if isinstance(actual, (int, float)) else step["at"]
        raw_frame = min(raw_count - 1, max(0, clk.frame_of(at)))
        anchor = Anchor(step={**step, "_index": index}, raw_frame=raw_frame)
        word = step.get("word")
        if word:
            t, exact = lookup(film_dir, word["shot"], word["text"], occ=word.get("occ", 1),
                              which=word.get("which", "start"))
            t = float(t) + float(word.get("offset", 0.0))
            anchor.target_t, anchor.exact = t, bool(exact)
            anchor.target_frame = clk.frame_of(t)
            if not exact:
                misses.append(f"{step.get('id', index)}: the voice never says '{word['text']}' "
                              f"(occurrence {word.get('occ', 1)}) in {word['shot']}")
        anchors.append(anchor)
    ordered = [a for a in anchors if a.exact]
    for earlier, later in zip(ordered, ordered[1:]):
        if later.raw_frame > earlier.raw_frame and later.target_frame <= earlier.target_frame:
            misses.append(f"{later.step.get('id')}: its word comes before {earlier.step.get('id')}'s, but the "
                          "recording taps it later; reorder the steps or the words")
    film_in = None
    if spec.get("place"):
        film_in = clk.frame_of(_shot_start(film_dir, spec["place"]["shot"], clk) + float(spec["place"].get("at", 0)))
    response_frames = max(0, clk.frame_of(float(spec.get("response", spec_mod.DEFAULT_RESPONSE_S))))
    needs_edits = any(a.exact for a in anchors)
    diffs = frame_diffs(raw_dir, raw_count) if needs_edits else []
    result = plan(raw_count, [a for a in anchors if a.step.get("word")], film_in=film_in,
                  response_frames=response_frames, diffs=diffs)
    frames_dir = cap / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    media.clear_sequence(frames_dir)
    for j, r in enumerate(result.mapping):
        media.link_or_copy(raw_dir / media.frame_name(r), frames_dir / media.frame_name(j))
    first_index: dict[int, int] = {}
    for j, r in enumerate(result.mapping):
        first_index.setdefault(r, j)
    try:
        shots = deps.load_shots(film_dir)
    except Exception:  # noqa: BLE001 - shots only name the event's shot; a missing file mustn't stop the map
        shots = []
    tolerance = float(spec.get("tolerance_frames", spec_mod.DEFAULT_TOLERANCE_FRAMES))
    events: list[dict] = []
    worst = 0.0
    for anchor in anchors:
        step = anchor.step
        sid = step.get("id") or f"{step['do']}{step['_index']}"
        j = first_index.get(anchor.raw_frame)
        landed_frame = (result.film_in + j) if (result.film_in is not None and j is not None) else None
        error = (landed_frame - anchor.target_frame) if (landed_frame is not None and anchor.exact) else None
        if error is not None:
            worst = max(worst, abs(error))
            if abs(error) > tolerance:
                misses.append(f"{sid}: lands {error:+d} frame(s) from '{step['word']['text']}' (tolerance "
                              f"±{tolerance:g}): there isn't enough idle time before it to retime")
        if step.get("word") or step["do"] in spec_mod.EVENT_STEPS:
            taps.append({"id": sid, "do": step["do"], "at": step["at"],
                         "actual": next((s.get("actual") for s in rec.get("steps") or []
                                         if s.get("index") == step["_index"]), None),
                         "word": step.get("word"),
                         "film_t": round(anchor.target_t, 4) if anchor.target_t is not None else None,
                         "exact": anchor.exact,
                         "landed_t": round(clk.secs_of(landed_frame), 4) if landed_frame is not None else None,
                         "error_frames": error, "sound": step.get("sound", True)})
        if step["do"] in spec_mod.EVENT_STEPS and step.get("sound", True) and landed_frame is not None:
            shot = (step.get("word") or {}).get("shot") or _shot_at(landed_frame, shots) or \
                (spec.get("place") or {}).get("shot")
            events.append({"shot": shot, "id": f"{name}.{sid}", "t": round(clk.secs_of(landed_frame), 3),
                           "frame": landed_frame, "kind": spec_mod.EVENT_STEPS[step["do"]],
                           "size": step.get("size", "small"),
                           "note": step.get("note") or f"{step['do']} in capture {name}",
                           "source": f"capture:{name}"})
        elif step["do"] in spec_mod.EVENT_STEPS and step.get("sound", True) and result.film_in is not None:
            misses.append(f"{sid}: its frame was cut by the retime, so it has no event")
    events.sort(key=lambda e: e["t"])
    beats = {"capture": name, "film_in": result.film_in, "events": events,
             "note": "Visual events in the shape of timing/visual_events/actN.json; the event emitter merges them "
                     "into their acts." if result.film_in is not None else
             "Unplaced: add `place` or word anchors to the spec so the capture has film times."}
    jsonio.write_atomic(cap / "beats.json", jsonio.mark_generated(beats, "slate capture beatmap"))
    deps.record_made(film_dir, deps.rel(film_dir, cap / "beats.json"), producer="slate capture beatmap",
                     inputs={deps.rel(film_dir, p): None for p in (store.spec_path(film_dir, name),
                                                                    Path(film_dir) / "timing" / "vo_words.json")
                             if p.is_file()})
    rec["frames"] = {**_frames_block(film_dir, frames_dir, film, spec), "film_in": result.film_in}
    rec["taps"] = taps
    rec["retime"] = result.edits
    rec["beats"] = {"file": deps.rel(film_dir, cap / "beats.json"), "count": len(events)}
    rec["misses"] = misses
    rec["made_at"] = deps.now_iso()
    store.save(film_dir, name, rec, "slate capture beatmap")
    gate = _gate(film_dir, name, rec, misses, worst, tolerance, anchored=bool(ordered) or bool(spec.get("place")))
    gate.details.setdefault("stage", "captures")
    gates.write_report(film_dir, [gate])
    return {"capture": name, "frames": rec["frames"], "film_in": result.film_in, "edits": result.edits,
            "events": len(events), "misses": misses, "gate": gate.to_dict()}


def _frames_block(film_dir: Path, frames_dir: Path, film: dict, spec: dict) -> dict:
    from slate.capture import conform  # noqa: PLC0415

    return conform.raw_block(film_dir, frames_dir, film, spec)


def _gate(film_dir: Path, name: str, rec: dict, misses: list[str], worst: float, tolerance: float,
          *, anchored: bool) -> gates.GateResult:
    cap = store.capture_dir(film_dir, name)
    inputs = gates.inputs_for(film_dir, [cap / "capture.json", cap / "spec.json"] +
                              [p for p in (Path(film_dir) / "timing" / "vo_words.json",) if p.is_file()])
    evidence = [deps.rel(film_dir, cap / "beats.json"), deps.rel(film_dir, cap / "capture.json")]
    engine = gates.engine("slate capture beatmap", "1")
    details = {"taps": rec.get("taps"), "retime": rec.get("retime"), "worst_error_frames": worst,
               "tolerance_frames": tolerance}
    gate = f"{GATE_PREFIX}{name}"
    if misses:
        return gates.GateResult.fail(gate, f"{len(misses)} beat miss(es): " + "; ".join(misses[:3]),
                                     evidence=evidence, engine=engine, inputs=inputs, details=details)
    if not anchored:
        return gates.GateResult.unchecked(gate, "not placed on the film clock: no place and no word anchors",
                                          evidence=evidence, engine=engine, inputs=inputs, details=details)
    return gates.GateResult.pass_(gate, f"every step lands within ±{tolerance:g} frame (worst {worst:g})",
                                  evidence=evidence, engine=engine, inputs=inputs, details=details)


def log_summary(result: dict) -> None:
    log.info(f"capture {result['capture']}: {result['frames']['count']} frames from film frame "
             f"{result['film_in']}, {len(result['edits'])} edit(s), {result['events']} event(s)")
