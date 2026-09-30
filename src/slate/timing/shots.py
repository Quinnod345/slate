"""storyboard -> shots.json: every shot's frames on the film's master clock, drift-free.

This is FILMROOT/pipeline/tools/make_shots.py with the frame rate and size taken from film.json instead of constants.
Each shot's *cumulative* start is rounded to the nearest master frame and its length is the difference between
neighbouring rounded starts, so no rounding error builds up across the film; every rounding is recorded per shot. The
record carries everything an engine needs (frames, seconds, the VO placement, the take's words, authored events), so
the engines read one file and never re-derive a boundary.
"""

from __future__ import annotations

from slate.clock import Clock
from slate.gates import GateResult, engine


def iter_shots(storyboard: dict):
    """(act index from 1, act, shot) for every shot in order."""
    for act_i, act in enumerate(storyboard.get("acts") or [], 1):
        for shot in act.get("shots") or []:
            yield act_i, act, shot


def make_shots(storyboard: dict, *, clock: Clock, width: int, height: int, cues: dict | None = None,
               words: dict | None = None, events: dict | None = None) -> dict:
    """The shots.json document for a storyboard, exactly as the films built it."""
    vo = {c["shot"]: c for c in (cues or {}).get("cues", [])}
    word_shots = (words or {}).get("shots", {}) or {}
    event_shots = (events or {}).get("shots", {}) or {}
    shots, t = [], 0.0
    for act_i, act, sh in iter_shots(storyboard):
        d = float(sh["dur"])
        f_in, f_out = clock.frame_of(t), clock.frame_of(t + d)
        rec = {
            "id": sh["id"], "act": act_i, "act_name": act["name"],
            "frame_in": f_in, "frame_out": f_out, "frames": f_out - f_in,
            "sec_in": round(clock.secs_of(f_in), 6), "sec_out": round(clock.secs_of(f_out), 6),
            "storyboard_sec_in": round(t, 6), "storyboard_dur": d,
            "rounding_in_sec": round(clock.secs_of(f_in) - t, 6),
            "rounding_dur_sec": round(clock.secs_of(f_out - f_in) - d, 6),
            "enter_token": sh.get("enter_token"),
            "vo": sh.get("vo") or "", "on_screen": sh.get("on_screen") or "",
            "picture": sh.get("picture", ""), "transition_out": sh.get("transition", ""),
            "build": sh.get("build", ""), "source": sh.get("source", ""), "sound": sh.get("sound", ""),
        }
        if sh["id"] in vo:
            c = vo[sh["id"]]
            rec["vo_take"] = c["file"]
            rec["vo_lead_sec"] = c["at"]
            rec["vo_frame"] = f_in + clock.frame_of(c["at"])
        if sh["id"] in word_shots:
            rec["vo_words"] = word_shots[sh["id"]]
        if sh["id"] in event_shots:
            rec["events"] = event_shots[sh["id"]]
        if sh.get("generate"):
            rec["generate"] = sh["generate"]  # the script's generation request for this shot (read by `slate gen`)
        shots.append(rec)
        t += d
    total_f = clock.frame_of(t)
    acts = []
    for i, a in enumerate(storyboard.get("acts") or [], 1):
        ids = [s["id"] for s in a.get("shots") or []]
        spans = [s for s in shots if s["act"] == i]
        act = {"index": i, "name": a["name"], "shots": ids}
        if spans:
            act.update(frame_in=spans[0]["frame_in"], frame_out=spans[-1]["frame_out"],
                       frames=spans[-1]["frame_out"] - spans[0]["frame_in"])
        acts.append(act)
    return {"fps_num": clock.fps_num, "fps_den": clock.fps_den, "width": int(width), "height": int(height),
            "storyboard_version": storyboard.get("version"), "storyboard_seconds": round(t, 6),
            "total_frames": total_f, "total_seconds": round(clock.secs_of(total_f), 6),
            "acts": acts, "shots": shots}


def check_frames(doc: dict, clock: Clock) -> GateResult:
    """Stage 5's gate: frame-exact and contiguous. First frame 0, no gaps or overlaps, whole frames, totals agree."""
    problems: list[str] = []
    shots = doc.get("shots") or []
    if not shots:
        problems.append("there are no shots")
    else:
        if shots[0]["frame_in"] != 0:
            problems.append(f"{shots[0]['id']} starts at frame {shots[0]['frame_in']}, not 0")
        for a, b in zip(shots, shots[1:]):
            if a["frame_out"] != b["frame_in"]:
                kind = "gap" if a["frame_out"] < b["frame_in"] else "overlap"
                problems.append(f"{kind} between {a['id']} (out {a['frame_out']}) and {b['id']} (in {b['frame_in']})")
        for s in shots:
            if not all(isinstance(s[k], int) for k in ("frame_in", "frame_out", "frames")):
                problems.append(f"{s['id']} has fractional frames")
            elif s["frames"] != s["frame_out"] - s["frame_in"]:
                problems.append(f"{s['id']}: frames {s['frames']} != out − in")
            elif s["frames"] < 1:
                problems.append(f"{s['id']} is {s['frames']} frames long (a shot needs at least one frame)")
            if abs(s["sec_in"] - round(clock.secs_of(s["frame_in"]), 6)) > 1e-6:
                problems.append(f"{s['id']}: sec_in doesn't match its frame on the {clock.fps_num}/{clock.fps_den} clock")
        if shots[-1]["frame_out"] != doc.get("total_frames"):
            problems.append(f"the last shot ends at {shots[-1]['frame_out']}, total_frames says {doc.get('total_frames')}")
        order = [sid for a in doc.get("acts") or [] for sid in a.get("shots") or []]
        if order != [s["id"] for s in shots]:
            problems.append("the acts' shot lists don't match the shot order")
        ids = [s["id"] for s in shots]
        dupes = sorted({x for x in ids if ids.count(x) > 1})
        if dupes:
            problems.append(f"duplicate shot ids: {', '.join(dupes)}")
    worst = max(shots, key=lambda s: abs(s["rounding_in_sec"])) if shots else None
    evidence = [f"{len(shots)} shots, {doc.get('total_frames')} frames at {clock.fps_num}/{clock.fps_den} fps"]
    if worst:
        evidence.append(f"largest boundary rounding {worst['rounding_in_sec']:+.4f} s at {worst['id']}")
    eng = engine("slate timing", doc.get("tool_version"), method="cumulative rounding")
    if problems:
        return GateResult.fail("timing.frames", f"frames aren't exact and contiguous: {problems[0]}",
                               evidence=evidence + problems[:20], engine=eng, details={"problems": problems})
    return GateResult.pass_("timing.frames", f"{len(shots)} shots, {doc.get('total_frames')} frames, contiguous from "
                            "frame 0 with no gaps or overlaps", evidence=evidence, engine=eng)
