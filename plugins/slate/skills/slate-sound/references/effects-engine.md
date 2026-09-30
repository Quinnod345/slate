# The effects engine (waits for proof 3)

The designed effects engine turns the picture's beats into sounds: it spots every beat to a cue (family, variation,
level, anchor rule) and renders the cues to stems (UI, effects, tonal, reverb) that the mix reads. It's the part of
Slate's sound that isn't open yet, on purpose.

## Why it waits

The approved sound design (proof 2) was being perfected with the owner's own notes when Slate was built: sampled tonal
instruments, no glides or slide-ups, no drone, one sound per kind of interaction with natural variation, proof 2's
levels. That perfected proof (proof 3) has to be approved by the owner, and the tools behind it re-inventoried, before
its engine becomes Slate's. Porting it earlier would port a design still being changed.

## What that means today

- `slate sfx` (and `slate sfx spot`, `slate sfx render`) refuses, saying what's needed. `slate sfx status` shows the
  gate: whether a person's approval of proof 3 is recorded with its SHA-256.
- A film's sound today is its **score**, its **voice** and the brand's **onboarding sound reference**, through the mix.
  That's exactly what the first reels were planned to use.
- **Effect stems made another way** can still be used: designed by hand in a DAW (Logic, GarageBand) from the film's
  events and supplied in `sound/stems/` with a `stems.json` manifest (mix.md). The mix and every sound gate treat them
  like the engine's.
- The events are still exported by every act (slate-build), so the moment the engine opens, every film already has what
  it needs.

## When proof 3 is approved

1. The owner records the approval of the exact proof file (its SHA-256 is checked on every run).
2. The engine's port starts: re-inventory the perfected proof's tools, then spotting and rendering, with the starting
   palette and each brand's palette as data.
3. `sound/sound_cues.json` then records every cue with its beat, anchor rule and render, and the stems land in
   `sound/stems/` with their manifest.

## Never

- Never fake the engine with a synthesized stand-in for the tonal roles: tonal roles play on real sampled instruments or
  not at all.
- Never pull generic library effects in to fill the gap: sound is designed, not stock. A generated sound effect only
  fills a specific gap, and says so.
