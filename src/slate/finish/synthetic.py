"""What in a film is synthetic, recorded for the owner only, and which assets are replayed stand-ins.

**Synthetic media (internal only).** No AI label goes on a film unless a law requires one; that's the owner's
decision (docs/PLAN.md, Protections). Slate still records, in each delivery's manifest, which voices, shots and
music are synthetic, so the owner can tell when a law does apply. TikTok, YouTube and Meta have their own rules that
can require labels on realistic AI voices or people; following them is the owner's call per platform, recorded in
[finish.labels] platforms. This record is never published with the film.

**Replayed stand-ins.** In replay mode (SLATE_FAKE_VENDORS=1) a vendor call is answered by a fixture or a
deterministic fake, and the record it lands in says `"replay": true`. A film with any replayed asset in use can be
delivered only as a draft: a final delivery refuses and lists them.
"""

from __future__ import annotations

from pathlib import Path

from slate import jsonio
from slate.finish import deps, settings
from slate.finish.film import Film

NOTE = ("internal only: never published with the film. No AI label is added unless a law requires one (the owner's "
        "decision); this record is how Slate can tell when a law does apply.")
PLATFORM_NOTE = ("TikTok, YouTube and Meta have their own rules that can require labels on realistic AI voices or "
                 "people. Whether to follow them is the owner's call per platform.")
UNDECIDED = "the owner's call (not decided yet)"


def _read(film: Film, rel: str) -> dict | None:
    data = jsonio.read(film.path(rel), default=None)
    return data if isinstance(data, dict) else None


def voices(film: Film) -> list[dict]:
    """One row per voiced shot: the take in use, its voice and whether that voice is synthetic."""
    takes = _read(film, "voice/takes.json") or {}
    out = []
    for shot, entry in (takes.get("shots") or {}).items():
        sel = (entry or {}).get("selected")
        if not sel:
            continue
        rel = f"voice/takes/{shot}/{sel}.json"
        tj = _read(film, rel) or {}
        source = tj.get("voice_source") or ((film.data.get("voice") or {}).get("source")
                                            if isinstance(film.data.get("voice"), dict) else None)
        out.append({"shot": str(shot), "take": sel, "record": rel, "voice_id": tj.get("voice_id"),
                    "voice_name": tj.get("voice_name"), "source": source or "unknown", "model": tj.get("model"),
                    "synthetic": source != "recording", "replay": bool(tj.get("replay"))})
    return out


def music(film: Film) -> list[dict]:
    """The score's parts in use (sound/score/score.json), each composed by a music model: synthetic."""
    score = _read(film, "sound/score/score.json") or {}
    out = []
    for part, p in (score.get("parts") or {}).items():
        f = (p or {}).get("file")
        if not f:
            continue
        rec_rel = str(Path(f).with_suffix(".json"))
        rec = _read(film, rec_rel) or {}
        out.append({"part": str(part), "file": f, "record": rec_rel,
                    "model": rec.get("model_id") or score.get("model_id"),
                    "synthetic": True, "replay": bool(rec.get("replay"))})
    return out


def plates(film: Film) -> list[dict]:
    """Generated plates in the film (plates/<shot>/plate/plate.json): the shots they cover are synthetic."""
    out = []
    root = film.path("plates")
    if not root.is_dir():
        return out
    for rec_path in sorted(root.glob("*/plate/plate.json")):
        rec = jsonio.read(rec_path, default=None) or {}
        prov = rec.get("provenance") or {}
        out.append({"shot": rec.get("shot"), "covers": rec.get("covers") or [rec.get("shot")],
                    "record": film.rel(rec_path), "provider": prov.get("provider"), "model": prov.get("model"),
                    "role": prov.get("role"), "prompt": prov.get("prompt"), "approved_by": prov.get("approved_by"),
                    "synthetic": True, "replay": bool(prov.get("replay") or rec.get("replay"))})
    return out


def unapproved_plates(film: Film) -> list[dict]:
    """Plates in the film with no person's approved take behind them (a final delivery refuses these)."""
    return [{"what": f"plate for {', '.join(r['covers'] or [str(r['shot'])])} has no approved take",
             "record": r["record"]} for r in plates(film) if not (r.get("approved_by") or "").strip()]


def mattes(film: Film) -> list[dict]:
    """Mattes shaped by a generation over real frames: no generated pixels on screen, noted all the same."""
    out = []
    root = film.path("plates")
    if not root.is_dir():
        return out
    for rec_path in sorted(root.glob("*/matte/matte.json")):
        rec = jsonio.read(rec_path, default=None) or {}
        out.append({"shot": rec.get("shot") or rec_path.parent.parent.name, "record": film.rel(rec_path),
                    "synthetic": False, "note": "a generation's shape used as a matte over real frames",
                    "replay": bool(rec.get("replay"))})
    return out


def captures(film: Film) -> list[dict]:
    """Scripted app and web captures: real screens, not synthetic."""
    out = []
    root = film.path("captures")
    if not root.is_dir():
        return out
    for rec_path in sorted(root.glob("*/capture.json")):
        rec = jsonio.read(rec_path, default=None) or {}
        out.append({"name": rec.get("name") or rec_path.parent.name, "kind": rec.get("kind"),
                    "record": film.rel(rec_path), "synthetic": False, "replay": bool(rec.get("replay"))})
    return out


def generations(film: Film) -> list[dict]:
    """Picked generation takes (plates/<shot>/generation.json), for the replay check."""
    out = []
    root = film.path("plates")
    if not root.is_dir():
        return out
    for rec_path in sorted(root.glob("*/generation.json")):
        rec = jsonio.read(rec_path, default=None) or {}
        picked = rec.get("picked")
        for t in rec.get("takes") or []:
            if picked and t.get("take", t.get("id")) == picked:
                out.append({"shot": rec.get("shot"), "take": picked, "record": film.rel(rec_path),
                            "replay": bool(t.get("replay"))})
    return out


def labels(film: Film) -> dict:
    """The labelling position: none unless a law requires one; each platform's rule is the owner's call."""
    cfg = settings.load().get("labels") or {}
    law = str(cfg.get("law") or "").strip() or None
    decided = {str(k): str(v) for k, v in (cfg.get("platforms") or {}).items()}
    known = deps.format_platforms(film.dir, film.data.get("format")) or list(cfg.get("default_platforms") or [])
    platforms = {p: decided.get(p, UNDECIDED) for p in sorted(set(known) | set(decided))}
    return {"ai_label": f"required by {law}" if law else "none",
            "why": (f"a law applies ({law})" if law else
                    "no AI labels unless a law requires one (the owner's decision)"),
            "law": law, "platforms": platforms, "platform_note": PLATFORM_NOTE}


def record(film: Film) -> dict:
    """The internal synthetic-media record for the manifest."""
    v, m, p = voices(film), music(film), plates(film)
    synthetic_voice_shots = sorted({r["shot"] for r in v if r["synthetic"]})
    generated_shots = sorted({s for r in p for s in (r["covers"] or []) if s})
    profile = film.data.get("voice") if isinstance(film.data.get("voice"), dict) else None
    return {
        "note": NOTE,
        "summary": {
            "synthetic_voice_shots": synthetic_voice_shots,
            "recorded_voice_shots": sorted({r["shot"] for r in v if not r["synthetic"]}),
            "generated_shots": generated_shots,
            "composed_music": bool(m),
            "any_synthetic": bool(synthetic_voice_shots or generated_shots or m),
        },
        "voice_profile": {k: profile.get(k) for k in ("provider", "source", "voice_id", "voice_name", "model",
                                                      "clone_of", "clone_kind", "verified_by_owner")
                          if k in profile} if profile else None,
        "voices": v, "shots": p, "mattes": mattes(film), "music": m, "captures": captures(film),
        "labels": labels(film),
    }


def replayed(film: Film) -> list[dict]:
    """Every asset in use that a replayed (fake) vendor call made. Any of these blocks a final delivery."""
    out = []
    for r in voices(film):
        if r["replay"]:
            out.append({"what": f"voice take for {r['shot']}", "record": r["record"]})
    for r in music(film):
        if r["replay"]:
            out.append({"what": f"score part {r['part']}", "record": r["record"]})
    for r in plates(film):
        if r["replay"]:
            out.append({"what": f"plate for {', '.join(r['covers'] or [str(r['shot'])])}", "record": r["record"]})
    for r in generations(film):
        if r["replay"]:
            out.append({"what": f"generated take {r['take']} for {r['shot']}", "record": r["record"]})
    for r in captures(film):
        if r["replay"]:
            out.append({"what": f"capture {r['name']}", "record": r["record"]})
    for r in mattes(film):
        if r["replay"]:
            out.append({"what": f"matte for {r['shot']}", "record": r["record"]})
    return out
