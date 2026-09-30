"""Scene cuts and changes: where a reference film cuts, where its picture changes, and one legible frame of each.

Generalized from the films' reference analysis (scene_frames.py and ANALYSIS.md's method table):

- every frame gets ffmpeg's scene score (select='gte(scene,0)' with metadata=print, kept in scores/scene_scores.txt),
  measured at SCORE_WIDTH so a long film scores in a minute or two;
- a hard cut scores CUT (0.4) or more; a softer change (a slide swap, a build step) CHANGE (0.08) or more; peaks
  closer than half a second are one change, and the strongest wins;
- each change's frame is grabbed half a second after it, so the incoming state is legible rather than
  mid-transition, and each scene also gets a settled frame from its middle; both go on contact sheets;
- a Higgsfield video analysis (0 credits; it runs in the chat lane, through Higgsfield's hosted connector) can be
  attached with --higgsfield. Each of its scene starts snaps to the strongest ffmpeg peak within 1.5 s when that
  peak clears 0.004, and the ffmpeg changes it didn't claim are added. Its scenes bring labels, shot types and visual
  descriptions. Its per-scene "audio" text can paraphrase the on-screen words instead of the voice, so the voice-over
  always comes from Whisper.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from slate import jsonio, procs
from slate.clock import Clock
from slate.errors import UsageError
from slate.gates import GateResult
from slate.qa import media

SCORE_WIDTH = 640  # px wide for scoring: sensitive enough for soft changes, quick on a long film
CUT = 0.4  # a hard cut
CHANGE = 0.08  # a softer visual change (the films kept these as "visual-only changes")
MERGE_S = 0.5  # peaks closer than this are one change
GRAB_AFTER_S = 0.5  # a change's frame is grabbed this long after it, once the new state is legible
SNAP_S = 1.5  # a Higgsfield boundary looks this far either side for an ffmpeg peak
SNAP_MIN = 0.004  # ... which must score at least this to move it
CLAIM_S = 1.0  # an ffmpeg change within this of a Higgsfield boundary belongs to it
SCORES_FILE = "scene_scores.txt"
THUMB_WIDTH = 320
SHEET_COLUMNS = 5


# ---------------------------------------------------------------------------------------------------------------
# Scores and peaks


def scene_scores(video: Path, out_dir: Path, *, duration_s: float | None = None) -> list[dict]:
    """[{frame, t, score}] for every frame, written to out_dir/scene_scores.txt as ffmpeg printed it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    media.require("ffmpeg")
    vf = f"scale=w='min({SCORE_WIDTH},iw)':h=-2,select='gte(scene,0)',metadata=print:file={SCORES_FILE}"
    timeout = max(900.0, 4.0 * float(duration_s or 0))
    procs.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(Path(video).resolve()), "-an", "-sn",
               "-vf", vf, "-f", "null", "-"], cwd=out_dir, check=True, timeout=timeout)
    return parse_scores((out_dir / SCORES_FILE).read_text(encoding="utf-8", errors="replace"))


_FRAME = re.compile(r"frame:(\d+)\s+pts:\S+\s+pts_time:([-\d.eE+]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([-\d.eE+]+)")


def parse_scores(text: str) -> list[dict]:
    """ffmpeg's metadata=print output as [{frame, t, score}] (the films' scene_scores_all.txt shape)."""
    rows: list[dict] = []
    current = None
    for line in text.splitlines():
        m = _FRAME.search(line)
        if m:
            current = (int(m.group(1)), float(m.group(2)))
            continue
        s = _SCORE.search(line)
        if s and current is not None:
            rows.append({"frame": current[0], "t": round(current[1], 6), "score": float(s.group(1))})
            current = None
    return rows


def peaks(scores: list[dict], threshold: float, *, merge_s: float = MERGE_S) -> list[dict]:
    """The strongest score in each cluster of scores at or above `threshold` (clusters: gaps under merge_s)."""
    above = [r for r in scores if r["score"] >= threshold and r["frame"] > 0]
    groups: list[list[dict]] = []
    for r in above:
        if groups and r["t"] - groups[-1][-1]["t"] <= merge_s:
            groups[-1].append(r)
        else:
            groups.append([r])
    out = []
    for g in groups:
        best = max(g, key=lambda r: r["score"])
        out.append({**best, "kind": "cut" if best["score"] >= CUT else "change", "source": "ffmpeg"})
    return out


# ---------------------------------------------------------------------------------------------------------------
# Higgsfield's scene list


def _timestamp(value) -> float:
    """"1:07" → 67 s, "1:02:03" → 3723 s, "0:04.5" → 4.5 s; numbers pass through."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    parts = str(value or "").strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise UsageError(f"{value!r} isn't a timestamp (M:SS or H:MM:SS).") from exc
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def parse_higgsfield(path: str | Path) -> dict:
    """The scene list from a Higgsfield video analysis JSON. Storage URLs in it are never copied."""
    data = jsonio.read(path)
    result = data.get("result", data) if isinstance(data, dict) else {}
    scenes = result.get("scenes") if isinstance(result, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise UsageError(f"{path} isn't a Higgsfield video analysis: it has no scenes.",
                         hint="Save the result of Higgsfield's video_analysis_create (via video_analysis_status) as JSON.")
    out = []
    for i, sc in enumerate(scenes, 1):
        if not isinstance(sc, dict):
            continue
        out.append({"scene_number": sc.get("scene_number", i), "start": _timestamp(sc.get("timestamp_start")),
                    "end": _timestamp(sc.get("timestamp_end")), "label": sc.get("label"), "visual": sc.get("visual"),
                    "shot_type": sc.get("shot_type"), "audio_summary": sc.get("audio")})
    out.sort(key=lambda s: s["start"])
    return {"id": result.get("id"), "status": result.get("status"), "scenes": out, "file": str(path)}


def snap_to_scores(hf: dict, scores: list[dict], changes: list[dict]) -> list[dict]:
    """Boundaries from Higgsfield's scene starts, each moved to the strongest nearby ffmpeg peak, plus the ffmpeg
    changes none of them claimed.

    A boundary looks SNAP_S either side, but never past the halfway point to its neighbours: two scenes that start a
    second apart would otherwise both snap to whichever of their cuts scores higher."""
    rows = []
    starts = [sc["start"] for sc in hf["scenes"]]
    for i, sc in enumerate(hf["scenes"]):
        s = sc["start"]
        lo = max(s - SNAP_S, (starts[i - 1] + s) / 2 if i else s - SNAP_S)
        hi = min(s + SNAP_S, (s + starts[i + 1]) / 2 if i + 1 < len(starts) else s + SNAP_S)
        window = [r for r in scores if lo <= r["t"] <= hi and r["frame"] > 0]
        best = max(window, key=lambda r: r["score"]) if window else None
        moved = best is not None and best["score"] >= SNAP_MIN
        t = best["t"] if moved else s
        score = best["score"] if best else 0.0
        rows.append({"t": 0.0 if s <= 0 else t, "frame": best["frame"] if moved and s > 0 else None, "score": score,
                     "kind": "cut" if score >= CUT else "change", "source": "higgsfield", "higgsfield": sc,
                     "snapped": bool(moved and s > 0)})
    extra = [dict(c) for c in changes if all(abs(c["t"] - r["t"]) > CLAIM_S for r in rows)]
    return sorted(rows + extra, key=lambda r: r["t"])


# ---------------------------------------------------------------------------------------------------------------
# Scenes and frames


def build_scenes(boundaries: list[dict], duration_s: float) -> list[dict]:
    """Scenes between boundaries (t > 0 starts a scene); a boundary at 0 only lends scene 1 its Higgsfield details."""
    starts = [b for b in boundaries if b["t"] > 0 and b["t"] < duration_s]
    opening = next((b for b in boundaries if b["t"] <= 0), None)
    edges = [0.0, *[b["t"] for b in starts], float(duration_s)]
    scenes = []
    for i in range(len(edges) - 1):
        start, end = edges[i], edges[i + 1]
        if end - start <= 0:
            continue
        b = starts[i - 1] if i else opening
        scene = {"id": len(scenes) + 1, "start": round(start, 3), "end": round(end, 3), "dur": round(end - start, 3),
                 "starts_with": (b or {}).get("kind", "start") if i else "start",
                 "peak_score": round(b["score"], 4) if (b and i) else None,
                 "change_t": round(start + min(GRAB_AFTER_S, (end - start) / 2), 3),
                 "settled_t": round((start + end) / 2, 3)}
        if b and b.get("higgsfield"):
            hf = b["higgsfield"]
            scene["higgsfield"] = {k: hf.get(k) for k in ("scene_number", "label", "shot_type", "visual", "audio_summary")}
            if i:
                scene["snapped"] = b.get("snapped", False)
        scenes.append(scene)
    return scenes


def grab_frames(video: Path, info: dict, scenes: list[dict], out_dir: Path) -> None:
    """One change frame and one settled frame per scene, as JPEGs at the film's own size (OCR reads them)."""
    from PIL import Image  # noqa: PLC0415

    clock = Clock(info["fps_num"], info["fps_den"])
    last = max(0, int(info.get("frames") or 1) - 1)
    for folder in ("changes", "settled"):
        (out_dir / folder).mkdir(parents=True, exist_ok=True)
        for stale in (out_dir / folder).glob("s[0-9][0-9][0-9]_*s.jpg"):  # an earlier run's frames, by our own names
            stale.unlink()
    for sc in scenes:
        picks = {"change": min(last, clock.frame_of(sc["change_t"])), "settled": min(last, clock.frame_of(sc["settled_t"]))}
        frames = media.read_frames(video, picks.values(), pix_fmt="rgb24", info=info)
        for kind, idx in picks.items():
            arr = frames.get(idx)
            if arr is None:
                continue
            t = sc["change_t"] if kind == "change" else sc["settled_t"]
            rel = Path("changes" if kind == "change" else "settled") / f"s{sc['id']:03d}_{t:09.3f}s.jpg"
            Image.fromarray(arr).save(out_dir / rel, quality=90)
            sc[f"{kind}_frame"] = str(Path("frames") / rel)


def contact_sheet(items: list[tuple[str, Path]], out: Path) -> Path | None:
    """A grid of labelled thumbnails (the films' scene_changes_sheet.jpg)."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    thumbs = []
    for label, path in items:
        if not path.is_file():
            continue
        with Image.open(path) as im:
            im = im.convert("RGB")
            h = max(1, int(round(im.height * THUMB_WIDTH / float(im.width))))
            thumbs.append((label, im.resize((THUMB_WIDTH, h))))
    if not thumbs:
        return None
    th = max(im.height for _, im in thumbs)
    label_h = 18
    cols = min(SHEET_COLUMNS, len(thumbs))
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * THUMB_WIDTH, rows * (th + label_h)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    for k, (label, im) in enumerate(thumbs):
        x, y = (k % cols) * THUMB_WIDTH, (k // cols) * (th + label_h)
        sheet.paste(im, (x, y))
        draw.text((x + 4, y + th + 3), label, fill=(232, 232, 232))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def _mmss(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}"


# ---------------------------------------------------------------------------------------------------------------
# The step


def run(video: Path, meta: dict, out_dir: Path, *, higgsfield: str | Path | None = None,
        grab: bool = True) -> tuple[GateResult, dict]:
    """Score every frame, find cuts and changes, build the scenes, grab their frames. (result, data)."""
    info = meta["video"]
    duration = float(meta.get("duration_s") or 0)
    scores = scene_scores(video, out_dir / "scores", duration_s=duration)
    changes = peaks(scores, CHANGE)
    hard = [c for c in changes if c["kind"] == "cut"]
    hf = parse_higgsfield(higgsfield) if higgsfield else None
    boundaries = snap_to_scores(hf, scores, changes) if hf else changes
    scenes = build_scenes(boundaries, duration)
    sheets = {}
    if grab and scenes:
        grab_frames(video, info, scenes, out_dir / "frames")
        for kind in ("change", "settled"):
            items = [(f"S{sc['id']:02d} {_mmss(sc[f'{kind}_t'])}", out_dir / sc[f"{kind}_frame"])
                     for sc in scenes if sc.get(f"{kind}_frame")]
            sheet = contact_sheet(items, out_dir / "frames" / f"{kind}_sheet.jpg")
            if sheet:
                sheets[kind] = str(sheet.relative_to(out_dir))
    soft = [c for c in changes if c["kind"] == "change"]
    longest = max(scenes, key=lambda s: s["dur"]) if scenes else None
    summary = (f"{len(scenes)} scene{'s' if len(scenes) != 1 else ''}: {len(hard)} hard cut{'s' if len(hard) != 1 else ''} "
               f"and {len(soft)} softer change{'s' if len(soft) != 1 else ''}")
    if hf:
        summary += f"; Higgsfield's {len(hf['scenes'])} scenes joined"
    engine = media.engine("scene score", width=SCORE_WIDTH, cut=CUT, change=CHANGE)
    evidence = [str(Path("scores") / SCORES_FILE), *sheets.values()]
    data = {
        "frames_scored": len(scores),
        "thresholds": {"cut": CUT, "change": CHANGE, "merge_s": MERGE_S, "score_width": SCORE_WIDTH},
        "scores_file": str(Path("scores") / SCORES_FILE),
        "hard_cuts": [{"t": c["t"], "frame": c["frame"], "score": round(c["score"], 4)} for c in hard],
        "changes": [{"t": c["t"], "frame": c["frame"], "score": round(c["score"], 4)} for c in soft],
        "scenes": scenes,
        "sheets": sheets,
        "average_scene_s": round(sum(s["dur"] for s in scenes) / len(scenes), 3) if scenes else None,
        "longest_scene": {"id": longest["id"], "dur": longest["dur"]} if longest else None,
    }
    if hf:
        data["higgsfield"] = {"id": hf["id"], "status": hf["status"], "scenes": len(hf["scenes"]), "file": hf["file"],
                              "snapped": sum(1 for b in boundaries if b.get("snapped")),
                              "ffmpeg_only": sum(1 for b in boundaries if b.get("source") == "ffmpeg")}
    return GateResult.pass_("analyze.cuts", summary, evidence=evidence, engine=engine), data
