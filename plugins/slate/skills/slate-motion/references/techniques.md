# Techniques

Moves that worked on the films, generalized. Each is a way to show one idea clearly; none is a template to apply
everywhere.

## Clarity moves

- **Tilt-to-flat resolve.** UI enters tilted (about 30 degrees on X, depth of field on) and lands flat and sharp as the
  idea lands. Use it as the beat of clarity.
- **Component zoom, data, pull-back.** Push into one card, let its chip snap and its numbers count, then pull back to
  the whole page or product. A pull-back from one detail to the whole product reveals context.
- **Lift a key value off its surface** as its own object: hand it to a screen-space copy on the same pixels, bring it
  forward with a soft shadow, and quiet the surface behind under a light wash so its statement reads cleanly.
- **Frame brackets** (when the brand has them as an icon) crop into a detail like a viewfinder, then open back out.

## Staging the voice

- **Stage only what the voice names**: three spoken items get three taps, not twelve.
- **Stage the script's spatial words literally** when you can ("underneath" means pages slide out from under).
- **Show an analysis step as the signature line reading the data**, so the pattern has a visible cause. Decoration
  without a cause goes.

## Reveals

- **Reveal a field of elements with a mask morph, not particles**: every element sits on its grid, appears from at
  least 85% scale as the mask clears it, and only elements wholly inside the final shape exist, so nothing is left
  half-faded at rest.
- **The mask morph** itself is a matte that expands on the entrance ease to reveal the next state, with the renderer's
  motion blur on its fastest frames. In After Effects that's a track matte with a wider shutter over those frames (the
  films used 270 degrees over the fastest six).
- **Words can exist before they're seen**: a blind deboss in the ground (shade on the upper edges, a lit lower edge, no
  ink) that inks in letter by letter as the voice says the word.
- **Show the world as many quiet peers**, nearest ring first, with ours the only one in the accent colour; the peers
  carry no names or numbers.

## Stress and contrast

- **Show stress on footage as an atmosphere change** (a shadow front masked to the land, the sky kept clean), never as a
  cover over the image.
- **A stress moment can be a passing band** that re-renders everything inside it in the inverse palette, then leaves,
  returning the scene to normal.
- **Stage an analogy as two parallel objects built alike** (header, number, curve, caption) side by side, joined by the
  signature line across both.

## Paper techniques

In a paper look, materials behave like the real thing:

- **Ink behaves like ink**: broken by the paper's tooth, with uneven pad pressure and a soak after contact.
- **A stamp** comes down out of focus, presses on the snap ease and lands its ink in the grain.
- **A page turn** hinges each leaf at the spine and bends it as strips with graded shading. The leaf casts a shadow on
  the pages beneath that thins as it nears upright and grows again on the other page, instead of jumping across the
  spine.

When the book carries a whole sequence, keep that one object through its cover hand-over, contact, comparison and
audience views. Give each idea a useful framing and a settled read hold. The kit provides `HingedBook`; the staging
and continuity checks are in [physical books](physical-books.md).

## Per-character type

For hero lines only (6 words or fewer, at most two per act): each character rises a little (about 24 px at a 1080-line
frame), fades in and un-blurs on the entrance ease, staggered by the character unit. Lines go per word, lists per line.
The kit's `PerChar`, `PerWord` and `Arrive` do this with the stagger cap built in.

## Count-ups

Always on the entrance ease over the broad duration, with tabular figures, and only real values in the product's real
range. Never linear. Each figure holds at least three frames so the eye can read the change.

## Product UI in motion

Load product surfaces the way the product loads them: the curve draws, the numeral counts, the marker settles, the
statement lands. Highlight items on their spoken word with the product's own hover or selection state. The rules for
product UI are in slate-build (app-ui.md).
