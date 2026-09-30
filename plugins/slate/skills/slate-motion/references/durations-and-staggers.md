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

**Rules**
- Exits are 0.75 times their entrance.
- Nothing moves in under 0.2 s except state flips.
- Anything longer than the signature token is a continuous camera drift or time-based data.
- At most one signature-length move per act. Signatures work because they're rare.
- Big moves run long: a page scroll at the signature length, a large rise over about 2 s, so no frame jumps far.

## Frame heuristics

At 30 fps (scale for other rates): 2 to 4 frames reads as a snap, 4 to 6 as anticipation, 6 to 10 as a settle, 8 to
12 as a quick transition, 18 to 30 as a slower entrance. A head turn in 2 in-betweens reads as a nervous twitch; a
stroke of about 10 frames reads as deliberate.

## Staggers

**Units** (the neutral law; brands set their own): per character about 0.05 to 0.067 s, per word 0.1 s, per line, list
row or card about 0.133 s, per major block 0.2 s. At a given frame rate these are whole frames.

**Caps**
- A group's total spread is at most 0.6 s and at most half of the group's own entrance duration.
- More than 8 items are grouped; they're never staggered one by one.

**What staggers:** only entrances, exits and state changes. Rest-to-rest moves travel as one piece.

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

- **Hero lines** (6 words or fewer, at most two per act) write on per character; lines write on per word; lists per
  line.
- Hero text must move at least 16 px or unmask; opacity alone is flat and cheap (fine for secondary labels).
- The kit's `PerChar`, `PerWord` and `Arrive` already cap their spread; don't re-implement them.
- Lay out text with room for words that arrive later, so no make-room move is needed.
