"""Hearing the takes: word timestamps from local Whisper (default) or ElevenLabs Forced Alignment.

Whisper runs exactly as the films ran it: `whisper <take> --model turbo --language <lang> --word_timestamps True
--output_format json --fp16 False` (openai-whisper from Homebrew; the model name and the CLI's version go into every
result as evidence). Results are cached per take in timing/align/<engine>/<shot>.json and reused while the take's
SHA-256, the engine, the model and the language are unchanged, so a rebuild never re-transcribes an approved take.

In replay mode (SLATE_FAKE_VENDORS=1: tests, evals, `slate demo`) nothing is transcribed: a recorded fixture answers
(`fixtures/vendors/whisper/<key>.json`), or a deterministic stand-in spreads the script line across the take, marked
`replay: true` so it can never pass for a real alignment in a delivery.

ElevenLabs Forced Alignment is a paid call. It goes through the voice module's client (`slate.vendors.elevenlabs`),
which estimates, checks the budget and writes the ledger row; it is here to evaluate against Whisper (Plan, Appendix A).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import os
import re
import tempfile
from pathlib import Path

from slate import config, jsonio, log, procs, replay
from slate.errors import MissingPrerequisite, SlateError
from slate.timing.takes import Take

ENGINES = ("whisper", "elevenlabs")
WHISPER_TIMEOUT_S = 900  # one take; turbo on this Mac's CPU takes well under a minute for a 10 s line


# Registry model ids for the alignment role -> the whisper CLI's model names.
WHISPER_MODELS = {"whisper_turbo": "turbo", "whisper_large_v3_turbo": "large-v3-turbo"}


def whisper_model(film: dict | None = None) -> str:
    """The Whisper model the CLI takes ("turbo"): the alignment role in registry.json, which a film can pin."""
    try:
        chosen = config.model("alignment", film)
    except Exception:  # noqa: BLE001 - an old registry still gets the films' model
        try:
            chosen = str(config.tool("whisper-model").get("version") or "turbo")
        except Exception:  # noqa: BLE001
            chosen = "turbo"
    return WHISPER_MODELS.get(chosen, chosen)


# The voice module's switch, honoured here too, so both Whisper runners use the same CLI: SLATE_WHISPER (or slate.toml's
# [voice] whisper) names one particular Whisper (a build outside Homebrew, or a test's stand-in). Slate's PATH puts
# Homebrew first whatever the caller's PATH says, so a PATH entry can't do this job.
WHISPER_ENV = "SLATE_WHISPER"


def whisper_exe(*, required: bool = True) -> str | None:
    """The Whisper CLI to run: SLATE_WHISPER (or [voice] whisper), else `whisper` on Slate's PATH."""
    chosen = os.environ.get(WHISPER_ENV) or (config.load().get("voice") or {}).get("whisper")
    if chosen:
        path = str(Path(str(chosen)).expanduser())
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        if required:
            raise MissingPrerequisite(f"{WHISPER_ENV} names {path}, which isn't an executable file.",
                                      hint="Point it at a Whisper CLI, or unset it to use the one on Slate's PATH.")
        return None
    if required:
        return procs.require("whisper", hint="Install openai-whisper (Homebrew) with `slate doctor --install`.")
    return procs.which("whisper")


def whisper_version() -> str:
    """openai-whisper's version, read from the Homebrew Cellar path of the `whisper` CLI (no Python import needed)."""
    exe = whisper_exe(required=False)
    if not exe:
        return "not installed"
    real = os.path.realpath(exe)
    m = re.search(r"openai-whisper/([^/]+)/", real)
    return m.group(1) if m else "unknown"


def _fake_whisper(key: str, request: dict) -> dict:
    """A deterministic stand-in transcript: the script line's words spread evenly across the take."""
    words = [w for w in (request.get("line") or "").split() if w.strip()]
    length = float(request.get("seconds") or max(1.0, 0.4 * len(words)))
    if not words:
        return {"text": "", "segments": [], "language": request.get("language")}
    start, end = min(0.1, length / 10), max(length - 0.1, length * 0.9)
    step = (end - start) / len(words)
    out = [{"word": f" {w}", "start": round(start + i * step, 3), "end": round(start + (i + 1) * step - 0.02, 3),
            "probability": 1.0} for i, w in enumerate(words)]
    text = " " + " ".join(words)
    return {"text": text, "segments": [{"id": 0, "start": out[0]["start"], "end": out[-1]["end"], "text": text,
                                        "words": out}], "language": request.get("language")}


replay.register_faker("whisper", "transcribe", _fake_whisper)


def cache_path(film_dir: Path, engine: str, shot: str) -> Path:
    return Path(film_dir) / "timing" / "align" / engine / f"{shot}.json"


def _hold_whisper_lock():
    """The machine-wide whisper lock from the job queue when it's installed (one heavy transcription at a time)."""
    try:
        from slate.jobs import locks  # noqa: PLC0415
    except ImportError:
        return contextlib.nullcontext()
    try:
        return locks.hold("whisper", owner="slate timing")
    except TypeError:
        return locks.hold("whisper")


def run_whisper(path: Path, *, model: str, language: str) -> dict:
    """Transcribe one take with the whisper CLI and return its JSON (segments with word timestamps)."""
    exe = whisper_exe()
    with tempfile.TemporaryDirectory(prefix="slate-whisper-") as tmp:
        procs.run([exe, str(path), "--model", model, "--language", language, "--word_timestamps", "True",
                   "--output_format", "json", "--output_dir", tmp, "--fp16", "False"],
                  check=True, timeout=WHISPER_TIMEOUT_S)
        out = Path(tmp) / f"{path.stem}.json"
        if not out.is_file():
            raise SlateError(f"whisper ran on {path.name} but wrote no JSON.",
                             hint="Run the same command by hand to see Whisper's own error.")
        return jsonio.read(out)


def _elevenlabs_align(path: Path, line: str, film_dir: Path, film: dict | None) -> tuple[dict, dict]:
    """(whisper-shaped result, engine info) from ElevenLabs Forced Alignment through the voice module's client.

    The client estimates the cost, checks the film's and the month's budget and writes the ledger row before it
    calls (or replays) the API; Slate's timing never talks to ElevenLabs directly.
    """
    try:
        from slate.vendors.elevenlabs import Client  # noqa: PLC0415
    except ImportError as exc:
        raise MissingPrerequisite("The ElevenLabs client isn't installed yet (the voice module provides it).",
                                  hint="Use `slate timing words --engine whisper`, the default aligner.") from exc
    mode = (film or {}).get("mode") or "supervised"
    result = Client(mode=mode, film=film_dir).align(audio=path, text=line)
    raw_words = (result or {}).get("words") or []
    words = [{"word": " " + str(w.get("text", w.get("word", ""))).strip(), "start": float(w["start"]),
              "end": float(w["end"])} for w in raw_words if str(w.get("text", w.get("word", ""))).strip()]
    text = " ".join(w["word"].strip() for w in words)
    shaped = {"text": text, "segments": [{"id": 0, "words": words}] if words else [], "language": None}
    try:
        model = config.model("alignment_eval")
    except Exception:  # noqa: BLE001
        model = "elevenlabs_forced_alignment"
    return shaped, {"engine": "elevenlabs", "model": model, "version": "api",
                    "replay": bool((result or {}).get("replay"))}


VOICE_ALIGN = Path("voice") / "align"
_VOICE_ENGINES = {"whisper": ("openai-whisper", "whisper"), "elevenlabs": ("elevenlabs",)}


def from_voice_stage(film_dir: Path, shot: str, sha: str, engine: str, replaying: bool) -> dict | None:
    """The voice stage's own alignment of this exact take (voice/align/<shot>.json), when it has one.

    `slate voice align` already heard every final take; timing reuses that rather than transcribing twice. It must be
    the same audio (SHA-256) and the same kind of aligner, and a replayed stand-in is only good enough in replay mode.
    """
    from slate.timing.takes import film_file  # noqa: PLC0415

    doc = jsonio.read(film_file(Path(film_dir), VOICE_ALIGN / f"{shot}.json"), default=None)
    if not isinstance(doc, dict) or doc.get("sha256") != sha or doc.get("segments") is None:
        return None
    eng = doc.get("engine") or {}
    name = str(eng.get("name") or "")
    is_replay = bool(doc.get("replay")) or name == "replay"
    if is_replay and not replaying:
        return None
    if not is_replay and not name.startswith(_VOICE_ENGINES[engine]):
        return None
    return {"take_sha256": sha, "language": doc.get("language"), "engine": engine,
            "model": eng.get("model"), "version": eng.get("version") or ("replay" if is_replay else None),
            "replay": is_replay, "from": str(VOICE_ALIGN / f"{shot}.json"),
            "result": {"text": doc.get("text", ""), "segments": doc.get("segments") or [],
                       "language": doc.get("language")}}


def align_take(film_dir: Path, shot: str, take: Take, line: str, *, engine: str = "whisper", language: str = "en",
               force: bool = False, film: dict | None = None, write_cache: bool = True) -> dict:
    """The alignment record for one take: the voice stage's, else timing's cache, else a fresh transcription."""
    if engine not in ENGINES:
        raise SlateError(f"Unknown aligner {engine!r}; use one of: {', '.join(ENGINES)}.")
    sha = take.sha256()
    replaying = replay.fake_vendors()
    model = whisper_model(film) if engine == "whisper" else None
    path = cache_path(film_dir, engine, shot)
    if not force:
        reused = from_voice_stage(film_dir, shot, sha, engine, replaying)
        if reused is not None:
            reused["take"] = take.rel
            return reused
    cached = jsonio.read(path, default=None)
    if (not force and isinstance(cached, dict) and cached.get("take_sha256") == sha and cached.get("language") == language
            and (model is None or cached.get("model") == model) and bool(cached.get("replay")) == replaying):
        return cached
    if engine == "whisper":
        if replaying:
            request = {"take_sha256": sha, "line": line, "seconds": take.read_seconds, "model": model,
                       "language": language}
            result = replay.respond("whisper", "transcribe", request, key=f"transcribe-{sha[:16]}-{model}")
            info = {"engine": "whisper", "model": model, "version": "replay", "replay": True}
        else:
            log.info(f"whisper {model}: {take.rel}")
            with _hold_whisper_lock():
                result = run_whisper(take.path, model=model, language=language)
            info = {"engine": "whisper", "model": model, "version": whisper_version(), "replay": False}
    else:
        result, info = _elevenlabs_align(take.path, line, Path(film_dir), film)
    record = {"take": take.rel, "take_sha256": sha, "language": language, **info,
              "made_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              "result": {k: v for k, v in result.items() if k not in ("replay", "replay_key")}}
    if write_cache:
        jsonio.write_atomic(path, jsonio.mark_generated(record, "slate timing words"))
    return record
