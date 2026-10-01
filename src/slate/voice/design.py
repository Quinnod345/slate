"""Voice Design: described voices, generated as previews, measured and ranked like a shootout.

The films' method (a character-voice brief, a design spec, the Voice Design client, measure_previews.py,
rank_previews.py and tts_check.py), generalized:

1. A spec: preview text (100-1,000 characters, in the voice's own words), prompts (each a voice description of
   20-1,000 characters, optionally the pitch window it aims for), and passes (seed x guidance scale x prompts).
   ElevenLabs' prompt order works best: "[Language]. [Gender], [age]. [Quality]. Persona: ... Emotion: ... [timbre,
   pacing, delivery]". Say "thick" rather than "strong" for accents; avoid "reverb" and "echo".
2. Each call returns three previews on `eleven_ttv_v3` (always sent: the API's default is an older model).
3. Every preview is measured (pace, pauses, pitch, endings, noise floor, what Whisper heard) and ranked.
4. `save` keeps a preview as a voice in the account; `check` renders the saved voices on the production model,
   because designed voices sound different there (on the films, 1.4-2.3 semitones higher and faster).
The owner picks by ear.
"""

from __future__ import annotations

import time
from pathlib import Path

from slate import config, jsonio, log, paths
from slate.errors import UsageError
from slate.vendors import elevenlabs
from slate.voice import align as align_mod
from slate.voice import measure, rank
from slate.voice import profile as profile_mod
from slate.voice.cast import onboarding_billing, slug
from slate.voice.film import VoiceFilm, now


def quick_spec(description: str, text: str, seeds: list[int], guidance: list[float], pitch_hz=None) -> dict:
    return {"preview_text": text, "loudness": 0.5,
            "prompts": {"main": {"name": "main", "voice_description": description,
                                 **({"pitch_hz": pitch_hz} if pitch_hz else {})}},
            "passes": [{"seed": s, "guidance_scale": g, "prompts": ["main"]} for s in seeds for g in guidance]}


def validate_spec(spec: dict) -> None:
    text = spec.get("preview_text") or ""
    if not 100 <= len(text) <= 1000:
        raise UsageError(f"The preview text must be 100-1,000 characters (got {len(text)}); longer text gives "
                         "steadier previews.")
    prompts = spec.get("prompts") or {}
    if not prompts:
        raise UsageError("The spec has no prompts.")
    for key, p in prompts.items():
        d = p.get("voice_description") or ""
        if not 20 <= len(d) <= 1000:
            raise UsageError(f"Prompt {key!r}: a voice description must be 20-1,000 characters (got {len(d)}).")
    for i, ps in enumerate(spec.get("passes") or [], 1):
        for key in ps.get("prompts") or list(prompts):
            if key not in prompts:
                raise UsageError(f"Pass {i} names prompt {key!r}, which the spec doesn't have.")
    if not spec.get("passes"):
        raise UsageError("The spec has no passes (seed and guidance scale per call).")


def _folder(vf: VoiceFilm | None, prof: dict, name: str, out: str | Path | None) -> Path:
    if out:
        return Path(out)
    if vf:
        return vf.dir / "voice" / "design" / name
    return paths.data() / "voice" / "design" / slug(prof.get("brand") or "brand") / name


def run(film_dir: str | Path | None = None, *, brand: str | None = None, spec: dict, name: str | None = None,
        out: str | Path | None = None) -> dict:
    vf = VoiceFilm(film_dir) if film_dir else None
    prof = vf.profile if vf else profile_mod.load(brand)
    validate_spec(spec)
    name = slug(name or f"design-{time.strftime('%Y%m%d-%H%M%S')}")
    folder = _folder(vf, prof, name, out)
    model = spec.get("model_id") or config.model("voice_design")
    client = elevenlabs.Client(mode=vf.mode if vf else "supervised",
                               film=vf.dir if vf else onboarding_billing(prof.get("brand") or brand))
    fmt = client.choose_format(prof.get("output_format") or "pcm_48000")
    manifest_path = folder / "previews.json"
    manifest = jsonio.read(manifest_path, default=None) or {"previews": [], "saved_voices": []}
    manifest["spec"] = spec
    text = spec["preview_text"]
    rows = []
    for ps in spec["passes"]:
        g = ps.get("guidance_scale", 5.0)
        gtag = f"{g:g}".replace(".", "p")
        for key in ps.get("prompts") or list(spec["prompts"]):
            p = spec["prompts"][key]
            res = client.design(voice_description=p["voice_description"], text=text, model_id=model,
                                seed=ps.get("seed"), guidance_scale=g, loudness=spec.get("loudness", 0.5),
                                output_format=fmt["format"], out_dir=folder / "previews",
                                prefix=f"{key}_s{ps.get('seed')}_g{gtag}")
            for pv in res["previews"]:
                rows.append({"preview_id": f"{key}-s{ps.get('seed')}-g{gtag}-{pv['index']}", "prompt_key": key,
                             "prompt_name": p.get("name", key), "voice_description": p["voice_description"],
                             "pitch_hz": p.get("pitch_hz"), "model_id": res["model_id"], "seed": ps.get("seed"),
                             "guidance_scale": g, "generated_voice_id": pv["generated_voice_id"],
                             "file": str(pv["file"]), "format": res["format"], "request_id": res["request_id"],
                             "replay": res["replay"], "words": pv.get("words"), "created_at": now()})
    need = [Path(r["file"]) for r in rows if r["words"] is None]
    heard = align_mod.run_whisper(need, folder / "whisper", language=prof.get("language") or "en") \
        if need and align_mod.whisper_info()["path"] else {}
    if need and not heard:
        log.warn("Whisper isn't installed, so the previews are ranked without the word check")
    for r in rows:
        doc = heard.get(Path(r["file"]))
        words = r.pop("words") if r.get("words") is not None else (measure.whisper_words(doc) if doc else None)
        r.pop("words", None)
        r["metrics"] = measure.measure_take(r["file"], script=text, words=words, lexicon=prof.get("lexicon") or [],
                                            language=prof.get("language") or "en")
    known = {r["preview_id"] for r in rows}
    manifest["previews"] = [r for r in manifest["previews"] if r["preview_id"] not in known] + rows
    ranking = rank.rank([{"id": r["preview_id"], "prompt_key": r["prompt_key"], "pitch_hz": r.get("pitch_hz"),
                          "metrics": r["metrics"]} for r in manifest["previews"]], prof.get("targets") or {},
                        group="prompt_key")
    jsonio.write_atomic(manifest_path, {"generated": "do not edit by hand: slate voice design", **manifest})
    jsonio.write_atomic(folder / "ranking.json", ranking)
    return {"name": name, "folder": str(folder), "previews": len(rows), "model": model,
            "best": {k: (v["id"], v["score"]) for k, v in ranking.get("best_by_prompt_key", {}).items()},
            "top": [(r["id"], r["score"]) for r in ranking["ranked"][:5]], "replay": client.replay}


def save(folder: str | Path, preview: str, voice_name: str, *, film_dir: str | Path | None = None,
         labels: dict | None = None, brand: str | None = None) -> dict:
    """Keep a preview (by preview id or generated voice id) as a voice in the account."""
    folder = Path(folder)
    manifest_path = folder / "previews.json"
    manifest = jsonio.read(manifest_path, default=None) or {"previews": [], "saved_voices": []}
    row = next((r for r in manifest.get("previews", []) if preview in (r["preview_id"], r["generated_voice_id"])),
               None)
    remix = jsonio.read(folder / "remix.json", default=None) or {}
    if row is None:
        row = next((r for r in remix.get("remixes", []) if r.get("generated_voice_id") == preview), None)
        if row:
            row = {"preview_id": preview, "generated_voice_id": preview,
                   "voice_description": remix.get("description")}
    if row is None:
        raise UsageError(f"No preview {preview!r} in {folder}.", hint="The ids are in previews.json (or remix.json).")
    vf = VoiceFilm(film_dir) if film_dir else None
    client = elevenlabs.Client(mode=vf.mode if vf else "supervised",
                               film=vf.dir if vf else onboarding_billing(brand))
    res = client.save_voice(generated_voice_id=row["generated_voice_id"], voice_name=voice_name,
                            voice_description=row.get("voice_description") or voice_name,
                            labels={"made_with": "slate", **(labels or {})})
    rec = {"voice_id": res.get("voice_id"), "name": res.get("name") or voice_name, "from_preview": row["preview_id"],
           "generated_voice_id": row["generated_voice_id"], "saved_at": now(), "replay": bool(res.get("replay"))}
    manifest.setdefault("saved_voices", [])
    manifest["saved_voices"] = [s for s in manifest["saved_voices"] if s.get("from_preview") != rec["from_preview"]]
    manifest["saved_voices"].append(rec)
    jsonio.write_atomic(manifest_path, {"generated": "do not edit by hand: slate voice design",
                                        **{k: v for k, v in manifest.items() if k != "generated"}})
    rec["next"] = (f"Add it to the brand's voice profile as a candidate: {{\"voice_id\": \"{rec['voice_id']}\", "
                   f"\"name\": \"{rec['name']}\", \"source\": \"designed\"}}, then `slate voice design --check`.")
    return rec


def check(folder: str | Path, *, text: str | None = None, models: list[str] | None = None, seed: int = 7,
          film_dir: str | Path | None = None, brand: str | None = None) -> dict:
    """Render every saved voice on the production model(s), measured, because designed voices change there."""
    folder = Path(folder)
    manifest = jsonio.read(folder / "previews.json", default=None) or {}
    saved = manifest.get("saved_voices") or []
    if not saved:
        raise UsageError("No saved voices to check yet.", hint="slate voice design --save <preview> --voice-name <n>")
    vf = VoiceFilm(film_dir) if film_dir else None
    prof = vf.profile if vf else profile_mod.load(brand)
    text = text or (manifest.get("spec") or {}).get("preview_text")
    if not text:
        raise UsageError("Pass --text for the check read.")
    models = models or [config.model("tts")]
    client = elevenlabs.Client(mode=vf.mode if vf else "supervised",
                               film=vf.dir if vf else onboarding_billing(prof.get("brand") or brand))
    rows = []
    for sv in saved:
        for m in models:
            res = client.tts(voice_id=sv["voice_id"], text=text, model_id=m, seed=seed,
                             out=folder / "tts_check" / f"{slug(sv['name'])}_{m}.wav", note=f"design check {m}")
            rows.append({"voice": sv["name"], "voice_id": sv["voice_id"], "model": m, "file": res["file"],
                         "request_id": res["request_id"], "replay": res["replay"], "words": res.get("words")})
    need = [Path(r["file"]) for r in rows if r["words"] is None]
    heard = align_mod.run_whisper(need, folder / "tts_check" / "whisper", language=prof.get("language") or "en") \
        if need and align_mod.whisper_info()["path"] else {}
    for r in rows:
        doc = heard.get(Path(r["file"]))
        words = r.pop("words") if r.get("words") is not None else (measure.whisper_words(doc) if doc else None)
        r.pop("words", None)
        r["metrics"] = measure.measure_take(r["file"], script=text, words=words, lexicon=prof.get("lexicon") or [],
                                            language=prof.get("language") or "en")
    out = {"generated": "do not edit by hand: slate voice design --check", "text": text, "models": models,
           "seed": seed, "renders": rows, "at": now()}
    jsonio.write_atomic(folder / "tts_check" / "tts_check.json", out)
    return out
