# Onboarding a brand

Onboarding happens once per brand, before its first script, and produces four approved references. It usually takes a
few sittings of the owner's (or the brand's approver's) time; the rest is research and building.

## 1. Research

Write one research document for the brand, cited throughout:
- **a source table**: every source with a short code, where it lives and its date (the website's pages, the live
  product, the product's code at a pinned commit, the guidelines, prior films, the brand's own people). Sources read in
  place are marked "not copied";
- **every claim with its source code** (and a URL, a file path, or a commit and line behind the code);
- **the brand's story**: what it does, who it's for, its method or standard, its value propositions, its proof points;
- **the brand system**: logos, fonts, colour tokens by source, UI patterns, icons, voice and naming, motion tokens;
- **originals worth having**: vector logos, project files, earlier recordings, preferred over anything traced from a
  video;
- **conflicts and open questions**: where sources disagree (the guidelines against the shipped product, say) and what
  needs a ruling.

Read the web with fetch and search tools; a page that needs a browser goes through Aside. Company code is read in place.

## 2. Truth

The claim authority, the sources of truth, the approved claims, what the brand never claims, its banned words and its
sample data (truth-pack.md).

## 3. Look and motion

The brand's own components rebuilt for film, reference films analysed, the product's own motion values measured,
moodboards grounded in the brand's kit, and a moving test (look-and-motion.md, reference-analysis.md).

## 4. Voice

Cast or design the brand's voice, direct it, and get one take approved by ear, with its model, settings and seed
(voice-reference.md; slate-voice does the work).

## 5. Sound

The brand's own palette (from its world, starting from the owner's starting palette) and a 10 to 20 second proof with
levels measured against the voice (sound-reference.md; slate-sound does the work).

## 6. The rest of the pack

Fonts and type roles (fonts.md, type.md), logos with build specs and any motif (logo-and-motif.md), grounding kits and
whether uploads are allowed, and the pack's data (pack.md).

## 7. Approvals

Each reference is approved by the right person and recorded with its SHA-256 (`slate brand approve --help`). Then
`slate brand validate <brand>` shows all four approved, and the brand is ready for films, including autonomous ones.
An approval, or a change to the look, leaves the brand's films as they were: each stage records only the parts of
brand.json it reads (the script its truth and duration scale, the timing its eases), so only a change to those makes
a film's script or timing stale.

## Bootstrapping a brand with no design system

A brand with no design system can bootstrap its pack with a brand-kit tool (a palette, SVG marks, type, a brandbook).
The owner approves the proposal before anything is built from it; after that it's the brand's system like any other.

## A light onboarding

When a first film is due soon, onboarding can be light: a truth reference with the few claims the film needs, a look
test built from what the brand has, a voice take and a short sound proof, each still approved. The film can't use
anything the light onboarding didn't approve, and onboarding is finished before the brand's next film.

## Budget

Onboarding has its own default budget (about 5,000 ElevenLabs credits for voice design, and $15 to $20 of generation for
look tests); the sound reference is made locally.
