# Contracts between acts

Acts built by different builders, or rendered separately, meet on **contract frames**: a named rest state that both
sides draw exactly. On the first films, five of five joins passed with jumps of 0.00 to 0.39 against the picture's own
motion, because every boundary was specified to the pixel and checked.

## How a contract works

- **One shared component per rest state**, in `scenes/contracts.tsx`. On the boundary frames both builders render that
  component and nothing else. Its position, size and every value come from named constants both sides import.
- **Hold it still for at least 6 frames** on each side (`slate check <act>` checks the hold; `--hold` changes it). The
  two boundary frames are then bit-identical, and a splice or a bridge has clean frames to start and end on.
- **Next to it**, animate to and from the rest state with the same named constants, so your first or last moving frame
  continues exactly from it.
- **A character is calm** in the rest state: every gesture finished, the mark at rest.
- Declare it in the act: `export const contracts = {in: 'NameIn', out: 'NameOut'}`. The engine registers each rest
  state as its own composition, so it can be rendered alone and compared.

## Good contracts

- **The simplest is an empty ground**: the next act's first frame is the brand's ground with its texture and nothing
  else.
- **A single object**: one line at a given place and weight, one ring, one lockup at an exact position, a set of frame
  brackets. The next act turns it into something else.
- **Geometry can encode the next act's data**: a line drawn as four segments whose joins sit exactly where the next
  builder splits it.
- **A whip**: one act ends whipping one way in its last fraction of a second; the next opens mid-whip. Mirror the
  direction and rotation.

## Settling out before the boundary

Everything that decorates a frame (fading rings, impact echoes, paper light, grain, a character's gesture) settles out
before the contract frame: over the last 0.6 s at least. On the first film a relief ring still fading on an act's last
frame blinked out on the cut: a join jump of 1.36 against 0.13 inside either side. The kit's `settleBy` does this for
logo builds.

After a contract frame, hold the contract state for its frames, then bring texture (light, grain) up slowly on the
rest-to-rest ease, so there's no flash against the flat contract frame.

## Hand-offs inside an act

Hand-offs inside one builder's span aren't pixel contracts, but they follow the same discipline: carries per the
script's transition column, with no pop or cut.
- Hand an element between screen space and a card only when the card is at rest and the camera at identity, so every
  hand-over is a carry by construction.
- Hand an object between components only at rest or on a morph's first moving frame.
- When a shot switches from an act's own drawing to a kit component mid-shot (a logo build), the component's start
  state matches the drawing exactly; check the switch frame pixel by pixel.
- When several acts share one continuous space, draw them from one stage function of film time, so internal cuts are
  the same pixels on both sides.

## When a shared component changes

Re-measure the join from both sides, and tell the other side's builder (in the component's header and their notes).
When a surface in an approved act is replaced, keep its outer geometry identical (card size, the anchor positions of
carried elements), so every pose, carry and contract stays where it was.

## Checking a join

1. Render both boundary frames at full size and compare: they should be bit-identical, or differ by at most a 1 px
   settle. CSS transforms change the raster, so "the same picture" drawn two ways isn't the same pixels: draw rest states
   untransformed.
2. `slate check <act>` renders the act's boundary frames against its neighbours' and the rest state, and checks the
   hold.
3. After the finals render, `slate qa boundary` measures every join between consecutive act renders against the
   picture's own motion either side.
