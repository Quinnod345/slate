# Headless Chrome: CSS and assets that fail quietly

The things below render fine in a normal browser and fail, silently or intermittently, when a frame is taken by the
renderer. Build around them from the start.

## Clipping and masking

- **CSS `clip-path: path()` silently fails in headless Chrome**, and SVG data-URI masks can decode after the frame is
  taken. Use `polygon()` clips and CSS `mask-image` gradients.
- **A clip path on a transformed element rides its transform** (a corner mark switched at the wrong height). Clip on
  untransformed, full-frame wrappers.

## Type and layout

- **Positioned type inside a zero-width container wraps one word per line**: set `white-space: nowrap` on positioned
  type.
- **Text snaps to whole pixels** under slow motion (remotion-gotchas.md): move with transforms and the kit's raster
  hints.
- **Centre a lockup** (a mark beside a word, a title block) on its visible extents and the type's cap height, aligned
  on the baseline, never on its layout box: the box carries ascender and descender space the eye doesn't see.
- **SVG text under a changing transform needs `text-rendering: geometricPrecision`.** Otherwise each render tab lays it
  out at a different scale, and the motion-blur samples, which come from different tabs, double it (a resting seal
  differed by 13 levels from frame to frame).
- **Fonts**: headless Chrome can't see fonts installed locally from a web font kit. Load every face explicitly through
  the brand's font plan (Google Fonts by family, a kit URL, or an installed face the renderer can reach); the font gate
  fails the render when a face is missing rather than letting a fallback slip through.

## Textures and effects

- **Draw textured objects (grain, ink) at one base size and scale the result**, so the texture never jumps as the object
  scales.
- **Post-processing passes render blank** under Remotion's frame-by-frame loop; build the effect into the scene or drop
  it.
- **Never fake motion blur** (a CSS blur on moving things, or Remotion's own camera blur, which is 8-bit additive and
  shifts flat colours). The renderer's sub-frame average is the only motion blur.

## Footage

- **Video decoders crash long parallel renders.** Play footage (plates, captures) as JPEG sequences through `<Img>`, one
  frame per film frame (`round((t − t0) × fps)`), with every sub-sample of a frame showing the same image.
- Heavy media lives in the film's `public/` folder, linked into the bundle, never copied: copied plates once made every
  bundle 290 MB and filled the disk.

## Performance

- Prefer the kit's own components to heavy CSS on machines without a strong GPU: large blurs, big shadows and gradient
  stacks are expensive, and the renderer draws each frame 16 times.
- PNG frames are only for where alpha or lossless averaging needs them; the renderer handles that.
- Huge `translateZ` zooms rasterize badly: use a 2D camera over poses and zoom in log space.
