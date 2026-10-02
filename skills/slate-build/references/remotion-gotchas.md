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
- **A box that grows or settles slowly is sized by a transform too**: a tile growing out of a line, a fill rising to its
  level, a segment, an axis, ticks, dividers, drop lines, row backgrounds through a fold, an edge shadow. Lay it out at a
  whole-pixel size and draw it to its exact place and size with `translate` and `scale`, counter-scaling its content so
  type keeps its size. Laid out by height or width, such a box settled a pixel at a time (a 15% tile and its number
  stepped −1.1, −1.0, −1.0 px as they came to rest).
- **Every raster switch can tick the text up to half a pixel.** Don't switch a hint on or off in place on something
  visible and still; a layer whose last rebuild falls just before it settles can keep one raster through its rest.
  Prefer a few percent of softness to a visible jump.
- **Keys must be named and unique among siblings** (Determinism, below).
- **A half-scale, one-sample preview hides this.** One act was approved from such a preview, and its 16-sample final
  shook: type stepped a pixel at a time under a slow push. Before a final, render short 16-sample windows of the risky
  moves (slow pushes, the tails of long eases, text rising on its own) through the real render path, and check them at
  full size.

### Tilt mode: crisp type under slow moves (opt in per act)

Hint mode, above, is the default and costs type about 15% of its sharpness under a slow push. Tilt mode draws type
under a skew too small to see (0.034° at full size), so Skia places each glyph at quarter pixels: as crisp as a fresh
raster, and two renders bit-identical. An act opts in with one wrapper at its root,
`<RasterModeProvider value={actRasterMode()}>`; the kit's `README.md` ("Raster modes") has the whole contract. The
rules that bite:

- **Tilt type only, never shapes.** A tilt moves everything else off whole pixels; keep tilted boxes tight around their
  type, and give a wide card with thin lines far from its centre `faceTilt={false}` and a `TiltSpan` around each text.
- **Inside the layer, after the text's own transform**: `<TiltSpan>` inside anything with opacity below 1, a filter, a
  blend mode or a blur; `useTypeTilt()` or `<TypeTilt>` for type you move yourself. Never on a composited layer's own
  transform or above one.
- **Running text tilts as one block**, never per character; a trailing character fades by its colour's alpha.
- **Move boxes by transform**, and **a card whose size morphs takes `morphPose`** (whole even layout sizes).
- **Both acts on a boundary render in the same mode**, and a text left untilted on purpose says why
  (`data-slate-untilted="<why>"`).
- **Prove it before and after.** No act converts until the kit's stability bench passes on this Mac
  (`slate qa stability conformance --render --detach`). Then convert one act at a time: `slate check <act> --markup
  --against <ref>` proves the conversion is a no-op in hint mode and audits the tilt rules without rendering, and the
  `stability` gate checks the act's render.

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
- **3D planes that meet edge to edge crack along the seam** and shimmer in camera moves: run every plane about 3 px
  past its shared edges.

## UI belongs to its card

Print a button (or any control) on the card that owns it, so it moves and clips with the card. Never float it in front
at a fixed position: a floating button was once left behind on the bare page when its card moved.

## Geometry that shouldn't be there

- **Remove geometry that's never meant to be seen**: a block hidden under a flat sheet only ever showed as a panel
  popping in when the sheet lifted, and it hid the sheet's own shadow.
- **Zero-length dashed lines still render a dot.**
- **Mount every component before its first animated frame and hide it until then**: a row mounted partway through a
  draw starts mid-move (a dash popped in at 45% of its length).

## Determinism

- The GPU renderer isn't bit-exact run to run (about one level): call a frame "changed" only when it differs from both
  of two baseline renders. Software GL is bit-exact.
- **Every rebuild key** (a raster or tilt key that remounts a layer) **is named and unique among its siblings**: two
  siblings sharing one were drawn wrong, one label shown twice.
- A lookup that throws at module load breaks every composition in the bundle; the kit's never do.
- A half-written act can break the bundle for everyone: the hand-in check's type check and module load catch it.
- **To find which frames a change can affect without a browser**, render the act's scene components to static markup
  for every frame, before and after (react-dom/server's static renderer), diff the markup, then pixel-compare only the
  frames whose markup changed.

## Load

Under heavy machine load, raster tiles can go missing (grey) for a frame. Render at moderate concurrency through the
queue, one heavy job at a time, and let the glitch scan catch what slips through.
