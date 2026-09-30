# Verifying the sound

`slate verify` runs the sound gates on the mix and records them in `qa/gates.json` (and `sound/mix/verify.json`). It
exits 0 only when every gate passes. `slate verify loudness gap` runs just those.

## The gates

| Gate | Passes when |
|---|---|
| `sound.loudness` | the mix is on target (−14 LUFS within 0.5 LU by default) with the true peak at or under −1 dBTP, by a BS.1770 meter cross-checked against ffmpeg's |
| `sound.stems_sum` | the four delivery stems add up to the mix, sample for sample, within one part in a million |
| `sound.gap` | no half-second window of music under −60 dBFS outside the planned stops |
| `sound.voice_sync` | every take sits exactly on its cue (found again in the dialogue stem by cross-correlation) |
| `sound.cue_sync` | every effect's audible anchor is within one frame of its beat |
| `sound.repeats` | no identical sound plays twice in a row |
| `sound.glides` | no sustained tone slides in pitch |
| `sound.drone` | no room-tone bed under the effects |
| `sound.levels` | each stem sits at its palette level against the voice, within tolerance |
| `sound.reference` | the mix matches the brand's approved sound reference (UNCHECKED when there isn't one) |
| `sound.clarity` | the voice stays clear: nothing louder than it, no bright noise under the words |
| `sound.music_licence` | the score's licence covers the destination (broadcast formats need an Enterprise music licence) |

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
