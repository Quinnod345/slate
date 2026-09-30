# Motion systems: the north star and the alternatives

A brand's motion law comes from its own product and its measured references (motion-law.md). These systems are where
to look when a brand has no motion language of its own, when a brand is built on one of them, and for the feeling the
films aim for.

## The north star: The Browser Company's craft

The design north star is The Browser Company's craft and warmth: interfaces with personality, physical and springy
motion that still settles, and animation that feels made by hand, where every move has one reason and lands with its
sound. Take the **feeling**, expressed inside the brand's own tokens. Never take their brand: no copied marks, colours,
type or signature animations.

## Material 3

For brands built on Material, or wanting its feel.

| Duration tokens | ms |
|---|---|
| short 1 to 4 | 50, 100, 150, 200 |
| medium 1 to 4 | 250, 300, 350, 400 |
| long 1 to 4 | 450, 500, 550, 600 |
| extra-long 1 to 4 | 700, 800, 900, 1000 |

| Easing | cubic-bezier |
|---|---|
| standard | (0.2, 0, 0, 1) |
| standard decelerate | (0, 0, 0, 1) |
| standard accelerate | (0.3, 0, 1, 1) |
| emphasized decelerate | (0.05, 0.7, 0.1, 1) |
| emphasized accelerate | (0.3, 0, 0.8, 0.15) |

The emphasized curve is really a path, not one bezier. Material 3 Expressive uses springs (stiffness and damping):
spatial springs damp at 0.9 (default about 700, fast about 1400, slow about 300 stiffness), and effects springs at 1.0,
so effects never overshoot. Its loading indicator adds exactly a quarter turn per morph on one spring, riding a slow
spin, so any cycle can end on the rest shape.

Film travel is longer than UI travel: scale Material's durations up for film-sized moves and soften the settle, and
measure the result against a reference.

## IBM Carbon

Carbon splits motion into **productive** (efficient UI moves) and **expressive** (prominent moments). In a film, that
maps cleanly: **product UI shown inside the film moves productively; the film's own big moments move expressively.**

| Duration | ms | Use |
|---|---|---|
| fast-01 | 70 | micro-interactions |
| fast-02 | 110 | fades |
| moderate-01 | 150 | the default |
| moderate-02 | 240 | expansion, toasts |
| slow-01 | 400 | large expansion |
| slow-02 | 700 | hero moments, dimming |

| Easing | Productive | Expressive |
|---|---|---|
| standard | (0.2, 0, 0.38, 0.9) | (0.4, 0.14, 0.3, 1) |
| entrance | (0, 0, 0.38, 0.9) | (0, 0, 0.3, 1) |
| exit | (0.2, 0, 1, 0.9) | (0.4, 0.14, 1, 1) |

## Apple's Human Interface Guidelines

- Add motion purposefully, supporting the experience without overshadowing it; gratuitous animation can leave people
  feeling disconnected or physically uncomfortable.
- Make motion optional, don't rely on it alone to communicate, and let people cancel it.
- Aim for brevity and precision in feedback motion.
- In visionOS, avoid sustained oscillation (especially around 0.2 Hz) and motion at the edges of the field of view.
- Springs: bounce 0 by default, about 0.3 is noticeable, above about 0.4 feels exaggerated for UI.

## Principles to cite

Disney's twelve principles (from *The Illusion of Life*) and the twelve UX in Motion principles (easing, offset and
delay, parenting, transformation, value change, masking, overlay, cloning, obscuration, parallax, dimensionality, dolly
and zoom). Only easing carries over directly from character animation to interface motion. How each applies:
[principles](principles.md).

## Frame heuristics

At 30 fps (scale for the film's own rate): 2 to 4 frames reads as a snap, 4 to 6 as anticipation, 6 to 10 as a settle,
8 to 12 as a quick transition, and 18 to 30 as a slower entrance. Use them to sanity-check a brand's duration scale, not
to replace it.

## Choosing

| The brand | Start from |
|---|---|
| Has product motion tokens (CSS easing variables, SwiftUI springs) | its own tokens, scaled for film (slate-brand) |
| Is built on Material | Material 3, scaled for film |
| Is an enterprise product with no motion language | Carbon: productive for product UI, expressive for moments |
| Is an Apple-platform app | its SwiftUI springs (response and damping) and the HIG's restraint |
| Has nothing at all | the kit's neutral law as a stand-in, then onboarding writes a real one |

Whatever the source, the brand's law still names its eases by job, caps its staggers and bans the same things
(motion-law.md, banned-moves.md).
