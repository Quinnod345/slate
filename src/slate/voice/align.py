"""Word timings for every final take: local Whisper by default, ElevenLabs Forced Alignment to evaluate.

- **Whisper** (openai-whisper, the turbo model, word timestamps) is the default aligner and the ear of the voice
  gates: it transcribes what was actually said, so it can catch a missing word, a repeat or a tag read aloud. The
  model and the Whisper build are recorded as evidence in every alignment file.
- **ElevenLabs Forced Alignment** (`--engine elevenlabs`, paid per minute) aligns the known script to the audio.
  It's usually tighter on timing, but it always places every word it's given, so it can't tell whether a word was
  said. Slate keeps it for evaluation against Whisper (`--compare`), never as the gates' ear.
- whisper.cpp isn't used (it was only ever a test model in the films).
- `SLATE_WHISPER` (or slate.toml's `[voice] whisper`) names one particular Whisper CLI; otherwise it's the `whisper`
  on Slate's PATH, Homebrew's first. The tests point it at a stand-in that answers from recorded transcripts.

Outputs, per shot: `voice/align/<shot>.whisper.json` or `<shot>.elevenlabs.json` (Whisper-shaped: segments ->
words with start and end, relative to the take), plus `voice/align/<shot>.json`, the canonical copy the timing stage
reads. Replayed takes (SLATE_FAKE_VENDORS=1) are aligned from the fake voice's own word times and marked replay:
they keep the pipeline running offline, and the gates treat them as unchecked, never as heard.
"""

from __future__ import annotations

import json
import math
import os
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
# Names one particular Whisper CLI (a build outside Homebrew, or a stand-in that answers from recorded transcripts in
# tests). Slate's PATH puts Homebrew first whatever the caller's PATH says, so a PATH entry can't do this job.
WHISPER_ENV = "SLATE_WHISPER"


def whisper_model(model: str | None = None) -> str:
    chosen = model or config.model("alignment")
    return WHISPER_MODELS.get(chosen, chosen)


def _whisper_override() -> str | None:
    """The Whisper CLI a person (or a test) chose: SLATE_WHISPER, then slate.toml's [voice] whisper."""
    chosen = os.environ.get(WHISPER_ENV) or (config.load().get("voice") or {}).get("whisper")
    return str(Path(str(chosen)).expanduser()) if chosen else None


def whisper_info() -> dict:
    """Where the Whisper CLI is and which build (from its Homebrew Cellar path when it has one)."""
    override = _whisper_override()
    if override:
        if not (os.path.isfile(override) and os.access(override, os.X_OK)):
            return {"path": None, "version": None, "override": override}
        exe = override
    else:
        exe = procs.which("whisper")
    if not exe:
        return {"path": None, "version": None}
    real = str(Path(exe).resolve())
    m = re.search(r"openai-whisper/([^/]+)/", real)
    return {"path": exe, "resolved": real, "version": m.group(1) if m else None,
            **({"override": override} if override else {})}


def run_whisper(files: list[Path], out_dir: Path, *, model: str | None = None, language: str = "en",
                timeout: float = 3600, context: bool = True) -> dict[Path, dict]:
    """Transcribe files in one Whisper run (the model loads once). Returns {file: whisper JSON}.

    context=False hears each 30 s window without the text Whisper decoded before it (its own
    --condition_on_previous_text False). A take is one window, so it changes nothing there; over a whole mix, the
    running context can loop (one film's mix came back with a line said twice and three section names heard as the
    brand)."""
    info = whisper_info()
    if not info["path"]:
        if info.get("override"):
            raise MissingPrerequisite(f"{WHISPER_ENV} (or slate.toml's [voice] whisper) points at "
                                      f"{info['override']}, which isn't a program Slate can run.",
                                      hint="Point it at a working Whisper CLI, or unset it to use Homebrew's.")
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
                "--fp16", "False", *([] if context else ["--condition_on_previous_text", "False"])]
        _hold("whisper", lambda: procs.run(argv, check=True, timeout=timeout))
        for link, original in links.items():
            produced = Path(tmp) / f"{link.stem}.json"
            if produced.is_file():
                results[original] = json.loads(produced.read_text(encoding="utf-8"))
    return results


def transcribe(path: str | Path, *, model: str | None = None, language: str = "en", timeout: float = 900,
               context: bool = True) -> dict:
    """One file through Whisper, the way every Slate module should run it: SLATE_WHISPER (or slate.toml's [voice]
    whisper) names the CLI, the job queue's whisper lock keeps it to one at a time, and the result is Whisper's own
    JSON (segments with word timestamps). For other modules' Whisper runs (timing, analyze), so a stand-in or a
    particular build named once is the one every stage hears with."""
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="slate-whisper-out-") as out:
        results = run_whisper([path], Path(out), model=model, language=language, timeout=timeout, context=context)
    if path not in results:
        raise MissingPrerequisite(f"Whisper ran on {path.name} but wrote no transcript.",
                                  hint="Run the same Whisper command by hand to see its own error.")
    return results[path]


def whisper_executable() -> str | None:
    """The Whisper CLI Slate runs (SLATE_WHISPER first, then Homebrew's), or None when there isn't one."""
    return whisper_info()["path"]


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


def _elevenlabs_doc(res: dict, *, audio: Path, source_text: str) -> dict:
    words = [{"word": str(w.get("text", "")).strip(), "start": w.get("start"), "end": w.get("end"),
              "loss": w.get("loss")} for w in res.get("words") or [] if str(w.get("text", "")).strip()]
    return {"engine": {"name": "elevenlabs forced alignment", "version": None, "model": res.get("model"),
                       "request_id": res.get("request_id")},
            "audio": str(audio), "sha256": jsonio.sha256(audio), "text": " ".join(w["word"] for w in words),
            "source_text": source_text, "segments": [{"words": words}], "loss": res.get("loss"), "replay": bool(res.get("replay")),
            "note": "Forced alignment places the script's words; it can't tell whether they were said.",
            "at": now()}


def replay_words(vf: VoiceFilm, shot: str, take: str) -> list[dict] | None:
    """A replayed take's own word times, carried through its pacing (pause cuts, then tempo)."""
    data = vf.take_json(shot, take)
    words = data.get("replay_words")
    if not words:
        return None
    pace = (data.get("stages") or {}).get("pace")
    if pace and vf.timing_stage(shot) == "paced":  # a polished take keeps its source's timing
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
            doc = _elevenlabs_doc(res, audio=audio, source_text=ln.text)
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


def _comparison_inputs(vf: VoiceFilm, line, a: dict, b: dict) -> tuple[dict | None, str | None]:
    """Saved alignments must describe the currently selected audio and forced-alignment script."""
    audio = vf.final_audio(line.shot)
    if audio is None:
        return None, "needs a current selected audio file"
    try:
        audio_hash = jsonio.sha256(audio)
    except OSError:
        return None, "needs a readable current selected audio file"
    for name, doc, engines in (("whisper", a, {"openai-whisper", "replay"}),
                               ("elevenlabs", b, {"elevenlabs forced alignment"})):
        if not isinstance(doc, dict) or not isinstance(doc.get("engine"), dict) or \
                doc["engine"].get("name") not in engines:
            return None, f"needs a {name} alignment with its engine binding"
        source = doc.get("audio")
        if not isinstance(source, str) or not source:
            return None, f"needs a {name} alignment with its audio binding"
        path = Path(source)
        if not path.is_absolute():
            path = vf.dir / path
        try:
            is_selected = path.resolve() == audio.resolve()
        except (OSError, RuntimeError, ValueError):
            is_selected = False
        if not is_selected:
            return None, f"needs a current {name} alignment: its audio path is not the selected take"
        if doc.get("sha256") != audio_hash:
            return None, f"needs a current {name} alignment: its audio SHA does not match the selected take"
        if "source_text" in doc and doc["source_text"] != line.text:
            return None, f"needs a current {name} alignment: its source text does not match the current line"
    if b.get("source_text") != line.text:
        return None, "needs an elevenlabs alignment bound to the current exact source text"
    return {"audio": str(audio), "sha256": audio_hash, "source_text": line.text}, None


def _comparison_words(doc: dict) -> list[dict]:
    """Validate saved numeric times before the word reader converts values to floats."""
    from slate.voice.measure import whisper_words  # noqa: PLC0415

    raw = [word for segment in doc.get("segments") or [] for word in segment.get("words") or []]
    if not raw:
        raw = doc.get("words") or []
    for word in raw:
        for field in ("start", "end"):
            value = word.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    not math.isfinite(value) or value < 0:
                raise ValueError("word times must be finite nonnegative numbers")
        if word["end"] < word["start"]:
            raise ValueError("a word ends before it starts")
    return whisper_words(doc)


def _comparison_mean(values: list[float]) -> float | None:
    # Divide before summing so finite, large diagnostic values cannot overflow the mean.
    return round(math.fsum(v / len(values) for v in values), 1) if values else None


def compare(film_dir: str | Path, *, only: str | list[str] | None = None) -> dict:
    """Compare current saved inputs; only equal normalized words contribute timing disagreement."""
    from slate.voice.measure import align_words  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    shots, all_d = [], []
    for ln in vf.select(only):
        shot = {"shot": ln.shot, "comparison_ready": False, "matched": None,
                "start_ms_mean": None, "start_ms_max": None, "end_ms_mean": None}
        shots.append(shot)
        a = load(vf.dir, ln.shot, "whisper") or load(vf.dir, ln.shot, "replay")
        b = load(vf.dir, ln.shot, "elevenlabs")
        if not a or not b:
            shot["status"] = "needs both engines (slate voice align, then --engine elevenlabs)"
            continue
        inputs, problem = _comparison_inputs(vf, ln, a, b)
        if problem:
            shot["status"] = problem
            continue
        shot["inputs"] = inputs
        try:
            wa, wb = _comparison_words(a), _comparison_words(b)
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as exc:
            shot["status"] = f"needs valid saved word times: {exc}"
            continue
        # Keep apostrophes and numeric spelling: a punctuation-only difference can match, but a contraction or
        # differently spelled number stays explicit. A multiword token remains one timestamped occurrence.
        na = [tuple(lang.norm_words(w["word"])) for w in wa]
        nb = [tuple(lang.norm_words(w["word"])) for w in wb]
        edits, _, aligned = align_words(nb, na)
        pairs = [(j, i) for i, j in aligned if nb[i] and nb[i] == na[j]]
        matched_a, matched_b = {i for i, _ in pairs}, {j for _, j in pairs}
        aligned_a, aligned_b = {j for _, j in aligned}, {i for i, _ in aligned}
        d_start = [abs(wa[i]["start"] - wb[j]["start"]) * 1000 for i, j in pairs]
        d_end = [abs(wa[i]["end"] - wb[j]["end"]) * 1000 for i, j in pairs]
        if not all(math.isfinite(d) for d in d_start + d_end):
            shot["status"] = "needs valid saved word times: timing differences exceed finite numeric range"
            continue
        all_d += d_start
        shot.update({"matched": len(pairs), "whisper_words": len(wa), "fa_words": len(wb), "edits": edits,
                     "coverage": {"whisper_fraction": len(pairs) / len(wa) if wa else None,
                                  "fa_fraction": len(pairs) / len(wb) if wb else None},
                     "substitutions": [{"fa_index": i, "whisper_index": j, "fa_word": wb[i]["word"],
                                        "whisper_word": wa[j]["word"]}
                                       for i, j in aligned if not nb[i] or nb[i] != na[j]],
                     "deletions": [{"index": i, "word": w["word"]} for i, w in enumerate(wb)
                                   if i not in aligned_b],
                     "insertions": [{"index": i, "word": w["word"]} for i, w in enumerate(wa)
                                    if i not in aligned_a],
                     "unmatched_whisper_words": [{"index": i, "word": w["word"]} for i, w in enumerate(wa)
                                                  if i not in matched_a],
                     "unmatched_fa_words": [{"index": i, "word": w["word"]} for i, w in enumerate(wb)
                                             if i not in matched_b],
                     "start_ms_mean": _comparison_mean(d_start),
                     "start_ms_max": round(max(d_start), 1) if d_start else None,
                     "end_ms_mean": _comparison_mean(d_end),
                     "engines": [a["engine"], b["engine"]], "replay": bool(a.get("replay") or b.get("replay")),
                     "comparison_ready": bool(pairs)})
        if not pairs:
            shot["status"] = "no equal normalized words matched; timing disagreement is unavailable"
    out = {"film": vf.id, "shots": shots, "complete": bool(shots) and all(s["comparison_ready"] for s in shots),
           "overall_start_ms_mean": _comparison_mean(all_d),
           "note": "Evaluation data only, not timing accuracy or listening approval. Replay was not heard. "
                   "Whisper stays the default aligner and the gates' ear. Edits describe forced-alignment words "
                   "missing, added or substituted in Whisper; complete means every selected shot has current "
                   "bound inputs, valid times and at least one equal-word comparison."}
    jsonio.write_atomic(vf.dir / "voice" / "align" / "compare.json", {"generated": "do not edit by hand: "
                                                                                    "slate voice align --compare", **out})
    return out
