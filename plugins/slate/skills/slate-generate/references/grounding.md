# Grounding every generation in the brand

Ungrounded moodboards on the first film drifted into generic grey glass. From then on, every generation (moodboards,
keyframes, plates, video) was grounded in the brand's real design system, and the drift stopped.

## The method

1. **Fetch the brand's kit.** The brand pack names its grounding kits (`grounding` in brand.json: an ElevenLabs brand
   kit id, a Higgsfield brand kit id) and its own reference assets.
2. **Wire its reference assets into the generation**: the logos, the palette, the typography rules, reference frames from
   the brand's website and product, when the brand allows uploads.
3. **Spell the brand's tokens and rules into the prompt**: its palette by name and value, its light (a product world, an
   inverse brand world), what's banned (glows, glass, stock grids, the brand's never-list).
4. **Grade toward the palette**: generated plates are graded toward the brand's palette tokens after conforming.

## A brand's two worlds

Most brands have a light product world (the product's own page and surfaces) and a darker or more saturated brand world
(for statements). Tell the generator which world a shot lives in, and ground it in that world's references.

## When the brand has no kit

A brand with no design system can bootstrap one during onboarding (a brand-kit tool can propose a palette, marks and
type), and the owner approves it before anything is generated from it (slate-brand).

## Checking a grounded take

Judge it against the brand's approved look reference, side by side (judging-takes.md): palette, light, texture, the
absence of anything banned. A take that drifts off-look is rejected even if it's beautiful.
