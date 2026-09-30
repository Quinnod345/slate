# Camera

The camera is the viewer's guide. It moves to reveal and to carry, it rests when geometry must be exact, and it never
moves just to fill time.

## When it moves and when it rests

- **A slow drift keeps the world alive.** A drift that starts before the shot and ends after it can be linear, because
  its start and stop are never seen.
- **The camera rests** for UI state cuts, and wherever geometry must be screen-exact: act contracts, hand-overs between
  screen space and a card, rest frames that must be pixel-exact.
- **One camera move per act** is plenty. Prefer bringing a card forward, at the product's own size, over pushing the
  camera in: whatever needs a closer read comes forward off its surface.
- **Fewer, better moves.** Don't pan out to one thing and back when one draw-back can show both; don't push onto a page
  and straight back out.
- **The camera can step down a list** as the voice reaches each line.

## How it moves

- Camera moves run on the brand's rest-to-rest ease, at the camera duration or longer.
- **Anything that travels a long way travels soft and slow**, and comes into focus as it arrives.
- **Land detailed content only once the camera has all but stopped.** Small type rushing past a moving camera strobes
  and trips the glitch scan. The rhythm that works: the camera travels, comes to rest, then the station speaks. Measure
  a travel's screen speed frame by frame, and keep its frame-to-frame change small.
- **Never a giant fast pull-back as a transition**: a 4,000 px truck with a big zoom-out in under a second moved 267 px
  a frame, read as a whip, popped objects in from nothing and landed on things too small to read. Fold or carry from
  where the camera is.
- **Zoom in log space**, and prefer a 2D camera over poses: huge `translateZ` zooms rasterize badly.
- A world authored at film scale and screen-aligned at rest keeps rest frames pixel-exact.

## Depth of field

- 3D camera moves keep depth of field on, with focus keyed to the rest-to-rest ease, and an aperture that blurs
  off-focus UI past legibility (so it doesn't compete).
- A rack focus between focal elements is a lens effect, allowed; it never counts as a crossfade.
- For a weighty line, hold everything still except a slow focus pull that leaves one element standing alone.

## Framing

- **Close-ups never crop text mid-line.**
- Keep the focal object inside the title-safe area and the platform's safe zones (safe-areas.md).
- When the frame will be derived to another aspect, keep the action where a re-staged layout can still hold it
  (slate-finish, derived-films.md).

## The kit

`Camera` (a look-at camera, dolly in z) for 3D; `WorldLayer` (2D, identity at rest) for flat worlds; `camAt`, `lerpCam`,
`cam2DAt`, `drift2D` for paths. Draw objects that move against each other in separate cameras, so their stacking is plain
document order and can't flip.
