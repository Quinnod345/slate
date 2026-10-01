"""`slate brand validate`: is a pack well formed, and does it keep the rules?

Every check is a gate result (docs/ARCHITECTURE.md §6), named `brand.<check>`. FAIL is a broken rule or broken data;
REVIEW is something a person should look at (a licence still to confirm, a pack that isn't onboarded yet); PASS is
fine. Only FAIL makes the command exit 1. The rules:

- brand.json matches the brand schema;
- only the neutral packs live in Slate's repo, and a company pack never sits in a repository with a remote (it stays
  on this Mac);
- fonts by reference only: no font files anywhere in a pack, every family's source is installed, Google Fonts or a
  type service's kit (never a font file), and every family's licence is noted;
- palette tokens are colours, roles point at tokens, declared text pairs meet their bar (4.5:1 body, 3:1 large);
- logos are SVGs the engines can build (shapes, a viewBox, the keystone the build names);
- the motion law is inline (engines read brand.json as it is), readable by the kit, every ease has its lineage, and
  motion.json and MOTION.md match it;
- the voice is a valid voice profile that passes the voice policy; the sound palette is coherent;
- the truth pack's claims cite its sources, and a brand the owner doesn't own names its authority's organisation;
- no approved reference has changed since its approval; the uploads decision is stated one way.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from slate import __version__, gates, jsonio
from slate.brand import motiondoc, onboard, pack, sources
from slate.errors import SlateError
from slate.gates import GateResult

FONT_FILE_EXTS = frozenset({".otf", ".ttf", ".ttc", ".woff", ".woff2", ".eot", ".dfont", ".pfb", ".pfm", ".afm",
                            ".otc", ".fon", ".fnt", ".suit"})
ENGINE = gates.engine("slate brand validate", __version__)


class _Ctx:
    def __init__(self, folder: Path):
        self.folder = folder
        self.raw = pack.read_raw(folder)
        self.view = pack.load(folder)
        self.name = self.view.get("name") or folder.name
        self.template = folder.name == "_template" and pack.is_neutral(folder)
        self.neutral = pack.is_neutral(folder)
        self.inputs = {str(folder / pack.PACK_FILE): jsonio.sha256(folder / pack.PACK_FILE)}

    def result(self, check: str, status: str, summary: str, evidence: list[str] | None = None, **details) -> GateResult:
        return GateResult(f"brand.{check}", status, summary, evidence=list(evidence or []), engine=ENGINE,
                          inputs=self.inputs, details={"brand": self.name, **details})


def _schema(c: _Ctx) -> GateResult:
    problems = jsonio.validate(c.raw, "brand")
    if problems:
        return c.result("schema", "FAIL", f"brand.json doesn't match the brand schema ({len(problems)} problem"
                                          f"{'s' if len(problems) != 1 else ''})", problems[:30])
    return c.result("schema", "PASS", "brand.json matches the brand schema")


def _location(c: _Ctx) -> GateResult:
    if pack.in_repo(c.folder) and not c.neutral:
        return c.result("location", "FAIL", f"{c.name} is a real brand inside Slate's repo, which holds only the "
                                            "neutral packs", [str(c.folder)],
                        hint=f"move it to the private store ({pack.paths.brands_store()})")
    if c.neutral:
        return c.result("location", "PASS", "Slate's own neutral pack, in its repo")
    if c.name in pack.NEUTRAL:
        return c.result("location", "REVIEW", f"a pack named {c.name!r} outside the repo is hidden by Slate's own "
                                              "neutral pack of that name", [str(c.folder)])
    if pack.is_company_material(c.view):
        remotes = sources.git_remotes(c.folder)
        if remotes:
            return c.result("location", "FAIL", "company material stays on this Mac, but this pack sits in a git "
                                                f"repository with remote(s) {', '.join(remotes)}",
                            [str(c.folder)], remotes=remotes)
    return c.result("location", "PASS", f"in the {pack.where(c.folder)} ({c.folder.parent})")


def _families(raw: dict) -> list[dict]:
    fams = (raw.get("type") or {}).get("families") if isinstance(raw.get("type"), dict) else None
    if isinstance(fams, dict):
        return [{"role": k, **v} for k, v in fams.items() if isinstance(v, dict)]
    return [f for f in fams or [] if isinstance(f, dict)]


def font_files(folder: Path) -> list[str]:
    """Every font file inside a pack folder (none is allowed: fonts are named by reference)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        for n in filenames:
            if Path(n).suffix.lower() in FONT_FILE_EXTS:
                out.append(str((Path(dirpath) / n).relative_to(folder)))
    return sorted(out)


def _fonts(c: _Ctx) -> GateResult:
    files = font_files(c.folder)
    fails, reviews = [], []
    if files:
        fails.append(f"font files in the pack (fonts are named by reference, never kept): {', '.join(files)}")
    fams = _families(c.raw)
    for f in fams:
        fam = f.get("family") or "a family"
        src = f.get("source")
        kind = src.get("kind") if isinstance(src, dict) else src
        url = src.get("url") if isinstance(src, dict) else (src if isinstance(src, str) and src.startswith("http")
                                                             else None)
        if url and sources.FONT_FILE.search(url):
            fails.append(f"{fam}: {url} is a font file; name the font installed, from Google Fonts, or by its type "
                         "service's kit stylesheet")
        elif kind not in ("installed", "google", "kit") and not url:
            fails.append(f"{fam}: source {src!r} isn't installed, google or a kit URL")
        licence = str(f.get("licence") or f.get("license") or "").strip()
        if not licence:
            fails.append(f"{fam}: no licence noted")
        elif licence.lower().startswith(("unknown", "to confirm", "tbd")):
            reviews.append(f"{fam}: licence still to confirm ({licence})")
        if kind == "google":
            missing = _google_missing(src.get("family") if isinstance(src, dict) else fam)
            if missing:
                reviews.append(missing)
    if fails:
        return c.result("fonts", "FAIL", fails[0] + (f" (and {len(fails) - 1} more)" if len(fails) > 1 else ""),
                        fails + reviews, font_files=files)
    if reviews:
        return c.result("fonts", "REVIEW", reviews[0], reviews)
    if not fams:
        return c.result("fonts", "PASS" if c.template else "REVIEW",
                        "no type families yet" + ("" if c.template else ": onboarding's look step names them"))
    return c.result("fonts", "PASS", f"{len(fams)} famil{'y' if len(fams) == 1 else 'ies'} by reference, each with its "
                                     "licence; no font files in the pack")


def _google_missing(family: str) -> str | None:
    """A note when the installed engine can't load a Google family (so the kit's font gate would fail)."""
    try:
        from slate.engine import bridge  # noqa: PLC0415

        engine = bridge.engine_dir()
    except (ImportError, SlateError):
        return None
    esm = engine / "node_modules" / "@remotion" / "google-fonts" / "dist" / "esm"
    if not esm.is_dir():
        return None
    mod = re.sub(r"[^A-Za-z0-9]", "", family)
    if not (esm / f"{mod}.mjs").is_file():
        return f"{family}: the engine's @remotion/google-fonts has no {family}, so the font gate would fail"
    return None


def _palette(c: _Ctx) -> GateResult:
    pal = c.raw.get("palette") if isinstance(c.raw.get("palette"), dict) else {}
    bad = []

    def walk(tree: dict, prefix: str = "") -> None:
        for k, v in tree.items():
            if not prefix and k in ("roles", "tones", "notes", "note", "source", "sources", "pairs",
                                    "contrast_pairs"):
                continue
            name = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(v, name)
            elif not pack.colour.is_colour(v):
                bad.append(f"{name}: {v!r} isn't a colour")

    walk(pal)
    tokens = pack.palette_tokens(c.view)
    roles = pal.get("roles") if isinstance(pal.get("roles"), dict) else {}
    for role, ref in roles.items():
        if pack.resolve_colour(ref, tokens) is None:
            bad.append(f"role {role}: {ref!r} isn't a token of the palette or a colour")
    if bad:
        return c.result("palette", "FAIL", bad[0] + (f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""), bad)
    if not tokens:
        return c.result("palette", "PASS" if c.template else "REVIEW",
                        "no palette yet" + ("" if c.template else ": onboarding's look step fills it"))
    return c.result("palette", "PASS", f"{len(tokens)} colour tokens, {len(roles)} roles")


def _contrast(c: _Ctx) -> GateResult:
    pairs = pack.contrast_pairs(c.view)
    if not pairs:
        return c.result("contrast", "PASS" if c.template else "REVIEW",
                        "no text colour pairs declared" + ("" if c.template else ": declare them (contrast_pairs) so "
                                                                                 "the accessibility gate can measure "
                                                                                 "them"))
    short = [p for p in pairs if not p["ok"]]
    if short:
        return c.result("contrast", "FAIL", f"{len(short)} of {len(pairs)} text colour pairs fall short: "
                                            f"{short[0]['fg']} on {short[0]['bg']} ({short[0]['role']}): "
                                            f"{short[0].get('problem')}",
                        [f"{p['fg']} on {p['bg']} ({p['role']}): {p.get('problem')}" for p in short], pairs=pairs)
    low = min(pairs, key=lambda p: p["ratio"] / p["needs"])
    return c.result("contrast", "PASS", f"all {len(pairs)} text colour pairs meet their bar (closest: {low['fg']} on "
                                        f"{low['bg']}, {low['ratio']:.2f}:1 for {low['role']} text)", pairs=pairs)


def _svg_problems(path: Path, build: Any) -> tuple[list[str], list[str]]:
    fails, reviews = [], []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return [f"{path.name} isn't a readable SVG ({exc})"], []
    tag = root.tag.split("}")[-1]
    if tag != "svg":
        return [f"{path.name} isn't an SVG (its root is <{tag}>)"], []
    if not root.get("viewBox") and not (root.get("width") and root.get("height")):
        fails.append(f"{path.name} has no viewBox (or width and height)")
    ids, shapes = set(), 0
    for el in root.iter():
        t = el.tag.split("}")[-1]
        if el.get("id"):
            ids.add(el.get("id"))
        if t in ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon"):
            shapes += 1
        if t in ("text", "image", "use"):
            reviews.append(f"{path.name} has a <{t}>, which the logo build can't draw (outline type and inline <use> "
                           "first)")
    if not shapes:
        fails.append(f"{path.name} has no shapes to build")
    key = build.get("keystone") if isinstance(build, dict) else None
    if isinstance(key, str) and key not in ids:
        fails.append(f"{path.name}: the build's keystone {key!r} isn't a shape id in the SVG")
    return fails, reviews


def _logos(c: _Ctx) -> GateResult:
    logos = pack.logos(c.view)
    fails, reviews = [], []
    for lg in logos:
        if lg["inline"]:
            continue
        if not lg["file"]:
            fails.append(f"logo {lg['name']}: no file")
        elif not lg["exists"]:
            fails.append(f"logo {lg['name']}: {lg['file']} isn't in the pack")
        else:
            f, r = _svg_problems(Path(lg["path"]), lg["build"])
            fails += [f"logo {lg['name']}: {x}" for x in f]
            reviews += [f"logo {lg['name']}: {x}" for x in r]
        if not lg["build"]:
            reviews.append(f"logo {lg['name']}: no build spec (the logo build uses its defaults)")
    for mark, ways in pack.colourways(c.view).items():
        if len(ways) < 2:
            only = next(iter(ways))
            reviews.append(f"logo {mark}: only one colourway ({only}). Add the other (ink and white at least) as its "
                           f"own SVG, for example a `{mark}-inverse` logo with `on: inverse`, so builders never "
                           "recolour the mark themselves")
    if fails:
        return c.result("logos", "FAIL", fails[0], fails + reviews)
    if reviews:
        return c.result("logos", "REVIEW", reviews[0], reviews)
    if not logos:
        return c.result("logos", "PASS" if c.template else "REVIEW",
                        "no logos yet" + ("" if c.template else ": add the brand's SVGs with their build specs"))
    return c.result("logos", "PASS", f"{len(logos)} logo{'s' if len(logos) != 1 else ''}, each an SVG the engines can "
                                     "build")


def _motion(c: _Ctx) -> GateResult:
    raw_motion = c.raw.get("motion")
    if isinstance(raw_motion, str):
        return c.result("motion", "FAIL", f"motion points at {raw_motion!r}; every engine reads brand.json as it is, "
                                          "so the law must be inline", hint=f"slate brand onboard {c.name} --step look")
    law = pack.motion(c.view)
    eases = law.get("eases") if isinstance(law.get("eases"), dict) else {}
    if not eases:
        return c.result("motion", "PASS" if c.template else "REVIEW",
                        "no motion law yet" + ("" if c.template else ": the kit's neutral law stands in until "
                                                                      "onboarding's look step writes one"))
    fails, reviews = [], []
    try:
        from slate.motion import eases as law_math  # noqa: PLC0415

        read = law_math.law_from(law)
        fails += [f"the kit reads it with a problem: {p}" for p in read.problems]
    except ImportError:
        reviews.append("slate.motion isn't installed, so the law couldn't be read the kit's way")
    no_lineage = [n for n, e in eases.items() if n != "linear" and not (isinstance(e, dict) and e.get("lineage"))]
    if no_lineage:
        reviews.append(f"no lineage for {', '.join(no_lineage)} (say where each ease comes from)")
    docs = motiondoc.check(c.folder, c.raw)
    stale = [f for f, s in docs.items() if s == "stale"]
    missing = [f for f, s in docs.items() if s == "missing"]
    if stale:
        fails.append(f"{', '.join(stale)} no longer match{'es' if len(stale) == 1 else ''} brand.json's motion "
                     f"(run: slate brand onboard {c.name} --step look)")
    if missing and not c.neutral:
        reviews.append(f"{', '.join(missing)} not written yet (slate brand onboard {c.name} --step look)")
    elif missing and c.neutral and c.name != "_template":
        fails.append(f"{', '.join(missing)} missing from the neutral pack")
    if fails:
        return c.result("motion", "FAIL", fails[0], fails + reviews, docs=docs)
    if reviews:
        return c.result("motion", "REVIEW", reviews[0], reviews, docs=docs)
    return c.result("motion", "PASS", f"{len(eases)} named eases the kit reads cleanly, each with its lineage; "
                                      "motion.json and MOTION.md match", docs=docs)


def _voice(c: _Ctx) -> GateResult:
    try:
        from slate.voice import profile  # noqa: PLC0415
    except ImportError:
        return c.result("voice", "UNCHECKED", "the voice module isn't installed, so the voice profile can't be read")
    try:
        prof = profile.load(c.view)
    except SlateError as exc:
        return c.result("voice", "FAIL", f"the voice isn't a valid voice profile: {exc.message.splitlines()[0]}",
                        exc.message.splitlines()[1:12])
    problems = profile.policy_problems(prof)
    if problems:
        return c.result("voice", "FAIL", f"the voice breaks the voice policy: {problems[0]}", problems)
    if not prof.get("voice_id") and prof.get("provider") != "recording":
        return c.result("voice", "PASS" if c.template else "REVIEW",
                        "no voice chosen yet" + ("" if c.template else ": cast or design one (onboarding's voice "
                                                                       "step)"))
    return c.result("voice", "PASS", f"{prof.get('voice_name') or prof.get('voice_id')} ({prof.get('source')}), a valid "
                                     "voice profile within the voice policy")


def _sound(c: _Ctx) -> GateResult:
    s = c.raw.get("sound") if isinstance(c.raw.get("sound"), dict) else {}
    fails, reviews = [], []
    mats = {m.lower() for m in s.get("materials") or [] if isinstance(m, str)}
    banned = {b.lower() for b in s.get("banned_timbres") or [] if isinstance(b, str)}
    both = sorted(mats & banned)
    if both:
        fails.append(f"{', '.join(both)} is both a material and a banned timbre")
    try:
        from slate.sound.effects import palette as sound_palette  # noqa: PLC0415

        sound_palette.from_brand(c.view)
    except ImportError:
        reviews.append("the sound module isn't installed, so the palette couldn't be merged its way")
    except Exception as exc:  # noqa: BLE001 - a malformed palette shows here, not in a film's mix
        fails.append(f"the sound palette doesn't merge with the starting palette: {exc}")
    if s.get("reference_levels") and not s.get("reference_measured_from"):
        reviews.append("reference_levels without a record of what they were measured from")
    if fails:
        return c.result("sound", "FAIL", fails[0], fails + reviews)
    if reviews:
        return c.result("sound", "REVIEW", reviews[0], reviews)
    if not s.get("key") and not s.get("materials") and not c.template:
        return c.result("sound", "REVIEW", "no sound palette of its own yet: the starting palette (proof 2's taste) "
                                           "stands in until onboarding's sound step")
    return c.result("sound", "PASS", "the sound palette merges with the starting palette" if not c.template else
                    "empty, as a template should be")


def _truth(c: _Ctx) -> GateResult:
    t = c.raw.get("truth") if isinstance(c.raw.get("truth"), dict) else {}
    reviews = []
    ids = {str(s.get("id")) for s in t.get("sources") or [] if isinstance(s, dict)}
    unsourced = [cl["id"] for cl in pack.approved_claims(c.view) if cl.get("source") and
                 str(cl["source"]).split(":")[0] not in ids]
    if unsourced:
        reviews.append(f"claims citing a source that isn't listed: {', '.join(unsourced)}")
    unpinned = [str(s.get("id")) for s in t.get("sources") or [] if isinstance(s, dict) and not s.get("pin")]
    if unpinned:
        reviews.append(f"sources not pinned: {', '.join(unpinned)}")
    own = pack.ownership(c.view)
    auth = pack.claim_authority(c.view)
    if own.get("kind") == "client" and auth and not auth.get("organisation"):
        reviews.append("this isn't the owner's brand, so its claim authority is a named person at that organisation: "
                       "add their organisation")
    if not isinstance(t.get("banned_words"), list) and not c.template:
        reviews.append("no banned-word list stated (an empty list says there are none)")
    if reviews:
        return c.result("truth", "REVIEW", reviews[0], reviews)
    return c.result("truth", "PASS", f"{len(ids)} source(s), {len(pack.approved_claims(c.view))} approved claim(s), "
                                     f"{len(pack.banned_words(c.view) or [])} banned word(s)")


def _references(c: _Ctx) -> GateResult:
    refs = pack.references(c.view)
    broken = [f"{k}: {st['reason']}" for k, st in refs.items() if st["state"] in ("changed", "data_changed",
                                                                                   "incomplete")]
    absent = [f"{k}: {st['reason']}" for k, st in refs.items() if st["approved"] and not st["file_present"]]
    approved = [k for k, st in refs.items() if st["approved"]]
    if broken:
        return c.result("references", "FAIL", f"approved references that no longer hold: {broken[0]}", broken + absent,
                        states={k: st["state"] for k, st in refs.items()})
    if absent:
        return c.result("references", "REVIEW", absent[0], absent, states={k: st["state"] for k, st in refs.items()})
    if c.neutral:
        return c.result("references", "PASS", "placeholders, unapproved by design (a neutral pack)",
                        states={k: st["state"] for k, st in refs.items()})
    status = "onboarded" if len(approved) == 4 else "needs onboarding"
    return c.result("references", "PASS", f"{len(approved)} of 4 references approved ({status}), none changed since "
                                          "its approval", states={k: st["state"] for k, st in refs.items()})


def _uploads(c: _Ctx) -> GateResult:
    top = c.raw.get("allow_generator_uploads")
    g = c.raw.get("grounding") if isinstance(c.raw.get("grounding"), dict) else {}
    nested = g.get("uploads_allowed")
    if isinstance(top, bool) and isinstance(nested, bool) and top != nested:
        return c.result("uploads", "FAIL", "allow_generator_uploads and grounding.uploads_allowed disagree, so no "
                                           "brand material goes to generators until they say the same")
    allowed = pack.uploads_allowed(c.raw)
    return c.result("uploads", "PASS", "brand material may go to generators (the brand owner allows it)" if allowed
                    else "brand material stays out of generators (uploads not allowed)")


def _skills(c: _Ctx) -> GateResult:
    sk = pack.skills(c.view)
    bad = [f"skills/{Path(s['folder']).name}: " + ("no SKILL.md" if not s["has_skill_md"] else
                                                     "SKILL.md needs a name and a description in its frontmatter")
           for s in sk if not s["has_skill_md"] or not s.get("description")]
    if bad:
        return c.result("skills", "FAIL", bad[0], bad)
    return c.result("skills", "PASS", f"{len(sk)} brand skill{'s' if len(sk) != 1 else ''}" if sk else
                    "no brand skills (optional)")


CHECKS = (_schema, _location, _fonts, _palette, _contrast, _logos, _motion, _voice, _sound, _truth, _references,
          _uploads, _skills)


def validate(ref: str | os.PathLike) -> list[GateResult]:
    """Every check for one pack."""
    c = _Ctx(pack.find(ref))
    out = []
    for check in CHECKS:
        try:
            out.append(check(c))
        except SlateError as exc:
            out.append(c.result(check.__name__.strip("_"), "UNCHECKED", f"couldn't check: {exc.message}"))
    return out


def validate_all() -> dict[str, list[GateResult]]:
    return {row["name"] if not row["shadowed"] else row["dir"]: validate(row["dir"]) for row in pack.list_packs()}


def onboarding_items(ref: str) -> dict[str, list[dict]]:
    """Each step's checklist for a pack (read-only), for `slate brand show`."""
    folder = pack.find(ref)
    raw = pack.read_raw(folder)
    return {step: [vars(i) for i in onboard.items_for(step, folder, raw)] for step in onboard.STEPS}
