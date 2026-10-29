---
name: slate-sound
description: Design, score, mix and verify the sound of a Slate film. Covers the warm starting palette as a contextual preset, each brand and film's chosen palette, measured effects from picture events, Eleven Music scores planned per act and assembled by measurement, balance against the voice, and the sound gates. Use when a Slate film needs its effects spotted or rendered, its score planned, composed or assembled, its mix built or rebalanced, its sound verified, a brand's sound palette defined, or an answer on how a film should sound.
---

# slate-sound

Sound is designed, not stock, and it's locked to the picture: every beat the eye follows lands within a frame of its
sound, and the voice is always understood. The bar is the approved proof: "intentional", noticeable without being too
loud, and matching exactly what happens on screen.

This is stage 9 of the production. Its inputs are the voice's placed takes (`timing/vo_cues.json`), the picture's beats
(`timing/visual_events/act<N>.json`, from slate-build) and the brand's sound palette.

## 1. Taste and the starting palette

Read `slate history context --topic sound` when a private history is selected. The starter below reflects one
successful direction, not a universal prohibition. Every brand can choose its own materials, energy and silence.
Use the current brief and listening feedback to set its palette; measurements check that palette and preserve
approved references. A detector flag is evidence to inspect, not a substitute for hearing the intended result.

- **The warm starter:** pads, small soft felt notes, and hollow wood (woodblock, temple block, clave, knock, rim), with
  air for moves and a sub for a big landing. **No chimes** is this starter's choice, along with avoiding bells, metal,
  glass and buzzers; another palette can use them.
- **Design the result.** The current effects renderer uses real sampled instruments for tonal roles and reports a
  missing sample rather than substituting a synthesized tone. A previous synthesized layer sounded cheap in that
  proof; it does not establish a universal creative ban on synthesis or stock material.
- **The starter avoids sustained pitch glides and room-tone drone.** A glide had read as a siren in the earlier proof.
  A different direction can permit glides with both `rules.no_glides` and `rules.no_slide_ups` false, and drone with
  `rules.no_drone` false in the chosen palette.
- **One deliberate sound per kind, with variation** is the starter's interaction language. Repeats and shared sounds
  can be chosen through the palette's rules; keep intentional repetition distinct from an accidental repeated file.
- **Restraint and silence** can make a landing matter. Off-screen sound, ambience and dense passages can also serve a
  requested story. Choose the hierarchy deliberately and keep narration intelligible.

That palette (proof 2's, the approved proof) is the **starting point**. Each brand gets its own palette during
onboarding: its key and tempo, its materials, its banned timbres, its motif, and its levels measured against its
voice (slate-brand). See [taste](references/taste.md) and [palette](references/palette.md).

Use the latest brief and authorized listening feedback for revisions. Keep the original proof, feedback and new result
as comparison history. The film's `sound.palette` overrides the brand's palette for that film. A requested experiment
does not need approval of the same choice again; source-bound proof, reference and delivery approvals remain separate.

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
character, impact) and **size**. The starting recipe gives kinds distinct families, groups bursts into gestures and uses
at most one impact per act, often with an echo on the score's tempo. Choose density and repetition for this brief;
every synchronized sound's measured anchor still needs to sit on its beat's frame. See
[spotting](references/spotting.md) and [families](references/families.md).

## 4. Run the approved effects design

`slate sfx status` reports the hash-bound proof 3 approval. The effects engine is built; it refuses until that approval
is present and current. After approval, `slate sfx spot` writes measured cues and
`slate sfx render` writes their balanced stems; `slate sfx run` does both. Use `--dry-run` to inspect the work without
writing, or `--detach` to queue it in the render slot. Missing samples and malformed event files stop with a report;
they never turn into a synthesized stand-in or a silent act. A score-and-voice film can still mix without effects.
See [effects engine](references/effects-engine.md).

## 5. The score

A composed score is part of most films: Eleven Music on its best model, composed per act and assembled by measurement.
The warm starter keeps the effects' materials out of the score to make their roles distinct; choose instrumentation
for the current sound direction and record it in the palette and score plan.

```
slate score plan          # one composition plan per act: 3 s pre-rolls, parts under 90 s, blocks of 3 s or more
slate score compose       # composes the missing takes (spends; estimated and budget-checked first)
slate score assemble      # measured placement, equal-power joins, freeze repairs, the gap check
slate score qa            # silence and noise in the takes and the score
```

`sound/score/directions.json` (hand-edited) says where the music does something special: stops, blooms on a landing,
splits at a story turn, styles. Never name an artist, songwriter, song or label in its styles or a plan's prompts,
never ask the music to sound like one, and no lyrics. The playbook, and why each rule exists:
[score](references/score.md).

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

The mix places the voice from its cue sheet, sets the score under the voice with ducking (by default 18.5 LU under
before a 6 dB duck, the starting palette's level; `slate mix --under` and `--duck` change it for one mix), runs the
score's own clarity guard (its top dips under any word it would crowd, by just enough), sets effect stems at the
palette's levels, and takes the whole to −14 LUFS with the true peak at or under −1 dBTP, in 48 kHz, 24-bit, exactly as
long as the picture. Four delivery stems (dialogue, effects, ambience, music) sum to the mix sample for sample. Manual
polish in a DAW is allowed, but the engine's timing stays the reference. See [mix](references/mix.md).

## 7. Verification

`slate verify` runs sixteen core gates (loudness, stems summing, the music gap, voice sync, cue sync, repeats, glides,
drone, levels against the palette, the brand's sound reference, clarity under the voice, the music licence for the
destination, the voice limiter's touch, notes on the brand's key, timbre, and Whisper hearing the mix word for word)
and the seven balance gates (section 6, an approved span kept as approved among them), and a delivery needs every one
of them under its configured policy. Glides, drone, repeats and timbre have supported palette controls; allowing a
material does not erase the measured detector evidence or approve the mix by ear. Only PASS counts; a gate that can't
run (no approved sound reference, say) is UNCHECKED. See
[verification](references/verification.md).

A change to the approved narration (a polish, a re-voice) moves the balance: re-run the balance gates after it and
re-solve the mix's rides (mix.md).

## 8. Preserve what worked; explore what was requested

Keep the approved sound as the comparison baseline. Apply small balance notes without changing unrelated decisions;
when the owner requests a new sound or an experiment, build it within the authorized scope. Record both successful
and rejected versions with their feedback. A previous request to return to a proof means that redesign lost something
valuable in that film; it does not ban future redesigns. Compare intentionality, audibility and voice clarity by ear
as well as by measurement. New experiments do not overwrite the approved source.

## References

- [taste](references/taste.md): the owner's sound taste, rule by rule.
- [palette](references/palette.md): the starting palette, a brand's palette, levels, instruments.
- [families](references/families.md): one family per kind of interaction; impacts and echoes.
- [spotting](references/spotting.md): from beats to sounds, moment by moment.
- [score](references/score.md): the Eleven Music playbook and directions.json.
- [mix](references/mix.md): the balance hierarchy, the mix, stems and their manifest.
- [verification](references/verification.md): the sixteen core and seven balance gates, configured policies and measured sync.
- [effects-engine](references/effects-engine.md): commands, proof approvals, reproducible cues, beds and approved spans.
