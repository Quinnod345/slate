# Remotion and headless Chrome: rendering traps

Remotion renders each frame in headless Chrome, and Slate's renderer draws each frame 16 times across the shutter and
averages them. Both are exact and unforgiving. These traps each cost a re-render on the first films.

## Smooth motion, crisp rest (raster stability)

Headless Chrome snaps text, and boxes placed by `left`/`top`, to whole pixels as it rasterizes. Under slow motion (a
drift, the tail of any ease) a line of type then steps a pixel at a time while the shapes around it glide: the film
looks jittery and shaky. The kit's answer changes no design, timing, layout or stacking:
- **`Card` and `Camera` carry raster hints when told they move**: pass `atRest={!poseMoving(pose, t)}`. The layer is
  hinted while it moves and drops the hint the frame it stops.
- **An element moved by its own transform** gets a key that changes each whole pixel of the move and once at rest, plus
  the kit's mover hint; `PerChar`, `PerWord` and `Arrive` do this for you.
- **Any other layer that scales by more than about 5%**: `key={rasterKey(scale)}` and `motionHint(moving)`.
- **Move with transforms, not `left`/`top`/`width`**, whenever something moves slowly or settles.
- **Every raster switch can tick the text up to half a pixel.** Don't switch a hint on or off in place on something
  visible and still; a layer whose last rebuild falls just before it settles can keep one raster through its rest.
  Prefer a few percent of softness to a visible jump.
- **Keys must be unique among siblings**: two siblings sharing a raster key were drawn wrong (a label appeared twice).
- **A half-scale, one-sample preview hides this.** One act was approved from such a preview, and its 16-sample final
  shook: type stepped a pixel at a time under a slow push. Before a final, render short 16-sample windows of the risky
  moves (slow pushes, the tails of long eases, text rising on its own) through the real render path, and check them at
  full size.

## Depth sorting and stacking

- **Several parallel 3D planes stacked close together make Chrome's depth sorting race**: a card's face dropped for two
  frames in a preview while stills of the same frames were clean. Avoid extra parallel planes, and scan previews, not
  just stills.
- **Use 3D for one object at a time** and keep everything else flat, drawn in document order: planes crossing inside one
  3D context swapped stacking mid-move, cut words in half and ghosted cards through each other.
- **A card tilting through another element's plane gets sliced** by it for a few frames: keep moving cards in front of
  the planes they pass.
- **Overlapping cards keep one depth order** for as long as they overlap; objects that move against each other live in
  separate cameras, so their stacking is plain document order and can't flip.
- **Declare the stacking explicitly**: a back-to-front list of layers per scene, each its own context.
- **Opacity or a filter on a 3D group flattens it**, and a translucent card shows its thickness layers through its face:
  put opacity and blur on leaves, and fade a card as one flat sheet (the kit's `FadeCard`).

## UI belongs to its card

Print a button (or any control) on the card that owns it, so it moves and clips with the card. Never float it in front
at a fixed position: a floating button was once left behind on the bare page when its card moved.

## Geometry that shouldn't be there

- **Remove geometry that's never meant to be seen**: a block hidden under a flat sheet only ever showed as a panel
  popping in when the sheet lifted, and it hid the sheet's own shadow.
- **Zero-length dashed lines still render a dot.**

## Determinism

- The GPU renderer isn't bit-exact run to run (about one level): call a frame "changed" only when it differs from both
  of two baseline renders. Software GL is bit-exact.
- A lookup that throws at module load breaks every composition in the bundle; the kit's never do.
- A half-written act can break the bundle for everyone: the hand-in check's type check and module load catch it.

## Load

Under heavy machine load, raster tiles can go missing (grey) for a frame. Render at moderate concurrency through the
queue, one heavy job at a time, and let the glitch scan catch what slips through.
