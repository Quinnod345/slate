"""Exact declared statistic displays, their canonical visual count landings, and measured post-count holds.

Authored timeline events are shot-local and may use estimated voice anchors. Only the final visible visual-events
export supplies a count landing; absent exports never become evidence that a displayed number is static.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from slate.errors import SlateError
from slate.qa import textmatch
from slate.qa.checks import Sub
from slate.qa.film import FilmContext, iter_storyboard_shots
from slate.story import numbers
from slate.timing import beats

KEY = "count_events"
EXACT_EXEMPT = "_count_not_for_reading"
HOLD_SECONDS = 2.0


def bindings(value: Any, sid: str) -> tuple[list[dict], list[str]]:
    """Validate metadata only. It names exact displays and event ids; it grants no reading exemption."""
    if not isinstance(value, list) or not value:
        return [], [f"{sid}: count_events should be a nonempty list of text/events bindings"]
    out, problems, seen = [], [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {"text", "events"}:
            problems.append(f"{sid}: each count_events binding needs exactly text and events")
            continue
        text, ids = row["text"], row["events"]
        if (not isinstance(text, str) or not text.strip() or not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) or not i.strip() for i in ids)):
            problems.append(f"{sid}: count_events needs a literal text and nonempty event-id strings")
            continue
        text = text.strip()
        if text in seen or len(set(ids)) != len(ids):
            problems.append(f"{sid}: count_events repeats a display or event id ({text!r})")
            continue
        seen.add(text)
        out.append({"text": text, "events": list(ids)})
    return out, problems


def exempt(decl: dict | None, text: str) -> bool:
    """Count-specific exemptions use literal identity; generic OCR squash cannot identify a counted surface."""
    decl = decl or {}
    return decl.get("not_for_reading") is True or text in decl.get(EXACT_EXEMPT, set())


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, TypeError, ValueError):
        return None


def _clock(ctx: FilmContext) -> tuple[float | None, str | None]:
    rates = []
    try:
        for source, doc in (("film.json", ctx.film), (ctx.SHOTS, ctx.shots_doc or {})):
            if not any(key in doc for key in ("fps_num", "fps_den")):
                continue
            num, den = doc.get("fps_num"), doc.get("fps_den")
            if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (num, den)):
                return None, f"{source} has no valid rational film clock"
            rates.append(Fraction(num, den))
        if not rates or len(set(rates)) != 1 or ctx.clock is None:
            return None, "film.json and shots.json need one consistent rational film clock"
    except (SlateError, TypeError, ValueError, ZeroDivisionError) as exc:
        return None, f"the film clock is unavailable: {str(exc).splitlines()[0]}"
    return float(rates[0]), None


def _raw_shots(script: dict) -> list[dict]:
    return [shot for act in script.get("acts", []) if isinstance(act, dict)
            for shot in (act.get("shots") or act.get("lines") or []) if isinstance(shot, dict)]


def _authored_count(ctx: FilmContext, sid: str) -> bool:
    board = [s for s in iter_storyboard_shots(ctx.storyboard or {}) if str(s.get("id")) == sid]
    source_ids = {str(s.get("source_id") or sid) for s in board} or {sid}
    try:
        script = _raw_shots(ctx.script or {})
    except SlateError:
        script = []  # declaration parsing reports the unreadable script; never infer a count from it
    sources = board + [s for s in ctx.shots if str(s.get("id")) == sid]
    sources += [s for s in script if str(s.get("id")) in source_ids]
    return any(isinstance(e, dict) and e.get("kind") == "count" for s in sources
               if isinstance(s.get("events"), list) for e in s["events"])


def _numeric(text: str, lang: str) -> bool:
    return any(mention.counts for mention in numbers.mentions(text, lang))


@dataclass
class Plan:
    inputs: list[str] = field(default_factory=list)
    anchors: dict[tuple[str, str], dict] = field(default_factory=dict)
    claimed: set[tuple[str, str]] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    discovery: dict = field(default_factory=dict)


def prepare(ctx: FilmContext, decls: dict[str, dict], displays: list[tuple[str, str]]) -> Plan:
    """Resolve explicit identities and discover unbound counts without imposing export prerequisites on static text."""
    plan = Plan(inputs=["film.json"])
    display_counts = Counter(displays)
    try:
        lang = ctx.film.get("language") or "en"
    except SlateError:
        lang = "en"
    numeric_shots = {sid for sid, text in displays if _numeric(text, lang)}
    acts = ctx.acts
    raw, files, limits = {}, [], []
    for act in acts:
        rel = ctx.events_rel(act["index"])
        plan.inputs.append(rel)
        try:
            doc = ctx.read_json(rel)
            listed = doc if isinstance(doc, list) else (doc.get("events", doc.get("beats"))
                                                       if isinstance(doc, dict) else None)
            raw[act["index"]] = listed if isinstance(listed, list) else []
            files.append({"path": rel, "exists": ctx.exists(rel),
                          "counts": sum(isinstance(e, dict) and e.get("kind") == "count"
                                        for e in raw[act["index"]])})
            if doc is None:
                limits.append(f"{rel} is absent: no exported count can be discovered from it")
        except SlateError as exc:
            raw[act["index"]] = []
            files.append({"path": rel, "exists": True, "problem": str(exc).splitlines()[0]})
            limits.append(f"{rel} is unreadable: no exported count can be discovered from it")
    if not acts:
        limits.append("no placed shot/act clock exists to locate canonical count exports")
    limits.append("Static checks are retained when no readable count is authored or exported; missing exports do not "
                  "prove that a number is static. Timers and nonnumeric motion acquire no count-reading rule merely "
                  "from kind=count.")
    plan.discovery = {"files": files, "limits": limits, "bound_exemptions": []}
    all_bindings = {sid: decl.get(KEY, []) for sid, decl in decls.items() if decl.get(KEY)}
    relevant = numeric_shots | set(all_bindings)
    fps, clock_problem = _clock(ctx) if relevant else (None, None)
    for sid in sorted(relevant):
        declarations = all_bindings.get(sid, [])
        for binding in declarations:
            text = binding["text"]
            plan.claimed.add((sid, text))
            if display_counts[(sid, text)] != 1:
                plan.problems.append(f"{sid}: count_events text must name exactly one literal on_screen part "
                                     f"({text!r}, found {display_counts[(sid, text)]})")
        if decls.get(sid, {}).get("not_for_reading") is True:
            # The validated whole-shot exemption excludes this surface from audience reading requirements.
            continue
        placed = [s for s in ctx.shots if str(s.get("id")) == sid]
        owners = [a for a in acts if sid in a["shots"]]
        if len(placed) != 1 or len(owners) != 1:
            if declarations or _authored_count(ctx, sid):
                plan.unknown.append(f"{sid}: count landing needs one placed shot and owning act; next: slate timing")
            continue
        shot, act = placed[0], owners[0]
        rel = ctx.events_rel(act["index"])
        events = raw.get(act["index"], [])
        counts = [e for e in events if isinstance(e, dict) and e.get("shot") == sid and e.get("kind") == "count"]
        authored = _authored_count(ctx, sid)
        if not (counts or authored or declarations):
            continue
        declared_ids = {i for b in declarations for i in b["events"]}
        uncovered = [e.get("id", "<missing id>") for e in counts
                     if not isinstance(e.get("id"), str) or e["id"] not in declared_ids]
        if not declarations or uncovered:
            ids = ", ".join(str(i) for i in uncovered) or "the authored count"
            plan.unknown.append(f"{sid}: {rel} has unbound readable count {ids}; next: add reading.count_events "
                                "with its exact final on_screen text and event ids, then rebuild the storyboard")
        try:
            export = ctx.act_events(act)
            export_problem = "; ".join(export.problems) if export.problems else None
        except (SlateError, TypeError, ValueError, OverflowError) as exc:
            export_problem = str(exc).splitlines()[0]
        duplicates = {i for i, n in Counter(e.get("id") for e in events if isinstance(e, dict)
                                          and isinstance(e.get("id"), str)).items() if n > 1}
        for binding in declarations:
            text, ids = binding["text"], binding["events"]
            if display_counts[(sid, text)] != 1:
                continue
            problem = clock_problem or export_problem
            landings = {}
            for identity in ids:
                candidates = [e for e in events if isinstance(e, dict) and e.get("shot") == sid
                              and e.get("id") == identity]
                if len(candidates) != 1 or identity in duplicates:
                    problem = problem or f"event {identity!r} needs one exact canonical identity in {sid}"
                    continue
                event = candidates[0]
                t, start, dur = (_finite(event.get(k)) for k in ("t", "start", "dur"))
                if (event.get("kind") != "count" or t is None or start is None or dur is None or start < 0
                        or dur <= 0 or not math.isfinite(start + dur)):
                    problem = problem or f"event {identity!r} needs kind=count and finite forward t/start/dur"
                    continue
                if fps is None:
                    continue
                if abs(t - start - dur) > beats.tolerance(fps):
                    problem = problem or f"event {identity!r} does not land at start + dur on the film clock"
                    continue
                act_lo = ctx.secs_of(int(act["frame_in"]))
                act_hi = ctx.secs_of(int(act["frame_out"]))
                half_frame = beats.tolerance(fps)
                if not act_lo - half_frame <= t <= act_hi + half_frame:
                    problem = problem or f"event {identity!r} is outside its owning act on the film clock"
                    continue
                lo, hi = ctx.secs_of(int(shot["frame_in"])), ctx.secs_of(int(shot["frame_out"]))
                if not lo - 1 / fps <= t <= hi + 1 / fps:
                    problem = problem or f"event {identity!r} is outside its owning shot"
                    continue
                for key in ("from", "to", "x", "pan"):
                    if key in event and _finite(event[key]) is None:
                        problem = problem or f"event {identity!r} has nonfinite/non-numeric {key}"
                if "frame" in event:
                    frame = event["frame"]
                    if (isinstance(frame, bool) or not isinstance(frame, int) or frame < 0
                            or abs(ctx.secs_of(frame) - t) > beats.tolerance(fps)):
                        problem = problem or f"event {identity!r} has an invalid/conflicting film frame"
                landings[identity] = t
            if problem or len(landings) != len(ids):
                plan.unknown.append(f"{sid} {text!r}: {rel}: {problem or 'the declared count is missing'}; "
                                    "next: repair the named visual event and emit its owning act")
                continue
            # every count in the shot, with its range when it declares one, so a bare value among other numbers can
            # be told apart from a counter passing through it (measure)
            counters = [{"id": e.get("id"), "start": _finite(e.get("start")), "t": _finite(e.get("t")),
                         "from": _finite(e.get("from")), "to": _finite(e.get("to"))} for e in counts]
            starts = [c["start"] for c in counters if c["id"] in ids and c["start"] is not None]
            # every count in the act too: a hold that runs on past its own shot is read against all of them (measure)
            act_counters = [{"id": e.get("id"), "start": _finite(e.get("start")), "t": _finite(e.get("t")),
                             "from": _finite(e.get("from")), "to": _finite(e.get("to"))}
                            for e in events if isinstance(e, dict) and e.get("kind") == "count"]
            plan.anchors[(sid, text)] = {"shot": sid, "text": text, "events": list(ids), "event_landings": landings,
                                         "source": rel, "act": act["index"], "landing_seconds": max(landings.values()),
                                         "counters": counters, "count_start_seconds": min(starts) if starts else None,
                                         "act_counters": act_counters,
                                         "act_seconds": [ctx.secs_of(int(act["frame_in"])),
                                                         ctx.secs_of(int(act["frame_out"]))]}
    return plan


AMBIGUOUS = "OCR shows several numeric surfaces without an exact display location"


def _own_display(text: str, at: float, anchor: dict, shown_before: bool) -> bool:
    """A bare counted value seen once among other numbers is still its own count's display when no other counter in
    the shot is mid-count across that value at that moment (so none can be passing through it) and the value wasn't
    on screen in the shot before its own count began (so no static number shares it). A counter that doesn't declare
    its from/to range could be showing anything, so it keeps the value ambiguous."""
    try:
        value = float(text.replace(",", ""))
    except ValueError:
        return False
    if shown_before or anchor.get("count_start_seconds") is None:
        return False
    for c in anchor.get("counters") or []:
        if c["id"] in anchor["events"]:
            continue
        if c["start"] is None or c["t"] is None:
            return False
        if c["start"] <= at <= c["t"]:
            if c["from"] is None or c["to"] is None:
                return False
            lo, hi = sorted((c["from"], c["to"]))
            if lo <= value <= hi:
                return False
    return True


def _carried(ctx: FilmContext, sweep, anchor: dict, text: str, hits: list[float], every: float, shot_end: float,
             lang: str, test, shown_before: bool) -> list[float]:
    """Samples past the shot's end that carry the same display on, for a count that lands late in its shot.

    A count landing 1.4 s before its shot ends can still hold 2 s on screen as the next shot begins. Only a run
    that reaches the shot's last sample carries on, sample after sample with no gap, inside the owning act, as the
    exact display; a bare value among other numbers counts only when no count anywhere in the act could be passing
    through it at that moment. The first miss ends it."""
    if not hits:
        return []
    in_shot = sorted(_finite(s.get("t")) for s in sweep.samples
                     if _finite(s.get("t")) is not None and _finite(s.get("t")) < shot_end)
    if not in_shot or abs(hits[-1] - in_shot[-1]) > 1e-6:
        return []
    act_end = (anchor.get("act_seconds") or [None, shot_end])[1]
    wide = {**anchor, "counters": anchor.get("act_counters") or anchor["counters"]}
    out, last = [], hits[-1]
    for sample in sorted((s for s in sweep.samples if _finite(s.get("t")) is not None),
                         key=lambda s: _finite(s.get("t"))):
        at = _finite(sample.get("t"))
        if at < shot_end:
            continue
        if at >= act_end or abs(at - last - every) >= 1e-6:
            break
        if test is not None and not test(ctx.frame_of(at)):
            break
        present, problem = textmatch.count_present(text, sample.get("lines"), lang=lang)
        if problem == AMBIGUOUS and _own_display(text, at, wide, shown_before):
            present, problem = True, None
        if problem or not present:
            break
        out.append(at)
        last = at
    return out


def _first_run(times: list[float], every: float, landing: float) -> tuple[float, float] | None:
    runs = []
    for at in times:
        if not runs or abs(at - runs[-1][1] - every) >= 1e-6:
            runs.append([at, at])
        else:
            runs[-1][1] = at
    return next(((start, end + every) for start, end in runs if end + every >= landing), None)


def measure(ctx: FilmContext, plan: Plan, sweep, legible: dict, exemptions: set[tuple[str, str]]) -> tuple[list[Sub], list[dict]]:
    """The first complete final-value run holds after landing/settle, with one sampled exit interval, never two."""
    rows, short, unknown = [], [], []
    every = _finite(sweep.every)
    for key, anchor in plan.anchors.items():
        if key in exemptions:
            continue
        sid, text = key
        if anchor["act"] in sweep.missing_acts:
            unknown.append(f"{sid} {text!r}: the count picture is not rendered; next: slate render")
            continue
        if every is None or every <= 0:
            unknown.append(f"{sid} {text!r}: OCR needs a finite positive sample interval")
            continue
        shot = ctx.shot_by_id[sid]
        lo, hi = ctx.secs_of(int(shot["frame_in"])), ctx.secs_of(int(shot["frame_out"]))
        hits, uncertain = [], []
        lang = ctx.film.get("language") or "en"
        begun = anchor.get("count_start_seconds")
        shown_before = begun is not None and any(
            lo <= _finite(s.get("t")) < begun
            and any(part.replace(" ", "") == text.replace(" ", "")
                    for line in (s.get("lines") or []) if isinstance(line, dict) and isinstance(line.get("text"), str)
                    for part in textmatch.literal_parts(line["text"]))
            for s in sweep.samples if _finite(s.get("t")) is not None)
        for sample in sweep.samples:
            at = _finite(sample.get("t"))
            if at is None or not lo <= at < hi:
                continue
            test = legible.get(key)
            if test is not None and not test(ctx.frame_of(at)):
                continue
            present, problem = textmatch.count_present(text, sample.get("lines"), lang=lang)
            if problem == AMBIGUOUS and _own_display(text, at, anchor, shown_before):
                present, problem = True, None
            if problem:
                uncertain.append((at, problem))
            elif present:
                hits.append(at)
        hits += _carried(ctx, sweep, anchor, text, hits, every, hi, lang, legible.get(key), shown_before)
        landing = anchor["landing_seconds"]
        run = _first_run(hits, every, landing)
        start, end = run if run else (None, None)
        required = max(landing, start if start is not None else landing) + HOLD_SECONDS
        row = {**anchor, "readable_start_seconds": start, "readable_end_seconds": end,
               "required_end_seconds": required, "sample_slack_seconds": every}
        rows.append(row)
        relevant_unknown = [problem for at, problem in uncertain if at + every >= landing and at <= required]
        if relevant_unknown:
            unknown.append(f"{sid} {text!r}: exact final display is unproved: {relevant_unknown[0]}")
        elif end is None or end + 1e-9 < required:
            observed = f"{end:.2f} s" if end is not None else "no exact complete final display"
            short.append(f"{sid} {text!r}: {observed}, needs a continuous 2.0 s after landing/settle "
                         f"through {required:.2f} s ({anchor['source']}, {', '.join(anchor['events'])})")
    subs = []
    if short:
        subs.append(Sub("count_hold", "FAIL", f"{len(short)} counted display(s) leave too early", short))
    if unknown:
        subs.append(Sub("count_picture", "UNCHECKED", "counted final displays need exact readable picture evidence", unknown))
    if rows and not short and not unknown:
        subs.append(Sub("count_hold", "PASS", f"all {len(rows)} counted display(s) hold after their final count"))
    return subs, rows
