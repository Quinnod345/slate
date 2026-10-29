# Read time

Text on screen is only useful if it can be read. The rule is counted from the frame the text has **fully settled** (the
end of its entrance), not from its first frame.

## The rule

- **Hold at least max(1.5 s, 0.4 s + 0.3 s per word).** That's about 200 words a minute, plus time to find the text.
- **Numbers and statistics:** at least 2 s after the count lands.
- **Labels of three words or fewer:** declare `reading.labels: true` with a reason. Each authored label uses the
  default hold `max(1.0 s, 0.4 s + 0.3 s per word)`: a three-word label still needs 1.3 s. Longer labels keep the
  usual floor. The existing format or script `read_time.floor`, `base` and `per_word` policy takes precedence;
  the inherited generic 1.5 s floor alone is not an explicit label override.
- **Lines the voice also says:** declare `reading.voice_matched` with a reason. The first qualifying complete
  readable run must meet the 1.5 s floor and cover the matching phrase's measured final word on the film clock.
  A statement that is also a declared label uses `max(1.5 s, the effective read_time.floor)`; the label's shorter
  default cannot shorten a spoken line. Progressive builds need not show the complete line throughout the phrase.
- **Density:** at most 12 on-screen words per shot; body text at least 28 px at a 1080-line frame; on-screen text about
  40% of what the voice says in the shot, or less. Text smaller than body size is only for labels of three words or
  fewer (meta times, dates, column heads, the sample-data label).
- **Judge type size by its measured cap height at full resolution**, not by the font size in code: 42 px names
  measured 26 px capitals.

A cross-check for supers: a slow reader at about 200 words a minute should be able to read it twice (hold at least
max(1.2 s, words × 0.3 s × 2)). When the two disagree, take the longer hold. Captions have their own rule: at most 17
characters a second, 2 lines of 42 (slate-voice).

## Where it's checked

1. **The script** (`slate script build`): the planned hold, from the shot's length minus its entrance and exit, against
   the rule, per shot (slate-story). Declared labels use the same resolved reading policy; a planned number hold
   does not prove where its rendered count lands.
2. **The rendered film** (`slate qa readtime`): OCR reads the picture every 0.25 s and times each on-screen statement
   from the frame it's fully built (in order, with its last word) to the frame it's gone. This catches what the script
   can't: a camera that leaves early, a statement that builds slowly, text that's too small to read. Declared
   read-along lines also need measured word timing and voice placement; missing evidence is UNCHECKED.

Logo lockups and stylized marks are left to the contact-sheet review: OCR misreads them.

## Declared counts

For a readable animated number, bind its exact final `on_screen` part to the event ids that count it:

```json
{"id": "S04", "on_screen": "72% complete", "reading": {"count_events": [{"text": "72% complete", "events": ["progress-count"]}]}}
```

Each binding has exactly `text` and `events`: one literal final display and a nonempty list of unique event ids.
Preserve its sign, decimal point, currency and percent marks. The metadata grants no reading exemption and needs no
reason by itself; `labels`, `voice_matched` and `not_for_reading` still need their own reason.

The landing comes from the owning act's canonical `timing/visual_events/actN.json`, with each unique `kind: count`
event belonging to that placed shot. Its finite `t`, `start` and positive `dur` are film seconds, not shot-local
estimates; `t` must agree with `start + dur` and stay within its owning act, each within half a film frame.
Its owning shot retains the existing clock tolerance. All named events must resolve before the latest landing is used. Another shot's count, an intermediate value or a similar OCR number cannot supply the proof.

The final display must hold continuously for 2 s from the later of that landing and its first complete, legible
onset. The check uses the first readable run that reaches the landing, within the owning shot, with one OCR sample
of exit uncertainty. A late settle cannot borrow earlier count time, and a later reappearance cannot erase a short
first exposure. The existing opacity and motion limits still apply.

Missing or malformed canonical event, clock or exact picture evidence is UNCHECKED. A malformed binding or one that
names no unique literal display fails; an independently measured short hold still fails even when other evidence is
unknown. A number alone does not declare an animation: ordinary static numbers keep their existing check, and timers
or nonnumeric motion gain no count-reading obligation merely from an event's kind. A valid whole-shot
`not_for_reading: true` declaration excludes the surface from count landing and coverage requirements; malformed or
stray metadata remains a problem. A listed exemption applies only to its exact bound display.

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
