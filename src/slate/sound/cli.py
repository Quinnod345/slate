"""The sound commands: score, mix, verify, sfx and sound.

    slate score plan                      plan the score: one Eleven Music composition plan per act
    slate score compose [PART ...]        compose every part (two seeds each); spends, through the ledger
    slate score assemble [--pick P=sN]    place, join and repair the takes into sound/score/score.wav
    slate score qa [FILE ...]             silence and noise in the takes (or given files)
    slate mix                             the delivery mix and its four stems
    slate verify [GATE ...]               the sound gates, recorded in qa/gates.json
    slate sfx [status|spot|render|run]    designed effects, gated on the approved sound proofs
    slate sound instruments               the sampled instruments for tonal roles: installed, where, what to download
    slate sound loudness FILE ...         BS.1770-4 loudness, true peak and LRA (with ffmpeg's meter beside it)

Heavy imports (numpy, scipy) happen inside the handlers, so `slate --help` stays quick.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import log
from slate.cli import add_command, add_group, dry_stop, film
from slate.errors import UsageError


# ---------------------------------------------------------------------------------------------------------------
# score


def _score_plan(args: argparse.Namespace) -> int:
    from slate.sound.score import plan as splan  # noqa: PLC0415

    dry = bool(getattr(args, "dry_run", False))
    p = splan.build(film(args), seeds=args.seeds, write=not dry)
    lines = [f"{'Would plan' if dry else 'Planned'} {len(p['parts'])} part(s) on {p['model_id']} as {p['plan_schema']} "
             f"(pre-roll {p['pre_roll_s']:g} s, every part under {p['max_part_s']:g} s):"]
    for part in p["parts"]:
        lines.append(f"  {part['id']:8s} {part['join']:6s} landing {part['landing_s']:8.3f} s  hand-over "
                     f"{part['hand_over_s']:8.3f} s  {part['seconds']:5.1f} s composed, "
                     f"{len(splan.plan_blocks(part['composition_plan']))} {p['plan_schema']}")
    if p["planned_stops"]:
        lines.append("  planned stops: " + ", ".join(f"{s['from']}-{s['to']} s" for s in p["planned_stops"]))
    if p["licence"]["needs_enterprise"]:
        lines.append("  BROADCAST: " + p["licence"]["note"])
    lines.append("Next: `slate score compose`." if not dry else "Nothing written (--dry-run).")
    log.result({"plan": p, "written": not dry}, "\n".join(lines))
    return 0


def _score_compose(args: argparse.Namespace) -> int:
    from slate.sound.score import compose  # noqa: PLC0415

    res = compose.compose(film(args), args.parts or None, args.seeds, force=args.force,
                          dry_run=bool(getattr(args, "dry_run", False)), output_format=args.format)
    est = res.get("estimate") or {}
    lines = [f"{len(res['to_compose'])} take(s) to compose on {res['model_id']}"
             + (f", about {est.get('amount', 0):g} {est.get('unit', '')}" if est else "")
             + (" (replay: nothing is spent)" if res["replay"] else ""),
             f"kept {len(res['kept'])} take(s) already on disk"]
    for rec in res.get("composed", []):
        lines.append(f"  {Path(rec['file']).name}: qa score {rec['qa']['score']}"
                     + (f", problems {rec['qa']['problems']}" if rec["qa"]["problems"] else ""))
    if res.get("dry_run"):
        lines.append("Nothing composed (--dry-run).")
    elif res.get("composed") is not None:
        lines.append("Next: `slate score assemble`.")
    log.result(res, "\n".join(lines))
    return 0


def _score_assemble(args: argparse.Namespace) -> int:
    from slate.sound.score import assemble  # noqa: PLC0415

    picks = {}
    for item in args.pick or []:
        if "=" not in item:
            raise UsageError(f"--pick takes PART=sSEED (for example act2=s21), not {item!r}.")
        k, v = item.split("=", 1)
        picks[k.strip()] = v.strip()
    if dry_stop(args, "place, join" + ("" if args.no_repairs else " and repair") + " the score's takes"
                          + (f" (picks: {', '.join(f'{k}={v}' for k, v in picks.items())})" if picks else "")
                          + " and write sound/score/score.wav and score.json"):
        return 0
    rec = assemble.assemble(film(args), picks, out=args.out, repairs=not args.no_repairs,
                            partial=bool(getattr(args, "partial", False)))
    log.result(rec, assemble.describe(rec) + ("\nNext: `slate mix`." if rec["pass"] else ""))
    return 0 if rec["pass"] else 1


def _score_qa(args: argparse.Namespace) -> int:
    from slate.sound.film import load  # noqa: PLC0415
    from slate.sound.score import qa  # noqa: PLC0415
    from slate.sound.score.compose import takes_for  # noqa: PLC0415

    files = [Path(f) for f in args.files or []]
    if not files:
        f = load(film(args))
        from slate.sound.score.plan import load_plan  # noqa: PLC0415

        for part in load_plan(f)["parts"]:
            files += takes_for(f, part["id"])
        score = f.path("sound/score/score.wav")
        if score.exists():
            files.append(score)
        if not files:
            raise UsageError("No takes or score to check yet.", hint="slate score compose")
    results = [qa.check_file(p) for p in files]
    bad = [r for r in results if r["problems"]]
    human = "\n".join(f"{Path(r['file']).name}: " + ("clean" if not r["problems"] else
                      ", ".join(f"{p['kind']} {p['from']}-{p['to']} s" for p in r["problems"])) for r in results)
    log.result({"results": results, "clean": not bad}, human)
    return 0 if not bad else 1


# ---------------------------------------------------------------------------------------------------------------
# mix and verify


def _mix(args: argparse.Namespace) -> int:
    from slate import jsonio  # noqa: PLC0415
    from slate.sound import mix  # noqa: PLC0415
    from slate.sound.film import load  # noqa: PLC0415

    if getattr(args, "listen", None):
        if dry_stop(args, f"render a listening file of the {int(args.listen)} hardest-ridden accent(s), before "
                              "their ride and now"):
            return 0
        rec = mix.listening_file(film(args), n=int(args.listen))
        log.result(rec, f"{rec['file']}: {len(rec['excerpts'])} excerpt(s) of the {rec['rides_heard']} hardest-ridden "
                        "accent(s), each as mixed before its ride and now, over " + str(rec["picture"]))
        return 0
    if getattr(args, "dry_run", False):
        p = mix.plan(load(film(args)), score=args.score, no_score=args.no_score)
        log.result({"dry_run": True, **p}, f"Would mix {p['film']}: {p['voice_cues']} voice cue(s), score "
                   f"{p['score'] or 'none'}, stems {', '.join(p['stems']) or 'none'}; {p['seconds']} s at "
                   f"{p['sample_rate']} Hz to {p['targets']['lufs']:g} LUFS, {p['targets']['true_peak']:g} dBTP.")
        return 0
    path = mix.run(film(args), score=args.score, no_score=args.no_score, calibrate=args.calibrate, under=args.under,
                   duck=args.duck)
    rep = jsonio.read(path.with_name("final_mix.json"))
    lv = rep.get("levels_vs_voice_lu") or {}
    lines = [f"{path}: {rep['final_lufs']:.1f} LUFS, {rep['final_true_peak_dbtp']:.1f} dBTP, gain {rep['gain_db']:+.1f} dB"
             + (", voice limited" if rep.get("dialogue_limited") else "")
             + (f", linked limiter {rep['linked_limiter']['max_reduction_db']} dB" if rep.get("linked_limiter") else ""),
             "stems sum to the mix within %.1e" % rep["stems_sum_max_abs"]]
    if lv:
        lines.append("against the voice: " + ", ".join(f"{k} {v:+.1f} LU" for k, v in lv.items() if v is not None))
    mc = rep.get("music_check")
    if mc:
        lines.append("music gap check: " + ("PASS" if mc["pass"] else f"FAIL {mc['failures'][:3]}")
                     + f" (quietest {mc['quietest_outside_stops_dbfs']} dBFS)")
    if rep.get("calibration"):
        lines.append("calibrated trims: " + ", ".join(f"{k} {v:+.1f} dB" for k, v in rep["calibration"]["stem_trim_db"].items())
                     + " (keep them in the brand's sound.palette.levels.stem_trim_db)")
    lines.append("Next: `slate verify`.")
    log.result({"mix": str(path), "report": rep}, "\n".join(lines))
    return 0


def _verify(args: argparse.Namespace) -> int:
    from slate import gates  # noqa: PLC0415
    from slate.sound import verify  # noqa: PLC0415

    if dry_stop(args, f"run the sound gates ({', '.join(args.gates) if args.gates else 'all of them'}) on the mix, "
                          "here (not queued)" + ("" if args.no_write else ", and record them in qa/gates.json and "
                                                                          "sound/mix/verify.json")):
        return 0
    results = verify.run(film(args), args.gates or None, write=not args.no_write)
    log.result({"summary": gates.summarize(results), "gates": [r.to_dict() for r in results]},
               gates.format_table(results))
    return gates.exit_code(results)


# ---------------------------------------------------------------------------------------------------------------
# sfx (gated) and sound


def _sfx(args: argparse.Namespace) -> int:
    from slate.sound import effects  # noqa: PLC0415

    stage = args.stage or "run"
    if stage == "status":
        st = effects.status()
        human = ("Proof 3 is approved: " + str((st["record"] or {}).get("approved_by")) if st["approved"] else
                 "The effects engine waits for a person's approval of proof 3: " + "; ".join(st["problems"]) + ".")
        log.result(st, human)
        return 0
    from slate.sound.effects import engine  # noqa: PLC0415

    folder = film(args)
    if bool(getattr(args, "dry_run", False)):
        planned = engine.plan(folder, stage=stage)
        log.result({"dry_run": True, "stage": stage, "seed": args.seed,
                    "queued": bool(args.detach), "plan": planned},
                   f"Would {stage} effects for {folder.name}" + (" as a job" if args.detach else "")
                   + ". Nothing written or queued (--dry-run).")
        return 0
    effects.require_proof3()
    if args.detach:
        from slate.jobs import client  # noqa: PLC0415

        command = ["sfx", stage, "--film", str(folder), "--seed", str(args.seed), "--json"]
        job = client.detach_current("sfx", ("render",), film=str(folder), argv=command)
        log.result({"stage": stage, "job": job, "queued": True, "resources": ["render"]},
                   f"Queued effects {stage} as {job}: slate job status {job}")
        return 0
    result = {"stage": stage, "film": str(folder)}
    if stage in ("spot", "run"):
        result["cue_sheet"] = str(engine.spot(folder, seed=args.seed))
        result["seed"] = args.seed
    if stage in ("render", "run"):
        result["render"] = engine.render(folder)
    log.result(result, (f"Spotted effects: {result['cue_sheet']}" if stage == "spot" else
                        f"Rendered effects for {folder.name}. Next: slate mix."))
    return 0


def _instruments(args: argparse.Namespace) -> int:
    from slate.sound import instruments  # noqa: PLC0415

    palette = None
    try:
        from slate.sound.film import load  # noqa: PLC0415

        palette = load(film(args)).palette  # the film's brand palette when there's a film here or --film
    except Exception:  # noqa: BLE001 - without a film, the starting palette's roles are reported
        palette = None
    rep = instruments.report(palette)
    lines = []
    for r in rep["roles"]:
        mark = "ok     " if r["status"] == "ok" else r["status"].upper().ljust(7)
        lines.append(f"{mark} {r['role']:12s} {r['summary']}")
    for k, lib in rep["libraries"].items():
        lines.append(f"{'found  ' if lib['installed'] else 'absent '} {lib['name']}: {lib['path']}")
    if not rep["ok"]:
        lines.append("Missing: " + instruments.DOWNLOAD)
    lines.append(rep["redistribution"])
    log.result(rep, "\n".join(lines))
    return 0 if rep["ok"] else 3


def _loudness(args: argparse.Namespace) -> int:
    from slate.sound import measure  # noqa: PLC0415

    out = []
    for f in args.files:
        r = measure.loudness(f)
        r["cross_check"] = measure.cross_check(f, r) if not args.no_ffmpeg else None
        out.append(r)
    human = []
    for r in out:
        line = (f"{Path(r['file']).name}: {r['integrated_lufs']:.2f} LUFS integrated, {r['true_peak_dbtp']:.2f} dBTP, "
                f"LRA {r['lra_lu']:.1f} LU, momentary max {r['momentary_max_lufs']:.1f}, short-term max "
                f"{r['short_term_max_lufs']:.1f}")
        cc = r.get("cross_check") or {}
        if cc.get("available"):
            line += (f"\n  ffmpeg ebur128: {cc['ffmpeg']['integrated_lufs']} LUFS, {cc['ffmpeg']['true_peak_dbtp']} dBTP, "
                     f"LRA {cc['ffmpeg']['lra_lu']} ({'agrees' if cc['agree'] else 'DISAGREES'})")
        human.append(line)
    log.result({"results": out}, "\n".join(human))
    return 0


def register(sub) -> None:
    score = add_group(sub, "score", help="the composed score: plan, compose, assemble, qa",
                      description="Eleven Music on its best model, composed per act with 3 s pre-rolls and parts under "
                                  "90 s, assembled by measurement with equal-power joins and freeze repairs, and gap-"
                                  "checked. Start with `slate score plan`.")
    p = add_command(score, "plan", _score_plan, help="plan the score: one composition plan per act",
                    description="Writes sound/score/plan.json and sound/score/plans/<part>.json from timing/shots.json, "
                                "the brand's sound palette and sound/score/directions.json (optional, hand-edited).")
    p.add_argument("--seeds", type=int, nargs="+", default=None, help="seeds to compose per part (default: 7 21)")
    p = add_command(score, "compose", _score_compose, help="compose the plan's parts (spends; replayed in tests)",
                    description="Calls Eleven Music for every part and seed that has no take yet, always passing the "
                                "model id. The batch is estimated and budget-checked before the first call; "
                                "--dry-run shows the estimate and changes nothing.")
    p.add_argument("parts", nargs="*", help="only these parts (default: all)")
    p.add_argument("--seeds", type=int, nargs="+", default=None, help="seeds (default: the plan's)")
    p.add_argument("--force", action="store_true", help="recompose takes already on disk (never locked ones)")
    p.add_argument("--format", default=None, help="Eleven Music output format (default: the best your ElevenLabs plan allows)")
    p = add_command(score, "assemble", _score_assemble, help="place, join and repair the takes into the score",
                    description="Writes sound/score/score.wav and score.json. Exits 1 when the gap check, the take "
                                "QA or a landing fails (the score is still written, for listening).")
    p.add_argument("--pick", action="append", metavar="PART=sSEED", help="force a take, e.g. --pick act2=s21")
    p.add_argument("--partial", action="store_true",
                   help="assemble only the parts that have takes, silent elsewhere (a proof; needs --out)")
    p.add_argument("--out", default=None, help="write the score elsewhere (for comparisons)")
    p.add_argument("--no-repairs", dest="no_repairs", action="store_true",
                   help="no freeze repairs or late-entry carries (to hear what the takes do on their own)")
    p = add_command(score, "qa", _score_qa, help="silence and noise in takes or a score",
                    description="Flags silence (under -45 dBFS) or noise lasting 1.5 s or more, away from the first "
                                "and last second. Exits 1 when anything is flagged.")
    p.add_argument("files", nargs="*", help="audio files (default: every take and the assembled score)")

    p = add_command(sub, "mix", _mix, help="the delivery mix: -14 LUFS, <= -1 dBTP, four stems that sum to it",
                    description="Voice from timing/vo_cues.json, the score from sound/score/score.wav and any effect "
                                "stems in sound/stems/, mixed with fixed gains to the film's targets. Works with voice "
                                "and score alone.")
    p.add_argument("--score", default=None, help="mix this score instead of sound/score/score.wav")
    p.add_argument("--no-score", dest="no_score", action="store_true", help="mix without a score")
    p.add_argument("--listen", nargs="?", const=9, type=int, metavar="N",
                   help="don't mix: write the listening file of the N (default 9) hardest-ridden accents, each as mixed "
                        "before its ride and now, from sound/mix/rides.json")
    p.add_argument("--calibrate", action="store_true",
                   help="trim each effect stem to its palette level against the voice, and report the trims")
    p.add_argument("--under", type=float, default=None, help="music LU under the voice (default: the palette's, 18.5)")
    p.add_argument("--duck", type=float, default=None, help="music ducking under speech, dB (default: the palette's, 6)")

    p = add_command(sub, "verify", _verify, help="the sound gates on the mix (recorded in qa/gates.json)",
                    description="loudness, stems sum, gap, voice sync, cue sync, repeats, glides, drone, levels, "
                                "reference, clarity, music licence, and the balance gates (slate.sound.balance) once "
                                "they're installed. Exit 0 only when every gate passes.")
    p.add_argument("gates", nargs="*", help="only these gates (e.g. loudness gap)")
    p.add_argument("--no-write", dest="no_write", action="store_true", help="don't record the results")

    p = add_command(sub, "sfx", _sfx, help="spot or render designed effects after sound-proof approval",
                    description="Designed, event-tied effects from the picture's beats. It waits for a person's "
                                "approval of proof 3 (`slate approve proof3`); `slate sfx status` shows the gate.")
    p.add_argument("stage", nargs="?", choices=("status", "spot", "render", "run"), default=None,
                   help="status (the gate), spot (beats to cues), render (cues to stems), run (both; default)")
    p.add_argument("--seed", type=int, default=0, help="stable variation seed for spot or run (default: 0)")
    p.add_argument("--detach", action="store_true", help="queue spot, render or run in the render slot")

    snd = add_group(sub, "sound", help="sound tools: instruments, loudness, balance")
    p = add_command(snd, "instruments", _instruments, help="the sampled instruments for tonal roles, and what's missing",
                    description="Reports the Apple sampled instruments the palette's tonal roles play on (installed "
                                "with GarageBand and Logic), where they are, and what to download from GarageBand's "
                                "Sound Library when one is missing. Exit 3 when any is missing.")
    p = add_command(snd, "loudness", _loudness, help="BS.1770-4 loudness, true peak and LRA of audio files")
    p.add_argument("files", nargs="+", help="audio files")
    p.add_argument("--no-ffmpeg", dest="no_ffmpeg", action="store_true", help="skip ffmpeg's cross-check")
    _register_extra(snd)


# `slate sound <stage>` stages served by other sound modules once they're installed (import-guarded: a module that
# isn't there adds nothing, and one that fails to import or register is reported, never fatal to `slate sound`).
EXTRA_STAGES = ("slate.sound.balance",)


def _register_extra(group) -> None:
    import importlib  # noqa: PLC0415

    for module_name in EXTRA_STAGES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                log.warn(f"{module_name} failed to import ({exc}); its `slate sound` stage is missing")
            continue
        except Exception as exc:  # noqa: BLE001
            log.warn(f"{module_name} failed to import ({type(exc).__name__}: {exc}); its `slate sound` stage is missing")
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            continue
        try:
            register(group)
        except Exception as exc:  # noqa: BLE001 - a clash or a bug there mustn't lose instruments and loudness
            log.warn(f"{module_name} couldn't add its `slate sound` stage: {type(exc).__name__}: {exc}")
