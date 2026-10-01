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
- **The score's own clarity guard** runs next: wherever the score on its own would come within 19 dB of the voice in
  1–5 kHz during a word, its top (above 600 Hz) dips under that word by just enough, at most 20 dB, held 0.15 s either
  side and ramped over 0.3 s, so it moves at a phrase's pace; everywhere else it plays as composed. The mix record
  lists every dip (`score_guard`), and a brand turns it off with `score_guard: false` in its palette's balance.
- **Effect stems** come from `sound/stems/` at the palette's levels against the voice; wood clicks don't duck.
- **One linear gain** takes the whole to the target (−14 LUFS integrated, true peak at or under −1 dBTP, 4× oversampled),
  any limiting as one linked curve on every stem, never a one-pass loudness normalizer (its stems couldn't sum).
- **The output**: `sound/mix/final_mix.wav` at 48 kHz, 24-bit, exactly as long as the picture (never trimmed to the
  shortest stream), and four delivery stems (dialogue, effects, ambience, music) in `sound/mix/stems/` that sum to it
  within one part in a million.

## The brand's balance

The balance gates and the score's guard read the `balance` in the brand's sound palette, laid key by key over the
starting balance (the films' approved mix). A palette that names its `families` replaces them whole.

| Key | What it sets (the starting value) |
|---|---|
| `families` | which cue kinds make each family: accents (tap, land, snap, count, data, stamp, character), textures (type, draw, page, whoosh), swells, impacts; the score is the music stem, the plate the ambience |
| `clarity` | the consonant band (1–5 kHz), the rule (every word 12 dB clear of everything else), the trade's flag and floor (8 and 6 dB), and a family's own floor (`alone_min_db`: the score 19 dB on its own) |
| `score_guard` | the score's clarity guard in `slate mix` (target 19 dB, split at 600 Hz, a dip of 20 dB at most, held 0.15 s, ramped over 0.3 s); `false` turns it off |
| `never_over` | how far under the voice everything stays while it speaks (0 dB: never louder) |
| `texture_air` | the textures' 2–6 kHz at least 20 dB under the voice's own |
| `accents_over_score` | accents and impacts clear the score by 3 dB in their own loudest band, over their loudest 120 ms |
| `levels` | each family's range against the voice: integrated, hit by hit and inside words (the score: −28 to −17 LU integrated, −19 at most inside words) |
| `stops` | how silences are read: the fade that counts as part of a planned stop (20 dB under the music's median, up to 2 s), whether the score's own rests count as planned (`score_rests`), the gap floor (−60 dBFS over 0.5 s), and whether the film opens or ends in silence (`open_silent`: only when the plan says so; `end_silent`: `"always"`, a silent last second whatever the plan, the films' rule and the default, or `"plan"`) |
| `approved_spans` | stretches the owner approved by ear (`{"from", "to"}` in film seconds): a word under the clarity rule there is listed as approved, not failed |

`slate sound balance --words` prints every word's clarity, so a level change can be judged word by word before the
next mix.

**An approved span stays as approved.** A span the owner approved as a clip (the palette balance's `approved_spans`,
naming the approved file and its window) is checked against that clip every time: family by family, ride by ride and by
the score's level (`sound.balance.approved_spans`).

**Hear what the rides did.** `slate mix --listen [N]` writes a listening file of the N hardest-ridden accents, each from
2.5 s before its hit to 3 s after, as mixed before and now, over the film's own picture (black with the same labels when
no master is on the machine). It reads the rides the mix's guards record (sound/mix/rides.json), so until those guards
run it says what it needs.

**A voice change moves the balance.** A voice polish, or any change to approved narration, changes the speech band the
balance measures (the first film's polish gave up about 0.8 dB of 1–5 kHz). Re-run the balance gates and re-solve the
mix's rides; a span pinned to rides approved before the polish can't keep them.

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
