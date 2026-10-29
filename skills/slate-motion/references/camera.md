# Camera

The camera is the viewer's guide. Choose its movement for the current brief: a reveal, a carry, sustained energy or
stillness. The gentle product-film recipe below is a starting point. Exact contract frames and truthful motion
evidence remain technical requirements, whatever camera style the film chooses.

## When it moves and when it rests

- **A slow drift keeps the world alive.** A drift that starts before the shot and ends after it can be linear, because
  its start and stop are never seen.
- **The camera rests** for UI state cuts, and wherever geometry must be screen-exact: act contracts, hand-overs between
  screen space and a card, rest frames that must be pixel-exact.
- **One camera move per act** was the restrained starting recipe. Bringing a card forward can give it a closer read;
  another brief may need several camera moves or continuous travel.
- **Fewer, better moves.** Don't pan out to one thing and back when one draw-back can show both; don't push onto a page
  and straight back out.
- **The camera can step down a list** as the voice reaches each line.
- **Overlays on a plate take the plate's own transform** (labels, marks, UI over footage), its slow push included, so
  they never drift against it. A linear push starts and stops off screen.
- **A move bounded by a later beat shrinks to fit** rather than overrunning it after a re-voice, and keyed drift points
  stay at least 0.8 s apart.

## How it moves

- A rest-to-rest ease at the camera duration is a useful starting point. Long travel can also use a supported even
  curve, a deliberate continuous drift or a faster named treatment. Check its applicable speed policy and blur.
- **Give detailed content a readable interval.** Earlier small type strobed during travel; letting the camera rest
  before the station spoke fixed that film. Another treatment can keep moving when its actual text stays readable.
- **Inspect large fast pull-backs carefully.** A previous 4,000 px truck with a big zoom-out in under a second moved
  267 px a frame, read as a whip, popped objects in from nothing and landed on unreadable details. Those defects need
  repair; the measurement does not ban every fast pull-back. A declared whip beyond the carry limits goes to review.
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
