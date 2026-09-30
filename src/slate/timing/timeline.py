"""Authored beats -> the resolved timeline: one frame for every move, on the film clock.

FILMROOT/pipeline/tools/timeline.py, generalized. An event is either timed ("t": seconds from its shot's first frame)
or anchored to a spoken word ("word", "occ", "offset"). "land": "<ease>:<seconds>" marks an entrance whose event time
is when it visibly lands (90% of its travel), and the resolver back-computes its start frame so the landing hits the
word. Events come from the script (each shot's `events`) and are collected into timing/events.json; the eases are the
brand's named eases. A word that can't be found never stops the build: the event gets an estimate, is marked
`estimated`, and the miss is listed for hand-in (it must be fixed before then).
"""

from __future__ import annotations

from slate.clock import Clock
from slate.timing import ease as easing
from slate.timing.shots import iter_shots
from slate.timing.words import estimate, lookup, read_span


def collect_events(storyboard: dict) -> dict:
    """timing/events.json: every shot's authored beats, in film order (the films' events.json shape)."""
    shots = {}
    for _i, _act, sh in iter_shots(storyboard):
        if sh.get("events"):
            shots[sh["id"]] = sh["events"]
    return {"version": storyboard.get("version"),
            "note": "Authored beats from script/script.json (each shot's events). Timed ('t', seconds from the shot's "
                    "first frame) or anchored to a spoken word ('word', 'occ', 'offset'); 'land' is ease:seconds for an "
                    "entrance whose landing (90% of travel) is the event time. Resolved into timing/timeline.json.",
            "shots": shots}


def resolve(events: dict, shots_doc: dict, words: dict, eases: dict, *, clock: Clock,
            lang: str = "en") -> tuple[dict, list[str], list[dict]]:
    """(timeline document, problems, word misses). Deterministic; never raises on a missing word."""
    fps = clock.fps
    shots = {s["id"]: s for s in shots_doc.get("shots", [])}
    word_shots = (words or {}).get("shots", {}) or {}
    timeline: dict[str, list] = {}
    problems: list[str] = []
    misses: list[dict] = []
    allowance = clock.frame_of(1.0)  # an event may land up to a second past its shot's cut (a carry)
    for sid, lst in (events.get("shots") or {}).items():
        sh = shots.get(sid)
        if sh is None:
            problems.append(f"{sid}: events for a shot that isn't in shots.json")
            continue
        out = []
        for e in lst:
            rec = {k: v for k, v in e.items() if k != "sound"}
            if "word" in e:
                record = word_shots.get(sid) or {}
                occ = e.get("occ", 1)
                vo_lead = (sh["vo_frame"] - sh["frame_in"]) / fps if "vo_frame" in sh else float(sh.get("vo_lead_sec") or 0)
                hit = lookup(record, str(e["word"]), occ, "start", lang)
                if hit is not None:
                    word_s = hit[0]
                else:
                    span = read_span(record.get("words") or [], (0.0, float(record.get("take_seconds") or
                                                                           max(0.0, sh["frames"] / fps - vo_lead))))
                    word_s = estimate(record.get("line") or sh.get("vo") or "", str(e["word"]),
                                      occ if isinstance(occ, int) else 1, "start", span, lang)
                    rec["estimated"] = True
                    problems.append(f"{sid}/{e.get('id')}: '{e['word']}' (occurrence {occ}) isn't heard in the take; "
                                    "estimated from its place in the line")
                    misses.append({"shot": sid, "word": str(e["word"]), "occ": occ, "which": "start",
                                   "estimate": round(sh["frame_in"] / fps + vo_lead + word_s, 4),
                                   "reason": f"timeline event {e.get('id')}"})
                t_evt = vo_lead + word_s + float(e.get("offset", 0.0))
                rec["anchored_word_sec"] = round(vo_lead + word_s, 4)
            elif "t" in e:
                t_evt = float(e["t"])
            else:
                problems.append(f"{sid}/{e.get('id')}: an event needs 't' (seconds into the shot) or 'word'")
                continue
            f_evt = clock.frame_of(t_evt)
            rec["frame"] = f_evt  # shot-relative frame of the event (the landing)
            rec["sec"] = round(f_evt / fps, 6)
            rec["film_frame"] = sh["frame_in"] + f_evt
            if "land" in e:
                try:
                    name, dur, curve = easing.parse_land(e["land"], eases)
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    problems.append(f"{sid}/{e.get('id')}: {getattr(exc, 'message', exc)}")
                else:
                    start = clock.frame_of(t_evt - easing.t90(curve, dur))
                    rec["start_frame"] = start
                    rec["start_sec"] = round(start / fps, 6)
                    rec["dur"] = dur
                    rec["ease"] = name
            if not (0 <= f_evt <= sh["frames"] + allowance):
                problems.append(f"{sid}/{e.get('id')}: frame {f_evt} is outside the shot ({sh['frames']} frames)")
            out.append(rec)
        timeline[sid] = out
    doc = {"fps": fps, "fps_num": clock.fps_num, "fps_den": clock.fps_den, "shots": timeline}
    return doc, problems, misses

