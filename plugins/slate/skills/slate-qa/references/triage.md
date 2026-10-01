# Triage

`slate qa triage` lists everything that isn't PASS, most severe first (FAIL, STALE, UNCHECKED, REVIEW), each with its
evidence and the one next step. Gates that never ran count as unchecked. Work down the list; fix causes, not symptoms.

## Glitch flags

The glitch gate scans every rendered act for one- and two-frame jumps. A scanner flags anything fast, so:
- **flags within 3 frames of a planned beat are skipped** (listed separately by act), because a planned landing is
  supposed to move;
- **the rest are open** until they're fixed or a person accepts them.

Known false-positive classes, to recognize (and then accept with a reason, not ignore):
- count-ups (the digits change every frame);
- fast write-ons;
- text moving further than its stroke width in a frame;
- periodic strokes moving about half their pitch (double letters, repeated digits);
- deliberate hard edges;
- a UI state cut under motion blur, which renders as one mixed frame (the shutter's samples straddle the cut): expected;
- a one-off renderer flake that doesn't reproduce on a re-render;
- any crisp edge moving faster than about 4 px a frame, and any zoom faster than about 0.5% a frame. On a clean act
  every glitch flag sits inside a scheduled move, which the stability report's motion triage confirms
  (`slate qa stability`, stability.md).

Two things a one- and two-frame scan can't see:
- **a pop that stays**: a card that appears in one frame and remains is unlike the frame before but like the frame
  after, so it passes; look for steps (a frame unlike its predecessor that then holds) as well as glitches;
- **fast motion hidden by blur**: the 16-sample master blurs fast moves, so a one-sample preview flags more of them.
  Compare like with like when judging whether a fix reduced the flags.

## When a pop is found

- **Before calling a flag a fault, check it against a synthetic perfect render** of the same move (the element drawn
  exactly on its path). When the perfect render flags the same tiles, the flag is the design. One half-pixel step was a
  button's own press (it shrinks 3% about its centre on the brand's snap ease), lifting its bottom edge 0.77 px on top
  of the camera's slowing tail; the render followed that path within 0.13 px. Smoothing it would have meant changing
  the press's ease, so it stayed in the act's baseline, with that reason.
- **Take the fault's flags out of the baseline before you re-render the fix**:
  `slate qa stability baseline --act N --drop F[-F] [--drop-class C] --by NAME --reason "…"`. A baseline that still
  holds the fault's flags would hide it in the new render, so the new render couldn't prove the fix.
- **Scan the whole act frame by frame** for other one-frame jumps and fix them together.
- **Prove unchanged frames are bit-identical** before and after the fix.
- The lead reviews stills at the changed frames and checks each fix in the builder's preview, then re-runs the shot's
  checks when the final render lands and updates the shot's status to fixed → pass with the measured evidence.
- **Confirm any flicker at full size** before fixing it: half-scale previews can flicker a level or two on slowly drifting
  cards, because the browser picks a different raster scale.
- **And the reverse**: a half-scale, one-sample preview can hide jitter that the 16-sample final shows (type stepping a
  pixel at a time under a slow move). Check risky moves in short 16-sample windows at full size before approving a
  final.
- **Review every transition of a product surface at 2 to 4 frame steps** as well: a one-frame scan can't see a card that
  stays wrong.
- **Pixel-diff every hand-over** at full resolution (a flat card to its 3D twin, a morph to its final component, frames at
  rest), and match shadows to the perspective before the switch.

## Read time

Run a read-time audit on the first full preview, and move lines earlier or give holds more time before the look is
locked; fixing it after the renders costs a re-render (slate-motion, read-time.md).

## Accepting a REVIEW item

Only a person accepts, with a reason:

```
slate qa triage --accept GATE:ITEM --by <name> --reason "<why>"
```

The acceptance binds to the hashes of the exact files judged; re-render, and it has to be judged again. An agent can't
accept a REVIEW item on an autonomous film: the film parks for the owner instead.

## UNCHECKED items

Each one's evidence says what to do: install a tool (`slate doctor` names it), make the missing input, approve a
reference. Never mark one as passed, and never swap in a different engine to make it pass.

## STALE items

Something changed since the check. Re-run the gate; if it now fails, the change broke something, and that's the finding.
