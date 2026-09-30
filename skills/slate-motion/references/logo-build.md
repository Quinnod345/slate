# Building a logo, and the sting

A brand's logo arriving is usually a film's first signature moment and its bookend. The kit builds it from the brand's
own SVG and a build spec, so the construction is the brand's own geometry, not a template.

## A logo build that worked

1. **Construction guides and vertex squares** drawn on the mark's own grid (dashed lines from the shapes' edges).
2. **An outline draw** of the mark.
3. **A left-to-right fill**.
4. **A line writing the name**.
5. **A held breath**: the keystone element (an accent dot, a square) hangs over its empty slot.
6. **The keystone drops into place** with a damped jolt of a few pixels (the one allowed impact overshoot), on the word.
7. **Relief rings with echoes and a contact shadow** answer the impact.
8. **Every mark settles** before the next contract frame, so nothing is still fading on the cut.

## The kit

- `LogoBuild` takes the parsed logo (paths, per-glyph boxes from the path bounds, a designated keystone, construction
  guides from the edges, the keystone's colour from the artwork) and a build spec (guides, vertices, order, keystone,
  timing). `settleBy` makes every mark finish before a given time.
- `LogoSting` is the short form for endings and stings; `stingLength()` says how long it runs.
- `logoEvents()` lists the build's beats for the sound: each construction line, the fill, the keystone's landing.
- The logo's SVG and build spec live in the brand pack (slate-brand, logo-and-motif.md).

## Rules

- **Never rotate a mark that has a fixed orientation**, even in a portal zoom.
- **Zero-length dashed strokes still render a dot**: guard them, or the build shows specks before it starts.
- **Bookends reuse the same kit component** as the opening (the same logo build with the closing's timing), never a
  redraw, so they match exactly.
- **When a shot hands from an act's own drawing to the logo build mid-shot**, the component's start state must match the
  drawing exactly (weight, caps, dot size and position); check the switch frame pixel by pixel.
- **The sonic logo mirrors the build**: each construction line clicks in, the fill is a soft swell, the keystone lands on
  one hollow knock with one small note; the ending reverses it and lands on the same knock (slate-sound).

## Checking it

Render the build's key frames as stills at full size: the first guide, the fill's midpoint, the held breath, the
landing frame, and the last frame before the contract. The landing frame should be the sound's contact frame, and the
last frame should be the contract's rest state exactly.
