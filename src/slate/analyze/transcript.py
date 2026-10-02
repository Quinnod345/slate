"""The voice-over of a reference film: local Whisper with word timings, cleaned, measured and joined to the scenes.

Whisper runs the way the films ran it (turbo, word timestamps, the film's language), through Slate's one Whisper
runner, slate.voice.align.transcribe: the CLI SLATE_WHISPER (or slate.toml's [voice] whisper) names, the job queue's
whisper lock, and the model setting `slate timing` uses (slate.timing.align). In replay mode (SLATE_FAKE_VENDORS=1:
tests, evals, the demo) nothing is transcribed: a recorded fixture answers, marked replay, so it can never pass for a
real transcript.

The cleanup generalizes the films' build_analysis_data.py, which had to drop a hallucination loop Whisper produced
at the end of the audio and one spurious word. The rules here are general, and every dropped segment is listed with
its reason:

- anything that starts at or after the end of the audio is dropped;
- an empty segment is dropped;
- a segment that repeats the one before it word for word is a loop, and is dropped;
- in the last LOOP_TAIL_S of the audio, a segment whose words Whisper was barely sure of is dropped.

Low-confidence words (below LOW_CONFIDENCE) are flagged, never changed: a person, or a second model, decides.
"""

from __future__ import annotations

import re
from pathlib import Path

from slate import jsonio, replay
from slate.errors import MissingPrerequisite, SlateError
from slate.gates import GateResult, engine as gate_engine

LOW_CONFIDENCE = 0.5
PAUSE_S = 0.8
LOOP_TAIL_S = 15.0
TAIL_MIN_PROBABILITY = 0.3


def _align():
    """The timing module's Whisper settings (the model) and its replay stand-in."""
    from slate.timing import align  # noqa: PLC0415

    return align


def _runner():
    """Slate's one Whisper runner (slate.voice.align): the CLI, the whisper lock and the flags every stage shares."""
    from slate.voice import align as voice_align  # noqa: PLC0415

    return voice_align


def transcribe(audio: Path, *, sha256: str, duration_s: float, language: str) -> tuple[dict, dict]:
    """(Whisper's JSON, engine) for one file. Replayed in replay mode; otherwise slate.voice.align.transcribe, which
    holds the machine-wide whisper lock, with a timeout that grows with the film."""
    model = _align().whisper_model(None)
    if replay.fake_vendors():
        request = {"line": "", "seconds": duration_s, "language": language, "file_sha256": sha256}
        result = replay.respond("whisper", "transcribe", request, key=f"analyze-{sha256[:16]}-{model}")
        return result, {"engine": "whisper", "model": model, "version": "replay", "replay": True}
    runner = _runner()
    timeout = max(900.0, 8.0 * float(duration_s or 0))
    result = runner.transcribe(audio, model=model, language=language, timeout=timeout)
    version = runner.whisper_info().get("version") or "unknown"
    return result, {"engine": "whisper", "model": model, "version": str(version), "replay": False}


def _norm(text: str) -> str:
    return re.sub(r"[^\w']+", " ", str(text).lower()).strip()


def clean(result: dict, duration_s: float | None) -> dict:
    """{segments, words, dropped, low_confidence} from Whisper's JSON, by the rules in the module docstring."""
    kept, dropped = [], []
    end = float(duration_s) if duration_s else None
    previous = None
    for seg in result.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        start = float(seg.get("start", 0.0))
        words = [w for w in seg.get("words") or [] if str(w.get("word", "")).strip()]
        reason = None
        if end is not None and start >= end:
            reason = "starts after the audio ends"
        elif not text:
            reason = "empty"
        elif previous is not None and _norm(text) == previous:
            reason = "repeats the segment before it (a Whisper loop)"
        elif end is not None and start >= end - LOOP_TAIL_S and words and \
                sum(float(w.get("probability", 1.0)) for w in words) / len(words) < TAIL_MIN_PROBABILITY:
            reason = "barely-heard words at the end of the audio (a Whisper hallucination)"
        if reason:
            dropped.append({"start": round(start, 3), "end": round(float(seg.get("end", start)), 3), "text": text,
                            "reason": reason})
            continue
        previous = _norm(text)
        kept.append({"start": round(start, 3), "end": round(float(seg.get("end", start)), 3), "text": text,
                     "words": [{"w": str(w["word"]).strip(), "start": round(float(w["start"]), 3),
                                "end": round(float(w["end"]), 3), "p": round(float(w.get("probability", 1.0)), 3)}
                               for w in words
                               if end is None or float(w.get("start", 0.0)) < end]})
    words = [w for s in kept for w in s["words"]]
    low = [w for w in words if w["p"] < LOW_CONFIDENCE]
    return {"segments": kept, "words": words, "dropped": dropped, "low_confidence": low}


def stats(words: list[dict]) -> dict:
    """Words, the span they cover, words a minute and a second over that span, and pauses longer than PAUSE_S."""
    if not words:
        return {"words": 0, "first_s": None, "last_s": None, "wpm": None, "words_per_second": None, "pauses": []}
    first, last = words[0]["start"], words[-1]["end"]
    span = max(1e-6, last - first)
    pauses = [{"from": round(a["end"], 3), "to": round(b["start"], 3), "gap": round(b["start"] - a["end"], 3)}
              for a, b in zip(words, words[1:]) if b["start"] - a["end"] > PAUSE_S]
    return {"words": len(words), "first_s": first, "last_s": last, "wpm": round(len(words) / span * 60, 1),
            "words_per_second": round(len(words) / span, 3), "pauses": pauses}


def join_scenes(scenes: list[dict], segments: list[dict]) -> None:
    """Give each scene the voice-over whose segments' midpoints fall inside it (whole sentences, as the films did)."""
    for sc in scenes:
        inside = [s for s in segments if sc["start"] <= (s["start"] + s["end"]) / 2 < sc["end"]]
        words = [w for s in inside for w in s["words"]]
        sc["vo"] = " ".join(s["text"] for s in inside)
        sc["vo_words"] = len(words)
        sc["vo_first"] = words[0]["start"] if words else None
        sc["vo_last"] = words[-1]["end"] if words else None
        sc["low_conf"] = [f"{w['w']}@{w['start']:.2f}({w['p']:.2f})" for w in words if w["p"] < LOW_CONFIDENCE]


def write_files(out_dir: Path, raw: dict, cleaned: dict) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(out_dir / "whisper.json", raw)
    with (out_dir / "words.tsv").open("w", encoding="utf-8") as fh:
        fh.write("idx\tstart\tend\tprob\tword\n")
        for i, w in enumerate(cleaned["words"]):
            fh.write(f"{i}\t{w['start']:.3f}\t{w['end']:.3f}\t{w['p']:.2f}\t{w['w']}\n")
    with (out_dir / "segments.tsv").open("w", encoding="utf-8") as fh:
        fh.write("start\tend\ttext\n")
        for s in cleaned["segments"]:
            fh.write(f"{s['start']:.3f}\t{s['end']:.3f}\t{s['text']}\n")
    return ["transcript/whisper.json", "transcript/words.tsv", "transcript/segments.tsv"]


def run(audio: Path, meta: dict, out_dir: Path, *, language: str, source: str) -> tuple[GateResult, dict]:
    """Transcribe, clean and measure. UNCHECKED (never a crash) when Whisper isn't there or fails."""
    duration = float(meta.get("duration_s") or 0)
    try:
        raw, eng = transcribe(audio, sha256=meta["sha256"], duration_s=duration, language=language)
    except MissingPrerequisite as exc:
        return GateResult.unchecked("analyze.transcript", f"no transcript: {exc.message}",
                                    evidence=[f"next: {exc.hint}"] if exc.hint else [],
                                    engine=gate_engine("whisper", "not installed")), {}
    except SlateError as exc:
        return GateResult.unchecked("analyze.transcript", f"Whisper didn't finish: {exc.message.splitlines()[0]}",
                                    engine=gate_engine("whisper", "unknown")), {}
    cleaned = clean(raw, duration or None)
    measured = stats(cleaned["words"])
    files = write_files(out_dir / "transcript", raw, cleaned)
    engine = gate_engine("whisper", eng["version"], model=eng["model"], replay=eng["replay"])
    summary = f"{measured['words']} words" + (f" at {measured['wpm']:g} words a minute" if measured["wpm"] else "")
    if cleaned["dropped"]:
        summary += f"; {len(cleaned['dropped'])} Whisper segment{'s' if len(cleaned['dropped']) != 1 else ''} dropped"
    if eng["replay"]:
        summary += " (replayed: not a real transcript)"
    data = {"engine": eng, "source": source, "language": language, **cleaned, "stats": measured, "files": files}
    return GateResult.pass_("analyze.transcript", summary, evidence=files, engine=engine), data
