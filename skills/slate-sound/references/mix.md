# The mix

`slate mix` builds the delivery mix: the voice from its cue sheet, the score under the voice with ducking, any effect
stems at their palette levels, one fixed gain to the film's loudness target, and four delivery stems that sum to it.

## The balance hierarchy

Balance a film's sound as a deliberate hierarchy, not one level for everything:
1. **The narration is always fully intelligible.** Nothing covers a word.
2. **Small tactile accents** (wood clicks, knocks, felt notes) can stick out a little: that's what makes sound locked to
   picture feel intentional.
3. **Noise textures** (whooshes, swishes, air, reverse bursts, write-on scratch) and **tonal sweeps** (risers, pad swells,
   builds) sit well under the voice and duck harder while it speaks.
4. **Bright noise stays out from under words**: roughly 2 to 6 kHz is where consonants live.
5. **No effect is ever louder than the voice**, at any moment.
6. **The tonal bed** sits about 20 LU under the voice, and pads under speech are low-passed at or below about 1.4 kHz, so
   harmony stays out of the words.

The owner judges by ear; the gates check every level by measurement (the levels and clarity gates).

## What the mix does

```
slate mix                       # voice, score, effect stems
slate mix --no-score            # voice and effects only
slate mix --calibrate           # trim each effect stem to its palette level against the voice, and report the trims
slate mix --under 17 --duck 5   # the score's level under the voice and its ducking, for this mix
```

- **The voice** is placed from `timing/vo_cues.json`, each take levelled by its voiced loudness, protected by a
  zero-latency limiter on the voice alone (a limiter with latency once moved the voice 240 samples late).
- **The score** sits under the voice and is ducked under every word by a follower on the voice.
- **Effect stems** come from `sound/stems/` at the palette's levels against the voice; wood clicks don't duck.
- **One linear gain** takes the whole to the target (−14 LUFS integrated, true peak at or under −1 dBTP, 4× oversampled),
  any limiting as one linked curve on every stem, never a one-pass loudness normalizer (its stems couldn't sum).
- **The output**: `sound/mix/final_mix.wav` at 48 kHz, 24-bit, exactly as long as the picture (never trimmed to the
  shortest stream), and four delivery stems (dialogue, effects, ambience, music) in `sound/mix/stems/` that sum to it
  within one part in a million.

## Effect stems and their manifest

Stems made outside the effects engine (in a DAW, or by hand) go in `sound/stems/`, with `sound/stems/stems.json` naming
each role's file and whether it ducks:

```json
{"stems": {"ui": {"file": "ui.wav", "duck": false}, "fx": {"file": "fx.wav", "duck": true}, "amb": "amb.wav"}}
```

Roles: `ui`, `fx`, `verb` (they sum to the effects stem), `tonal`, `tverb` (music), `amb` (ambience). Without a
manifest, the mix takes whichever role files are present. Manual polish in a DAW is fine; the engine's timing stays the
reference.

## Levels and taste

- The approved proof put the effects about 11 LU and the UI clicks about 17 LU under the voice, clearly audible. Those
  are the starting palette's levels; a brand's own come from its sound reference.
- Calibrate a new design to the last approved level before comparing, so an A/B hears the design, not the volume.
- Don't bury the effects: a mix that put them 25 to 28 LU under the voice lost the thing the owner loved.

## After the mix

`slate verify` runs the sound gates (verification.md). Loudness targets differ by destination (platforms normalize
differently; −14 LUFS is a common web target, not a documented rule for every platform); the format sets the film's
target, and delivery can derive others.
