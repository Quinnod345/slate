# The brand pack

A brand pack is a folder: `brand.json` plus assets (logo SVGs, the approved reference files, a MOTION.md). It's data:
Slate's code knows no brand, and everything brand-specific lives here.

## Where packs live

- Slate's repository ships only `brands/_template` and the neutral `brands/demo`.
- Real brands live in a separate brand store on the owner's machine (never pushed). Company packs stay on the owner's
  personal machine only, because they're company material; a pack the owner owns outright can live in a private
  repository.
- `slate brand init <name>` makes a new pack from the template; `slate brand validate <name>` checks it.

## brand.json, key by key

| Key | What it holds | Read by |
|---|---|---|
| `name` | the pack's name | everything |
| `palette` | tokens (nested or flat); `roles` naming page, surface, ink, accent, inverse by token; `tones` for ok, warn, risk | the kit, the contrast check, generation grounding |
| `type` | `families` with role, family, weights, `source`, `licence` and a PostScript name per role; an optional `scale` | the kit's font plan and gate, After Effects |
| `logos` | each logo's SVG path and its `build` spec (guides, vertices, order, keystone, timing) | the kit's logo build, After Effects |
| `motif` | the motif mark's parts, groups, sizes, curves, ratio, and `gestures` | the kit's motif rig |
| `look` | the look's id (from the looks library, or an AE look) and overrides | the kit, After Effects |
| `motion` | `eases` (with lineage), `durations`, `staggers`, `stagger_cap`, `exit_factor`, `roles`, `overshoot` | the kit, the motion gate, the script's read-time gate, After Effects |
| `voice` | the voice profile: provider, source, voice id, model, settings, seed, lexicon, tag policy, clone fields, pace, clean | the voice stages |
| `sound` | `palette`, `materials`, `banned_timbres`, `key`, `tempo`, `instrumentation`, `motif`, `reference_levels` | the score, the mix, the sound gates |
| `truth` | `claim_authority`, `sources`, `approved_claims` (id, text, numbers), `banned_words`, `never_claim`, `sample_data` | the script gates, the claims and banned-word gates |
| `grounding` | brand-kit ids for ElevenLabs and Higgsfield; `uploads_allowed` (default false) | generation |
| `references` | `truth`, `look_motion`, `voice`, `sound`: each `{file, sha256, approved_by, approved_at}` or null | the modes (autonomous needs all four), the gates, `slate status` |

Every reader is forgiving and says what it had to assume (the kit's problems list, `slate brand validate`), so a young
pack still renders a placeholder; nothing brand-specific is ever guessed silently.

## Approvals

`references` records each approval: who approved it, when, and the SHA-256 of the reference file. A changed file is no
longer approved. Autonomous films refuse a brand until all four are approved; supervised films carry a look test in
their pre-production sitting while the look isn't approved.

## What never goes in a pack in Slate's repository

A real brand's colours, names, copy, voice ids, logos, fonts, sample data or banned words. Those are exactly what the
brand store is for.
