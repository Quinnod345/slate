"""`slate render` and its stages: list, splice, clean, stills."""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import cli, log
from slate.errors import MissingPrerequisite, Refused, UsageError

ENGINES = ("remotion", "web", "ae")

DESCRIPTION = """\
Render the film's acts with true motion blur (16 sub-frames averaged in 16-bit, 4 in draft mode), as ProRes 422 HQ.

  slate render                          every act, one at a time
  slate render --act 3                  one act; registering it locks the film's timeline
  slate render --act 3 --from 120 --to 260
                                        re-render frames 120-260 of act 3 (its own frame numbers, inclusive) and
                                        splice them into the registered render, proven by frame hashes and PSNR
  slate render --from 900 --to 960      the same with film frames; Slate finds the acts
  slate render --act 3 --out review.mov a range or act to a file of your choosing (not registered)
  slate render --resume                 pick up renders that didn't finish, keeping every good chunk
  slate render --engine ae --act 2 --go an After Effects act, only on a person's go (never for autonomous films)

Renders are chunked and resumable: a chunk with the right frame count and hash is kept, a short or missing one is
rendered again. Long renders always go through the job queue and return a job id at once (follow it with
`slate job status <id>`); --detach sends any render there. Every render reserves its disk first and removes its bundle
when it ends. Renders always run on this Mac.

Stages: list (what's rendered, moved or unfinished), splice (a patch you rendered yourself), clean (leaked bundles,
caches and chunks), stills (review frames).
"""


def register(sub) -> None:
    p = cli.add_command(sub, "render", _cmd_render, help="render acts, patches and stills (chunked, resumable, proven)",
                        description=DESCRIPTION)
    p.add_argument("--act", type=int, metavar="N", help="one act (default: every act)")
    p.add_argument("--from", dest="frame_from", type=int, metavar="F",
                   help="first frame: the act's own with --act, otherwise a film frame (inclusive)")
    p.add_argument("--to", dest="frame_to", type=int, metavar="T", help="last frame (inclusive)")
    p.add_argument("--engine", choices=ENGINES, default=None,
                   help="remotion (default), web, or ae; a patch defaults to its act's engine")
    p.add_argument("--samples", type=int, metavar="N", help="motion-blur sub-frames per frame (default 16; draft 4)")
    p.add_argument("--sub", type=int, metavar="N", help="the same as --samples (render_mb's name for it)")
    p.add_argument("--draft", action="store_true", help="draft samples (4): for animatics, never delivered as final")
    p.add_argument("--out", metavar="PATH", help="write this act or range to PATH instead (not registered)")
    p.add_argument("--detach", action="store_true", help="send it to the job queue and return the job id at once")
    p.add_argument("--wait", action="store_true", help="with the queue: wait for the job and report how it ended")
    p.add_argument("--resume", action="store_true", help="finish renders that stopped, with their original options")
    p.add_argument("--fresh", action="store_true", help="throw away this render's chunks and start over")
    p.add_argument("--go", action="store_true", help="a person's go, which After Effects renders need")
    p.add_argument("--in-queue", dest="in_queue", action="store_true", help=argparse.SUPPRESS)
    stages = p.add_subparsers(dest="render_stage", metavar="<stage>")

    s = cli.add_command(stages, "list", _cmd_list, help="what's rendered, moved, missing or unfinished")
    s.add_argument("--verify", action="store_true", help="also hash every registered file (slow for long acts)")

    s = cli.add_command(stages, "splice", _cmd_splice, help="splice a patch into an act render, with proof",
                        description="Replace frames F-T (the render's own frame numbers, inclusive) with a patch you "
                                    "rendered, by stream copy. Refused, with nothing replaced, unless the frame count, "
                                    "the frame hashes at both joins and the edge PSNR scan all hold.")
    s.add_argument("patch", help="the re-rendered range (ProRes, F-T inclusive)")
    s.add_argument("--act", type=int, metavar="N", help="splice into this act's registered render")
    s.add_argument("--base", metavar="PATH", help="or into this file")
    s.add_argument("--from", dest="frame_from", type=int, metavar="F", required=True)
    s.add_argument("--to", dest="frame_to", type=int, metavar="T", required=True)

    s = cli.add_command(stages, "clean", _cmd_clean, help="remove leaked bundles, caches and finished chunks")
    s.add_argument("--older-than", dest="older_than", type=float, default=180.0, metavar="MIN",
                   help="leaked bundles untouched this many minutes (default 180)")
    s.add_argument("--all", dest="everything", action="store_true",
                   help="also unfinished chunks (what --resume would use) and leftover patches")
    s.add_argument("--force", action="store_true", help="sweep even while a render holds the render slot")

    s = cli.add_command(stages, "stills", _cmd_stills, help="review stills from one bundle")
    s.add_argument("--frames", required=True, metavar="F,F,…", help="frame numbers, comma-separated")
    s.add_argument("--act", type=int, metavar="N", help="frames are this act's own (default: film frames)")
    s.add_argument("--scale", type=float, default=0.5, help="size relative to the film (default 0.5)")
    s.add_argument("--samples", type=int, default=1, help="motion-blur sub-frames (default 1)")
    s.add_argument("--out", metavar="DIR", help="where the PNGs go (default renders/stills)")
    s.add_argument("--engine", choices=ENGINES, default="remotion")


# ---- helpers ---------------------------------------------------------------------------------------------------

def _samples(args: argparse.Namespace) -> int | None:
    a, b = getattr(args, "samples", None), getattr(args, "sub", None)
    if a is not None and b is not None and a != b:
        raise UsageError(f"--samples {a} and --sub {b} disagree; they're the same setting, so give one.")
    return a if a is not None else b


def _frames(args: argparse.Namespace) -> tuple[int, int] | None:
    f, t = getattr(args, "frame_from", None), getattr(args, "frame_to", None)
    if (f is None) != (t is None):
        raise UsageError("--from and --to go together (both inclusive).")
    return None if f is None else (int(f), int(t))


def _mb(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{max(n, 0) / 1e3:.0f} KB"


def _describe_plan(p) -> str:
    lines = [f"{p.engine}, {p.samples if p.samples else 'AE'} samples{' (draft)' if p.draft else ''}, "
             f"{p.width}x{p.height} at {p.fps_num}/{p.fps_den} fps, chunks of {p.chunk_frames} frames"]
    for t in p.targets:
        n = len(p.chunks[f"act{t.act.index}:{t.first}-{t.last}"])
        what = {"act": "whole act", "patch": "patch, spliced into the registered render", "range": "range"}[t.kind]
        lines.append(f"  act {t.act.index} ({t.act.shots}) frames {t.first}–{t.last}: {what}, {n} chunk(s) "
                     f"-> {t.out}")
    where = "in the queue" if p.runs == "queue" else "here"
    lines.append(f"Disk: about {_mb(p.disk_gb * 1e9)} reserved. Runs {where}: {p.why}.")
    return "\n".join(lines)


def _describe_result(result: dict) -> str:
    lines = []
    for r in result.get("renders", []):
        chunks = r["chunks"]
        kept = f", {len(chunks['kept'])} kept from before" if chunks["kept"] else ""
        head = f"act {r['act']} ({r['shots']})"
        if r["kind"] == "patch":
            lines.append(f"{head} frames {r['first']}–{r['last']}: re-rendered in {chunks['total']} chunk(s){kept} "
                         f"and spliced into {r['file']}. {r['splice']['gate']['summary']}.")
        else:
            lines.append(f"{head}: {r['frame_count']} frames joined from {chunks['total']} chunk(s){kept}, every join "
                         f"proven ({r['proof']['method']}) -> {r['file']} (sha256 {r['sha256'][:12]}…).")
            if r.get("registered"):
                lock = r.get("timeline_locked") or {}
                if lock.get("warning"):
                    lines.append(f"  registered in renders/registry.json; slate.state couldn't record it: "
                                 f"{lock['warning']}")
                else:
                    locked = ", ".join(lock.get("files") or []) or "no timing files yet"
                    lines.append(f"  registered in renders/registry.json; timeline locked ({locked}).")
    cleaned = result.get("cleaned") or {}
    if cleaned.get("bundles"):
        lines.append(f"Swept {cleaned['bundles']} leaked bundle(s), {_mb(cleaned['freed_bytes'])}.")
    if lines:
        lines.append("Next: `slate qa` checks the renders (glitch scan, act joins, motion).")
    return "\n".join(lines) or "Nothing to render."


def _check_before_queueing(film_dir: Path, film: dict, p, *, go: bool) -> None:
    """Refuse now, not an hour later in the queue: the engine's rules, what's installed, and patch bases."""
    from slate.render import deps, engines, runner  # noqa: PLC0415

    eng = engines.get(p.engine, film_dir, film, go=go)
    eng.rules()
    eng.check_installed()
    if any(t.kind == "act" for t in p.targets):
        deps.require_state_lock()
    for t in p.targets:
        if t.kind == "patch":
            runner.check_patch(film_dir, p, t)


def _queue(film_dir: Path, film: dict, p, argv: list[str], *, wait: bool) -> int:
    from slate.render import deps, runner  # noqa: PLC0415

    job_id = runner.submit(film_dir, film, p, argv)
    out = {"ok": True, "ran": "queue", "job_id": job_id, "why": p.why, "plan": p.to_dict()}
    human = (f"Queued as job {job_id} ({p.why}).\n{_describe_plan(p)}\n"
             f"Follow it with `slate job status {job_id}`.")
    if not wait:
        log.result(out, human=human)
        return 0
    log.info(human)
    status = deps.job_wait(job_id)
    out["status"] = status
    state = str(status.get("state", "")) if isinstance(status, dict) else ""
    out["ok"] = state == "done"
    log.result(out, human=f"Job {job_id} ended: {state or status}." + (
        "" if out["ok"] else f" `slate job log {job_id}` shows why."))
    return 0 if out["ok"] else 1


# ---- commands --------------------------------------------------------------------------------------------------

def _cmd_render(args: argparse.Namespace) -> int:
    from slate.render import chunks, runner  # noqa: PLC0415

    film_dir = cli.film(args)
    samples, frames = _samples(args), _frames(args)
    dry = bool(cli.flag(args, "dry_run", False))
    if args.in_queue:
        result = runner.run(film_dir, act=args.act, frames=frames, engine=args.engine, samples=samples, out=args.out,
                            draft=args.draft, go=args.go, fresh=args.fresh, resume=args.resume, in_queue=True)
        log.result(result, human=_describe_result(result))
        return 0
    if args.resume:
        todo = [pl for pl in chunks.unfinished(film_dir) if pl.get("options")]
        if args.act is not None:
            todo = [pl for pl in todo if int(pl["options"]["act"]) == args.act]
        if not todo:
            raise Refused("Nothing to resume: every render in this film finished.",
                          hint="slate render list shows what's registered; slate render --act N starts a new one.")
        prepared = []
        for pl in todo:
            o = pl["options"]
            frames_o = tuple(o["frames"]) if o.get("frames") else None
            prepared.append(runner.prepare(film_dir, act=o["act"], frames=frames_o, engine=o["engine"],
                                           samples=o.get("samples"), out=o.get("out"), draft=bool(o.get("draft")),
                                           detach=args.detach))
        if dry:
            log.result({"ok": True, "dry_run": True, "resume": [p.to_dict() for _f, p in prepared]},
                       human="\n".join(_describe_plan(p) for _f, p in prepared))
            return 0
        if any(p.runs == "queue" for _f, p in prepared):
            for f, each in prepared:
                _check_before_queueing(film_dir, f, each, go=args.go)
            film, p = prepared[0]
            p.disk_gb = sum(each.disk_gb for _f, each in prepared)
            argv = runner.resume_argv(film_dir, act=args.act, go=args.go)
            return _queue(film_dir, film, p, argv, wait=args.wait)
        result = runner.resume_run(film_dir, act=args.act, go=args.go)
        log.result(result, human=_describe_result(result))
        return 0

    film, p = runner.prepare(film_dir, act=args.act, frames=frames, engine=args.engine, samples=samples,
                             out=args.out, draft=args.draft, detach=args.detach)
    if dry:
        log.result({"ok": True, "dry_run": True, "plan": p.to_dict()}, human=_describe_plan(p))
        return 0
    if p.runs == "queue":
        _check_before_queueing(film_dir, film, p, go=args.go)
        argv = runner.job_argv(film_dir, p, act=args.act, frames=frames, out=args.out, go=args.go, fresh=args.fresh,
                               resume=False)
        return _queue(film_dir, film, p, argv, wait=args.wait)
    result = runner.execute(film_dir, film, p, go=args.go, fresh=args.fresh)
    log.result(result, human=_describe_result(result))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from slate.render import chunks, deps, registry  # noqa: PLC0415
    from slate.render.plan import acts_from_shots  # noqa: PLC0415

    film_dir = cli.film(args)
    note = None
    try:
        acts = acts_from_shots(deps.load_shots(film_dir))
    except (MissingPrerequisite, UsageError) as exc:
        acts, note = [], exc.message
    rows = registry.status(film_dir, acts, verify=args.verify)
    unfinished = [{"act": pl["options"]["act"] if pl.get("options") else None, "status": pl.get("status"),
                   "target": pl.get("target"), "updated_at": pl.get("updated_at"), "dir": pl["dir"]}
                  for pl in chunks.unfinished(film_dir)]
    lines = []
    for r in rows:
        e = r.get("entry") or {}
        span = f"{r['frames'][0]}-{r['frames'][1]}" if r.get("frames") else "?"
        extra = ""
        if e:
            extra = f"  {e['engine']}, {e.get('samples') or 'AE'} samples{' (draft)' if e.get('draft') else ''}, " \
                    f"{e['file']}, {str(e.get('made_at', ''))[:16]}"
            if r["state"] == "moved":
                extra += f"  (registered {e['frames'][0]}-{e['frames'][1]}: needs a render)"
        lines.append(f"act {r['act']:<3} {str(r.get('shots') or ''):<10} {span:>11}  {r['state']}{extra}")
    for u in unfinished:
        t = u.get("target") or {}
        lines.append(f"unfinished: act {u['act']} frames {t.get('first')}-{t.get('last')} ({u['status']}); "
                     "slate render --resume picks it up")
    if note:
        lines.append(f"note: {note}")
    log.result({"ok": True, "film": str(film_dir), "acts": rows, "unfinished": unfinished, "note": note},
               human="\n".join(lines) or "Nothing rendered yet.")
    return 0


def _cmd_splice(args: argparse.Namespace) -> int:
    from slate.render import deps, registry, splice  # noqa: PLC0415

    film_dir = cli.film(args)
    if (args.act is None) == (args.base is None):
        raise UsageError("Say where the patch goes: --act N (its registered render) or --base PATH.")
    if args.act is not None:
        entry = registry.find(film_dir, args.act)
        if entry is None:
            raise Refused(f"Act {args.act} has no registered render.", hint=f"slate render --act {args.act}")
        base = entry["file"]
    else:
        base = args.base
    base_path = Path(base) if Path(base).is_absolute() else film_dir / base
    if cli.flag(args, "dry_run", False):
        log.result({"ok": True, "dry_run": True, "base": str(base_path), "patch": args.patch,
                    "frames": [args.frame_from, args.frame_to]},
                   human=f"Would splice {args.patch} into {base_path} at frames {args.frame_from}–{args.frame_to}, "
                         "proven by frame count, frame hashes at both joins and an edge PSNR scan.")
        return 0
    gb = 2.0 * (base_path.stat().st_size if base_path.is_file() else 0) / 1e9
    with deps.render_lock(owner="slate render splice"), deps.disk_reserve(base_path.parent, gb):
        proof = splice.splice(film_dir, base, args.patch, args.frame_from, args.frame_to)
    log.result({"ok": True, **proof}, human=proof["gate"]["summary"])
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    from slate.render import clean  # noqa: PLC0415

    try:
        film_dir = cli.film(args)
    except UsageError:
        film_dir = None  # outside a film: bundles and caches only
    dry = bool(cli.flag(args, "dry_run", False))
    report = clean.sweep(film_dir, older_than_min=args.older_than, everything=args.everything, dry_run=dry,
                         force=args.force)
    if report.get("skipped"):
        log.result({"ok": True, **report}, human=f"Nothing swept: {report['why']}. Try again when it's done, or add "
                                                 "--force.")
        return 0
    lines = [f"{i['action']}: {i['path']} ({_mb(i['bytes'])}; {i['why']})" for i in report["items"]]
    total = sum(i["bytes"] for i in report["items"])
    lines.append(("Would free" if dry else "Freed") + f" {_mb(total if dry else report['freed_bytes'])}."
                 if report["items"] else "Nothing to clean.")
    log.result({"ok": True, **report}, human="\n".join(lines))
    return 0


def _cmd_stills(args: argparse.Namespace) -> int:
    from slate.render import stills  # noqa: PLC0415

    film_dir = cli.film(args)
    try:
        frames = [int(x) for x in str(args.frames).replace(" ", "").split(",") if x != ""]
    except ValueError as exc:
        raise UsageError(f"--frames takes whole frame numbers separated by commas (got {args.frames!r}).") from exc
    if cli.flag(args, "dry_run", False):
        log.result({"ok": True, "dry_run": True, "frames": frames, "act": args.act},
                   human=f"Would render {len(frames)} still(s) from one bundle, then delete the bundle.")
        return 0
    result = stills.stills(film_dir, frames=frames, act=args.act, scale=args.scale, samples=args.samples,
                           out_dir=args.out, engine=args.engine)
    log.result(result, human="\n".join(f"act {s['act']} frame {s['frame']} (film {s['film_frame']}): {s['file']}"
                                       for s in result["stills"]) or "No stills came back from the engine.")
    return 0
