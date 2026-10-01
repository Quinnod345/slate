"""A listening file of the hardest-ridden accents: each in context, as mixed before and as mixed now.

When the mix's guards ride hits down to keep every word clear (sound/mix/rides.json: one clip gain per hit), the owner
hears the trade before he signs it. This writes one short video, from 2.5 s before each hit to 3 s after: first the
moment as it was before that hit's ride (the mix plus the difference the ride made, nothing else changed), then as it
is now. The picture is the film's own (its latest master), or black with the same labels where no master is on this
machine, so the file always plays. Close hits share an excerpt.

    listen.ridden_accents(film_dir, n=9) -> {"file": ..mp4, "index": ..json, "excerpts": [...]}

It reads the accent family's own stem (sound/mix/family_stems/accents.wav): a ride is a gain on one hit, so the hit
alone is what's added back. A mix without family stems has nothing to take a hit from, and says so.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import gates, jsonio, log, procs
from slate.errors import MissingPrerequisite
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
             if r.get("family") in FAMILIES and r.get("t") is not None and float(r.get("depth_db") or 0.0) < 0.0]
    if not rides:
        raise MissingPrerequisite("Nothing was ridden: sound/mix/rides.json lists no ridden accents.",
                                  hint="The mix's guards write it as they ride; there's nothing to hear until then.")
    stems = {f: film.path(f"{MIX_DIR}/family_stems/{f}.wav") for f in FAMILIES}
    missing = [f for f, p in stems.items() if not p.exists()]
    if missing:
        raise MissingPrerequisite(f"The mix has no {', '.join(missing)} family stem (sound/mix/family_stems/), so a "
                                  "hit can't be heard on its own.", hint="Mix with family stems (the effects engine "
                                                                         "writes them), then make the listening file.")
    mix_path = film.path(f"{MIX_DIR}/final_mix.wav")
    if not mix_path.exists():
        raise MissingPrerequisite(f"{film.id} hasn't been mixed yet.", hint="Run `slate mix` first.")
    folder = film.path(out) if out else film.path(OUT_DIR)
    work = folder / "parts"
    work.mkdir(parents=True, exist_ok=True)
    sr = film.sr
    picture = _master(film)
    fps = f"{film.fps_num}/{film.fps_den}"
    excerpts = _excerpts(rides, n, film.duration_s)
    clips, rows = [], []
    for k, ex in enumerate(excerpts, 1):
        a, b = int(round(ex["from"] * sr)), int(round(ex["to"] * sr))
        now, _ = audio.read_wav(mix_path, a, b - a)
        now = audio.to_stereo(now)
        before = now.copy()
        for r in ex["rides"]:
            fam, _ = audio.read_wav(stems[r["family"]], a, b - a)
            fam = audio.to_stereo(fam)
            i0, i1 = max(0, int(round(float(r["t"]) * sr)) - a), min(b - a, int(round(float(r["end"]) * sr)) - a)
            undo = 10 ** (-float(r["depth_db"]) / 20) - 1.0  # what the ride took off that hit, added back
            before[i0:i1] += fam[i0:i1] * undo
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
