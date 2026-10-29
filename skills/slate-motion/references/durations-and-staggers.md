# Durations and staggers

## The duration scale

Seconds are the source of truth; frames come from the film's own rate. A brand's scale names its tokens; the kit's
neutral scale, and what each token is for:

| Token | Seconds (neutral) | Use |
|---|---|---|
| tick | 0.20 | chip flips, checks, a dot lighting, a glyph morph committing |
| quick | 0.33 | small UI elements, icon morphs, make-room re-layouts |
| base | 0.50 | standard entrances: a line of type, a card |
| broad | 0.80 | panels, mask and band wipes, short count-ups |
| camera | 1.20 | camera push, pull and truck; shared-element carries |
| signature | 1.80 | logo construction beats, portal zooms, the big pull-back |

A brand's own values come from its product's duration tokens and its measured references (slate-brand).

**Starting recipe**
- Earlier product films used exits at 0.75 times their entrance and a 0.2 s floor outside state flips. The brand's
  duration scale and `exit_factor` choose the actual values for this film.
- Continuous drift and time-based data can run beyond a duration token. Other traced moves must match the chosen
  scale; choose a suitable token instead of disguising the move's duration.
- One signature-length move per act can make it feel rare. Current QA enforces a count only when the token declares
  `max_per_act`; a different brief can choose a different structure.
- Earlier large carries ran long to reduce screen speed. A requested fast carry can stay fast when its declared curve,
  applicable speed policy, readability and rendered blur support it.

## Frame heuristics

At 30 fps (scale for other rates): 2 to 4 frames reads as a snap, 4 to 6 as anticipation, 6 to 10 as a settle, 8 to
12 as a quick transition, 18 to 30 as a slower entrance. A head turn in 2 in-betweens reads as a nervous twitch; a
stroke of about 10 frames reads as deliberate.

## Staggers

**Units** (the neutral law; brands set their own): per character about 0.05 to 0.067 s, per word 0.1 s, per line, list
row or card about 0.133 s, per major block 0.2 s. At a given frame rate these are whole frames.

**Starting caps**
- The neutral law caps a group's spread at 0.6 s and half of its entrance duration, and groups more than 8 items.
  Brands can choose their own caps and item count; the gate checks the declared law.

**Current gate:** stagger groups are limited to entrance, exit and state roles. The earlier recipe moved rest-to-rest
objects as one piece. A requested treatment must still fit the supported trace roles rather than bypass this check.

**Exits** stagger in reverse at two thirds of the unit, or leave together on the exit ease when a camera move is
carrying the scene away.

## Order

- **Reading order** for type and lists.
- **Along the data's direction** for charts: low to high, baseline to latest reading, the product's own order.
- **From the focal element outward** for grids.
- **The product's own order** for rows and steps: cascade lists and rows out at about 0.2 s intervals.

## Sync

- The first element of a group lands within 2 frames of the voice's word it illustrates.
- Groups without a voice line land on the music's beat grid.

## Type

- **Hero lines** used a 6-word, two-per-act script preset, with per-character entrances; lines arrived per word and
  lists per line. Script reading settings can change the hero limits, and the brief chooses the entrance treatment.
- Current motion QA requires hero text to move at least 16 px at a 1080-line frame or unmask. Opacity-only remains
  supported for secondary labels; a creative preference does not turn a failing hero entrance into a pass.
- The kit's `PerChar`, `PerWord` and `Arrive` already cap their spread; don't re-implement them.
- Lay out text with room for words that arrive later, so no make-room move is needed.
