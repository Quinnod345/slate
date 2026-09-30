# Fonts

Brand fonts are licensed per brand. Slate never ships or commits a font file; the pack references each face, and the
renderer loads it explicitly, failing loudly when it can't.

## Declaring them

`brand.json` → `type.families`: each role's family, weights and styles, its **source**, and its **licence**:
- `installed`: a face installed on the machine (by its name);
- `google`: a Google Fonts family (loaded through Remotion's font loader);
- a font-kit URL: the brand's web font kit;
- a file URL, only for faces the brand licenses for this use.

For After Effects, each role also names its PostScript name, because AE sets fonts by that name.

## The traps

- **Headless Chrome can't see fonts installed locally from a web font kit.** Load every face explicitly; don't rely on
  what the machine has.
- **A brand's web font kit may serve only its allowed domains**, and loads online during renders; in After Effects the
  same fonts must be activated or installed separately.
- **Never fetch a brand's licensed fonts from its web kit into a third-party sandbox** (a cloud renderer, a generator):
  the licence covers specific uses.
- A missing weight silently falls back to another face in most tools. Slate's font gate fails the render instead.

## The font gate

The kit builds a font plan from the pack and checks each face in the renderer before any frame is drawn; a missing or
substituted face fails the render (and the hand-in check's fonts gate) with the face named. `slate doctor` checks the
fonts After Effects needs.

## When a brand's fonts can't be used

Say so, and pick a licensed stand-in with the owner (an open font close to the brand's), recorded in the pack as a
stand-in with its reason. Never quietly substitute.
