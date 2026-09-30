"""Word timings for every final take: local Whisper by default, ElevenLabs Forced Alignment to evaluate.

- **Whisper** (openai-whisper, the turbo model, word timestamps) is the default aligner and the ear of the voice
  gates: it transcribes what was actually said, so it can catch a missing word, a repeat or a tag read aloud. The
  model and the Whisper build are recorded as evidence in every alignment file.
- **ElevenLabs Forced Alignment** (`--engine elevenlabs`, paid per minute) aligns the known script to the audio.
  It's usually tighter on timing, but it always places every word it's given, so it can't tell whether a word was
  said. Slate keeps it for evaluation against Whisper (`--compare`), never as the gates' ear.
- whisper.cpp isn't used (it was only ever a test model in the films).

Outputs, per shot: `voice/align/<shot>.whisper.json` or `<shot>.elevenlabs.json` (Whisper-shaped: segments ->
words with start and end, relative to the take), plus `voice/align/<shot>.json`, the canonical copy the timing stage
reads. Replayed takes (SLATE_FAKE_VENDORS=1) are aligned from the fake voice's own word times and marked replay:
they keep the pipeline running offline, and the gates treat them as unchecked, never as heard.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from slate import config, jsonio, log, procs, replay
from slate.errors import MissingPrerequisite, UsageError
from slate.vendors import elevenlabs
from slate.voice import lang
from slate.voice.film import VoiceFilm, now, record
from slate.voice.pace import map_time

ENGINES = ("whisper", "elevenlabs")
# Registry model ids -> the Whisper CLI's model names.
WHISPER_MODELS = {"whisper_turbo": "turbo", "whisper_large_v3_turbo": "large-v3-turbo"}


def whisper_model(model: str | None = None) -> str:
    chosen = model or config.model("alignment")
    return WHISPER_MODELS.get(chosen, chosen)


def whisper_info() -> dict:
    """Where the Whisper CLI is and which build (from its Homebrew Cellar path when it has one)."""
    exe = procs.which("whisper")
    if not exe:
        return {"path": None, "version": None}
    real = str(Path(exe).resolve())
    m = re.search(r"openai-whisper/([^/]+)/", real)
    return {"path": exe, "resolved": real, "version": m.group(1) if m else None}


def run_whisper(files: list[Path], out_dir: Path, *, model: str | None = None, language: str = "en",
                timeout: float = 3600) -> dict[Path, dict]:
    """Transcribe files in one Whisper run (the model loads once). Returns {file: whisper JSON}."""
    info = whisper_info()
    if not info["path"]:
        raise MissingPrerequisite("Whisper isn't installed; the voice gates need it to hear the takes.",
                                  hint="Run `slate doctor --install` (openai-whisper and its 1.6 GB turbo model; it "
                                       "asks first).")
    name = whisper_model(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[Path, dict] = {}
    with tempfile.TemporaryDirectory(prefix="slate-whisper-") as tmp:
        # Unique names in, so outputs never collide (two shots' takes are both called t01).
        links = {}
        for i, f in enumerate(files):
            link = Path(tmp) / f"f{i:04d}{Path(f).suffix}"
            link.symlink_to(Path(f).resolve())
            links[link] = Path(f)
        argv = [info["path"], *[str(p) for p in links], "--model", name, "--language", language,
                "--word_timestamps", "True", "--output_format", "json", "--output_dir", tmp, "--verbose", "False",
                "--fp16", "False"]
        _hold("whisper", lambda: procs.run(argv, check=True, timeout=timeout))
        for link, original in links.items():
            produced = Path(tmp) / f"{link.stem}.json"
            if produced.is_file():
                results[original] = json.loads(produced.read_text(encoding="utf-8"))
    return results


def _hold(resource: str, fn):
    """Run under the job queue's resource lock when the queue is installed (one Whisper at a time on this Mac)."""
    try:
        from slate.jobs import locks  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        if not str(exc.name or "").startswith("slate.jobs"):
            raise
        return fn()
    with locks.hold(resource):
        return fn()


def _whisper_doc(data: dict, *, audio: Path, model: str, info: dict, language: str) -> dict:
    return {"engine": {"name": "openai-whisper", "version": info.get("version"), "model": model,
                       "path": info.get("path")},
            "language": data.get("language", language), "audio": str(audio), "sha256": jsonio.sha256(audio),
            "text": str(data.get("text", "")).strip(), "segments": [
                {"start": s.get("start"), "end": s.get("end"), "text": s.get("text"),
                 "avg_logprob": s.get("avg_logprob"),
                 "words": [{"word": str(w.get("word", "")).strip(), "start": w.get("start"), "end": w.get("end"),
                            **({"probability": w["probability"]} if "probability" in w else {})}
                           for w in s.get("words") or []]}
                for s in data.get("segments") or []],
            "replay": False, "at": now()}


def _elevenlabs_doc(res: dict, *, audio: Path) -> dict:
    words = [{"word": str(w.get("text", "")).strip(), "start": w.get("start"), "end": w.get("end"),
              "loss": w.get("loss")} for w in res.get("words") or [] if str(w.get("text", "")).strip()]
    return {"engine": {"name": "elevenlabs forced alignment", "version": None, "model": res.get("model"),
                       "request_id": res.get("request_id")},
            "audio": str(audio), "sha256": jsonio.sha256(audio), "text": " ".join(w["word"] for w in words),
            "segments": [{"words": words}], "loss": res.get("loss"), "replay": bool(res.get("replay")),
            "note": "Forced alignment places the script's words; it can't tell whether they were said.",
            "at": now()}


def replay_words(vf: VoiceFilm, shot: str, take: str) -> list[dict] | None:
    """A replayed take's own word times, carried through its pacing (pause cuts, then tempo)."""
    data = vf.take_json(shot, take)
    words = data.get("replay_words")
    if not words:
        return None
    pace = (data.get("stages") or {}).get("pace")
    if pace and vf.final_stage(shot) == "paced":
        cuts, tempo = pace.get("cuts") or [], float(pace.get("tempo") or 1.0)
        return [{"word": w["word"], "start": round(map_time(w["start"], cuts, tempo), 4),
                 "end": round(map_time(w["end"], cuts, tempo), 4)} for w in words]
    return [dict(w) for w in words]


def _replay_doc(words: list[dict], audio: Path) -> dict:
    return {"engine": {"name": "replay", "version": None, "model": None},
            "audio": str(audio), "sha256": jsonio.sha256(audio), "text": " ".join(w["word"] for w in words),
            "segments": [{"words": words}], "replay": True, "at": now(),
            "note": "Word times from the replayed voice itself. Nothing was heard: the voice gates report these "
                    "takes as unchecked."}


def _write(vf: VoiceFilm, shot: str, engine: str, doc: dict, *, canonical: bool) -> Path:
    folder = vf.dir / "voice" / "align"
    path = jsonio.write_atomic(folder / f"{shot}.{engine}.json", {"generated": "do not edit by hand: slate voice align",
                                                                  "shot": shot, **doc})
    if canonical:
        jsonio.write_atomic(folder / f"{shot}.json", {"generated": "do not edit by hand: slate voice align",
                                                      "shot": shot, **doc})
        sel = vf.selected(shot)
        if sel:
            vf.update_take(shot, sel[0], align=vf.rel(folder / f"{shot}.json"))
            record(vf.dir, vf.rel(folder / f"{shot}.json"), producer=f"slate voice align ({doc['engine']['name']})",
                   inputs={vf.rel(doc["audio"]): doc["sha256"]}, tool_version=doc["engine"].get("model"))
    return path


def align(film_dir: str | Path, *, only: str | list[str] | None = None, engine: str = "whisper",
          model: str | None = None, canonical: bool | None = None) -> dict:
    """Align every shot's final take. Whisper writes the canonical alignment; ElevenLabs writes only its evaluation
    copy unless `canonical=True`."""
    if engine not in ENGINES:
        raise UsageError(f"Unknown aligner {engine!r}: {' or '.join(ENGINES)}.")
    vf = VoiceFilm(film_dir)
    lines = vf.select(only)
    canonical = (engine == "whisper") if canonical is None else canonical
    todo, rows, missing = [], [], []
    for ln in lines:
        audio = vf.final_audio(ln.shot)
        if audio is None:
            missing.append(ln.shot)
            continue
        todo.append((ln, vf.selected(ln.shot)[0], audio))
    if not todo:
        raise MissingPrerequisite("No voiced takes to align.", hint="slate voice tts")

    real = []
    for ln, take, audio in todo:
        replayed = bool(vf.take_json(ln.shot, take).get("replay"))
        words = replay_words(vf, ln.shot, take) if replayed and engine == "whisper" else None
        if words is not None:
            path = _write(vf, ln.shot, "replay", _replay_doc(words, audio), canonical=canonical)
            rows.append({"shot": ln.shot, "take": take, "engine": "replay", "file": vf.rel(path),
                         "words": len(words)})
        else:
            real.append((ln, take, audio))

    if real and engine == "whisper":
        name = whisper_model(model)
        info = whisper_info()
        language = lang.load(vf.language).get("whisper_language", vf.language)
        results = run_whisper([a for _, _, a in real], vf.dir / "voice" / "align", model=name, language=language)
        for ln, take, audio in real:
            data = results.get(audio)
            if data is None:
                missing.append(ln.shot)
                continue
            doc = _whisper_doc(data, audio=audio, model=name, info=info, language=language)
            path = _write(vf, ln.shot, "whisper", doc, canonical=canonical)
            rows.append({"shot": ln.shot, "take": take, "engine": f"whisper {name}", "file": vf.rel(path),
                         "heard": doc["text"]})
    elif real:
        client = elevenlabs.Client(mode=vf.mode, film=vf.dir)
        for ln, take, audio in real:
            res = client.align(audio=audio, text=ln.text)
            doc = _elevenlabs_doc(res, audio=audio)
            path = _write(vf, ln.shot, "elevenlabs", doc, canonical=canonical)
            rows.append({"shot": ln.shot, "take": take, "engine": f"elevenlabs {res.get('model')}",
                         "file": vf.rel(path), "request_id": res.get("request_id")})
    for ln, take, audio in todo:
        data = vf.take_json(ln.shot, take)
        data.setdefault("stages", {})[f"align_{engine}"] = {"audio": vf.rel(audio), "at": now(),
                                                             "replay": bool(data.get("replay"))}
        vf.save_take_json(ln.shot, take, data)
    if missing:
        log.warn(f"not aligned: {', '.join(missing)}")
    return {"film": vf.id, "engine": engine, "aligned": rows, "missing": missing,
            "model": whisper_model(model) if engine == "whisper" else config.model("alignment_eval"),
            "replay_mode": replay.fake_vendors()}


def load(film_dir: str | Path, shot: str, engine: str | None = None) -> dict | None:
    """A shot's alignment: the canonical file, or one engine's copy."""
    name = f"{shot}.{engine}.json" if engine else f"{shot}.json"
    path = Path(film_dir) / "voice" / "align" / name
    return jsonio.read(path) if path.is_file() else None


def compare(film_dir: str | Path, *, only: str | list[str] | None = None) -> dict:
    """Whisper against ElevenLabs Forced Alignment, word by word (start and end differences in ms)."""
    from slate.voice.measure import align_words, whisper_words  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    shots, all_d = [], []
    for ln in vf.select(only):
        a = load(vf.dir, ln.shot, "whisper") or load(vf.dir, ln.shot, "replay")
        b = load(vf.dir, ln.shot, "elevenlabs")
        if not a or not b:
            shots.append({"shot": ln.shot, "status": "needs both engines (slate voice align, then --engine elevenlabs)"})
            continue
        wa, wb = whisper_words(a), whisper_words(b)
        _, _, pairs = align_words([lang.norm_token(w["word"]) for w in wa], [lang.norm_token(w["word"]) for w in wb])
        d_start = [abs(wa[i]["start"] - wb[j]["start"]) * 1000 for i, j in pairs]
        d_end = [abs(wa[i]["end"] - wb[j]["end"]) * 1000 for i, j in pairs]
        all_d += d_start
        shots.append({"shot": ln.shot, "matched": len(pairs), "whisper_words": len(wa), "fa_words": len(wb),
                      "start_ms_mean": round(sum(d_start) / len(d_start), 1) if d_start else None,
                      "start_ms_max": round(max(d_start), 1) if d_start else None,
                      "end_ms_mean": round(sum(d_end) / len(d_end), 1) if d_end else None,
                      "engines": [a["engine"], b["engine"]], "replay": bool(a.get("replay") or b.get("replay"))})
    out = {"film": vf.id, "shots": shots,
           "overall_start_ms_mean": round(sum(all_d) / len(all_d), 1) if all_d else None,
           "note": "Evaluation only: Whisper stays the default aligner and the gates' ear."}
    jsonio.write_atomic(vf.dir / "voice" / "align" / "compare.json", {"generated": "do not edit by hand: "
                                                                                    "slate voice align --compare", **out})
    return out
