"""`slate capture …`: scripted app and web captures, conformed to the film's clock and beat-mapped."""

from __future__ import annotations

import argparse

from slate import cli, jsonio, log
from slate.capture import beatmap, conform, ios, media, spec as spec_mod, store, web
from slate.errors import UsageError
from slate.gen import deps

HELP = """Real footage of an app or a page, on the film's clock.

  slate capture ios --spec capture.json   a pinned build on an isolated Simulator (demo account, 9:41 status bar)
  slate capture web --spec capture.json   a page through Aside at a fixed viewport, frame by frame
  slate capture conform --name N          re-conform a capture (or --src a recording you made, as a new capture)
  slate capture beatmap --name N          land its taps on the voice's words and write their visual events

Specs: src/slate/schemas/capture.schema.json ($defs/spec). Frames land in captures/<name>/frames/ as f00000.jpg …"""


def _summary(film, path) -> tuple[dict, str]:
    rec = jsonio.read(path)
    frames = rec.get("frames") or {}
    lines = [f"capture {rec['name']} ({rec['kind']}): {frames.get('count', 0)} frames at "
             f"{rec['film']['fps_num']}/{rec['film']['fps_den']} fps"
             + (f", {frames.get('width')}×{frames.get('height')}" if frames.get("width") else "")
             + (f", from film frame {frames.get('film_in')}" if frames.get("film_in") is not None else "")]
    if rec.get("beats"):
        lines.append(f"beats: {rec['beats']['count']} event(s) in {rec['beats']['file']}")
    for miss in rec.get("misses") or []:
        lines.append(f"miss: {miss}")
    for note in rec.get("notes") or []:
        lines.append(f"note: {note}")
    return rec, "\n".join(lines)


def _load_spec(args: argparse.Namespace, film_dir, kind: str) -> dict:
    film = deps.load_film(film_dir)
    if args.spec:
        data = jsonio.read(args.spec)
    elif args.name:
        data = jsonio.read(store.spec_path(film_dir, args.name))
    else:
        raise UsageError("Give the capture's spec (--spec file.json), or --name to rerun one this film already has.")
    if data.get("kind") != kind:
        raise UsageError(f"That spec is a {data.get('kind')} capture; this is `slate capture {kind}`.")
    return spec_mod.normalize(data, film)


def _ios(args: argparse.Namespace) -> int:
    film = cli.film(args)
    spec = _load_spec(args, film, "ios")
    if cli.flag(args, "dry_run", False):
        plan = ios.plan(film, spec)
        log.result(plan, f"would capture {spec['name']} on {plan['device']} (isolated: {plan['isolate']}), build "
                         f"{plan['build']['sha256'][:12]}… matches its pin, {len(plan['steps'])} step(s), tap driver "
                         f"{plan['tap_driver'] or 'none needed'}; nothing was booted")
        return 0
    path = ios.capture(film, spec, keep_booted=args.keep_booted)
    rec, human = _summary(film, path)
    log.result(rec, human)
    return 1 if rec.get("misses") else 0


def _web(args: argparse.Namespace) -> int:
    film = cli.film(args)
    spec = _load_spec(args, film, "web")
    if cli.flag(args, "dry_run", False):
        plan = web.plan(film, spec)
        log.result(plan, f"would capture {spec['name']} through Aside: {plan['frames']} frames at a "
                         f"{plan['viewport']['width']}×{plan['viewport']['height']} viewport, {plan['clock']} clock, "
                         f"{plan['chunks']} call(s); nothing was opened")
        return 0
    path = web.capture(film, spec)
    rec, human = _summary(film, path)
    log.result(rec, human)
    return 1 if rec.get("misses") else 0


def _conform(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if args.spec or args.name:
        what = (f"the file capture in {args.spec}" if args.spec else
                f"{args.src} as capture {args.name}" if args.src else f"capture {args.name}")
        if cli.dry_stop(args, f"conform {what} to a JPEG sequence at the film's rate (captures/), beat-map it and "
                              "record it"):
            return 0
    film_json = deps.load_film(film)
    if args.spec:
        spec = spec_mod.normalize(jsonio.read(args.spec), film_json)
        if spec["kind"] != "file":
            raise UsageError("`slate capture conform --spec` takes a file capture (kind: file); record ios and web "
                             "captures with `slate capture ios|web`.")
        src = spec_mod.resolve(film, spec["source"])
        store.capture_dir(film, spec["name"]).mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(store.spec_path(film, spec["name"]), spec)
        path = ios.finish(film, spec, source=src, command="slate capture conform")
    elif args.name and args.src and store.load(film, args.name) is None:
        raw = {"kind": "file", "name": args.name, "source": str(args.src),
               "duration": args.duration or media.probe(args.src).get("duration") or 0}
        if args.fit:
            raw["fit"] = args.fit
        spec = spec_mod.normalize(raw, film_json)
        store.capture_dir(film, spec["name"]).mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(store.spec_path(film, spec["name"]), spec)
        path = ios.finish(film, spec, source=spec_mod.resolve(film, spec["source"]), command="slate capture conform")
    elif args.name:
        rec = store.require(film, args.name)
        spec = rec["spec"]
        cap = store.capture_dir(film, args.name)
        if rec["kind"] == "web" and (cap / "shots").is_dir():
            shots = sorted((cap / "shots").glob("f[0-9][0-9][0-9][0-9][0-9].jpg"))
            if shots:
                raw = conform.from_frames(shots, film, args.name, spec=spec)
            else:
                stamps = jsonio.read(cap / "shots" / "stamps.json").get("stamps") or []
                raw = conform.from_timestamps(sorted((cap / "shots").glob("r[0-9][0-9][0-9][0-9][0-9].jpg")), stamps,
                                              film, args.name, duration=spec["duration"], spec=spec)
            path = ios.finish(film, spec, source=None, raw=raw, command="slate capture conform",
                              extra={k: rec.get(k) for k in ("web", "steps", "source", "notes")
                                     if rec.get(k) is not None})
        else:
            src = args.src or (cap / "raw.mov" if (cap / "raw.mov").is_file() else
                               spec_mod.resolve(film, spec["source"]) if spec.get("source") else None)
            if src is None:
                raise UsageError(f"Capture {args.name} has no recording to conform; pass --src.")
            raw = conform.to_sequence(src, film, args.name, duration=spec["duration"], spec=spec, method=args.method)
            path = ios.finish(film, spec, source=src, raw=raw, command="slate capture conform",
                              extra={k: rec.get(k) for k in ("ios", "steps") if rec.get(k) is not None})
    else:
        raise UsageError("Say what to conform: --name (a capture this film has), --name with --src (a recording "
                         "you made), or --spec (a file capture).")
    rec, human = _summary(film, path)
    log.result(rec, human)
    return 1 if rec.get("misses") else 0


def _beatmap(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if cli.dry_stop(args, f"beat-map capture {args.name} onto the film's words and record its gate"):
        return 0
    result = beatmap.run(film, args.name)
    gate = result["gate"]
    human = (f"{gate['status']}: {gate['summary']}\n{result['frames']['count']} frames from film frame "
             f"{result['film_in']}, {len(result['edits'])} retime edit(s), {result['events']} event(s)")
    human += "".join(f"\nmiss: {m}" for m in result["misses"])
    if gate["status"] == "UNCHECKED":
        human += "\n(unchecked is never passed: place the capture on the film clock, or anchor a step to a word)"
    log.result(result, human)
    return 0 if gate["status"] == "PASS" else 1  # only PASS counts as passed


def register(sub) -> None:
    g = cli.add_group(sub, "capture", "Scripted app and web captures, conformed to the film's clock and beat-mapped.",
                      description=HELP)
    p = cli.add_command(g, "ios", _ios, "record a pinned build on an isolated iOS Simulator (--dry-run checks only)")
    p.add_argument("--spec", help="the capture spec (JSON)")
    p.add_argument("--name", help="rerun the capture this film already has under that name")
    p.add_argument("--keep-booted", action="store_true", help="leave the Simulator booted afterwards")
    p = cli.add_command(g, "web", _web, "capture a page through Aside at a fixed viewport (--dry-run checks only)")
    p.add_argument("--spec", help="the capture spec (JSON)")
    p.add_argument("--name", help="rerun the capture this film already has under that name")
    p = cli.add_command(g, "conform", _conform, "conform a capture (or a recording you made) to the film's frame rate")
    p.add_argument("--name", help="the capture")
    p.add_argument("--src", help="a recording to conform (makes a new file capture when --name is new)")
    p.add_argument("--spec", help="a file capture's spec (kind: file), with marks for its taps")
    p.add_argument("--duration", type=float, help="seconds (default: the recording's length)")
    p.add_argument("--fit", choices=("native", "contain", "cover"))
    p.add_argument("--method", choices=("dup", "mci", "blend"), default="dup",
                   help="frame-rate conversion (dup shows what was on screen at each frame; right for captures)")
    p = cli.add_command(g, "beatmap", _beatmap, "land a capture's taps on the voice's words; write their visual events")
    p.add_argument("--name", required=True)
