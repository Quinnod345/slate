# Building hinged books

`HingedBook` is a reusable physical surface in the kit's 2.5D world. It draws board thickness, an underlying page
block, a fixed last page, a hinged front cover and caller-owned recto/verso faces on curling leaves. The film owns
the faces, timing, camera staging, morphs and any stamp or comparison. Read slate-motion's physical-books.md before
staging one as the act's carrier.

## API and coordinates

Import `HingedBook`, `PageLeaf` and their types from `@slate/kit`. Geometry helpers also export from
`@slate/kit/core`, so beats and checks can use them without React.

| Input | Contract |
|---|---|
| `w`, `h` | Positive page dimensions in world pixels. Derive their size from the film's frame; choose their aspect for the object. |
| `state.cover` | Degrees: `0` closes the cover on the right; `-180` opens it on the left. |
| `state.leaves` | One angle per leaf, ordered from the top of the closed stack down. A missing angle defaults to `0`. |
| `coverOuter`, `coverInner` | Caller-owned cover faces, with their origin at the face's top left. |
| `leaves` | `{recto, verso}` pairs. Each face is laid out once in page coordinates, from `(0, 0)` to `(w, h)`. |
| `last` | The fixed right-hand page under every turning leaf, in the same page coordinates. |
| `overhang` | Cover extension beyond the free page edge and both ends of the spine. Default: `0.02 × w`. |
| `pageThickness`, `coverThickness` | Positive depth units. Defaults: `0.002 × w` and three page thicknesses. |
| `pageRadius`, `coverRadius` | Nonnegative corner radii. Defaults: `0.013 × w` and `0.035 × w`. |
| `strips`, `curl` | Whole strip count from 1 to 100, default 10; nonnegative curl strength, default 1. Zero curl makes a rigid leaf. |
| `palette` | Optional page, page-edge, cover, cover-edge and shade colours. Unspecified roles come from the brand's tokens. |

The book's origin is the **centre of the spine**. A closed page occupies `x = 0…w`, `y = -h/2…h/2`; a turned page
occupies `x = -w…0` over the same height. Face content uses its own top-left origin, so callers never mirror a verso
themselves. The kit gives that face its orientation. A cover face measures `w + overhang` by `h + 2 × overhang`;
`bookGeometry(size, leaves.length)` returns those dimensions as `cw` and `ch` for precise emblem placement.

`PageLeaf` provides the same spine-centred leaf on its own, with a caller-chosen `z`. Its `radius` controls the page's
free corners. Angles must be finite; the helpers clamp easing overshoot to the physical range `-180…0`.

## Build faces once, drive state from beats

Make a shared face component for the film's pages, expressed in the page's local coordinates. Its progress props can
reveal ink or data; it needs no private timeline. Reuse that component and one ordered face list for the whole act.

The book itself takes state rather than start times. A turn can be computed from the act's word-derived beats:

```tsx
import {roleEase, tw} from '@slate/kit/core';
import type {BookState} from '@slate/kit';

const state: BookState = {
  cover: -180 * tw(t, beats.cover.start, beats.cover.dur, roleEase('move')),
  leaves: beats.turns.map(turn => -180 * tw(t, turn.start, turn.dur, roleEase('move'))),
};
```

Here `t` is film seconds and `beats` is the act's own beat data. Register those turns with `defineMotion`, and the
meaningful page or stamp landings with `defineEvent`; the book does not emit authored events for the film. Align
contact times with the voice through the existing beat helpers, and keep the reading hold after the turn.

Place the same instance inside `Camera` and, when needed, `Place`. Reframe that object as the idea changes; retain
the same face list and accumulated marks. Camera and object transforms belong to the film's motion trace too. Rest
the camera for the hand-over between a carried mark and a cover emblem so both use the same screen position.

## Raster and depth rules

- Use `RasterModeProvider` at the act root. Put `TiltSpan` around each text block in a face, keeping shapes outside it.
- A reverse face has the same physical registration in hint and tilt modes. Tilt mode places it by transform to keep
  fractional offsets stable; its target matches the final flat page.
- Keep `preserve-3d` parents free of opacity and filters. A page may clip its own face content; an ancestor that
  flattens the whole book destroys the board and strip depth.
- Use the kit's thickness ordering. Changing a palette or face does not need a new depth stack.
- If a hand-over crosses acts, both boundary frames draw the same book state, camera, raster mode and face content
  through one shared contract component. Apply the existing [contract holds](contracts.md).

## Review before hand-in

The kit includes `Kit-Book` and `Kit-Book-Hint`, neutral short compositions that open a cover and turn two leaves.
Use that mechanism proof when changing the primitive. A film adds its own complete scenario: emblem hand-over,
contact, comparison, audience view and read holds, wherever its treatment calls for them.

Record the uninterrupted sequence and inspect the moving frames, the rest switch and the settled result. Keep the
recording, frame numbers and any failure samples. Type checks and geometry tests support this review; they do not
establish that page turns, type registration or the authored sequence look right in the rendered film.
