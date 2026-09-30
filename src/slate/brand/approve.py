"""Approving a brand's four references: who approved what, when, and exactly which bytes (docs/PLAN.md §4).

`slate brand approve <name> <truth|look_motion|voice|sound> --file F --by NAME` checks the reference first, then
records it in brand.json: `{file, sha256, approved_by, approved_at, covers, covers_sha256, …}`. The file is kept in
the pack (`references/<reference>.<ext>`), so the approval never points at something that can quietly move; an earlier
approved file is moved to `references/history/`, never deleted. Every approval is also appended to
`onboarding/approvals.jsonl`.

What each reference must be before a person's yes is recorded:

- **truth**: the step's needed items done (pinned sources, a named claim authority, a sourced claims list, TRUTH.md),
  and approved by that authority (`--by` is the authority's name): the person who rules on claims is the one who
  approves them.
- **look_motion**: a moving picture (stills don't count), 10–15 seconds long, with sound you can hear; at least two
  moodboard directions grounded in the brand kit; the motion law, MOTION.md and the tokens in place. For a brand the
  owner doesn't own, brand use is approved by the approver named at that organisation (docs/PLAN.md decision 5).
- **voice**: a take you can hear, and the voice's model, settings and seed recorded; the voice passes the voice
  policy.
- **sound**: a 10–20 second proof you can hear, whose levels were measured against the voice from this exact file.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path

from slate import jsonio
from slate.brand import media, onboard, pack
from slate.errors import Refused, UsageError

ALIASES = {"truth": "truth", "look": "look_motion", "look_motion": "look_motion", "look-motion": "look_motion",
           "lookmotion": "look_motion", "voice": "voice", "sound": "sound"}
STEP_FOR = {v: k for k, v in onboard.REFERENCE_OF.items()}
HISTORY = "onboarding/approvals.jsonl"


def reference_key(text: str) -> str:
    key = ALIASES.get(str(text).strip().lower())
    if not key:
        raise UsageError(f"{text!r} isn't a reference; the four are {', '.join(pack.REFERENCES)}.")
    return key


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _needed_open(step: str, folder: Path, raw: dict) -> list[onboard.Item]:
    return [i for i in onboard.items_for(step, folder, raw) if i.required and not i.done and i.id != "approved"]


def _refuse_open(name: str, step: str, items: list[onboard.Item]) -> None:
    listed = "; ".join(f"{i.text} ({i.evidence})" if i.evidence else i.text for i in items)
    raise Refused(f"The {onboard.REFERENCE_OF[step]} reference can't be approved yet: {listed}.",
                  hint=items[0].next or f"slate brand onboard {name} --step {step}",
                  details={"open": [i.id for i in items]})


def _in_range(seconds: float | None, span: tuple[float, float]) -> bool:
    lo, hi = span
    return seconds is not None and lo - onboard.SLACK_S <= float(seconds) <= hi + onboard.SLACK_S


def check_truth(name: str, folder: Path, raw: dict, file: Path, by: str) -> dict:
    auth = pack.claim_authority(raw)
    if auth and _norm(by) != _norm(auth["name"]):
        raise Refused(f"Truth is approved by the brand's claim authority, {auth['name']}; {by} isn't named as that "
                      "authority.", hint=f"Record their approval with --by \"{auth['name']}\", or name the authority "
                                         f"in the truth step: slate brand onboard {name} --step truth --authority NAME")
    open_items = _needed_open("truth", folder, raw)
    if open_items:
        _refuse_open(name, "truth", open_items)
    return {"claims": len(pack.approved_claims(raw)), "authority": auth}


def _client_approver(name: str, raw: dict, by: str, what: str) -> None:
    """For a brand the owner doesn't own, brand use is approved by the approver named at that organisation (the claim
    authority the truth step records), not by the owner on their behalf."""
    own = pack.ownership(raw)
    if own.get("kind") != "client":
        return
    auth = pack.claim_authority(raw)
    org = (auth or {}).get("organisation") or own.get("organisation") or "that organisation"
    if not auth:
        raise Refused(f"{name} isn't the owner's brand, so {what} is approved by a named approver at {org}, and nobody "
                      "is named yet.", hint=f"slate brand onboard {name} --step truth --authority NAME "
                                            f"--authority-org \"{org}\"")
    if _norm(by) != _norm(auth["name"]):
        raise Refused(f"{name} isn't the owner's brand, so {what} is approved by its named approver at {org}, "
                      f"{auth['name']}; {by} isn't that approver.",
                      hint=f"Record their approval with --by \"{auth['name']}\", or name a different approver in the "
                           "truth step.")


def check_look(name: str, folder: Path, raw: dict, file: Path, by: str) -> dict:
    _client_approver(name, raw, by, "brand use (the look and motion)")
    info = media.probe(file)
    if info["kind"] != "video":
        raise Refused(f"{file.name} is a {info['kind']}, and the look-and-motion reference is a moving test: stills "
                      "don't count.", hint="Render a 10–15 second look-and-motion test with sound and approve that.")
    if not _in_range(info.get("duration_s"), onboard.LOOK_TEST_S):
        lo, hi = onboard.LOOK_TEST_S
        raise Refused(f"{file.name} runs {info.get('duration_s') or 0:.2f} s; the look-and-motion test runs "
                      f"{lo:g}–{hi:g} seconds.")
    if not info.get("has_audio"):
        raise Refused(f"{file.name} has no sound; the look-and-motion test is judged with its sound.")
    loud, lufs = media.audible(file)
    if not loud:
        raise Refused(f"{file.name}'s sound is silent ({lufs if lufs is not None else 'unreadable'} LUFS); the test is "
                      "judged with its sound.")
    boards = onboard.grounded_directions(raw)
    open_items = _needed_open("look", folder, raw)
    if open_items:
        _refuse_open(name, "look", open_items)
    return {"duration_s": round(float(info["duration_s"]), 4), "width": info.get("width"), "height": info.get("height"),
            "fps": info.get("fps"), "frames": info.get("frames"), "audio_lufs": lufs,
            "moodboards": sorted({b["direction"] for b in boards}), "engine": info.get("engine")}


def check_voice(name: str, folder: Path, raw: dict, file: Path, by: str) -> dict:
    info = media.probe(file)
    if not info.get("has_audio"):
        raise Refused(f"{file.name} has no sound in it, so it can't be the voice reference.")
    loud, lufs = media.audible(file)
    if not loud:
        raise Refused(f"{file.name} is silent ({lufs if lufs is not None else 'unreadable'} LUFS).")
    open_items = _needed_open("voice", folder, raw)
    if open_items:
        _refuse_open(name, "voice", open_items)
    v = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    return {"voice": {k: v.get(k) for k in ("provider", "source", "voice_id", "voice_name", "model", "settings",
                                            "seed")},
            "duration_s": info.get("duration_s"), "audio_lufs": lufs}


def check_sound(name: str, folder: Path, raw: dict, file: Path, by: str) -> dict:
    info = media.probe(file)
    if not info.get("has_audio"):
        raise Refused(f"{file.name} has no sound in it.")
    if not _in_range(info.get("duration_s"), onboard.SOUND_PROOF_S):
        lo, hi = onboard.SOUND_PROOF_S
        raise Refused(f"{file.name} runs {info.get('duration_s') or 0:.2f} s; the sound reference is a {lo:g}–{hi:g} "
                      "second proof.")
    loud, lufs = media.audible(file)
    if not loud:
        raise Refused(f"{file.name} is silent ({lufs if lufs is not None else 'unreadable'} LUFS).")
    s = raw.get("sound") if isinstance(raw.get("sound"), dict) else {}
    measured = s.get("reference_measured_from") if isinstance(s.get("reference_measured_from"), dict) else {}
    if not s.get("reference_levels") or measured.get("sha256") != jsonio.sha256(file):
        raise Refused(f"{file.name}'s levels haven't been measured against the voice (the sound reference carries "
                      "them, measured from this exact file).",
                      hint=f"slate brand onboard {name} --step sound --proof {file} --stems STEMS_DIR "
                           "(with vo.wav), or --voice VOICE.wav")
    open_items = _needed_open("sound", folder, raw)
    if open_items:
        _refuse_open(name, "sound", open_items)
    return {"duration_s": round(float(info["duration_s"]), 4), "levels": dict(s["reference_levels"]),
            "audio_lufs": lufs}


CHECKS = {"truth": check_truth, "look_motion": check_look, "voice": check_voice, "sound": check_sound}


def _place(folder: Path, key: str, src: Path, in_place: bool) -> tuple[str, str | None]:
    """(the path recorded, where the file came from if it was copied into the pack)."""
    src = src.resolve()
    try:
        return src.relative_to(folder.resolve()).as_posix(), None
    except ValueError:
        pass
    if in_place:
        return str(src), None
    refs = folder / "references"
    refs.mkdir(parents=True, exist_ok=True)
    dest = refs / f"{key}{src.suffix.lower()}"
    if dest.exists():
        if jsonio.sha256(dest) == jsonio.sha256(src):
            return dest.relative_to(folder).as_posix(), str(src)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        hist = refs / "history"
        hist.mkdir(exist_ok=True)
        shutil.move(str(dest), str(hist / f"{key}-{stamp}{dest.suffix}"))
    shutil.copy2(src, dest)
    return dest.relative_to(folder).as_posix(), str(src)


def approve(name: str, reference: str, file: str, by: str, *, note: str | None = None, in_place: bool = False,
            dry_run: bool = False) -> dict:
    """Check a reference, then record a person's approval of exactly this file."""
    key = reference_key(reference)
    folder = pack.find(name)
    if pack.in_repo(folder):
        raise Refused(f"{folder.name} is Slate's neutral pack; its references stay placeholders, unapproved by design.",
                      hint=f"Approve references in your own copy: slate brand init <name> --from-pack {folder.name}")
    who = str(by or "").strip()
    if not who:
        raise UsageError("Say who approved it: --by NAME (a person, in their own name).")
    src = Path(file).expanduser()
    if not src.is_file():
        raise UsageError(f"{file} doesn't exist.")
    raw = pack.read_raw(folder)
    brand = raw.get("name") or folder.name
    facts = CHECKS[key](brand, folder, raw, src, who)
    if dry_run:
        return {"brand": brand, "reference": key, "dry_run": True, "would_record": {"file": str(src), "by": who},
                "checks": facts}
    rel, copied_from = _place(folder, key, src, in_place)
    placed = folder / rel if not Path(rel).is_absolute() else Path(rel)
    record = {"file": rel, "sha256": jsonio.sha256(placed), "approved_by": who,
              "approved_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
              "covers": list(pack.COVERS[key]), "covers_sha256": pack.fingerprint({**raw, "_dir": str(folder)}, key),
              "bytes": placed.stat().st_size, "method": "slate brand approve"}
    if copied_from:
        record["copied_from"] = copied_from
    if note:
        record["note"] = note
    record.update({k: v for k, v in facts.items() if v is not None})
    refs = raw.get("references") if isinstance(raw.get("references"), dict) else {}
    refs[key] = record
    raw["references"] = refs
    pack.save(folder, raw)
    hist = folder / HISTORY
    hist.parent.mkdir(parents=True, exist_ok=True)
    with hist.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"reference": key, **{k: v for k, v in record.items() if k != "covers"}},
                            ensure_ascii=False, default=str) + "\n")
    view = pack.load(folder)
    step = STEP_FOR[key]
    items = onboard.items_for(step, folder, pack.read_raw(folder))
    onboard._finish(folder, pack.read_raw(folder), step, items, outputs=[], changes=[], unapproved=[], notes=[],
                    dry_run=False)
    recommended = [i.text for i in items if not i.done and not i.required]
    return {"brand": brand, "reference": key, "record": record, "status": pack.status(view),
            "still_open": recommended}
