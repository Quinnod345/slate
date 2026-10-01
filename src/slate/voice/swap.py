"""`slate voice swap`: the takes the owner approved by ear go into the film, exactly as heard, and the film re-times.

One command, after the owner has listened to a set of takes (a preview, an A/B, a tag round) and said yes. Without
--go it prints the plan and writes nothing; nothing downstream re-times until the swap is approved. With --go (and
who approved it), in order, stopping at the first step that fails, with the step named and every step in a log:

1. the files the owner heard: each one checked by its SHA-256, so what goes in is exactly what was heard (an approved
   take is never regenerated);
2. the approval, recorded for the set (voice/swaps/<name>.approval.json and the film's approvals);
3. a backup of what the swap replaces: voice/takes.json, each replaced take's files and its alignment, under
   voice/backups/<UTC time>/;
4. the swap: each file becomes a new take of its shot, selected, approved and locked by hash;
5. Whisper on the new takes (slate.voice.align, Slate's one runner);
6. the re-timing: the storyboard from the takes (`slate script build --from-takes`), the film clock (`slate timing`,
   with the owner's approval when act renders lock it), the captions (`slate voice captions`).

Then it says what's left downstream (the mix, the renders), which `slate make` brings up to date.

The set (voice/swaps/<name>.json, or any JSON file):

    {"name": "round-4", "takes": {"S01": {"file": "voice/tags/main/paced/S01_A.wav", "sha256": "…",
                                          "stage": "paced"}}}

`stage` says which of a take's stages the file is (raw, clean, paced or polished; paced by default, the take as the
film plays it before the studio polish).
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path
from typing import Callable

from slate import jsonio
from slate.errors import Refused, SlateError, UsageError
from slate.voice.film import TAKE_STAGES, VoiceFilm, approve, lock_asset, now, record

STEPS = ("the files the owner heard", "the approval", "a backup of what's replaced", "the swap",
         "Whisper on the new takes", "the re-timing")
Runner = Callable[[list[str]], tuple[int, str, str]]


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    from slate.cli import run_cli  # noqa: PLC0415

    return run_cli(argv)


def load_set(film_dir: Path, set_path: str | Path) -> dict:
    p = Path(set_path).expanduser()
    p = p if p.is_absolute() or p.exists() else Path(film_dir) / p
    if not p.is_file():
        raise UsageError(f"No swap set at {p}.", hint="A JSON file: {\"name\": …, \"takes\": {shot: {file, sha256}}}.")
    doc = jsonio.read(p)
    takes = doc.get("takes") or {}
    if not isinstance(takes, dict) or not takes:
        raise UsageError(f"{p} names no takes to swap in.")
    for shot, t in takes.items():
        if not isinstance(t, dict) or not t.get("file") or not t.get("sha256"):
            raise UsageError(f"{p}: {shot} needs the file the owner heard and its sha256.")
        if t.get("stage", "paced") not in TAKE_STAGES:
            raise UsageError(f"{p}: {shot}'s stage is one of {', '.join(TAKE_STAGES)}.")
    return {"path": p, "name": str(doc.get("name") or p.stem), "takes": takes}


def _file(vf: VoiceFilm, rel: str) -> Path:
    p = Path(str(rel)).expanduser()
    return p if p.is_absolute() else vf.dir / p


def plan(film_dir: str | Path, set_path: str | Path) -> dict:
    """What a swap would do, shot by shot, and the steps it would run. Writes nothing."""
    vf = VoiceFilm(film_dir)
    s = load_set(vf.dir, set_path)
    rows = []
    for shot, t in s["takes"].items():
        vf.line(shot)
        sel = vf.selected(shot)
        f = _file(vf, t["file"])
        rows.append({"shot": shot, "replaces": sel[0] if sel else None, "with": t["file"],
                     "stage": t.get("stage", "paced"), "heard_as_is": f.is_file() and jsonio.sha256(f) == t["sha256"],
                     "approved_now": bool(vf.approved(shot))})
    return {"film": vf.id, "set": s["name"], "file": vf.rel(s["path"]), "takes": rows, "steps": list(STEPS),
            "go": f"slate voice swap {vf.rel(s['path'])} --go --by NAME"}


class _Log:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, step: str, status: str, detail: object = None) -> None:
        line = {"step": step, "status": status, "at": now(), **({"detail": detail} if detail is not None else {})}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def go(film_dir: str | Path, set_path: str | Path, *, by: str, reason: str | None = None,
       runner: Runner | None = None) -> dict:
    """Do the swap (see the module docstring). Raises Refused naming the step that failed; the log keeps the rest."""
    if not (by or "").strip():
        raise UsageError("A swap puts approved takes in the film: say who approved them (--by NAME).")
    vf = VoiceFilm(film_dir)
    s = load_set(vf.dir, set_path)
    runner = runner or _default_runner
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = _Log(vf.path(f"voice/swaps/{s['name']}.log.jsonl"))
    done: dict[str, object] = {}

    def step(i: int, fn: Callable[[], object]) -> object:
        name = STEPS[i]
        try:
            out = fn()
        except SlateError as exc:
            log.write(name, "FAILED", exc.message)
            raise Refused(f"The swap stopped at step {i + 1}, {name}: {exc.message}",
                          hint=exc.hint or f"See {vf.rel(log.path)}; fix it and run the swap again.",
                          details={"step": i + 1, "name": name, "log": vf.rel(log.path), "done": done}) from exc
        log.write(name, "done", out)
        done[name] = out
        return out

    shots = list(s["takes"])

    def heard() -> list[str]:
        bad = []
        for shot, t in s["takes"].items():
            vf.line(shot)
            f = _file(vf, t["file"])
            if not f.is_file():
                bad.append(f"{shot}: {t['file']} isn't there")
            elif jsonio.sha256(f) != t["sha256"]:
                bad.append(f"{shot}: {t['file']} has changed since it was heard")
        if bad:
            raise Refused("; ".join(bad), hint="Swap in only the exact files the owner heard; re-listen to any that "
                                               "changed.")
        return shots

    def approval() -> str:
        kept = vf.path(f"voice/swaps/{s['name']}.json")  # the set as approved, kept in the film
        if Path(s["path"]).resolve() != kept.resolve():
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s["path"], kept)
        rec = approve(vf.dir, what="voice_swap", path=vf.rel(kept), approved_by=by, note=reason)
        jsonio.write_atomic(vf.path(f"voice/swaps/{s['name']}.approval.json"),
                            {"set": vf.rel(kept), "approved_by": by, "reason": reason, "at": now(), "record": rec,
                             "takes": {k: v["sha256"] for k, v in s["takes"].items()}})
        return by

    def backup() -> str:
        folder = vf.path(f"voice/backups/{stamp}")
        folder.mkdir(parents=True, exist_ok=True)
        if vf.manifest_path.is_file():
            shutil.copyfile(vf.manifest_path, folder / "takes.json")
        for shot in shots:
            sel = vf.selected(shot)
            if sel:
                for stage in TAKE_STAGES:
                    rel = sel[1].get(stage)
                    if rel and (vf.dir / rel).is_file():
                        dst = folder / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(vf.dir / rel, dst)
            al = vf.path(f"voice/align/{shot}.json")
            if al.is_file():
                (folder / "align").mkdir(exist_ok=True)
                shutil.copyfile(al, folder / "align" / al.name)
        return vf.rel(folder)

    def swap_in() -> dict:
        out = {}
        for shot, t in s["takes"].items():
            src = _file(vf, t["file"])
            stage = t.get("stage", "paced")
            prev = vf.selected(shot)
            base = vf.take_json(shot, prev[0]) if prev else {}
            take = vf.next_take_id(shot)
            tdir = vf.take_dir(shot)
            tdir.mkdir(parents=True, exist_ok=True)
            names = {"raw": f"{take}.wav", "clean": f"{take}.clean.wav", "paced": f"{take}.paced.wav",
                     "polished": f"{take}.polished.wav"}
            files = {"raw": tdir / names["raw"]}
            if stage != "raw":
                files[stage] = tdir / names[stage]
            for dst in files.values():
                shutil.copyfile(src, dst)
            from slate.voice import audio as audio_mod  # noqa: PLC0415

            seconds = round(audio_mod.duration(files["raw"]), 3)
            ln = vf.line(shot)
            data = {"generated": "do not edit by hand: slate voice swap", "shot": shot, "take": take, "film": vf.id,
                    "brand": base.get("brand"), "mode": vf.mode, "text": ln.text,
                    "sent": t.get("sent") or base.get("sent") or ln.text, "model": t.get("model") or
                    base.get("model") or "unknown", "voice_id": t.get("voice_id") or base.get("voice_id") or "unknown",
                    "voice_settings": t.get("voice_settings") or base.get("voice_settings") or {}, "seed": t.get("seed"),
                    "format": t.get("format") or base.get("format") or "wav",
                    "request_id": t.get("request_id") or f"swap-{s['name']}-{shot}",
                    "replay": bool(t.get("replay", False)), "file": vf.rel(files["raw"]),
                    "sha256": jsonio.sha256(files["raw"]), "seconds": seconds, "created_at": now(),
                    "stages": {"swap": {"set": s["name"], "file": t["file"], "sha256": t["sha256"], "stage": stage,
                                        "replaces": prev[0] if prev else None, "approved_by": by}}}
            if t.get("words"):
                data["replay_words"] = t["words"]
            vf.save_take_json(shot, take, data)
            vf.update_take(shot, take, **{k: vf.rel(v) for k, v in files.items()},
                           **{k: None for k in TAKE_STAGES if k not in files}, align=None, replay=data["replay"],
                           seconds=seconds, json=vf.rel(tdir / f"{take}.json"))
            vf.select_take(shot, take)
            rels = [vf.rel(p) for p in files.values()]
            for rel in rels:
                record(vf.dir, rel, producer="slate voice swap", inputs={t["file"]: t["sha256"]},
                       note=f"swapped in from {s['name']}, exactly as heard")
                lock_asset(vf.dir, rel, reason=f"approved by ear ({by}), swapped in from {s['name']}", by=by)
            manifest = vf.manifest()
            manifest["shots"][shot]["approved"] = {"take": take, "by": by, "at": now(), "note": reason or
                                                   f"swapped in from {s['name']}",
                                                   "sha256": {rel: jsonio.sha256(vf.dir / rel) for rel in rels}}
            vf.save_manifest(manifest)
            out[shot] = take
        return out

    def transcribe() -> dict:
        from slate.voice import align as align_mod  # noqa: PLC0415

        res = align_mod.align(vf.dir, only=shots)
        if res.get("missing"):
            raise Refused(f"Whisper couldn't hear {', '.join(res['missing'])}.")
        return {"aligned": [r["shot"] for r in res["aligned"]]}

    def retime() -> list[str]:
        from slate.timing.builder import timing_lock_reason  # noqa: PLC0415

        lock = ["--force", "--approved-by", by, "--reason", reason or f"voice swap {s['name']}"] \
            if timing_lock_reason(vf.dir) else []
        ran = []
        for argv in (["script", "build", "--from-takes"], ["timing", *lock], ["voice", "captions"]):
            code, out, err = runner([*argv, "--film", str(vf.dir)])
            ran.append(" ".join(["slate", *argv]))
            if code != 0:
                tail = (err or out or "").strip().splitlines()[-1:] or [f"exit {code}"]
                raise Refused(f"`slate {' '.join(argv)}` exited {code}: {tail[0]}")
        return ran

    for i, fn in enumerate((heard, approval, backup, swap_in, transcribe, retime)):
        step(i, fn)
    return {"film": vf.id, "set": s["name"], "takes": done[STEPS[3]], "backup": done[STEPS[2]],
            "log": vf.rel(log.path), "ran": done[STEPS[5]],
            "next": "slate make all (the mix, the renders and everything after them are re-made from the new timing)"}
