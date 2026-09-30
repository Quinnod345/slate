# Read time

Text on screen is only useful if it can be read. The rule is counted from the frame the text has **fully settled** (the
end of its entrance), not from its first frame.

## The rule

- **Hold at least max(1.5 s, 0.4 s + 0.3 s per word).** That's about 200 words a minute, plus time to find the text.
- **Numbers and statistics:** at least 2 s after the count lands.
- **Labels of three words or fewer:** at least 1 s.
- **Lines the voice also says:** on screen at least as long as the voice takes to say them, and never gone before the
  voice finishes them.
- **Density:** at most 12 on-screen words per shot; body text at least 28 px at a 1080-line frame; on-screen text about
  40% of what the voice says in the shot, or less.

A cross-check for supers: a slow reader at about 200 words a minute should be able to read it twice (hold at least
max(1.2 s, words × 0.3 s × 2)). When the two disagree, take the longer hold. Captions have their own rule: at most 17
characters a second, 2 lines of 42 (slate-voice).

## Where it's checked

1. **The script** (`slate script build`): the planned hold, from the shot's length minus its entrance and exit, against
   the rule, per shot (slate-story).
2. **The rendered film** (`slate qa readtime`): OCR reads the picture every 0.25 s and times each on-screen statement
   from the frame it's fully built (in order, with its last word) to the frame it's gone. This catches what the script
   can't: a camera that leaves early, a statement that builds slowly, text that's too small to read.

Logo lockups and stylized marks are left to the contact-sheet review: OCR misreads them.

## Fixing a short hold

- **Extend the settled hold inside the shot**: start the next camera move later, or land the text earlier within its
  shot. Don't move word timings or shot boundaries; they're the voice's clock.
- When you retime one move to fix read time, choose its new start and ease so every downstream key position stays
  identical (the camera still reaches the next fold exactly where it did), and nothing after it moves.
- If the hold can't grow, cut words: the density rule is usually what's really broken.
- Run a read-time audit on the first full preview, before the look is locked: moving a line earlier is cheap then and
  expensive after the renders.

## Text that moves

- Don't land a line of type while the layout around it is still moving; the clock starts when everything has settled.
- Small type rushing past a moving camera can't be read and strobes; land detailed content only once the camera has all
  but stopped.
- A statement that rides a camera move across a cut must stay legible across the cut (check the strip of frames either
  side).
