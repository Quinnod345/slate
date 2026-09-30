# Writing the voice-over and the words on screen

The voice carries the story; the screen carries the keywords; the picture carries the rest. These rules are checked by
the script gates, and they come from the films' measured failures.

## Pace

- **At most 2.6 words a second, per shot.** Faster reads as a rush and leaves no room for the picture.
- Plan shot lengths from the words. A deliberate narrator reads 1.5 to 3.2 words a second; the real takes re-time the
  shots after voicing (`slate script build --from-takes`), so the plan only has to be close.
- Leave air: a read starts a short lead into its shot and ends a short tail before the cut. Heavy lines get their air
  from the edit (a longer hold), not from pause tags.

## Read time

- Every on-screen statement stays on screen, once fully built, for at least max(1.5 s, 0.4 s + 0.3 s per word).
- A number holds at least 2 s after its count lands; a label of three words or fewer, at least 1 s.
- A line the voice also says stays up at least as long as the voice takes to say it, and never leaves before the voice
  finishes it.
- The master is checked by OCR as well: an on-screen line that looked long enough in the script can still fall short if
  the camera leaves early. Fix that in the picture (slate-motion, read-time.md).

## Density

- At most 12 on-screen words per shot (headline and labels together).
- On-screen text is keywords: about 40% of what the voice says in that shot, or less. Never a transcript.
- Body text on screen is at least 28 px at a 1080-line frame (scale with the frame); the kit's type scale does this.

## Numbers, symbols and brand words

- Write numbers the way the film shows them and let the clock work out how they're said: word lookups know that "2026"
  is said "twenty twenty-six" and "&" is said "and", so beats can key to them.
- Every number said or shown needs a source (truth packs).
- Put the brand's own words (names, product terms) in the brand's lexicon; the voice checks confirm each one is heard in
  full on every take.

## The words are final before voicing

- Direction (warmth, a smile, a pause) is added later as audio tags that never change a word. A tagged line must strip
  back to the script exactly (slate-voice).
- If a line doesn't land when voiced, change the direction first; change the words only on purpose, in the script, and
  rebuild.
- When the voice and the product disagree on a figure, re-voice the line to match the product.

## A character's own words

When a product character or assistant speaks or writes on screen, its words follow the product's own content rules (for
example: lead with the answer, no dashes or emoji, every number from the report). Describe it only with capabilities
the owner treats as true (truth.md).

## Style that helps the voice

- Short sentences, one idea each; the stress where the meaning is.
- Punctuation is direction: a full stop gives a beat, an ellipsis a trailing pause, a question lifts.
- Avoid words that trip a synthetic voice: stacked consonants at a line's end, homographs whose reading changes the
  meaning, abbreviations the voice would spell out.
- If the avoid-ai-writing skill is available, run the script through it for phrasing that reads as machine-written.
