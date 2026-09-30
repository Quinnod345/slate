"""Helpers for the generation and capture tests: a neutral film layout, synthetic clips, and a CLI runner."""

from __future__ import annotations

import json
from pathlib import Path

from slate import procs

FPS = (25, 1)
SIZE = (320, 180)

SHOTS = [
    {"id": "S01", "act": 1, "frame_in": 0, "frame_out": 100, "frames": 100, "sec_in": 0.0, "sec_out": 4.0},
    {"id": "S02", "act": 1, "frame_in": 100, "frame_out": 150, "frames": 50, "sec_in": 4.0, "sec_out": 6.0,
     "generate": {"reason": "real-scene", "why": "a real shoreline at dawn",
                  "prompt": "a quiet shoreline at dawn, a slow push in", "duration": 2, "image": "refs/start.png",
                  "audio": True}},
    {"id": "S03", "act": 1, "frame_in": 150, "frame_out": 200, "frames": 50, "sec_in": 6.0, "sec_out": 8.0,
     "generate": {"reason": "adds-something", "why": "an ink bleed between two worlds",
                  "prompt": "ink bleeding into wet paper"}},
    {"id": "S04", "act": 1, "frame_in": 200, "frame_out": 250, "frames": 50, "sec_in": 8.0, "sec_out": 10.0,
     "generate": {"reason": "stock", "prompt": "a busy street from above, stock-style", "role": "plate_still"}},
    {"id": "S05", "act": 1, "frame_in": 250, "frame_out": 300, "frames": 50, "sec_in": 10.0, "sec_out": 12.0,
     "generate": {"reason": "bridge-matte", "why": "paper bleeding into the real plate", "prompt": "ink bleed",
                  "duration": 2, "image": "take:S04"}},
]

WORDS = {"S01": {"take": "voice/S01.wav", "line": "Open the app and tap save.",
                 "words": [{"w": "Open", "start": 0.1, "end": 0.4}, {"w": "the", "start": 0.4, "end": 0.5},
                           {"w": "app", "start": 0.5, "end": 0.9}, {"w": "and", "start": 1.0, "end": 1.2},
                           {"w": "tap", "start": 1.3, "end": 1.6}, {"w": "save.", "start": 2.1, "end": 2.6}]}}


def write_brand(store: Path, *, uploads: bool = True, name: str = "neutral") -> Path:
    pack = {"name": name, "grounding": {"uploads_allowed": uploads},
            "references": {k: {"file": f"refs/{k}.txt", "sha256": "0" * 64, "approved_by": "tester",
                               "approved_at": "2026-09-30"} for k in ("truth", "look_motion", "voice", "sound")}}
    folder = store / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "brand.json").write_text(json.dumps(pack))
    return folder


def write_timing(film: Path, shots: list[dict] | None = None) -> None:
    (film / "timing").mkdir(exist_ok=True)
    rows = shots if shots is not None else SHOTS
    total = max(s["frame_out"] for s in rows)
    (film / "timing" / "shots.json").write_text(json.dumps(
        {"fps_num": FPS[0], "fps_den": FPS[1], "width": SIZE[0], "height": SIZE[1], "total_frames": total,
         "acts": [1], "shots": rows}, indent=1))
    (film / "timing" / "vo_cues.json").write_text(json.dumps({"cues": [{"shot": "S01", "at": 0.4,
                                                                        "file": "voice/S01.wav"}]}))
    (film / "timing" / "vo_words.json").write_text(json.dumps({"shots": WORDS}))


def vfr_clip(path: Path, segments: list[tuple[tuple[int, int, int], float]], size=(64, 36)) -> Path:
    """A variable-frame-rate clip: one still colour per segment, each shown for its seconds (like simctl writes)."""
    from PIL import Image

    folder = path.parent / (path.stem + "_src")
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (colour, seconds) in enumerate(segments):
        Image.new("RGB", size, colour).save(folder / f"c{i}.png")
        lines += [f"file 'c{i}.png'", f"duration {seconds}"]
    lines.append(f"file 'c{len(segments) - 1}.png'")
    (folder / "list.txt").write_text("\n".join(lines) + "\n")
    procs.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(folder / "list.txt"),
               "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def cfr_clip(path: Path, seconds: float, fps: int = 24, size=(64, 36)) -> Path:
    procs.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
               f"testsrc2=size={size[0]}x{size[1]}:rate={fps}:duration={seconds}", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def run_json(argv: list[str]) -> tuple[int, dict | None, str]:
    """Run the CLI in-process with --json; returns (exit code, the JSON object or None, stderr)."""
    from slate.cli import run_cli

    code, out, err = run_cli(["--json", *argv])
    obj = None
    lines = [line for line in out.splitlines() if line.strip()]
    if lines:
        obj = json.loads(lines[-1])
    return code, obj, err
