"""The four modes, as rules other modules ask about: `settings(mode)` and `check(film_dir, action)`.

- **supervised** (default): the two sittings; spend past the film's budget asks first.
- **express**: a short film runs straight to a review cut; every approval is batched into the final-cut sitting.
- **draft**: no plates, a library voice, fewer motion-blur samples (the configured draft count); for animatics and
  quick variants; never delivered as final.
- **autonomous** (Oneiro's pursuits): only for brands whose four references are approved; only claims from the
  approved list (a new claim parks the film with a question); REVIEW verdicts park the film with their evidence; no
  After Effects; every Higgsfield call needs a person's approval id; default output a 15–45 second 9:16 reel; held
  for a person at the final cut.

`check` returns quietly-ish (a dict) when an action may go ahead and raises `Refused` (exit 1) or `SpendRefused`
(exit 4) when it may not, with the reason and the next step. A parked film refuses production actions until a person
answers.

A film's mode here is the one Slate judges it in (`slate.state.autonomy.film_mode`): a film made autonomous stays
autonomous whatever its film.json says, until a person at their own Terminal changes it. The one After Effects path such
a film has is its owner's own finish (`slate master --ae --go` from their Terminal, after they approved its held
master), which `check` lets through while `autonomy.ae_finish` holds it open.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from slate import config, ledger
from slate.errors import Refused, SlateError, SpendRefused, UsageError
from slate.gates import GateResult
from slate.state import deps

MODES = ("supervised", "express", "draft", "autonomous")
AUTONOMOUS_OUTPUT = {"aspect": "9:16", "runtime_s": (15.0, 45.0)}

AE_ACTIONS = {"ae", "ae_build", "ae_master", "ae_finish", "ae_render", "ae_project"}
GENERATE_ACTIONS = {"generate", "gen", "plate", "plates"}
FINAL_ACTIONS = {"deliver_final", "final", "publish"}
PRODUCTION_ACTIONS = AE_ACTIONS | GENERATE_ACTIONS | FINAL_ACTIONS | {"spend", "render", "master", "voice_design",
                                                                        "voice_clone", "tts", "music"}


def settings(mode: str) -> dict:
    """What a mode means, as data other modules read (motion-blur samples come from config, never a literal)."""
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    render = config.load().get("render", {})
    base: dict[str, Any] = {
        "mode": mode,
        "blocking_sittings": ["preproduction", "final_cut"],
        "batch_approvals_into_final_cut": False,
        "plates": True,
        "voice": "the brand's voice, a designed voice or a library voice; a clone only of the owner's own verified voice",
        "voice_design": True,
        "motion_blur_samples": int(render.get("motion_blur_samples", 16)),
        "deliver_final": True,
        "after_effects": True,
        "master": "ae",
        "claims": "on the brand's approved list or sourced",
        "review_verdicts": "a person decides at the sitting",
        "higgsfield_approval_each_call": False,
        "spend_past_budget": "asks a person first (the ledger refuses past the film's budget)",
        "brand_references_required": False,
        "default_output": None,
        "parks_on_questions": False,
        "held_for_person": False,
    }
    if mode == "express":
        base.update(blocking_sittings=["final_cut"], batch_approvals_into_final_cut=True)
    elif mode == "draft":
        base.update(blocking_sittings=[], plates=False, voice="a library voice (no design or clone spend)",
                    voice_design=False, motion_blur_samples=int(render.get("draft_samples", 4)),
                    deliver_final=False, master="ffmpeg")
    elif mode == "autonomous":
        base.update(blocking_sittings=["final_cut"], batch_approvals_into_final_cut=True, after_effects=False,
                    master="ffmpeg", claims="only claims on the brand's approved list; a new claim parks the film",
                    review_verdicts="REVIEW parks the film with its evidence; it never counts as a pass",
                    higgsfield_approval_each_call=True, brand_references_required=True,
                    default_output={"aspect": AUTONOMOUS_OUTPUT["aspect"],
                                    "runtime_s": list(AUTONOMOUS_OUTPUT["runtime_s"])},
                    parks_on_questions=True, held_for_person=True,
                    voice="the brand's approved voice; the only clone allowed is the owner's own verified clone")
    return base


# ---------------------------------------------------------------------------------------------------------------
# At creation (and when the mode changes)


def check_new(mode: str, brand: str | None, pack: dict | None) -> None:
    """The rules a film must meet to exist in `mode`. Autonomous films need all four brand references approved."""
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    if mode != "autonomous":
        return
    if pack is None:
        raise Refused(f"Autonomous films need an onboarded brand, and {brand!r} can't be read.")
    missing = deps.brand_missing_references(pack)
    if missing:
        raise Refused(f"Autonomous films only run for brands whose four references are approved; {brand} is still "
                      f"missing: {', '.join(missing)}.",
                      hint=f"Onboard the brand with a person first: slate brand onboard {brand}",
                      details={"missing_references": missing})


def default_format(mode: str) -> str:
    """The format a mode uses when none is named. Autonomous films default to a 15–45 second 9:16 reel."""
    if mode != "autonomous":
        raise UsageError("Name a format with --format.")
    from slate.state import film  # noqa: PLC0415

    lo, hi = AUTONOMOUS_OUTPUT["runtime_s"]
    matches = []
    for name in deps.format_names():
        try:
            fmt, _src = deps.format_load(name)
            d = film.format_defaults(fmt, aspect=AUTONOMOUS_OUTPUT["aspect"] if fmt.get("aspects") else None)
        except SlateError:
            continue
        if d["aspect"] != AUTONOMOUS_OUTPUT["aspect"]:
            continue
        rng = d["runtime_range_s"] or [d["runtime_s"], d["runtime_s"]]
        if rng[0] is None or rng[1] is None:
            continue
        exact = rng[0] >= lo and rng[1] <= hi
        overlap = rng[0] <= hi and rng[1] >= lo
        if exact or overlap:
            # the format marked autonomous_default wins among the ones that fit; then exact fits, then by name
            matches.append((0 if fmt.get("autonomous_default") else 1, 0 if exact else 1, name))
    if not matches:
        raise UsageError("No installed format is a 15–45 second 9:16 reel, so an autonomous film needs --format.",
                         hint=f"Known formats: {', '.join(deps.format_names()) or 'none yet'}.")
    return sorted(matches)[0][-1]


# ---------------------------------------------------------------------------------------------------------------
# During production


def _film(film_dir: Path) -> dict:
    from slate.state import film  # noqa: PLC0415

    return film.load(film_dir)


def check(film_dir: Path, action: str, **ctx: Any) -> dict:
    """May `action` happen in this film now? Returns {allowed: True, mode, action, notes} or raises Refused /
    SpendRefused with the reason. Context: what= (approve), approval_id= (generate, spend), estimate= (spend),
    claim= (claim), results= (review), path= (publish), owner_verified= (voice_clone)."""
    from slate.state import approvals, autonomy  # noqa: PLC0415

    film_dir = Path(film_dir)
    fj = _film(film_dir)
    mode = autonomy.film_mode(film_dir, fj)
    rules = settings(mode)
    notes: list[str] = []
    act = str(action or "").strip().lower()
    parked_q = approvals.open_questions(film_dir, parking=True)
    if parked_q and act in PRODUCTION_ACTIONS:
        q = parked_q[0]
        raise Refused(f"The film is parked, waiting on a person: {q['text']}",
                      hint=f"A person answers with: slate film answer {q['id']} --by NAME --text \"…\"",
                      details={"question": q})
    if act in AE_ACTIONS:
        if not rules["after_effects"]:
            if autonomy.finishing(film_dir, act):
                notes.append("the owner's own After Effects finish, started from their Terminal after they approved "
                             "the held master")
                return {"allowed": True, "mode": mode, "action": act, "notes": notes}
            held = "" if fj.get("mode") == mode else (
                f" This film's film.json says {fj.get('mode')}, but Slate's own record holds it autonomous.")
            raise Refused(f"Autonomous films never touch After Effects (its terms bar operations a person didn't "
                          f"start). They're mastered with ffmpeg and held.{held}",
                          hint="Once the owner approves the held master (the final cut), they can start its AE finish "
                               f"themselves, in their own Terminal: {autonomy.FINISH_COMMAND} (not as a queued job)")
        if mode == "draft" and act in ("ae_master", "ae_finish"):
            raise Refused("Draft films are mastered with ffmpeg; the AE finish is for films a person supervises.",
                          hint="slate master --ffmpeg")
        notes.append("After Effects steps start only on a person's go")
    elif act in GENERATE_ACTIONS:
        if not rules["plates"]:
            raise Refused("Draft films have no plates: build the shot in code, or change the film's mode.")
        if fj.get("generation") == "none":
            raise Refused("This film's generation setting is 'none', so nothing is generated.",
                          hint="The requester decides: slate film set generation rarely")
        if rules["higgsfield_approval_each_call"]:
            approval_id = ctx.get("approval_id")
            if not approval_id:
                raise SpendRefused("Every generation in an autonomous film waits for a person's yes, and this one "
                                   "has no approval id.",
                                   hint="Ask the person (Oneiro sends it by iMessage), record their yes with "
                                        "slate approve spend <path> --by NAME, then pass --approval <id>.")
            if approvals.spend_approval(film_dir, approval_id) is None:
                raise SpendRefused(f"Approval {approval_id} isn't a recorded spend approval in this film.",
                                   hint="slate approve spend <path> --by NAME records one and prints its id.")
            notes.append(f"approved by the person ({approval_id})")
    elif act == "voice_design":
        if not rules["voice_design"]:
            raise Refused("Draft films use a library voice: no voice design spend.")
    elif act == "voice_clone":
        if not ctx.get("owner_verified"):
            raise Refused("Slate clones only the owner's own voice, as a professional clone they verified themselves.",
                          hint="Anyone else's voice is used only as a real recording, never a clone.")
    elif act in FINAL_ACTIONS or (act == "approve" and ctx.get("what") == "final_cut"):
        if not rules["deliver_final"]:
            raise Refused("Draft films are never delivered or approved as final.",
                          hint="Make it a supervised or express film (slate film set mode supervised) and run every "
                               "gate at full quality first.")
        if act == "publish":
            path = ctx.get("path")
            if not path or not approvals.is_approved(film_dir, "final_cut", path):
                raise Refused("Nothing publishes itself: this exact file needs a person's final-cut approval.",
                              hint="slate approve final_cut <master> --by NAME, after they've watched it.")
    elif act == "spend":
        est = ctx.get("estimate")
        if isinstance(est, ledger.Estimate):
            ledger.check_budget(est, film=film_dir, mode=mode, approval_id=ctx.get("approval_id"),
                                rework=bool(ctx.get("rework")))
        notes.append(rules["spend_past_budget"])
    elif act == "brand":
        check_new(mode, fj.get("brand"), deps.brand_load(fj["brand"]))
    elif act == "claim":
        unapproved = check_claims(film_dir, [ctx.get("claim", "")], and_park=True)
        if unapproved:
            raise Refused(f"'{unapproved[0]}' isn't on the brand's approved list, so the film is parked with a "
                          "question.", details={"claims": unapproved})
    elif act == "review":
        parked_now = park_on_review(film_dir, ctx.get("results") or [])
        if parked_now:
            raise Refused(f"{len(parked_now)} REVIEW verdict(s) parked the film with their evidence; REVIEW never "
                          "counts as a pass.", details={"questions": parked_now})
    return {"allowed": True, "mode": mode, "action": act, "notes": notes}


def allowed(film_dir: Path, action: str, **ctx: Any) -> tuple[bool, str]:
    """(allowed, why) without raising: for callers that report rather than stop."""
    try:
        out = check(film_dir, action, **ctx)
        return True, "; ".join(out["notes"]) or "allowed"
    except SlateError as exc:
        return False, exc.message


# ---------------------------------------------------------------------------------------------------------------
# Parking


def park(film_dir: Path, question: str, *, evidence: Iterable[str] = (), source: str,
         for_: str | None = None) -> dict:
    """Park the film with a question for a person. It waits, and `slate status` shows the question first."""
    from slate.state import approvals  # noqa: PLC0415

    return approvals.ask(film_dir, question, source=source, evidence=list(evidence), parks=True, for_=for_)


def parked(film_dir: Path) -> list[dict]:
    from slate.state import approvals  # noqa: PLC0415

    return approvals.open_questions(film_dir, parking=True)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%$€£.,/-]", "", str(text or "").lower())).strip(" .,;:")


def check_claims(film_dir: Path, claims: Iterable[str], *, and_park: bool = True) -> list[str]:
    """The claims that aren't on the brand's approved list. In an autonomous film, any such claim parks the film with
    one question listing them (other modes leave claims to the script's source gate)."""
    from slate.state import autonomy  # noqa: PLC0415

    fj = _film(film_dir)
    claims = [c for c in claims if str(c or "").strip()]
    if autonomy.film_mode(film_dir, fj) != "autonomous" or not claims:
        return []
    pack = deps.brand_load(fj["brand"])
    approved = {_norm(c.get("text") or c.get("claim") or "") for c in deps.brand_approved_claims(pack)}
    unapproved = [c for c in claims if _norm(c) not in approved]
    if unapproved and and_park:
        park(film_dir, "May the film say this? It isn't on the brand's approved claims: "
             + " · ".join(f"“{c}”" for c in unapproved),
             evidence=[f"brand {fj['brand']}: approved claims list"], source="autonomous claim check", for_="claims")
    return unapproved


def park_on_review(film_dir: Path, results: Iterable[Any]) -> list[dict]:
    """Autonomous films park on every REVIEW verdict, with its evidence. Other modes leave REVIEW to the sitting."""
    from slate.state import autonomy  # noqa: PLC0415

    fj = _film(film_dir)
    if autonomy.film_mode(film_dir, fj) != "autonomous":
        return []
    out = []
    for r in results:
        gr = r if isinstance(r, GateResult) else GateResult.from_dict(r) if isinstance(r, dict) else None
        if gr is None or gr.status != "REVIEW":
            continue
        out.append(park(film_dir, f"{gr.gate} needs a person's judgement: {gr.summary}", evidence=gr.evidence,
                        source=f"gate {gr.gate}", for_="review"))
    return out
