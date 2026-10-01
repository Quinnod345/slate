"""Clean-up: diagnose a take's noise floor, then clean it the approved way (or try the alternatives).

From the films' `clean_test.py` and `remix_clean.py` (2026-09-29):
- The buzz under a designed voice was in the voice itself, not the codec: lossless PCM carried the same floor, and
  the Voice Isolator lowered it only about 2 dB. So Slate asks for lossless PCM when the plan allows it.
- The chosen fix was a conventional dialogue clean-up: an FFT denoiser tracking the measured floor (`afftdn`), then
  a gentle downward expander between phrases (`agate`). That is the default chain here.
- Alternatives, for the person to compare by ear: ElevenLabs' Voice Isolator on a take (`--method isolator`, paid),
  and Voice Remix, which re-records the voice itself ("keep this exact voice, change only the recording").
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import jsonio, log, procs
from slate.errors import MissingPrerequisite, UsageError
from slate.vendors import elevenlabs
from slate.voice import audio as audio_mod
from slate.voice.film import VoiceFilm, now, record

# The approved chain (the films' voice_c.py), with the denoiser's floor tracking the measured gap floor.
DEFAULT_CHAIN = "afftdn=nr=18:nf={nf}:tn=1,agate=threshold=0.004:ratio=2.5:attack=4:release=180:range=0.06"
FIXED_FLOOR_DB = -64  # the films' fixed floor, used when a take has no gaps to measure

REMIX_DESCRIPTION = ("Keep this exact voice: the same person, same age, accent, pitch, warmth, pace and delivery. "
                     "Change only the recording: a pristine, noise-free studio recording, close-miked in a treated "
                     "vocal booth, with a completely silent background. No hiss, no hum, no buzz, no room noise, no "
                     "crackle and no digital artefacts. Clean, smooth, natural, broadcast-quality voice.")
REMIX_RUNS = ((0.25, 11), (0.35, 22), (0.5, 33))  # (prompt_strength, seed): low strength keeps identity


def diagnose(path: str | Path) -> dict:
    """Speech level against the gap floor, in 20 ms windows (the films' clean_test measure)."""
    x, sr = audio_mod.load(path)
    w = max(1, int(0.02 * sr))
    n = len(x) // w
    if n < 4:
        return {"seconds": round(len(x) / sr, 2), "speech_dbfs": None, "gap_floor_dbfs": None,
                "floor_below_speech_db": None, "note": "too short to measure"}
    frames = x[: n * w].reshape(n, w)
    db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    speech = db[db >= np.percentile(db, 60)]
    gaps = db[db <= np.percentile(db, 15)]  # <=: digital silence makes many frames tie at the bottom
    out = {"seconds": round(len(x) / sr, 2), "speech_dbfs": round(float(np.median(speech)), 1),
           "gap_floor_dbfs": round(float(np.median(gaps)), 1),
           "floor_below_speech_db": round(float(np.median(speech) - np.median(gaps)), 1)}
    floor = out["gap_floor_dbfs"]
    out["verdict"] = ("digital-quiet gaps: no clean-up needed" if floor <= -75 else
                      "a faint floor: the default clean-up (afftdn + agate) suits it" if floor <= -55 else
                      "a loud floor: compare the default clean-up with the Voice Isolator and a Remix by ear")
    return out


def chain_for(profile: dict, floor_dbfs: float | None) -> str:
    """The brand's approved chain when it has one, else the default with the denoiser tracking the floor."""
    if floor_dbfs is None or np.isnan(floor_dbfs):
        nf = FIXED_FLOOR_DB
    else:  # digital silence (-inf) clamps to the quietest floor the denoiser takes
        nf = int(max(-80, min(-20, (round(floor_dbfs) if np.isfinite(floor_dbfs) else -80) - 2)))
    chain = (profile.get("clean") or {}).get("chain") or DEFAULT_CHAIN
    return chain.replace("{nf}", str(nf))


def clean_file(src: str | Path, dst: str | Path, chain: str) -> dict:
    """Run a clean-up chain through ffmpeg into a 48 kHz / 24-bit WAV (written atomically)."""
    ffmpeg = procs.which("ffmpeg")
    if not ffmpeg:
        raise MissingPrerequisite("The clean-up chain runs in ffmpeg, which isn't installed.",
                                  hint="Run `slate doctor --install` (it asks before installing anything).")
    dst = Path(dst)
    tmp = dst.with_name(f".{dst.stem}.part.wav")
    procs.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-af", chain, "-ar", "48000", "-c:a", "pcm_s24le",
               str(tmp)], check=True, timeout=600)
    tmp.replace(dst)
    return {"method": "dsp", "chain": chain, "engine": {"name": "ffmpeg", "path": ffmpeg}}


def _source(vf: VoiceFilm, shot: str) -> tuple[str, dict, Path]:
    sel = vf.selected(shot)
    if not sel:
        raise MissingPrerequisite(f"{shot} has no take yet.", hint=f"slate voice tts --only {shot}")
    take, info = sel
    return take, info, vf.dir / info["raw"]


def clean_take(vf: VoiceFilm, shot: str, *, method: str = "dsp", chain: str | None = None,
               client: elevenlabs.Client | None = None) -> dict:
    """Clean the shot's selected take into <take>.clean.wav (48 kHz / 24-bit) and record the stage."""
    take, info, raw = _source(vf, shot)
    out = raw.with_name(f"{take}.clean.wav")
    before = diagnose(raw)
    if method == "dsp":
        stage = clean_file(raw, out, chain or chain_for(vf.profile, before.get("gap_floor_dbfs")))
    elif method == "isolator":
        client = client or elevenlabs.Client(mode=vf.mode, film=vf.dir)
        res = client.isolate(audio=raw, out=out.with_name(f"{take}.iso.wav"))
        x, sr = audio_mod.load(res["file"])
        audio_mod.write_wav(out, audio_mod.resample(x, sr, 48000), 48000, 24)
        stage = {"method": "isolator", "request_id": res["request_id"], "replay": res["replay"],
                 "isolated_file": vf.rel(res["file"])}
    else:
        raise UsageError(f"Unknown clean-up method {method!r}: dsp or isolator.")
    after = diagnose(out)
    stage.update({"source": vf.rel(raw), "file": vf.rel(out), "sha256": jsonio.sha256(out), "before": before,
                  "after": after, "at": now()})
    data = vf.take_json(shot, take)
    data.setdefault("stages", {})["clean"] = stage
    data["stages"].pop("polish", None)  # a new clean-up means the old polish was made from something else
    vf.save_take_json(shot, take, data)
    vf.update_take(shot, take, clean=vf.rel(out), paced=None, polished=None, align=None)
    record(vf.dir, vf.rel(out), producer=f"slate voice clean ({method})", inputs={vf.rel(raw): jsonio.sha256(raw)})
    return {"shot": shot, "take": take, "file": vf.rel(out), "method": method, "before": before, "after": after,
            **({"chain": stage["chain"]} if "chain" in stage else {})}


def remix(vf: VoiceFilm | None, *, voice_id: str, text: str, description: str | None = None,
          runs: tuple = REMIX_RUNS, out_dir: Path, client: elevenlabs.Client) -> dict:
    """Voice Remix previews of the voice with only the recording changed, each measured (floor against speech)."""
    if len(text) < 100:
        raise UsageError(f"Remix preview text must be at least 100 characters (got {len(text)}).",
                         hint="Pass --text with a longer passage in the voice's own words.")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for strength, seed in runs:
        res = client.remix(voice_id=voice_id, voice_description=description or REMIX_DESCRIPTION, text=text[:1000],
                           prompt_strength=strength, seed=seed, out_dir=out_dir,
                           prefix=f"remix_ps{int(round(strength * 100)):02d}_s{seed}")
        for p in res["previews"]:
            rows.append({"file": str(p["file"]), "prompt_strength": strength, "seed": seed,
                         "generated_voice_id": p["generated_voice_id"], "request_id": res["request_id"],
                         "replay": res["replay"], **diagnose(p["file"])})
    report = {"voice_id": voice_id, "description": description or REMIX_DESCRIPTION, "text": text[:1000],
              "remixes": rows, "at": now(),
              "note": "Listen before choosing: a remix is a new voice. Keep one with `slate voice design --save "
                      "<generated_voice_id>`, then A/B it against the original."}
    jsonio.write_atomic(out_dir / "remix.json", report)
    log.info(f"{len(rows)} remix previews in {out_dir}")
    return report
