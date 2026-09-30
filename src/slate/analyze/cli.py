"""`slate analyze <video>`, plus the other commands this module family owns.

slate.cli loads this module for `analyze`. It also adds `slate formats` (slate.formats) and `slate bench`
(slate.bench.cli), which have no line of their own in slate.cli.MODULES yet; each registers only if no other module
has taken the name, and a failure there is recorded in slate.cli.BROKEN (where `slate doctor` looks) rather than
taking `analyze` down with it.
"""

from __future__ import annotations

import argparse
import importlib

from slate import cli, log, paths
from slate.analyze import DEFAULT_STEPS, STEPS

FLAGS = {"cuts": "cuts", "ocr": "ocr", "eases": "eases", "transcript": "transcript", "stems": "stems", "beats": "beats"}
MODEL_STEPS = ("transcript", "stems")  # the ones that hold the machine-wide whisper slot


def _requested(args: argparse.Namespace) -> tuple[list[str] | None, list[str]]:
    """(steps to run or None for the defaults, the steps named explicitly)."""
    explicit = [s for s in STEPS if (args.eases is not None if s == "eases" else getattr(args, s, False))]
    if args.all:
        return list(STEPS), list(STEPS)
    return (explicit or None), explicit


def _outs(videos: list[str], out: str | None) -> list[str | None]:
    """One folder per video: --out itself for one video, --out/<name> for several (numbered if two share a name)."""
    if not out or len(videos) == 1:
        return [out] * len(videos)
    from pathlib import Path  # noqa: PLC0415

    seen: dict[str, int] = {}
    folders = []
    for v in videos:
        stem = Path(v).stem or "video"
        seen[stem] = seen.get(stem, 0) + 1
        folders.append(str(Path(out) / (stem if seen[stem] == 1 else f"{stem}-{seen[stem]}")))
    return folders


def _payload(result: dict, explicit: list[str]) -> dict:
    from slate import analyze  # noqa: PLC0415

    out = result["out"]
    return {"video": result["video"]["path"], "out": out, "analysis": f"{out}/analysis.json",
            "report": f"{out}/analysis.md",
            "steps": {k: {"status": v["status"], "summary": v["summary"]} for k, v in result["steps"].items()},
            "scenes": len(result.get("scenes") or []), "moves": len((result.get("eases") or {}).get("moves") or []),
            "unchecked_requested": analyze.unchecked_requested(result, explicit)}


def cmd_analyze(args: argparse.Namespace) -> int:
    from slate import analyze  # noqa: PLC0415

    steps, explicit = _requested(args)
    film_dir = cli.film(args) if cli.flag(args, "film") else paths.find_film_root()
    eases_spec = args.eases if args.eases not in (None, "find") else None
    planned = steps or list(DEFAULT_STEPS)
    videos = list(args.video)
    outs = _outs(videos, args.out)
    if args.detach:
        from slate.jobs import client  # noqa: PLC0415

        resources = ("whisper",) if any(s in planned for s in MODEL_STEPS) else ()
        job = client.detach_current("analyze", resources, film=str(film_dir) if film_dir else None)
        log.result({"job": job, "steps": planned}, f"Queued as job {job}. Follow it with `slate job status {job}`.")
        return 0
    if cli.flag(args, "dry_run"):
        from slate.analyze import probe  # noqa: PLC0415

        rows, lines = [], []
        for video, out in zip(videos, outs):
            meta = probe.probe(video)
            where = out or str(analyze.default_out(video, meta["sha256"], film_dir))
            rows.append({"video": meta["path"], "out": where})
            lines.append(f"Would analyze {meta['name']} ({meta.get('duration_s') or 0:.1f} s) with "
                         f"{', '.join(planned)} into {where}.")
        lines.append("Nothing was run.")
        payload = {**rows[0], "steps": planned, "dry_run": True} if len(rows) == 1 else \
            {"videos": rows, "steps": planned, "dry_run": True}
        log.result(payload, "\n".join(lines))
        return 0
    payloads, lines = [], []
    for video, out in zip(videos, outs):
        result = analyze.run(video, out=out, steps=steps, film_dir=film_dir, higgsfield=args.higgsfield,
                             eases_spec=eases_spec, brand=args.brand, language=args.language,
                             allow_download=args.allow_download, max_moves=args.max_moves)
        payload = _payload(result, explicit)
        payloads.append(payload)
        lines.append(f"Analyzed {result['video']['name']} into {payload['out']}")
        for k, v in payload["steps"].items():
            lines.append(f"  {v['status']:<9} {k:<10} {v['summary']}")
        lines.append(f"Report: {payload['report']}")
        if payload["unchecked_requested"]:
            lines.append(f"Couldn't run what you asked for: {', '.join(payload['unchecked_requested'])} "
                         "(see the report for the fix).")
    missing = sorted({s for p in payloads for s in p["unchecked_requested"]})
    if len(payloads) == 1:
        log.result(payloads[0], "\n".join(lines))
    else:
        log.result({"analyses": payloads, "unchecked_requested": missing}, "\n".join(lines))
    return 3 if missing else 0


def register(sub) -> None:
    if "analyze" not in getattr(sub, "choices", {}):
        p = cli.add_command(
            sub, "analyze", cmd_analyze, "analyze a reference film: cuts, on-screen text, eases, voice, music",
            description="Measure what a reference film does, so a brand's look and motion start from evidence.\n\n"
                        "Without step flags it runs the quick, local steps (--cuts --ocr --eases). --all adds the "
                        "model-based ones. A step that can't run here is UNCHECKED, with the fix, and the rest still "
                        "run. Writes analysis.json and analysis.md to the film's analysis/ folder (with --film), or to "
                        "Slate's data folder; never beside the video.\n\n"
                        "Exit codes: 0 ok, 2 usage, 3 a step you named couldn't run here (the rest still ran).")
        p.add_argument("video", nargs="+", help="the reference film (or two or three, for a brand's motion study)")
        p.add_argument("--cuts", action="store_true", help="scene cuts and changes, frames and contact sheets (ffmpeg)")
        p.add_argument("--ocr", action="store_true", help="the words on screen in each scene (Apple Vision)")
        p.add_argument("--eases", nargs="?", const="find", metavar="MOVES.json",
                       help="measured eases: find the moves, or measure the ones described in MOVES.json")
        p.add_argument("--transcript", action="store_true", help="the voice-over, by local Whisper with word timings")
        p.add_argument("--stems", action="store_true", help="split voice and music with Demucs (uvx; analyze extra)")
        p.add_argument("--beats", action="store_true", help="the music's tempo and beat grid with librosa (uvx)")
        p.add_argument("--all", action="store_true", help="every step")
        p.add_argument("--higgsfield", metavar="ANALYSIS.json",
                       help="join a Higgsfield video analysis (0 credits; made in the chat lane) for scene labels")
        p.add_argument("--brand", help="compare measured eases with this brand pack's named eases too")
        p.add_argument("--language", default="en", help="the voice-over's language, for Whisper (default: en)")
        p.add_argument("--out", help="where to write the analysis (default: see above)")
        p.add_argument("--max-moves", dest="max_moves", type=int, help="most moves to find and fit (default 12)")
        p.add_argument("--allow-download", dest="allow_download", action="store_true",
                       help="let uvx and Demucs fetch what they need (off: Slate stays offline and says what's missing)")
        p.add_argument("--detach", action="store_true", help="run as a queued job and return its id at once")
    for module in ("slate.formats", "slate.bench.cli"):
        try:
            importlib.import_module(module).register(sub)
        except Exception as exc:  # noqa: BLE001 - one broken command family must not take `analyze` down
            cli.BROKEN[module] = f"register failed: {type(exc).__name__}: {exc}"
