# Product UI on screen

When a film shows a product, the product is the truth. Its copy, its states, its order and its hierarchy come from its
code at a pinned commit (slate-story's truth pack), and the film shows it the way it really behaves, only bigger and
better staged.

## Show the product as it is

- **At the size and form factor its code lays it out.** Don't invent a device hand-off (a phone) for a product that
  ships at desktop size.
- **Ground UI in the product's real code**, read in place, never copied into Slate. Captions, chips and wording are the
  product's own.
- **Show only statuses the product really has**: no invented intermediate state before the real one.
- **Show UI in the state the data implies**: a feature that needs two readings isn't visible on the first.
- **Simplify only by choosing states it really has** (a real toggle turned off), never by deleting parts arbitrarily.
- **Keep the product's typographic hierarchy**: an eyebrow stays an eyebrow, not a big heading.
- **Show UI state changes with the product's own state styles** (a disabled button waking from its disabled opacity on
  the word) and highlight items on their spoken word with the product's own hover or selection state.
- **Load surfaces the way the product loads them**: the curve draws, the numeral counts, the marker settles, the
  statement lands.
- **Redraw the product's icons from their source geometry** (the icon library's own paths, at the product's pinned
  version), never approximations. The kit's generic icons are for brands without a set of their own.
- **Figures in the voice match the data state on screen.** One constant in the act picks which reading the film shows
  (the first reading or the second), and every surface that depends on it (the report, the chart, the character's line)
  reads that constant, so a sentence about the old score never sits beside a report showing the new one.

## Interactions

- **Show interactions as the product behaves.** Whether a question is typed, chosen from a chip or already sent
  comes from the actual screen and the requested staging. Stream an answer only when the product does.
- **Never show a password being typed or a sign-in link being sent**; show sign-in the way the product really does it.
- **Keep conversation continuity readable**: a follow-up occupies its actual place in the thread. Use the product's
  scrolling behavior when the shot needs it, with enough context to understand what changed.
- **UI belongs to the card that owns it**: print a button on its card so it moves and clips with the card; never float
  it in front at a fixed position (a floating button was once left behind on a bare page).

## Film copy and product copy

- The script's on-screen lines and the product's copy are separate layers. Show the product's copy as the product
  writes it (emphasizing the script's words), and set the script's line as film type beside it rather than rewriting
  the product.
- Don't dress plain concepts as UI chips: chips read as invented UI. Use plain type.
- The on-screen word budget can justify leaving product elements out of a shot; record the omission in NOTES.md.

## Restaging for film

- When a product surface would vanish on the film's ground (a light-grey panel on a light-grey page), set it as its own
  card and note the restaging.
- On a fidelity pass, correct the surfaces shown in the film against the selected source. Keep the requested scope
  and accepted work visible; a local correction can preserve the rest of a scene. Record deliberate omissions,
  restaging and any remaining mismatch instead of treating a secondary surface as automatically accurate.

## Native geometry and type

Use `AppViewport` from `@slate/kit` for a reconstruction whose product layout uses points. Supply its `profile`
from private product data: width, height, capture pixel ratio and safe areas. Children lay out at that native size;
the completed viewport scales into the film. Film dimensions and camera scale must not change native wrapping.
The ordinary `Field`, `Text` and other film primitives keep their existing film scale; they are not native matches.

Keep control rectangles in one exported layout object. `AppBox` draws those native rectangles; `appLayout` from
`@slate/kit/core` maps the same rectangles and anchors into viewport pixels for camera and interaction targets.
Its mapping is local to the viewport; apply the scene's world or camera transform afterward. Safe areas describe
content bounds and do not insert padding automatically. Nested AppBox rectangles establish child coordinate frames;
use `appLayout(...).within(parentRect)` for the same local frame outside React. The hook inherits that frame inside
AppBox and rect-bearing AppText. AppBox paints borders separately so they do not shift the child origin; express
padding and margins in the native child rectangles. Additional CSS transforms must be represented in the staging
before using an anchor. AppText anchors describe its box; baseline adjustments move glyphs inside that box.
AppText children are text and inline content. Place positioned icons, AppBox elements, additional AppText boxes and
AppViewport elements in sibling native rectangles, outside its baseline-adjusted span. Native primitives reject
placement inside AppText so a glyph offset cannot silently move a geometric target. Custom inline components must
also keep their content inline; their arbitrary CSS is not included in the native geometry map.
Use `snapAppPoint` only where the product needs source-grid
rounding; camera scaling does not redefine that grid.

Use `AppText` with explicit product typography: family, point size, line height, weight, tracking and any calibrated
optical size, variable-font axes or baseline adjustment. Optical size stays at its authored point value as the screen
scales. AppText disables synthesized faces; declare and load the actual faces through the brand's font plan so a
missing font cannot pass as a match. Keep the film's type roles separate. Platform font aliases, metrics, palettes
and device profiles belong to the private brand or film, never the shared kit.

Source code and captures answer different questions. Record the selected source revision and the capture's
build/device/runtime; identify version differences before deciding which appearance governs. Compare the flat
reconstruction with real captures using side-by-side frames, overlays, crops and named regional measurements.
Similarity scores are measurements, not percentages of overall fidelity. A source-only screen remains unmeasured
until its actual comparison exists. Inspect the staged film at the intended viewing size separately for legibility.

For translucent UI, preserve the relationship between the material and what lies beneath it. A clipped redraw of
the underlying screen with blur can work under a three-dimensional camera where browser backdrop filtering does
not; document the approximation and compare it with the actual product material. Avoid shared mutable material
settings that let one screen alter another screen's result.

## Capture or rebuild?

Real app captures (`slate capture ios`, `slate capture web`) provide appearance and behavior evidence. Choose the
scene's representation for its intended transformations: a capture can move as a whole surface, while an unfolding
card, a reshaping chart or a label carried into another screen needs independently controllable parts. Use the
product's actual components or source geometry where practical, and measure source and destination seats in the
same camera space. Keep the owning surface and its contents together through the change.

A film transformation can connect truthful product states without claiming the app offers that transition as an
interaction. Record that editorial restaging in NOTES.md. Compare the reconstruction with captures for appearance,
then judge the staged transformation in motion. The same truth rules apply to captures and reconstructions
(captures.md); neither representation is a universal default for every shot.
