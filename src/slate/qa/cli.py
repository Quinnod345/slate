"""`slate qa`: run the film's gates, triage what isn't passing, compare two pictures.

    slate qa                          every gate, in film order (writes qa/gates.json)
    slate qa loudness sync            just these gates
    slate qa --quick                  skip the gates that read the whole picture (ocr, readtime, glitch, psnr)
    slate qa --list                   the gates, where each comes from, and (in a film) its latest status
    slate qa --detach                 queue the run as a job and return its id at once (`slate job status <id>`)
    slate qa triage                   what isn't passing, most severe first, with the next step for each
    slate qa triage --accept GATE:ITEM --by NAME --reason TEXT
                                      a person accepts one REVIEW item (bound to the exact files it was judged on)
    slate qa psnr A B [--min 40]      PSNR between two images, or frame for frame between two videos
    slate qa unpark --by NAME         a person releases an autonomous film that a REVIEW verdict parked

    slate qa motion [--act N] [--render PATH] [--no-pixels] [--only CHECK]
    slate qa access [--only CHECK] [--captions PATH] [--sheet PATH] [--platform NAME]
                                      the motion and access gates with their own options (a narrowed run reports
                                      what it finds but can't pass the gate)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import cli, gates, log
from slate.errors import UsageError


def _gate_options():
    """slate.qa.motion's hook for the motion and access gates' own flags, or None when that module isn't here.

    `slate qa` is the one command that runs every gate, the motion and access gates included, so their flags live on
    it (added once, whichever of the two gates a run names) instead of on commands of their own."""
    try:
        from slate.qa import motion  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - another module's import error mustn't take `slate qa` down
        return None
    needed = ("add_qa_options", "qa_options", "qa_option_argv")
    return motion if all(callable(getattr(motion, n, None)) for n in needed) else None

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
    p.add_argument("--accept", metavar="GATE:ITEM", help="triage: accept one REVIEW item (needs --by and --reason)")
    p.add_argument("--by", metavar="NAME", help="the person accepting or releasing")
    p.add_argument("--reason", metavar="TEXT", help="why the item is acceptable")
    p.add_argument("--note", metavar="TEXT", help="unpark: a note for the record")
    hook = _gate_options()
    if hook is not None:
        hook.add_qa_options(p)


def _gate_flags(args: argparse.Namespace) -> list[str]:
    """The motion and access flags given on this command line, as words (empty when there are none)."""
    hook = _gate_options()
    return hook.qa_option_argv(args) if hook is not None else []


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
    if compare:
        return _psnr(targets[1:], args.min_db)
    if targets and targets[0] == "triage":
        if len(targets) > 1:
            raise UsageError("`slate qa triage` takes no gate names.", hint="slate qa triage [--accept GATE:ITEM]")
        return _triage(args)
    if targets and targets[0] == "unpark":
        if len(targets) > 1:
            raise UsageError("`slate qa unpark` takes no gate names.", hint="slate qa unpark --by NAME [--note TEXT]")
        return _unpark(args)
    quick = bool(getattr(args, "quick", False))
    hook = _gate_options()
    if getattr(args, "detach", False):
        if hook is not None:
            with hook.qa_options(args, targets):  # refuse flags that don't fit before anything is queued
                pass
        return _detach(targets, quick=quick, extra=_absolute(_gate_flags(args)))
    if hook is None:
        return _run(targets, quick=quick)
    with hook.qa_options(args, targets):
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
def _triage(args: argparse.Namespace) -> int:
    from slate import jsonio  # noqa: PLC0415
    from slate.qa import accept, triage  # noqa: PLC0415

    film_dir = _film_dir()
    if getattr(args, "accept", None):
        mode = (jsonio.read(film_dir / "film.json", default={}) or {}).get("mode", "supervised")
        record = accept.accept(film_dir, args.accept, by=args.by, reason=args.reason, mode=mode)
        log.result({"accepted": record, "next": f"slate qa {record['gate']}"},
                   f"accepted {record['gate']}:{record['key']} ({record['what']}) by {record['by']}: {record['reason']}\n"
                   f"next: slate qa {record['gate']}   (the gate re-runs and counts the acceptance)")
        return 0
    rep = triage.report(film_dir)
    log.result(rep, triage.format_report(rep))
    return 0


# ---------------------------------------------------------------------------------------------- unpark
def _unpark(args: argparse.Namespace) -> int:
    from slate import jsonio  # noqa: PLC0415
    from slate.qa import park  # noqa: PLC0415

    film_dir = _film_dir()
    mode = (jsonio.read(film_dir / "film.json", default={}) or {}).get("mode", "supervised")
    record = park.unpark(film_dir, by=args.by, note=args.note, mode=mode)
    log.result({"unparked": record}, f"released by {record['released_by']}; the film can go on.")
    return 0


# ---------------------------------------------------------------------------------------------- psnr A B
def _psnr(files: list[str], min_db: float) -> int:
    from slate import gates as g  # noqa: PLC0415
    from slate.qa import measure, media  # noqa: PLC0415

    if len(files) != 2:
        raise UsageError("`slate qa psnr` compares exactly two files: slate qa psnr A B [--min 40].")
    a, b = (Path(f).expanduser() for f in files)
    for p in (a, b):
        if not p.is_file():
            raise UsageError(f"No such file: {p}")
    if a.suffix.lower() in IMAGE_EXT and b.suffix.lower() in IMAGE_EXT:
        db = measure.psnr(a, b)
        ok = db >= min_db
        import numpy  # noqa: PLC0415

        obj = {"a": str(a), "b": str(b), "kind": "image", "psnr_db": measure.finite(db), "threshold_db": min_db,
               "passed": ok, "engine": g.engine("slate psnr (numpy)", numpy.__version__)}
        log.result(obj, f"{'PASS' if ok else 'FAIL'} {measure.finite(db)} dB (threshold {min_db:g} dB)"
                   + ("  identical" if db == float("inf") else ""))
        return 0 if ok else 1
    ia, ib = media.video_info(a), media.video_info(b)
    values = measure.video_psnr(a, b)
    s = measure.summarize(values, threshold=min_db)
    problems = []
    if ia.get("frames") != ib.get("frames"):
        problems.append(f"frame counts differ: {ia.get('frames')} vs {ib.get('frames')}")
    if s["frames"] != min(ia.get("frames") or 0, ib.get("frames") or 0):
        problems.append(f"only {s['frames']} frames could be compared")
    ok = not problems and not s.get("below_count") and s["frames"] > 0
    obj = {"a": str(a), "b": str(b), "kind": "video", "summary": s, "problems": problems, "threshold_db": min_db,
           "passed": ok, "engine": media.engine("psnr", pix_fmt="yuv422p10le")}
    human = (f"{'PASS' if ok else 'FAIL'} {s['frames']} frames: mean {s['mean_db']} dB, worst {s['min_db']} dB"
             + (f" at frame {s['worst'][0]['frame']}" if s["worst"] else "")
             + f"; {s.get('below_count', 0)} under {min_db:g} dB" + ("".join(f"\n  {p}" for p in problems)))
    log.result(obj, human)
    return 0 if ok else 1
