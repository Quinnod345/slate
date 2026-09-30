"""Phase 8's benchmark: is Higgsedit a faster route than a Remotion cut-down for social channels, at acceptable quality?

Decision 9 (plan): variants by channel. Hero placements (website, App Store, YouTube masters) are derived films at
master quality through the full pipeline; social cut-downs (Instagram, TikTok, Shorts, LinkedIn) can take a fast
route. Higgsedit, Higgsfield's code-driven motion engine, becomes that route only if this benchmark shows it is
genuinely faster than a Remotion cut-down at acceptable quality. Otherwise Slate drops it.

The protocol (protocol.json, version 1):

- one finished film with a master, one social channel (TikTok unless another is named) and one cut: the channel
  format's target length in the master's own frames, the same frames for both arms;
- arm **remotion**, the fast route today: a derived film (`slate variant`) rendered by Remotion at
  render.draft_samples and mastered with ffmpeg;
- arm **higgsedit**: the same cut built in Higgsedit, in Higgsfield's hosted sandbox, from the film's own words,
  events and assets, driven through the chat lane's Higgsfield connector;
- per run: the wall time from the first command to the finished file, measured by whoever runs it; PSNR and SSIM of
  the cut against the master's frames (slate.bench.quality); the app credits it used;
- a person's REVIEW of the Higgsedit channel cut, watched beside the Remotion cut and the master.

The decision rule is in RULE and in every report. Missing measurements never make a keep: the verdict is
"incomplete" until both arms have runs and a person has reviewed the Higgsedit cut.

Running the Higgsedit arm needs Quinn: Higgsedit bills Higgsfield app credits, a separate pool from the Cloud API
Slate is funded on, and app credits aren't funded. `slate bench higgsedit run` refuses with that reason. Everything
else (the plan, measuring a finished cut, the review, the report) works now.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from slate import __version__, formats, jsonio, paths
from slate.clock import Clock
from slate.errors import MissingPrerequisite, SpendRefused, UsageError
from slate.gates import now_iso

BENCH = "higgsedit"
VERSION = 1
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


def plan(*, film_dir: Path | None = None, channel: str = DEFAULT_CHANNEL, cut_s: float | None = None,
         ranges: str | None = None, master: str | Path | None = None, out: str | Path | None = None) -> dict:
    """Write protocol.json (and the report) for one film and channel. Nothing runs; nothing is spent."""
    ch = formats.channel_key(channel)
    route = formats.route_for(ch)
    if route["route"] != "social":
        raise UsageError(f"{route['label']} is a hero placement: it always gets a derived film at master quality "
                         "through the full pipeline, so there's nothing to benchmark.",
                         hint="Benchmark a social channel: tiktok, instagram_reels, youtube_shorts or linkedin.")
    fmt_name, aspect = social_format_for(ch)
    fmt = formats.load(fmt_name, aspect=aspect)
    seconds = float(cut_s) if cut_s else float(fmt["runtime_s"]["target"])
    film = None
    master_path = Path(master).expanduser().resolve() if master else find_master(film_dir)
    if film_dir:
        fj = jsonio.read(Path(film_dir) / "film.json")
        film = {"id": fj.get("id"), "dir": str(film_dir), "format": fj.get("format"), "fps_num": fj.get("fps_num"),
                "fps_den": fj.get("fps_den"), "width": fj.get("width"), "height": fj.get("height")}
    frames = None
    cut_ranges = _parse_ranges(ranges)
    if film and film.get("fps_num"):
        clock = Clock(int(film["fps_num"]), int(film["fps_den"]))
        frames = clock.frame_of(seconds)
        cut_ranges = cut_ranges or [[0, frames]]
        frames = sum(b - a for a, b in cut_ranges)
    folder = bench_dir(film_dir, out)
    child = f"{film['id']}-bench-remotion" if film else "<film>-bench-remotion"
    film_ref = film["id"] if film else "<film>"
    protocol = {
        "generated": "do not edit by hand: slate bench higgsedit plan",
        "bench": BENCH, "version": VERSION, "slate_version": __version__, "created_at": now_iso(),
        "question": "Is Higgsedit genuinely faster than a Remotion cut-down for a social channel, at acceptable quality?",
        "decision": "Plan decision 9 and phase 8: keep Higgsedit as the fast route for social cut-downs only if it wins.",
        "status": "not run: needs Quinn",
        "needs": [REFUSAL, REFUSAL_HINT],
        "film": film, "master": str(master_path) if master_path else None,
        "channel": {"key": ch, "label": route["label"], "route": route["route"]},
        "format": {"name": fmt_name, "aspect": aspect, "width": fmt["width"], "height": fmt["height"]},
        "cut": {"seconds": seconds, "ranges": cut_ranges, "frames": frames,
                "note": "The master's own frames, the same for both arms. Pass --ranges for a real cut list."},
        "arms": {
            "remotion": {
                "engine": "remotion", "route": "the social fast route: fewer motion-blur samples, an ffmpeg master",
                "steps": [f"slate variant {film_ref} {child} --aspect {aspect} --format {fmt_name} --cut {seconds:g}",
                          f"slate render --film {child}", f"slate master --ffmpeg --film {child}"],
                "timing": "wall time from the first command to the finished file",
                "cost": "nothing: it runs on this Mac",
            },
            "higgsedit": {
                "engine": "higgsedit",
                "route": "Higgsedit in Higgsfield's hosted sandbox, through the chat lane's Higgsfield connector",
                "steps": ["Build the same cut in Higgsedit from the film's own words, events and assets.",
                          "Render it at the channel's size on the master's clock.",
                          "Download the file; note the app credits it used."],
                "timing": "wall time from the first command to the finished file, the same way",
                "cost": "Higgsfield app credits (unfunded)",
            },
        },
        "measure": {
            "wall_time": "per run, recorded with --wall-s; the median of an arm's runs counts",
            "quality": "PSNR and SSIM against the master's frames in the cut's ranges, at the master's size; a "
                       "reframed reference when the cut changes aspect (slate.bench.quality)",
            "review": "a person watches both channel cuts beside the master and records acceptable or not acceptable",
            "record": "slate bench higgsedit measure --arm <arm> --cut <file> --wall-s <seconds> [--app-credits N]",
            "review_command": "slate bench higgsedit review --arm higgsedit --verdict acceptable|not-acceptable --by <name>",
        },
        "rule": RULE,
    }
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
    return jsonio.read(proto_path), jsonio.read(folder / "measurements.json", default={"runs": [], "reviews": []})


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
    if not wall_s or float(wall_s) <= 0:
        raise UsageError("--wall-s is the run's wall time in seconds, from the first command to the finished file.")
    protocol, data = load(folder)
    master_path = master or protocol.get("master")
    if not master_path or not Path(master_path).is_file():
        raise MissingPrerequisite("The benchmark compares each cut with the film's master, and there isn't one yet.",
                                  hint="Make the master first (slate master), or pass --master <file>.")
    if comparer is None:
        from slate.bench import quality  # noqa: PLC0415

        comparer = quality.compare
    q = comparer(cut, master_path, ranges=(protocol.get("cut") or {}).get("ranges"))
    run = {"arm": arm, "wall_s": round(float(wall_s), 3), "cut": str(Path(cut).resolve()),
           "cut_sha256": jsonio.sha256(cut) if Path(cut).is_file() else None, "master": str(master_path),
           "frames": q["frames_cut"], "frames_match": q["frames_match"], "reference": q["reference"],
           "psnr": {"mean_db": q["psnr"]["mean_db"], "min_db": q["psnr"]["min_db"]},
           "ssim": {k: q["ssim"][k] for k in ("mean", "min", "below_floor", "below_floor_share")},
           "app_credits": app_credits, "note": note, "measured_at": now_iso(), "engine": q["engine"]}
    data.setdefault("runs", []).append(run)
    jsonio.write_atomic(folder / "measurements.json", data)
    report(folder)
    return run


def record_review(folder: Path, *, arm: str, verdict: str, by: str, note: str | None = None) -> dict:
    """A person's judgement of an arm's channel cut, watched beside the master."""
    if arm not in ARMS:
        raise UsageError(f"Unknown arm {arm!r}; the arms are {', '.join(ARMS)}.")
    if verdict not in VERDICTS:
        raise UsageError(f"A review is {' or '.join(VERDICTS)}, not {verdict!r}.")
    if not str(by or "").strip():
        raise UsageError("A review needs the name of the person who watched it (--by).")
    _protocol, data = load(folder)
    row = {"arm": arm, "verdict": verdict, "by": str(by).strip(), "note": note, "at": now_iso()}
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
    return {"runs": len(runs), "wall_s_median": round(statistics.median(r["wall_s"] for r in runs), 3),
            "ssim_mean": round(min(ssim_means), 5) if ssim_means else None,
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


def decide(protocol: dict, data: dict) -> dict:
    """The verdict: keep, drop or incomplete, with every reason. Never keep on missing inputs."""
    rule = {**RULE, **(protocol.get("rule") or {})}
    runs = data.get("runs") or []
    arms = {arm: _arm_summary([r for r in runs if r["arm"] == arm]) for arm in ARMS}
    reviews = [r for r in data.get("reviews") or [] if r["arm"] == "higgsedit"]
    review = reviews[-1] if reviews else None
    missing = [f"{arm}: at least {rule['runs_min']} measured run" for arm in ARMS if arms[arm]["runs"] < rule["runs_min"]]
    if review is None:
        missing.append("higgsedit: a person's review of the channel cut beside the master")
    out = {"verdict": "incomplete", "arms": arms, "review": review, "missing": missing, "reasons": [],
           "speedup": None, "rule": {k: v for k, v in rule.items() if k != "why"}}
    if arms["remotion"]["runs"] and arms["higgsedit"]["runs"]:
        out["speedup"] = round(arms["remotion"]["wall_s_median"] / arms["higgsedit"]["wall_s_median"], 3)
    if missing:
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
    if review["verdict"] != rule["review_needed"]:
        reasons.append(f"{review['by']} reviewed the Higgsedit cut as {review['verdict']}")
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
    lines.append(f"- **Channel:** {protocol['channel']['label']} (social route), in the {fmt.get('name')} format at "
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
    review = verdict.get("review")
    lines.append("")
    lines.append(f"Review: {review['by']} said {review['verdict']} on {review['at'][:10]}"
                 + (f" ({review['note']})" if review.get("note") else "") + "." if review else "Review: none yet.")
    lines += ["", "## Background", "", BACKGROUND, ""]
    return "\n".join(lines)


def report(folder: Path) -> dict:
    """Recompute the verdict and rewrite REPORT.md and report.json. Returns the verdict."""
    protocol, data = load(folder)
    verdict = decide(protocol, data)
    jsonio.write_atomic(folder / "report.json", {"generated": "do not edit by hand: slate bench higgsedit",
                                                  "bench": BENCH, "version": VERSION, **verdict})
    (folder / "REPORT.md").write_text(render(protocol, data, verdict), encoding="utf-8")
    return verdict
