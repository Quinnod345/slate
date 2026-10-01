"""Voice every line: one take per line, with its neighbours as context, pinned and fitted to its shot.

Ported from the films' `voice_script.py` and `voice_c.py`:
- each line is read with the previous and next lines' plain words as `previous_text` / `next_text`, so the reads
  flow as one narration (tags stay on their own line);
- the best format the plan allows (lossless PCM 48 kHz on Pro or higher), with the fallback recorded;
- a take never overwrites another: `voice/takes/<shot>/t01.wav`, `t02.wav`… each with a take JSON (text sent,
  model, voice id, settings, seed, format, request ids, billed characters);
- the model, voice and settings are pinned in film.json at first use, so a re-voice in week three matches week
  one; re-pinning is explicit (`--repin`, with who approved it and why);
- an approved take is locked by hash and is never re-voiced without `--force` and a spend approval;
- every read is checked against its shot: it starts `lead` seconds in and must end `tail` seconds before the cut.
"""

from __future__ import annotations

from pathlib import Path

from slate import config, jsonio, ledger, log
from slate.errors import Refused, SpendRefused, UsageError
from slate.vendors import elevenlabs
from slate.voice import profile as profile_mod
from slate.voice import tags as tags_mod
from slate.voice.film import TAKE_STAGES, VoiceFilm, get_pin, guard_regenerate, is_locked, now, pin, record

# The film's pins, in slate.state's keys: the TTS model (registry role "tts"), the voice id, the voice settings, and
# per line the first seed and the first request id.
PIN_KEYS = {"tts": "model", "voice_id": "voice_id", "voice_settings": "settings"}


def check_direction(vf: VoiceFilm, lines) -> dict[str, str]:
    """Every line's text to send, after checking it: banned tags and changed words are refused before any request."""
    policy = vf.profile.get("tag_policy") or {}
    sent, problems = {}, []
    for ln in lines:
        tagged = vf.tagged_for(ln.shot)
        for p in tags_mod.check(tagged, ln.text, policy=policy):
            problems.append(f"{ln.shot}: {p}")
        sent[ln.shot] = tagged
    if problems:
        raise Refused("The direction can't be sent as written:\n  - " + "\n  - ".join(problems),
                      hint="Fix the lines with `slate voice tags set <shot> \"<tagged line>\"` (tags change delivery, "
                           "never words), then run it again.", details={"problems": problems})
    return sent


def requested_pin(profile: dict, film_json: dict | None = None) -> dict:
    """What the voice profile asks for today, in pin keys. The model: the profile's, else the film's own model
    override, else the registry's (pins are resolved separately, in resolve_pin)."""
    unpinned = {**(film_json or {}), "pins": {}}
    return {"tts": profile.get("model") or config.model("tts", unpinned), "voice_id": profile.get("voice_id"),
            "voice_settings": dict(profile.get("settings") or {})}


def current_voice(vf: VoiceFilm) -> dict:
    """The voice this film reads with: its pins where they exist, else the profile (nothing is pinned here)."""
    req = requested_pin(vf.profile, vf.data)
    out = {}
    for key, field in PIN_KEYS.items():
        pinned = get_pin(vf.dir, key)
        out[field] = pinned if pinned is not None else req[key]
    return out


def resolve_pin(vf: VoiceFilm, requested: dict, *, repin: bool = False, approved_by: str | None = None,
                reason: str | None = None) -> tuple[dict, str]:
    """(model, voice_id, settings to use; what happened). Pinned at first use; a pinned film keeps its pins unless
    re-pinned on purpose (after an A/B, with who approved it and why)."""
    used, pinned_now, kept = {}, [], []
    for key, field in PIN_KEYS.items():
        value = requested.get(key)
        existing = get_pin(vf.dir, key)
        if existing is None:
            used[field] = pin(vf.dir, key, value, source="slate voice tts (first use)")
            pinned_now.append(key)
        elif existing == value:
            used[field] = existing
        elif repin:
            if not (approved_by and reason):
                raise Refused("Re-pinning a film's voice changes how every re-voice sounds, so it needs the person's "
                              "yes and the reason.",
                              hint="A/B the new voice first (slate voice tags ab / slate voice cast), then pass "
                                   "--repin --approved-by <name> --reason \"<why>\".")
            used[field] = pin(vf.dir, key, value, repin=True, approved_by=approved_by, reason=reason,
                              source="slate voice tts --repin")
            pinned_now.append(f"{key} (re-pinned)")
        else:
            used[field] = existing
            kept.append(key)
    if kept:
        log.warn(f"this film's {', '.join(kept)} {'is' if len(kept) == 1 else 'are'} pinned and differ from the voice "
                 "profile now; keeping the pins so re-voices match (`--repin --approved-by <name> --reason <why>` "
                 "switches, after an A/B)")
    note = ("pinned " + ", ".join(pinned_now)) if pinned_now else ("kept the film's pins" if kept else "pinned")
    return used, note


def seed_for_take(vf: VoiceFilm, shot: str, override: int | None) -> int:
    """A line's seed: --seed when given, else the line's pinned seed, else pinned now (the profile's, or a stable
    one per line)."""
    if override is not None:
        return int(override)
    pinned = get_pin(vf.dir, f"seed.{shot}")
    if pinned is not None:
        return int(pinned)
    return int(pin(vf.dir, f"seed.{shot}", vf.seed_for(shot), source="slate voice tts (first take)"))


def fit_of(vf: VoiceFilm, shot: str, seconds: float) -> dict:
    """Does a read of `seconds` fit its shot? (starts `lead` in, ends at least `tail` before the cut)"""
    shot_s = vf.shot_seconds(shot)
    lead, tail = vf.lead(shot), float(vf.profile["fit"]["tail_s"])
    if shot_s is None:
        return {"shot_s": None, "lead_s": lead, "tail_s": tail, "read_s": round(seconds, 3), "slack_s": None,
                "fits": None}
    slack = shot_s - (lead + seconds)
    return {"shot_s": round(shot_s, 4), "lead_s": lead, "tail_s": tail, "read_s": round(seconds, 3),
            "slack_s": round(slack, 3), "fits": slack >= tail - 1e-9}


def estimate(vf: VoiceFilm, lines, sent: dict[str, str], model: str) -> dict:
    chars = sum(len(sent[ln.shot]) for ln in lines)
    provider = "elevenlabs_oneiro" if vf.mode == "autonomous" else "elevenlabs"
    try:
        est = ledger.estimate(provider, "tts", model=model, chars=chars)
    except UsageError:
        est = ledger.estimate(provider, "tts", amount=chars, basis=f"no registry price for {model}; 1 credit per char")
    return {"lines": len(lines), "characters": chars, "estimate": est.to_dict()}


def voice(film_dir: str | Path, *, only: str | list[str] | None = None, force: bool = False,
          approval_id: str | None = None, repin: bool = False, approved_by: str | None = None,
          reason: str | None = None, seed: int | None = None, dry_run: bool = False) -> dict:
    """Voice the film's lines (all, or `only` some). Existing takes are kept unless `force`."""
    vf = VoiceFilm(film_dir)
    prof = vf.profile
    profile_mod.enforce(prof, mode=vf.mode, what=f"{prof.get('brand')}'s voice")
    voice_id = profile_mod.require_voice(prof)
    lines = vf.select(only)
    if not lines:
        raise UsageError("This film's storyboard has no voice lines.")
    sent = check_direction(vf, lines)

    todo, skipped, unlock = [], [], []
    for ln in lines:
        has = vf.selected(ln.shot) is not None
        if vf.locked(ln.shot):
            if not force:
                skipped.append({"shot": ln.shot, "why": "approved and locked"})
                continue
            if not approval_id:
                raise SpendRefused(f"{ln.shot}'s take is approved and locked; re-voicing it spends again and replaces "
                                   "approved work.", hint="Re-voice only with --force and the person's spend approval "
                                                          "(slate approve spend voice/takes/" + ln.shot
                                                          + " --by NAME, then --approval <id>).")
            unlock.append(ln.shot)
        elif has and not force:
            skipped.append({"shot": ln.shot, "why": "has a take (use --force for a new one)"})
            continue
        todo.append(ln)

    requested = requested_pin(prof, vf.data)
    if dry_run:
        model = current_voice(vf)["model"]
        return {"dry_run": True, "film": vf.id, "to_voice": [ln.shot for ln in todo], "skipped": skipped,
                **estimate(vf, todo, sent, model)}

    for shot in unlock:  # the person's spend approval must cover the approved take before anything is spent
        info = vf.selected(shot)[1]
        for stage in TAKE_STAGES:
            if info.get(stage) and is_locked(vf.dir, info[stage]):
                guard_regenerate(vf.dir, info[stage], force=True, approval_id=approval_id)
    use, pin_note = resolve_pin(vf, requested, repin=repin, approved_by=approved_by, reason=reason)
    # The spend approval covered the locked takes (checked above); each ledger yes pays for one call, so it isn't
    # handed to every request. Re-voicing approved lines counts against the film's rework allowance.
    rework = bool(unlock)
    client = elevenlabs.Client(mode=vf.mode, film=vf.dir, rework=rework)
    fmt = client.choose_format(prof.get("output_format") or "pcm_48000") if todo else {}
    if use.get("voice_id") != voice_id:
        # The pinned voice is the one used; check it against ElevenLabs' own record in live mode as well.
        voice_id = str(use["voice_id"])
    if todo and not client.replay:
        info = client.voice_info(voice_id)
        problems = profile_mod.vendor_problems(info, prof, mode=vf.mode)
        if problems:
            raise Refused("Refusing this voice:\n  - " + "\n  - ".join(problems),
                          hint="Use a designed or library voice, or the owner's verified professional clone.")

    takes, fit_problems = [], []
    for ln in todo:
        take = vf.next_take_id(ln.shot)
        wav = vf.take_dir(ln.shot) / f"{take}.wav"
        prev, nxt = vf.neighbours(ln.shot)
        line_seed = seed_for_take(vf, ln.shot, seed)
        res = client.tts(voice_id=voice_id, text=sent[ln.shot], model_id=use["model"],
                         voice_settings=use.get("settings") or {}, seed=line_seed, previous_text=prev, next_text=nxt,
                         output_format=fmt.get("format"), out=wav, language_code=None,
                         note=f"{vf.id} {ln.shot} {take}" + (f" (re-voice, approval {approval_id})"
                                                              if ln.shot in unlock else ""))
        fit = fit_of(vf, ln.shot, float(res["seconds"]))
        data = {
            "generated": "do not edit by hand: slate voice tts",
            "shot": ln.shot, "take": take, "film": vf.id, "brand": prof.get("brand"), "mode": vf.mode,
            "text": ln.text, "sent": sent[ln.shot], "tags": tags_mod.tags_in(sent[ln.shot]),
            "previous_text": prev, "next_text": nxt,
            "model": res["model"], "voice_id": voice_id, "voice_name": prof.get("voice_name"),
            "voice_source": prof.get("source"), "voice_settings": use.get("settings") or {}, "seed": line_seed,
            "format": res["format"], "format_wanted": fmt.get("wanted"), "format_reason": fmt.get("reason"),
            "format_fallbacks": res.get("format_fallbacks") or [], "tier": fmt.get("tier"),
            "lossless": res["lossless"], "sample_rate": res["sample_rate"],
            "request_id": res["request_id"], "client_request_id": res["client_request_id"],
            "character_cost": res["character_cost"], "ledger_row": res["ledger_row"], "replay": res["replay"],
            "file": vf.rel(wav), "source_file": vf.rel(res["source_file"]) if res.get("source_file") else None,
            "sha256": res["sha256"], "seconds": res["seconds"], "fit": fit, "created_at": now(), "stages": {},
        }
        if res.get("words") is not None:
            data["replay_words"] = res["words"]
        problems = jsonio.validate(data, "voice_take")
        if problems:  # pragma: no cover - a bug in this module, not in the film
            raise UsageError("A take record doesn't match its schema: " + "; ".join(problems[:5]))
        vf.save_take_json(ln.shot, take, data)
        vf.update_take(ln.shot, take, raw=vf.rel(wav), json=vf.rel(vf.take_dir(ln.shot) / f"{take}.json"),
                       clean=None, paced=None, align=None, replay=res["replay"], seconds=res["seconds"])
        vf.select_take(ln.shot, take)
        if ln.shot in unlock:
            data_m = vf.manifest()
            data_m["shots"][ln.shot]["approved"] = None  # the new take needs its own approval
            vf.save_manifest(data_m)
        record(vf.dir, vf.rel(wav), producer="slate voice tts", tool_version=res["model"],
               inputs=[f"film:pins.{k}" for k in PIN_KEYS if get_pin(vf.dir, k) is not None])
        if not res["replay"]:
            pin(vf.dir, f"request_id.{ln.shot}", res["request_id"], source="slate voice tts (first take)")
        if fit["fits"] is False:
            fit_problems.append(f"{ln.shot}: a {fit['read_s']:.2f} s read in a {fit['shot_s']:.2f} s shot ends "
                                f"{fit['tail_s'] - fit['slack_s']:.2f} s too late")
        takes.append({"shot": ln.shot, "take": take, "file": vf.rel(wav), "seconds": res["seconds"],
                      "format": res["format"], "request_id": res["request_id"], "fit": fit,
                      "replay": res["replay"]})
        log.info(f"voiced {ln.shot} {take} ({res['seconds']:.2f} s, {res['format']})")
    return {"film": vf.id, "voice_id": voice_id, "model": use["model"], "pin": pin_note, "format": fmt,
            "takes": takes, "skipped": skipped, "fit_problems": fit_problems}
