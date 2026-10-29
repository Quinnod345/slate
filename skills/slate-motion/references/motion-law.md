# The motion law

A brand's motion law records the movement chosen for its films: named eases, their jobs, a duration scale, stagger
caps, overshoot and speed policies, and the source of those choices. Onboarding starts it (slate-brand,
look-and-motion.md); the brand's pack stores the data, and MOTION.md explains where every number came from.

The recipes below came from earlier product films. Use them as starting craft guidance. The current brief and newer
authorized feedback govern the creative direction; preserve the earlier law, exact feedback and revision in the
history. A requested redesign is work within that scope, not another request for the same creative approval.
Current technical gates still apply, including checks that do not yet have a configurable creative policy.

## Where it lives

`brand.json` → `motion`:

| Key | What it holds |
|---|---|
| `eases` | each name maps to a cubic-bezier `[x1, y1, x2, y2]`, or `"sine"`, `"linear"` or `{"kind": "spring0"}`, with its `lineage` (where it came from) |
| `durations` | the duration scale, `{token: seconds}` |
| `staggers` | stagger units, in seconds or `{frames, at_fps}` |
| `stagger_cap` | `{seconds, fraction}`: a group's spread at most this many seconds and this fraction of its entrance |
| `exit_factor` | exits as a fraction of their entrance (0.75 is the starter value) |
| `roles` | which named ease does each job: `entrance`, `move`, `state`, `exit`, `relayout` |
| `overshoot` | the allowed overshoot and peak: `allowed` can be true or a list of ease names; `max` sets the amount above 1; an ease can carry its own `peak` |
| `linear_props` | properties allowed to move linearly, beyond time, continuous drifts and constant spins |
| `extra_curves` | kit curves available to more classes: `sway`, `land`, `spring0`, `even` |
| `speed_limits` | class limits replacing the defaults for `rig`, `ui` and `carry`, as fractions of the short side or pixels with their measured `short_side` |
| `loops` | a brand's minimum loop period; the current gate can tighten its floor, not lower it |
| `rigs` | the named rig's permitted phase modes and their lag or step |

The kit reads it through `motionLawFrom()` (the Python mirror is `slate.motion.law_from`), and both are held to the
same shared test vectors. Besides named brand eases, the gate recognizes specific kit curves where they apply:
rig parts can use sway, land and spring0; carries can use even; `extra_curves` extends their use. An unknown name is
recorded instead of crashing the preview, and the hand-in check refuses unresolved motion problems. A brand with no
law gets the kit's neutral law so placeholders render; that stand-in cannot pass as the brand's own motion law.

Name a deliberate linear transform in `linear_props`, or use a supported trace exemption with its reason. Merely
naming a linear ease does not permit every transform. Overshoot is checked by ease and peak; a descriptive
`where` field does not implement a separate object-level allowance.

## A product-film starting point

- The film's motion grammar comes from the product's own grammar (one clock, the camera as the guide, pieces carried
  between beats), so the film feels like the product, only bigger.
- **Keep the product's state-change ease** when fidelity to the app is the goal, so the action feels familiar.
- **Give a calm entrance a longer settle** when film travel is much larger than UI travel. Earlier film moves
  travelled 5 to 20 times as far; a different brief can call for a sharper arrival.
- **Measure camera acceleration**: a quart in-out lurched over earlier 1 to 3 second moves. Rest-to-rest travel
  solved that case; a purposeful fast move still needs a clean render.
- **A website's scroll easing is a different vocabulary** from a film's law; don't reuse web-scroll eases for film moves
  without measuring.
- Measure reference films frame by frame and fit their curves (slate-brand, reference-analysis.md). Good references use
  hand-tuned curves; neither clean measurement on the first films came anywhere near After Effects' default Easy Ease.

## The jobs and their starting shapes

- **entrance**: type, cards, UI arriving, count-ups and draw-ons. The calm starter uses a fast start and a long settle.
- **move**: camera push, pull and truck, and shared-element carries. The starter begins and ends at rest to avoid an
  abrupt change in blur; matching velocity across a handoff is another deliberate choice.
- **relayout**: a new line pushes a block, a list re-sorts, a card steps aside. Earlier make-room moves used 0.4 s or
  less; choose the brand's duration for the actual travel.
- **state**: state changes: a chip flips, a check lands, a toggle, a glyph morph commits, a bar segment lights.
- **exit**: the starter accelerates away and uses `exit_factor: 0.75`; the brand can choose a different factor.
- **settle spring**: a persistent container can change size with a damped settle. Bounce or other follow-through can
  serve an expressive object; name the curve and configure its overshoot rather than treating the calm recipe as a ban.

When the current brief changes the desired movement, update its named motion choices and record why. Do not let an
older preset override a newer creative direction. Preserve the previous law and the comparison in the history.

## Craft choices

- **Intentionality**: one carrier, one idea per beat, and every move tied to the voice's word or to the idea. The
  approved first film's bar was one object carrying each act, with each phrase of the voice doing one legible thing to
  it.
- **Fewer, better moves**: don't pan out to one thing and back when one draw-back can show both.
- **Add motion purposefully.** Stillness, flutter, sustained movement and sharp travel can each serve a film. Inspect
  the rendered result for unintended flicker, aliasing and loss of meaning.
- **Keep physical logic legible.** Overshoot and follow-through need a controlled amplitude and endpoint; an intended
  idle gesture should not look like an accidental wobble.
- **Linear movement** works naturally for time and steady travel. Mechanical starts and stops can also be intentional;
  declare the supported property allowance and inspect their speed and blur.
- **Crossfades** can carry atmosphere or a deliberate transition. Earlier product films kept them to atmosphere,
  audio and generated-shot seam blends of at most 4 frames to avoid doubled type.
- **Hard cuts** can create contrast, land a musical hit or show an instant state change. Carries and motion-masked
  cuts are useful when continuity is the chosen grammar.
- **Graphic reveals and effects** can use scale, light, particles or streamed type when they serve the brief and the
  brand. The earlier restrained preset avoided them; see [motion choices](banned-moves.md) for its failure evidence.

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
AE's default Easy Ease has 33.3% influence and 0 speed on both sides. Earlier references used different curves, so
applying this preset everywhere lost their character. It can still be a deliberate named choice; the gate checks
the actual samples against the named curve.

## Current technical checks

1. Every traced move uses a permitted named or kit ease, and its samples follow that curve. Duration and stagger
   checks use the brand's selected values; linear transforms still need the applicable allowance.
2. The current motion gate requires scaled entrances to start from at least 85% scale, hero text to move at least
   16 px at a 1080 px short side or unmask, and text arrivals to wait until other carries are below 2 px per frame
   at that size. These thresholds do not currently have brand overrides.
3. Speed and acceleration are checked by class against brand limits or the defaults: rig 30/8, UI 60/16 and carry
   170/45 at a 1080 px short side. A whip past carry limits becomes a REVIEW item.
4. Loops have a minimum period of max(16 frames, fps/1.9), and rig parts are checked for whole-frame-delayed symmetry.
   A brand may require slower loops or narrower rig modes; it cannot lower the current loop floor.
5. Traces must be current and well formed. Render pixels cross-check unexplained fast motion; stability,
   accessibility and read-time gates retain their own evidence requirements.
6. Review representative frames and the full-speed movement with the chosen transition grammar and delivery blur.

A trace's reasoned `exception` turns its violation into REVIEW, not an automatic pass. Acceptance is bound to the
trace's bytes and must follow the existing person-approval rules. Preserve the plan's current entrance, loop and
symmetry requirements; a creative direction is not evidence that a technical check passed.
