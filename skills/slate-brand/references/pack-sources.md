# Building a pack from what the brand has

Most brands arrive with one of three things: a website, a guidelines document, or an app. Each gives different parts of
the pack; most brands give some of each.

## From a website

- **Read it with fetch and search tools**; save the pages you rely on with their retrieval dates. A page that only
  renders in a browser goes through Aside, never another browser.
- **Tokens**: the site's CSS custom properties and stylesheets give colour tokens, radii, shadows, spacing and type
  (families, weights, sizes). Record each token with its source file.
- **Components**: cards, buttons, chips, charts, section styles. These become the kit's components, rebuilt at film scale
  (never imported code).
- **Motion**: the site's easing tokens and transitions (a scroll easing set is a different vocabulary from a film's law:
  measure before reusing).
- **Imagery and voice**: photography style, illustration, copy tone.
- **Claims**: what the site says, with dates, for the truth reference.

## From a guidelines PDF

- Read it in place; cite pages.
- **Colours** (with their official values), **type** (families, weights, hierarchy), **logo** rules (clear space,
  minimum size, colourways, what never to do), **voice** (tone, words to avoid), imagery.
- Guidelines and the shipped product sometimes disagree (type weights, a colour's value): the film follows the product,
  and the conflict goes in the research document for a ruling.

## From an app

- **Screens**: every screen the films may show, captured on a demo account (`slate capture ios` or `slate capture web`)
  or studied from the code.
- **Code, read in place** at a pinned commit, cited `path:line`: the exact copy, the states, the real distributions and
  ranges behind any data it shows. Never copied anywhere.
- **Motion values**: CSS easing tokens, or SwiftUI spring response and damping; the product's own loaders and
  transitions (a motif's timing often comes from its loader).
- **Its character or agent**, if it has one: a capability brief of what it really does (slate-story, truth.md).

## From nothing

A brand with no design system can bootstrap one: a brand-kit tool proposes a palette, marks, type and a brandbook; the
owner approves it; then it's the brand's system. Generation for brand assets follows slate-generate's rules (grounded,
judged, never ugly).

## Grounding kits

If the brand's material is allowed to go to generators (`grounding.uploads_allowed`), set up its grounding kits (an
ElevenLabs brand kit, a Higgsfield brand kit) with its logos, palettes, type rules, dos and don'ts, and reference frames,
and record their ids in the pack (`grounding`). Every generation for the brand wires them in (slate-generate,
grounding.md).
