"""`slate script build` and `slate script lint`."""

from __future__ import annotations

import argparse

from slate import cli, gates, log

BUILD_HELP = """\
Build the storyboard from script/script.json, the one hand-edited script file.

Reads the script, the brand's truth (banned words, approved claims, what it never claims), the format (runtime, pace)
and brief/truth.json (pinned sources); runs the script gates; and only when none fails writes script/storyboard.json
and script/SCRIPT.md (both stamped generated). Any FAIL and nothing is written.

Gates: pace (at most 2.6 words a second), on-screen read time, on-screen density, banned words, runtime (±10% of the
target), no unsourced numbers, every claim approved or sourced, no invented quotes, and the shot-id freeze (once scenes
use the ids, a renumber is refused).

  --check        run everything the build would, write nothing
  --from-takes   after voicing: retime every voiced shot from its approved take so each read fits its shot

Exit codes: 0 written (or checked clean), 1 a gate failed, 2 usage, 3 missing prerequisite (no script, no brand pack,
a line not voiced for --from-takes)."""

LINT_HELP = """\
Check script/script.json against the script gates at planning lengths, without building or writing anything.

The quick loop while writing: pace, read time, density, banned words, runtime, numbers, claims and quotes. `slate
script build --check` adds the real takes (once the film is timed from them) and the shot-id freeze."""


def _report(results, *, written, refused: bool, extra: dict, human_tail: list[str], film_dir=None) -> int:
    summary = gates.summarize(results)
    shown = [str(p.relative_to(film_dir)) if film_dir else str(p) for p in written]
    payload = {"ok": not refused and summary["counts"]["FAIL"] == 0, "status": summary["status"],
               "written": shown, "refused": refused, "summary": summary,
               "gates": [r.to_dict() for r in results], **extra}
    log.result(payload, "\n".join([gates.format_table(results), *human_tail]))
    return 1 if summary["counts"]["FAIL"] else 0


def _build(args: argparse.Namespace) -> int:
    from slate.story.builder import run  # noqa: PLC0415

    film_dir = cli.film(args)
    check = bool(getattr(args, "check", False) or cli.flag(args, "dry_run", False))
    out = run(film_dir, check_only=check, from_takes=bool(getattr(args, "from_takes", False)))
    p = out.plan
    tail = []
    if out.refused:
        tail.append("Nothing written: fix the FAILs above, then build again.")
    elif check:
        tail.append("Checked; nothing written (--check).")
    else:
        tail.append(f"Wrote {', '.join(str(x.relative_to(film_dir)) for x in out.written)}.")
    if p is not None and p.shots:
        timing = "from the takes" if p.mode == "takes" else "planning lengths"
        tail.append(f"{len(p.shots)} shots, {p.runtime:.1f} s, {timing}.")
        if p.missing_takes and p.mode == "takes":
            tail.append(f"Still planned (no take yet): {', '.join(p.missing_takes)}.")
    if out.retimed:
        tail.append(f"Retimed: {', '.join(out.retimed[:20])}" + (" …" if len(out.retimed) > 20 else ""))
    extra = {"film": str(film_dir), "checked_only": check,
             "shots": len(p.shots) if p else 0, "runtime_s": p.runtime if p else None,
             "timing": p.mode if p else None, "retimed": out.retimed,
             "ids": {s.source_id: s.id for s in p.shots} if p else {}}
    return _report(out.results, written=out.written, refused=out.refused, extra=extra, human_tail=tail,
                   film_dir=film_dir)


def _lint(args: argparse.Namespace) -> int:
    from slate.story import inputs  # noqa: PLC0415
    from slate.story.gates import check_script  # noqa: PLC0415
    from slate.story.model import load_script  # noqa: PLC0415

    film_dir = cli.film(args)
    film = inputs.load_film(film_dir)
    script = load_script(film_dir)
    brand = inputs.load_brand(film.get("brand"))
    fmt, problem = inputs.load_format(film.get("format"))
    results = check_script(script, brand=brand, fmt=fmt, truth=inputs.load_truth(film_dir), film=film,
                           default_enter_token=(script.get("defaults") or {}).get("enter_token"))
    tail = [problem] if problem else []
    tail.append("Lint only: nothing written.")
    return _report(results, written=[], refused=False, extra={"film": str(film_dir), "lint": True}, human_tail=tail)


def register(sub) -> None:
    stages = cli.add_group(sub, "script", help="the script: gate it and build the storyboard",
                           description="The one hand-edited source, script/script.json, and the storyboard built from it.")
    build = cli.add_command(stages, "build", _build, help="gate script.json and write storyboard.json + SCRIPT.md",
                            description=BUILD_HELP)
    build.add_argument("--check", action="store_true", help="run every gate; write nothing")
    build.add_argument("--from-takes", dest="from_takes", action="store_true",
                       help="retime voiced shots from their approved takes")
    cli.add_command(stages, "lint", _lint, help="check script.json against the script gates; write nothing",
                    description=LINT_HELP)
