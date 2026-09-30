# Sound palettes

A palette says what a brand's films sound like: which material plays each kind of beat, where each sits against the
voice, which instruments carry the tonal roles, what the score must never play, and what's banned. Slate ships one
starting palette (the approved proof's), and each brand's pack overrides it key by key (`sound.palette` in brand.json).

## The starting palette

| Kind of beat | Family | Stem |
|---|---|---|
| tap (a row, check, chip or button) | wood block | UI |
| type (text writing on) | soft air | effects |
| land (a card, sheet or panel landing) | wood knock | UI |
| snap (a state flips) | clave | UI |
| count (a numeral counting) | tuned wood clicks | UI |
| draw (a line or chart drawing) | air on the draw's ease | effects |
| data (a value, bar or marker lands) | a small felt note | tonal |
| page (a sheet slides, deals or turns) | swish | effects |
| stamp (a group completes, a seal sets) | temple block with a felt note | UI |
| whoosh (a view carries into the next) | air | effects |
| swell (the picture fills or opens) | a sampled string pad | tonal |
| character (the brand's mark gestures) | small felt notes, the motif | tonal |
| impact (an act's one big landing) | a sub with a hollow temple knock | effects |

**Levels against the voice** (integrated, measured on the approved proof's stems): UI clicks about 17 LU under, effects
about 11 LU under, within about 2 LU. The voice is levelled by the loudness of its voiced part; every other cue by its
peak.

**Ducking under speech:** tonal and ambience about 6 dB, effects about 3 dB, reverb about 4 dB; the UI clicks stay crisp.

**Rules the palette carries:** no glides, no slide-ups, no drone, one sound per kind, no sound shared between kinds, no
identical repeats, every sound within a frame of its beat, tonal roles on sampled instruments.

**Materials** the effects are made of (wood block, temple block, clave, knock, rim, felt marimba and the like): the
composed score never plays them.

**Pads are space**: low, slowly breathing chords hold the harmony under whole passages and open up at the big moments.
Under the voice they stay low-passed, so the harmony stays out of the words.

**Designed, not generated**: generated sound-effect and music models only ever fill a specific gap, and say so; the
film's effects are designed from its palette.

**Banned timbres:** bells, chimes, glockenspiel, celesta, crotales, tubular bells, vibraphone, music box, metallic hits,
glass, buzzers.

## A brand's palette

Onboarding (slate-brand) makes it, from the brand's own world:
- its **key and tempo range** (every pitched effect sits on the key's pentatonic, so nothing clashes with the score, and
  nothing sits on a leading tone);
- its **materials** and **banned timbres**;
- its **motif** (a few notes that belong to the brand or its character) and the instrument that plays it;
- its **instrumentation** for the score;
- its **reference levels**, measured from its approved 10 to 20 second sound reference against its voice.

The owner's palette is where every brand starts, and a brand's own world decides where it goes.

## Instruments

Tonal roles (small notes, pads) play on Apple's sampled instruments, found in place on the machine (installed with
GarageBand's sound library and Logic) and never redistributed. If they're missing, `slate doctor` fails and says what to
download; there's no synthesized stand-in for a tonal role. Plate ambience from a generated shot can serve as a bed
(it came back as usable wind and air on the first films), high-passed and well under the voice.

## Changing a palette

A palette approved with a brand's sound reference changes only on purpose, with the owner's approval, and is checked
against the reference by measurement (levels against the voice, brightness, sync). Notes on it are perfecting notes.
