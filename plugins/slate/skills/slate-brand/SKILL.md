---
name: slate-brand
description: Onboard a brand into Slate once, before its first film, and keep its brand pack. Covers the four approved references (truth with a named claim authority, look and motion from a moving test, voice, sound), building a pack from the brand's website, a guidelines PDF or its app, measuring a motion law from reference films and the product's own motion tokens, fonts and type by reference with their licences, logos, a motif mark and the pack's data. Use when a film names a brand that has no approved Slate pack, when a brand's pack needs building, fixing or re-approving, or when someone asks why a brand needs onboarding.
---

# slate-brand

Every brand gets its own look, its own motion, its own voice and its own sound, and Slate learns them once, before the
brand's first script. That's why onboarding exists: the first film looked right only after its look was rebuilt from
the brand's own design system (after four generic looks were rejected), with a motion law measured from reference films
and the product's own motion tokens. Every brand gets the same treatment.

A brand is data: `brand.json` plus its assets, in a brand pack. The core ships a neutral `demo` pack and a `_template`;
real brands live in a separate brand store on the owner's machine, and company packs never enter Slate's repository.

## When to onboard

The front door (the slate skill) sends a film here when `slate brand validate <brand>` finds no pack, or a pack whose
four references aren't all approved. No script is written for a brand until its truth reference exists, and autonomous
films refuse any brand without all four references approved. The neutral `demo` pack is the exception: it exists for
trials, tests and demos.

## The four references

| Reference | What it is | Approved when |
|---|---|---|
| **Truth** | the brand's sources of truth, a named claim authority (who rules on claims for this brand), an approved-claims list, what it never claims, its banned words and its sample data | the claim authority approves the claims list |
| **Look and motion** | kit components rebuilt from the brand's design language, a brand MOTION.md and motion law, a kit review sheet, and a **moving** 10 to 15 second look-and-motion test with sound | the owner approves the moving test (stills don't count), after at least two moodboard directions grounded in the brand's kit |
| **Voice** | an approved take of the brand's voice, with its model, settings and seed | the owner approves it by ear |
| **Sound** | a 10 to 20 second sound reference proof, with its levels measured against the voice | the owner approves it by ear |

Each approval is recorded in `brand.json`: who approved it, when, and the SHA-256 of the reference. Later films are
checked against these references. For a brand the owner doesn't own, the claim authority (and often the approver for
brand use) is a named person at that company.

## The steps

```
slate brand status <brand>        # onboarded, or "needs onboarding" and the next step (--json for agents)
slate brand init <brand>          # a new pack from the template (brands/_template/README.md explains every key)
slate brand onboard <brand>       # the onboarding checklist: what exists, what's missing, what's next, what it's spent
slate analyze <reference-film>    # transcript, scenes, OCR and measured eases from each reference film
slate brand validate <brand>      # the pack against its schema, and which references are approved
slate brand approve --help        # records one reference's approval: who, when, and its SHA-256
```

Onboarding spends a little (Voice Design, a look test): it bills against the brand's own onboarding budget (5,000
ElevenLabs credits and $20 of Higgsfield by default), shown on `slate brand onboard`'s spend line, separate from any
film's.

1. **Research**: sources, claims and the brand system, every claim cited (see [onboarding](references/onboarding.md)).
2. **Truth**: the truth reference (see [truth pack](references/truth-pack.md)).
3. **Look and motion**: the brand's own components, reference films measured, the product's motion values, moodboards
   grounded in the brand's kit, then the moving test (see [look and motion](references/look-and-motion.md) and
   [reference analysis](references/reference-analysis.md)).
4. **Voice**: cast or design, directed and approved by ear (slate-voice; see [voice reference](references/voice-reference.md)).
5. **Sound**: the brand's own palette from its world, and a short proof with levels against the voice (slate-sound; see
   [sound reference](references/sound-reference.md)).
6. **Fonts, type, logos, motif** (see [fonts](references/fonts.md), [type](references/type.md),
   [logo and motif](references/logo-and-motif.md)).
7. **Approve each reference** with the owner (or the brand's approver), and validate the pack.

## Building a pack from what the brand has

- **A website**: its CSS tokens, components, type and imagery, read with fetch tools (pages that need a browser go
  through Aside).
- **A guidelines PDF**: colours, type, logo rules, voice, dos and don'ts.
- **An app**: its screens, its code (read in place, never copied), and its own motion values (CSS easing tokens, or
  SwiftUI spring response and damping).
- **Nothing at all**: a brand-kit tool can propose a palette, marks, type and a brandbook for the owner to approve first.

See [pack sources](references/pack-sources.md). The pack's keys, and what each module reads from them:
[pack](references/pack.md).

## Rules

- Company source code is read in place, never copied into Slate, a prompt or a pack.
- Brand material goes to a generator only when the brand's owner allows it (`grounding.uploads_allowed`).
- Fonts by reference only (installed name, kit URL or Google Fonts), with the licence noted; never font files in a
  repository.
- Company brand packs stay on the owner's personal machine, in the brand store, never pushed.
- A brand's approved references change only on purpose, with a new approval; notes on them are perfecting notes.

## References

- [onboarding](references/onboarding.md): the whole onboarding, the research document, bootstrapping a pack.
- [truth-pack](references/truth-pack.md): the truth reference, the claim authority, approved claims.
- [look-and-motion](references/look-and-motion.md): look development and the brand's motion law.
- [reference-analysis](references/reference-analysis.md): analysing reference films and measuring their motion.
- [voice-reference](references/voice-reference.md): the brand's voice and its profile.
- [sound-reference](references/sound-reference.md): the brand's palette and its sound proof.
- [fonts](references/fonts.md): sourcing and licensing fonts, the font gate.
- [type](references/type.md): the brand's type roles and hierarchy in film.
- [logo-and-motif](references/logo-and-motif.md): logos, build specs and motif marks.
- [pack-sources](references/pack-sources.md): building from a website, a guidelines PDF or an app.
- [pack](references/pack.md): brand.json key by key, and where packs live.
