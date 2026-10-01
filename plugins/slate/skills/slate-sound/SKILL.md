---
name: slate-sound
description: Design, score, mix and verify the sound of a Slate film. Covers the approved starting palette (pads, small soft felt notes, hollow wood clicks and clacks, no chimes) and each brand's own palette, spotting sound from the picture's events with one deliberate sound per kind of interaction, composed scores on Eleven Music planned per act and assembled by measurement, the delivery mix and its balance against the voice, and the sound gates. The designed effects engine waits for the owner's approval of proof 3. Use when a Slate film needs its score planned, composed or assembled, its mix built or rebalanced, its sound verified, a brand's sound palette defined, or a question answered about how a film should sound.
---

# slate-sound

Sound is designed, not stock, and it's locked to the picture: every beat the eye follows lands within a frame of its
sound, and the voice is always understood. The bar is the approved proof: "intentional", noticeable without being too
loud, and matching exactly what happens on screen.

This is stage 9 of the production. Its inputs are the voice's placed takes (`timing/vo_cues.json`), the picture's beats
(`timing/visual_events/act<N>.json`, from slate-build) and the brand's sound palette.

## 1. Taste and the starting palette

- **Pads, small soft felt notes, and hollow wood** (woodblock, temple block, clave, knock, rim), with air for moves and
  a sub for the one big landing. **No chimes, bells, metal, glass or buzzers.**
- **Designed, not stock.** Tonal roles play on real sampled instruments; a synthesized tonal layer sounded cheap.
- **Never glide a sustained tone's pitch** (it reads as a siren); change notes by crossfading held notes.
- **No room-tone drone** under the effects.
- **One deliberate sound per kind of interaction**, the same across a brand's films, with natural variation between
  repeats: never the same hit twice in a row.
- **Restraint and silence**: only what the eye follows makes a sound; a held breath before a landing is silence on
  purpose.

That palette (proof 2's, the approved proof) is the **starting point**. Each brand gets its own palette during
onboarding: its key and tempo, its materials, its banned timbres, its motif, and its levels measured against its
voice (slate-brand). See [taste](references/taste.md) and [palette](references/palette.md).

## 2. Balance: the voice first

A mix is a hierarchy, not one level for everything:
- the narration is always fully intelligible;
- small tactile accents (wood clicks, knocks, felt notes) may stick out a little;
- noise textures (whooshes, swishes, air, reverse bursts, write-on scratch) and tonal sweeps (risers, pad swells) sit
  well under the voice and duck harder while it speaks;
- bright noise (roughly 2 to 6 kHz, where consonants live) stays out from under words;
- no effect is ever louder than the voice.

The approved proof put its effects about 11 LU and its UI clicks about 17 LU under the voice, clearly audible. A later
mix buried them 25 to 28 LU under, and they stopped standing out: that was a failure too. See [mix](references/mix.md).

## 3. Spotting: tell the sound what each beat is

The picture's events name each beat's **kind** (tap, type, land, snap, count, draw, data, page, stamp, whoosh, swell,
character, impact) and **size**. Each kind has its own family of sounds; bursts of beats become one gesture; an act has
at most one impact; a big impact gets an echo on the score's tempo; every sound's attack sits on its beat's frame. See
[spotting](references/spotting.md) and [families](references/families.md).

## 4. The effects engine waits for proof 3

The designed effects engine (spotting beats to cues and rendering them to stems) is built only after the owner approves
proof 3, the perfected proof of the approved design: `slate sfx` refuses until that approval is recorded, and
`slate sfx status` shows the gate. Until then a film's sound is its score, its voice and its brand's onboarding sound
reference, through the mix; effect stems made another way (by hand, in a DAW) can be supplied as stems. See
[effects engine](references/effects-engine.md).

## 5. The score

A composed score is part of most films: Eleven Music on its best model, composed per act and assembled by measurement,
never playing the effects' own materials.

```
slate score plan          # one composition plan per act: 3 s pre-rolls, parts under 90 s, blocks of 3 s or more
slate score compose       # composes the missing takes (spends; estimated and budget-checked first)
slate score assemble      # measured placement, equal-power joins, freeze repairs, the gap check
slate score qa            # silence and noise in the takes and the score
```

`sound/score/directions.json` (hand-edited) says where the music does something special: stops, blooms on a landing,
splits at a story turn, styles. The playbook, and why each rule exists: [score](references/score.md).

## 6. The mix

```
slate mix                 # voice, score and any effect stems, mixed to the film's targets
slate mix --calibrate     # trim each effect stem to its palette level against the voice first
slate verify              # the sound gates, the balance gates included
slate sound balance       # the balance on its own: every word clear of the score and effects, each family in place
```

The balance gates (`sound.balance.*` and `sound.planned_stops`) measure the mix word by word against the voice: every
word at least 12 dB clear of everything else in the consonant band, nothing louder than the voice while it speaks,
textures and accents where the brand's palette puts them. A delivery needs them to pass like every other sound gate.

The mix places the voice from its cue sheet, sets the score under the voice with ducking, sets effect stems at the
palette's levels, and takes the whole to −14 LUFS with the true peak at or under −1 dBTP, in 48 kHz, 24-bit, exactly as
long as the picture. Four delivery stems (dialogue, effects, ambience, music) sum to the mix sample for sample. Manual
polish in a DAW is allowed, but the engine's timing stays the reference. See [mix](references/mix.md).

## 7. Verification

`slate verify` runs twelve gates: loudness, stems summing, the music gap, voice sync, cue sync, repeats, glides, drone,
levels against the palette, the brand's sound reference, clarity under the voice, and the music licence for the
destination. Only PASS counts; a gate that can't run (no approved sound reference, say) is UNCHECKED. See
[verification](references/verification.md).

## 8. Perfect, don't replace

When the owner approves a sound and adds notes, apply the notes to that exact sound, and keep its levels measurable
against the approved version. The films' sound once went off the rails exactly here: small notes (a cheap-sounding
tonal layer, glides that read as a siren, more variation wanted) were answered with a new vocabulary and a new layer,
and the effects ended up buried. Research only fixes a named flaw.

## References

- [taste](references/taste.md): the owner's sound taste, rule by rule.
- [palette](references/palette.md): the starting palette, a brand's palette, levels, instruments.
- [families](references/families.md): one family per kind of interaction; impacts and echoes.
- [spotting](references/spotting.md): from beats to sounds, moment by moment.
- [score](references/score.md): the Eleven Music playbook and directions.json.
- [mix](references/mix.md): the balance hierarchy, the mix, stems and their manifest.
- [verification](references/verification.md): the twelve sound gates and how sync is measured.
- [effects-engine](references/effects-engine.md): the gated effects engine, and what a film uses until it's open.
