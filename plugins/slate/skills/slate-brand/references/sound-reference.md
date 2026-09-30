# The sound reference

The brand's sound is the fourth reference: its own palette, and a short proof that shows it, with its levels measured
against the brand's voice.

## The palette

Every brand starts from the owner's starting palette (pads, small soft felt notes, hollow wood clicks and clacks, air, a
sub for the one big landing; no chimes; designed, not stock) and gets its own from its world (slate-sound, palette.md):
- **key and tempo range**: pitched effects sit on the key's pentatonic, so they never clash with the score;
- **materials**: what the effects are made of (the score never plays them);
- **banned timbres**: what the brand never sounds like;
- **the motif**: a few notes that belong to the brand or its character, and the instrument that plays them;
- **instrumentation** for the brand's scores;
- **levels**: where each stem sits against the voice.

In `brand.json` → `sound`: `palette` (overriding the starting palette key by key), `materials`, `banned_timbres`, `key`,
`tempo`, `instrumentation`, `motif`, `reference_levels`.

## The proof

A 10 to 20 second piece of film (often the look test's own picture) with the brand's voice, its sound design and, if the
brand will use one, a short score passage, mixed to the film's targets.
- **Levels measured against the voice**: each stem's loudness relative to the voice, recorded as the brand's reference
  levels. Later films are checked against them (`sound.reference`, `sound.levels`).
- **Made locally**: the proof's effects use Apple's sampled instruments installed on the machine, never redistributed.
- **Approved by ear**, and recorded with its SHA-256 as the brand's sound reference.

## Rules

- Taste stays the owner's: the voice first, sound locked to the picture, one sound per kind of interaction, restraint
  and silence.
- Changing the palette after approval is a perfecting change, measured against the approved proof.
