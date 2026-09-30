"""Reference-film analysis: what a film does, measured, so a new brand's look and motion start from evidence.

`slate analyze <video>` runs the films' reference-analysis stage as one command (plan §2 "analyze", §4 onboarding):

- **cuts**: ffmpeg's scene score on every frame; hard cuts, softer changes, scenes, a legible frame of each and
  contact sheets (slate.analyze.cuts);
- **ocr**: the words on screen in each scene, by Slate's one OCR engine (slate.analyze.onscreen);
- **eases**: moves found (or described), tracked and fitted to cubic-beziers, with the nearest named eases
  (slate.analyze.eases, generalized from the films' fit_ease.py);
- **transcript**: local Whisper with word timings, cleaned of its loops, measured and joined to the scenes;
- **stems**: Demucs's voice and music split, through uvx (the optional analyze extra);
- **beats**: the music's tempo and beat grid with librosa, through uvx.

Without step flags it runs the quick, local ones (cuts, ocr, eases); `--all` adds the model-based ones. The mix's
loudness is measured whenever the film has sound. A step that can't run here is UNCHECKED, with the fix, and the rest
still run. A Higgsfield video analysis (0 credits, made in the chat lane through Higgsfield's hosted connector) can be
attached with `--higgsfield` for its scene labels and descriptions.

Everything lands in one folder: analysis.json (the data), analysis.md (the readable report), frames/, scores/,
transcript/, stems/. Brand onboarding runs two or three reference films through `run()` and copies the measured eases,
with their lineage, into the brand's motion law.

    run(video, *, out=None, steps=None, film_dir=None, higgsfield=None, eases_spec=None, brand=None,
        language="en", allow_download=False, max_moves=12) -> dict
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from slate import __version__, jsonio, log, paths
from slate.errors import UsageError
from slate.gates import GateResult, now_iso

STEPS = ("cuts", "ocr", "eases", "transcript", "stems", "beats")
DEFAULT_STEPS = ("cuts", "ocr", "eases")
GENERATED = "do not edit by hand: slate analyze"

NOTE_HIGGSFIELD = ("Higgsfield's per-scene audio text can paraphrase the on-screen words rather than the voice, so the "
                   "voice-over here always comes from Whisper; Higgsfield gives the scene labels and visual descriptions.")
NOTE_TRANSCRIPT = ("Low-confidence words are flagged, never changed: check them by ear, or run a second Whisper model "
                   "on the disputed lines.")
NOTE_EASES = ("Measured eases are evidence for the brand's motion law, not the law itself: brand onboarding names "
              "them, keeps their lineage and gets them approved in a moving look test.")
NOTE_FOUND = ("These moves were found automatically: each follows whatever changed most in one stretch of motion, so "
              "when several things move at once its curve is a blend, and a rough or fair fit says so. For the moves a "
              "motion law depends on, describe them (a window, a region, a colour and a measurement) with "
              "--eases moves.json, as the films measured theirs.")


def default_out(video: Path, sha256: str, film_dir: Path | None = None) -> Path:
    """Where an analysis goes when no --out is given: the film's analysis/ folder, else Slate's data folder.
    Never beside the video, which may sit in a folder Slate mustn't write to."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(video).stem).strip("-.") or "video"
    if film_dir:
        return Path(film_dir) / "analysis" / stem
    return paths.data() / "analysis" / f"{stem}-{sha256[:8]}"


def _mix_loudness(wav: Path) -> dict | None:
    try:
        from slate.sound import measure  # noqa: PLC0415

        m = measure.loudness(wav)
    except Exception as exc:  # noqa: BLE001 - loudness is a nice-to-have in an analysis
        log.warn(f"couldn't measure the mix's loudness: {exc}")
        return None
    return {"file": "audio/mix.wav", "integrated_lufs": m.get("integrated_lufs"), "true_peak_dbtp": m.get("true_peak_dbtp"),
            "lra_lu": m.get("lra_lu"), "silent": m.get("silent", False), "engine": m.get("engine")}


def _no_audio(step: str) -> GateResult:
    return GateResult.unchecked(f"analyze.{step}", "the film has no audio track, so there's nothing to hear")


def run(video: str | Path, *, out: str | Path | None = None, steps: Iterable[str] | None = None,
        film_dir: str | Path | None = None, higgsfield: str | Path | None = None, eases_spec: str | Path | None = None,
        brand: str | None = None, language: str = "en", allow_download: bool = False,
        max_moves: int | None = None) -> dict:
    """Analyze one film and write analysis.json and analysis.md. Returns the analysis (with "out" and "files")."""
    from slate.analyze import beats, cuts, eases, library, onscreen, probe, report, stems, transcript  # noqa: PLC0415

    requested = list(dict.fromkeys(steps)) if steps else list(DEFAULT_STEPS)
    unknown = [s for s in requested if s not in STEPS]
    if unknown:
        raise UsageError(f"Unknown analysis step {unknown[0]!r}; the steps are {', '.join(STEPS)}.")
    video = Path(video).expanduser().resolve()
    meta = probe.probe(video)
    out_dir = Path(out).expanduser().resolve() if out else default_out(video, meta["sha256"], film_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"analyzing {video.name} into {out_dir}")
    results: dict[str, GateResult] = {}
    analysis: dict = {"generated": GENERATED, "slate_version": __version__, "made_at": now_iso(), "video": meta,
                      "requested": requested, "notes": []}
    vi = meta["video"]
    results["probe"] = GateResult.pass_(
        "analyze.probe", f"{meta.get('duration_s') or 0:.2f} s of {vi['width']} x {vi['height']} at "
        f"{vi['fps_num']}/{vi['fps_den']} fps, {vi.get('frames')} frames", engine=meta["engine"])

    # Scenes first: OCR reads their frames, the finder never tracks across their cuts, the transcript joins them.
    scenes: list[dict] = []
    cut_frames: list[int] = []
    if "cuts" in requested or "ocr" in requested or higgsfield:
        log.info("scoring every frame for cuts and changes")
        results["cuts"], cut_data = cuts.run(video, meta, out_dir, higgsfield=higgsfield)
        analysis["cuts"] = {k: v for k, v in cut_data.items() if k != "scenes"}
        scenes = cut_data.get("scenes", [])
        cut_frames = [c["frame"] for c in cut_data.get("hard_cuts", [])]
        if cut_data.get("higgsfield"):
            analysis["higgsfield"] = cut_data["higgsfield"]
            analysis["notes"].append(NOTE_HIGGSFIELD)
        if "cuts" not in requested:
            analysis["notes"].append("Scene cuts ran as well, because the on-screen text and Higgsfield's scenes need them.")

    # Sound: the mix's loudness always; stems, transcript and beats when asked.
    wav = None
    if meta.get("audio"):
        rate = (meta["audio"] or {}).get("sample_rate") or 48000
        wav = probe.extract_audio(video, out_dir / "audio" / "mix.wav", sample_rate=int(rate))
        mix = _mix_loudness(wav)
        keep_wav = any(s in requested for s in ("stems", "transcript", "beats"))
        if mix:
            if not keep_wav:
                mix["file"] = None  # measured and let go: nothing else here listens to it
            analysis["audio"] = {"mix": mix}
            results["audio"] = GateResult.pass_(
                "analyze.audio", f"integrated {mix['integrated_lufs']:.1f} LUFS, LRA {mix.get('lra_lu') or 0:.1f} LU",
                engine=mix.get("engine") or {}, evidence=["audio/mix.wav"] if keep_wav else [])
        if not keep_wav:
            wav.unlink(missing_ok=True)
            wav = None
    vocals = music = None
    if "stems" in requested:
        if wav is None:
            results["stems"] = _no_audio("stems")
        else:
            log.info("splitting voice and music with Demucs")
            results["stems"], stem_data = stems.run(wav, out_dir / "stems", duration_s=float(meta.get("duration_s") or 0),
                                                    allow_download=allow_download)
            if stem_data:
                analysis.setdefault("audio", {})["stems"] = stem_data
                vocals, music = out_dir / stem_data["vocals"], out_dir / stem_data["music"]
    if "transcript" in requested:
        if wav is None:
            results["transcript"] = _no_audio("transcript")
        else:
            source = "voice stem" if vocals else "mix"
            log.info(f"transcribing the {source} with Whisper")
            results["transcript"], tr = transcript.run(vocals or wav, meta, out_dir, language=language, source=source)
            if tr:
                analysis["transcript"] = tr
                transcript.join_scenes(scenes, tr["segments"])
                analysis["notes"].append(NOTE_TRANSCRIPT)
    if "beats" in requested:
        if wav is None:
            results["beats"] = _no_audio("beats")
        else:
            log.info("reading the music's tempo")
            results["beats"], beat_data = beats.run(music or wav, source="music stem" if music else "mix",
                                                    allow_download=allow_download)
            if beat_data:
                analysis["beats"] = beat_data

    if "ocr" in requested:
        log.info("reading the words on screen")
        results["ocr"], ocr_data = onscreen.run(scenes, out_dir)
        if ocr_data:
            analysis["ocr"] = ocr_data

    if "eases" in requested:
        brand_curves, brand_note = library.brand_eases(brand)
        if brand_note:
            analysis["notes"].append(brand_note.capitalize() + ".")
        cands = library.candidates(brand_curves)
        limit = max_moves or eases.MAX_MOVES
        if eases_spec:
            log.info("measuring the described moves")
            moves, skipped = eases.described_moves(video, vi, eases.load_specs(eases_spec), cands=cands, video_meta=meta)
            how = f"{len(moves)} described move{'s' if len(moves) != 1 else ''} fitted"
        else:
            log.info("finding and following moves")
            moves, skipped = eases.found_moves(video, vi, cuts=cut_frames, cands=cands, video_meta=meta, max_moves=limit)
            how = f"{len(moves)} move{'s' if len(moves) != 1 else ''} found and fitted"
        analysis["eases"] = {"moves": moves, "skipped": skipped, "compared_with": [c["name"] for c in cands],
                             "families": {**library.FAMILIES, **({"brand": f"the {brand} pack's named eases"} if brand_curves else {})},
                             "spec": str(eases_spec) if eases_spec else None}
        if moves:
            analysis["notes"].append(NOTE_EASES)
            if not eases_spec:
                analysis["notes"].append(NOTE_FOUND)
            results["eases"] = GateResult.pass_("analyze.eases", how + (f"; {len(skipped)} couldn't be fitted" if skipped else ""),
                                                engine={"name": "slate.motion.fit", "version": __version__,
                                                        "tracker": "slate.analyze.eases"})
        else:
            why = (skipped[0]["reason"] if skipped else "no move with stillness on both sides was found")
            results["eases"] = GateResult.unchecked("analyze.eases", f"no eases measured: {why}",
                                                    evidence=["next: describe the move (slate analyze <video> --eases moves.json)"],
                                                    engine={"name": "slate.motion.fit", "version": __version__})

    analysis["scenes"] = scenes
    order = ["probe", "cuts", "audio", "stems", "transcript", "beats", "ocr", "eases"]
    analysis["steps"] = {k: results[k].to_dict() for k in order if k in results}
    analysis["out"] = str(out_dir)
    analysis["files"] = {"analysis": "analysis.json", "report": "analysis.md"}
    jsonio.write_atomic(out_dir / "analysis.json", analysis)
    (out_dir / "analysis.md").write_text(report.render(analysis), encoding="utf-8")
    return analysis


def unchecked_requested(analysis: dict, explicit: Iterable[str]) -> list[str]:
    """The steps a person asked for by name that couldn't run (they set the exit code)."""
    steps = analysis.get("steps") or {}
    return [s for s in explicit if s in steps and steps[s]["status"] != "PASS"]
