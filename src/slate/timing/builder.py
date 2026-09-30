"""`slate timing`: the storyboard and the real takes in, the film's timing files out.

Writes, in one go and only when the frames are exact:

- timing/shots.json      every shot on the master clock (fps and size from film.json)
- timing/vo_cues.json    where each approved take plays
- timing/vo_words.json   word timings per take (Whisper turbo by default), with every script token resolved
- timing/events.json     the script's authored beats
- timing/timeline.json   those beats resolved to frames

and starts a fresh timing/word_misses.json (the timeline's anchors that fell back to an estimate).

Stage 5's gate is "frame-exact and contiguous": if the frames aren't, nothing is written. The other gates (reads fit
their shots, words heard, events resolved) are reported with their evidence and set the exit code, but don't block the
files, because a film in progress needs its clock before every line is voiced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slate import __version__, gates, jsonio, log
from slate.clock import Clock
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.gates import GateResult
from slate.timing import align as aligner
from slate.timing import ease as easing
from slate.timing import words as wordlib
from slate.timing.cues import make_cues
from slate.timing.shots import check_frames, iter_shots, make_shots
from slate.timing.takes import Take, find_takes
from slate.timing.timeline import collect_events, resolve
from slate.timing.vowords import TWO_WORD_SHARE, record_for_take, unheard

TIMING = Path("timing")
FILES = {
    "shots": TIMING / "shots.json",
    "vo_cues": TIMING / "vo_cues.json",
    "vo_words": TIMING / "vo_words.json",
    "timeline": TIMING / "timeline.json",
    "events": TIMING / "events.json",
}
STORYBOARD = Path("script") / "storyboard.json"
COMMAND = "slate timing"


# --- film inputs --------------------------------------------------------------------------------------------------------

def load_film(film_dir: Path) -> dict:
    path = Path(film_dir) / "film.json"
    if not path.is_file():
        raise UsageError(f"{film_dir} has no film.json.", hint="slate new <id> --brand <brand> --format <format>")
    return jsonio.read(path)


def film_clock(film: dict) -> Clock:
    """The film's clock. film.json must say its rate: Slate never assumes one."""
    try:
        num, den = int(film["fps_num"]), int(film["fps_den"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageError("film.json needs fps_num and fps_den (the frame rate as a fraction).",
                         hint="Set them in film.json; `slate new` writes them from the format.") from exc
    if num <= 0 or den <= 0:
        raise UsageError(f"film.json's frame rate {num}/{den} isn't a positive fraction.")
    return Clock(num, den)


def film_size(film: dict) -> tuple[int, int]:
    try:
        return int(film["width"]), int(film["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageError("film.json needs width and height (the frame size in pixels).",
                         hint="Set them in film.json; `slate new` writes them from the format.") from exc


def film_voice(film: dict) -> str | None:
    voice = film.get("voice")
    if isinstance(voice, dict):
        return voice.get("voice_id") or voice.get("id")
    return voice if isinstance(voice, str) else None


def load_storyboard(film_dir: Path) -> dict:
    path = Path(film_dir) / STORYBOARD
    if not path.is_file():
        raise MissingPrerequisite("There's no storyboard yet: timing starts from script/storyboard.json.",
                                  hint="Run `slate script build` first.")
    return jsonio.read(path)


def load_shots(film_dir: Path) -> dict:
    path = Path(film_dir) / FILES["shots"]
    if not path.is_file():
        raise MissingPrerequisite("This film has no timing/shots.json yet.", hint="Run `slate timing`.")
    return jsonio.read(path)


def brand_eases(film_dir: Path, film: dict) -> dict:
    """The brand's named eases for landings (linear is always there)."""
    from slate.story.inputs import brand_motion  # noqa: PLC0415 (story owns the brand-pack reading)

    return easing.eases_from_motion(brand_motion(film))


def timing_lock_reason(film_dir: Path) -> str | None:
    """Why the timing can't change now, or None, without changing anything (dry runs, and films without state).

    Renders are cut to these frames, so a registered act render locks them; an approved timing file is locked too.
    """
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        state_lock = None
    if state_lock is not None:
        try:
            data = state_lock.load(Path(film_dir))
            for rel in FILES.values():
                if (data.get("files", {}).get(str(rel)) or {}).get("locked"):
                    return f"{rel} is locked in film.lock.json"
            if ((data.get("frozen") or {}).get("timeline") or {}).get("locked"):
                return "the timeline is locked in film.lock.json (act renders were registered)"
        except Exception:  # noqa: BLE001 - a state problem mustn't hide the registry check below
            pass
    registry = jsonio.read(Path(film_dir) / "renders" / "registry.json", default=None) or {}
    entries = registry.get("entries") or []
    if entries:
        return f"{len(entries)} render(s) are registered in renders/registry.json and were cut to the current frames"
    return None


def guard_timing(film_dir: Path, *, force: bool, approved_by: str | None, reason: str | None) -> dict:
    """Refuse to move frames once act renders are registered, unless a person forces it with their name and a reason.

    The state module owns the timeline lock (`slate.state.lock.guard_timeline`: it remembers the lock in film.lock.json
    and logs every forced change). Without it, the render registry is checked here with the same rule.
    """
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        state_lock = None
    if state_lock is not None and hasattr(state_lock, "guard_timeline"):
        for rel in FILES.values():  # an approved file is locked by content: no force moves it
            entry = state_lock.entry(film_dir, str(rel)) if hasattr(state_lock, "entry") else None
            if entry and entry.get("locked"):
                raise Refused(f"{rel} is locked in film.lock.json ({(entry.get('lock') or {}).get('reason', 'approved')}).",
                              hint="It changes only through its approval.")
        return state_lock.guard_timeline(film_dir, force=force, approved_by=approved_by, reason=reason)
    locked = timing_lock_reason(film_dir)
    if locked and not force:
        raise Refused(f"The timing is locked: {locked}.",
                      hint="If a person decides to re-time and re-render, run with --force --approved-by NAME "
                           "--reason \"…\".")
    if locked and not ((approved_by or "").strip() and (reason or "").strip()):
        raise Refused("Changing a locked timeline needs the person's name (--approved-by) and a reason (--reason).")
    return {"allowed": True, "locked": bool(locked), "approved_by": approved_by}


def _record_lock(film_dir: Path, rel: Path, inputs: dict) -> None:
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        return
    try:
        state_lock.record(film_dir, str(rel), producer=COMMAND, inputs=inputs, tool_version=__version__)
    except Exception as exc:  # noqa: BLE001 - the files are written; say so rather than fail after the fact
        log.warn(f"couldn't record {rel} in film.lock.json: {exc}")


# --- gates ----------------------------------------------------------------------------------------------------------------

def check_reads(storyboard: dict, shots_doc: dict, takes: dict[str, Take], clock: Clock) -> GateResult:
    """Each read fits its shot: it starts `lead` in and ends at least `min_tail` before the cut (voice_script.py's rule)."""
    timing = storyboard.get("timing") or {}
    min_tail = float(timing.get("min_tail", 0.25))
    frames = {s["id"]: s for s in shots_doc.get("shots", [])}
    too_long, missing, fits = [], [], 0
    for _i, _act, sh in iter_shots(storyboard):
        if not (sh.get("vo") or "").strip():
            continue
        take = takes.get(sh["id"])
        if take is None:
            missing.append(sh["id"])
            continue
        lead = float(sh.get("lead", timing.get("lead", 0.0)) or 0.0)
        dur = frames[sh["id"]]["frames"] / clock.fps
        slack = dur - (lead + take.read_seconds)
        if slack < min_tail:
            too_long.append(f"{sh['id']}: the {take.read_seconds:.2f} s read starting {lead:.2f} s into a {dur:.2f} s "
                            f"shot ends {min_tail - slack:.2f} s too late (it needs {min_tail:.2f} s before the cut)")
        else:
            fits += 1
    eng = gates.engine("slate timing", __version__)
    if too_long:
        return GateResult.fail("timing.reads", f"{len(too_long)} read(s) don't fit their shots",
                               evidence=too_long + ["retime with `slate script build --from-takes`"], engine=eng)
    if missing:
        return GateResult.unchecked("timing.reads", f"{len(missing)} line(s) aren't voiced yet: {', '.join(missing[:12])}",
                                    evidence=[f"{fits} read(s) fit"], engine=eng, details={"missing": missing})
    return GateResult.pass_("timing.reads", f"all {fits} reads fit their shots" if fits else "no voice in this film",
                            engine=eng)


def check_words(storyboard: dict, words_doc: dict, takes: dict[str, Take]) -> GateResult:
    """Every voiced shot has word timings, and every script word is accounted for in what the aligner heard."""
    shots = words_doc.get("shots") or {}
    voiced = [sh["id"] for _i, _a, sh in iter_shots(storyboard) if (sh.get("vo") or "").strip()]
    without = [sid for sid in voiced if sid in takes and sid not in shots]
    unvoiced = [sid for sid in voiced if sid not in takes]
    unheard_by_shot = {sid: unheard(rec) for sid, rec in shots.items() if unheard(rec)}
    infos = [r.get("aligner") or {} for r in shots.values()]
    aligners = sorted({f"{i.get('engine')} model {i.get('model') or '-'}, version {i.get('version') or '-'}"
                       for i in infos})
    replayed = any(i.get("replay") for i in infos)
    first = infos[0] if infos else {}
    eng = gates.engine(first.get("engine") or "none", first.get("version"), model=first.get("model"), replay=replayed)
    evidence = [f"aligner: {a}" for a in aligners] + [f"{len(shots)} take(s) timed"]
    if replayed:
        evidence.append("replay mode: word timings are stand-ins, not a real transcription")
    if without:
        return GateResult.fail("timing.words", f"{len(without)} voiced take(s) have no word timings: "
                               f"{', '.join(without[:12])}", evidence=evidence + ["run `slate timing words`"], engine=eng)
    if unheard_by_shot:
        listed = [f"{sid}: {', '.join(v)}" for sid, v in unheard_by_shot.items()]
        return GateResult.review("timing.words", f"{sum(len(v) for v in unheard_by_shot.values())} script word(s) aren't "
                                 "heard as written; listen to these takes", evidence=evidence + listed, engine=eng,
                                 details={"unheard": unheard_by_shot})
    if unvoiced:
        return GateResult.unchecked("timing.words", f"{len(unvoiced)} line(s) aren't voiced yet", evidence=evidence,
                                    engine=eng, details={"unvoiced": unvoiced})
    return GateResult.pass_("timing.words", f"every word of {len(shots)} take(s) is heard and timed" if shots else
                            "no voice in this film", evidence=evidence, engine=eng)


def check_events(problems: list[str], words_doc: dict, events: dict) -> GateResult:
    eng = gates.engine("slate timing", __version__)
    n = sum(len(v) for v in (events.get("shots") or {}).values())
    if not problems:
        return GateResult.pass_("timing.events", f"all {n} authored event(s) resolved to frames" if n else
                                "no authored events", engine=eng)
    timed = set((words_doc.get("shots") or {}).keys())
    hard = [p for p in problems if "isn't heard" not in p or p.split("/", 1)[0] in timed]
    if hard:
        return GateResult.fail("timing.events", f"{len(hard)} event problem(s): {hard[0]}", evidence=problems[:30],
                               engine=eng)
    return GateResult.review("timing.events", f"{len(problems)} event(s) anchor to words that aren't timed yet",
                             evidence=problems[:30], engine=eng)


# --- the build --------------------------------------------------------------------------------------------------------------

@dataclass
class TimingBuild:
    paths: list[Path] = field(default_factory=list)
    results: list[GateResult] = field(default_factory=list)
    docs: dict = field(default_factory=dict)
    misses: list[dict] = field(default_factory=list)


def _warn_if_stale(film_dir: Path, storyboard: dict) -> None:
    made_from = (storyboard.get("made_from") or {}).get("script/script.json")
    script = Path(film_dir) / "script" / "script.json"
    if made_from and script.is_file() and jsonio.sha256(script) != made_from:
        log.warn("script/script.json changed since the storyboard was built; run `slate script build` to catch up.")


def run(film_dir: Path, *, force: bool = False, engine: str = "whisper", realign: bool = False,
        dry_run: bool = False, approved_by: str | None = None, reason: str | None = None) -> TimingBuild:
    """Build everything in memory, check it, then write it (unless dry_run or the frames fail)."""
    film_dir = Path(film_dir)
    film = load_film(film_dir)
    clock = film_clock(film)
    width, height = film_size(film)
    lang = film.get("language") or "en"
    storyboard = load_storyboard(film_dir)
    if dry_run:
        locked = timing_lock_reason(film_dir)
        if locked:
            log.warn(f"the timing is locked ({locked}); a real run would need --force --approved-by --reason")
    else:
        guard_timing(film_dir, force=force, approved_by=approved_by, reason=reason)
    _warn_if_stale(film_dir, storyboard)
    voiced = [sh["id"] for _i, _a, sh in iter_shots(storyboard) if (sh.get("vo") or "").strip()]
    takes = find_takes(film_dir, voiced)
    cues, _missing = make_cues(storyboard, takes, voice=film_voice(film))
    share = float((storyboard.get("timing") or {}).get("two_word_share", TWO_WORD_SHARE))
    words_doc: dict = {"note": "Word timings inside each approved take (seconds from the take's first sample). Starts "
                               "are the aligner's, snapped to the take's own energy onset within ±80 ms; a start with "
                               "snap null was placed inside a transcribed number, not measured. `tokens` resolves "
                               "every word of the script line.", "shots": {}}
    lines = {sh["id"]: sh.get("vo") or "" for _i, _a, sh in iter_shots(storyboard)}
    runs = []
    for sid in voiced:
        take = takes.get(sid)
        if take is None:
            continue
        rec_align = aligner.align_take(film_dir, sid, take, lines[sid], engine=engine, language=lang, force=realign,
                                       film=film, write_cache=not dry_run)
        info = {k: rec_align.get(k) for k in ("engine", "model", "version", "replay")}
        runs.append(info)
        rec = record_for_take(take_rel=take.rel, line=lines[sid], result=rec_align["result"], take_path=take.path,
                              lang=lang, take_seconds=take.read_seconds, two_word_share=share)
        rec["aligner"] = info
        words_doc["shots"][sid] = rec
    if runs:
        words_doc["aligner"] = runs[0]
    events = collect_events(storyboard)
    shots_doc = make_shots(storyboard, clock=clock, width=width, height=height, cues=cues, words=words_doc,
                           events=events)
    shots_doc["tool_version"] = __version__
    frames_gate = check_frames(shots_doc, clock)
    out = TimingBuild(results=[frames_gate])
    eases = brand_eases(film_dir, film)
    timeline_doc, problems, misses = resolve(events, shots_doc, words_doc, eases, clock=clock, lang=lang)
    out.results += [check_reads(storyboard, shots_doc, takes, clock), check_words(storyboard, words_doc, takes),
                    check_events(problems, words_doc, events)]
    out.docs = {"shots": shots_doc, "vo_cues": cues, "vo_words": words_doc, "timeline": timeline_doc,
                "events": events}
    out.misses = misses
    if not frames_gate.passed or dry_run:
        return out
    inputs = {str(STORYBOARD): jsonio.sha256(film_dir / STORYBOARD), "film.json": jsonio.sha256(film_dir / "film.json")}
    for sid, take in sorted(takes.items()):
        inputs[take.rel] = take.sha256()
    for key, rel in FILES.items():
        doc = jsonio.mark_generated(out.docs[key], COMMAND)
        out.paths.append(jsonio.write_atomic(film_dir / rel, doc))
    wordlib.reset_misses(film_dir)
    if misses:
        wordlib.record_misses(film_dir, misses)
    for rel in FILES.values():
        _record_lock(film_dir, rel, inputs)
    for r in out.results:
        r.inputs = gates.inputs_for(film_dir, [STORYBOARD, "film.json", *[t.rel for t in takes.values()]])
    gates.write_report(film_dir, out.results)
    return out


def build(film_dir: Path, **kw) -> list[Path]:
    """The §12 interface: write the timing files and return their paths. Refuses when the frames aren't exact."""
    result = run(Path(film_dir), **kw)
    frames = result.results[0]
    if not frames.passed:
        raise Refused(f"Timing not written: {frames.summary}.", details={"gate": frames.to_dict()})
    return result.paths
