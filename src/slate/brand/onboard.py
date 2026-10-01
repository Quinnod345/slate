"""Brand onboarding: once per brand, before any script (docs/PLAN.md §4).

Every brand gets the treatment that made the first films look right: a look built from its own design system, a
motion law measured from its own product and from reference films, a voice and a sound of its own, and a truth pack
someone at the brand stands behind. Onboarding produces four references a person approves:

1. **truth**: the sources of truth (pinned; company code read in place), a named claim authority (for a brand the
   owner doesn't own, a person at that organisation) and the approved-claims list. Output: `onboarding/truth/TRUTH.md`.
2. **look and motion**: inputs are the brand's own components (website code, app screens or SwiftUI views,
   guidelines), two or three reference films measured with fit_ease, and the product's own motion values (CSS easing
   tokens, SwiftUI springs). Outputs: the palette, type and motion law in brand.json, `motion.json`, `MOTION.md`, the
   kit review sheet. Gate: at least two moodboard directions grounded in the brand kit, then an approved moving 10–15
   second look-and-motion test with sound. Stills don't count.
3. **voice**: an approved take, with its model, settings and seed.
4. **sound**: a 10–20 second reference proof, with its levels measured against the voice.

`slate brand onboard <name> --step <step>` records what it's given, writes that step's outputs and its checklist
(`onboarding/<step>/CHECKLIST.md` and `step.json`), and says what's next. Without `--step` it prints where all four
stand. It never runs on the repo's neutral packs, and it never quietly changes what an approved reference covers:
that needs `--revise`, and then a person's approval again ("perfect, don't replace").

**Spend.** Onboarding itself makes no paid call. The paid parts (Voice Design, about 5,000 credits; look tests, about
$15-20) are made by the voice and generation stages for the brand, and they bill against the onboarding budget:
`billing(name)` is what they pass to the ledger (as `film=`), so `slate.ledger` holds them to
`ledger.format_budget('onboarding')` and counts them per brand. `spend(name)` says where that budget stands.
"""

from __future__ import annotations

import copy
import datetime as _dt
import importlib
import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from slate import jsonio, paths
from slate.brand import motiondoc, pack, sources
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError

STEPS = ("truth", "look", "voice", "sound")
REFERENCE_OF = {"truth": "truth", "look": "look_motion", "voice": "voice", "sound": "sound"}
TITLES = {"truth": "truth", "look": "look and motion", "voice": "voice", "sound": "sound"}
TRUTH_MD = "onboarding/truth/TRUTH.md"
LOOK_INPUTS = "onboarding/look/inputs.json"
LOOK_PROPOSAL = "onboarding/look/motion.proposed.json"
REVIEW_SHEET = "onboarding/look/review_sheet.png"
# The review sheet's still: frame 45 of the kit's review composition, the frame the kit's own golden still uses, where
# the logo build and the type in motion are mid-move (at frame 0 both panels are still empty).
REVIEW_FRAME = 45
VOICE_TAKE = "onboarding/voice/take.json"
SOUND_LEVELS = "onboarding/sound/levels.json"
LOOK_TEST_S = (10.0, 15.0)
SOUND_PROOF_S = (10.0, 20.0)
SLACK_S = 0.05  # container durations round; a 15.03 s test is a 15 s test
MAX_AUTOFILL_COLOURS = 40
KIT_ROLES = ("page", "surface", "ink", "ink2", "ink3", "hairline", "accent", "accentInk", "softAccent", "inverse",
             "inverseDeep", "line")


@dataclass
class Item:
    """One line of a step's checklist. `required` items must be done before the reference can be approved."""

    id: str
    text: str
    done: bool
    evidence: str = ""
    next: str | None = None
    required: bool = True


# ------------------------------------------------------------------------------------------------------- helpers
def _today() -> str:
    return _dt.date.today().isoformat()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _slug(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:48] or fallback


def _cmd(name: str, step: str, extra: str = "") -> str:
    return f"slate brand onboard {name} --step {step}" + (f" {extra}" if extra else "")


def _approve_cmd(name: str, ref: str, file: str, by: str = "NAME") -> str:
    return f"slate brand approve {name} {ref} --file {file} --by \"{by}\""


def open_pack(name: str, *, write: bool = True) -> tuple[Path, dict]:
    """(folder, brand.json as written). Onboarding writes files, so it never runs on the repo's neutral packs."""
    folder = pack.find(name)
    if write and pack.in_repo(folder):
        raise Refused(f"{folder.name} is Slate's neutral pack, kept in its repo; onboarding writes files, so it never "
                      "runs there.", hint=f"Onboard your own copy: slate brand init <name> --from-pack {folder.name}")
    return folder, pack.read_raw(folder)


def _view(folder: Path, raw: dict) -> dict:
    """brand.json as the pack functions read it (with its folder), without touching the file."""
    return {**raw, "name": raw.get("name") or folder.name, "_dir": str(folder)}


def guard(folder: Path, before: dict, after: dict, *, revise: bool) -> list[str]:
    """The approved references this change would un-approve. Refuses unless `revise` says it's on purpose."""
    was, now = _view(folder, before), _view(folder, after)
    hit = [k for k in pack.REFERENCES
           if pack.reference_status(was, k)["approved"] and pack.fingerprint(was, k) != pack.fingerprint(now, k)]
    if hit and not revise:
        who = "; ".join(f"{k} ({pack.reference_status(was, k)['reason']})" for k in hit)
        raise Refused(f"This changes what an approved reference covers: {who}. Approved work is revised on purpose, "
                      "never by accident.",
                      hint="Run it again with --revise to make the change; the reference then needs a person's "
                           "approval again (slate brand approve).",
                      details={"would_unapprove": hit})
    return hit


def _write_text(folder: Path, rel: str, text: str) -> Path:
    return jsonio.write_text_atomic(folder / rel, text)


def render_checklist(name: str, step: str, items: list[Item], ref_status: dict, notes: Iterable[str] = ()) -> str:
    lines = [f"# Onboarding {name}: {TITLES[step]}", "",
             f"*Written by `{_cmd(name, step)}`; run it again to refresh. Items marked (needed) must be done before "
             f"the {REFERENCE_OF[step]} reference can be approved.*", ""]
    for it in items:
        mark = "x" if it.done else " "
        tail = f" — {it.evidence}" if it.evidence else ""
        need = " (needed)" if it.required and not it.done else ""
        lines.append(f"- [{mark}] {it.text}{need}{tail}")
        if not it.done and it.next:
            lines.append(f"      next: `{it.next}`")
    lines += ["", f"**Reference `{REFERENCE_OF[step]}`:** {ref_status['state'].replace('_', ' ')} "
                  f"({ref_status['reason']})."]
    notes = [n for n in notes if n]
    if notes:
        lines += [""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"


def _finish(folder: Path, raw: dict, step: str, items: list[Item], *, outputs: list[str], changes: list[str],
            unapproved: list[str], notes: list[str], dry_run: bool, extra: dict | None = None) -> dict:
    name = raw.get("name") or folder.name
    view = _view(folder, raw)
    ref = REFERENCE_OF[step]
    st = pack.reference_status(view, ref)
    if not dry_run:
        _write_text(folder, f"onboarding/{step}/CHECKLIST.md", render_checklist(name, step, items, st, notes))
        jsonio.write_atomic(folder / f"onboarding/{step}/step.json",
                            {"generated": f"do not edit by hand: {_cmd(name, step)}", "brand": name, "step": step,
                             "reference": ref, "items": [asdict(i) for i in items], "notes": notes})
        write_overview(folder, raw)
    open_needed = [i for i in items if i.required and not i.done and i.id != "approved"]
    nxt = next((i for i in items if not i.done and i.next), None)
    out = {"brand": name, "step": step, "reference": ref, "items": [asdict(i) for i in items],
           "ready_to_approve": not open_needed, "approved": st["approved"],
           "reference_status": {k: v for k, v in st.items() if k != "record"},
           "outputs": outputs, "changes": changes, "unapproved": unapproved, "notes": notes,
           "next": {"command": nxt.next, "why": nxt.text} if nxt else None, "dry_run": dry_run}
    out.update(extra or {})
    return out


def items_for(step: str, folder: Path, raw: dict) -> list[Item]:
    return {"truth": truth_items, "look": look_items, "voice": voice_items, "sound": sound_items}[step](folder, raw)


# --------------------------------------------------------------------------------------------------------- spend
BUDGET_FAMILY = "onboarding"  # the ledger's budget row for a brand's one-time onboarding (plan §8)


def billing(name_or_pack: Any) -> dict:
    """What a paid call made for this brand's onboarding bills against. Pass it to the ledger as `film=` (or to a
    vendor client's `film=`): it carries no budget of its own, so the ledger holds the call to
    `ledger.format_budget('onboarding')` (Voice Design about 5,000 credits, look tests $15-20) and counts it per brand,
    once. Its `brand` also means an autonomous run is refused: onboarding needs a person, and autonomous spend needs
    a brand whose four references are approved."""
    p = pack.load(name_or_pack) if isinstance(name_or_pack, (str, Path)) else dict(name_or_pack or {})
    name = str(p.get("name") or (Path(p["_dir"]).name if p.get("_dir") else "")).strip()
    if not name:
        raise UsageError("Onboarding spend needs a brand: a pack name, or a loaded pack.")
    return {"id": f"onboarding-{name}", "title": f"{name}: brand onboarding", "brand": name, "format": BUDGET_FAMILY,
            "kind": "brand_onboarding"}


def spend(name_or_pack: Any) -> dict:
    """Where this brand's onboarding budget stands: {provider: {budget, spent, unit, source}} (read-only). Empty when
    the ledger can't be read, which never stops onboarding."""
    try:
        from slate import ledger  # noqa: PLC0415 - the spend ledger (foundation), read only

        limits = ledger.film_limits(billing(name_or_pack))
    except Exception:  # noqa: BLE001 - a missing or unreadable ledger mustn't stop onboarding's bookkeeping
        return {}
    return {p: {k: v.get(k) for k in ("budget", "spent", "unit", "source")} for p, v in limits["providers"].items()}


def spend_line(sp: dict) -> str | None:
    """One line for people: what onboarding has spent against its budget, per provider."""
    if not sp:
        return None

    def dollars(v: float) -> str:
        return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"

    parts = []
    for provider, row in sp.items():
        if row.get("budget") is None:
            continue
        unit = str(row.get("unit") or "")
        spent, budget = float(row.get("spent") or 0), float(row["budget"])
        if unit.lower() in ("usd", "$"):
            parts.append(f"{provider} {dollars(spent)} of {dollars(budget)}")
        else:
            parts.append(f"{provider} {spent:,.0f} of {budget:,.0f} {unit}".rstrip())
    return ("onboarding spend: " + "; ".join(parts)) if parts else None


def overview(name: str) -> dict:
    """Where all four steps stand (read-only)."""
    folder, raw = open_pack(name, write=False)
    return _overview(folder, raw)


def _overview(folder: Path, raw: dict) -> dict:
    view = _view(folder, raw)
    st = pack.status(view)
    steps = {}
    for step in STEPS:
        items = items_for(step, folder, raw)
        steps[step] = {"reference": REFERENCE_OF[step], "state": st["references"][REFERENCE_OF[step]]["state"],
                       "done": sum(i.done for i in items), "of": len(items),
                       "open": [i.text for i in items if not i.done and i.required],
                       "next": next((i.next for i in items if not i.done and i.next), _cmd(view["name"], step))}
    return {"brand": view["name"], "status": st["status"], "missing": st["missing"], "steps": steps,
            "next": st["next"], "spend": spend(view), "billing": billing(view)}


def write_overview(folder: Path, raw: dict) -> Path:
    ov = _overview(folder, raw)
    name = ov["brand"]
    lines = [f"# Onboarding {name}", "",
             f"*Written by `slate brand onboard {name}`; `slate brand status {name}` says the same.*", "",
             f"**Status:** {ov['status']}.", ""]
    for step, s in ov["steps"].items():
        lines.append(f"- **{TITLES[step]}** ({s['reference']}: {s['state'].replace('_', ' ')}), {s['done']} of "
                     f"{s['of']} done" + (f"; next: `{s['next']}`" if s["state"] != "approved" else ""))
    line = spend_line(ov["spend"])
    if line:
        lines += ["", f"{line[:1].upper()}{line[1:]} (the paid parts are billed as `{ov['billing']['id']}` "
                      "against the ledger's onboarding budget)."]
    return _write_text(folder, "onboarding/CHECKLIST.md", "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------------------------------------- truth
def _source_record(where: str, *, sid: str | None, pin: str | None, kind: str | None, read: str | None) -> dict:
    if sources.is_work_path(where):
        remote = sources.remote_path(where)
        rec = {"id": sid or f"work-{_slug(Path(remote).name)}", "kind": kind or "code", "where": f"WORK:{remote}",
               "read": read or "in_place", "pin": pin}
    elif where.startswith(("http://", "https://")):
        rec = {"id": sid or f"web-{_slug(where.split('//', 1)[1].split('/')[0])}", "kind": kind or "website",
               "where": where, "read": read or "fetched", "pin": pin or f"fetched {_today()}"}
    else:
        p = Path(where).expanduser()
        if not p.exists():
            raise UsageError(f"{where} isn't a path here, a URL or a work: path.",
                             hint="Name a source that's somewhere else as text with --source-kind person or other.")
        auto = sources.git_head(p if p.is_dir() else p.parent) or (
            f"sha256 {jsonio.sha256(p)[:16]}" if p.is_file() else f"read {_today()}")
        default_kind = "guidelines" if p.suffix.lower() == ".pdf" else ("code" if p.is_dir() else "document")
        rec = {"id": sid or _slug(p.stem, "source"), "kind": kind or default_kind, "where": str(p.resolve()),
               "read": read or "in_place", "pin": pin or auto}
    return rec


def _saved_page(where: str) -> Path | None:
    """A saved web page given as a source (an .html file, or a folder holding index.html), else None. Live URLs are
    read by the look step (--from-website), never fetched here."""
    if where.startswith(("http://", "https://")) or sources.is_work_path(where):
        return None
    p = Path(where).expanduser()
    if p.is_file() and p.suffix.lower() in (".html", ".htm"):
        return p
    if p.is_dir() and (p / "index.html").is_file():
        return p / "index.html"
    return None


def _merge_list(items: list, new: Iterable, key) -> list:
    out = list(items)
    seen = {key(x) for x in out}
    for x in new:
        k = key(x)
        if k in seen:
            out = [x if key(y) == k else y for y in out]
        else:
            out.append(x)
            seen.add(k)
    return out


def _claim_key(c: Any) -> str:
    return (str(c.get("id") or c.get("text")) if isinstance(c, dict) else str(c)).strip().lower()


def render_truth(raw: dict) -> str:
    """TRUTH.md: the truth reference the claim authority approves. Deterministic, and nothing but the truth data, so
    re-running the step never changes an approved file."""
    name = raw.get("name") or "<brand>"
    t = raw.get("truth") if isinstance(raw.get("truth"), dict) else {}
    auth = pack.claim_authority(raw)
    lines = [f"# TRUTH.md: what {name}'s films may say", "",
             f"*Written from brand.json's truth by `{_cmd(name, 'truth')}`. The claim authority approves this file: "
             f"`{_approve_cmd(name, 'truth', TRUTH_MD, (auth or {}).get('name') or 'NAME')}`.*", ""]
    if auth:
        bits = [auth.get("name"), auth.get("role"), auth.get("organisation")]
        lines.append("**Claim authority:** " + ", ".join(str(b) for b in bits if b) +
                     (f". Decides: {', '.join(auth['decides'])}." if auth.get("decides") else "."))
    else:
        lines.append("**Claim authority:** nobody named yet.")
    lines += ["", "## Sources of truth", ""]
    srcs = [s for s in t.get("sources") or [] if isinstance(s, dict)]
    if srcs:
        lines += ["| Id | Kind | Where | Pinned to | Read |", "|---|---|---|---|---|"]
        for s in srcs:
            lines.append(f"| {s.get('id')} | {s.get('kind') or ''} | {s.get('where') or ''} | {s.get('pin') or '—'} | "
                         f"{(s.get('read') or '').replace('_', ' ')} |")
    else:
        lines.append("None yet.")
    lines += ["", "## Approved claims", ""]
    claims = pack.approved_claims(raw)
    if claims:
        lines += ["| Id | Claim | Source |", "|---|---|---|"]
        for c in claims:
            lines.append(f"| {c['id']} | {c['text']} | {c.get('source') or '—'} |")
    else:
        lines.append("None yet.")
    never = [x for x in t.get("never_claim") or [] if x]
    lines += ["", "## Never claimed or shown", ""]
    for x in never:
        lines.append(f"- {x.get('text')}" + (f": {x['why']}" if isinstance(x, dict) and x.get("why") else "")
                     if isinstance(x, dict) else f"- {x}")
    if not never:
        lines.append("None yet.")
    words = pack.banned_words(raw) or []
    lines += ["", "## Banned words", "", " · ".join(words) if words else "None yet."]
    lines += ["", "## Sample data", ""]
    if t.get("sample_data") is not None:
        lines += ["Films show these numbers, always labelled as sample data:", "", "```json",
                  jsonio.dumps(t["sample_data"]).rstrip(), "```"]
    else:
        lines.append("None yet.")
    rulings = [r for r in t.get("rulings") or [] if isinstance(r, dict)]
    if rulings:
        lines += ["", "## Rulings", ""]
        for r in rulings:
            lines.append(f"- {r.get('question')}: {r.get('ruling')}" + (f" ({r['by']})" if r.get("by") else ""))
    return "\n".join(lines) + "\n"


def truth_items(folder: Path, raw: dict) -> list[Item]:
    name = raw.get("name") or folder.name
    t = raw.get("truth") if isinstance(raw.get("truth"), dict) else {}
    own = pack.ownership(raw)
    srcs = [s for s in t.get("sources") or [] if isinstance(s, dict)]
    unpinned = [str(s.get("id")) for s in srcs if not str(s.get("pin") or "").strip()]
    copied = [str(s.get("id")) for s in srcs if s.get("kind") in ("code", "app") and s.get("read") == "copied"]
    ev = f"{len(srcs)} source(s): " + ", ".join(str(s.get("id")) for s in srcs) if srcs else "none yet"
    if unpinned:
        ev += f"; not pinned: {', '.join(unpinned)}"
    if copied:
        ev += f"; code copied instead of read in place: {', '.join(copied)}"
    items = [Item("sources", "Sources of truth, each pinned (a commit, a date or a hash), with the product's own code "
                             "first and read in place",
                  bool(srcs) and not unpinned and not copied, ev,
                  _cmd(name, "truth", "--source PATH|URL|work:PATH --source-id ID --pin COMMIT"))]
    auth = pack.claim_authority(raw)
    client = own.get("kind") == "client"
    ok = bool(auth) and (not client or bool(str((auth or {}).get("organisation") or "").strip()))
    if auth:
        ev = f"{auth.get('name')}" + (f", {auth['organisation']}" if auth.get("organisation") else "")
        if client and not auth.get("organisation"):
            ev += (f"; this isn't the owner's brand, so the authority is a named person at "
                   f"{own.get('organisation') or 'the brand'}: add --authority-org")
    else:
        ev = "nobody named yet"
    items.append(Item("authority", "A named claim authority: who rules on claims for this brand (for a brand the "
                                   "owner doesn't own, a person at that organisation)", ok, ev,
                      _cmd(name, "truth", '--authority "NAME" --authority-org "ORGANISATION" --authority-role ROLE')))
    claims = pack.approved_claims(raw)
    ids = {str(s.get("id")) for s in srcs}
    unsourced = [c["id"] for c in claims if not str(c.get("source") or "").split(":")[0] in ids]
    ev = f"{len(claims)} claim(s)" + (f"; without a source from the list: {', '.join(unsourced)}" if unsourced else "")
    items.append(Item("claims", "An approved-claims list, every claim citing one of the sources", bool(claims) and
                      not unsourced, ev, _cmd(name, "truth", '--claim "TEXT" --claim-source ID')))
    words = pack.banned_words(raw) or []
    items.append(Item("banned", "One banned-word list (the script, captions, the picture's text and transcripts all "
                                "read it)", bool(words), f"{len(words)} word(s)" if words else "none yet",
                      _cmd(name, "truth", "--banned WORD"), required=False))
    never = pack.never_claim(raw)
    items.append(Item("never", "What films never claim or show: the authority's rulings and anything not built",
                      bool(never), f"{len(never)} entr(ies)" if never else "none yet",
                      _cmd(name, "truth", '--never "TEXT"'), required=False))
    items.append(Item("sample", "The one set of sample data films show, labelled as sample data",
                      t.get("sample_data") is not None,
                      "recorded" if t.get("sample_data") is not None else "none yet",
                      _cmd(name, "truth", "--sample-data FILE.json"), required=False))
    md = folder / TRUTH_MD
    current = md.is_file() and md.read_text(encoding="utf-8") == render_truth(raw)
    items.append(Item("reference", "TRUTH.md, the truth reference the claim authority approves", current,
                      TRUTH_MD if current else ("stale: brand.json's truth changed" if md.is_file() else "not written"),
                      _cmd(name, "truth")))
    st = pack.reference_status(_view(folder, raw), "truth")
    items.append(Item("approved", "Approved by the claim authority", st["approved"], st["reason"],
                      _approve_cmd(name, "truth", TRUTH_MD, (auth or {}).get("name") or "NAME")))
    return items


def truth(name: str, *, source: str | None = None, source_id: str | None = None, pin: str | None = None,
          kind: str | None = None, read: str | None = None, authority: str | None = None,
          authority_role: str | None = None, authority_org: str | None = None, authority_contact: str | None = None,
          claims: Iterable[str] = (), claim_source: str | None = None, banned: Iterable[str] = (),
          never: Iterable[str] = (), sample_data: str | None = None, import_file: str | None = None,
          revise: bool = False, dry_run: bool = False) -> dict:
    """The truth step: record sources, the claim authority and claims, then write TRUTH.md and the checklist."""
    folder, raw = open_pack(name, write=not dry_run)
    before = copy.deepcopy(raw)
    t = raw.get("truth") if isinstance(raw.get("truth"), dict) else {}
    raw["truth"] = t
    for key in ("sources", "approved_claims", "banned_words", "never_claim", "rulings"):
        if not isinstance(t.get(key), list):
            t[key] = []
    changes: list[str] = []
    if import_file:
        data = jsonio.read(Path(import_file).expanduser())
        data = data.get("truth", data) if isinstance(data, dict) else {}
        if not isinstance(data, dict):
            raise UsageError(f"{import_file} isn't a truth object (sources, claim_authority, approved_claims, …).")
        t["sources"] = _merge_list(t["sources"], [s for s in data.get("sources") or [] if isinstance(s, dict)],
                                   lambda s: str(s.get("id")))
        t["approved_claims"] = _merge_list(t["approved_claims"], data.get("approved_claims") or [], _claim_key)
        t["banned_words"] = list(dict.fromkeys([*t["banned_words"], *(data.get("banned_words") or [])]))
        t["never_claim"] = _merge_list(t["never_claim"], data.get("never_claim") or [], _claim_key)
        t["rulings"] = [*t["rulings"], *[r for r in data.get("rulings") or [] if r not in t["rulings"]]]
        for key in ("claim_authority", "sample_data", "notes"):
            if data.get(key) is not None:
                t[key] = data[key]
        changes.append(f"merged {import_file}")
    if source:
        rec = _source_record(source, sid=source_id, pin=pin, kind=kind, read=read)
        page = _saved_page(source)
        if page is not None:  # a saved copy of the brand's website: keep its own words with it
            try:
                rec["copy"] = sources.read_website(str(page))["copy"]
            except SlateError as exc:
                changes.append(f"its copy couldn't be read: {exc.message}")
        t["sources"] = _merge_list(t["sources"], [rec], lambda s: str(s.get("id")))
        changes.append(f"source {rec['id']}: {rec['where']} (pinned to {rec.get('pin') or 'nothing yet'})"
                       + (f", {len(rec['copy'])} lines of its copy kept" if rec.get("copy") else ""))
    elif source_id or pin:
        raise UsageError("--source-id and --pin describe a source: give it with --source.")
    if authority:
        a = {"name": authority.strip()}
        for key, val in (("role", authority_role), ("organisation", authority_org), ("contact", authority_contact)):
            if val:
                a[key] = val.strip()
        t["claim_authority"] = a
        changes.append(f"claim authority: {a['name']}")
    elif authority_role or authority_org or authority_contact:
        auth = t.get("claim_authority") if isinstance(t.get("claim_authority"), dict) else None
        if not auth:
            raise UsageError("Name the claim authority first (--authority NAME).")
        for key, val in (("role", authority_role), ("organisation", authority_org), ("contact", authority_contact)):
            if val:
                auth[key] = val.strip()
        changes.append("claim authority details")
    existing = {str(c.get("id")) for c in t["approved_claims"] if isinstance(c, dict)}
    n = len(t["approved_claims"])
    for text in claims:
        if not str(text).strip():
            continue
        n += 1
        cid = f"claim-{n}"
        while cid in existing:
            n += 1
            cid = f"claim-{n}"
        existing.add(cid)
        entry = {"id": cid, "text": str(text).strip()}
        if claim_source:
            entry["source"] = claim_source
        t["approved_claims"].append(entry)
        changes.append(f"claim {cid}")
    for w in banned:
        if str(w).strip() and str(w).strip() not in t["banned_words"]:
            t["banned_words"].append(str(w).strip())
            changes.append(f"banned word {w!r}")
    for x in never:
        if str(x).strip():
            t["never_claim"].append({"text": str(x).strip()})
            changes.append("never-claim entry")
    if sample_data:
        t["sample_data"] = jsonio.read(Path(sample_data).expanduser())
        changes.append(f"sample data from {sample_data}")
    unapproved = guard(folder, before, raw, revise=revise)
    outputs = []
    if not dry_run:
        if raw != before:
            pack.save(folder, raw)
        _write_text(folder, TRUTH_MD, render_truth(raw))
        outputs.append(TRUTH_MD)
    notes = []
    if pack.ownership(raw).get("company_material"):
        notes.append("Company material: its source code is read in place and never copied; pin each code source to "
                     "the commit you read.")
    return _finish(folder, raw, "truth", truth_items(folder, raw), outputs=outputs, changes=changes,
                   unapproved=unapproved, notes=notes, dry_run=dry_run)


# ----------------------------------------------------------------------------------------------------------- look
def load_inputs(folder: Path) -> dict:
    data = jsonio.read(folder / LOOK_INPUTS, default=None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("sources", {})
    data.setdefault("measured", {})
    data.setdefault("pending", [])
    return data


def _record(inputs: dict, result: dict) -> str:
    src = dict(result["source"])
    inputs["sources"][src["id"]] = {**src, "found": result["found"], "problems": result.get("problems") or []}
    if result.get("copy") is not None:  # a website's visible copy, beside what was found in its styles
        inputs["sources"][src["id"]]["copy"] = list(result["copy"])
    return src["id"]


def _moves_of(data: Any) -> tuple[list[tuple[str, dict]], str | None]:
    """[(name, move)] from any of the shapes measured moves come in, and the film they came from (when it says)."""
    film = None
    if isinstance(data, dict) and isinstance(data.get("eases"), dict):  # slate analyze's analysis.json
        film = (data.get("video") or {}).get("name") if isinstance(data.get("video"), dict) else None
        data = data["eases"]
    moves = data.get("moves", data) if isinstance(data, dict) else data
    if isinstance(moves, list):
        return [(str(m.get("name") or f"move-{i + 1:02d}"), m) for i, m in enumerate(moves) if isinstance(m, dict)], film
    if isinstance(moves, dict):
        return [(k, m) for k, m in moves.items() if isinstance(m, dict) and k not in ("generated", "note", "film",
                                                                                      "skipped", "families")], film
    return [], film


def measured_moves(path: str | Path) -> tuple[dict, list[str]]:
    """Moves measured in reference films → fitted eases. Accepts slate analyze's analysis.json, {"moves": [...]} or
    {"moves": {name: {t|times, v|values, source, note}}} (samples are fitted here with fit_ease), or
    {name: {bezier, rms, duration_s, …}} (already fitted)."""
    from slate.motion import fit  # noqa: PLC0415

    data = jsonio.read(Path(path).expanduser())
    pairs, film = _moves_of(data)
    out, problems = {}, []
    label = Path(path).name
    for name, m in pairs:
        lin = m.get("lineage") if isinstance(m.get("lineage"), dict) else {}
        where = str(m.get("source") or m.get("film") or lin.get("video") or film or label)
        if lin.get("window_s"):
            where += " " + "–".join(f"{float(x):.2f}" for x in lin["window_s"]) + " s"
        if film:
            name = f"{Path(film).stem}-{name}"
        if m.get("error"):
            problems.append(f"{name}: measured with an error ({m['error']})")
            continue
        t, v = m.get("t", m.get("times")), m.get("v", m.get("values"))
        try:
            if t is not None and v is not None:
                r = fit.fit_bezier(t, v)
            elif m.get("bezier"):
                r = {"bezier": [float(x) for x in m["bezier"]], "rms": m.get("rms"), "duration_s": m.get("duration_s")}
            else:
                problems.append(f"{name}: no samples (t, v) and no fitted bezier")
                continue
        except (ValueError, TypeError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        rms = r.get("rms")
        out[name] = {"bezier": [round(float(x), 4) for x in r["bezier"]],
                     "lineage": f"measured from {where}" + (f" ({m['note']})" if m.get("note") else "") +
                                (f", fitted with fit_ease (RMS {float(rms):.2%})" if rms is not None else ""),
                     "duration_s": r.get("duration_s"), "fit_rms": rms,
                     "source": {"kind": "measured", "where": where, "film": lin.get("video") or film or where}}
    return out, problems


_ROLE_RULES: list[tuple[str, re.Pattern]] = [
    ("state", re.compile(r"snap|state|toggle|switch|commit", re.I)),
    ("relayout", re.compile(r"lift|relayout|layout|room|shift|reflow", re.I)),
    ("move", re.compile(r"in[-_]?out|glide|move|standard|camera|carry|travel", re.I)),
    ("exit", re.compile(r"exit|leave|depart|dismiss|(^|[-_])in($|[-_])|accel", re.I)),
    ("entrance", re.compile(r"out|enter|arrive|entrance|reveal|appear|decel|emphasi", re.I)),
]


def _role_by_shape(e: dict) -> str | None:
    from slate.motion.eases import evaluate, parse  # noqa: PLC0415

    spec = parse(e)
    if spec is None or spec.kind == "linear":
        return None
    a, mid, b = evaluate(spec, 0.25), evaluate(spec, 0.5), evaluate(spec, 0.75)
    if a > 0.55:
        return "entrance"
    if b < 0.45:
        return "exit"
    if abs(mid - 0.5) < 0.12 and a < 0.3 and b > 0.7:
        return "move"
    return None


def role_of(name: str, ease: dict) -> str | None:
    """Which job an ease does, from its name first (the product's own words), then from its shape."""
    for role, rx in _ROLE_RULES:
        if rx.search(name):
            return role
    return _role_by_shape(ease)


def ease_name(text: str) -> str:
    """A readable ease name: `sheetArrive` → `sheet-arrive`, `--ease-out-soft` → `ease-out-soft`."""
    return _slug(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(text or "")), "ease")


VIA_RANK = {"css token": 0, "theme value": 1, "SwiftUI named animation": 2, "SwiftUI animation": 3}


def _rank(e: dict) -> int:
    """Design tokens first (the system's own vocabulary), then theme values, then named animations, then inline
    ones."""
    return VIA_RANK.get(str(e.get("via")), 4)


def _usage(inputs: dict) -> dict[str, int]:
    """How often each curve appears in the product's own rules (transitions and animations)."""
    counts: dict[str, int] = {}
    for src in inputs.get("sources", {}).values():
        for u in (src.get("found") or {}).get("usage") or []:
            key = jsonio.sha256_obj([round(float(v), 4) for v in u.get("bezier") or []])
            counts[key] = counts.get(key, 0) + 1
    return counts


def candidate_eases(inputs: dict) -> list[dict]:
    """Every ease the look step has found, product first (tokens, theme values, named animations, inline ones; the
    curves the product's rules actually use first within each), then measured moves, deduplicated by curve."""
    used = _usage(inputs)
    found = []
    for sid, src in sorted(inputs.get("sources", {}).items()):
        for i, e in enumerate((src.get("found") or {}).get("eases") or []):
            key = jsonio.sha256_obj([round(float(v), 4) for v in e.get("bezier") or []])
            found.append((_rank(e), -used.get(key, 0), sid, i, e))
    out: list[dict] = []
    seen: set[str] = set()
    for *_, sid, _, e in sorted(found, key=lambda x: x[:4]):
        key = jsonio.sha256_obj(e.get("bezier") or [e.get("kind"), e.get("stiffness")])
        if key in seen:
            continue
        seen.add(key)
        out.append({**e, "name": ease_name(e.get("name")), "origin": "product", "source_id": sid})
    for name, e in sorted((inputs.get("measured") or {}).items()):
        out.append({**e, "name": ease_name(name), "origin": "measured"})
    return out


def propose_motion(inputs: dict, raw: dict) -> dict:
    """A motion law from what was found: one ease per job (the product's own first, then measured moves, the kit's
    neutral law where neither says), the product's duration and stagger tokens, and an overshoot policy consistent
    with the eases it picks. A proposal only: a person adopts it and the look test approves it."""
    from slate.motion.eases import NEUTRAL_MOTION  # noqa: PLC0415

    cands = candidate_eases(inputs)
    chosen: dict[str, dict] = {}
    for c in cands:
        r = role_of(c["name"], c)
        if r and r not in chosen:
            chosen[r] = c
    eases: dict[str, Any] = {}
    roles: dict[str, str] = {}
    neutral_names = {"entrance": "enter", "move": "move", "state": "state", "exit": "leave"}
    for role in ("entrance", "move", "state", "exit", "relayout"):
        c = chosen.get(role)
        if c:
            name = c["name"]
            eases[name] = {k: v for k, v in c.items() if k not in ("name", "origin", "source_id", "via")}
            eases[name]["job"] = role
            roles[role] = name
        elif role in neutral_names:
            n = neutral_names[role]
            eases[n] = {**copy.deepcopy(NEUTRAL_MOTION["eases"][n]), "job": role,
                        "lineage": NEUTRAL_MOTION["eases"][n]["lineage"] + " (the kit's neutral law: nothing in the "
                                                                          "product or the references said otherwise)"}
            roles[role] = n
    eases["linear"] = {"kind": "linear", "job": "only things that show time", "lineage": "time is linear"}
    durs: dict[str, Any] = {}
    stagger_unit = None
    for sid, src in sorted(inputs.get("sources", {}).items()):
        for d in (src.get("found") or {}).get("durations") or []:
            s = d.get("seconds")
            if not isinstance(s, (int, float)) or s <= 0:
                continue
            if "stagger" in str(d.get("name")).lower():
                stagger_unit = stagger_unit or d
                continue
            if 0.05 <= s <= 5.0:
                durs.setdefault(_slug(d.get("name")), {"seconds": s, "lineage": f"the product's {d.get('name')} "
                                                                                 f"token, {d.get('raw') or s} "
                                                                                 f"({d.get('where')})"})
    notes = []
    if len(durs) < 3:
        durs = {k: {"seconds": v, "lineage": "the kit's neutral scale (the product has too few duration tokens)"}
                for k, v in NEUTRAL_MOTION["durations"].items()}
    else:
        notes.append("Durations are the product's own; film travel is often 5–20× UI travel, so consider longer "
                     "tokens for film distances before adopting.")
    if stagger_unit:
        base = float(stagger_unit["seconds"])
        staggers = {u: round(base * f, 4) for u, f in (("char", 2), ("word", 3), ("line", 4), ("block", 6))}
        stag_note = (f"the product's {stagger_unit.get('name')} token, {stagger_unit.get('raw')} "
                     f"({stagger_unit.get('where')}), scaled ×2 (per character) to ×6 (per block) for film")
    else:
        staggers = dict(NEUTRAL_MOTION["staggers"])
        stag_note = "the kit's neutral staggers (the product has no stagger token)"
    over = [(n, float(e.get("peak") or 1.0)) for n, e in eases.items() if isinstance(e, dict)
            and float(e.get("peak") or 1.0) > 1.0005]
    overshoot = ({"allowed": True, "max": round(max(p for _, p in over), 4), "where": [n for n, _ in over],
                  "notes": "only where the product's own curves overshoot; decide whether films may"} if over else
                 {"allowed": False, "max": 1.0, "where": [], "notes": "nothing the proposal picked overshoots"})
    return {"generated": f"a proposal from {_cmd(raw.get('name') or '<brand>', 'look')}; adopt it with --adopt",
            "eases": eases, "durations": durs, "staggers": staggers, "stagger_notes": stag_note,
            "stagger_cap": dict(NEUTRAL_MOTION["stagger_cap"]), "exit_factor": 0.75, "roles": roles,
            "overshoot": overshoot, "signature": None, "linear_props": ["progress", "playhead", "spin"],
            "notes": " ".join(notes) or None,
            "candidates": [{k: v for k, v in c.items() if k in ("name", "bezier", "kind", "stiffness", "lineage",
                                                                  "peak", "duration_s", "origin")} for c in cands]}


def _adoptable(proposal: dict) -> dict:
    return {k: v for k, v in proposal.items() if k not in ("generated", "candidates", "stagger_notes")}


def _palette_tokens(raw: dict) -> dict:
    return pack.palette_tokens(raw)


#: The names the kit itself recognises for each role (engine/remotion/kit/core/tokens.ts ROLE_NAMES), so a token
#: named for its job becomes that role explicitly (After Effects and the contrast pairs read roles, not guesses).
ROLE_NAMES: dict[str, tuple[str, ...]] = {
    "page": ("page", "background", "bg", "canvas", "paper", "ground", "base"),
    "surface": ("surface", "card", "panel", "raised", "sheet"),
    "ink": ("ink", "text", "foreground", "fg", "body", "textprimary", "inkprimary"),
    "ink2": ("ink2", "textsecondary", "inksecondary", "secondary"),
    "ink3": ("ink3", "texttertiary", "inktertiary", "muted", "subtle"),
    "hairline": ("hairline", "border", "divider", "rule", "outline"),
    "accent": ("accent", "primary", "brand", "highlight", "key"),
    "accentInk": ("accentink", "inkbrand", "brandink", "primaryink", "accentdark", "primarydark"),
    "softAccent": ("softaccent", "accentsoft", "primarysoft", "brandsoft", "accentbg", "tint"),
    "inverse": ("inverse", "dark", "deep", "night", "bginverse"),
    "inverseDeep": ("inversedeep", "deeper", "deepest", "darker"),
    "line": ("line", "accentline", "stroke"),
}
#: Starter text pairs from the roles a palette names: the ones the kit draws text with.
STARTER_PAIRS = (("ink", "page", "body"), ("ink", "surface", "body"), ("ink2", "page", "body"),
                 ("ink3", "page", "body"), ("accentInk", "page", "body"), ("accent", "page", "large"),
                 ("page", "inverse", "body"))
FONT_VIA_RANK = {"Google Fonts link": 0, "@font-face": 1, "font token": 2, "font-family": 3, "theme": 4,
                 "guidelines": 5, "SwiftUI custom font": 6}


def _squash(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _postscript_base(name: str) -> str | None:
    """`KettleGrotesk-Bold` → `kettlegrotesk` (a PostScript-style face name's family part), else None."""
    m = re.fullmatch(r"([A-Za-z0-9]+)-([A-Za-z]+)", str(name or "").strip())
    return m.group(1).lower() if m else None


def fill_tokens(raw: dict, inputs: dict) -> list[str]:
    """Fill an empty palette, empty type and empty contrast pairs from what was found. Never overwrites what a person
    set: a filled value is a starting point for onboarding, and the look test approves it."""
    changes: list[str] = []
    colours, aliases, fonts = [], [], []
    for sid, src in sorted(inputs.get("sources", {}).items()):
        f = src.get("found") or {}
        colours += f.get("colours") or []
        aliases += f.get("aliases") or []
        fonts += f.get("fonts") or []
    pal = raw.get("palette") if isinstance(raw.get("palette"), dict) else {}
    if not _palette_tokens(raw) and colours:
        named: dict[str, str] = {}
        for c in colours:
            named.setdefault(_slug(c.get("name") or c.get("value"), "colour"), c["value"])
        if len(named) <= MAX_AUTOFILL_COLOURS:
            pal = {**named, "roles": dict(pal.get("roles") or {}), "tones": dict(pal.get("tones") or {}),
                   "notes": pal.get("notes")}
            by_squash = {_squash(r): r for r in KIT_ROLES}
            for a in aliases:  # --accent: var(--brand-500) names the role outright
                role = by_squash.get(_squash(a.get("name")))
                target = _slug(a.get("target"))
                if role and target in named:
                    pal["roles"].setdefault(role, target)
            for role, words in ROLE_NAMES.items():  # a token named for its job
                if role not in pal["roles"]:
                    hit = next((t for t in named if _squash(t) in words), None)
                    if hit:
                        pal["roles"][role] = hit
            raw["palette"] = pal
            changes.append(f"palette: {len(named)} tokens from the brand's own code"
                           + (f", roles {', '.join(pal['roles'])}" if pal["roles"] else ""))
        else:
            changes.append(f"palette left empty: the sources hold {len(named)} colours, too many to guess from; "
                           f"choose the brand's tokens from {LOOK_INPUTS}")
    typ = raw.get("type") if isinstance(raw.get("type"), dict) else {}
    if not typ.get("families") and fonts:
        picked: dict[str, dict] = {}
        ordered = sorted(fonts, key=lambda f: FONT_VIA_RANK.get(str(f.get("via")), 9))
        for f in ordered:
            fam = str(f.get("family") or "").strip()
            if not fam or fam.lower() in sources.GENERIC_FONTS:
                continue
            base = _postscript_base(fam)
            owner = next((k for k in picked if base and _squash(k) == base), None)
            if owner:  # a face's PostScript name (as SwiftUI and After Effects write it): that family's, not a new one
                picked[owner].setdefault("postscript", fam)
                continue
            if fam in picked or len(picked) >= 4:
                continue
            role = "mono" if "mono" in fam.lower() else ("sans" if not any(v["role"] == "sans" for v in picked.values())
                                                        else _slug(fam))
            picked[fam] = {"role": role, "family": fam, "weights": [400, 700], "source": f.get("source") or "installed",
                           "licence": "unknown: confirm how the brand licenses this face before a film ships",
                           "notes": f"found at {f.get('where')} ({f.get('via')})"}
        if picked:
            typ["families"] = list(picked.values())
            raw["type"] = typ
            changes.append(f"type: {', '.join(picked)} (licences to confirm)")
    roles = (raw.get("palette") or {}).get("roles") if isinstance(raw.get("palette"), dict) else None
    if not raw.get("contrast_pairs") and isinstance(roles, dict) and roles:
        pairs = [{"fg": fg, "bg": bg, "role": role, "where": "proposed from the palette's roles; confirm where the "
                                                              "brand really sets text"}
                 for fg, bg, role in STARTER_PAIRS if fg in roles and bg in roles]
        if pairs:
            raw["contrast_pairs"] = pairs
            changes.append(f"contrast pairs: {len(pairs)} proposed from the palette's roles (measured by "
                           "slate brand validate)")
    return changes


def _moodboard(folder: Path, raw: dict, files: list[str], direction: str, grounded_in: str | None) -> str:
    look = raw.get("look") if isinstance(raw.get("look"), dict) else {}
    raw["look"] = look
    boards = [b for b in look.get("moodboards") or [] if isinstance(b, dict)]
    kept = []
    dest = folder / "onboarding" / "look" / "moodboards" / _slug(direction)
    for f in files:
        p = Path(f).expanduser()
        if not p.is_file():
            raise UsageError(f"Moodboard {f} doesn't exist.")
        try:
            rel = p.resolve().relative_to(folder.resolve()).as_posix()
        except ValueError:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest / p.name)
            rel = (dest / p.name).relative_to(folder).as_posix()
        kept.append(rel)
    entry = next((b for b in boards if b.get("direction") == direction), None)
    if entry is None:
        entry = {"direction": direction, "files": [], "grounded_in": None}
        boards.append(entry)
    entry["files"] = list(dict.fromkeys([*(entry.get("files") or []), *kept]))
    if grounded_in:
        entry["grounded_in"] = grounded_in
    entry["recorded_at"] = entry.get("recorded_at") or _today()
    look["moodboards"] = boards
    return f"moodboard {direction!r}: {len(kept)} file(s)" + (f", grounded in {grounded_in}" if grounded_in else "")


def grounded_directions(raw: dict) -> list[dict]:
    look = raw.get("look") if isinstance(raw.get("look"), dict) else {}
    return [b for b in look.get("moodboards") or [] if isinstance(b, dict) and b.get("files")
            and str(b.get("grounded_in") or "").strip()]


def _analyze_film(video: Path, brand: str, inputs: dict, notes: list[str]) -> dict | None:
    """Run slate analyze's cuts and eases on a reference film and take its measured moves (fit_ease), with lineage."""
    try:
        analyze = importlib.import_module("slate.analyze")
    except ImportError:
        notes.append(f"{video.name}: slate analyze isn't installed, so its moves weren't measured (give them with "
                     "--measured)")
        return None
    result = analyze.run(video, steps=["cuts", "eases"], brand=brand)
    out = Path(str(result.get("out") or ""))
    analysis_file = out / "analysis.json"
    fits, problems = measured_moves(analysis_file) if analysis_file.is_file() else ({}, ["no analysis.json written"])
    inputs["measured"].update(fits)
    notes += [f"{video.name}: {p}" for p in problems]
    return {"file": str(analysis_file), "moves": sorted(fits)}


def _analyze_available() -> bool:
    try:
        importlib.import_module("slate.analyze")
        return True
    except ImportError:
        return False


def look_items(folder: Path, raw: dict) -> list[Item]:
    name = raw.get("name") or folder.name
    inputs = load_inputs(folder)
    srcs = inputs.get("sources") or {}
    comp = [s for s in srcs.values() if s.get("kind") in ("app", "code", "website", "guidelines")]
    screens = [r for r in ((raw.get("look") or {}).get("references") or []) if isinstance(r, dict)
               and r.get("what") == "app screen"]
    pending = inputs.get("pending") or []
    ev = ", ".join(f"{s['id']} ({s.get('kind')}, {s.get('files_read', 0)} files)" for s in comp) or \
        ("none yet" if not screens else "")
    if screens:
        ev += (", " if comp else "") + f"{len(screens)} app screen(s)"
    if pending:
        ev += f"; waiting on {len(pending)} read-only WORK procedure(s)"
    items = [Item("components", "The brand's own components, read in place: website code, app screens or SwiftUI "
                                "views, guidelines", bool(comp or screens), ev,
                  _cmd(name, "look", "--from-app PATH | --from-website URL | --from-pdf FILE"), required=False)]
    product = [e for s in srcs.values() for e in (s.get("found") or {}).get("eases") or []]
    kinds = sorted({(e.get("source") or {}).get("kind", "?") for e in product})
    items.append(Item("product_motion", "The product's own motion values: CSS easing tokens or SwiftUI springs, "
                                        "converted into named eases", bool(product),
                      f"{len(product)} ease(s) from {', '.join(kinds)}" if product else "none yet",
                      _cmd(name, "look", "--from-app PATH"), required=False))
    measured = inputs.get("measured") or {}
    films = {str((m.get("source") or {}).get("film") or (m.get("source") or {}).get("where") or "").split(" ")[0]
             for m in measured.values()}
    films.discard("")
    how = ("slate analyze <film>, then " if _analyze_available() else "") + _cmd(name, "look", "--measured FILE.json")
    items.append(Item("reference_films", "Two or three reference films, their moves measured and fitted (fit_ease)",
                      len(films) >= 2 and len(measured) >= 2,
                      f"{len(measured)} move(s) from {len(films)} film(s)" if measured else "none yet", how,
                      required=False))
    law = raw.get("motion") if isinstance(raw.get("motion"), dict) else {}
    eases = law.get("eases") if isinstance(law.get("eases"), dict) else {}
    no_lineage = [n for n, e in eases.items() if not (isinstance(e, dict) and e.get("lineage"))
                  and n != "linear"]
    missing = [k for k, ok in (("named eases", bool(eases)), ("a duration scale", bool(law.get("durations"))),
                               ("staggers", bool(law.get("staggers"))),
                               ("an overshoot policy", law.get("overshoot") is not None),
                               ("the signature motion", bool(law.get("signature")))) if not ok]
    ev = (f"{len(eases)} eases" if eases else "no eases") + (f"; missing {', '.join(missing)}" if missing else "")
    if no_lineage:
        ev += f"; no lineage for {', '.join(no_lineage)}"
    items.append(Item("law", "The motion law in brand.json and motion.json: named eases with their lineage, a "
                             "duration scale, staggers, an overshoot policy and the signature motion",
                      not missing and not no_lineage, ev,
                      _cmd(name, "look", "--adopt") if not eases else "edit brand.json's motion, then " +
                      _cmd(name, "look")))
    docs = motiondoc.check(folder, raw)
    items.append(Item("motion_md", "MOTION.md and motion.json written from the law", all(v == "current" for v in
                                                                                     docs.values()),
                      ", ".join(f"{k} {v}" for k, v in docs.items()), _cmd(name, "look")))
    view = _view(folder, raw)
    toks, roles = pack.palette_tokens(view), pack.palette_roles(view)
    fams = [f for f in ((raw.get("type") or {}).get("families") or []) if isinstance(f, dict)] \
        if isinstance((raw.get("type") or {}).get("families"), list) else \
        list(((raw.get("type") or {}).get("families") or {}).values())
    unlicensed = [f.get("family") for f in fams if not str(f.get("licence") or "").strip()
                  or str(f.get("licence")).lower().startswith("unknown")]
    pairs = pack.contrast_pairs(view)
    short = [f"{p['fg']} on {p['bg']}" for p in pairs if not p["ok"]]
    missing_roles = [r for r in ("page", "ink", "accent") if r not in roles]
    ok = bool(toks) and not missing_roles and bool(fams) and not unlicensed and bool(pairs) and not short
    ev = (f"{len(toks)} tokens, roles {', '.join(sorted(roles)) or 'none'}; {len(fams)} type famil(ies); "
          f"{len(pairs)} contrast pair(s)")
    if missing_roles:
        ev += f"; name palette.roles {', '.join(missing_roles)}"
    if unlicensed:
        ev += f"; licence to confirm for {', '.join(map(str, unlicensed))}"
    if short:
        ev += f"; below their bar: {', '.join(short)}"
    items.append(Item("tokens", "Palette tokens and roles, type by reference with its licence, and the text colour "
                                "pairs (4.5:1 body, 3:1 large)", ok, ev, "edit brand.json (palette, type, "
                                                                          "contrast_pairs), then slate brand validate "
                                + name))
    lg = pack.logos(view)
    good = [x["name"] for x in lg if (x["exists"] or x["inline"]) and x["build"]]
    items.append(Item("logos", "Logos as SVG, each with its build spec", bool(good),
                      ", ".join(good) if good else ("logo files missing" if lg else "none yet"),
                      "add logos/<name>.svg and brand.json logos.<name> {file, build}", required=False))
    sheet = folder / REVIEW_SHEET
    items.append(Item("kit_sheet", "The kit rebuilt in the brand's design language: the kit review sheet",
                      sheet.is_file(), REVIEW_SHEET if sheet.is_file() else "not rendered yet",
                      _cmd(name, "look", "--sheet"), required=False))
    boards = grounded_directions(raw)
    dirs = sorted({b["direction"] for b in boards})
    items.append(Item("moodboards", "At least two moodboard directions, each grounded in the brand kit",
                      len(dirs) >= 2, f"{len(dirs)}: {', '.join(dirs)}" if dirs else "none yet",
                      _cmd(name, "look", '--moodboard FILE --direction NAME --grounded-in KIT')))
    st = pack.reference_status(view, "look_motion")
    items.append(Item("approved", "A moving 10–15 second look-and-motion test with sound, approved (stills don't "
                                  "count)", st["approved"], st["reason"],
                      _approve_cmd(name, "look_motion", "LOOK_TEST.mp4")))
    return items


def review_entry_source(google: list[tuple[str, str]], kit_root: Path) -> str:
    """A review entry that registers the brand's Google Fonts families before the kit's review root (the kit's own
    review entry registers none, and its font gate refuses a face nobody registered)."""
    lines = ["// Written by slate brand onboard --step look --sheet: the kit's review sheet for one brand.",
             "import {registerRoot} from 'remotion';"]
    if google:
        lines.append("import {registerGoogleFont} from '@slate/kit';")
    for i, (_, module) in enumerate(google):
        lines.append(f"import {{loadFont as loadFont{i}}} from '{module}';")
    lines.append(f"import {{KitReviewRoot}} from '{(kit_root / 'review' / 'root').as_posix()}';")
    for i, (family, _) in enumerate(google):
        lines.append(f"registerGoogleFont({jsonio.dumps(family, indent=None).strip()}, loadFont{i});")
    lines += ["registerRoot(KitReviewRoot);", ""]
    return "\n".join(lines)


def google_families(raw: dict) -> list[str]:
    fams = (raw.get("type") or {}).get("families") if isinstance(raw.get("type"), dict) else None
    items = fams if isinstance(fams, list) else list((fams or {}).values())
    out = []
    for f in items:
        if not isinstance(f, dict):
            continue
        src = f.get("source")
        kind = src.get("kind") if isinstance(src, dict) else src
        family = (src.get("family") if isinstance(src, dict) else None) or f.get("family")
        if kind == "google" and family and family not in out:
            out.append(family)
    return out


def render_review_sheet(folder: Path, raw: dict) -> Path:
    """Render the kit review sheet for this brand with the engine (one still), into onboarding/look/."""
    from slate.engine import bridge  # noqa: PLC0415

    engine = bridge.engine_dir()
    kit = engine / "remotion" / "kit"
    neutral = engine / "remotion" / "neutral"
    if not (kit / "review" / "root.tsx").is_file() or not (neutral / "film.json").is_file():
        raise MissingPrerequisite("This engine has no kit review sheet (remotion/kit/review) or neutral film.",
                                  hint="slate engine install")
    google = []
    for fam in google_families(raw):
        module = "@remotion/google-fonts/" + re.sub(r"[^A-Za-z0-9]", "", fam)
        if not (engine / "node_modules" / "@remotion" / "google-fonts" / "dist" / "esm" /
                f"{module.rsplit('/', 1)[1]}.mjs").is_file():
            raise MissingPrerequisite(f"{fam} comes from Google Fonts, but the engine's @remotion/google-fonts has no "
                                      f"{fam}, so the font gate would fail.",
                                      hint="Name a Google family the engine has, or install the face and use "
                                           "source: installed.")
        google.append((fam, module))
    scratch = Path(tempfile.mkdtemp(prefix=f"review-{_slug(raw.get('name') or 'brand')}-",
                                    dir=paths.ensure_dir(paths.cache_dir() / "brand-review")))
    try:
        film = scratch / "film"
        shutil.copytree(neutral, film)
        fj = jsonio.read(film / "film.json")
        fj.update(id=f"review-{_slug(raw.get('name') or 'brand')}", brand=raw.get("name"))
        jsonio.write_atomic(film / "film.json", fj)
        entry = scratch / "review-entry.tsx"
        entry.write_text(review_entry_source(google, kit), encoding="utf-8")
        result = bridge.call("stills", {"film": str(film), "entry": str(entry), "specs": [f"Kit-Review:{REVIEW_FRAME}"],
                                        "out_dir": str(scratch / "out"), "scale": 1, "brand_path": str(folder)},
                             timeout=900)
        stills = result.get("stills") or []
        if not stills:
            raise SlateError("The engine rendered no review still.")
        dest = folder / REVIEW_SHEET
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stills[0]["file"], dest)
        return dest
    finally:
        try:
            bridge.call("bundle", {"film": str(scratch / "film"), "entry": str(scratch / "review-entry.tsx"),
                                   "release": True}, timeout=120)
        except SlateError:
            pass
        shutil.rmtree(scratch, ignore_errors=True)


def look(name: str, *, from_app: Iterable[str] = (), from_website: str | None = None, from_pdf: str | None = None,
         stdin_text: str | None = None, stdin_as: str | None = None, source_pin: str | None = None,
         measured: Iterable[str] = (), reference_films: Iterable[str] = (), moodboards: Iterable[str] = (),
         direction: str | None = None, grounded_in: str | None = None, adopt: bool = False, sheet: bool = False,
         analyze: bool = False, app_screens: Iterable[str] = (), revise: bool = False, dry_run: bool = False) -> dict:
    """The look-and-motion step: read the brand's own design language and motion, propose the law, write motion.json,
    MOTION.md and the checklist, and record moodboards and reference films."""
    folder, raw = open_pack(name, write=not dry_run)
    before = copy.deepcopy(raw)
    inputs = load_inputs(folder)
    changes: list[str] = []
    notes: list[str] = []
    procedures: list[dict] = []
    for spec in from_app:
        if sources.is_work_path(spec):
            proc = sources.peer_procedure(spec, raw.get("name") or folder.name)
            procedures.append(proc)
            inputs["pending"] = [p for p in inputs["pending"] if p.get("where") != proc["where"]] + [proc]
            changes.append(f"{proc['where']}: read-only procedure printed (the code stays on WORK)")
            continue
        sid = _record(inputs, sources.read_app(spec))
        changes.append(f"read {spec} in place ({sid})")
    if from_website:
        sid = _record(inputs, sources.read_website(from_website))
        changes.append(f"read {from_website} ({sid})")
    if from_pdf:
        sid = _record(inputs, sources.read_pdf(from_pdf))
        changes.append(f"read {from_pdf} in place ({sid})")
    if stdin_text is not None:
        if not stdin_as:
            raise UsageError("--stdin-as names the file the text on stdin came from (for citations).")
        remote = sources.remote_path(stdin_as)
        found = sources.read_file_text(stdin_text, remote)
        where = f"WORK:{remote}" if sources.is_work_path(stdin_as) else stdin_as
        rec = {"id": f"stdin-{_slug(Path(remote).name)}", "kind": "code", "where": where, "read": "in_place",
               "pin": source_pin or "not pinned yet: pass --source-pin <commit>", "files_read": 1}
        _record(inputs, {"source": rec, "found": found, "problems": found.get("problems")})
        inputs["pending"] = [p for p in inputs["pending"]
                             if not remote.startswith(sources.remote_path(str(p.get("where") or "")) + "/")]
        changes.append(f"read {where} from stdin (values only: no code is written anywhere)")
    for mfile in measured:
        fits, problems = measured_moves(mfile)
        inputs["measured"].update(fits)
        notes += [f"{mfile}: {p}" for p in problems]
        changes.append(f"{len(fits)} measured move(s) from {mfile}")
    look_sec = raw.get("look") if isinstance(raw.get("look"), dict) else {}
    for film in reference_films:
        p = Path(film).expanduser()
        if not p.is_file():
            raise UsageError(f"Reference film {film} doesn't exist.")
        refs = [r for r in look_sec.get("references") or [] if isinstance(r, dict)]
        rec = {"file": str(p.resolve()), "what": "reference film", "sha256": jsonio.sha256(p)}
        try:
            from slate.brand import media  # noqa: PLC0415

            rec["duration_s"] = media.probe(p).get("duration_s")
        except SlateError:
            pass
        if analyze and not dry_run:
            rec["analysis"] = _analyze_film(p, raw.get("name") or folder.name, inputs, notes)
        look_sec["references"] = _merge_list(refs, [rec], lambda r: str(r.get("file")))
        raw["look"] = look_sec
        changes.append(f"reference film {p.name} (read in place, not copied)"
                       + (f"; {len(rec['analysis']['moves'])} move(s) measured" if rec.get("analysis") else ""))
    for screen in app_screens:
        p = Path(screen).expanduser()
        if not p.is_file():
            raise UsageError(f"App screen {screen} doesn't exist.")
        refs = [r for r in look_sec.get("references") or [] if isinstance(r, dict)]
        look_sec["references"] = _merge_list(refs, [{"file": str(p.resolve()), "what": "app screen",
                                                     "sha256": jsonio.sha256(p)}], lambda r: str(r.get("file")))
        raw["look"] = look_sec
        changes.append(f"app screen {p.name} (kept where it is)")
    boards = list(moodboards)
    if boards:
        if not direction:
            raise UsageError("Say which direction these moodboards are (--direction NAME).")
        changes.append(_moodboard(folder, raw, boards, direction, grounded_in))
    elif direction or grounded_in:
        raise UsageError("--direction and --grounded-in describe moodboards: pass the files with --moodboard.")
    changes += fill_tokens(raw, inputs)
    proposal = propose_motion(inputs, raw)
    law = raw.get("motion") if isinstance(raw.get("motion"), dict) else {}
    if adopt or not law.get("eases"):
        raw["motion"] = {**_adoptable(proposal), **({"signature": law["signature"]} if law.get("signature") else {})}
        changes.append("adopted the proposed motion law" if law.get("eases") else "wrote a first motion law from "
                                                                                "the proposal")
    unapproved = guard(folder, before, raw, revise=revise)
    outputs: list[str] = []
    extra: dict = {"procedures": procedures}
    if not dry_run:
        if raw != before:
            pack.save(folder, raw)
        jsonio.write_atomic(folder / LOOK_INPUTS, {"generated": f"do not edit by hand: {_cmd(raw['name'], 'look')}",
                                                   **inputs})
        jsonio.write_atomic(folder / LOOK_PROPOSAL, proposal)
        motiondoc.write(folder, raw)
        outputs += [LOOK_INPUTS, LOOK_PROPOSAL, motiondoc.MOTION_JSON, motiondoc.MOTION_MD]
        if sheet:
            try:
                outputs.append(render_review_sheet(folder, raw).relative_to(folder).as_posix())
            except SlateError as exc:
                notes.append(f"the kit review sheet didn't render: {exc.message}")
                extra["sheet_error"] = exc.to_dict()
    for proc in procedures:
        notes.append(f"{proc['where']}: {proc['why']} Run: " + " · ".join(s for s in proc["steps"]
                                                                            if not s.startswith(" ")))
    return _finish(folder, raw, "look", look_items(folder, raw), outputs=outputs, changes=changes,
                   unapproved=unapproved, notes=notes, dry_run=dry_run, extra=extra)


# ---------------------------------------------------------------------------------------------------------- voice
def voice_items(folder: Path, raw: dict) -> list[Item]:
    name = raw.get("name") or folder.name
    v = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    recording = v.get("provider") == "recording" or v.get("source") == "recording"
    items = [Item("voice", "A voice for the brand: cast from the library, designed, the owner's own verified clone, "
                           "or real recordings", bool(v.get("voice_id")) or recording,
                  f"{v.get('voice_name') or v.get('voice_id')} ({v.get('source')})" if v.get("voice_id") else
                  ("real recordings" if recording else "none chosen yet"),
                  f"slate voice cast --brand {name}  (or slate voice design --brand {name}), then "
                  + _cmd(name, "voice", "--voice-id ID"))]
    problems: list[str] = []
    try:
        from slate.voice import profile  # noqa: PLC0415

        prof = profile.load(_view(folder, raw))
        problems = profile.policy_problems(prof)
    except ImportError:
        problems = ["the voice module isn't installed, so the voice policy can't be checked"]
    except SlateError as exc:
        problems = [exc.message]
    items.append(Item("policy", "The voice passes the voice policy (no clone of anyone but the owner, never a public "
                                "figure)", not problems, "; ".join(problems) or "clean",
                      _cmd(name, "voice", "--source library|designed|clone|recording")))
    pinned_missing = [k for k in ("model", "seed") if v.get(k) in (None, "")]
    if not isinstance(v.get("settings"), dict) or not v.get("settings"):
        pinned_missing.append("settings")
    if recording:
        pinned_missing = []
    items.append(Item("pinned", "The take's model, settings and seed recorded", not pinned_missing,
                      "all recorded" if not pinned_missing else f"missing {', '.join(pinned_missing)}",
                      _cmd(name, "voice", "--model MODEL --seed N --settings '{\"stability\": 0.5, "
                                          "\"similarity_boost\": 0.75}'")))
    lex = v.get("lexicon") or []
    items.append(Item("lexicon", "The brand words Whisper must hear in full (the lexicon)", bool(lex),
                      ", ".join(map(str, lex)) or "none yet", _cmd(name, "voice", '--lexicon "WORD"'),
                      required=False))
    take = jsonio.read(folder / VOICE_TAKE, default=None)
    take_ok = isinstance(take, dict) and Path(str(take.get("file"))).is_file()
    items.append(Item("take", "A take to approve", take_ok, str(take.get("file")) if take_ok else "none recorded yet",
                      _cmd(name, "voice", "--take FILE.wav"), required=False))
    st = pack.reference_status(_view(folder, raw), "voice")
    items.append(Item("approved", "The take approved, with its model, settings and seed", st["approved"], st["reason"],
                      _approve_cmd(name, "voice", str(take.get("file")) if take_ok else "TAKE.wav")))
    return items


def voice(name: str, *, take: str | None = None, voice_id: str | None = None, voice_name: str | None = None,
          model: str | None = None, seed: int | None = None, settings: str | None = None, source: str | None = None,
          lexicon: Iterable[str] = (), revise: bool = False, dry_run: bool = False) -> dict:
    """The voice step: record the voice, its model, settings and seed, the lexicon and the take to approve."""
    folder, raw = open_pack(name, write=not dry_run)
    before = copy.deepcopy(raw)
    v = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    raw["voice"] = v
    changes: list[str] = []
    for key, val in (("voice_id", voice_id), ("voice_name", voice_name), ("model", model), ("source", source)):
        if val is not None:
            v[key] = val
            changes.append(f"{key}: {val}")
    if source == "recording":
        v["provider"] = "recording"
    elif source:
        v.setdefault("provider", "elevenlabs")
    if seed is not None:
        if seed < 0:
            raise UsageError("A seed is a whole number from 0 up.")
        v["seed"] = int(seed)
        changes.append(f"seed: {seed}")
    if settings:
        try:
            parsed = json.loads(settings)
        except json.JSONDecodeError as exc:
            raise UsageError(f"--settings isn't JSON: {exc.msg}.",
                             hint='For example: --settings \'{"stability": 0.5, "similarity_boost": 0.75}\'') from exc
        if not isinstance(parsed, dict):
            raise UsageError("--settings must be a JSON object.")
        v["settings"] = parsed
        changes.append("settings")
    for w in lexicon:
        lex = v.setdefault("lexicon", [])
        if str(w).strip() and str(w).strip() not in lex:
            lex.append(str(w).strip())
            changes.append(f"lexicon word {w!r}")
    unapproved = guard(folder, before, raw, revise=revise)
    outputs: list[str] = []
    take_rec = None
    if take:
        p = Path(take).expanduser()
        if not p.is_file():
            raise UsageError(f"Take {take} doesn't exist.")
        take_rec = {"generated": f"do not edit by hand: {_cmd(raw.get('name') or folder.name, 'voice')}",
                    "file": str(p.resolve()), "sha256": jsonio.sha256(p), "recorded_at": _now(),
                    "voice": {k: v.get(k) for k in ("provider", "source", "voice_id", "voice_name", "model",
                                                     "settings", "seed")}}
        try:
            from slate.brand import media  # noqa: PLC0415

            take_rec["duration_s"] = media.probe(p).get("duration_s")
        except SlateError:
            pass
        changes.append(f"take {p.name}")
    if not dry_run:
        if raw != before:
            pack.save(folder, raw)
        if take_rec:
            jsonio.write_atomic(folder / VOICE_TAKE, take_rec)
            outputs.append(VOICE_TAKE)
    return _finish(folder, raw, "voice", voice_items(folder, raw), outputs=outputs, changes=changes,
                   unapproved=unapproved, notes=[], dry_run=dry_run)


# ---------------------------------------------------------------------------------------------------------- sound
def _audio(path: Path):
    from slate.sound import audio  # noqa: PLC0415

    if audio.is_wav(path):
        x, sr = audio.read_wav(path)
    else:
        sr = audio.DEFAULT_SR
        x = audio.load(path, sr)
    return x, sr


def measure_levels(proof: str | Path, *, stems: str | Path | None = None, voice: str | Path | None = None) -> dict:
    """The proof's length and loudness, and its levels against the voice (LU per stem, or the whole bed under the
    voice when only the voice is given separately). Measured by the sound module's meter."""
    import numpy as np  # noqa: PLC0415

    from slate.sound import measure  # noqa: PLC0415

    p = Path(proof).expanduser()
    if not p.is_file():
        raise UsageError(f"Sound proof {proof} doesn't exist.")
    whole = measure.loudness(p)
    x, sr = _audio(p)
    out: dict[str, Any] = {"file": str(p.resolve()), "sha256": jsonio.sha256(p), "duration_s": round(len(x) / sr, 4),
                           "integrated_lufs": whole.get("integrated_lufs"),
                           "true_peak_dbtp": whole.get("true_peak_dbtp"), "engine": measure.engine_info(),
                           "levels": {}, "method": None}
    if stems:
        d = Path(stems).expanduser()
        vo = next((d / n for n in ("vo.wav", "voice.wav") if (d / n).is_file()), None)
        if vo is None:
            raise UsageError(f"{stems} has no vo.wav (or voice.wav): the levels are measured against the voice.")
        vo_lufs = measure.loudness(vo, true_peak=False, lra=False)["integrated_lufs"]
        for s in sorted(d.glob("*.wav")):
            if s == vo:
                continue
            lu = measure.lu_vs(vo_lufs, measure.loudness(s, true_peak=False, lra=False)["integrated_lufs"])
            if lu is not None:
                out["levels"][s.stem] = lu
        out.update(method="stems", voice_lufs=vo_lufs, stems=sorted(s.name for s in d.glob("*.wav")))
    elif voice:
        vp = Path(voice).expanduser()
        if not vp.is_file():
            raise UsageError(f"Voice {voice} doesn't exist.")
        vx, vsr = _audio(vp)
        if vsr != sr:
            raise UsageError(f"The proof is {sr} Hz and the voice {vsr} Hz; give both at one rate (or give stems).")
        n = min(len(x), len(vx))
        xx, vv = x[:n], vx[:n]
        if xx.shape[1] != vv.shape[1]:
            vv = np.repeat(vv[:, :1], xx.shape[1], axis=1) if vv.shape[1] == 1 else vv[:, : xx.shape[1]]
        vo_lufs = measure.integrated(vv, sr)
        out["levels"]["bed"] = measure.lu_vs(vo_lufs, measure.integrated(xx - vv, sr))
        out.update(method="proof minus voice", voice_lufs=vo_lufs, voice_file=str(vp.resolve()))
    return out


def sound_items(folder: Path, raw: dict) -> list[Item]:
    name = raw.get("name") or folder.name
    s = raw.get("sound") if isinstance(raw.get("sound"), dict) else {}
    missing = [k for k in ("key", "tempo") if s.get(k) in (None, "", [])]
    if not s.get("materials"):
        missing.append("materials")
    items = [Item("palette", "The brand's own sound palette: key, tempo range, materials, banned timbres and motif, "
                             "starting from proof 2's taste", not missing,
                  "set" if not missing else f"missing {', '.join(missing)}",
                  _cmd(name, "sound", '--key "D major" --tempo 84-96 --material "wood knock" --banned-timbre chimes'))]
    lv = jsonio.read(folder / SOUND_LEVELS, default=None)
    have = isinstance(lv, dict) and lv.get("file")
    dur = lv.get("duration_s") if have else None
    lo, hi = SOUND_PROOF_S
    ok = bool(have) and dur is not None and lo - SLACK_S <= float(dur) <= hi + SLACK_S
    items.append(Item("proof", "A 10–20 second reference proof", ok,
                      (f"{Path(lv['file']).name}, {dur:.2f} s" + ("" if ok else " (needs 10–20 s)")) if have else
                      "none yet", _cmd(name, "sound", "--proof PROOF.wav --stems STEMS_DIR")))
    measured = s.get("reference_measured_from") if isinstance(s.get("reference_measured_from"), dict) else {}
    levels_ok = bool(s.get("reference_levels")) and have and measured.get("sha256") == lv.get("sha256")
    items.append(Item("levels", "Its levels measured against the voice", bool(levels_ok),
                      ", ".join(f"{k} {v:+.1f} LU" for k, v in (s.get("reference_levels") or {}).items()
                                if isinstance(v, (int, float))) or "not measured yet",
                      _cmd(name, "sound", "--proof PROOF.wav --stems STEMS_DIR (with vo.wav) | --voice VOICE.wav")))
    st = pack.reference_status(_view(folder, raw), "sound")
    items.append(Item("approved", "The sound reference approved", st["approved"], st["reason"],
                      _approve_cmd(name, "sound", lv["file"] if have else "PROOF.wav")))
    return items


def _tempo(text: str) -> float | list[float]:
    parts = [p for p in re.split(r"\s*[-–,]\s*", str(text).strip()) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise UsageError(f"--tempo is beats per minute or a range, e.g. 90 or 84-96 (got {text!r}).") from None
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2 and 0 < nums[0] <= nums[1]:
        return nums
    raise UsageError(f"--tempo range {text!r} must be low-high.")


def sound(name: str, *, proof: str | None = None, stems: str | None = None, voice: str | None = None,
          key: str | None = None, tempo: str | None = None, materials: Iterable[str] = (),
          banned_timbres: Iterable[str] = (), instrumentation: Iterable[str] = (),
          motif_instrument: str | None = None, revise: bool = False, dry_run: bool = False) -> dict:
    """The sound step: the brand's own palette, and a reference proof measured against the voice."""
    folder, raw = open_pack(name, write=not dry_run)
    before = copy.deepcopy(raw)
    s = raw.get("sound") if isinstance(raw.get("sound"), dict) else {}
    raw["sound"] = s
    changes: list[str] = []
    if key:
        s["key"] = key
        changes.append(f"key {key}")
    if tempo:
        s["tempo"] = _tempo(tempo)
        changes.append(f"tempo {s['tempo']}")
    for field_name, values in (("materials", materials), ("banned_timbres", banned_timbres),
                               ("instrumentation", instrumentation)):
        vals = [str(x).strip() for x in values if str(x).strip()]
        if vals:
            cur = s.get(field_name) if isinstance(s.get(field_name), list) else []
            s[field_name] = list(dict.fromkeys([*cur, *vals]))
            changes.append(f"{field_name}: {', '.join(vals)}")
    if motif_instrument:
        motif = s.get("motif") if isinstance(s.get("motif"), dict) else {}
        motif["instrument"] = motif_instrument
        s["motif"] = motif
        changes.append(f"motif instrument {motif_instrument}")
    levels = None
    if proof:
        levels = measure_levels(proof, stems=stems, voice=voice)
        if levels["levels"]:
            s["reference_levels"] = dict(levels["levels"])
            s["reference_measured_from"] = {"file": levels["file"], "sha256": levels["sha256"],
                                            "method": levels["method"], "engine": levels["engine"].get("name")}
        changes.append(f"measured {Path(proof).name}: {levels['duration_s']:.2f} s"
                       + (f", {len(levels['levels'])} level(s) against the voice" if levels["levels"] else
                          "; no voice to measure against yet (give --stems or --voice)"))
    elif stems or voice:
        raise UsageError("--stems and --voice measure a proof: give it with --proof.")
    unapproved = guard(folder, before, raw, revise=revise)
    outputs: list[str] = []
    if not dry_run:
        if raw != before:
            pack.save(folder, raw)
        if levels:
            jsonio.write_atomic(folder / SOUND_LEVELS, {"generated": f"do not edit by hand: "
                                                                     f"{_cmd(raw.get('name') or folder.name, 'sound')}",
                                                        **levels})
            outputs.append(SOUND_LEVELS)
    notes = ["The palette starts from proof 2's taste (pads, small felt notes, hollow wood clicks, no chimes); the "
             "brand's own values here win, key by key."]
    return _finish(folder, raw, "sound", sound_items(folder, raw), outputs=outputs, changes=changes,
                   unapproved=unapproved, notes=notes, dry_run=dry_run)


def human(result: dict) -> str:
    """A step's result for people: what changed, the checklist, and the next command."""
    lines = [f"{result['brand']}: {TITLES[result['step']]}" + (" (dry run: nothing written)" if result.get("dry_run")
                                                              else "")]
    for c in result.get("changes") or []:
        lines.append(f"  + {c}")
    if result.get("unapproved"):
        lines.append(f"  ! needs a person's approval again: {', '.join(result['unapproved'])}")
    for it in result["items"]:
        lines.append(f"  [{'x' if it['done'] else ' '}] {it['text']}" + (f" — {it['evidence']}" if it['evidence']
                                                                           else ""))
    for n in result.get("notes") or []:
        lines.append(f"  · {n}")
    if result.get("next"):
        lines.append(f"next: {result['next']['command']}")
    return "\n".join(lines)
