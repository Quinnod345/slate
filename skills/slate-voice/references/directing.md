# Directing the read: settings, context and timing

## Settings

eleven_v4 has two settings: **stability** and **similarity**. Style and speed aren't available on it, and neither is
SSML.
- **Stability** trades consistency for expressiveness. Too low gives odd, random, rushed reads; too high goes monotone.
  Start at 0.5 and change it only when a probe earns the change: on the first films a looser 0.35 changed the waveform
  and nothing else (the tags didn't land better).
- **Similarity** raises adherence to the reference voice at some cost to naturalness, and can reproduce flaws in the
  source recording. The voice's stored value (often about 0.75) is the start.

## Context

- Each line is voiced with its **previous and next lines** as context, so the narration flows as one read. The context
  carries the neighbours' **plain words**, with tags stripped: a tag belongs to the line it directs. (Tagged context was
  tested: harmless, and no better.)
- Request ids of neighbouring takes can stitch generations more tightly than text, within a couple of hours; the voice
  stage handles it.
- Context can leak: the next line's first sound sometimes appears at the end of a take (takes-and-revoicing.md).

## Timing comes from the text

On eleven_v4, identical text and context gave sample-identical lengths across seeds and stability values. So:
- **change pacing through the text**: punctuation, tags, line structure;
- **regenerating** changes texture and inflection, not timing;
- **air after heavy lines comes from the edit** (a longer hold in the shot), not from pause tags; use pause tags
  sparingly.

## Consistency across a film

- One small tag vocabulary, the same settings and the same context rule on every line.
- The model can shift over time as the vendor retrains it: record the seed and request id of every take, so a re-test
  is comparable, and pin the model per film.
- Tagged takes can come out 1 to 2 dB quieter than untagged ones; the mix levels each take by its voiced loudness, and
  A/B clips are level-matched.

## Punctuation is direction

- A full stop gives a beat; an ellipsis adds a trailing pause and weight; a line break gives a longer beat.
- A dash cuts the speaker off; an exclamation mark adds intensity; capitals add emphasis.
- A question lifts: don't end a statement with one.

## When a line doesn't land

1. Listen to what's wrong: pace, pitch, a word, an emotion.
2. Swap the tag (or move it to the exact clause) and regenerate. Don't rewrite the words.
3. If the words must change, change them in the script on purpose, rebuild, and re-voice with neighbours.
