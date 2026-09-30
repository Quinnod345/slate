"""The beats an act's picture lands (timing/visual_events/actN.json), and the timing rules a schema can't hold.

`t` is always the beat: the film second the motion lands, its contact. A sustained motion also carries its span,
`start` (film seconds) and `dur` (seconds):

- a draw and a count end on the beat: t = start + dur (a count reaches its `to` at t);
- a whoosh and a swell peak on the beat, somewhere inside the span;
- any other kind may give a span too, and its beat sits inside it.

Why it's explicit: the films timed one act's sustained draw at its start while the sound handover said a draw ends on
t (lesson L-timing-D034), so the sound engine had to guess which. The schema (visual_events.schema.json) requires the
span for draws and counts and keeps start and dur together; `problems()` checks the arithmetic the schema can't, to
half a frame when it knows the frame rate. Every engine that writes beats (the Remotion kit, the open web renderer,
After Effects acts, capture beat maps) and every reader (sound, QA) can call it, so they agree on what t means.
"""

from __future__ import annotations

from typing import Any, Iterable

KINDS = ("tap", "type", "land", "snap", "count", "draw", "data", "page", "stamp", "whoosh", "swell", "character",
         "impact")
ENDS_ON_BEAT = ("draw", "count")  # the beat is the end of the span
PEAKS_ON_BEAT = ("whoosh", "swell")  # the beat is inside the span
SUSTAINED = ENDS_ON_BEAT + PEAKS_ON_BEAT
NO_CLOCK_TOLERANCE_S = 0.002  # without a frame rate: writers round to the millisecond, so allow two


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def span(beat: dict) -> tuple[float, float] | None:
    """(start, end) in film seconds when a beat gives its span, else None."""
    if not isinstance(beat, dict):
        return None
    start, dur = _number(beat.get("start")), _number(beat.get("dur"))
    if start is None or dur is None:
        return None
    return start, start + dur


def tolerance(fps: float | None = None) -> float:
    """How far a beat may sit from its span's edge: half a frame on the film's clock, else two milliseconds."""
    return 0.5 / float(fps) if fps else NO_CLOCK_TOLERANCE_S


def problems(beats: Iterable[Any] | dict, *, fps: float | None = None) -> list[str]:
    """What's wrong with an act's beat timing beyond the schema. Empty when every beat agrees with its span.

    A silent act ({"silent": true, ...}) has nothing to check. Entries that aren't beats are the schema's to report.
    """
    if isinstance(beats, dict):
        return []
    tol = tolerance(fps)
    out: list[str] = []
    for i, beat in enumerate(beats or [], 1):
        if not isinstance(beat, dict):
            continue
        where = f"beat {i} ({beat.get('id')})" if beat.get("id") else f"beat {i}"
        kind = beat.get("kind")
        t = _number(beat.get("t"))
        has_start, has_dur = "start" in beat, "dur" in beat
        if kind in ENDS_ON_BEAT and not (has_start and has_dur):
            out.append(f"{where}: a {kind} runs over time, so it needs start and dur (it ends on t)")
            continue
        if has_start != has_dur:
            out.append(f"{where}: start and dur go together (the span a sustained motion runs over)")
            continue
        sp = span(beat)
        if sp is None:
            if has_start or has_dur:
                out.append(f"{where}: start and dur must be numbers of seconds")
            continue
        start, end = sp
        if start < 0 or end <= start:
            out.append(f"{where}: the span {start:.3f}–{end:.3f} s isn't a forward stretch of film time")
            continue
        if t is None:
            continue  # the schema reports a beat without a time
        if kind in ENDS_ON_BEAT:
            if abs(end - t) > tol:
                out.append(f"{where}: a {kind} ends on its beat, but start + dur is {end:.3f} s and t is {t:.3f} s "
                           f"({(end - t) * 1000:+.0f} ms)")
        elif not (start - tol <= t <= end + tol):
            what = "peaks" if kind in PEAKS_ON_BEAT else "lands"
            out.append(f"{where}: a {kind} {what} inside its span, but t {t:.3f} s is outside {start:.3f}–{end:.3f} s")
    return out


def beat_span(beat: dict, *, default_dur: float | None = None) -> tuple[float, float] | None:
    """The span to shape a sound over: the beat's own, else (for a sustained kind, when the caller has a default
    length) the default length ending on the beat for a draw or count, and centred on it for a whoosh or swell."""
    own = span(beat)
    if own is not None or default_dur is None:
        return own
    t = _number(beat.get("t"))
    if t is None or beat.get("kind") not in SUSTAINED:
        return None
    if beat.get("kind") in ENDS_ON_BEAT:
        return max(0.0, t - default_dur), t
    return max(0.0, t - default_dur / 2), t + default_dur / 2
