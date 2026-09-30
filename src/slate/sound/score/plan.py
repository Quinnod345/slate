"""`slate score plan`: one Eleven Music composition plan per act (split when an act is too long), cut to the shots.

Inputs: timing/shots.json (acts and shots on the film's clock), the brand's sound (key, tempo, instrumentation,
materials, banned timbres) through its palette, and, optionally, sound/score/directions.json, the one hand-edited
file here, which says where the music does something special:

    {"parts": {"act3": {"end": "stop", "stop_before_s": 0.5},
               "act4": {"join": "bloom", "landing_event": "score_landing"}},   # or "landing_s": 182.3,
                                                                              # or "landing_word": {"shot": "S20", "word": "your"}
     "split": {"act2": ["S09"]},              # split an act at these shots (a story turn)
     "planned_stops": [{"from": 0.0, "to": 1.0, "why": "the film opens out of silence"}],
     "styles": {"positive": ["felt piano", "soft low strings"], "negative": []},
     "section_styles": {"S05": ["opens up, wider"]},
     "shape_db": {"act1": -1.0}, "seeds": [7, 21]}

Joins (how a part comes in): start (the film's first frame), act (a 1.2 s equal-power crossfade centred on the cut),
bloom (the bloom lands exactly on the landing), lift (a small arrival nudged onto its beat), arrive (an end-card
cadence). Ends (how a part goes out): handover (the next part's join), or stop (stops dead stop_before_s before the
next landing: a held breath, recorded as a planned stop).

Writes sound/score/plan.json (generated) and sound/score/plans/<part>.json (exactly what's sent to the API).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

from slate import config, jsonio, log
from slate.errors import UsageError
from slate.sound.film import SCORE_DIR, Film, load

PRE_ROLL_S = 3.0
TAIL_S = 3.0
STOP_TAIL_S = 4.5  # a part that stops dead is composed to build this far past the hand-over, so it's at level when cut
MIN_SECTION_S = 3.0
DEFAULT_MAX_PART_S = 90.0
JOINS = ("start", "act", "bloom", "lift", "arrive")
ENDS = ("handover", "stop")


def directions(film: Film) -> dict:
    return jsonio.read(film.path(f"{SCORE_DIR}/directions.json"), default={}) or {}


def max_part_s(model_id: str) -> float:
    limits = config.model_info(model_id).get("limits") or {}
    return float(limits.get("part_seconds_max", DEFAULT_MAX_PART_S))


def _acts(film: Film) -> list[tuple[str, list[dict]]]:
    """[(part id, shots)] in film order, grouped by each shot's act."""
    order: list = []
    groups: dict = {}
    for s in sorted(film.shots.get("shots") or [], key=lambda s: float(s.get("sec_in", 0.0))):
        key = s.get("act", 1)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(s)
    out = []
    for i, key in enumerate(order, 1):
        label = f"act{key}" if isinstance(key, int) or str(key).isdigit() else re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-")
        out.append((label or f"act{i}", groups[key]))
    return out


def _sec(s: dict, film: Film, key: str) -> float:
    if key in s:
        return float(s[key])
    frame = s["frame_in"] if key == "sec_in" else s["frame_out"]
    return int(frame) * film.fps_den / film.fps_num


def _event_time(film: Film, event_id: str) -> float | None:
    folder = film.path("timing/visual_events")
    for p in sorted(folder.glob("act*.json")) if folder.exists() else []:
        try:
            data = jsonio.read(p)
        except Exception:  # noqa: BLE001 - an unreadable act file is skipped (and reported by the events gate)
            continue
        rows = data if isinstance(data, list) else (data.get("events") or data.get("beats") or [])
        for e in rows:
            if isinstance(e, dict) and e.get("id") == event_id and "t" in e:
                return float(e["t"])
    return None


def _word_time(film: Film, spec: dict) -> float | None:
    shot, word, occ = spec.get("shot"), str(spec.get("word", "")), int(spec.get("occ", 1))
    try:
        from slate.timing import words as twords  # noqa: PLC0415

        t, exact = twords.word_time(film.dir, shot, word, occ=occ, which=spec.get("which", "start"))
        if t is not None and exact:  # a landing never rests on an estimate
            return float(t)
    except (ImportError, AttributeError):
        pass
    cues = jsonio.read(film.path("timing/vo_cues.json"), default={}) or {}
    shot_cue = next((c for c in cues.get("cues") or [] if c.get("shot") == shot), None)
    words = (jsonio.read(film.path("timing/vo_words.json"), default={}) or {}).get("shots", {}).get(shot, {}).get("words")
    if not shot_cue or not words:
        return None
    norm = lambda w: re.sub(r"[^a-z0-9']", "", w.lower())  # noqa: E731
    hits = [w for w in words if norm(w.get("w", "")) == norm(word)]
    if len(hits) < occ:
        return None
    t0 = float(shot_cue["t"]) if "t" in shot_cue else film.shot_start(shot) + float(shot_cue.get("at", 0.0))
    return t0 + float(hits[occ - 1]["start"])


def _landing(film: Film, spec: dict, default: float) -> tuple[float, str]:
    if spec.get("landing_s") is not None:
        return float(spec["landing_s"]), "directions: landing_s"
    if spec.get("landing_event"):
        t = _event_time(film, str(spec["landing_event"]))
        if t is None:
            raise UsageError(f"directions.json lands a part on event {spec['landing_event']!r}, which isn't in "
                             "timing/visual_events.")
        return t, f"the picture's beat {spec['landing_event']!r}"
    if spec.get("landing_word"):
        t = _word_time(film, spec["landing_word"])
        if t is None:
            raise UsageError(f"directions.json lands a part on the word {spec['landing_word']}, which isn't in the word "
                             "timings.")
        return t, f"the word {spec['landing_word'].get('word')!r} in {spec['landing_word'].get('shot')}"
    return default, "the act's first frame"


def _split(shots: list[dict], film: Film, limit_body: float, at: list[str]) -> list[list[dict]]:
    """Split an act's shots into runs whose span fits limit_body: first at the directed shots, then nearest the middle."""
    groups = [shots]
    if at:
        groups = []
        cur: list[dict] = []
        for s in shots:
            if s["id"] in at and cur:
                groups.append(cur)
                cur = []
            cur.append(s)
        groups.append(cur)
    out: list[list[dict]] = []
    for g in groups:
        stack = [g]
        while stack:
            run = stack.pop(0)
            span = _sec(run[-1], film, "sec_out") - _sec(run[0], film, "sec_in")
            if span <= limit_body or len(run) == 1:
                out.append(run)
                continue
            mid = _sec(run[0], film, "sec_in") + span / 2
            k = min(range(1, len(run)), key=lambda i: abs(_sec(run[i], film, "sec_in") - mid))
            stack[0:0] = [run[:k], run[k:]]
    return out


def _sections(body: list[tuple[str, float, float]], start: float, end: float, pre_s: float, tail_s: float,
              styles: dict, shots_by_id: dict) -> list[dict]:
    """Section list for a part: pre-roll, one section per shot span inside [start, end] (merged to at least 3 s),
    tail. Durations come from rounded cumulative boundaries, so they add up exactly."""
    spans = []
    for sid, a, b in body:
        a, b = max(a, start), min(b, end)
        if b - a > 1e-6:
            spans.append([sid, a - start + pre_s, b - start + pre_s])
    # every section at least 3 s: a short one joins the section after it (so a short last shot joins the tail)
    rows = [["pre-roll", 0.0, pre_s]] + spans + [["tail", pre_s + (end - start), pre_s + (end - start) + tail_s]]
    i = 1
    while i < len(rows) - 1:
        if rows[i][2] - rows[i][1] < MIN_SECTION_S:
            j = i + 1 if i + 1 < len(rows) else i - 1
            lo, hi = min(i, j), max(i, j)
            name = rows[i][0] + "+" + rows[j][0] if j > i else rows[j][0] + "+" + rows[i][0]
            rows[lo:hi + 1] = [[name, rows[lo][1], rows[hi][2]]]
            i = max(1, lo)
            continue
        i += 1
    ms = [int(round(r[1] * 1000)) for r in rows] + [int(round(rows[-1][2] * 1000))]
    names = [r[0] for r in rows]
    sections = []
    for i, name in enumerate(names):
        pos, neg = [], []
        if name == "pre-roll":
            pos = ["the part's opening, already at its full level by the end of this section"]
            neg = ["silence", "fade-in from nothing"]
        elif name.endswith("tail"):
            pos = ["carries on at the same level, no ending, no fade"]
            neg = ["ending", "fade out", "silence"]
        else:
            for sid in name.split("+"):
                pos += list((styles.get(sid) or []))
                note = (shots_by_id.get(sid) or {}).get("music")
                if note:
                    pos += [note] if isinstance(note, str) else list(note)
        sections.append({"section_name": name, "positive_local_styles": pos, "negative_local_styles": neg,
                         "duration_ms": ms[i + 1] - ms[i], "lines": []})
    return sections


def global_styles(film: Film, extra: dict | None = None) -> tuple[list[str], list[str]]:
    pal = film.palette
    sound = (film.brand or {}).get("sound") or {}
    pos = list((pal.get("score") or {}).get("positive_styles") or [])
    for key in ("instrumentation", "instruments", "mood"):
        v = sound.get(key)
        if v:
            pos += [v] if isinstance(v, str) else list(v)
    tempo = sound.get("tempo") or sound.get("tempo_bpm")
    if isinstance(tempo, (int, float)):
        pos.append(f"{tempo:g} BPM")
    elif isinstance(tempo, (list, tuple)) and len(tempo) == 2:
        pos.append(f"{tempo[0]:g}-{tempo[1]:g} BPM")
    if sound.get("key"):
        pos.append(f"in {sound['key']}")
    pos += list((extra or {}).get("positive") or [])
    neg = list((pal.get("score") or {}).get("negative_styles") or [])
    neg += list(pal.get("banned_timbres") or [])
    neg += list(pal.get("materials") or [])  # the score never plays the effects' materials
    motif = sound.get("motif") or {}
    if isinstance(motif, dict) and motif.get("instrument"):
        neg.append(str(motif["instrument"]))  # the character's own instrument belongs to the character
    neg += list((extra or {}).get("negative") or [])
    return list(dict.fromkeys(pos)), list(dict.fromkeys(neg))


def build(film_dir: Path, *, seeds: list[int] | None = None, write: bool = True) -> dict:
    """Plan the film's score. Returns the plan (and writes plan.json and plans/<part>.json unless write=False)."""
    film = load(film_dir)
    d = directions(film)
    model_id = config.model("music", film.data)
    limit = max_part_s(model_id)
    shots_by_id = film.shots_by_id()
    part_dirs = d.get("parts") or {}
    split_at = d.get("split") or {}
    pre_s = float(d.get("pre_roll_s", PRE_ROLL_S))
    # 1. parts: acts, split where they'd run past the limit with their pre-roll and tail
    raw: list[dict] = []
    for label, shots in _acts(film):
        body_limit = limit - pre_s - STOP_TAIL_S - 0.5
        runs = _split(shots, film, body_limit, list(split_at.get(label) or []))
        for k, run in enumerate(runs):
            pid = label if len(runs) == 1 else f"{label}{chr(ord('a') + k)}"
            raw.append({"id": pid, "act": label, "shots": [s["id"] for s in run],
                        "start": _sec(run[0], film, "sec_in"), "end": _sec(run[-1], film, "sec_out")})
    if not raw:
        raise UsageError("timing/shots.json has no shots, so there's nothing to score.")
    # 2. landings and joins
    for i, p in enumerate(raw):
        spec = part_dirs.get(p["id"]) or (part_dirs.get(p["act"]) if p["id"] == p["act"] else None) or {}
        p["join"] = spec.get("join") or ("start" if i == 0 else "act")
        if p["join"] not in JOINS:
            raise UsageError(f"Part {p['id']}: join {p['join']!r} isn't one of {', '.join(JOINS)}.")
        p["end_kind"] = spec.get("end") or "handover"
        if p["end_kind"] not in ENDS:
            raise UsageError(f"Part {p['id']}: end {p['end_kind']!r} isn't one of {', '.join(ENDS)}.")
        p["stop_before_s"] = float(spec.get("stop_before_s", 0.5))
        default_landing = 0.0 if i == 0 else p["start"]
        p["landing_s"], p["landing_from"] = _landing(film, spec, default_landing)
        p["shape_db"] = float((d.get("shape_db") or {}).get(p["id"], 0.0))
    for i, p in enumerate(raw):
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        p["hand_over_s"] = nxt["landing_s"] if nxt else film.duration_s
        if p["hand_over_s"] <= p["landing_s"]:
            raise UsageError(f"Part {p['id']} would hand over at {p['hand_over_s']:.3f} s, before its landing at "
                             f"{p['landing_s']:.3f} s; check directions.json.")
        if p["end_kind"] == "stop" and nxt is None:
            raise UsageError(f"Part {p['id']} is the last part, so it can't stop before a next one.")
    # 3. composition plans
    pos_g, neg_g = global_styles(film, d.get("styles"))
    body_all = [(s["id"], _sec(s, film, "sec_in"), _sec(s, film, "sec_out")) for s in
                sorted(film.shots.get("shots") or [], key=lambda s: float(s.get("sec_in", 0.0)))]
    stops = [dict(s) for s in (d.get("planned_stops") or [])]
    parts = []
    for p in raw:
        tail = STOP_TAIL_S if p["end_kind"] == "stop" else TAIL_S
        sections = _sections(body_all, p["landing_s"], p["hand_over_s"], pre_s, tail, d.get("section_styles") or {},
                             shots_by_id)
        total_ms = sum(s["duration_ms"] for s in sections)
        if total_ms / 1000 >= limit:
            raise UsageError(f"Part {p['id']} would be {total_ms / 1000:.1f} s with its pre-roll and tail, not under the "
                             f"{limit:g} s limit; split it (directions.json \"split\").")
        short = [s for s in sections if s["duration_ms"] < MIN_SECTION_S * 1000]
        if short:
            raise UsageError(f"Part {p['id']}: section {short[0]['section_name']} is under {MIN_SECTION_S:g} s.")
        comp = {"positive_global_styles": pos_g, "negative_global_styles": neg_g, "sections": sections}
        if p["end_kind"] == "stop":
            stops.append({"from": round(p["hand_over_s"] - p["stop_before_s"], 3), "to": round(p["hand_over_s"], 3),
                          "why": f"{p['id']} stops dead {p['stop_before_s']:g} s before {p['hand_over_s']:.3f} s (a held breath)"})
        parts.append({"id": p["id"], "act": p["act"], "shots": p["shots"], "join": p["join"], "end": p["end_kind"],
                      "stop_before_s": p["stop_before_s"] if p["end_kind"] == "stop" else None,
                      "landing_s": round(p["landing_s"], 6), "landing_from": p["landing_from"],
                      "hand_over_s": round(p["hand_over_s"], 6), "pre_roll_s": pre_s, "tail_s": tail,
                      "seconds": round(total_ms / 1000, 3), "shape_db": p["shape_db"],
                      "plan_file": f"{SCORE_DIR}/plans/{p['id']}.json", "composition_plan": comp})
    broadcast = film.broadcast()
    licence = {"broadcast": broadcast, "needs_enterprise": broadcast,
               "recorded": film.music_licence(),
               "note": ("Broadcast use (film, TV, radio): Eleven Music's self-serve plans exclude it; this score needs "
                        "an Enterprise licence before the film airs.") if broadcast else
                       "Online use: a paid Eleven Music plan covers it."}
    plan = {"kind": "plan", "film": film.id, "model_id": model_id, "model_note": "always passed explicitly: the API's "
            "default is an older model", "sample_rate": film.sr, "duration_s": round(film.duration_s, 6),
            "fps_num": film.fps_num, "fps_den": film.fps_den, "pre_roll_s": pre_s, "max_part_s": limit,
            "min_section_s": MIN_SECTION_S, "seeds": list(seeds or d.get("seeds") or (film.palette.get("score") or {}).get("seeds") or [7, 21]),
            "level_lufs": float((film.palette.get("score") or {}).get("level_lufs", -16.0)),
            "styles": {"positive": pos_g, "negative": neg_g}, "licence": licence, "planned_stops": stops,
            "parts": parts}
    problems = jsonio.validate(jsonio.mark_generated(plan, "slate score plan"), "score")
    if problems:
        raise UsageError("The score plan doesn't match its schema:\n  " + "\n  ".join(problems[:10]))
    if broadcast and not str(film.music_licence() or "").lower().startswith("enterprise"):
        log.warn("broadcast format: Eleven Music needs its Enterprise licence for film, TV and radio (recorded in the plan)")
    if write:
        out = film.path(SCORE_DIR)
        for part in parts:
            jsonio.write_atomic(film.path(part["plan_file"]), copy.deepcopy(part["composition_plan"]))
        jsonio.write_atomic(out / "plan.json", jsonio.mark_generated(plan, "slate score plan"))
    return plan


def load_plan(film: Film) -> dict:
    from slate.errors import MissingPrerequisite  # noqa: PLC0415

    p = film.path(f"{SCORE_DIR}/plan.json")
    if not p.exists():
        raise MissingPrerequisite(f"{film.id} has no score plan yet.", hint="Run `slate score plan`.")
    return jsonio.read(p)
