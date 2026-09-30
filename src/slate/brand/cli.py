"""`slate brand …`: brand packs and onboarding.

    slate brand list                     every pack Slate can see, and where each stands
    slate brand show <name>              a pack in brief (--raw for brand.json, --checklists for onboarding)
    slate brand init <name>              a new pack in the private store (never in Slate's repo)
    slate brand validate [<name> …]      the schema and the rules (font files, repo, motion docs, contrast …)
    slate brand onboard <name>           the four onboarding steps; --step truth|look|voice|sound runs one
    slate brand approve <name> <ref>     record a person's approval of a reference (--file, --by)
    slate brand status <name>            "needs onboarding" until all four references are approved

Every command prints a person's answer, or one JSON object with --json. Exit codes: 0 ok, 1 refused or a check
failed, 2 usage, 3 missing prerequisite.
"""

from __future__ import annotations

import argparse
import json
import sys

from slate import cli, gates, log
from slate.brand import approve as approve_mod
from slate.brand import create, onboard, pack, validate
from slate.errors import UsageError

EPILOG = "Exit codes: 0 ok, 1 refused or a check failed, 2 usage, 3 missing prerequisite."

ONBOARD_HELP = """Once per brand, before any script. Four steps, each ending in a reference a person approves:

  truth   sources of truth (pinned; company code read in place), a named claim authority (for a brand the
          owner doesn't own, a person at that organisation) and the approved-claims list; writes TRUTH.md
  look    the brand's own components and motion values (CSS easing tokens, SwiftUI springs), two or three
          reference films measured with fit_ease, moodboard directions grounded in the brand kit; writes the
          palette, type and motion law, motion.json, MOTION.md and (with --sheet) the kit review sheet
  voice   a take with its model, settings and seed, within the voice policy
  sound   the brand's own palette (starting from proof 2's taste) and a 10-20 second proof measured
          against the voice

Each run records what it's given, writes that step's outputs and onboarding/<step>/CHECKLIST.md, and names the
next command. Without --step it shows where all four stand. It never runs on the repo's neutral packs, and a change
to what an approved reference covers needs --revise (and then a person's approval again)."""

APPROVE_HELP = """Checks the reference, then records who approved it, when, and the SHA-256 of exactly this file (kept
in the pack as references/<reference>.<ext>). A reference stays approved only while that file and the part of
brand.json it covers are unchanged.

  truth        the claim authority approves it (--by is their name); sources pinned, claims sourced
  look_motion  a moving 10-15 second test with sound (stills don't count), after two or more moodboard
               directions grounded in the brand kit, with the motion law and tokens in place; for a brand the
               owner doesn't own, approved by the approver named at that organisation
  voice        a take you can hear, with its model, settings and seed recorded
  sound        a 10-20 second proof whose levels were measured against the voice from this exact file"""


def _dry(args: argparse.Namespace) -> bool:
    return bool(cli.flag(args, "dry_run", False))


# ------------------------------------------------------------------------------------------------------ list, show
def cmd_list(args: argparse.Namespace) -> int:
    rows = pack.list_packs()
    if not rows:
        log.result({"packs": []}, "No brand packs found. `slate brand init <name>` makes one.")
        return 0
    width = max(len(r["name"]) for r in rows)
    lines = []
    for r in rows:
        tail = f"missing {', '.join(r['missing'])}" if r.get("missing") else ""
        where = "neutral" if r["neutral"] else r["where"]
        lines.append(f"{r['name']:<{width}}  {where:<7}  {r.get('status', '?'):<16}  {tail}".rstrip()
                     + ("  (hidden by the repo's pack)" if r.get("shadowed") else ""))
    log.result({"packs": rows}, "\n".join(lines))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    p = pack.load(args.name)
    if args.raw:
        log.result({"brand": p.get("name"), "brand_json": pack.strip_runtime(p)},
                   json.dumps(pack.strip_runtime(p), indent=1, ensure_ascii=False))
        return 0
    s = pack.summary(p)
    out = {"brand": s["name"], "summary": s}
    if args.checklists:
        out["checklists"] = validate.onboarding_items(str(p["_dir"]))
    st = s["status"]
    lines = [f"{s['display_name'] or s['name']} ({s['name']}): {st['status']}, in the {s['where']} ({s['dir']})",
             f"  ownership   {s['ownership'].get('kind') or 'not stated'}"
             + (f", {s['ownership']['organisation']}" if s["ownership"].get("organisation") else "")
             + (", company material (stays on this Mac)" if s["ownership"].get("company_material") else ""),
             f"  palette     {s['palette']['tokens']} tokens; roles "
             + (", ".join(f"{k} {v}" for k, v in s["palette"]["roles"].items()) or "none"),
             f"  contrast    {s['contrast_pairs']} declared text pair(s)",
             f"  type        {'; '.join(s['type']) or 'none yet'}",
             f"  logos       {', '.join(s['logos']) or 'none yet'}" + ("; a motif" if s["motif"] else ""),
             f"  look        {s['look'] or 'none yet'}",
             f"  motion      {', '.join(s['motion']['eases']) or 'no law yet (the kit neutral law stands in)'}",
             f"  voice       {s['voice'].get('voice_name') or s['voice'].get('voice_id') or 'none yet'}"
             f" ({s['voice'].get('source')}, model {s['voice'].get('model') or 'the registry default'})",
             f"  sound       key {s['sound'].get('key') or '—'}, tempo {s['sound'].get('tempo') or '—'}, materials "
             f"{', '.join(s['sound'].get('materials') or []) or '—'}",
             f"  truth       {s['truth']['sources']} source(s), {s['truth']['approved_claims']} claim(s), "
             f"{s['truth']['banned_words']} banned word(s); authority "
             f"{(s['truth']['claim_authority'] or {}).get('name') or 'not named'}",
             f"  uploads     {'allowed' if s['uploads_allowed'] else 'not allowed'} to generators",
             f"  skills      {', '.join(s['skills']) or 'none'}"]
    lines.append("  references")
    for k, r in st["references"].items():
        lines.append(f"    {k:<11} {r['state'].replace('_', ' ')}: {r['reason']}")
    if st["next"]:
        lines.append(f"next: {st['next']['command']}")
    log.result(out, "\n".join(lines))
    return 0


# ------------------------------------------------------------------------------------------------------------ init
def cmd_init(args: argparse.Namespace) -> int:
    res = create.init(args.name, store=args.store, from_app=args.from_app or [], from_website=args.from_website,
                      from_pdf=args.from_pdf, from_pack=args.from_pack, display_name=args.display_name,
                      kind=args.kind, organisation=args.organisation, company_material=args.company_material,
                      dry_run=_dry(args))
    if res.get("dry_run"):
        log.result(res, f"Dry run: would create {res['dir']}. Nothing changed.")
        return 0
    lines = [f"Created {res['brand']} at {res['dir']}"
             + (f" (from the {res['seeded']['from_pack']} pack's design)" if res.get("seeded") else "")]
    if not res.get("in_store"):
        lines.append("  note: that's outside the private store, so pass its path to other commands")
    look = res.get("look")
    if look:
        lines.append(onboard.human(look))
    lines.append(f"status: {res['status']}")
    lines.append(f"next: {res['next']}")
    log.result(res, "\n".join(lines))
    return 0


# -------------------------------------------------------------------------------------------------------- validate
def cmd_validate(args: argparse.Namespace) -> int:
    if args.names:
        packs = {n: validate.validate(n) for n in args.names}
    else:
        packs = validate.validate_all()
    code = 0
    out, lines = {}, []
    for name, results in packs.items():
        summary = gates.summarize(results)
        out[name] = {"status": summary["status"], "results": [r.to_dict() for r in results],
                     "not_passed": summary["not_passed"]}
        if any(r.status == "FAIL" for r in results):
            code = 1
        lines.append(f"{name}:")
        lines += ["  " + line for line in gates.format_table(results).splitlines()]
    log.result({"packs": out, "ok": code == 0}, "\n".join(lines) if lines else "No brand packs to validate.")
    return code


# --------------------------------------------------------------------------------------------------------- onboard
STEP_FLAGS = {
    "truth": ("source", "source_id", "pin", "source_kind", "read", "authority", "authority_role", "authority_org",
              "authority_contact", "claim", "claim_source", "banned", "never", "sample_data", "import_file"),
    "look": ("from_app", "from_website", "from_pdf", "stdin_as", "source_pin", "measured", "reference_film",
             "analyze", "app_screen", "moodboard", "direction", "grounded_in", "adopt", "sheet"),
    "voice": ("take", "voice_id", "voice_name", "model", "seed", "settings", "voice_source", "lexicon"),
    "sound": ("proof", "stems", "voice_file", "key", "tempo", "material", "banned_timbre", "instrument",
              "motif_instrument"),
}


def _given(args: argparse.Namespace, dest: str) -> bool:
    v = getattr(args, dest, None)
    return v not in (None, False, [])


def cmd_onboard(args: argparse.Namespace) -> int:
    step = args.step
    stray = {d: s for s, dests in STEP_FLAGS.items() for d in dests if s != step and _given(args, d)}
    if stray:
        flag, owner = next(iter(stray.items()))
        raise UsageError(f"--{flag.replace('_', '-')} belongs to --step {owner}" + (f", not {step}" if step else "")
                         + ".", hint=f"slate brand onboard {args.name} --step {owner} --{flag.replace('_', '-')} …")
    dry = _dry(args)
    if not step:
        ov = onboard.overview(args.name)
        if not dry:
            folder = pack.find(args.name)
            if not pack.in_repo(folder):
                onboard.write_overview(folder, pack.read_raw(folder))
        lines = [f"{ov['brand']}: {ov['status']}"]
        for s, info in ov["steps"].items():
            lines.append(f"  {onboard.TITLES[s]:<16} {info['state'].replace('_', ' '):<13} {info['done']} of "
                         f"{info['of']} done" + (f"; next: {info['next']}" if info["state"] != "approved" else ""))
        log.result(ov, "\n".join(lines))
        return 0
    if step == "truth":
        res = onboard.truth(args.name, source=args.source, source_id=args.source_id, pin=args.pin,
                            kind=args.source_kind, read=args.read, authority=args.authority,
                            authority_role=args.authority_role, authority_org=args.authority_org,
                            authority_contact=args.authority_contact, claims=args.claim or [],
                            claim_source=args.claim_source, banned=args.banned or [], never=args.never or [],
                            sample_data=args.sample_data, import_file=args.import_file, revise=args.revise,
                            dry_run=dry)
    elif step == "look":
        text = sys.stdin.read() if args.stdin_as else None
        res = onboard.look(args.name, from_app=args.from_app or [], from_website=args.from_website,
                           from_pdf=args.from_pdf, stdin_text=text, stdin_as=args.stdin_as,
                           source_pin=args.source_pin, measured=args.measured or [],
                           reference_films=args.reference_film or [], moodboards=args.moodboard or [],
                           direction=args.direction, grounded_in=args.grounded_in, adopt=args.adopt,
                           sheet=args.sheet, analyze=args.analyze, app_screens=args.app_screen or [],
                           revise=args.revise, dry_run=dry)
    elif step == "voice":
        res = onboard.voice(args.name, take=args.take, voice_id=args.voice_id, voice_name=args.voice_name,
                            model=args.model, seed=args.seed, settings=args.settings, source=args.voice_source,
                            lexicon=args.lexicon or [], revise=args.revise, dry_run=dry)
    else:
        res = onboard.sound(args.name, proof=args.proof, stems=args.stems, voice=args.voice_file, key=args.key,
                            tempo=args.tempo, materials=args.material or [], banned_timbres=args.banned_timbre or [],
                            instrumentation=args.instrument or [], motif_instrument=args.motif_instrument,
                            revise=args.revise, dry_run=dry)
    log.result(res, onboard.human(res))
    return 0


# --------------------------------------------------------------------------------------------------------- approve
def cmd_approve(args: argparse.Namespace) -> int:
    res = approve_mod.approve(args.name, args.reference, args.file, args.by, note=args.note, in_place=args.in_place,
                              dry_run=_dry(args))
    if res.get("dry_run"):
        log.result(res, f"Dry run: {res['reference']} passes its checks; would record {args.by}'s approval of "
                        f"{args.file}. Nothing changed.")
        return 0
    rec, st = res["record"], res["status"]
    human = (f"Approved {res['reference']} for {res['brand']}: {rec['file']} (sha256 {rec['sha256'][:12]}…) by "
             f"{rec['approved_by']} at {rec['approved_at']}."
             + "".join(f"\n  still open (recommended): {x}" for x in res.get("still_open") or [])
             + f"\n{res['brand']}: {st['status']}"
             + (f" (still missing {', '.join(st['missing'])})\nnext: {st['next']['command']}" if st["missing"] else ""))
    log.result(res, human)
    return 0


# ---------------------------------------------------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace) -> int:
    st = pack.status(pack.load(args.name))
    lines = [f"{st['brand']}: {st['status']}"]
    for k, r in st["references"].items():
        lines.append(f"  {k:<11} {r['state'].replace('_', ' ')}: {r['reason']}")
    if st["next"]:
        lines.append(f"next: {st['next']['command']}")
    log.result(st, "\n".join(lines))
    return 0


# -------------------------------------------------------------------------------------------------------- register
def register(sub) -> None:
    grp = cli.add_group(sub, "brand", "brand packs and onboarding: list, show, init, validate, onboard, approve, status",
                        description=__doc__.split("\n\n")[0] + "\n\n" + __doc__.split("\n\n")[1], epilog=EPILOG)

    cli.add_command(grp, "list", cmd_list, "every brand pack Slate can see, and whether it's onboarded",
                    epilog=EPILOG)

    p = cli.add_command(grp, "show", cmd_show, "a brand pack in brief", epilog=EPILOG, description=(
        "Palette, contrast pairs, type, logos, look, motion law, voice, sound, truth, uploads, the brand's own skills "
        "and the four references. --raw prints brand.json; --checklists adds each onboarding step's checklist."))
    p.add_argument("name", help="the brand (a name, or a path to its pack)")
    p.add_argument("--raw", action="store_true", help="print brand.json itself")
    p.add_argument("--checklists", action="store_true", help="include each onboarding step's checklist")

    p = cli.add_command(grp, "init", cmd_init, "make a new brand pack in the private store", epilog=EPILOG,
                        description=(
                            "Makes <store>/<name>/ (the store is ~/code/slate-brands, or SLATE_BRANDS) from the "
                            "template, or from another pack's design with --from-pack. Refuses any folder inside "
                            "Slate's repo: the repo holds only the neutral packs. --from-app, --from-website and "
                            "--from-pdf start onboarding's look step on the brand's own material, read in place and "
                            "never copied; for code on the WORK Mac give --from-app work:<path> and follow the "
                            "read-only procedure it prints."))
    p.add_argument("name", help="the brand's name: lowercase letters, digits, dots, dashes, underscores")
    p.add_argument("--from-app", action="append", metavar="PATH",
                   help="the brand's website or app code, read in place (work:<path> for code on the WORK Mac)")
    p.add_argument("--from-website", metavar="URL", help="the brand's website (HTML and CSS, no browser), or a saved "
                                                         "copy of a page")
    p.add_argument("--from-pdf", metavar="FILE", help="the brand's guidelines PDF, read in place")
    p.add_argument("--from-pack", metavar="NAME", help="start from another pack's design (never its references)")
    p.add_argument("--store", metavar="DIR", help="make it here instead of the private store (never inside the repo)")
    p.add_argument("--display-name", help="the brand as it's written and said")
    p.add_argument("--kind", choices=("own", "client"), help="own: the owner's own brand; client: a brand the owner "
                                                              "doesn't own (default: client with --organisation)")
    p.add_argument("--organisation", help="whose brand it is, for a brand the owner doesn't own")
    p.add_argument("--company-material", action="store_true",
                   help="company material: the pack stays on this Mac and its code is read in place, never copied")

    p = cli.add_command(grp, "validate", cmd_validate, "check packs against the schema and the rules", epilog=EPILOG,
                        description=(
                            "Schema, location (only neutral packs in the repo; company packs never in a repository "
                            "with a remote), fonts (no font files; every family by reference, with its licence), "
                            "palette and contrast, logos, the motion law and its motion.json and MOTION.md, voice, "
                            "sound, truth, references (none changed since approval), uploads and brand skills. With "
                            "no name it checks every pack. Exit 1 when any check fails."))
    p.add_argument("names", nargs="*", metavar="NAME", help="brands to check (default: every pack)")

    p = cli.add_command(grp, "onboard", cmd_onboard, "walk a brand's onboarding: truth, look, voice, sound",
                        epilog=EPILOG, description=ONBOARD_HELP)
    p.add_argument("name", help="the brand (a name, or a path to its pack)")
    p.add_argument("--step", choices=onboard.STEPS, help="run one step (without it: where all four stand)")
    p.add_argument("--revise", action="store_true",
                   help="allow a change to what an approved reference covers (it then needs approving again)")
    t = p.add_argument_group("--step truth")
    t.add_argument("--source", metavar="WHERE", help="a source of truth: a path (read in place), a URL, or work:<path>")
    t.add_argument("--source-id", help="its short id, which claims cite (default: from its name)")
    t.add_argument("--pin", help="what exactly was read: a commit, a date or a hash (paths pin themselves)")
    t.add_argument("--source-kind", choices=("code", "website", "app", "guidelines", "document", "knowledge_base",
                                             "person", "demo", "video", "other"))
    t.add_argument("--read", choices=("in_place", "fetched", "copied", "told"))
    t.add_argument("--authority", metavar="NAME", help="the claim authority: the person who rules on claims")
    t.add_argument("--authority-role")
    t.add_argument("--authority-org", help="their organisation (required for a brand the owner doesn't own)")
    t.add_argument("--authority-contact")
    t.add_argument("--claim", action="append", metavar="TEXT", help="an approved claim (repeat for more)")
    t.add_argument("--claim-source", metavar="ID", help="the source id the claims in this run cite")
    t.add_argument("--banned", action="append", metavar="WORD", help="a word the brand never uses")
    t.add_argument("--never", action="append", metavar="TEXT", help="something films never claim or show")
    t.add_argument("--sample-data", metavar="FILE", help="the brand's sample data (JSON)")
    t.add_argument("--import", dest="import_file", metavar="FILE", help="merge a truth JSON (sources, claims, …)")
    lk = p.add_argument_group("--step look")
    lk.add_argument("--from-app", action="append", metavar="PATH",
                    help="website or app code, read in place (work:<path> prints the read-only procedure)")
    lk.add_argument("--from-website", metavar="URL", help="a website's HTML and CSS (no browser), or a saved page")
    lk.add_argument("--from-pdf", metavar="FILE", help="a guidelines PDF, read in place")
    lk.add_argument("--stdin-as", metavar="PATH", help="read one file's text from stdin (e.g. piped from peer cat); "
                                                       "PATH is where it lives, for citations")
    lk.add_argument("--source-pin", metavar="COMMIT", help="the commit the stdin file was read at")
    lk.add_argument("--measured", action="append", metavar="FILE",
                    help="moves measured in a reference film (samples or fitted beziers, JSON)")
    lk.add_argument("--app-screen", action="append", metavar="FILE", help="a screenshot of the product's own screens "
                                                                           "(kept in place)")
    lk.add_argument("--reference-film", action="append", metavar="FILE", help="a reference film (kept in place)")
    lk.add_argument("--analyze", action="store_true", help="measure each --reference-film's moves with slate analyze "
                                                            "(cuts and eases; fitted with fit_ease)")
    lk.add_argument("--moodboard", action="append", metavar="FILE", help="a moodboard image (repeat for more)")
    lk.add_argument("--direction", help="which direction the moodboards are")
    lk.add_argument("--grounded-in", metavar="KIT", help="the brand kit they were grounded in: elevenlabs:<id>, "
                                                         "higgsfield:<id> or pack")
    lk.add_argument("--adopt", action="store_true", help="write the proposed motion law into brand.json")
    lk.add_argument("--sheet", action="store_true", help="render the kit review sheet (one still, with the engine)")
    v = p.add_argument_group("--step voice")
    v.add_argument("--take", metavar="FILE", help="the take to approve")
    v.add_argument("--voice-id")
    v.add_argument("--voice-name")
    v.add_argument("--model", help="the TTS model the take was made with")
    v.add_argument("--seed", type=int)
    v.add_argument("--settings", metavar="JSON", help='e.g. \'{"stability": 0.5, "similarity_boost": 0.75}\'')
    v.add_argument("--voice-source", choices=("library", "designed", "clone", "recording"))
    v.add_argument("--lexicon", action="append", metavar="WORD", help="a brand word Whisper must hear in full")
    s = p.add_argument_group("--step sound")
    s.add_argument("--proof", metavar="FILE", help="the 10–20 second reference proof")
    s.add_argument("--stems", metavar="DIR", help="its stems, with vo.wav, to measure each against the voice")
    s.add_argument("--voice", dest="voice_file", metavar="FILE", help="the voice alone, to measure the bed against")
    s.add_argument("--key", help='e.g. "D major"')
    s.add_argument("--tempo", help="bpm, or a range like 84-96")
    s.add_argument("--material", action="append", help="what the effects are made of (repeat)")
    s.add_argument("--banned-timbre", action="append", help="a timbre the brand never uses (repeat)")
    s.add_argument("--instrument", action="append", help="instrumentation for the score (repeat)")
    s.add_argument("--motif-instrument", help="the character's or motif's own instrument")

    p = cli.add_command(grp, "approve", cmd_approve, "record a person's approval of a reference", epilog=EPILOG,
                        description=APPROVE_HELP)
    p.add_argument("name", help="the brand")
    p.add_argument("reference", help="truth, look_motion, voice or sound")
    p.add_argument("--file", required=True, help="exactly what was approved")
    p.add_argument("--by", required=True, help="the person who approved it (for truth, the claim authority)")
    p.add_argument("--note", help="their words, or where they said it")
    p.add_argument("--in-place", action="store_true", help="record the file where it is instead of keeping a copy "
                                                           "in the pack")

    p = cli.add_command(grp, "status", cmd_status, "onboarded, or \"needs onboarding\" and the next step",
                        epilog=EPILOG, description=(
                            "A brand is onboarded once all four references (truth, look_motion, voice, sound) are "
                            "approved and unchanged. Until then this says \"needs onboarding\" and names the next "
                            "step, which is how the front door routes a new brand to onboarding."))
    p.add_argument("name", help="the brand")
