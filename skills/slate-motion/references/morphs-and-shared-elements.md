# Morphs and shared elements

A shared element persists across a scene change and keeps its identity while the world changes around it. It's how
the product itself carries objects between views, and it's the film's strongest grammar: the eye stays on one thing
while everything else changes.

## Shared elements

- **Use hand-offs the way the product carries objects between views**: a mark landing as the nav logo, a value and its
  chart carried from one screen to the next. Mirror the product's own view transitions.
- **A carried object keeps its label**: a caption travels with the value it describes across the boundary.
- **Carry by the product's own structure**: a checklist row lifts out and opens into its step (its header as the shared
  element), new steps are dealt in from one side while old ones leave the other, and one home card is the place the act
  keeps returning to.
- **Carries follow the product's own navigation order** (scroll down to the next section) rather than inventing an
  in-place transform.
- **Draw a carried element over its destination for the whole carry**, and let the destination take it over on the
  landing frame. Switching its layer mid-carry makes it slip behind, then jump in front.
- **Hand an object between components only at rest**, or on a morph's first moving frame, with one shared count
  function and one position for each label. A mid-count hand-over made a numeral jump three values and a label jump
  4 px.
- **Never duplicate an object to animate it** (a copy slides away while the original stays): move the one object.

## Morphs

- **A morph's first frame equals its source exactly**, and every property blends through it: no mid-flight flips of
  glyphs, line breaks, weight, border, font or sharpness. A chip that swapped font on its first frame popped.
- **Never crossfade one version of a card into another**: it double-exposes headers, figures and charts for several
  frames. Morph the shared elements so nothing is drawn twice.
- **Route shared elements around each other** (one goes down, then left, passing under another) so nothing travels
  through anything; the destination's own new parts arrive only once the shared parts have all but landed.
- **When many pieces morph into shapes**, their union becomes the destination shape on arrival, and the solid shape takes
  over on that frame. Never fade in a second set at the destination while the first is still moving.
- **When a small card grows into a large one**, its position leads its growth, so it grows out of where it is.
- **A container that grows open** (a chat out of a button) reveals content that sits still in its own layer, clipped
  to the growing paper. Carried inside the growing card, its type re-rasterises at a new sub-pixel phase every frame
  and shimmers.
- **Animate corner radii before a split or merge**: corners that snapped from 28 px to 8 px on a split's first frame
  popped.
- **When segments merge into one shape** (a donut's groups into a ring), draw it as one continuous path once merged, so
  no seams show.
- **A container and its contents share timing.** A card that shortened on a spring while its contents travelled on
  another ease let its edge run ahead and left a chart's baseline on the bare page.
- **Open detail below an anchor without moving the anchor**, so everything attached to it stays put. Open a panel in
  its final place; don't open it centred and then glide it aside.
- **Don't drop a large object into streaming text**; carry it out beside the text so the text stays intact.

## Path morphs: the signature line

- A brand's signature line (one stroke in the accent colour at a fixed weight) can carry a whole film through its forms:
  a progress rule, a data curve, a then-to-now mark, a landscape ridge, the underline under the last line. It never
  changes colour, so the eye can follow it through every scene.
- Its weight changes in exactly one place, the reveal, and there it copies the product (drawing heavy and settling to a
  hairline).
- When the script asks for several connecting lines, draw one thread through a node on each item and on into the
  target: one signature line.
- An object that becomes a line arrives moving along that line's direction, and the line draws out of it as it shrinks
  into the line's start, so dot and line are one object.
- **Build:** shapes with equal vertex counts (resample every target to the same count), the first vertex left-most, no
  reversed winding. Path keys use the rest-to-rest ease for moving shapes and the state ease for committing state;
  draw-ons use a trim reveal on the entrance ease.

## Word and glyph morphs

Set the word as shapes (one group per glyph) and morph them into the target icon's paths on the state ease at the quick
duration, and back. Pair it with the make-room move when a new word pushes the block.

## Counting

- **Count a value once.** Never re-count from zero after it's been shown: shown, reset and counted again reads as a
  glitch. Re-count only when the value really changes, from its old value.
- Count-ups run on the entrance ease over the broad duration, with tabular figures and the product's real range; each
  figure holds at least three frames.
