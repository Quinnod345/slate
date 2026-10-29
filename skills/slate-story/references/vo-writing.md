# Writing the voice-over and the words on screen

The earlier product-film recipe gave the voice the story, the screen the keywords and the picture the rest. Choose
those roles for the current brief. The script gates check the configured pace, reading and density policies; their
defaults came from measured failures and do not prohibit another supported policy.

## Pace

- **The starting pace is at most 2.6 words a second per shot.** A faster read rushed the earlier film. The format and
  `script.timing` can set another pace; measure the actual read instead of treating a planned rate as voice evidence.
- Plan shot lengths from the words. A deliberate narrator reads 1.5 to 3.2 words a second; the real takes re-time the
  shots after voicing (`slate script build --from-takes`), so the plan only has to be close.
- Leave air: a read starts a short lead into its shot and ends a short tail before the cut. Heavy lines get their air
  from the edit (a longer hold), not from pause tags.

## Read time

- The starting reading policy holds statements, once fully built, for max(1.5 s, 0.4 s + 0.3 s per word). Format
  settings and `script.rules.read_time` can choose the policy. A declared label, voice-matched line or non-reading text
  uses its explicit gate treatment and reason where required, not an undeclared exemption.
- A number holds at least 2 s after its count lands; a label of three words or fewer, at least 1 s.
- A line the voice also says stays up at least as long as the voice takes to say it, and never leaves before the voice
  finishes it.
- The master is checked by OCR as well: an on-screen line that looked long enough in the script can still fall short if
  the camera leaves early. Fix that in the picture (slate-motion, read-time.md).

## Density

- The starting limits are 12 on-screen words per shot and about 40% of its VO word count. Format settings and
  `script.rules.on_screen_max_words` / `on_screen_max_vo_ratio` can change these limits.
- Keywords, a transcript or another treatment can serve the brief. Voice-matched lines and small labels have explicit
  gate handling; choose it accurately and verify the rendered reading time.
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
