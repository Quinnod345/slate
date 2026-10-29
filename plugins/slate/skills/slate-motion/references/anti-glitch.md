# Anti-glitch rules

These diagnoses came from glitches that reached review on the first films. Their fixes are contextual craft recipes;
the current brief can choose fast travel, flutter, bounce, sustained motion, cuts or crossfades. Preserve that choice
while repairing the actual jitter, unreadability, clipping or discontinuity. Current technical gates still apply (see
[motion law](motion-law.md)). The pre-render checklist is [the glitch catalogue](glitch-catalogue.md).

## Speed, blur and flicker

- **Blur length follows speed.** A wobble at f Hz makes motion blur pulse at 2f; flicker sensitivity peaks near 8 Hz.
  A 4 to 4.5 Hz damped wobble on every beat read as jitter. Current QA limits loop frequency to 1.9 Hz and requires at
  least a 16-frame period. Test a permitted intentional flutter under the final shutter and remove unwanted flicker;
  the failed wobble is not a general ban on oscillation.
- **Sub-frame staggers** (1.05 and 1.65 frames) mean no two parts sit at the same phase of the shutter, so every part's
  blur differs. Offsets between parts are whole frames.
- **A strong out-ease leaves rest at over six times its average speed**, so the blur jumps from nothing to a long streak
  in one frame; an exit that ends fast does the same at the end. A rest-to-rest curve or velocity-matched handoff can
  fix it; inspect the rendered continuity of a deliberately fast curve.
- **Strobing is too much change between adjacent frames**, not a frame-rate problem. Keep a fast move's speed and
  acceleration within its applicable class policy. At 1080 lines the default rig limits are 30 px a frame and 8 px a
  frame squared; UI and carries have different defaults and brands can set supported speed overrides.
- **Unexpected doubled edges during a whip** need inspection. Repair sampling, path or timing while preserving the
  intended gesture; a fast whip does not automatically need to become a glide.
- **Stacked out-of-phase loops read as swimming.**

## Entrances and exits

- **Nothing appears from nothing.** An entrance moves in from fully outside the frame, or fades in as one sheet over a
  real duration, or starts from at least 85% scale in place. It never starts part-way through its move, and never
  starts partly inside the frame.
- **An opacity fade on a strong out-ease** is mostly done in its first frames and reads as a pop: fade-ins need a
  gentler curve or a longer duration, and objects lay in from just above rather than from deep behind.
- **Unplanned one-frame disappearances** need repair. The carried-object recipe uses exits, including line endpoints;
  a deliberate cut or state change can be instantaneous when it serves the chosen grammar.
- **Exits leave the frame as opaque objects**, front one first, and are removed once off frame; cards fading out over
  each other read as cards seen through cards.
- **Return an object by carrying it back** (rising from below), not by fading it up from depth: a fade read as a ghost.
- **Carried elements leave with their page**; nothing lingers half-cropped at the frame edge into the next section.
- **A sheet's edge shadow fades out as the edge reaches the frame's border**, so the shadow band never vanishes in one
  frame.
- **Deal new cards in** rather than leaving an empty frame between screens, and keep content visible during a morph.

## Morphs and overlaps

- **Inspect overlapping cards and headings during crossfades.** Sequencing one out before another enters fixed the
  earlier muddy overlap. A deliberate blend can remain when its text is readable and its continuity is intentional.
- **Never duplicate an object to animate it.**
- **Never ease a value twice** (a mask fed through two eases filled in one frame).
- **Animate corner radii before a split or merge**; don't let them snap on the first frame.
- **A container and its contents share timing**, or the edge runs ahead of what it holds.
- **Two moves on the same path sum into an overshoot**: drive them from one clock and measure the path.
- **When an element lifts off a chart**, its attached parts (area fill, head marker) go with it or fade gradually;
  removing them all on the hand-off frame changed 23,654 pixels in one frame.
- **Draw merged segments as one continuous path** so no seams show.
- **When a layer splits in two along a line**, overlap the halves by about 2 px so no seam shows.

## Depth and stacking

- **Surfaces crossing through other planes get sliced** for a few frames: nothing crosses, and exits finish before
  entrances take the space. Keep moving cards in front of the planes they pass.
- **A card flip lifts toward the camera** and never passes behind its neighbours.
- **Deal cards as opaque paper**, parallel to the deck and always in front of it, with only in-plane rotation: a
  translucent, depth-turned card let the card under it show through and got sliced.
- **Show supporting surfaces one at a time**, rising and sinking in place, each in its own layer: surfaces never slide
  past or under each other.
- **Connecting threads route through gaps** and never cross words.

## Text and layout

- **Current motion QA rejects type entrances during another same-act carry moving above 2 px per frame at a 1080-line
  frame**, with an exemption for type on that same carried element. Preserve this check; inspect any other moving-text
  treatment for actual readability rather than assuming all moving layouts are prohibited.
- **Never write glyphs into space that's still occupied**: clear each glyph's space before it arrives, and let an impact
  ring out before the end card forms.
- **Keep layouts stable when data changes**: leave out explanatory lines that would make a column reflow (a reflow
  became a fast 112 px move).
- **Count a value once**; re-count only when it really changes, from its old value.

## Fine detail

- **Fine regular patterns shimmer and moire**, worst in downscaled copies: a field of 2,560 small dots on a tight pitch
  rang and sparkled. Use fewer, larger elements jittered off any lattice, move each only a little, pin the pattern to
  the screen while the camera travels, and check it at the smallest delivery size.
- **Thin rotating lines (about 1 px) crawl with jaggies**: use strokes of 3 px or more, and give every moving line a
  cause.

## Character marks

- **A mark whose parts overlap by design** shows seams and double shadows when each part is drawn separately with its
  own opacity, paper edge and shadow; parts placed in 3D at the same depth z-fight; and a spread that hovers near the
  point where the mark splits apart flickers between fused and split. Draw the parts as two fixed sheets, opacity and
  shadow per sheet, flat, and never rest near the split point.
- **Parts cut off at container edges**: nothing had checked the mark's envelope against the containers that clip it.
- **A colour mixed by distance from an edge** flickers at that edge; mix by flight progress instead.

The full method is in [character and motif motion](character-and-motif-motion.md).

## Renderer-specific

Headless Chrome and Remotion have their own traps (text snapping to whole pixels under slow motion, depth-sorting races,
clip paths that fail silently). Those are builders' rules: slate-build, remotion-gotchas.md and headless-chrome.md.
