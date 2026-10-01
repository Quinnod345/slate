"""Listening reels: takes level-matched and joined with short gaps, so voices are compared by ear, not by volume; and
the narration preview, a re-voiced narration on the film's own timeline before anything is re-timed.

From the films' `voice_reel.py` and `tag_ab.py`: every take is gained to the same integrated loudness (-20 LUFS)
so neither side of a comparison wins by being louder. The reel is a WAV plus a JSON listing what plays when (the
films' title cards carried brand colours; a listing carries no brand at all).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import jsonio
from slate.errors import UsageError
from slate.voice import audio as audio_mod

TARGET_LUFS = -20.0
SR = 48000


def matched(path: str | Path, target: float = TARGET_LUFS, sr: int = SR) -> tuple[np.ndarray, dict]:
    """A take gained to the target loudness (mono, `sr`), and what was done to it."""
    x, rate = audio_mod.load(path)
    x = audio_mod.resample(x, rate, sr)
    before = audio_mod.loudness(x, sr)
    y, gain = audio_mod.gain_to(x, sr, target)
    after = audio_mod.loudness(y, sr)
    return y, {"source": str(path), "lufs_before": before["lufs"], "gain_db": gain, "lufs_after": after["lufs"],
               "true_peak_dbtp": after["true_peak_dbtp"]}


def build(items: list[dict], out_wav: str | Path, *, target: float = TARGET_LUFS, gap_s: float = 0.9,
          sr: int = SR) -> dict:
    """items: [{file, title, [detail]}]. Writes the reel and <reel>.json; returns the listing."""
    out_wav = Path(out_wav)
    parts, listing, t = [], [], 0.0
    for i, item in enumerate(items):
        y, info = matched(item["file"], target, sr)
        if i:
            parts.append(audio_mod.silence(gap_s, sr))
            t += gap_s
        listing.append({"index": i + 1, "at_s": round(t, 3), "seconds": round(len(y) / sr, 3),
                        **{k: v for k, v in item.items() if k != "file"}, **info})
        parts.append(y)
        t += len(y) / sr
    reel = np.concatenate(parts) if parts else np.zeros(1)
    audio_mod.write_wav(out_wav, reel, sr, 24)
    data = {"reel": str(out_wav), "target_lufs": target, "gap_s": gap_s, "seconds": round(len(reel) / sr, 3),
            "items": listing}
    jsonio.write_atomic(out_wav.with_suffix(".json"), data)
    return data


def ab_clip(a: str | Path, b: str | Path, out_wav: str | Path, *, gap_s: float = 0.8, target: float = TARGET_LUFS,
            sr: int = SR) -> dict:
    """A, then a gap, then B, both at the same loudness."""
    ya, ia = matched(a, target, sr)
    yb, ib = matched(b, target, sr)
    audio_mod.write_wav(out_wav, np.concatenate([ya, audio_mod.silence(gap_s, sr), yb]), sr, 24)
    return {"file": str(out_wav), "A": ia, "B": ib, "gap_s": gap_s}


# ---------------------------------------------------------------------------------------------------------------
# The narration preview (L-voice-S018): a re-voiced narration heard on the film's timeline before anything re-times


def _fade(n: int, fade_in: float, fade_out: float, sr: int) -> np.ndarray:
    """The cue sheet's fades: sin² in, cos² out (as the mix places every take)."""
    e = np.ones(n)
    a, r = min(n, int(fade_in * sr)), min(n, int(fade_out * sr))
    if a:
        e[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if r:
        e[n - r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return e


def preview(film_dir: str | Path, *, takes: dict[str, str] | None = None, name: str = "narration",
            target: float = TARGET_LUFS, sr: int = SR) -> dict:
    """The re-voiced narration as one voice-only track on the storyboard's timeline, next to the current track made
    the same way. The current track is what the timing files place now (timing/vo_cues.json on timing/shots.json);
    the proposed one swaps in each line's selected take (or `takes`: {shot: take id}), each shot grown or shrunk by
    exactly its take's change in length, so everything after it moves as the re-timing would move it. Every line is at
    -20 LUFS with the cue sheet's fades. Writes voice/preview/<name>_current.wav, <name>_proposed.wav and
    <name>.json; never a timing file."""
    from slate.voice.film import VoiceFilm, now  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    shots_doc = jsonio.read(vf.path("timing/shots.json"), default=None)
    cues_doc = jsonio.read(vf.path("timing/vo_cues.json"), default=None)
    if not shots_doc or cues_doc is None:
        from slate.errors import MissingPrerequisite  # noqa: PLC0415

        raise MissingPrerequisite("The preview places the narration on the film's current timing, and there isn't one "
                                  "yet (timing/shots.json, vo_cues.json).", hint="slate timing")
    fps = float(shots_doc["fps_num"]) / float(shots_doc["fps_den"])
    cues = {c["shot"]: c for c in cues_doc.get("cues") or [] if c.get("shot")}
    takes = dict(takes or {})
    rows, t_cur, t_new = [], 0.0, 0.0
    placed: dict[str, list] = {"current": [], "proposed": []}
    for sh in sorted(shots_doc.get("shots") or [], key=lambda s: int(s["frame_in"])):
        sid = sh["id"]
        length = (int(sh["frame_out"]) - int(sh["frame_in"])) / fps
        cue = cues.get(sid)
        row = {"shot": sid, "start_current_s": round(t_cur, 3), "start_proposed_s": round(t_new, 3),
               "length_current_s": round(length, 3)}
        delta = 0.0
        if cue:
            cur_file = vf.dir / cue["file"] if not Path(cue["file"]).is_absolute() else Path(cue["file"])
            take_id = takes.get(sid) or (vf.selected(sid) or (None, {}))[0]
            new_file = cur_file
            if take_id:
                info = ((vf.entry(sid) or {}).get("takes") or {}).get(take_id)
                if info is None:
                    raise UsageError(f"{sid} has no take {take_id}.")
                stage = next((s for s in vf.STAGES if info.get(s) and (vf.dir / info[s]).is_file()), None)
                new_file = vf.dir / info[stage] if stage else cur_file
            cur_len, new_len = audio_mod.duration(cur_file), audio_mod.duration(new_file)
            delta = new_len - cur_len
            at = float(cue.get("at", 0.0))
            fades = (float(cue.get("fade_in", 0.01)), float(cue.get("fade_out", 0.05)))
            placed["current"].append((t_cur + at, cur_file, fades))
            placed["proposed"].append((t_new + at, new_file, fades))
            row.update({"take_current": vf.rel(cur_file), "take_proposed": vf.rel(new_file),
                        "take_change_s": round(delta, 3), "at_s": at})
        row["length_proposed_s"] = round(length + delta, 3)
        rows.append(row)
        t_cur += length
        t_new += length + delta
    out = {"current": round(t_cur, 3), "proposed": round(t_new, 3)}
    folder = vf.path("voice/preview")
    files = {}
    for which, total in (("current", t_cur), ("proposed", t_new)):
        track = np.zeros(int(round(total * sr)) + 1)
        for t0, path, (fi, fo) in placed[which]:
            y, _info = matched(path, target, sr)
            y = y * _fade(len(y), fi, fo, sr)
            a = int(round(t0 * sr))
            b = min(len(track), a + len(y))
            if b > a:
                track[a:b] += y[: b - a]
        files[which] = audio_mod.write_wav(folder / f"{name}_{which}.wav", track, sr, 24)
    doc = {"generated": "do not edit by hand: slate voice preview", "film": vf.id, "made_at": now(),
           "target_lufs": target, "seconds": out, "change_s": round(t_new - t_cur, 3),
           "files": {k: vf.rel(v) for k, v in files.items()}, "shots": rows,
           "note": "Nothing is re-timed: this is how the re-voiced narration would sit on the film's timeline, each "
                   "shot grown or shrunk by its take's change in length, next to the current track."}
    jsonio.write_atomic(folder / f"{name}.json", doc)
    return doc
