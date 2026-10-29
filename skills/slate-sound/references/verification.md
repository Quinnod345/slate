# Verifying the sound

`slate verify` runs the sound gates on the mix (the sixteen core gates and the seven balance gates) and records them in
`qa/gates.json` (and `sound/mix/verify.json`). It exits 0 only when every gate passes, and a delivery needs every one.
`slate verify loudness gap` runs just those.

## The gates

| Gate | Passes when |
|---|---|
| `sound.loudness` | the mix is on target (−14 LUFS within 0.5 LU by default) with the true peak at or under −1 dBTP, by a BS.1770 meter cross-checked against ffmpeg's |
| `sound.stems_sum` | the four delivery stems add up to the mix, sample for sample, within one part in a million |
| `sound.gap` | no half-second window of music under −60 dBFS outside the planned stops |
| `sound.voice_sync` | every take sits exactly on its cue (found again in the dialogue stem by cross-correlation) |
| `sound.cue_sync` | every effect's audible anchor is within one frame of its beat |
| `sound.repeats` | the measured repeats match the palette's interaction rules; the starter rejects consecutive identical files, while a sonic logo or marked bookend is intentional |
| `sound.glides` | no forbidden sustained pitch glide is measured; a palette with both `rules.no_glides` and `rules.no_slide_ups` false permits glides |
| `sound.drone` | no forbidden room-tone bed is measured; `rules.no_drone: false` permits a bed |
| `sound.levels` | each stem sits at its palette level against the voice, within tolerance |
| `sound.reference` | the mix matches the brand's approved sound reference (UNCHECKED when there isn't one), and effects more than 25% brighter than the reference's (spectral centroid, or the share above 2 kHz) wait for a person |
| `sound.clarity` | the voice stays clear: nothing louder than it, no bright noise under the words |
| `sound.music_licence` | the score's licence covers the destination (broadcast formats need an Enterprise music licence) |
| `sound.voice_limiter` | the voice-only limiter touches under 5% of the speech; past that it's shaping the read |
| `sound.key` | every pitched note in the UI and tonal stems is on the brand key's pentatonic (each note's pitch over its first 60 ms); a stem with more than half its notes on one or two pitches waits for a person |
| `sound.timbre` | measured families meet `timbre.centroid_max_hz`, `no_chimes` and `no_metallic`; the warm starter uses 1.5 kHz, true and true, while null removes the centroid ceiling |
| `sound.mix_words` | Whisper on the delivered mix hears the script word for word, after only the normalization a listener can't hear; a word heard over the music where the voice is quiet is set aside, never counted |
| `sound.balance.clarity` | every word at least 12 dB clear of everything else in 1–5 kHz, and the score on its own 19 dB clear; a word under a half-ridden accent is the owner's trade: listed, a person listens under 8 dB, and under 6 dB it fails |
| `sound.balance.never_over` | while the voice speaks, nothing is louder than it: each family, all the effects together and everything else together |
| `sound.balance.texture_air` | the textures' 2–6 kHz sits at least 20 dB under the voice's own |
| `sound.balance.accents` | every accent and impact clears the score by at least 3 dB in one of its own loudest bands, over its loudest 120 ms |
| `sound.balance.levels` | each family sits in its range against the voice: integrated, its hits' median and loudest, and inside words |
| `sound.balance.approved_spans` | every span the owner approved as a clip is kept exactly as approved: the film's mix over the span against the approved clip, family by family, ride by ride and by the score's level |
| `sound.planned_stops` | the music's quiet stretches are all planned (each stop widened by the music's own fade, the score's own rests counted), and the film opens out of silence or ends in it only when its plan says so |

The seven balance gates measure word by word against the voice (phrase by phrase before word timings exist), from the
brand's sound palette (`sound.palette.balance`, mix.md). `slate sound balance` runs them on their own, and
`--words` prints every word's clarity. A film whose effects arrive as one stem has the effect families' rules
UNCHECKED until the effects engine writes a stem per family; a score-and-voice film is measured in full.

The effective palette starts with the warm preset, takes the brand's settings and then the film's `sound.palette`
overrides. Explicit material and banned-timbre lists replace the starter lists. A configured timbre allowance retains
the detector flags in the report; it does not erase evidence, approve a source-bound clip or prove the detector correct.
Follow the latest authorized sound direction and preserve its earlier proof and feedback for comparison.

## How sync is measured

- Each cue is rendered alone and its **anchor** measured the way the ear hears it: a strike's first sample within 12 dB
  of its peak; paper's first audible sound; for air and swells, their loudest 20 or 50 ms; for a lift or a drawn line,
  where it ends.
- A cue passes when its anchor is within one frame of its beat. On the first films the worst of hundreds of anchors was
  about 24 ms.
- A pencil stroke's tip tick once led its letter by up to 200 ms, and a broad swell's peak sat almost a second off: that's
  why designed sounds get designed envelopes, placed by their perceptual onset.
- Documented exceptions (a character's anticipation, designed to land a few frames before its word) are listed with a
  reason, and still locked to the picture.

## Statuses

Only PASS counts. UNCHECKED means the gate couldn't run and says what to do (mix first, approve a sound reference);
replayed audio in tests is UNCHECKED, never passed. `slate qa` includes the sound gates in the film's full run (slate-qa).

## By ear, too

The gates protect an approved sound; they don't approve one. Proofs go to the owner in the packet, as a preview with the
picture and a labelled spectrogram if it helps, and the owner decides.
