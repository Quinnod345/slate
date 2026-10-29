"""A listening file of the hardest-ridden accents: each in context, as mixed before and as mixed now.

When the mix's guards ride hits down to keep every word clear (sound/mix/rides.json: one clip gain per hit), the owner
hears the trade before he signs it. This writes one short video, from 2.5 s before each hit to 3 s after: first the
moment as it was before that hit's ride (the mix plus the difference the ride made, nothing else changed), then as it
is now. The picture is the film's own (its latest master), or black with the same labels where no master is on this
machine, so the file always plays. Close hits share an excerpt.

    listen.ridden_accents(film_dir, n=9) -> {"file": ..mp4, "index": ..json, "excerpts": [...]}

It reads the mixer's exact per-hit difference files: each contains the hit before its adaptive ride minus the hit
afterward, including its own reverb and the final master processing. This preserves overlapping sounds and spectral
rides. A missing or changed difference file requires another mix; a family stem cannot reconstruct it safely.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import gates, jsonio, log, procs
from slate.errors import MissingPrerequisite, UsageError
from slate.sound import audio
from slate.sound.film import MIX_DIR, Film, load

BEFORE_S, AFTER_S = 2.5, 3.0  # each excerpt: from 2.5 s before the hit to 3 s after
FAMILIES = ("accents",)  # the rides worth hearing one by one: accents on words (the owner's "in between")
OUT_DIR = f"{MIX_DIR}/listen"
SIZE = (1280, 720)


def _excerpts(rides: list[dict], n: int, film_s: float) -> list[dict]:
    """The n deepest rides, merged into excerpts where their windows overlap, in film order."""
    deepest = sorted(rides, key=lambda r: float(r.get("depth_db") or 0.0))[:n]
    out: list[dict] = []
    for r in sorted(deepest, key=lambda r: float(r["t"])):
        a, b = max(0.0, float(r["t"]) - BEFORE_S), min(film_s, float(r.get("end", r["t"])) + AFTER_S)
        if out and a <= out[-1]["to"]:
            out[-1]["to"] = max(out[-1]["to"], b)
            out[-1]["rides"].append(r)
        else:
            out.append({"from": round(a, 3), "to": round(b, 3), "rides": [r]})
    return out


def _master(film: Film) -> Path | None:
    """The film's newest master video on this machine (the picture the excerpts play over), or None."""
    rec = jsonio.read(film.path("masters/master.json"), default=None) or {}  # slate.finish's master record
    files = [film.path(m["file"]) for m in rec.get("masters") or [] if isinstance(m, dict) and m.get("file")]
    files += sorted(film.path("masters").glob("**/*.mov")) + sorted(film.path("masters").glob("**/*.mp4"))
    files = [p for p in files if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _ride_delta(film: Film, ride: dict) -> tuple[Path, int, int]:
    """Validate the exact difference for one hit; return its file, film start and sample count."""
    hint = "Run `slate mix` again to rebuild the exact per-hit listening deltas."
    delta = ride.get("delta")
    if not isinstance(delta, dict) or not isinstance(delta.get("file"), str) or not delta.get("sha256"):
        raise MissingPrerequisite(f"Ride {ride.get('hit')!r} has no verified per-hit delta.", hint=hint)
    relative = Path(delta["file"])
    path = film.path(relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(film.dir.resolve()):
        raise MissingPrerequisite("A listening delta must be a file inside this film.", hint=hint)
    start = delta.get("start_sample")
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise MissingPrerequisite("A listening delta has an invalid start sample.", hint=hint)
    if not path.is_file() or jsonio.sha256(path) != delta["sha256"]:
        raise MissingPrerequisite(f"Ride {ride.get('hit')!r} has a missing or changed per-hit delta.", hint=hint)
    try:
        info = audio.wav_info(path)
    except (OSError, ValueError, UsageError) as error:
        # A checksum alone cannot establish that the bytes are usable audio.
        raise MissingPrerequisite("A listening delta is not a readable WAV file.", hint=hint) from error
    if info.sample_rate != film.sr or info.channels != 2 or info.frames <= 0 or start + info.frames > film.n:
        raise MissingPrerequisite("A listening delta's rate, channels or bounds do not match this film.", hint=hint)
    return path, start, info.frames


def _before_excerpt(now: np.ndarray, start: int, deltas: list[tuple[Path, int, int]], sr: int) -> np.ndarray:
    """Add only the selected hits' exact differences, including tails outside their dry cue spans."""
    before = np.array(now, copy=True)
    end = start + len(before)
    for path, at, size in deltas:
        a, b = max(start, at), min(end, at + size)
        if b <= a:
            continue
        delta, rate = audio.read_wav(path, a - at, b - a)
        if rate != sr or delta.shape != (b - a, 2) or not np.isfinite(delta).all():
            raise MissingPrerequisite("A listening delta changed or contains invalid audio.",
                                      hint="Run `slate mix` again to rebuild the listening deltas.")
        before[a-start:b-start] += delta
    return before


def _label(text: str, sub: str, path: Path, size: tuple[int, int]) -> Path:
    """A transparent label strip for the bottom of the picture: what's playing, and the hit."""
    from PIL import Image  # noqa: PLC0415

    from slate.voice import sheet  # noqa: PLC0415  (its fonts draw every character they show)

    w, h = size[0], max(48, size[1] // 9)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 170))
    d = sheet.draw(img)
    d.text((16, 6), text, fill=(255, 255, 255, 255), font=sheet.F_HEAD)
    d.text((16, 6 + 24), sub, fill=(210, 210, 210, 255), font=sheet.F_SMALL)
    img.save(path)
    return path


def _segment(out: Path, wav: Path, label: Path, seconds: float, picture: Path | None, start: float, fps: str,
             size: tuple[int, int]) -> Path:
    """One excerpt version as an H.264/AAC clip: the picture (or black), the label strip at the bottom, the audio."""
    w, h = size
    if picture is not None:
        video_in = ["-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(picture)]
        scale = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                 f"fps={fps}[pic]")
    else:
        video_in = ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", f"color=c=black:s={w}x{h}:r={fps}"]
        scale = "[0:v]null[pic]"
    argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", *video_in, "-i", str(label), "-i", str(wav),
            "-filter_complex", f"{scale};[pic][1:v]overlay=0:H-h[v]", "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "256k",
            "-t", f"{seconds:.3f}", str(out)]
    procs.run(argv, check=True, timeout=600)
    return out


def ridden_accents(film_dir: str | Path, *, n: int = 9, out: str | Path | None = None,
                   size: tuple[int, int] = SIZE) -> dict:
    """Write the listening file of the n hardest-ridden accents (L-sound-S030). Returns its record."""
    from slate.sound import balance  # noqa: PLC0415

    film = load(film_dir)
    if not procs.which("ffmpeg"):
        raise MissingPrerequisite("The listening file is made with ffmpeg, which isn't installed.",
                                  hint="slate doctor --install")
    bal = balance.palette_balance(film.palette)
    rides = [r for r in balance.read_rides(film, balance.cue_hits(film, bal))["rides"]
             if r.get("family") in FAMILIES and not r.get("pinned") and r.get("t") is not None
             and float(r.get("depth_db") or 0.0) < 0.0]
    if not rides:
        raise MissingPrerequisite("Nothing was ridden: sound/mix/rides.json lists no ridden accents.",
                                  hint="The mix's guards write it as they ride; there's nothing to hear until then.")
    mix_path = film.path(f"{MIX_DIR}/final_mix.wav")
    if not mix_path.exists():
        raise MissingPrerequisite(f"{film.id} hasn't been mixed yet.", hint="Run `slate mix` first.")
    record = jsonio.read(film.path(f"{MIX_DIR}/rides.json"), default={}) or {}
    if record.get("mix_sha256") != jsonio.sha256(mix_path):
        raise MissingPrerequisite("The listening deltas are missing a current mix binding or belong to an older mix.",
                                  hint="Run `slate mix` again to rebuild the exact per-hit listening deltas.")
    if len({r.get("hit") for r in rides}) != len(rides):
        raise MissingPrerequisite("The rides record repeats a hit, so its listening delta would be added twice.",
                                  hint="Run `slate mix` again to rebuild the rides record.")
    excerpts = _excerpts(rides, n, film.duration_s)
    deltas = {r["hit"]: _ride_delta(film, r) for ex in excerpts for r in ex["rides"]}
    folder = film.path(out) if out else film.path(OUT_DIR)
    work = folder / "parts"
    work.mkdir(parents=True, exist_ok=True)
    sr = film.sr
    picture = _master(film)
    fps = f"{film.fps_num}/{film.fps_den}"
    clips, rows = [], []
    for k, ex in enumerate(excerpts, 1):
        a, b = int(round(ex["from"] * sr)), int(round(ex["to"] * sr))
        now, _ = audio.read_wav(mix_path, a, b - a)
        now = audio.to_stereo(now)
        before = _before_excerpt(now, a, [deltas[r["hit"]] for r in ex["rides"]], sr)
        hits = ", ".join(f"{r.get('hit')} at {float(r['t']):.2f} s ({float(r['depth_db']):+.1f} dB"
                         + (", half depth" if r.get("half_depth") else "") + ")" for r in ex["rides"])
        secs = (b - a) / sr
        for tag, x, what in (("before", before, "before: as mixed before the ride"),
                             ("now", now, "now: as mixed, ridden")):
            wav = audio.write_wav(work / f"{k:02d}_{tag}.wav", x, sr, 24)
            lab = _label(f"{k}/{len(excerpts)} · {what}", f"{film.id} · {ex['from']:.2f}-{ex['to']:.2f} s · {hits}",
                         work / f"{k:02d}_{tag}.png", size)
            clips.append(_segment(work / f"{k:02d}_{tag}.mp4", wav, lab, secs, picture, ex["from"], fps, size))
        rows.append({"excerpt": k, "from": ex["from"], "to": ex["to"], "rides": ex["rides"],
                     "deepest_ride_db": min(float(r["depth_db"]) for r in ex["rides"])})
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    final = folder / "ridden_accents.mp4"
    procs.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
               str(final)], check=True, timeout=600, cwd=work)
    rec = {"kind": "listening_file", "film": film.id, "file": film.rel(final), "made_at": gates.now_iso(),
           "picture": film.rel(picture) if picture else "black (no master on this machine)",
           "excerpts": rows, "rides_heard": sum(len(e["rides"]) for e in excerpts),
           "note": "Each excerpt plays twice: first as mixed before its ride (the mix plus what the ride took off "
                   "that hit, nothing else), then as mixed now."}
    jsonio.write_atomic(folder / "ridden_accents.json", jsonio.mark_generated(rec, "slate mix --listen"))
    log.info(f"listening file: {final} ({len(rows)} excerpt(s), {rec['rides_heard']} ride(s))")
    return rec
