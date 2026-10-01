"""`slate voice …`: cast or design a voice, direct it, voice every line, clean, pace, polish, align, measure, caption
and check it.

    slate voice profile            the film's (or a brand's) voice profile and the cloning-policy verdict
    slate voice account            the ElevenLabs plan: tier, characters left, the best voice format it allows
    slate voice cast               several voices read the same lines; measured, ranked, and a listening reel
    slate voice design             Voice Design previews from a spec, measured and ranked; --save, --check
    slate voice tags               direction: strip, check, set, clear, ab (tag A/B takes), rules; the tag loop:
                                   optimize (voice and measure a round), pick, abc (listen), adopt (the exact file)
    slate voice tts                one take per line with neighbour context, pinned, fitted to its shot
    slate voice clean              diagnose and clean the takes (ffmpeg chain, Voice Isolator, or Voice Remix)
    slate voice pace               shorten pauses and quicken gently with Rubber Band (formants kept)
    slate voice polish             the studio vocal chain on every take (de-esser, EQ, compression, a small room),
                                   length and loudness kept, verified by measurement; --try A,B to hear strengths
    slate voice align              word timings: local Whisper, or ElevenLabs Forced Alignment to evaluate
    slate voice analyze            measured inflection: pitch, breath, sibilance, endings, the Whisper checks, and
                                   A/B spectrogram sheets for comparing takes, tags or polish strengths
    slate voice captions           SRT, WebVTT and timing/captions.json, timed to the words
    slate voice check              the voice gates (policy, direction, takes, words, tails, fit, inflection, polish)
    slate voice approve            record the person's approval of a take and lock it by hash

Paid calls go through the spend ledger and replay offline under SLATE_FAKE_VENDORS=1; --live spends.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from slate import cli, jsonio, log
from slate.errors import MissingPrerequisite, UsageError

LONG_STAGES = {"tts": ("elevenlabs_tts",), "align": ("whisper",), "cast": ("elevenlabs_tts",),
               "design": ("elevenlabs_tts",)}


def _film(args) -> Path:
    return cli.film(args)


def _maybe_film(args) -> Path | None:
    try:
        return cli.film(args)
    except UsageError:
        return None


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _nums(value: str | None, kind=float) -> list | None:
    items = _csv(value)
    try:
        return [kind(v) for v in items] if items else None
    except ValueError as exc:
        raise UsageError(f"Couldn't read {value!r} as a list of numbers.") from exc


def _rebuild(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[str]:
    """The stage's own arguments, back as argv (for a detached job), without --detach."""
    argv: list[str] = []
    for act in parser._actions:  # noqa: SLF001 - argparse has no public API for this
        if act.dest in ("help", "detach") or isinstance(act, argparse._HelpAction):  # noqa: SLF001
            continue
        if not act.option_strings:
            value = getattr(args, act.dest, None)
            if value is None:
                continue
            argv += [str(v) for v in value] if isinstance(value, list) else [str(value)]
            continue
        if act.dest in ("json", "film", "live", "dry_run", "quiet"):
            continue
        value = getattr(args, act.dest, act.default)
        opt = act.option_strings[-1]
        if isinstance(act, argparse._StoreTrueAction):  # noqa: SLF001
            if value:
                argv.append(opt)
        elif isinstance(act, argparse._AppendAction):  # noqa: SLF001
            for v in value or []:
                argv += [opt, str(v)]
        elif value is not None and value != act.default:
            argv += [opt, str(value)]
    return argv


def _detach(args, stage: str, parser: argparse.ArgumentParser) -> int:
    try:
        # By its full name, so the module registered in sys.modules is the one used (a `from slate.jobs import
        # client` reads the package attribute instead, which can point elsewhere after a module is reloaded).
        jobs = importlib.import_module("slate.jobs.client")
    except ModuleNotFoundError as exc:
        if not str(exc.name or "").startswith("slate.jobs"):
            raise
        raise MissingPrerequisite("The job queue isn't installed yet, so this can't run detached.",
                                  hint=f"Run `slate voice {stage}` without --detach.") from None
    film = _maybe_film(args)
    argv = [sys.executable, "-m", "slate", "voice", stage, *(["--film", str(film)] if film else []),
            *(["--live"] if cli.flag(args, "live") else []), *_rebuild(parser, args)]
    job = jobs.submit(f"voice-{stage}", argv, film=str(film) if film else None, resources=LONG_STAGES[stage])
    log.result({"job": job, "argv": argv}, f"submitted as job {job}; follow it with `slate job status {job}`")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# Handlers


def cmd_profile(args) -> int:
    from slate.voice import profile as profile_mod  # noqa: PLC0415

    if args.brand:
        prof, mode = profile_mod.load(args.brand), args.mode or "supervised"
    else:
        from slate.voice.film import VoiceFilm  # noqa: PLC0415
        vf = VoiceFilm(_film(args))
        prof, mode = vf.profile, args.mode or vf.mode
    problems = profile_mod.policy_problems(prof, mode=mode)
    human = profile_mod.summary(prof) + "\n" + ("policy: allowed in " + mode + " mode" if not problems else
                                                "policy: REFUSED\n  - " + "\n  - ".join(problems))
    log.result({"profile": prof, "mode": mode, "policy_problems": problems, "allowed": not problems}, human)
    return 1 if problems else 0


def cmd_account(args) -> int:
    from slate.vendors.elevenlabs import Client  # noqa: PLC0415

    client = Client(mode="autonomous" if args.oneiro else "supervised")
    sub = client.subscription(snapshot=args.snapshot)
    left = None
    if sub.get("character_limit") is not None:
        left = int(sub["character_limit"]) - int(sub.get("character_count") or 0)
    human = (f"ElevenLabs ({client.key_name}): {sub.get('tier')} plan, {left:,} characters left" if left is not None
             else f"ElevenLabs ({client.key_name}): {sub.get('tier')} plan")
    human += f"\nvoice format: {sub.get('best_voice_format')} ({sub.get('format_reason')})"
    if sub.get("replay"):
        human += "\n(replay: a stand-in plan; run with --live to read the real one, free)"
    log.result({**sub, "characters_left": left, "key": client.key_name}, human)
    return 0


def cmd_cast(args) -> int:
    if args.detach:
        return _detach(args, "cast", args._parser)
    from slate.voice import cast  # noqa: PLC0415

    film = None if args.brand else _maybe_film(args)
    if film is None and not args.brand:
        raise UsageError("Cast for a film (--film, or run inside one) or for a brand (--brand).")
    res = cast.run(film, brand=args.brand, voices=_csv(args.voices), shots=_csv(args.shots), text=args.text,
                   name=args.name, seed=args.seed, model=args.model, out=args.out, make_reel=not args.no_reel)
    lines = [f"{i}. {r['voice']} ({r['source']}): {r['score']:.3f}" for i, r in enumerate(res["ranking"], 1)]
    human = f"shootout {res['name']}: {res['takes']} takes in {res['folder']}\n" + "\n".join(lines)
    if res.get("reel"):
        human += f"\nlisten: {res['reel']} (level-matched; the ranking is a shortlist, pick by ear)"
    log.result(res, human)
    return 0


def cmd_design(args) -> int:
    from slate.voice import design  # noqa: PLC0415

    film = None if args.brand else _maybe_film(args)
    if args.save or args.check:
        folder = Path(args.folder) if args.folder else None
        if folder is None:
            raise UsageError("Name the design folder with --folder (it holds previews.json).")
        if args.save:
            if not args.voice_name:
                raise UsageError("--save needs --voice-name.")
            rec = design.save(folder, args.save, args.voice_name, film_dir=film, brand=args.brand)
            log.result(rec, f"saved {rec['name']} as voice {rec['voice_id']}\nnext: {rec['next']}")
            return 0
        res = design.check(folder, text=args.text, models=_csv(args.models), film_dir=film, brand=args.brand)
        rows = [f"{r['voice']} on {r['model']}: {r['metrics'].get('wpm_span')} wpm, "
                f"F0 {r['metrics'].get('f0_median_hz')} Hz, WER {r['metrics'].get('wer')}" for r in res["renders"]]
        log.result(res, "production-model check:\n" + "\n".join(rows))
        return 0
    if args.detach:
        return _detach(args, "design", args._parser)
    if film is None and not args.brand:
        raise UsageError("Design for a film (--film, or run inside one) or for a brand (--brand).")
    if args.spec:
        spec = jsonio.read(args.spec)
    elif args.description and args.text:
        spec = design.quick_spec(args.description, args.text, _nums(args.seeds, int) or [11, 22],
                                 _nums(args.guidance) or [5.0], _nums(args.pitch))
    else:
        raise UsageError("Give a spec (--spec FILE) or --description and --text.")
    res = design.run(film, brand=args.brand, spec=spec, name=args.name, out=args.out)
    human = (f"{res['previews']} previews on {res['model']} in {res['folder']}\nbest per prompt: "
             + ", ".join(f"{k}: {v[0]} ({v[1]:.3f})" for k, v in res["best"].items())
             + "\nlisten, then keep one: slate voice design --folder <folder> --save <preview id> --voice-name <name>")
    log.result(res, human)
    return 0


def cmd_tts(args) -> int:
    if args.detach:
        return _detach(args, "tts", args._parser)
    from slate.voice import tts  # noqa: PLC0415

    res = tts.voice(_film(args), only=args.only, force=args.force, approval_id=args.approval, repin=args.repin,
                    approved_by=args.approved_by, reason=args.reason, seed=args.seed,
                    dry_run=bool(cli.flag(args, "dry_run")) or args.estimate)
    if res.get("dry_run"):
        est = res["estimate"]
        human = (f"would voice {len(res['to_voice'])} line(s), {res['characters']} characters: about "
                 f"{est['amount']:g} {est['unit']} ({est['basis']})")
        log.result(res, human)
        return 0
    rows = [f"{t['shot']} {t['take']}: {t['seconds']:.2f} s, {t['format']}"
            + ("" if t["fit"]["fits"] is not False else "  <- too long for its shot") for t in res["takes"]]
    fmt = res["format"] or {}
    human = (f"voice {res['voice_id']} on {res['model']} ({res['pin']})"
             + (f"; format {fmt.get('format')} ({fmt.get('reason')})" if fmt else "") + "\n"
             + ("\n".join(rows) if rows else "nothing new to voice")
             + (f"\nkept: {', '.join(s['shot'] for s in res['skipped'])}" if res["skipped"] else "")
             + ("\nfit problems:\n  " + "\n  ".join(res["fit_problems"]) if res["fit_problems"] else "")
             + "\nnext: slate voice clean, pace, align, then slate voice check")
    log.result(res, human)
    return 0


def cmd_clean(args) -> int:
    from slate.voice import clean  # noqa: PLC0415
    from slate.voice.film import VoiceFilm  # noqa: PLC0415

    vf = VoiceFilm(_film(args))
    if args.remix:
        from slate.vendors.elevenlabs import Client  # noqa: PLC0415
        from slate.voice import profile as profile_mod  # noqa: PLC0415
        voice_id = profile_mod.require_voice(vf.profile)
        text = args.text or " ".join(ln.text for ln in vf.lines)[:1000]
        strengths, seeds = _nums(args.strengths) or [r[0] for r in clean.REMIX_RUNS], \
            _nums(args.seeds, int) or [r[1] for r in clean.REMIX_RUNS]
        runs = tuple(zip(strengths, seeds))
        res = clean.remix(vf, voice_id=voice_id, text=text, description=args.description, runs=runs,
                          out_dir=vf.dir / "voice" / "clean" / "remix", client=Client(mode=vf.mode, film=vf.dir))
        rows = [f"{Path(r['file']).name}: floor {r['gap_floor_dbfs']} dBFS, {r['floor_below_speech_db']} dB under "
                "speech" for r in res["remixes"]]
        log.result(res, "\n".join(rows) + "\n" + res["note"])
        return 0
    results = []
    for ln in vf.select(args.only):
        if args.diagnose:
            audio = vf.final_audio(ln.shot)
            if audio is None:
                results.append({"shot": ln.shot, "error": "not voiced yet"})
                continue
            results.append({"shot": ln.shot, "file": vf.rel(audio), **clean.diagnose(audio)})
        else:
            if vf.selected(ln.shot) is None:
                results.append({"shot": ln.shot, "skipped": "not voiced yet"})
                continue
            results.append(clean.clean_take(vf, ln.shot, method=args.method, chain=args.chain))
    if args.diagnose:
        human = "\n".join(f"{r['shot']}: speech {r.get('speech_dbfs')} dBFS, gap floor {r.get('gap_floor_dbfs')} "
                          f"dBFS: {r.get('verdict', r.get('error'))}" for r in results)
    else:
        human = "\n".join(f"{r['shot']}: {r.get('skipped') or ('floor ' + str(r['before']['gap_floor_dbfs']) + ' -> ' + str(r['after']['gap_floor_dbfs']) + ' dBFS')}"
                          for r in results)
    log.result({"results": results}, human or "no lines")
    return 0


def cmd_pace(args) -> int:
    from slate.voice import pace  # noqa: PLC0415
    from slate.voice.film import VoiceFilm  # noqa: PLC0415

    if args.check_tools:
        info = pace.rubberband_info()
        log.result(info, f"Rubber Band: {info.get('path') or 'missing'} {info.get('version') or ''} "
                         f"{','.join(info.get('arch') or [])} on {info['machine']}: {info['note']}")
        return 0 if info["status"] == "ok" else 1
    vf = VoiceFilm(_film(args))
    results = []
    for ln in vf.select(args.only):
        if vf.selected(ln.shot) is None:
            results.append({"shot": ln.shot, "skipped": "not voiced yet"})
            continue
        results.append(pace.pace_take(vf, ln.shot, tempo=args.tempo, pause_keep=args.pause_keep,
                                      min_pause=args.min_pause, threshold_db=args.threshold_db,
                                      no_tempo=args.no_tempo))
    human = "\n".join(r.get("skipped") and f"{r['shot']}: {r['skipped']}" or
                      f"{r['shot']}: {r['before_s']:.2f} s -> {r['after_s']:.2f} s ({r['shorter_pct']}% shorter)"
                      for r in results)
    rb = next((r["rubberband"] for r in results if r.get("rubberband")), None)
    if rb and rb.get("status") != "ok":
        human += f"\nRubber Band: {rb['note']}"
    log.result({"results": results}, human or "no lines")
    return 0


def cmd_align(args) -> int:
    if args.detach:
        return _detach(args, "align", args._parser)
    from slate.voice import align  # noqa: PLC0415

    film = _film(args)
    if args.compare:
        res = align.compare(film, only=args.only)
        rows = [f"{s['shot']}: " + (s.get("status") or f"starts differ by {s['start_ms_mean']} ms on average "
                                                        f"(max {s['start_ms_max']})") for s in res["shots"]]
        log.result(res, "\n".join(rows) + f"\n{res['note']}")
        return 0
    res = align.align(film, only=args.only, engine=args.engine, model=args.model,
                      canonical=True if args.canonical else None)
    rows = [f"{r['shot']} {r['take']}: {r['engine']}" + (f": {r['heard']}" if r.get("heard") else "")
            for r in res["aligned"]]
    human = "\n".join(rows) + (f"\nnot aligned: {', '.join(res['missing'])}" if res["missing"] else "") \
        + "\nnext: slate voice check"
    log.result(res, human)
    return 0


def cmd_captions(args) -> int:
    from slate.voice import captions  # noqa: PLC0415

    paths = captions.build(_film(args), parity=args.parity)
    data = jsonio.read(paths[0])
    human = f"{len(data['cues'])} captions: {', '.join(str(p) for p in paths)}"
    if data["problems"]:
        human += "\nproblems:\n  - " + "\n  - ".join(data["problems"])
    log.result({"files": [str(p) for p in paths], "cues": len(data["cues"]), "problems": data["problems"]}, human)
    return 1 if data["problems"] else 0


def cmd_tags(args) -> int:
    from slate.voice import tags  # noqa: PLC0415

    action, rest = args.action, list(args.rest or [])
    if action == "rules":
        info = {"banned": [label for label, _ in tags.BANNED], "why": tags.WHY_BANNED,
                "rules": ["A tagged line must strip back to the script exactly.",
                          "Neighbouring-line context carries plain words; a tag belongs to its own line.",
                          "No SSML on eleven_v4: pause with punctuation or a [pause] tag.",
                          "One emotion per phrase; a tag at the head of a line carries across it.",
                          "Tags describe something you hear, never a face, a gesture or music."]}
        human = ("Never: " + ", ".join(info["banned"]) + f" ({tags.WHY_BANNED}).\n- " + "\n- ".join(info["rules"]))
        log.result(info, human)
        return 0
    if action == "strip":
        text = " ".join(rest) if rest else args.text
        if not text:
            raise UsageError("slate voice tags strip \"<tagged line>\"")
        out = tags.strip(text)
        log.result({"stripped": out, "tags": tags.tags_in(text), "banned": [t for t, _ in tags.banned_in(text)]}, out)
        return 0
    if action == "check" and args.text is not None:
        if args.line is None:
            raise UsageError("--text needs --line (the plain script line) to check against.")
        problems = tags.check(args.text, args.line)
        log.result({"problems": problems, "ok": not problems}, "ok" if not problems else "\n".join(problems))
        return 1 if problems else 0
    from slate.voice.film import VoiceFilm  # noqa: PLC0415

    vf = VoiceFilm(_film(args))
    if action in ("optimize", "pick", "abc", "adopt"):
        return _tag_loop(args, vf)
    policy = vf.profile.get("tag_policy") or {}
    if action == "check":
        rows, bad = [], 0
        for ln in vf.select(args.only):
            tagged = vf.tagged_for(ln.shot)
            problems = tags.check(tagged, ln.text, policy=policy)
            bad += bool(problems)
            rows.append({"shot": ln.shot, "tagged": tagged, "problems": problems})
        human = "\n".join(f"{r['shot']}: " + ("ok" if not r["problems"] else "; ".join(r["problems"])) for r in rows)
        log.result({"lines": rows, "ok": not bad}, human)
        return 1 if bad else 0
    if action in ("set", "clear"):
        if not rest:
            raise UsageError(f"slate voice tags {action} <shot>" + (" \"<tagged line>\"" if action == "set" else ""))
        shot = rest[0]
        ln = vf.line(shot)
        tagged = " ".join(rest[1:]) if action == "set" else None
        if action == "set":
            if not tagged:
                raise UsageError("slate voice tags set <shot> \"<tagged line>\"")
            tags.require(tagged, ln.text, policy=policy, where=shot)
        path = vf.set_direction(shot, tagged)
        log.result({"shot": shot, "tagged": tagged, "file": str(path)},
                   f"{shot}: {tagged or '(untagged)'}\nre-voice it: slate voice tts --only {shot} --force")
        return 0
    if action == "ab":
        if not rest:
            raise UsageError("slate voice tags ab <shot> --variant \"[tag] line\" [--variant …]")
        from slate.voice import ab  # noqa: PLC0415
        res = ab.run(vf.dir, rest[0], args.variant or [], seed=args.seed, control=not args.no_control, raw=args.raw)
        human = "\n".join(f"{k}: {'passes the measurements' if v else ('measured out' if v is False else 'unchecked')}"
                          for k, v in res["summary"].items())
        human += f"\nclips: {res['folder']} (level-matched; listen before picking)"
        if res["notes"]:
            human += "\n" + "\n".join(res["notes"])
        log.result(res, human)
        return 0
    raise UsageError(f"Unknown tags action {action!r}.")


def cmd_polish(args) -> int:
    from slate.voice import polish  # noqa: PLC0415

    film = _film(args)
    if args.try_:
        res = polish.trial(film, _csv(args.try_) or [], only=args.only, sheet=args.sheet)
        rows = []
        for label, v in res["strengths"].items():
            ch = v["effect"].get("change") or {}
            rows.append(f"{label}: S peaks {polish._fmt(ch.get('sibilance_peaks_db'))}, "  # noqa: SLF001
                        f"edge peaks {polish._fmt(ch.get('edge_peaks_db'))}, warmth "  # noqa: SLF001
                        f"{polish._fmt(ch.get('warmth_db'))}, 1-5 kHz {polish._fmt(ch.get('clarity_1k_5k'))}, "  # noqa: SLF001
                        f"overall change {v['effect'].get('spectral_distance_db')} dB")
        human = "\n".join(rows) + f"\nlisten: {res['reel']} (level-matched; nothing pinned)" + \
            (f"\nsheets: {', '.join(res['sheets'])}" if res["sheets"] else "") + \
            "\nthen: slate voice polish --strength <the one you pick>"
        log.result(res, human)
        return 0
    res = polish.run(film, only=args.only, strength=args.strength, repin=args.repin, approved_by=args.approved_by,
                     reason=args.reason, force=args.force, approval_id=args.approval,
                     dry_run=bool(cli.flag(args, "dry_run")))
    if res.get("dry_run"):
        log.result(res, f"would polish {len(res['would_polish'])} take(s) at strength {res['strength']}"
                        + (" (the film's pinned polish)" if res["pinned"] else " (pinned at first use)"))
        return 0
    bad = [r for r in res["polished"] if not r["verified"]["ok"]]
    if args.report:
        human = polish.describe(res)
    else:
        human = "\n".join(f"{r['shot']} {r['take']}: {r['file']}" + ("" if r["verified"]["ok"] else
                                                                       "  <- " + r["verified"]["problems"][0])
                          for r in res["polished"]) or "nothing to polish"
    human = f"strength {res['strength']} ({res['pin']})\n" + human
    if res["skipped"]:
        human += "\nskipped: " + ", ".join(f"{s['shot']} ({s['why']})" for s in res["skipped"])
    human += f"\nnext: {res['next']}"
    log.result(res, human)
    return 1 if bad else 0


def cmd_analyze(args) -> int:
    from slate.voice import analyze  # noqa: PLC0415

    film = _maybe_film(args)
    res = analyze.run(args.takes, film_dir=film, ab_ref=args.ab, sheet=args.sheet, text=args.text,
                      tagged=args.tagged, words_file=args.words, whisper=args.whisper, stage=args.stage, out=args.out,
                      name=args.name)
    rows = []
    for t in res["takes"]:
        line = t["measures"].get("line") or {}
        w = t["measures"].get("whisper")
        row = (f"{t['label']}: F0 {line.get('f0_median_hz')} Hz, range {line.get('f0_range_st')} st, voiced "
               f"{line.get('voiced_frac')}, CPPS {line.get('cpps_db')} dB, tilt {line.get('tilt_db')} dB, "
               f"{t['measures'].get('lufs')} LUFS")
        if w:
            row += ", Whisper " + ("word for word" if w["wer"] == 0 and not w["repeats"] else f"heard \"{w['heard']}\"")
        vs = t.get("vs_reference")
        if vs and vs.get("breath"):
            b = vs["breath"]
            row += f"; vs A: breath index {b['index']:+.2f}" + (" (whisper-like)" if b["whisper"] else
                                                                 " (breathier)" if b["breathier"] else "")
        rows.append(row)
    human = "\n".join(rows) + f"\nanalysis: {res['file']}" + (f"\nsheet: {res['sheet']}" if res["sheet"] else "")
    log.result(res, human)
    return 0


def _tag_loop(args, vf) -> int:
    from slate.voice import tagloop  # noqa: PLC0415

    action, rest = args.action, list(args.rest or [])
    name = args.round or "main"
    if action == "optimize":
        res = tagloop.optimize(vf.dir, name=name, only=_csv(args.only), estimate_only=args.estimate,
                               generate_only=args.generate_only, measure_only=args.measure_only,
                               include_held=args.include_held)
        est = res["estimate"]["estimate"]
        human = (f"round {name}: {res['new']} new take(s) of {res['planned']} planned, "
                 f"{res['estimate']['characters']} characters, about {est['amount']:g} {est['unit']} ({est['basis']})")
        if res.get("voiced") is not None:
            human += f"\nvoiced {len(res['voiced'])}"
        if res.get("measured") is not None:
            human += f"\nmeasured {res['measured']} take(s): {res['metrics']}\nnext: {res['next']}"
        log.result(res, human)
        return 0
    if action == "pick":
        res = tagloop.pick_round(vf, name)
        human = "\n".join(f"{shot}: " + (f"pick {p['pick']}" + (f", runner-up {p['runner_up']}" if p["runner_up"] else "")
                                         if p["pick"] else f"no pick ({p['why']})") for shot, p in res["picks"].items())
        log.result(res, human + f"\nranking: {vf.rel(tagloop.round_dir(vf, name) / 'ranking.md')}")
        return 0
    if action == "abc":
        if not rest:
            raise UsageError("slate voice tags abc <shot> [--round R] [--n 3]")
        res = tagloop.abc(vf, name, rest[0], n=args.n, also=_csv(args.also))
        human = "\n".join(f"{k}: {v['take']}" + (f" ({v.get('tagged')})" if v.get("tagged") else "")
                          for k, v in res["takes"].items())
        log.result(res, human + f"\nlisten: {res['reel']} (level-matched; pick by ear, then adopt it)")
        return 0
    if len(rest) < 2:
        raise UsageError("slate voice tags adopt <shot> <take key> [--round R]")
    res = tagloop.adopt(vf, name, rest[0], rest[1], force=args.force)
    log.result(res, f"{res['shot']}: {res['from']} is now {res['take']} ({res['tagged']})\nnext: {res['next']}")
    return 0


def cmd_check(args) -> int:
    from slate import gates as gates_mod  # noqa: PLC0415
    from slate.voice import gates  # noqa: PLC0415

    results = gates.run(_film(args), args.gate or None)
    log.result({"gates": [r.to_dict() for r in results], "summary": gates_mod.summarize(results)},
               gates_mod.format_table(results))
    return gates_mod.exit_code(results)


def cmd_approve(args) -> int:
    from slate.voice.film import TAKE_STAGES, VoiceFilm, approve, lock_asset, now  # noqa: PLC0415

    vf = VoiceFilm(_film(args))
    shot = args.shot
    vf.line(shot)
    if args.take:
        vf.select_take(shot, args.take)
    sel = vf.selected(shot)
    if not sel:
        raise MissingPrerequisite(f"{shot} has no take to approve.", hint=f"slate voice tts --only {shot}")
    take, info = sel
    files = [info[s] for s in TAKE_STAGES if info.get(s)]
    final = vf.rel(vf.final_audio(shot))
    rec = approve(vf.dir, what="take", path=final, approved_by=args.by, note=args.note)
    for rel in files:
        lock_asset(vf.dir, rel, reason=f"approved take ({args.by})")
    data = vf.manifest()
    data["shots"][shot]["approved"] = {"take": take, "by": args.by, "at": now(), "note": args.note,
                                       "sha256": {rel: jsonio.sha256(vf.dir / rel) for rel in files}}
    vf.save_manifest(data)
    log.result({"shot": shot, "take": take, "approval": rec, "locked": files},
               f"{shot} {take} approved by {args.by} and locked ({', '.join(files)})")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# Registration


def register(sub) -> None:
    stages = cli.add_group(sub, "voice", help="the voice stages: cast, design, tags, tts, clean, pace, polish, "
                                              "align, analyze, captions, check",
                           description=__doc__)

    def add(name, handler, help, **kw):
        p = cli.add_command(stages, name, handler, help, **kw)
        p.set_defaults(_parser=p)
        return p

    p = add("profile", cmd_profile, "the voice profile and the cloning-policy verdict")
    p.add_argument("--brand", help="a brand's profile instead of the film's (name or path)")
    p.add_argument("--mode", choices=["supervised", "express", "draft", "autonomous"], help="check for this mode")

    p = add("account", cmd_account, "the ElevenLabs plan: tier, characters left, best voice format (free)")
    p.add_argument("--oneiro", action="store_true", help="Oneiro's capped key (autonomous films)")
    p.add_argument("--snapshot", action="store_true", help="record the balance in the ledger (live only)")

    p = add("cast", cmd_cast, "a casting shootout: voices read the same lines, measured, ranked, with a reel")
    p.add_argument("--voices", help="voice ids, comma-separated (default: the profile's candidates)")
    p.add_argument("--shots", help="shots whose lines to read (default: the first two lines)")
    p.add_argument("--text", help="a line to read instead of the storyboard's")
    p.add_argument("--brand", help="cast for a brand (no film)")
    p.add_argument("--name", help="the shootout's name (a folder under voice/cast/)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--model", help="TTS model (default: the registry's)")
    p.add_argument("--out", help="write the shootout here instead")
    p.add_argument("--no-reel", action="store_true", help="skip the listening reel")
    p.add_argument("--detach", action="store_true", help="run as a job in the queue")

    p = add("design", cmd_design, "Voice Design previews, measured and ranked; --save keeps one; --check tests "
                                  "saved voices on the production model")
    p.add_argument("--spec", help="a design spec (preview_text, prompts, passes)")
    p.add_argument("--description", help="one voice description (20-1,000 characters)")
    p.add_argument("--text", help="preview text (100-1,000 characters, in the voice's own words)")
    p.add_argument("--seeds", help="comma-separated seeds (default 11,22)")
    p.add_argument("--guidance", help="comma-separated guidance scales (default 5)")
    p.add_argument("--pitch", help="the pitch window the voice aims for, 'lo,hi' in Hz")
    p.add_argument("--brand", help="design for a brand (no film)")
    p.add_argument("--name", help="the design's name (a folder under voice/design/)")
    p.add_argument("--out", help="write the previews here instead")
    p.add_argument("--folder", help="an existing design folder (for --save and --check)")
    p.add_argument("--save", metavar="PREVIEW", help="keep this preview (preview id or generated voice id)")
    p.add_argument("--voice-name", help="the name for the saved voice")
    p.add_argument("--check", action="store_true", help="render saved voices on the production model, measured")
    p.add_argument("--models", help="models for --check (default: the registry's TTS model)")
    p.add_argument("--detach", action="store_true", help="run as a job in the queue")

    p = add("tags", cmd_tags, "direction with audio tags: strip, check, set, clear, ab, rules; the tag loop: "
                              "optimize, pick, abc, adopt")
    p.add_argument("action", choices=["strip", "check", "set", "clear", "ab", "rules", "optimize", "pick", "abc",
                                      "adopt"])
    p.add_argument("rest", nargs="*", help="shot and tagged line, or text to strip; abc: shot; adopt: shot and take key")
    p.add_argument("--round", help="the tag round (a folder under voice/tags/, default main)")
    p.add_argument("--estimate", action="store_true", help="optimize: the cost only; voice nothing")
    p.add_argument("--generate-only", action="store_true", help="optimize: voice the new takes, then stop")
    p.add_argument("--measure-only", action="store_true", help="optimize: measure what's voiced; voice nothing")
    p.add_argument("--include-held", action="store_true", help="optimize: also voice lines marked hold")
    p.add_argument("--n", type=int, default=3, help="abc: how many passing candidates to hear (default 3)")
    p.add_argument("--also", help="abc: near misses to hear too, labelled with what they missed (comma-separated)")
    p.add_argument("--force", action="store_true", help="adopt: replace an approved take (the person's go)")
    p.add_argument("--only", help="shots to check (comma-separated)")
    p.add_argument("--text", help="a tagged line to check or strip without a film")
    p.add_argument("--line", help="the plain script line to check --text against")
    p.add_argument("--variant", action="append", help="a tagged variant for ab (repeat for more)")
    p.add_argument("--seed", type=int, help="one seed for every variant (default: the line's)")
    p.add_argument("--no-control", action="store_true", help="ab without the untagged control take")
    p.add_argument("--raw", action="store_true", help="ab on raw takes (skip the clean-up and pacing)")

    p = add("tts", cmd_tts, "voice every line: one take per line with neighbour context, pinned, fitted")
    p.add_argument("--only", help="shots to voice (comma-separated)")
    p.add_argument("--force", action="store_true", help="voice a new take even where one exists")
    p.add_argument("--approval", help="the person's spend approval id (needed to re-voice an approved take)")
    p.add_argument("--repin", action="store_true", help="switch the film's pinned voice to the profile's")
    p.add_argument("--approved-by", help="who approved the re-pin")
    p.add_argument("--reason", help="why the voice is re-pinned (the A/B it follows)")
    p.add_argument("--seed", type=int, help="a new seed for this take (default: the line's pinned seed)")
    p.add_argument("--estimate", action="store_true", help="the cost only; voice nothing")
    p.add_argument("--detach", action="store_true", help="run as a job in the queue")

    p = add("clean", cmd_clean, "diagnose and clean the takes (default: the ffmpeg chain tracking the floor)")
    p.add_argument("--only", help="shots (comma-separated)")
    p.add_argument("--method", choices=["dsp", "isolator"], default="dsp",
                   help="dsp (afftdn + agate, the default) or ElevenLabs' Voice Isolator (paid)")
    p.add_argument("--chain", help="an ffmpeg filter chain instead of the brand's or the default")
    p.add_argument("--diagnose", action="store_true", help="measure the floor against the speech; change nothing")
    p.add_argument("--remix", action="store_true", help="Voice Remix previews of the voice (paid)")
    p.add_argument("--text", help="remix preview text (100-1,000 characters)")
    p.add_argument("--description", help="remix description (default: keep the voice, change only the recording)")
    p.add_argument("--strengths", help="remix prompt strengths (default 0.25,0.35,0.5)")
    p.add_argument("--seeds", help="remix seeds (default 11,22,33)")

    p = add("pace", cmd_pace, "shorten pauses, then quicken gently with Rubber Band (R3, formants kept)")
    p.add_argument("--only", help="shots (comma-separated)")
    p.add_argument("--tempo", type=float, help="tempo (default: the profile's, 1.06)")
    p.add_argument("--pause-keep", type=float, help="keep this share of each pause (default 0.8)")
    p.add_argument("--min-pause", type=float, help="shorten pauses longer than this, never below it (0.25 s)")
    p.add_argument("--threshold-db", type=float, help="silence threshold (default -45 dBFS)")
    p.add_argument("--no-tempo", action="store_true", help="shorten pauses only")
    p.add_argument("--check-tools", action="store_true", help="report Rubber Band's path, version and architecture")

    p = add("polish", cmd_polish, "the studio vocal chain on every take: length and loudness kept, verified by "
                                  "measurement, pinned per film")
    p.add_argument("--strength", help="A, B (the voice's named strengths) or a number from 0 (untouched) to 1; "
                                      "default: the film's pinned polish, else the voice's default")
    p.add_argument("--report", action="store_true", help="show each take's measurements, before and after")
    p.add_argument("--try", dest="try_", metavar="STRENGTHS", help="trial renders at these strengths (comma-separated) "
                                                                   "for listening; nothing pinned or registered")
    p.add_argument("--sheet", action="store_true", help="with --try: an A/B spectrogram sheet per line")
    p.add_argument("--only", help="shots (comma-separated)")
    p.add_argument("--repin", action="store_true", help="switch the film's pinned polish to --strength")
    p.add_argument("--approved-by", help="who approved the re-pin")
    p.add_argument("--reason", help="why the polish is re-pinned (the trial it follows)")
    p.add_argument("--force", action="store_true", help="polish again over an approved, locked polished take")
    p.add_argument("--approval", help="the person's approval id (needed with --force on approved work)")

    p = add("analyze", cmd_analyze, "measured inflection and A/B spectrogram sheets for takes, tags or polish")
    p.add_argument("takes", nargs="+", help="WAV files, or shots in the film (S01, or S01/t02)")
    p.add_argument("--ab", metavar="REFERENCE", help="compare every take with this one (it's drawn first, as A)")
    p.add_argument("--sheet", action="store_true", help="draw the A/B spectrogram sheet (PNG)")
    p.add_argument("--text", help="the script line (for files outside a film)")
    p.add_argument("--tagged", help="what was sent, tags included (measures the tag's scope)")
    p.add_argument("--words", help="a Whisper JSON with the takes' words (files outside a film)")
    p.add_argument("--whisper", action="store_true", help="transcribe takes without known words (local Whisper)")
    p.add_argument("--stage", choices=["raw", "clean", "paced", "polished"],
                   help="which stage of a shot's take (default: the take before the polish)")
    p.add_argument("--out", help="write the analysis here")
    p.add_argument("--name", help="the analysis folder's name (under voice/analysis/)")

    p = add("align", cmd_align, "word timings: local Whisper (default) or ElevenLabs Forced Alignment to evaluate")
    p.add_argument("--only", help="shots (comma-separated)")
    p.add_argument("--engine", choices=["whisper", "elevenlabs"], default="whisper")
    p.add_argument("--model", help="Whisper model (default: turbo)")
    p.add_argument("--canonical", action="store_true", help="make forced alignment the canonical timing")
    p.add_argument("--compare", action="store_true", help="compare Whisper with forced alignment, word by word")
    p.add_argument("--detach", action="store_true", help="run as a job in the queue")

    p = add("captions", cmd_captions, "SRT, WebVTT and timing/captions.json, timed to the words")
    p.add_argument("--parity", action="store_true", help="the films' rules exactly (no reading-speed hold)")

    p = add("check", cmd_check, "the voice gates: policy, direction, takes, words, tails, fit, inflection, polish")
    p.add_argument("--gate", action="append", help="run only this gate (repeat for more)")

    p = add("approve", cmd_approve, "record the person's approval of a take and lock it by hash")
    p.add_argument("shot")
    p.add_argument("--take", help="the take to approve (default: the selected one)")
    p.add_argument("--by", required=True, help="who approved it")
    p.add_argument("--note")


__all__ = ["register"]
