"""`slate qa`: run the film's gates, triage what isn't passing, compare two pictures.

    slate qa                          every gate, in film order (writes qa/gates.json)
    slate qa loudness sync            just these gates
    slate qa --quick                  skip the gates that read the whole picture (ocr, readtime, glitch, psnr,
                                      stability)
    slate qa --list                   the gates, where each comes from, and (in a film) its latest status
    slate qa --detach                 queue the run as a job and return its id at once (`slate job status <id>`)
    slate --dry-run qa [...]          which gates would run, on which renders, and whether it would queue; runs nothing
                                      and writes nothing (psnr, triage --accept, unpark and stability plan the same way)
    slate qa triage                   what isn't passing, most severe first, with the next step for each
    slate qa triage --accept GATE:ITEM --by NAME --reason TEXT
                                      a person accepts one REVIEW item (bound to the exact files it was judged on)
    slate qa psnr A B [--min 40]      PSNR between two images, or frame for frame between two videos
    slate qa unpark --by NAME         a person releases an autonomous film that a REVIEW verdict parked

    slate qa motion [--act N] [--render PATH] [--no-pixels] [--only CHECK]
    slate qa access [--only CHECK] [--captions PATH] [--sheet PATH] [--platform NAME]
                                      the motion and access gates with their own options (a narrowed run reports
                                      what it finds but can't pass the gate)
    slate qa stability [run|baseline|reference|strip|selftest|controls|conformance] [--act N] …
                                      the render stability gate and its tools (slate.qa.stability; `slate qa
                                      stability --help` lists them)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import cli, gates, log
from slate.errors import UsageError


HOOK_NAMES = ("add_qa_options", "qa_options", "qa_option_argv")
_HOOKS_ADDED: list = []  # the hook modules whose flags are on the `slate qa` parser built last


def _hook_modules() -> list:
    """The gate modules whose own flags ride on `slate qa`: slate.qa.motion (for the motion and access gates), and any
    other merged gate module (slate.qa.stability, when it's installed) that offers the same three hooks.

    `slate qa` is the one command that runs every gate, these included, so their flags live on it (added once,
    whichever of their gates a run names) instead of on commands of their own. A module that isn't installed, or
    fails to import, simply adds nothing: it must never take `slate qa` down."""
    import importlib  # noqa: PLC0415

    from slate import qa  # noqa: PLC0415

    out = []
    for name in ("slate.qa.motion", *[m for m in qa.EXTERNAL if m not in ("slate.qa.motion", "slate.qa.access")]):
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001
            continue
        if all(callable(getattr(module, n, None)) for n in HOOK_NAMES):
            out.append(module)
    return out


def _gate_options():
    """slate.qa.motion's hook for the motion and access gates' own flags, or None when that module isn't here."""
    hooks = [h for h in _hook_modules() if h.__name__ == "slate.qa.motion"]
    return hooks[0] if hooks else None


def _hooks() -> list:
    """The hook modules whose flags were added to this `slate qa` parser."""
    return list(_HOOKS_ADDED)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SPECIAL = ("triage", "psnr", "unpark")

EPILOG = """Gate states: PASS, FAIL, REVIEW (a person judges), UNCHECKED (couldn't run: the evidence says what to
do), STALE (an input changed since). Only PASS counts as passed. On autonomous films a REVIEW parks the film for a
person. Exit codes: 0 every gate passed (or the command did what it says), 1 a gate isn't passing or a refusal,
2 usage, 3 missing prerequisite, 5 busy."""


def register(sub) -> None:
    p = cli.add_command(sub, "qa", handle, help="run the film's quality gates; triage; compare pictures",
                        description=__doc__, epilog=EPILOG)
    p.add_argument("targets", nargs="*", metavar="GATE",
                   help="gate names (default: all), or: triage | psnr A B | unpark")
    p.add_argument("--list", dest="list_gates", action="store_true", help="list the gates and exit")
    p.add_argument("--quick", action="store_true", help="skip the gates that read the whole picture")
    p.add_argument("--detach", action="store_true",
                   help="run the gates as a queued job (it holds the render slot) and return the job id at once")
    p.add_argument("--min", dest="min_db", type=float, default=40.0, metavar="DB",
                   help="psnr A B: the lowest acceptable PSNR in dB (default 40)")
    p.add_argument("--a-frames", dest="a_frames", metavar="FIRST-LAST",
                   help="psnr A B: compare only these frames of A (inclusive, from 0)")
    p.add_argument("--b-frames", dest="b_frames", metavar="FIRST-LAST",
                   help="psnr A B: compare only these frames of B, e.g. one shot's frames in a whole act")
    p.add_argument("--accept", metavar="GATE:ITEM", help="triage: accept one REVIEW item (needs --by and --reason)")
    p.add_argument("--by", metavar="NAME", help="the person accepting or releasing")
    p.add_argument("--reason", metavar="TEXT", help="why the item is acceptable")
    p.add_argument("--note", metavar="TEXT", help="unpark: a note for the record")
    _HOOKS_ADDED.clear()
    for hook in _hook_modules():
        try:
            hook.add_qa_options(p)
        except Exception as exc:  # noqa: BLE001 - a flag clash or a bug in one gate module mustn't lose `slate qa`
            from slate import qa  # noqa: PLC0415

            qa.EXTERNAL_STATUS[hook.__name__] = f"its `slate qa` flags couldn't be added: {type(exc).__name__}: {exc}"
            continue
        _HOOKS_ADDED.append(hook)


def _gate_flags(args: argparse.Namespace) -> list[str]:
    """The gate modules' own flags given on this command line (motion, access, stability), as words."""
    words: list[str] = []
    for hook in _hooks():
        words += list(hook.qa_option_argv(args) or [])
    return words


def _options(args: argparse.Namespace, targets: list[str]):
    """Every hook module's qa_options for this run, entered together (each checks and applies its own flags)."""
    import contextlib  # noqa: PLC0415

    stack = contextlib.ExitStack()
    for hook in _hooks():
        stack.enter_context(hook.qa_options(args, targets))
    return stack


PATH_FLAGS = ("--render", "--captions", "--sheet")


def _absolute(words: list[str]) -> list[str]:
    """Flag words with file paths made absolute: a queued job runs from another folder, so `--render take3.mov` has
    to mean the file it meant here."""
    out = list(words)
    for i, w in enumerate(out[:-1]):
        if w in PATH_FLAGS:
            p = Path(out[i + 1]).expanduser()
            if not p.is_absolute() and p.exists():
                out[i + 1] = str(p.resolve())
    return out


def handle(args: argparse.Namespace) -> int:
    targets = list(getattr(args, "targets", []) or [])
    # `psnr` is a gate too: `slate qa psnr` runs the gate, `slate qa psnr A B` compares two files
    compare = bool(targets) and targets[0] == "psnr" and len(targets) >= 2
    special = "--list" if getattr(args, "list_gates", False) else (
        targets[0] if targets and (targets[0] in ("triage", "unpark") or compare) else None)
    if special and _gate_flags(args):
        flags = " ".join(w for w in _gate_flags(args) if w.startswith("--"))
        raise UsageError(f"{flags} {'goes' if len(flags.split()) == 1 else 'go'} with `slate qa motion` or "
                         f"`slate qa access`, not with `slate qa {special.lstrip('-')}`.",
                         hint="slate qa motion --act 2  ·  slate qa access --only captions")
    if getattr(args, "list_gates", False):
        return _list()
    dry = bool(cli.flag(args, "dry_run", False))
    if compare:
        return _psnr(targets[1:], args.min_db, a_frames=getattr(args, "a_frames", None),
                     b_frames=getattr(args, "b_frames", None), dry_run=dry)
    if targets and targets[0] == "triage":
        if len(targets) > 1:
            raise UsageError("`slate qa triage` takes no gate names.", hint="slate qa triage [--accept GATE:ITEM]")
        return _triage(args, dry_run=dry)
    if targets and targets[0] == "unpark":
        if len(targets) > 1:
            raise UsageError("`slate qa unpark` takes no gate names.", hint="slate qa unpark --by NAME [--note TEXT]")
        return _unpark(args, dry_run=dry)
    quick = bool(getattr(args, "quick", False))
    if dry:
        return _plan(args, targets, quick=quick)
    if getattr(args, "detach", False):
        with _options(args, targets):  # refuse flags that don't fit before anything is queued
            pass
        return _detach(targets, quick=quick, extra=_absolute(_gate_flags(args)))
    with _options(args, targets):
        return _run(targets, quick=quick)


def _detach(names: list[str], *, quick: bool, extra: list[str] | None = None) -> int:
    """Queue `slate qa` as a job: the OCR sweep, glitch scan and PSNR of a long film outlast an agent's session."""
    import sys  # noqa: PLC0415

    from slate import qa  # noqa: PLC0415
    from slate.errors import MissingPrerequisite  # noqa: PLC0415

    film_dir = _film_dir()
    qa.resolve(names or None, quick=quick)  # refuse unknown gates now, not in the queue
    try:
        from slate.jobs import client  # noqa: PLC0415
    except ImportError as exc:
        raise MissingPrerequisite("The job queue isn't installed, so `slate qa` can't run detached.",
                                  hint="Run `slate qa` without --detach.") from exc
    argv = [sys.executable, "-m", "slate", "qa", "--film", str(film_dir), *names] + (["--quick"] if quick else [])
    argv += list(extra or [])
    job_id = client.submit("qa", argv, film=str(film_dir), resources=("render",), resumable=True,
                           note="slate qa " + " ".join([*names, *(extra or [])]) if names or extra else
                           "slate qa (every gate)")
    log.result({"job": job_id, "film_dir": str(film_dir), "argv": argv, "next": f"slate job status {job_id}"},
               f"queued as job {job_id}\nnext: slate job status {job_id}   (then slate qa triage)")
    return 0


# ---------------------------------------------------------------------------------------------- dry run
def _whole_picture(name: str) -> bool:
    """Whether a gate reads the whole picture (decodes every frame of a render or the master): qa.SLOW, and a merged
    gate module's own SLOW list (the motion gate's pixel cross-check, the access gate's sheets)."""
    import importlib  # noqa: PLC0415

    from slate import qa  # noqa: PLC0415

    if name in qa.SLOW:
        return True
    source = str(qa.SOURCES.get(name) or "")
    try:
        module = importlib.import_module(source.split(":")[0]) if source else None
    except Exception:  # noqa: BLE001 - a module that won't import reads nothing
        return False
    return name.rsplit(".", 1)[-1] in tuple(getattr(module, "SLOW", ()) or ())


def _plan(args: argparse.Namespace, targets: list[str], *, quick: bool) -> int:
    """`slate --dry-run qa …`: which gates would run, on what, and whether the run would be queued. Nothing runs (no
    gate, no analysis, no cache) and nothing is written; gate names and flags that don't fit are refused, as the run
    itself would refuse them."""
    from slate import qa  # noqa: PLC0415
    from slate.qa.film import context  # noqa: PLC0415

    film_dir = _film_dir()
    if not (film_dir / "film.json").is_file():
        raise UsageError(f"{film_dir} isn't a film folder (no film.json).",
                         hint="slate new <id> --brand <brand> --format <format> creates one.")
    with _options(args, targets):  # each gate module checks its own flags, and nothing runs inside
        pass
    names = qa.resolve(targets or None, quick=quick)
    whole = [n for n in names if _whole_picture(n)]
    ctx = context(film_dir)
    renders = {str(a): ctx.rel(e["path"]) for a, e in ctx.current_renders().items()}
    masters = [ctx.rel(p) for p in ctx.masters()]
    mix = ctx.mix_path()
    detach = bool(getattr(args, "detach", False))
    flags = _gate_flags(args)
    skipped = [n for n in qa.GATES if n in qa.SLOW and n not in names] if quick and not targets else []
    where = ("as a queued job that holds the render slot (`slate job status <id>` follows it)" if detach else
             "here, in this process (not queued; --detach would queue it)")
    on = {"renders": renders, "masters": masters, "mix": ctx.rel(mix) if mix else None}
    command = ["slate", "qa", "--film", str(film_dir), *targets, *(["--quick"] if quick else []), *_absolute(flags),
               *(["--detach"] if detach else [])]
    obj = {"film": film_dir.name, "film_dir": str(film_dir), "dry_run": True, "would_run": names,
           "whole_picture": whole, "not_run_quick": skipped, "on": on, "options": flags, "queued": detach,
           "where": where, "command": command,
           "would_write": ["qa/gates.json", "qa/history.jsonl"] + (["the whole-picture gates' caches in qa/"]
                                                                   if whole else [])}
    rendered = "; ".join(f"act {a} {p}" for a, p in renders.items()) or "no registered render yet"
    human = [f"dry run: slate qa would run {len(names)} gate{'s' if len(names) != 1 else ''} on {film_dir.name}, "
             f"{where}", f"  gates: {', '.join(names)}"]
    if whole:
        human.append(f"  reading the whole picture (every frame of a render or the master; cached in qa/): "
                     f"{', '.join(whole)}")
    if skipped:
        human.append(f"  not run (--quick): {', '.join(skipped)}")
    human.append(f"  on: {rendered}; master {', '.join(masters) or 'none yet'}; mix {on['mix'] or 'none yet'}")
    if flags:
        human.append(f"  options: {' '.join(flags)}")
    human.append("nothing ran, nothing written")
    log.result(obj, "\n".join(human))
    return 0


# ---------------------------------------------------------------------------------------------- run
def _run(names: list[str], *, quick: bool) -> int:
    from slate import qa  # noqa: PLC0415
    from slate.qa import park  # noqa: PLC0415

    film_dir = _film_dir()
    results = qa.run(film_dir, names or None, quick=quick)
    summary = gates.summarize(results)
    parked = park.status(film_dir)
    ran = {r.gate for r in results}
    skipped = [n for n in qa.GATES if n in qa.SLOW and n not in ran] if quick and not names else []
    obj = {"film": film_dir.name, "film_dir": str(film_dir), "status": summary["status"], "counts": summary["counts"],
           "not_passed": summary["not_passed"], "results": [r.to_dict() for r in results],
           "report": str(film_dir / "qa" / "gates.json"), "parked": parked, "skipped": skipped}
    human = [gates.format_table(results), f"report: {film_dir / 'qa' / 'gates.json'}"]
    if skipped:
        human.append(f"not run (--quick): {', '.join(skipped)}. The film hasn't passed them; run `slate qa` "
                     "without --quick for those.")
    if parked:
        human.append(f"PARKED: {parked.get('why')} Release: {parked.get('release')}")
    if summary["status"] != "PASS":
        human.append("next: slate qa triage   (what isn't passing, with the next step for each)")
    log.result(obj, "\n".join(human))
    return gates.exit_code(results)


def _film_dir() -> Path:
    return cli.film(argparse.Namespace())


# ---------------------------------------------------------------------------------------------- list
def _list() -> int:
    from slate import qa  # noqa: PLC0415

    qa.load_external()
    latest = {}
    try:
        film_dir = _film_dir()
        latest = {k: v.status for k, v in gates.load_report(film_dir).items()}
    except UsageError:
        film_dir = None
    rows = [{"gate": n, "source": qa.SOURCES.get(n), "slow": n in qa.SLOW, "description": qa.describe(n),
             **({"latest": latest.get(n, "not run")} if film_dir else {})} for n in qa.GATES]
    width = max(len(r["gate"]) for r in rows)
    human = []
    for r in rows:
        status = f"{r['latest']:<9} " if film_dir else ""
        human.append(f"{status}{r['gate']:<{width}}  {r['description']}" + ("  [slow]" if r["slow"] else ""))
    human.append("")
    human += [f"{m}: {s}" for m, s in qa.EXTERNAL_STATUS.items()]
    log.result({"gates": rows, "external": qa.EXTERNAL_STATUS, "film_dir": str(film_dir) if film_dir else None},
               "\n".join(human))
    return 0


# ---------------------------------------------------------------------------------------------- triage
def _triage(args: argparse.Namespace, *, dry_run: bool = False) -> int:
    from slate import jsonio  # noqa: PLC0415
    from slate.qa import accept, triage  # noqa: PLC0415

    film_dir = _film_dir()
    if getattr(args, "accept", None):
        mode = (jsonio.read(film_dir / "film.json", default={}) or {}).get("mode", "supervised")
        record = accept.accept(film_dir, args.accept, by=args.by, reason=args.reason, mode=mode, dry_run=dry_run)
        if dry_run:
            log.result({"accepted": record, "dry_run": True},
                       f"dry run: would accept {record['gate']}:{record['key']} ({record['what']}) by {record['by']}: "
                       f"{record['reason']}; nothing recorded")
            return 0
        log.result({"accepted": record, "next": f"slate qa {record['gate']}"},
                   f"accepted {record['gate']}:{record['key']} ({record['what']}) by {record['by']}: {record['reason']}\n"
                   f"next: slate qa {record['gate']}   (the gate re-runs and counts the acceptance)")
        return 0
    rep = triage.report(film_dir)
    log.result(rep, triage.format_report(rep))
    return 0


# ---------------------------------------------------------------------------------------------- unpark
def _unpark(args: argparse.Namespace, *, dry_run: bool = False) -> int:
    from slate import jsonio  # noqa: PLC0415
    from slate.qa import park  # noqa: PLC0415

    film_dir = _film_dir()
    mode = (jsonio.read(film_dir / "film.json", default={}) or {}).get("mode", "supervised")
    record = park.unpark(film_dir, by=args.by, note=args.note, mode=mode, dry_run=dry_run)
    if dry_run:
        log.result({"unparked": record, "dry_run": True},
                   f"dry run: would release the film, by {record['released_by']}; nothing changed")
        return 0
    log.result({"unparked": record}, f"released by {record['released_by']}; the film can go on.")
    return 0


# ---------------------------------------------------------------------------------------------- psnr A B
def _frame_range(text: str | None, flag: str) -> tuple[int, int] | None:
    """'644-1212' (or '644:1212'): a first and last frame, inclusive, from 0."""
    if text is None:
        return None
    parts = str(text).replace(":", "-").split("-")
    if len(parts) != 2 or not all(x.strip().isdigit() for x in parts):
        raise UsageError(f"{flag} takes a first and last frame, like 644-1212; {text!r} isn't one.")
    first, last = (int(x) for x in parts)
    if last < first:
        raise UsageError(f"{flag} {text}: the last frame comes before the first.")
    return first, last


def _psnr(files: list[str], min_db: float, *, a_frames: str | None = None, b_frames: str | None = None,
          dry_run: bool = False) -> int:
    from slate import gates as g  # noqa: PLC0415
    from slate.qa import measure, media  # noqa: PLC0415

    if len(files) != 2:
        raise UsageError("`slate qa psnr` compares exactly two files: slate qa psnr A B [--min 40].")
    a, b = (Path(f).expanduser() for f in files)
    for p in (a, b):
        if not p.is_file():
            raise UsageError(f"No such file: {p}")
    ra, rb = _frame_range(a_frames, "--a-frames"), _frame_range(b_frames, "--b-frames")
    images = a.suffix.lower() in IMAGE_EXT and b.suffix.lower() in IMAGE_EXT
    if images and (ra or rb):
        raise UsageError("--a-frames and --b-frames are for videos; two images are one frame each.")
    if dry_run:  # say what would be compared; decode nothing
        what = "two images" if images else "frame for frame" + "".join(
            f", {name} {r[0]}-{r[1]}" for name, r in (("A frames", ra), ("B frames", rb)) if r)
        log.result({"a": str(a), "b": str(b), "kind": "image" if images else "video", "threshold_db": min_db,
                    "dry_run": True, **({"a_frames": list(ra)} if ra else {}), **({"b_frames": list(rb)} if rb else {})},
                   f"dry run: would compare {a} with {b} ({what}) against {min_db:g} dB, here (not queued); "
                   "nothing compared")
        return 0
    if images:
        db = measure.psnr(a, b)
        ok = db >= min_db
        import numpy  # noqa: PLC0415

        obj = {"a": str(a), "b": str(b), "kind": "image", "psnr_db": measure.finite(db), "threshold_db": min_db,
               "passed": ok, "engine": g.engine("slate psnr (numpy)", numpy.__version__)}
        log.result(obj, f"{'PASS' if ok else 'FAIL'} {measure.finite(db)} dB (threshold {min_db:g} dB)"
                   + ("  identical" if db == float("inf") else ""))
        return 0 if ok else 1
    ia, ib = media.video_info(a), media.video_info(b)
    for name, rng, info in (("--a-frames", ra, ia), ("--b-frames", rb, ib)):
        if rng and info.get("frames") and rng[1] >= int(info["frames"]):
            raise UsageError(f"{name} {rng[0]}-{rng[1]} runs past the file's last frame ({int(info['frames']) - 1}).")
    values = measure.video_psnr(a, b, a_frames=ra, b_frames=rb)
    s = measure.summarize(values, threshold=min_db, first_frame=ra[0] if ra else 0)
    problems = []
    na = (ra[1] - ra[0] + 1) if ra else ia.get("frames")
    nb = (rb[1] - rb[0] + 1) if rb else ib.get("frames")
    if na != nb:
        problems.append(f"frame counts differ: {na} vs {nb}" + (" (the ranges asked for)" if ra or rb else ""))
    if s["frames"] != min(na or 0, nb or 0):
        problems.append(f"only {s['frames']} frames could be compared")
    ok = not problems and not s.get("below_count") and s["frames"] > 0
    obj = {"a": str(a), "b": str(b), "kind": "video", "summary": s, "problems": problems, "threshold_db": min_db,
           "passed": ok, "engine": media.engine("psnr", pix_fmt="yuv422p10le"),
           **({"a_frames": list(ra)} if ra else {}), **({"b_frames": list(rb)} if rb else {})}
    human = (f"{'PASS' if ok else 'FAIL'} {s['frames']} frames: mean {s['mean_db']} dB, worst {s['min_db']} dB"
             + (f" at frame {s['worst'][0]['frame']}" if s["worst"] else "")
             + f"; {s.get('below_count', 0)} under {min_db:g} dB" + ("".join(f"\n  {p}" for p in problems)))
    log.result(obj, human)
    return 0 if ok else 1
