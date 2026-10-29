"""Phase 8's benchmark: is Higgsedit a faster route than a Remotion cut-down for social channels, at acceptable quality?

Decision 9 (plan): variants by channel. Hero placements (website, App Store, YouTube masters) are derived films at
master quality through the full pipeline; social cut-downs (Instagram, TikTok, Shorts, LinkedIn) can take a fast
route. Higgsedit, Higgsfield's code-driven motion engine, becomes that route only if this benchmark shows it is
genuinely faster than a Remotion cut-down at acceptable quality. Otherwise Slate drops it.

The protocol (protocol.json, version 2):

- one finished film with a master, one social channel (TikTok unless another is named) and one cut: the channel
  format's target length in the master's own frames, the same frames for both arms;
- arm **remotion**, the fast route today: a derived film (`slate variant`) rendered by Remotion at
  render.draft_samples and mastered with ffmpeg;
- arm **higgsedit**: the same cut built in Higgsedit, in Higgsfield's hosted sandbox, from the film's own words,
  events and assets, driven through the chat lane's Higgsfield connector;
- per run: the wall time from the first arm-specific preparation action to the finished local file, including
  authoring and staging; PSNR and SSIM of the cut against the master's frames (slate.bench.quality); app credits used;
- a person's REVIEW of each distinct measured Higgsedit cut, watched beside the measured Remotion cuts and master.

The decision rule is in RULE and in every report. Missing measurements never make a keep: the verdict is
"incomplete" until both arms have runs and a person has reviewed the Higgsedit cut.

Running the Higgsedit arm needs Quinn: Higgsedit bills Higgsfield app credits, a separate pool from the Cloud API
Slate is funded on, and app credits aren't funded. `slate bench higgsedit run` refuses with that reason. Everything
else (the plan, measuring a finished cut, the review, the report) works now.
"""

from __future__ import annotations

import math
import statistics
from copy import deepcopy
from pathlib import Path

from slate import __version__, formats, jsonio, paths
from slate.clock import Clock
from slate.errors import MissingPrerequisite, SpendRefused, UsageError
from slate.gates import now_iso

BENCH = "higgsedit"
VERSION = 2
ARMS = ("remotion", "higgsedit")
VERDICTS = ("acceptable", "not-acceptable")
DEFAULT_CHANNEL = "tiktok"
MASTER_EXTS = (".mov", ".mp4", ".mkv", ".m4v")

RULE = {
    "speedup_min": 1.5,
    "ssim_mean_min": 0.90,
    "ssim_frame_floor": 0.80,
    "ssim_below_floor_max_share": 0.05,
    "frames_must_match": True,
    "review_needed": "acceptable",
    "runs_min": 1,
    "runs_recommended": 3,
    "why": {
        "speed": "Genuinely faster means a margin, not a tie: the Higgsedit arm's median wall time, end to end, must be "
                 "at most two thirds of the Remotion arm's (1.5 times faster).",
        "fidelity": "A floor against the master that catches broken or missing picture: mean SSIM 0.90 or better, no "
                    "more than 5% of frames under 0.80, and the same number of frames. Another engine never matches "
                    "pixel for pixel, so this is a floor, not a beauty score.",
        "review": "Acceptable quality is a person's call, made watching both channel cuts beside the master. A review "
                  "is never assumed: until a person records one, there is no verdict.",
        "harness": "The Remotion arm must clear the same fidelity floor itself; if it doesn't, the harness is wrong "
                   "and nothing is decided.",
    },
}

REFUSAL = ("The Higgsedit arm can't run from here. Higgsedit runs in Higgsfield's hosted sandbox and bills Higgsfield "
           "app credits, a separate pool from the Cloud API Slate is funded on, and app credits aren't funded. The "
           "benchmark waits for Quinn's go.")
REFUSAL_HINT = ("Quinn: fund app credits and say go. Then build the cut in the chat lane with Higgsfield's connector, "
                "time it, and record it: slate bench higgsedit measure --arm higgsedit --cut <file> --wall-s <seconds>. "
                "The Remotion arm can be measured any time: "
                "slate bench higgsedit measure --arm remotion --cut <file> --wall-s <seconds>.")

BACKGROUND = ("An earlier timeboxed test built a 100-second cut natively in Higgsedit. It rendered in about 95 to 125 "
              "seconds per full render in Higgsfield's sandbox after about an hour and a half of authoring; it matched "
              "the flagship's timing and brand but not its depth (no 3D, limited motion blur), and the project could "
              "only be edited as code. That test spent no credits; the sandbox now bills app credits.")


# ---------------------------------------------------------------------------------------------------------------
# Where things live


def bench_dir(film_dir: Path | None = None, out: str | Path | None = None) -> Path:
    if out:
        return Path(out).expanduser().resolve()
    if film_dir:
        return Path(film_dir) / "bench" / BENCH
    return paths.data() / "bench" / BENCH


def find_master(film_dir: Path | None) -> Path | None:
    """The film's newest master (masters/*.mov, .mp4, .mkv or .m4v), or None."""
    if not film_dir:
        return None
    folder = Path(film_dir) / "masters"
    if not folder.is_dir():
        return None
    found = [p for p in folder.rglob("*") if p.suffix.lower() in MASTER_EXTS and p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def social_format_for(channel: str) -> tuple[str, str]:
    """(format, aspect) a social cut-down for this channel is made in: the first format made for it, the reel first."""
    preferred = ["social-reel", "linkedin", "ad"]
    ordered = preferred + [n for n in formats.names() if n not in preferred]
    for name in ordered:
        if name not in formats.names():
            continue
        fmt = formats.load(name)
        for aspect, spec in fmt["aspects"].items():
            if channel in spec["channels"]:
                return name, aspect
    raise UsageError(f"No format is made for {channel}.")


# ---------------------------------------------------------------------------------------------------------------
# The protocol


def _parse_ranges(text: str | None) -> list[list[int]] | None:
    if not text:
        return None
    out = []
    for part in str(text).split(","):
        a, _, b = part.strip().partition("-")
        if not (a.strip().isdigit() and b.strip().isdigit()) or int(b) <= int(a):
            raise UsageError(f"{part!r} isn't a frame range; write ranges like 0-900,1800-2250 (in, out).")
        out.append([int(a), int(b)])
    return out


def _protocol_hash(protocol: dict) -> str:
    """Creation time is bookkeeping; every comparison and decision input is immutable."""
    return jsonio.sha256_obj({k: v for k, v in protocol.items() if k not in ("created_at", "protocol_sha256")})


def _digest(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _number(value, *, positive: bool = False, unit: bool = False) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and (value > 0 if positive else value >= 0) and (not unit or value <= 1))


def _media_valid(info) -> bool:
    return (isinstance(info, dict) and all(isinstance(info.get(k), int) and not isinstance(info[k], bool)
                                        and info[k] > 0 for k in ("width", "height", "fps_num", "fps_den")))


def _binding(protocol: dict) -> dict:
    cut = protocol.get("cut") if isinstance(protocol.get("cut"), dict) else {}
    media = protocol.get("master_media") if isinstance(protocol.get("master_media"), dict) else {}
    return deepcopy({"protocol_sha256": protocol.get("protocol_sha256"),
                     "master_sha256": protocol.get("master_sha256"),
                     "ranges": cut.get("ranges"),
                     "clock": {k: media.get(k) for k in ("fps_num", "fps_den")},
                     "format": protocol.get("format")})


def _protocol_problems(protocol: dict) -> list[str]:
    if protocol.get("version") != VERSION or not _digest(protocol.get("protocol_sha256")):
        return ["protocol has legacy unbound evidence; plan a new benchmark in a new --out folder"]
    if protocol["protocol_sha256"] != _protocol_hash(protocol):
        return ["protocol changed after it was frozen; plan a new benchmark in a new --out folder"]
    for key in ("master_media", "format", "cut", "rule", "channel", "arms", "measure"):
        if not isinstance(protocol.get(key), dict):
            return [f"protocol {key} is malformed; preserve it and plan a new --out folder"]
    if not protocol.get("master") or not _digest(protocol.get("master_sha256")):
        return ["protocol has no frozen master; plan with --master in a new --out folder"]
    info, fmt, cut = protocol.get("master_media"), protocol.get("format") or {}, protocol.get("cut") or {}
    ranges = cut.get("ranges")
    if not _media_valid(info) or not all(isinstance(fmt.get(k), int) and not isinstance(fmt[k], bool)
                                       and fmt[k] > 0 for k in ("width", "height")):
        return ["protocol has no valid frozen clock and dimensions"]
    if not isinstance(fmt.get("aspect"), str) or not fmt["aspect"]:
        return ["protocol has no frozen channel aspect"]
    if not (isinstance(ranges, list) and ranges and all(isinstance(r, list) and len(r) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in r) and 0 <= r[0] < r[1] for r in ranges)
            and cut.get("frames") == sum(b - a for a, b in ranges)):
        return ["protocol has no valid frozen frame ranges"]
    rule = {**RULE, **protocol["rule"]}
    if (not _number(rule.get("speedup_min"), positive=True)
            or not all(_number(rule.get(k), unit=True) for k in
                       ("ssim_mean_min", "ssim_frame_floor", "ssim_below_floor_max_share"))
            or not isinstance(rule.get("runs_min"), int) or isinstance(rule["runs_min"], bool)
            or rule["runs_min"] <= 0 or rule.get("review_needed") not in VERDICTS):
        return ["protocol decision rule needs finite thresholds and a positive run count"]
    return []


def _run_problems(run: dict, protocol: dict) -> list[str]:
    """Reject incomplete coverage and malformed historical measurements before calculating a median."""
    if not isinstance(run, dict):
        return ["measurement is not a record"]
    if run.get("binding") != _binding(protocol) or not _digest(run.get("cut_sha256")):
        return ["measurement has stale or legacy unbound protocol/master/cut evidence"]
    if not _digest(run.get("run_sha256")) or run["run_sha256"] != jsonio.sha256_obj(
            {k: v for k, v in run.items() if k != "run_sha256"}):
        return ["measurement changed after it was recorded"]
    if run.get("arm") not in ARMS or not _number(run.get("wall_s"), positive=True):
        return ["measurement needs a known arm and finite positive wall time"]
    if run.get("app_credits") is not None and not _number(run["app_credits"]):
        return ["measurement app credits must be finite and nonnegative"]
    ssim, psnr = run.get("ssim"), run.get("psnr")
    if not isinstance(ssim, dict) or not isinstance(psnr, dict):
        return ["measurement has no finite quality values"]
    if not all(_number(ssim.get(k), unit=True) for k in ("mean", "min", "below_floor_share")):
        return ["measurement SSIM values must be finite and between zero and one"]
    if not all(isinstance(psnr.get(k), (int, float)) and not isinstance(psnr[k], bool)
               and math.isfinite(psnr[k]) for k in ("mean_db", "min_db")):
        return ["measurement PSNR values must be finite"]
    master, cut = protocol.get("master_media") or {}, run.get("cut_media") or {}
    if run.get("master_media") != master or not _media_valid(cut):
        return ["measurement master metadata differs from the frozen protocol"]
    if any(cut[k] != master[k] for k in ("fps_num", "fps_den")):
        return ["measurement clock differs from the frozen master"]
    if any(cut[k] != protocol["format"][k] for k in ("width", "height")):
        return ["measurement dimensions/aspect differ from the planned channel"]
    expected = protocol["cut"]["frames"]
    counts = [run.get(k) for k in ("frames", "frames_reference", "frames_compared")]
    if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in counts):
        return ["measurement has no exact positive frame coverage"]
    if run.get("frames_reference") != expected or run.get("ranges") != protocol["cut"]["ranges"]:
        return ["measurement frame ranges differ from the frozen protocol"]
    if (not isinstance(run.get("frames_match"), bool) or run["frames_match"] != (run["frames"] == expected)
            or run["frames_compared"] != min(run["frames"], expected)
            or psnr.get("frames") != run["frames_compared"]):
        return ["measurement did not compare every required frame"]
    return []


def _freshness(protocol: dict, data: dict) -> list[str]:
    """A report or review only uses the bytes that were actually measured."""
    files = [(protocol.get("master"), protocol.get("master_sha256"), "protocol master")]
    runs = data.get("runs", [])
    for i, run in enumerate(runs if isinstance(runs, list) else []):
        if isinstance(run, dict):
            binding = run.get("binding") if isinstance(run.get("binding"), dict) else {}
            files += [(run.get("cut"), run.get("cut_sha256"), f"measurement {i + 1} cut"),
                      (run.get("master"), binding.get("master_sha256"),
                       f"measurement {i + 1} master")]
    problems, cache = [], {}
    for path, expected, label in files:
        if not isinstance(path, str) or not _digest(expected):
            problems.append(f"{label} has no bound file/hash")
            continue
        if path not in cache:
            try:
                cache[path] = jsonio.sha256(path)
            except (OSError, ValueError):
                cache[path] = None
        if cache[path] != expected:
            problems.append(f"{label} is missing or its bytes changed")
    return problems


def _require_protocol(protocol: dict) -> None:
    problems = _protocol_problems(protocol)
    if problems:
        raise MissingPrerequisite(problems[0], hint="slate bench higgsedit plan --master <file> --out <new folder>")


def _full_source_timing(film_dir: Path | None, film: dict | None, master_media: dict | None,
                        fmt: dict, ranges: list | None) -> dict | None:
    """Recognize an unchanged source selection; other selections still need explicit cut authoring."""
    if not film_dir or not film or not master_media:
        return None
    if any(film.get(k) != master_media.get(k) for k in ("fps_num", "fps_den", "width", "height")):
        return None
    if (film.get("width"), film.get("height")) != (fmt.get("width"), fmt.get("height")):
        return None
    from slate.state import lock  # noqa: PLC0415 - use the same source resolver as rendering

    source = Path(lock.resolve(Path(film_dir), "timing/shots.json"))
    shots = jsonio.read(source, default={}) or {}
    if not isinstance(shots, dict):
        return None
    frames = shots.get("total_frames")
    if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0 or ranges != [[0, frames]]:
        return None
    if any(shots.get(k) != film.get(k) for k in ("fps_num", "fps_den", "width", "height")):
        return None
    return {"path": str(source), "sha256": jsonio.sha256(source), "total_frames": frames}


def plan(*, film_dir: Path | None = None, channel: str = DEFAULT_CHANNEL, cut_s: float | None = None,
         ranges: str | None = None, master: str | Path | None = None, out: str | Path | None = None) -> dict:
    """Freeze one comparison. Repeating it preserves history; a different comparison needs a new folder."""
    ch = formats.channel_key(channel)
    route = formats.route_for(ch)
    if route["route"] != "social":
        raise UsageError(f"{route['label']} is a hero placement: it always gets a derived film at master quality "
                         "through the full pipeline, so there's nothing to benchmark.",
                         hint="Benchmark a social channel: tiktok, instagram_reels, youtube_shorts or linkedin.")
    fmt_name, aspect = social_format_for(ch)
    fmt = formats.load(fmt_name, aspect=aspect)
    seconds = float(cut_s) if cut_s is not None else float(fmt["runtime_s"]["target"])
    if not _number(seconds, positive=True):
        raise UsageError("The cut's length must be finite and positive.")
    film = None
    master_path = Path(master).expanduser().resolve() if master else find_master(film_dir)
    master_hash, master_media = None, None
    if master_path and master_path.is_file():
        from slate.qa import media  # noqa: PLC0415

        master_hash = jsonio.sha256(master_path)
        master_media = {k: v for k, v in media.video_info(master_path, count=False).items()
                        if k in ("width", "height", "fps_num", "fps_den")}
        if jsonio.sha256(master_path) != master_hash:
            raise UsageError("The master changed while its protocol was being frozen.")
        if not _media_valid(master_media):
            raise UsageError("The master needs a valid clock and dimensions.")
    if film_dir:
        fj = jsonio.read(Path(film_dir) / "film.json")
        film = {"id": fj.get("id"), "dir": str(film_dir), "format": fj.get("format"), "fps_num": fj.get("fps_num"),
                "fps_den": fj.get("fps_den"), "width": fj.get("width"), "height": fj.get("height")}
    frames = None
    variant_seconds = seconds
    cut_ranges = _parse_ranges(ranges)
    if master_media or (film and film.get("fps_num")):
        source = master_media or film
        if master_media and film and any(film.get(k) != master_media[k] for k in ("fps_num", "fps_den")):
            raise UsageError("The film's clock differs from its master; choose one coherent delivery.")
        clock = Clock(int(source["fps_num"]), int(source["fps_den"]))
        frames = clock.frame_of(seconds)
        cut_ranges = cut_ranges or [[0, frames]]
        frames = sum(b - a for a, b in cut_ranges)
        variant_seconds = clock.secs_of(frames)
    folder = bench_dir(film_dir, out)
    child = f"{film['id']}-bench-remotion" if film else "<film>-bench-remotion"
    film_ref = film["id"] if film else "<film>"
    full_source = _full_source_timing(film_dir, film, master_media, fmt, cut_ranges)
    if full_source:
        preparation = [
            f"slate variant {film_ref} {child} --channel {ch} --aspect {aspect} --format {fmt_name}",
            "Reuse the unchanged full-length script, exact source timing and captions by reference. "
            "Do not shorten the selection or rebuild its clock from missing historical voice takes.",
        ]
    else:
        preparation = [
            f"slate variant {film_ref} {child} --channel {ch} --aspect {aspect} --format {fmt_name} "
            f"--cut {variant_seconds}",
            "Apply protocol.cut.ranges in their recorded order to the child picture and sound. "
            "--cut sets duration only. Set script/script.json cut.keep for whole parent shots in "
            "parent order; author child acts and explicit scene/audio trims for partial or reordered ranges.",
            f"slate script build --film {child}",
            "Use --from-takes on the script build when unchanged approved takes should set shot lengths; "
            "it does not trim arbitrary source ranges.",
            f"slate timing --film {child}",
        ]
    protocol = {
        "generated": "do not edit by hand: slate bench higgsedit plan",
        "bench": BENCH, "version": VERSION, "slate_version": __version__, "created_at": now_iso(),
        "question": "Is Higgsedit genuinely faster than a Remotion cut-down for a social channel, at acceptable quality?",
        "decision": "Plan decision 9 and phase 8: keep Higgsedit as the fast route for social cut-downs only if it wins.",
        "status": "not run: needs Quinn",
        "needs": [REFUSAL, REFUSAL_HINT],
        "film": film, "master": str(master_path) if master_path else None,
        "master_sha256": master_hash, "master_media": master_media,
        "channel": {"key": ch, "label": route["label"], "route": route["route"]},
        "format": {"name": fmt_name, "aspect": aspect, "width": fmt["width"], "height": fmt["height"]},
        "cut": {"seconds": seconds, "ranges": cut_ranges, "frames": frames,
                "note": "Ordered [in, out) master-frame ranges, the same for both arms. --ranges freezes the "
                        "selection; it does not apply it to a variant."},
        "arms": {
            "remotion": {
                "engine": "remotion", "route": "the social fast route: fewer motion-blur samples, an ffmpeg master",
                "steps": preparation + ["Stage/re-stage the child Remotion scenes, imported film modules, required public assets "
                          "and fonts, and events at the selected ranges and channel layout. A variant does not copy them.",
                          f"Align and stage the child's sound/mix/final_mix.wav for the cut. Use slate mix --film {child} "
                          "when rebuilding the mix, or retain a valid authored/imported candidate mix.",
                          f"slate render --film {child}", f"slate master --ffmpeg --film {child}"],
                "timing": "wall time from the first arm-specific preparation action through the finished local file, "
                          "including manual authoring, staging, build, queue wait, render and master",
                "cost": "nothing: it runs on this Mac",
            },
            "higgsedit": {
                "engine": "higgsedit",
                "route": "Higgsedit in Higgsfield's hosted sandbox, through the chat lane's Higgsfield connector",
                "steps": ["Build protocol.cut.ranges in their recorded order in Higgsedit from the film's own words, "
                          "events and assets; prepare its resources and aligned audio.",
                          "Render it at the channel's size on the master's clock.",
                          "Download the file; note the app credits it used."],
                "timing": "wall time from the first arm-specific preparation action through the finished local file, "
                          "including manual authoring, staging, queue wait, render and download, the same way",
                "cost": "Higgsfield app credits (unfunded)",
            },
        },
        "measure": {
            "wall_time": "per run, preparation through finished local output, recorded with --wall-s; the median of "
                         "an arm's runs counts; record reuse, cache and queue conditions with --note",
            "quality": "PSNR and SSIM against the master's frames in the cut's ranges, at the master's size; a "
                       "reframed reference when the cut changes aspect (slate.bench.quality)",
            "review": "a person watches both channel cuts beside the master and records acceptable or not acceptable",
            "record": "slate bench higgsedit measure --arm <arm> --cut <file> --wall-s <seconds> [--app-credits N]",
            "review_command": "slate bench higgsedit review --arm higgsedit --verdict acceptable|not-acceptable --by <name>",
        },
        "rule": deepcopy(RULE),
    }
    protocol["protocol_sha256"] = _protocol_hash(protocol)
    if (folder / "protocol.json").exists():
        previous = jsonio.read(folder / "protocol.json")
        if (isinstance(previous, dict) and previous.get("version") == VERSION
                and previous.get("protocol_sha256") == protocol["protocol_sha256"]
                and _protocol_hash(previous) == protocol["protocol_sha256"]):
            return previous
        raise UsageError("This folder already has a different or legacy benchmark protocol; its evidence is preserved.",
                         hint="Keep this benchmark and plan the new comparison with --out <new folder>.")
    if (folder / "measurements.json").exists():
        raise UsageError("This folder already contains benchmark measurements without a protocol; they are preserved.",
                         hint="Plan the new comparison with --out <new folder>.")
    folder.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(folder / "protocol.json", protocol)
    if not (folder / "measurements.json").exists():
        jsonio.write_atomic(folder / "measurements.json", {"runs": [], "reviews": []})
    report(folder)
    return protocol


def load(folder: Path) -> tuple[dict, dict]:
    proto_path = folder / "protocol.json"
    if not proto_path.is_file():
        raise MissingPrerequisite(f"No benchmark protocol in {folder}.",
                                  hint="Write one first: slate bench higgsedit plan --film <film>")
    protocol = jsonio.read(proto_path)
    data = jsonio.read(folder / "measurements.json", default={"runs": [], "reviews": []})
    if not isinstance(protocol, dict) or not isinstance(data, dict):
        raise UsageError("Benchmark protocol and measurements must be JSON objects; malformed evidence is preserved.",
                         hint="Preserve this folder and plan a new comparison with --out <new folder>.")
    return protocol, data


def refuse_run() -> None:
    """The Higgsedit arm needs app credits nobody has funded: refuse, and say what unlocks it."""
    raise SpendRefused(REFUSAL, hint=REFUSAL_HINT, details={"needs": "quinn", "arm": "higgsedit",
                                                             "billing": "higgsfield app credits (unfunded)"})


# ---------------------------------------------------------------------------------------------------------------
# Measurements and the verdict


def record_run(folder: Path, *, arm: str, cut: str | Path, wall_s: float, master: str | Path | None = None,
               app_credits: float | None = None, note: str | None = None, comparer=None) -> dict:
    """Measure one finished cut against the master and add it to measurements.json."""
    if arm not in ARMS:
        raise UsageError(f"Unknown arm {arm!r}; the arms are {', '.join(ARMS)}.")
    if not _number(wall_s, positive=True):
        raise UsageError("--wall-s must be finite and positive, measured from the first command to the finished file.")
    if app_credits is not None and not _number(app_credits):
        raise UsageError("--app-credits must be finite and nonnegative.")
    protocol, data = load(folder)
    _require_protocol(protocol)
    if not isinstance(data.get("runs", []), list):
        raise UsageError("Existing measurements are malformed; preserve them and plan a new --out folder.")
    master_path = master or protocol.get("master")
    if not master_path or not Path(master_path).is_file():
        raise MissingPrerequisite("The benchmark compares each cut with the film's master, and there isn't one yet.",
                                  hint="Make the master first (slate master), or pass --master <file>.")
    cut_path, master_path = Path(cut).expanduser().resolve(), Path(master_path).expanduser().resolve()
    if not cut_path.is_file():
        raise UsageError(f"No video at {cut_path}.")
    before = {str(p): jsonio.sha256(p) for p in (cut_path, master_path)}
    if before[str(master_path)] != protocol["master_sha256"]:
        raise UsageError("This master differs from the frozen benchmark master.",
                         hint="Measure both arms against the frozen master, or plan a new --out folder.")
    if comparer is None:
        from slate.bench import quality  # noqa: PLC0415

        comparer = quality.compare
    q = comparer(cut_path, master_path, ranges=deepcopy(protocol["cut"]["ranges"]))
    for p, expected in before.items():
        if not Path(p).is_file() or jsonio.sha256(p) != expected:
            raise UsageError("Benchmark input bytes changed during the quality comparison; no run was recorded.")
    if q.get("cut") != str(cut_path) or q.get("master") != str(master_path):
        raise UsageError("The quality comparison reports different input files; no run was recorded.")
    psnr, ssim = q.get("psnr"), q.get("ssim")
    if not isinstance(psnr, dict) or not isinstance(ssim, dict):
        raise UsageError("The quality comparison has no finite quality values; no run was recorded.")
    run = {"arm": arm, "wall_s": float(wall_s), "cut": str(cut_path),
           "binding": _binding(protocol), "cut_sha256": before[str(cut_path)], "master": str(master_path),
           "cut_media": q.get("cut_media"), "master_media": q.get("master_media"), "ranges": q.get("ranges"),
           "frames": q.get("frames_cut"), "frames_reference": q.get("frames_reference"),
           "frames_compared": q.get("frames_compared"), "frames_match": q.get("frames_match"),
           "reference": q.get("reference"),
           "psnr": {k: psnr.get(k) for k in ("mean_db", "min_db", "frames")},
           "ssim": {k: ssim.get(k) for k in ("mean", "min", "below_floor", "below_floor_share")},
           "app_credits": app_credits, "note": note, "measured_at": now_iso(), "engine": q.get("engine")}
    run["run_sha256"] = jsonio.sha256_obj(run)
    problems = _run_problems(run, protocol)
    if problems:
        raise UsageError(problems[0] + "; no run was recorded.")
    data.setdefault("runs", []).append(run)
    jsonio.write_atomic(folder / "measurements.json", data)
    report(folder)
    return run


def record_review(folder: Path, *, arm: str, verdict: str, by: str, note: str | None = None,
                  cut: str | Path | None = None) -> dict:
    """A person's judgement of an arm's channel cut, watched beside the master."""
    if arm not in ARMS:
        raise UsageError(f"Unknown arm {arm!r}; the arms are {', '.join(ARMS)}.")
    if verdict not in VERDICTS:
        raise UsageError(f"A review is {' or '.join(VERDICTS)}, not {verdict!r}.")
    if not str(by or "").strip():
        raise UsageError("A review needs the name of the person who watched it (--by).")
    protocol, data = load(folder)
    _require_protocol(protocol)
    if not isinstance(data.get("runs", []), list) or not isinstance(data.get("reviews", []), list):
        raise UsageError("Existing evidence is malformed; preserve it and plan a new --out folder.")
    problems = _freshness(protocol, data)
    for i, run in enumerate(data.get("runs", [])):
        problems += [f"measurement {i + 1}: {p}" for p in _run_problems(run, protocol)]
    if problems:
        raise UsageError("The measured inputs are not current: " + problems[0],
                         hint="Preserve this benchmark and measure a coherent comparison in a new --out folder.")
    runs = [r for r in data.get("runs", []) if r["arm"] == arm]
    remotion_hashes = sorted({r["cut_sha256"] for r in data.get("runs", []) if r["arm"] == "remotion"})
    if not runs or (arm == "higgsedit" and not remotion_hashes):
        raise MissingPrerequisite("Review needs a measured cut and the measured Remotion comparison.",
                                  hint="Measure both arms first, then review the exact cut with --cut <file>.")
    if cut is not None:
        path = Path(cut).expanduser().resolve()
        if not path.is_file():
            raise UsageError(f"No reviewed cut at {path}.")
        digest = jsonio.sha256(path)
        runs = [r for r in runs if r["cut_sha256"] == digest]
        if not runs:
            raise UsageError("These cut bytes have not been measured for this benchmark.",
                             hint="Measure this cut first, then record the person's review.")
    row = {"arm": arm, "verdict": verdict, "by": str(by).strip(), "note": note, "at": now_iso(),
           "binding": _binding(protocol), "cut_sha256": runs[-1]["cut_sha256"],
           "remotion_cut_sha256s": remotion_hashes}
    data.setdefault("reviews", []).append(row)
    jsonio.write_atomic(folder / "measurements.json", data)
    report(folder)
    return row


def _arm_summary(runs: list[dict]) -> dict:
    if not runs:
        return {"runs": 0}
    ssim_means = [r["ssim"]["mean"] for r in runs if r["ssim"].get("mean") is not None]
    shares = [r["ssim"]["below_floor_share"] for r in runs if r["ssim"].get("below_floor_share") is not None]
    psnrs = [r["psnr"]["mean_db"] for r in runs if r["psnr"].get("mean_db") is not None]
    credits = [r["app_credits"] for r in runs if r.get("app_credits") is not None]
    return {"runs": len(runs), "wall_s_median": statistics.median(r["wall_s"] for r in runs),
            "ssim_mean": min(ssim_means) if ssim_means else None,
            "below_floor_share": max(shares) if shares else None,
            "psnr_mean_db": round(min(psnrs), 3) if psnrs else None,
            "frames_match": all(r["frames_match"] for r in runs),
            "app_credits": sum(credits) if credits else None}


def _fidelity(s: dict, rule: dict) -> list[str]:
    """What keeps an arm below the fidelity floor (empty when it clears it). The worst run counts."""
    out = []
    if s.get("ssim_mean") is None or s["ssim_mean"] < rule["ssim_mean_min"]:
        out.append(f"mean SSIM {s.get('ssim_mean')} is under {rule['ssim_mean_min']}")
    if s.get("below_floor_share") is None or s["below_floor_share"] > rule["ssim_below_floor_max_share"]:
        out.append(f"{(s.get('below_floor_share') or 0) * 100:.1f}% of frames are under SSIM {rule['ssim_frame_floor']} "
                   f"(at most {rule['ssim_below_floor_max_share'] * 100:.0f}% allowed)")
    if rule.get("frames_must_match") and not s.get("frames_match"):
        out.append("its frame count doesn't match the cut's")
    return out


def decide(protocol: dict, data: dict, *, stale: list[str] | None = None) -> dict:
    """Judge bound records; report supplies file freshness. Invalid or unreviewed evidence stays incomplete."""
    problems = _protocol_problems(protocol)
    rule = {**RULE, **protocol["rule"]} if not problems else deepcopy(RULE)
    raw_runs = data.get("runs", [])
    runs = []
    if not isinstance(raw_runs, list):
        problems.append("measurements are not a list of bound records")
    elif not problems:
        for i, run in enumerate(raw_runs):
            faults = _run_problems(run, protocol)
            problems += [f"measurement {i + 1}: {p}" for p in faults]
            if not faults:
                runs.append(run)
    arms = {arm: _arm_summary([r for r in runs if r["arm"] == arm]) for arm in ARMS}
    raw_reviews, latest = data.get("reviews", []), {}
    if not isinstance(raw_reviews, list):
        problems.append("reviews are not a list of bound records")
    else:
        for i, row in enumerate(raw_reviews):
            if (not isinstance(row, dict) or row.get("binding") != _binding(protocol)
                    or not _digest(row.get("cut_sha256")) or row.get("arm") not in ARMS
                    or row.get("verdict") not in VERDICTS or not isinstance(row.get("by"), str) or not row["by"].strip()
                    or not isinstance(row.get("at"), str) or not row["at"]):
                problems.append(f"review {i + 1} has stale or legacy unbound cut/protocol evidence")
                continue
            if not any(r["arm"] == row["arm"] and r["cut_sha256"] == row["cut_sha256"] for r in runs):
                problems.append(f"review {i + 1} does not name a measured cut")
                continue
            if row["arm"] == "higgsedit":
                latest.pop(row["cut_sha256"], None)
                latest[row["cut_sha256"]] = row
    reviews = list(latest.values())
    review = reviews[-1] if reviews else None
    missing = [f"{arm}: at least {rule['runs_min']} measured run" for arm in ARMS if arms[arm]["runs"] < rule["runs_min"]]
    cut_hashes = {r["cut_sha256"] for r in runs if r["arm"] == "higgsedit"}
    remotion_hashes = sorted({r["cut_sha256"] for r in runs if r["arm"] == "remotion"})
    if not cut_hashes:
        missing.append("higgsedit: a person's review of the channel cut beside the master")
    for digest in sorted(cut_hashes):
        if digest not in latest:
            missing.append("higgsedit: a person's review of the channel cut beside the master"
                           + (f" (cut {digest})" if len(cut_hashes) > 1 else ""))
        elif latest[digest].get("remotion_cut_sha256s") != remotion_hashes:
            problems.append(f"review of cut {digest} does not bind the current Remotion cuts")
    problems += stale or []
    for arm in ARMS:
        if arms[arm].get("app_credits") is not None and not _number(arms[arm]["app_credits"]):
            problems.append(f"{arm}: total app credits are not finite and nonnegative")
            arms[arm]["app_credits"] = None
    out = {"verdict": "incomplete", "arms": arms, "review": review, "reviews": reviews,
           "missing": missing, "reasons": problems,
           "speedup": None, "rule": {k: v for k, v in rule.items() if k != "why"}}
    if arms["remotion"]["runs"] and arms["higgsedit"]["runs"]:
        if all(_number(arms[a]["wall_s_median"], positive=True) for a in ARMS):
            speedup = arms["remotion"]["wall_s_median"] / arms["higgsedit"]["wall_s_median"]
            if math.isfinite(speedup):
                out["speedup"] = speedup
            else:
                problems.append("measured speedup is not finite")
        else:
            problems.append("measured wall-time median is not finite and positive")
            for arm in ARMS:
                if not _number(arms[arm]["wall_s_median"], positive=True):
                    arms[arm]["wall_s_median"] = None
    if missing or problems:
        return out
    harness = _fidelity(arms["remotion"], rule)
    if harness:
        out["reasons"] = [f"the Remotion arm fails its own fidelity floor ({'; '.join(harness)}), so the harness is "
                          "wrong: fix it before judging"]
        return out
    reasons = []
    if out["speedup"] < rule["speedup_min"]:
        reasons.append(f"not genuinely faster: {out['speedup']:g}x the Remotion arm's speed, under {rule['speedup_min']:g}x")
    reasons += [f"fidelity: {r}" for r in _fidelity(arms["higgsedit"], rule)]
    for row in reviews:
        if row["verdict"] != rule["review_needed"]:
            reasons.append(f"{row['by']} reviewed the Higgsedit cut as {row['verdict']} (cut {row['cut_sha256']})")
    out["reasons"] = reasons
    out["verdict"] = "drop" if reasons else "keep"
    return out


# ---------------------------------------------------------------------------------------------------------------
# The report


def _fmt(v, unit: str = "", digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def render(protocol: dict, data: dict, verdict: dict) -> str:
    """Refuse malformed display metadata before rewriting any derived report."""
    for key in ("format", "cut", "rule", "channel", "arms", "measure"):
        if not isinstance(protocol.get(key), dict):
            raise UsageError(f"Benchmark protocol {key} is malformed; its evidence is preserved.")
    if not all(isinstance(protocol.get(k), str) for k in ("question", "decision")):
        raise UsageError("Benchmark protocol display text is malformed; its evidence is preserved.")
    if protocol.get("film") is not None and not isinstance(protocol["film"], dict):
        raise UsageError("Benchmark protocol film metadata is malformed; its evidence is preserved.")
    for spec in protocol["arms"].values():
        if (not isinstance(spec, dict) or not isinstance(spec.get("steps"), list)
                or not all(isinstance(s, str) for s in spec["steps"])
                or not all(isinstance(spec.get(k), str) for k in ("route", "cost"))):
            raise UsageError("Benchmark protocol arm metadata is malformed; its evidence is preserved.")
    if not all(isinstance(protocol["measure"].get(k), str) for k in ("quality", "record", "review_command")):
        raise UsageError("Benchmark protocol measurement metadata is malformed; its evidence is preserved.")
    ranges = protocol["cut"].get("ranges")
    if ranges is not None and (not isinstance(ranges, list) or not all(isinstance(r, list) and len(r) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in r) for r in ranges)):
        raise UsageError("Benchmark protocol frame ranges are malformed; its evidence is preserved.")
    rule = protocol.get("rule") or RULE
    film = protocol.get("film") or {}
    cut = protocol.get("cut") or {}
    fmt = protocol.get("format") or {}
    lines = ["# Higgsedit benchmark", "",
             f"{protocol['question']} {protocol['decision']}", ""]
    status = {"keep": "**Verdict: keep.** Higgsedit wins on every count: it becomes the fast route for social cut-downs.",
              "drop": "**Verdict: drop.** Higgsedit doesn't win, so Remotion cut-downs stay the fast route.",
              "incomplete": "**Status: not decided.**"}[verdict["verdict"]]
    lines.append(status)
    if verdict["verdict"] == "incomplete":
        if not data.get("runs"):
            lines += ["", f"Not run: needs Quinn. {REFUSAL}", "", f"Next: {REFUSAL_HINT}"]
        for m in verdict["missing"]:
            lines.append(f"- Missing: {m}.")
    for r in verdict["reasons"]:
        lines.append(f"- {r}.")
    lines += ["", "## Protocol", ""]
    lines.append(f"- **Film:** {film.get('id') or 'not chosen yet (plan with --film)'}"
                 + (f", {film.get('width')} x {film.get('height')} at {film.get('fps_num')}/{film.get('fps_den')} fps" if film else "")
                 + (f"; master {Path(protocol['master']).name}" if protocol.get("master") else "; no master yet (slate master)") + ".")
    lines.append(f"- **Channel:** {protocol['channel'].get('label', 'unspecified')} (social route), in the {fmt.get('name')} format at "
                 f"{fmt.get('aspect')}, {fmt.get('width')} x {fmt.get('height')}.")
    frames = f", {cut['frames']} frames: {', '.join(f'{a}-{b}' for a, b in cut['ranges'])}" if cut.get("ranges") else ""
    lines.append(f"- **The cut:** {cut.get('seconds', 0):g} s of the master{frames}. The same frames for both arms.")
    for arm, spec in protocol["arms"].items():
        commands = [s for s in spec["steps"] if s.startswith("slate ")]
        prose = [s for s in spec["steps"] if not s.startswith("slate ")]
        how = (", then ".join(f"`{c}`" for c in commands) + ".") if commands else ""
        how = " ".join(x for x in (how, *prose) if x)
        lines.append(f"- **Arm {arm}** ({spec['route']}): {how} Cost: {spec['cost']}.")
    quality_text = protocol["measure"]["quality"]
    lines.append(f"- **Measure each run:** `{protocol['measure']['record']}`. {quality_text[:1].upper()}{quality_text[1:]}.")
    lines.append(f"- **Review:** `{protocol['measure']['review_command']}`.")
    lines.append(f"- **Runs:** at least {rule['runs_min']} per arm, {rule['runs_recommended']} recommended; the median wall time counts.")
    lines += ["", "## The decision rule", "",
              "Keep Higgsedit as the fast route for social cut-downs only if all three hold:", "",
              f"1. **Genuinely faster:** its median wall time is at most the Remotion arm's divided by {rule['speedup_min']:g}.",
              f"2. **Above the fidelity floor:** mean SSIM against the master at least {rule['ssim_mean_min']:g}, no more than "
              f"{rule['ssim_below_floor_max_share'] * 100:.0f}% of frames under {rule['ssim_frame_floor']:g}, and the same frame count.",
              "3. **Acceptable:** a person reviewed its channel cut beside the master and said acceptable.", "",
              "Otherwise it's dropped. Missing measurements mean no decision, never a keep.", ""]
    for key in ("speed", "fidelity", "review", "harness"):
        lines.append(f"- {RULE['why'][key]}")
    lines += ["", "## Results", "", "| Arm | Runs | Median wall time | Mean SSIM (worst run) | Mean PSNR | Frames match | App credits |",
              "|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        s = verdict["arms"][arm]
        if not s["runs"]:
            lines.append(f"| {arm} | 0 | - | - | - | - | - |")
            continue
        lines.append(f"| {arm} | {s['runs']} | {_fmt(s['wall_s_median'], ' s', 1)} | {_fmt(s['ssim_mean'], '', 4)} | "
                     f"{_fmt(s['psnr_mean_db'], ' dB', 2)} | {'yes' if s['frames_match'] else 'no'} | {_fmt(s['app_credits'])} |")
    if verdict.get("speedup"):
        lines.append(f"\nHiggsedit ran {verdict['speedup']:g}x the Remotion arm's speed.")
    lines.append("")
    for review in verdict.get("reviews") or []:
        lines.append(f"Review: {review['by']} said {review['verdict']} on {review['at'][:10]} for cut {review['cut_sha256']}"
                     + (f" ({review['note']})" if review.get("note") else "") + ".")
    if not verdict.get("reviews"):
        lines.append("Review: none yet.")
    lines += ["", "## Background", "", BACKGROUND, ""]
    return "\n".join(lines)


def report(folder: Path) -> dict:
    """Recompute the verdict and rewrite REPORT.md and report.json. Returns the verdict."""
    protocol, data = load(folder)
    verdict = decide(protocol, data, stale=_freshness(protocol, data))
    text = render(protocol, data, verdict)
    jsonio.write_atomic(folder / "report.json", {"generated": "do not edit by hand: slate bench higgsedit",
                                                  "bench": BENCH, "version": VERSION, **verdict})
    (folder / "REPORT.md").write_text(text, encoding="utf-8")
    return verdict
