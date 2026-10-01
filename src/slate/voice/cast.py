"""Casting shootouts: several voices read the same lines, measured and ranked, with a level-matched listening reel.

From the films' voice shootout (seventeen takes across engines and voices, compared in `voice_reel.py`) and the
measured ranking behind Voice Design (`measure_previews.py`, `rank_previews.py`). Library and designed voices are
the default. Every candidate passes the cloning policy first, and in live mode ElevenLabs' own record of each
voice is checked too, so a cloned or famous voice can't slip in as a library voice.

The ranking is a shortlist. The owner picks by ear from the reel.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from slate import config, jsonio, log, paths
from slate.errors import Refused, UsageError
from slate.vendors import elevenlabs
from slate.voice import align as align_mod
from slate.voice import measure, rank, reel
from slate.voice import profile as profile_mod
from slate.voice.film import VoiceFilm, now


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()[:48] or "voice"


def onboarding_billing(brand_name: str | None) -> dict | None:
    """What a brand's voice work outside any film bills against: the brand's one-time onboarding budget
    (`slate.brand.onboard.billing`), so casting and Voice Design count per brand, once, not only against the monthly
    caps. None when there's no brand to bill (or the brand module isn't installed)."""
    if not brand_name:
        return None
    try:
        from slate.brand import onboard  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        if not str(exc.name or "").startswith("slate.brand"):
            raise
        return None
    return onboard.billing({"name": str(brand_name)})


def candidates_for(profile: dict, voices: list[str] | None) -> list[dict]:
    """Voices on the command line (library by default), else the profile's candidates plus its current voice."""
    if voices:
        known = {c["voice_id"]: c for c in profile.get("candidates") or []}
        return [dict(known.get(v, {"voice_id": v, "source": "library"})) for v in voices]
    out = [dict(c) for c in profile.get("candidates") or []]
    if profile.get("voice_id") and profile["voice_id"] not in {c["voice_id"] for c in out}:
        out.append({k: profile.get(k) for k in ("voice_id", "voice_name", "source", "clone_of", "clone_kind",
                                                "verified_by_owner", "public_figure")})
    return out


def check_candidates(cands: list[dict], mode: str, client: elevenlabs.Client) -> None:
    problems = []
    for c in cands:
        who = c.get("name") or c.get("voice_name") or c["voice_id"]
        problems += [f"{who}: {p}" for p in profile_mod.policy_problems(c, mode=mode)]
        if not client.replay:
            info = client.voice_info(c["voice_id"])
            c.setdefault("name", info.get("name"))
            c["vendor_category"] = info.get("category")
            problems += [f"{who}: {p}" for p in profile_mod.vendor_problems(info, c, mode=mode)]
    if problems:
        raise Refused("Refusing these casting candidates:\n  - " + "\n  - ".join(problems),
                      hint="Cast designed or library voices (or the owner's verified professional clone).")


def run(film_dir: str | Path | None = None, *, brand: str | None = None, voices: list[str] | None = None,
        shots: list[str] | None = None, text: str | None = None, name: str | None = None, seed: int = 7,
        model: str | None = None, out: str | Path | None = None, make_reel: bool = True) -> dict:
    vf = VoiceFilm(film_dir) if film_dir else None
    prof = vf.profile if vf else profile_mod.load(brand)
    mode = vf.mode if vf else "supervised"
    lexicon, language, targets = prof.get("lexicon") or [], prof.get("language") or "en", prof.get("targets") or {}
    cands = candidates_for(prof, voices)
    if not cands:
        raise UsageError("No voices to cast.", hint="Pass --voices <id>,<id> (library voices), or list candidates in "
                                                    "the brand's voice profile.")
    if text:
        lines = [{"id": "line1", "text": " ".join(text.split()), "prev": None, "next": None}]
    elif vf:
        picked = vf.select(shots) if shots else vf.lines[:2]
        lines = [{"id": ln.shot, "text": ln.text, "prev": vf.neighbours(ln.shot)[0], "next": vf.neighbours(ln.shot)[1]}
                 for ln in picked]
    elif prof.get("cast_text"):
        lines = [{"id": "line1", "text": prof["cast_text"], "prev": None, "next": None}]
    else:
        raise UsageError("Nothing for the voices to read.", hint="Pass --text \"<a line in the brand's voice>\".")
    if not lines:
        raise UsageError("The storyboard has no lines to read yet.", hint="Pass --text.")

    name = slug(name or f"shootout-{time.strftime('%Y%m%d-%H%M%S')}")
    folder = Path(out) if out else ((vf.dir / "voice" / "cast" / name) if vf
                                    else paths.data() / "voice" / "cast" / slug(prof.get("brand") or "brand") / name)
    client = elevenlabs.Client(mode=mode, film=vf.dir if vf else onboarding_billing(prof.get("brand") or brand))
    check_candidates(cands, mode, client)
    model = model or config.model("tts")
    fmt = client.choose_format(prof.get("output_format") or "pcm_48000")

    takes = []
    for c in cands:
        vdir = folder / slug(c.get("name") or c.get("voice_name") or c["voice_id"])
        for ln in lines:
            res = client.tts(voice_id=c["voice_id"], text=ln["text"], model_id=model, seed=seed,
                             voice_settings=prof.get("settings") or {}, previous_text=ln["prev"],
                             next_text=ln["next"], output_format=fmt["format"], out=vdir / f"{ln['id']}.wav",
                             note=f"cast {name} {c['voice_id']} {ln['id']}")
            takes.append({"voice_id": c["voice_id"], "voice": c.get("name") or c.get("voice_name") or c["voice_id"],
                          "line": ln["id"], "text": ln["text"], "file": res["file"], "request_id": res["request_id"],
                          "format": res["format"], "replay": res["replay"], "words": res.get("words")})
    need = [Path(t["file"]) for t in takes if t["words"] is None]
    heard = {}
    if need:
        if align_mod.whisper_info()["path"]:
            heard = align_mod.run_whisper(need, folder / "whisper", language=language)
        else:
            log.warn("Whisper isn't installed, so the shootout is ranked without the word check")
    for t in takes:
        doc = heard.get(Path(t["file"]))
        words = t.pop("words") if t.get("words") is not None else (measure.whisper_words(doc) if doc else None)
        t.pop("words", None)
        logprob = None
        if doc and doc.get("segments"):
            probs = [s.get("avg_logprob") for s in doc["segments"] if s.get("avg_logprob") is not None]
            logprob = sum(probs) / len(probs) if probs else None
        t["metrics"] = measure.measure_take(t["file"], script=t["text"], words=words, lexicon=lexicon,
                                            language=language, logprob=logprob)
    ranked_takes = rank.rank([{"id": f"{t['voice_id']}:{t['line']}", "voice_id": t["voice_id"], "voice": t["voice"],
                               "line": t["line"], "metrics": t["metrics"]} for t in takes], targets)
    by_voice: dict[str, list[float]] = {}
    for r in ranked_takes["ranked"]:
        by_voice.setdefault(r["voice_id"], []).append(r["score"])
    order = sorted(cands, key=lambda c: -sum(by_voice[c["voice_id"]]) / len(by_voice[c["voice_id"]]))
    ranking = [{"voice_id": c["voice_id"], "voice": c.get("name") or c.get("voice_name") or c["voice_id"],
                "source": c.get("source", "library"),
                "score": round(sum(by_voice[c["voice_id"]]) / len(by_voice[c["voice_id"]]), 3)} for c in order]
    reel_info = None
    if make_reel:
        items = [{"file": t["file"], "title": t["voice"], "detail": f"{t['line']}: {t['text'][:80]}"}
                 for c in order for t in takes if t["voice_id"] == c["voice_id"]]
        reel_info = reel.build(items, folder / "reel.wav")
    record = {"generated": "do not edit by hand: slate voice cast", "name": name, "brand": prof.get("brand"),
              "film": vf.id if vf else None, "model": model, "seed": seed, "format": fmt, "lines": lines,
              "candidates": cands, "takes": takes, "ranking": ranking, "take_ranking": ranked_takes,
              "reel": reel_info["reel"] if reel_info else None, "at": now(),
              "note": "Proxies, not ears: listen to reel.wav (level-matched to -20 LUFS) and pick by ear."}
    jsonio.write_atomic(folder / "shootout.json", record)
    jsonio.write_atomic(folder / "ranking.json", {"ranking": ranking, "take_ranking": ranked_takes})
    return {"name": name, "folder": str(folder), "ranking": ranking, "reel": record["reel"], "takes": len(takes),
            "replay": client.replay}
