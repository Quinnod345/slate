# Sound palettes

A palette says what a brand's films sound like: which material plays each kind of beat, where each sits against the
voice, which instruments carry the tonal roles, what the score must never play, and what's banned. Slate ships one
starting palette (the approved proof's), and each brand's pack overrides it key by key (`sound.palette` in brand.json).
The film's `sound.palette` applies after the brand for that film. This is a contextual starting sound, not a universal
style rule; the latest brief and authorized listening feedback govern creative revisions.

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
| impact (a designed big landing) | a sub with a hollow temple knock | effects |

**Levels against the voice** (integrated, measured on the approved proof's stems): UI clicks about 17 LU under, effects
about 11 LU under, within about 2 LU. The voice is levelled by the loudness of its voiced part; every other cue by its
peak.

**Ducking under speech:** tonal and ambience about 6 dB, effects about 3 dB, reverb about 4 dB; the UI clicks stay crisp.

**Rules the starter carries:** no glides, no slide-ups, no drone, one sound per kind, no sound shared between kinds, no
identical repeats, every sound within a frame of its beat, tonal roles on sampled instruments.

The chosen palette can set `rules.no_glides`, `no_slide_ups`, `no_drone`, `one_sound_per_kind`,
`no_sound_shared_by_kinds` and `no_identical_repeats`. Permit glides by setting both glide rules false. These controls
change the applicable creative policy; cue timing, readable narration and required source evidence still need checks.

**Materials** in this starter (wood block, temple block, clave, knock, rim, felt marimba and the like) stay out of its
composed score to keep roles distinct. Choose the current score's instrumentation with the sound direction.

**Pads are space**: low, slowly breathing chords hold the harmony under whole passages and open up at the big moments.
Under the voice they stay low-passed, so the harmony stays out of the words.

**Design the source choice:** the earlier effects recipe used the palette's sampled materials and generated audio for
specific gaps. Another requested approach can use generated or stock sources within its authorization and budget;
record their provenance and inspect the result. The current renderer still requires its declared samples.

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

Explicit `materials` and `banned_timbres` lists replace the starter lists, including an intentionally empty ban list.
A list in `sound.palette` takes priority over the top-level shorthand. A palette can also choose its measured timbre
limits with `sound.palette.timbre`: `centroid_max_hz` (null for no ceiling), `no_chimes`, and `no_metallic`. The warm
starter uses 1500 Hz, true and true. Configure those choices for the intended brand sound instead of inheriting an
unwanted ban. The timbre report keeps detector flags even when a permitted instrument does not fail the palette
gate; passing the configured limits is not listening approval or proof that a classifier is always right.

The renderer reads each kind's `type`, `gain`, `params` and `balance_family` alongside its material family, stem and
anchor. For example, a wood kind uses `type: "wood"` and `params.kind` for its material; a sampled felt note uses
`type: "snote"` with its role and pitch. Composite stamps and impacts use `type: "layered"` with their child voices in
`params.layers`. A page uses the installed sampled swish takes, with rate variation measured before placement.
Explicit kind params override the starting data. Put chord pitches in `params.notes`; the top-level cue `notes` field
is for independently anchored gesture hits. A character's `sound.motif` gives `notes` or a `gestures` mapping, so its
sound is authored by the brand. Processing families are accents, textures, swells, impacts and plate; authored musical
beds use the score family.

## Instruments

Tonal roles (small notes, pads) play on Apple's sampled instruments, found in place on the machine (installed with
GarageBand's sound library and Logic) and never redistributed. If they're missing, `slate doctor` fails and says what to
download; there's no synthesized stand-in for a tonal role. Plate ambience from a generated shot can serve as a bed
(it came back as usable wind and air on the first films), high-passed and well under the voice.

## Changing a palette

A palette changes on purpose under the current brief and authorized feedback. A requested redesign is work already
authorized, not another request for approval of that same creative choice. Keep the original palette, listening notes
and revision as history, and compare levels, brightness, sync and the intended feeling. Required approval of a new
source-bound sound reference or delivered clip remains distinct from permission to explore; never overwrite an
approved source or mark the revised result approved without its own evidence.
