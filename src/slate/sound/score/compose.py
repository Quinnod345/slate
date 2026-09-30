"""`slate score compose`: every part of the plan composed by Eleven Music, two seeds each by default.

- The model is always passed (`model_id` from registry.json's music role, or the film's pin): the API's own default is
  an older model. The first use pins it in film.json, so a recompose in week three uses week one's model.
- Takes already on disk are kept (a take is paid for; `--force` redoes it, and a locked take is never redone).
- Before the first call, the whole batch is estimated and checked against the film's and the month's budgets, so a
  batch that would break a budget stops before spending anything (exit 4). Every call then goes through the
  ElevenLabs client, which writes its own ledger row with the request id before waiting.
- Replay mode (SLATE_FAKE_VENDORS=1, the default in tests, evals and demos) spends nothing.
- Each take is saved with the exact plan it was composed from and a record (model, seed, request id, whether it was
  replayed, its QA), so assembly can tell how far the film has moved since.
- A broadcast film is flagged on every take: Eleven Music needs its Enterprise licence for film, TV and radio.

The ElevenLabs client (slate.vendors.elevenlabs) is the voice module's; this calls its `compose_music`.
"""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Any

from slate import gates, jsonio, ledger, log, replay
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.sound.film import SCORE_DIR, Film, is_locked, load
from slate.sound.score import qa
from slate.sound.score.plan import load_plan

DEFAULT_FORMAT = None  # the client picks the best format the ElevenLabs plan allows (lossless PCM where it can)


def take_path(film: Film, part: str, seed: int) -> Path:
    """Takes are WAV on disk whatever came over the wire (the client keeps an mp3 beside it when that's all it got)."""
    return film.path(f"{SCORE_DIR}/takes/{part}_s{seed}.wav")


def takes_for(film: Film, part: str) -> list[Path]:
    folder = film.path(f"{SCORE_DIR}/takes")
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob(f"{part}_s*.wav"))


def client(film: Film):
    """The ElevenLabs client for this film and mode, or MissingPrerequisite when it isn't installed."""
    try:
        from slate.vendors.elevenlabs import Client  # noqa: PLC0415
    except ImportError as exc:
        raise MissingPrerequisite("The ElevenLabs client (slate.vendors.elevenlabs) isn't installed, so the score can't "
                                  "be composed.", hint="It's part of Slate's voice module; update Slate "
                                  "(`slate update`) or install that module.") from exc
    try:
        return Client(mode=film.mode, film=film.dir)
    except TypeError:
        return Client()


def _call(cl: Any, *, plan: dict, model_id: str, seed: int, output_format: str | None, out: Path, film: Film,
          note: str | None = None) -> dict:
    """Call client.compose_music with the arguments it takes; normalise what comes back to {path, request_id, ...}."""
    fn = cl.compose_music
    params = inspect.signature(fn).parameters
    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    kw: dict[str, Any] = {}

    def offer(names: tuple[str, ...], value: Any, api: bool = False) -> bool:
        """Pass value under the first name the client declares; to a **kwargs client, only API body fields."""
        for name in names:
            if name in params:
                kw[name] = value
                return True
        if accepts_any and api:
            kw[names[0]] = value
            return True
        return False

    args: list[Any] = []
    if not offer(("composition_plan", "plan"), plan, api=True):
        args.append(plan)
    if not offer(("model_id", "model"), model_id, api=True):
        raise UsageError("The ElevenLabs client's compose_music doesn't take a model id; Slate always passes one.")
    offer(("seed",), seed, api=True)
    if output_format:
        offer(("output_format", "fmt", "format"), output_format, api=True)
    offer(("respect_sections_durations",), True, api=True)
    offer(("out", "dest", "path", "output"), out)
    offer(("film", "film_dir"), film.dir)
    offer(("mode",), film.mode)
    if note:
        offer(("note",), note)
    result = fn(*args, **kw)
    info: dict[str, Any] = {}
    if isinstance(result, (bytes, bytearray)):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(result))
    elif isinstance(result, (str, Path)):
        src = Path(result)
        if src.resolve() != out.resolve():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
    elif isinstance(result, dict):
        info = dict(result)
        src = info.get("path") or info.get("file")
        audio_bytes = info.pop("audio", None)
        if audio_bytes is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(bytes(audio_bytes))
        elif src and Path(src).resolve() != out.resolve():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
    if not out.exists():
        raise Refused(f"The ElevenLabs client returned no audio for {out.name}.")
    return info


def estimate(film: Film, plan: dict, todo: list[tuple[dict, int]]) -> ledger.Estimate | None:
    seconds = sum(float(p["seconds"]) for p, _ in todo)
    if not todo:
        return None
    try:
        return ledger.estimate("elevenlabs", "music", model=plan["model_id"], seconds=seconds, count=1,
                               film=film.id, takes=len(todo))
    except UsageError:
        return ledger.estimate("elevenlabs", "music", amount=0, basis="no price in registry.json for this model",
                               model=plan["model_id"], seconds=seconds)


def compose(film_dir: Path, parts: list[str] | None = None, seeds: list[int] | None = None, *, force: bool = False,
            dry_run: bool = False, output_format: str | None = None) -> dict:
    film = load(film_dir)
    plan = load_plan(film)
    fmt = output_format or (film.data.get("sound") or {}).get("music_format") or DEFAULT_FORMAT
    seeds = list(seeds or plan.get("seeds") or [7, 21])
    by_id = {p["id"]: p for p in plan["parts"]}
    unknown = [p for p in (parts or []) if p not in by_id]
    if unknown:
        raise UsageError(f"No part called {', '.join(unknown)} in the score plan. Parts: {', '.join(by_id)}.")
    chosen = [by_id[p] for p in (parts or list(by_id))]
    todo, kept, locked = [], [], []
    for part in chosen:
        for seed in seeds:
            path = take_path(film, part["id"], seed)
            rel = film.rel(path)
            if path.exists() and is_locked(film, rel):
                locked.append(rel)
                continue
            if path.exists() and not force:
                kept.append(rel)
                continue
            todo.append((part, seed))
    if force and locked:
        raise Refused(f"{len(locked)} take(s) are locked (approved), so --force won't redo them: {', '.join(locked[:3])}.",
                      hint="Approved takes are never regenerated; compose a new seed instead (--seeds).")
    est = estimate(film, plan, todo)
    summary: dict[str, Any] = {"film": film.id, "model_id": plan["model_id"], "output_format": fmt, "seeds": seeds,
                               "to_compose": [f"{p['id']} s{s}" for p, s in todo], "kept": kept,
                               "estimate": est.to_dict() if est else None, "replay": replay.fake_vendors(),
                               "licence": plan.get("licence")}
    if plan.get("licence", {}).get("needs_enterprise") and "enterprise" not in str(plan["licence"].get("recorded") or "").lower():
        log.warn("broadcast use: this score needs Eleven Music's Enterprise licence before the film airs")
    if est is not None:
        ledger.check_budget(est, film=film.data, mode=film.mode)  # SpendRefused (exit 4) before any call
    if dry_run or not todo:
        summary["composed"] = []
        summary["dry_run"] = dry_run
        return summary
    cl = client(film)
    composed = []
    lock_ctx = _music_lock()
    with lock_ctx:
        for part, seed in todo:
            out = take_path(film, part["id"], seed)
            log.info(f"composing {part['id']} seed {seed} ({part['seconds']:.1f} s) on {plan['model_id']}")
            info = _call(cl, plan=part["composition_plan"], model_id=plan["model_id"], seed=seed, output_format=fmt,
                         out=out, film=film, note=f"score {part['id']} seed {seed}")
            plan_copy = out.with_name(f"{out.stem}.plan.json")
            jsonio.write_atomic(plan_copy, part["composition_plan"])
            take_qa = qa.check_file(out, film.sr)
            record = {"kind": "take", "part": part["id"], "seed": seed, "model_id": plan["model_id"],
                      "output_format": info.get("format") or fmt, "file": film.rel(out),
                      "plan_file": film.rel(plan_copy),
                      "plan_sha256": jsonio.sha256_obj(part["composition_plan"]), "seconds_planned": part["seconds"],
                      "request_id": info.get("request_id"),
                      "credits": info.get("character_cost") or info.get("credits") or info.get("cost"),
                      "replay": bool(info.get("replay", replay.fake_vendors())), "composed_at": gates.now_iso(),
                      "qa": {k: take_qa[k] for k in ("duration", "problems", "score", "rms_median")},
                      "licence": plan.get("licence")}
            jsonio.write_atomic(out.with_name(f"{out.stem}.json"), record)
            composed.append(record)
            log.info(f"  {out.name}: qa score {take_qa['score']} ({len(take_qa['problems'])} problem(s))")
    _pin_model(film, plan["model_id"])
    summary["composed"] = composed
    return summary


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _music_lock():
    """The machine-wide Eleven Music lock (the account allows two music requests at once), when the queue is installed."""
    try:
        from slate.jobs import locks  # noqa: PLC0415

        return locks.hold("elevenlabs_music")
    except (ImportError, AttributeError):
        return _NullLock()


def _pin_model(film: Film, model_id: str) -> None:
    try:
        from slate.state import pins  # noqa: PLC0415

        pins.pin(film.dir, "music", model_id)
    except (ImportError, AttributeError):
        return
    except Exception as exc:  # noqa: BLE001 - a pin that can't be written is reported, never fatal to a paid take
        log.warn(f"couldn't pin the music model in film.json: {exc}")
