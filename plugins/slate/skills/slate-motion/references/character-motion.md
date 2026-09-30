# Character motion: the one-page checklist

Run this before handing in any act where a brand mark, motif or character moves. Each line is a rule from the full
method in [character and motif motion](character-and-motif-motion.md); the reason is there.

## Clock and phase

- [ ] The pose is a pure function of the film frame, sub-frame samples included. No timers, no randomness, no noise.
- [ ] Every key and every per-part offset is a whole number of frames. No sub-frame staggers (35 or 55 ms offsets put
      parts at different shutter phases and read as jitter).
- [ ] Only three phase modes: unison, pairs (a fixed whole-frame lag), a wave (a fixed whole-frame step per slot).
- [ ] Parts stay on their spokes and never spin on their own.

## Curves and speed

- [ ] Every move starts and ends at rest, or hands its exact velocity on. No entrance, exit, state or linear ease on
      the parts (the fast-out entrance ease leaves rest at over six times its average speed and smears under blur).
- [ ] Every point at most 30 px a frame and 8 px a frame squared, at a 1080-line frame.
- [ ] No oscillation faster than a 16-frame swing; nothing loops but a breath; at most one overshoot per gesture.
- [ ] Nothing is driven by the voice's loudness (it modulates at 2 to 8 Hz, the jitter band).

## Drawing

- [ ] Drawn as one flat plane in two fixed sheets, in the brand's paint order; never 3D parts in a shared 3D context
      (they z-fight under the 16-sample shutter).
- [ ] Opacity, shadow and paper edge per sheet or whole mark, never per part.
- [ ] Nothing clips it: an overlay layer, a clear circle in every clip, the envelope checked per gesture.
- [ ] A spread never rests near the split point.

## Size

- [ ] Below the small-size threshold (about 80 px as a rule of thumb) the whole mark moves as one.
- [ ] Per-part motion under 2 px is dropped.
- [ ] The regime is chosen once per gesture, from the size at its contact frame.

## Meaning and timing

- [ ] Each gesture has one meaning and keys to its word: contact C = S + 2, early never late.
- [ ] One momentary gesture at a time, at least 4 still frames between.
- [ ] Still between gestures; calm (the exact brand mark) at both ends of every act.
- [ ] The mark returns to the exact brand mark at rest and at every act boundary.
- [ ] Every sound on its own contact frame, one event per note.

## Proof

- [ ] `slate check <act>` passes; `slate qa motion` passes on the act's trace.
- [ ] The gesture sheet reviewed at the smallest and largest sizes, full and quarter speed, with sound.

## Designing a new character

Start from research, not taste alone: a sourced survey of how voice-assistant and loader marks move (assistant rings and
glows, dot marks, loaders, orbs, cursors) is the reference material for designing a mark's gestures. The findings that
matter are in [sources](sources.md): take the principle, never the look.
