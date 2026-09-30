# The glitch catalogue: a pre-render checklist

Thirty classes of glitch the first films shipped to a review or caught on the way, each with the rule that prevents it.
Walk the list before every render that matters; the reasons are in [anti-glitch](anti-glitch.md).

| # | Glitch | Prevent it by |
|---|---|---|
| 1 | "Has it started?" checks open early, because an ease returned 1e-15 instead of 0 | the kit's exact-endpoint eases; never your own wrapper |
| 2 | One word lookup that throws at module load breaks every composition in a bundle | lookups that never throw; an empty miss list at hand-in |
| 3 | One half-written act breaks the shared bundle | the type-check and module-load parts of the hand-in check before any render |
| 4 | Opacity or a filter on a 3D group flattens it; a translucent card shows its thickness through its face | opacity and blur on leaves; fading cards render as one flat sheet |
| 5 | The browser's 3D sorting races with many parallel planes: a card face drops for two frames | few parallel planes; one explicit stacking order, each layer its own context; moderate render concurrency |
| 6 | Parts placed in 3D inside a tilted group z-fight under the shutter | characters drawn as one flat plane in passes |
| 7 | Surfaces crossing through other planes get sliced | nothing crosses; exits finish before entrances take the space |
| 8 | Pop-ins: a first frame mid-motion inside the frame, a mask eased twice, a shadow or colour switching at a threshold, a label reflowing in one frame | enter from off frame or from 85% scale or zero opacity; blend colour over the tick duration on a snap; continuous morphs |
| 9 | Moves that start at full speed smear one frame and go sharp the next | big moves on the rest-to-rest ease |
| 10 | Fast carries streak under the shutter (a quick carry peaked at 310 px a frame) | a speed audit per move; big moves at the camera or signature duration |
| 11 | Oscillation reads as noise | one clock, explicit phases, swells instead of wobbles, no loops |
| 12 | A spline through uneven keys overshoots and backtracks | the kit's monotone spline |
| 13 | CSS `clip-path: path()` fails silently in headless Chrome; SVG data-URI masks decode after the frame is taken | `polygon()` clips and CSS gradient masks |
| 14 | A clip path on a transformed element rides its transform | clip on untransformed full-frame wrappers |
| 15 | A zero-length dashed line still renders a dot | guard zero-length strokes |
| 16 | Video decoders crash long parallel renders | footage as JPEG sequences played from film time |
| 17 | The headless browser can't see locally installed web-kit fonts | fonts loaded explicitly; the font gate fails the render on substitution |
| 18 | Huge `translateZ` zooms rasterize badly | a 2D camera over poses, zooming in log space |
| 19 | CSS transforms change the raster (shadows by a few levels): "same picture" isn't "same pixels" | rest states drawn untransformed; contract frames pixel-diffed |
| 20 | The GPU renderer isn't bit-exact run to run (about 1 level) | "changed" only when a frame differs from both of two baseline renders |
| 21 | Temporary bundles fill the disk | the engine deletes each bundle after use |
| 22 | Impact marks still fading at a shot's end blink out on the cut | everything settles out over the last 0.6 s before a contract; the boundary check |
| 23 | Per-character type with no spread cap | the kit's capped `PerChar`; never your own |
| 24 | Parallel builders overwrite each other's scratch files | a scratch folder per builder |
| 25 | New data rendered with old code | code and data always move together; one bundle per source hash |
| 26 | A tracker hops between near-equal features, or drifts outside its reliable window | follow features, hold outside the window, a temporal median |
| 27 | Generated pixels on screen read as AI | a generation only as a matte or shape over real frames (slate-generate) |
| 28 | A second count of a value already on screen, two overlapping texts crossfading, a fade up from depth | count once; state changes snap; carries rise from below |
| 29 | Per-part character motion at small sizes reads as noise | the whole mark moves as one below its size threshold |
| 30 | Positioned type in a zero-width container wraps one word per line | `white-space: nowrap` on positioned type |

## Also check

- Text that steps a pixel at a time while shapes glide (slow drifts, the tails of eases): move with transforms, give
  moving layers the kit's raster hints, and keep one raster through a rest (slate-build, remotion-gotchas.md).
- Fine regular patterns that shimmer at the smallest delivery size.
- A UI state cut under the shutter renders as one mixed frame: expected, not a glitch.

## After the render

`slate qa glitch` scans every act for one- and two-frame glitches and skips flags within 3 frames of a planned beat;
triage what's left (slate-qa, triage.md). When one pop is found, scan the whole act frame by frame for others and fix
them together.
