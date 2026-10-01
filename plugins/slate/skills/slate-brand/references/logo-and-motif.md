# Logos and motifs

## Logos

- **SVG only**, from the brand's own artwork (a vector from the brand, or a careful trace of its official raster, checked
  for overlap with the original).
- `brand.json` → `logos`: each logo by name, with its SVG path and a **build spec** for the kit's logo build: the
  construction guides, the vertices, the order, the **keystone** (the element that lands last, such as an accent dot or
  square), and the timing.
- The kit parses the SVG (absolute paths, exact bounds, per-glyph boxes from the path bounds) and builds from it; the
  keystone's colour comes from the artwork.
- **Every mark comes in each colourway a film needs, ink and white at least**, so builders never recolour a mark: a
  sibling logo drawn for the inverse ground (`"mark-inverse"` with `"on": "inverse"`), or a logo's `colourways` naming
  them (`{"ink": "mark", "white": "mark-inverse"}`). The kit's `LogoBuild` and `LogoSting` draw the one made for their
  ground (`dark`), and `slate brand validate` asks for the missing one.
- **Never rotate a mark that has a fixed orientation**, even in a portal zoom.
- **A mark that stays on screen across sections of different grounds** (a corner logo over a light page and a dark
  section) changes colourway exactly at each section's moving edge: draw it in both colourways, each clipped to its own
  ground, so the split travels with the edge.
- A logo you don't hold as a licensed asset (a partner's, a publisher's) is set as the name in type, never drawn.

## Motif marks

Some brands have a motif: a mark made of parts (a rosette, a set of dots, a ring) that can move as a character. The pack
describes it as data for the kit's motif rig:
- `motif`: the parts (shape, count, size), where they sit (a ring's radius), their rest rotations, the groups (the
  brand's paint order becomes two fixed sheets), the mark's ratio inside its disc, the colour, the size thresholds for
  per-part motion;
- `motif.gestures`: the brand's gesture vocabulary, each meaning mapped to the rig's gesture kinds (the kit has
  defaults).

A motif keeps one colour through a film and changes form only at designed moments. Its motion follows the mark-as-
character method (slate-motion, character-and-motif-motion.md): computed from this artwork and the film's words.

## Checking

Render the logo build and any motif gestures in the kit's review sheet at the smallest and largest sizes they'll appear,
and compare the rest frames with the brand's own artwork pixel by pixel.
