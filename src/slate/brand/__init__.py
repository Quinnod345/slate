"""Brand packs and onboarding (docs/ARCHITECTURE.md §7, docs/PLAN.md §4).

A brand is data: `brand.json` plus its assets, in the private store (`~/code/slate-brands`) or, for Slate's own
neutral packs, in the repo (`brands/_template`, `brands/demo`). Every brand is onboarded once, before any script, into
four approved references (truth, look and motion, voice, sound); later films are checked against them.

- `slate.brand.pack`: find, load and read a pack (the interface other modules call: find, load, references_approved,
  missing_references, banned_words, approved_claims, motion, palette, contrast_pairs).
- `slate.brand.onboard`: the four onboarding steps. `slate.brand.approve`: recording approvals.
- `slate.brand.create`: `slate brand init`. `slate.brand.validate`: `slate brand validate`.
- `slate.brand.sources`: reading a brand's own code, website and guidelines in place. `slate.brand.easing`: CSS
  easing tokens and SwiftUI springs as named eases. `slate.brand.colour`: colours and contrast.
- `slate.brand.cli`: the `slate brand` commands.
"""
