# Tracer bullet: the first Inner Echo reel

Phase 2 of the plan. One real film through every stage of Slate, small enough to finish by **Oct 8**: a 15-second
9:16 reel for Inner Echo, made supervised first, then again by Oneiro's pursuit #27 in autonomous mode. It's done when
Quinn approves the reel and Oneiro posts that exact file.

This runbook is generic Slate usage plus the Inner Echo facts it needs. The brand pack itself lives in the private
store (`~/code/slate-brands/inner-echo`), never in this repo.

## What Inner Echo is (for the truth pack)

- An iOS SwiftUI companion for reflective journaling, mood check-ins, dream logging, AI chat, goals, affirmations,
  streaks, weekly recaps and personal insight patterns (`~/InnerEcho/AGENTS.md`).
- **Therapy-adjacent.** The truth pack must keep every claim to what the app observably does. No health outcomes, no
  "therapy", no testimonials. The App Store listing (public, read through Aside) is the claim source, as Oneiro's
  2026-09-23 reels already did.
- Instagram: `@getinnerecho`. Pursuit #27 already holds a launch kit, two hand-built 20-second caption-led reels
  (`~/oneiro/runtime/workspace/pursuit-work/27/reels-2026-09-23/`) and candidate slots on Oct 8 and Oct 22.

## Look and motion inputs (light onboarding)

- **Type:** the app bundles Space Grotesk and uses Poppins in code; Oneiro's reels used Niramit. All three are open
  fonts (Google Fonts), so they go in by reference with the licence noted.
- **Colour:** the app's text tokens (`textSet` #4B4C4C light / #FEFFFF dark), its dark surfaces (about #0D0D1F and
  #474766) and the cream and lavender Oneiro's reels used. The look test settles the palette.
- **Motion (the product's own values, converted to named eases at onboarding):** SwiftUI `.snappy` (49 uses),
  `.easeInOut(duration: 0.3)` (38), `.spring(response: 0.6, dampingFraction: 0.8)` (57 with and without blend),
  `.easeInOut(0.2)`, `.spring(response: 0.3, dampingFraction: 0.7)`. These springs overshoot gently, so Inner
  Echo's overshoot policy differs from a no-bounce brand. That's the point of a per-brand motion law.
- **Sound:** its own world (decision 7), not the films' palette. It's made locally as a 10–20-second reference, with
  levels measured against the voice.

## Quinn's approvals (needed by about Oct 4)

Onboarding, once:
1. **Truth:** the sources, the claim authority (Quinn) and the approved-claims list.
2. **Look and motion:** two moodboard directions, then a moving 10–15-second look-and-motion test with sound.
3. **Voice:** one approved take, with its model, settings and seed. Options: a designed voice, a library voice, or
   Quinn's own professional clone if he has verified one.
4. **Sound:** the 10–20-second reference proof.

Then the reel's own two sittings: pre-production (checklist, claims, script, voice shortlist) and final cut.

## The supervised run

```bash
slate brand status inner-echo            # routes to onboarding until all four references are approved
slate new ie-reel-001 --brand inner-echo --format social-reel --title "…" --request "<Quinn's words>"
slate status                             # every step after this is what status names next
slate script build                       # gates: ≤ 2.6 words/s, read time, banned words, runtime ±10%, claims sourced
slate voice tts                          # --live spends; the reel budget is VO 500–3,000 credits
slate timing
slate capture ios --spec captures/journal.json   # iPhone 17 Pro Simulator, 9:41 status bar, App Store fixtures
slate emit-events && slate check 1
slate render --samples 4 --detach        # the tracer renders at 4 samples
slate score compose --live && slate mix  # score + voice + the onboarding sound reference
slate qa                                 # schema, frame counts, loudness, sync, captions
slate master --ae --go                   # Quinn starts the AE finish (the osascript route; no file-write pref needed)
slate review                             # the phone cut and contact sheet go to Downloads
slate deliver                            # after the final-cut sitting; the manifest is written last
```

The capture uses the app's own marketing fixtures, exactly as Oneiro did: launch arguments `-ui-testing
-appstore-fixtures -show-journal-editor` (or `-show-guided-journaling-intro-fixture`). The fixture entry "A slow
Saturday" is hard-coded marketing data, not anyone's journal. Every product shot is labelled as the Simulator.

## The autonomous run (Oneiro)

Oneiro needs its self-want to land first (filed 2026-09-30 as chain #64). Then it:

1. picks a topic from its campaign;
2. runs `slate new … --mode autonomous` for Inner Echo (refused until the four references are approved);
3. submits long work as jobs and polls, because its research slices are killed after 10 minutes;
4. masters with ffmpeg (no After Effects, ever, in autonomous mode);
5. has a separate reviewer agent grade the cut, then parks the film;
6. sends Quinn the phone cut by iMessage, plus a push notification;
7. posts only the file whose SHA-256 matches Quinn's approval (`slate approved <file>` exits 0).

**Fallback** if the self-want is late: the supervised reel ships through Oneiro's normal campaign path after Quinn's
yes.

## Spend

| Item | Estimate |
|---|---|
| Voice Design for onboarding (only if a designed voice is chosen) | about 5,000 credits |
| Reel VO | 500–3,000 credits |
| Reel score | 1,000–2,000 credits |
| Higgsfield | $0 (no generation for this reel) |

Everything goes through the ledger. Autonomous films are capped at 20,000 ElevenLabs credits a month.

## Done when

Quinn approves the reel in his own words, and Oneiro posts that exact file. After that, schema v1 freezes.
