# The motion law

A brand's motion law is how everything in its films moves: a few named eases, each with one job; a duration scale; stagger
rules; morph techniques; motion-blur settings; the read-time rule; a banned list with reasoned exceptions; and a motion
check per shot. It's written once per brand at onboarding (slate-brand, look-and-motion.md), stored as data in the
brand's pack, and explained in a brand MOTION.md that records where every number came from.

## Where it lives

`brand.json` → `motion`:

| Key | What it holds |
|---|---|
| `eases` | each name maps to a cubic-bezier `[x1, y1, x2, y2]`, or `"sine"`, `"linear"` or `{"kind": "spring0"}`, with its `lineage` (where it came from) |
| `durations` | the duration scale, `{token: seconds}` |
| `staggers` | stagger units, in seconds or `{frames, at_fps}` |
| `stagger_cap` | `{seconds, fraction}`: a group's spread at most this many seconds and this fraction of its entrance |
| `exit_factor` | exits as a fraction of their entrance (0.75 is the norm) |
| `roles` | which named ease does each job: `entrance`, `move`, `state`, `exit`, `relayout` |
| `overshoot` | `{allowed, max, where}`: normally not allowed, or only for named container morphs |

The kit reads it through `motionLawFrom()` (the Python mirror is `slate.motion.law_from`), and both are held to the
same shared test vectors. A name a scene uses that the brand doesn't define is recorded, never thrown, and the hand-in
check refuses the act until it's fixed. A brand with no law gets the kit's neutral law (standard CSS curves and a plain
duration scale), recorded as a problem: it's a stand-in so placeholders render, never a brand's look.

## Anchor the law in the product

- The film's motion grammar comes from the product's own grammar (one clock, the camera as the guide, pieces carried
  between beats), so the film feels like the product, only bigger.
- **The state-change ease stays identical to the product's**, so a state change feels the same in the app and the film.
- **Film entrances decelerate harder than the product's UI entrance curve**: film travel is 5 to 20 times UI travel and
  needs a longer settle to read as calm.
- **Soften an in-out for camera moves**: a quart in-out lurches over 1 to 3 second camera moves, and good reference
  camera moves start and end at rest.
- **A website's scroll easing is a different vocabulary** from a film's law; don't reuse web-scroll eases for film moves
  without measuring.
- Measure reference films frame by frame and fit their curves (slate-brand, reference-analysis.md). Good references use
  hand-tuned curves; neither clean measurement on the first films came anywhere near After Effects' default Easy Ease.

## The jobs

- **entrance**: everything that enters (type, cards, UI arriving, count-ups, draw-ons). A fast start and a long settle.
- **move**: rest-to-rest moves already on screen: camera push, pull and truck; shared-element carries. Starts and ends
  at rest, so it never smears under motion blur.
- **relayout**: short make-room re-layouts: a new line pushes a block, a list re-sorts, a card steps aside. 0.4 s or
  less.
- **state**: state changes: a chip flips, a check lands, a toggle, a glyph morph commits, a bar segment lights.
- **exit**: anything leaving accelerates away, 25% shorter than its entrance.
- **settle spring**: one persistent container that changes size (a card growing into a page, an island opening) uses a
  damped settle, never a bounce, and only for named container morphs.

When a brief's ease note conflicts with the law's ease for that job (a make-room move written with an entrance ease),
use the law's ease and record the deviation in the act's notes.

## Standing rules

- **Intentionality**: one carrier, one idea per beat, and every move tied to the voice's word or to the idea. The
  approved first film's bar was one object carrying each act, with each phrase of the voice doing one legible thing to
  it.
- **Fewer, better moves**: don't pan out to one thing and back when one draw-back can show both.
- **Add motion purposefully**, aim for brevity and precision, and avoid anything that oscillates in a sustained way.
- **No idle wobble** unless asked. Overshoot and follow-through share physical logic and decay to a stable endpoint.
- **Linear is for time**: continuous drifts that start before the shot and end after it, and things that represent time
  (progress bars, timelines, loaders, streaming text, a constant spin on a "thinking" indicator). Never for a move that
  starts or stops on screen.
- **Crossfades** only for atmosphere inside one shot, a seam blend of at most 4 frames within the same generated shot,
  and audio.
- **Hard cuts** only when motion masks them (inside a whip or a defocus), for a few musical hit cuts on downbeats if the
  brand's law allows them, and for UI state cuts with the camera locked (the product itself changes state instantly).
- **Nothing scales from zero**: entrances start from at least 85% scale.
- **Banned by default**: glows, light leaks, flares and glitch presets; typewriter text except where the product really
  streams text (banned-moves.md).

## Motion blur

- Rendered, never faked: the renderer averages 16 sub-frame samples across a 180-degree shutter (4 in draft). Don't add
  blur in scene code.
- Generated footage gets no extra motion blur; it's baked in. Blur only the graphics over it.
- Depth of field sits on the 3D camera, with focus pulls keyed to the rest-to-rest ease.

## After Effects values

AE stores an ease as speed and influence. For a segment of duration T moving a distance Δ, the average speed is v = Δ/T:
- the out handle (x1, y1): influence = x1 × 100%, speed = (y1 / x1) × v;
- the in handle (x2, y2): influence = (1 − x2) × 100%, speed = ((1 − y2) / (1 − x2)) × v;
- AE's minimum influence is 0.1%, so a vertical tangent can't be drawn exactly.

Multi-dimensional properties take one ease per dimension, and the build reads every ease back after setting it.
AE's default Easy Ease (33.3% influence, 0 speed on both sides) is banned on anything visible: it's the look of a
template.

## The motion check, per shot

1. Every visible move uses one of the brand's named eases, or its settle spring on a named container.
2. No Easy Ease, and no linear keys on transforms outside the listed exceptions.
3. Every read-time rule holds.
4. Motion blur is on for every moving graphic and off for footage.
5. Every scene change is a carry, a portal, a mask, a whip or an allowed exception.
6. Representative frames are reviewed on contact sheets, the way the references were studied.

`slate qa motion` checks the traces; a person checks the frames.
