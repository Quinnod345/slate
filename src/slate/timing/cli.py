"""`slate timing`, `slate timing words`, `slate timing misses`, `slate timing show <shot>`, `slate timing stress`."""

from __future__ import annotations

import argparse

from slate import cli, gates, jsonio, log
from slate.errors import UsageError

TIMING_HELP = """\
Build the film's clock: timing/shots.json, vo_cues.json, vo_words.json, events.json and timeline.json.

Frames come from the storyboard on the film's own rate and size (film.json), with drift-free rounding; the takes place
the voice; Whisper (turbo, word timestamps) times every word, cached per take; the script's authored beats resolve to
frames. Stage 5's gate is "frame-exact and contiguous": if the frames aren't, nothing is written.

Once an act render is registered the timing is locked (renders were cut to these frames). A person can still decide
to re-time: --force --approved-by NAME --reason "…" (every render is then stale and gets re-rendered).

Stages:
  words     (re)time the words: --engine whisper (default, local) or elevenlabs (paid, through the ledger)
  misses    word lookups that fell back to an estimate; must be empty at hand-in (exit 1 when it isn't)
  show ID   one shot: frames, timecodes, its read, its words and events
  pin       keep a shot's delivered word times through a re-voice (--shot S --words a,b#2 | all; --list; --clear)
  stress    where a word's stressed syllable starts (S) and its contact frame (C = S + 2), measured from the take

Exit codes: 0 written and every gate passed or is waiting on a person, 1 a gate failed or the timing is locked,
2 usage, 3 missing prerequisite (no storyboard, no Whisper)."""


def _table(results) -> str:
    return gates.format_table(results)


def _summary_lines(out, film_dir) -> list[str]:
    lines = []
    if out.paths:
        lines.append(f"Wrote {', '.join(str(p.relative_to(film_dir)) for p in out.paths)}.")
    else:
        lines.append("Nothing written.")
    shots = out.docs.get("shots") or {}
    if shots:
        lines.append(f"{len(shots.get('shots', []))} shots, {shots.get('total_frames')} frames "
                     f"({shots.get('total_seconds')} s) at {shots.get('fps_num')}/{shots.get('fps_den')} fps, "
                     f"{shots.get('width')}x{shots.get('height')}.")
    if out.misses:
        lines.append(f"{len(out.misses)} word anchor(s) fell back to an estimate: `slate timing misses`.")
    return lines


def _payload(out, film_dir) -> dict:
    summary = gates.summarize(out.results)
    shots = out.docs.get("shots") or {}
    return {"ok": summary["counts"]["FAIL"] == 0 and bool(out.paths), "status": summary["status"],
            "written": [str(p.relative_to(film_dir)) for p in out.paths], "summary": summary,
            "gates": [r.to_dict() for r in out.results], "shots": len(shots.get("shots", [])),
            "total_frames": shots.get("total_frames"), "fps": [shots.get("fps_num"), shots.get("fps_den")],
            "size": [shots.get("width"), shots.get("height")], "word_misses": len(out.misses),
            "aligner": (out.docs.get("vo_words") or {}).get("aligner")}


def _exit(out) -> int:
    return 1 if gates.summarize(out.results)["counts"]["FAIL"] else 0


def _align_first(args: argparse.Namespace, film_dir, engine: str = "whisper", realign: bool = False) -> bool:
    """A dry run whose takes would have to be transcribed first says so and stops (True): transcribing is the slow,
    and with ElevenLabs the paid, part of a run. With every alignment reusable, the dry run builds in memory."""
    if not cli.dry_run(args):
        return False
    from slate.timing.builder import alignments_to_make  # noqa: PLC0415

    need = alignments_to_make(film_dir, engine=engine, realign=realign)
    how = "ElevenLabs Forced Alignment (paid)" if engine == "elevenlabs" else "Whisper on this Mac"
    return bool(need) and cli.dry_stop(args, f"transcribe {len(need)} take(s) ({', '.join(need)}) with {how}, then "
                                             "build and write the timing files", align=need, engine=engine,
                                       paid=engine == "elevenlabs")


def _build(args: argparse.Namespace) -> int:
    from slate.timing.builder import run  # noqa: PLC0415

    film_dir = cli.film(args)
    if _align_first(args, film_dir):
        return 0
    out = run(film_dir, force=bool(getattr(args, "force", False)), dry_run=bool(cli.flag(args, "dry_run", False)),
              approved_by=getattr(args, "approved_by", None), reason=getattr(args, "reason", None))
    log.result(_payload(out, film_dir), "\n".join([_table(out.results), *_summary_lines(out, film_dir)]))
    return _exit(out)


def _words(args: argparse.Namespace) -> int:
    from slate.timing.builder import run  # noqa: PLC0415

    film_dir = cli.film(args)
    if _align_first(args, film_dir, engine=args.engine, realign=bool(getattr(args, "realign", False))):
        return 0
    out = run(film_dir, force=bool(getattr(args, "force", False)), engine=args.engine,
              realign=bool(getattr(args, "realign", False)), dry_run=bool(cli.flag(args, "dry_run", False)),
              approved_by=getattr(args, "approved_by", None), reason=getattr(args, "reason", None))
    words = (out.docs.get("vo_words") or {}).get("shots") or {}
    rows = []
    for sid, rec in words.items():
        missing = [t["t"] for t in rec.get("tokens") or [] if not t.get("exact")]
        snaps = [abs(w["snap"]) for w in rec.get("words") or [] if w.get("snap") is not None]
        rows.append(f"{sid:8} {len(rec.get('words') or []):3} words  max snap {max(snaps or [0]) * 1000:3.0f} ms"
                    + (f"  not heard as written: {', '.join(missing)}" if missing else ""))
    words_gate = next((r for r in out.results if r.gate == "timing.words"), None)
    payload = _payload(out, film_dir)
    payload["engine"] = args.engine
    payload["takes"] = {sid: {"words": len(rec.get("words") or []),
                              "unheard": [t["t"] for t in rec.get("tokens") or [] if not t.get("exact")]}
                        for sid, rec in words.items()}
    human = "\n".join(rows + ([f"{words_gate.status} {words_gate.gate}: {words_gate.summary}"] if words_gate else []) +
                      _summary_lines(out, film_dir))
    log.result(payload, human or "No voiced takes yet.")
    return _exit(out)


def _misses(args: argparse.Namespace) -> int:
    from slate.timing.words import load_misses  # noqa: PLC0415

    film_dir = cli.film(args)
    misses = load_misses(film_dir)
    status = "PASS" if not misses else "FAIL"
    human = ("No word misses: every lookup was exact." if not misses else
             "\n".join([f"{len(misses)} word lookup(s) fell back to an estimate (must be empty at hand-in):"] +
                       [f"  {m.get('shot')} '{m.get('word')}' #{m.get('occ')} {m.get('which')}: {m.get('reason')} "
                        f"(estimated {m.get('estimate')} s)" for m in misses]))
    log.result({"status": status, "misses": misses, "count": len(misses)}, human)
    return 0 if not misses else 1


def _show(args: argparse.Namespace) -> int:
    from slate.clock import Clock  # noqa: PLC0415
    from slate.timing.builder import FILES, load_shots  # noqa: PLC0415

    film_dir = cli.film(args)
    doc = load_shots(film_dir)
    shot = next((s for s in doc.get("shots", []) if s["id"] == args.shot), None)
    if shot is None:
        known = ", ".join(s["id"] for s in doc.get("shots", [])[:40])
        raise UsageError(f"No shot {args.shot!r} in timing/shots.json.", hint=f"Shots: {known}")
    clock = Clock(int(doc["fps_num"]), int(doc["fps_den"]))
    timeline = (jsonio.read(film_dir / FILES["timeline"], default=None) or {}).get("shots", {}).get(args.shot, [])
    start = shot["frame_in"] / clock.fps
    lines = [f"{shot['id']} · act {shot['act']} ({shot['act_name']})",
             f"frames {shot['frame_in']}–{shot['frame_out']} ({shot['frames']} frames) · "
             f"{clock.timecode(shot['frame_in'])}–{clock.timecode(shot['frame_out'])} · {shot['sec_in']:.3f}–"
             f"{shot['sec_out']:.3f} s · storyboard {shot['storyboard_dur']} s, rounding {shot['rounding_in_sec']:+.4f} s in"]
    if shot.get("on_screen"):
        lines.append(f"on screen: {shot['on_screen']}")
    words_out = []
    if shot.get("vo"):
        lines.append(f"VO: {shot['vo']}")
        if "vo_take" in shot:
            lead = float(shot["vo_lead_sec"])
            lines.append(f"take {shot['vo_take']} at +{lead:.3f} s (frame {shot['vo_frame']})")
            rec = shot.get("vo_words") or {}
            for w in rec.get("words") or []:
                t = start + lead + float(w["start"])
                words_out.append({"w": w["w"], "film_s": round(t, 3), "frame": clock.frame_of(t)})
            if words_out:
                lines.append("words: " + "  ".join(f"{w['w']} {w['film_s']:.2f}" for w in words_out))
        else:
            lines.append("not voiced yet")
    for e in timeline:
        extra = f", starts frame {e['start_frame']} ({e.get('ease')} {e.get('dur')} s)" if "start_frame" in e else ""
        anchor = f"word '{e['word']}'" if "word" in e else f"t {e.get('t')} s"
        lines.append(f"event {e['id']}: {anchor} → frame {e['frame']} (film {e['film_frame']}){extra}"
                     + (" ESTIMATED" if e.get("estimated") else ""))
    log.result({"shot": {k: v for k, v in shot.items() if k != "vo_words"}, "words": words_out, "events": timeline,
                "timecode": [clock.timecode(shot["frame_in"]), clock.timecode(shot["frame_out"])]}, "\n".join(lines))
    return 0


def _pin(args: argparse.Namespace) -> int:
    from slate.timing import pins  # noqa: PLC0415

    film_dir = cli.film(args)
    words = [w.strip() for w in (args.words or "").split(",") if w.strip()]
    if args.list:
        shots = pins.load(film_dir)
        lines = [f"{sid}: " + ", ".join(f"{p['w']}{'#' + str(p['occ']) if p.get('occ', 1) > 1 else ''} "
                                         f"({p['start']:.3f} s)" for p in e.get("words") or [])
                 + (f" ({e['why']})" if e.get("why") else "") for sid, e in shots.items()]
        log.result({"pinned": shots}, "\n".join(lines) or "Nothing is pinned: every word follows its take.")
        return 0
    if args.clear:
        if cli.dry_stop(args, f"let go of {', '.join(words) if words else 'every pinned word'}"
                              + (f" in {args.shot}" if args.shot else "") + " (timing/pinned_words.json)"):
            return 0
        res = pins.clear(film_dir, args.shot, words or None)
        log.result(res, f"let go of {res['cleared']} pinned word(s); rebuild the timing (slate timing) to use the takes'")
        return 0
    if not args.shot or not words:
        raise UsageError("Pin words of one shot: slate timing pin --shot S12 --words companies,growth#2 [--why ...]")
    if cli.dry_stop(args, f"pin {args.shot}'s {', '.join(words)} at their delivered times in timing/pinned_words.json"):
        return 0
    res = pins.pin(film_dir, args.shot, words, why=args.why)
    log.result(res, f"{args.shot}: pinned " + ", ".join(f"{p['w']} ({p['start']:.3f} s)" for p in res["pinned"])
               + f" in {res['file']}. A re-voice keeps them; `slate timing` lays them over the new take.")
    return 0


def _stress(args: argparse.Namespace) -> int:
    from slate.timing import stress  # noqa: PLC0415

    res = stress.measure(cli.film(args), args.shot, args.word, occ=args.occ, gap=args.gap)
    occ = f" (time {res['occ']})" if res["occ"] > 1 else ""
    lines = [f"{res['shot']} {res['word']!r}{occ}: S at {res['stress_start']:.3f} s (frame {res['s_frame']}), "
             f"contact frame {res['contact_frame']}",
             f"  {res['why']}" + (f" (in the take: {res['gap_take_s'][0]:.3f}-{res['gap_take_s'][1]:.3f} s)"
                                   if res["gap_take_s"] else ""),
             "  beat: " + ", ".join(f"{k}: {v!r}" for k, v in res["beat"].items()),
             "  stress_end is the word's end; re-measure after any re-voice."]
    log.result(res, "\n".join(lines))
    return 0


def _lock_flags(parser) -> None:
    parser.add_argument("--force", action="store_true",
                        help="rebuild although act renders lock the timing (needs --approved-by and --reason)")
    parser.add_argument("--approved-by", dest="approved_by", help="the person who decided to re-time")
    parser.add_argument("--reason", help="why the locked timing changes")


def register(sub) -> None:
    stages = cli.add_group(sub, "timing", help="the film clock: shots, voice cues, words, events, timeline",
                           description=TIMING_HELP)
    parser = sub.choices["timing"]
    _lock_flags(parser)
    parser.set_defaults(handler=_build)
    words = cli.add_command(stages, "words", _words, help="time every word of the takes (Whisper by default)",
                            description="Align every approved take with its line and rebuild the timing files.\n\n"
                                        "--engine whisper runs the local whisper CLI (turbo, word timestamps); "
                                        "--engine elevenlabs uses ElevenLabs Forced Alignment, a paid call through "
                                        "the ledger. Results are cached per take; --realign redoes them.")
    words.add_argument("--engine", choices=("whisper", "elevenlabs"), default="whisper", help="the aligner")
    words.add_argument("--realign", action="store_true", help="re-run the aligner even for cached takes")
    _lock_flags(words)
    cli.add_command(stages, "misses", _misses, help="word lookups that fell back to an estimate (empty at hand-in)",
                    description="List timing/word_misses.json. Exit 0 when empty, 1 when any lookup was an estimate.")
    show = cli.add_command(stages, "show", _show, help="one shot on the clock: frames, read, words, events",
                           description="Show one shot from timing/shots.json with its words and events in film time.")
    show.add_argument("shot", help="the shot id, e.g. S03")
    pin = cli.add_command(stages, "pin", _pin, help="keep delivered word times through a re-voice",
                          description="Record a shot's delivered word times (film seconds) in timing/pinned_words.json "
                                      "before it's re-voiced. The timing build lays them over the new take, so beats "
                                      "that shape unchanged shots or contracts between acts don't move; only the "
                                      "words left unpinned follow the new take.")
    pin.add_argument("--shot", help="the shot whose words to pin (or clear)")
    pin.add_argument("--words", help="comma-separated words, word#2 for a second time in the line, or all")
    pin.add_argument("--why", help="what leans on these times (an approved neighbour, a contract)")
    pin.add_argument("--list", action="store_true", help="show what's pinned")
    pin.add_argument("--clear", action="store_true", help="let pins go (a shot's, some of its words, or all)")
    stress = cli.add_command(stages, "stress", _stress,
                             help="where a word's stressed syllable starts (S) and its contact frame (C = S + 2)",
                             description="Measure S for a gesture keyed to a word: the word's start for first-syllable "
                                         "stress, else the end of the stop gap before the stressed syllable, found in "
                                         "the take's loudness envelope (the films' stress_onset.py). Prints film "
                                         "seconds and frames, and the beat's word, stress_start and stress_end to put "
                                         "in the act's beats. Changes nothing.")
    stress.add_argument("shot", help="the shot id, e.g. S12")
    stress.add_argument("word", help="the key word, as the line says it")
    stress.add_argument("--occ", type=int, default=1, help="which time the word is said in the line (default 1)")
    stress.add_argument("--gap", type=int, default=1,
                        help="the Nth stop gap from the word's end (default 1, the last): ex-CLU-sive has two")
