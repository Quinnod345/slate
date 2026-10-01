# The kit (`@slate/kit`)

The shared visual kit every scene imports. It draws a film in its brand's tokens, on the brand's motion law, on the
film's one clock, and it knows no brand, frame size or frame rate of its own. Its React-free half, `@slate/kit/core`, is
what an act's beats module imports, so the engine can run the same beats in Node for the events, the motion trace and
the hand-in check.

## What it reads

- **`@film/data`**, generated per film by the engine: `film` (id, size and frame rate from film.json, the only source of
  either), `shots`, and as the film gains them `vo_words`, `vo_cues`, `timeline`, `events`, `captions`, `spoken` (how
  each number or symbol is said) and `brand` (the pack, logos inlined). Never edit it; it's rebuilt from the film.
- **The brand pack**: palette tokens and roles, type families with their sources and licences, the motion law, logos
  with their build specs, the motif, the look. Every reader is forgiving and lists what it had to assume
  (`kit_problems` in the hand-in check).

## What it gives you

| Area | Pieces |
|---|---|
| Clock | `useFilmTime()`, `shot()`, `voSpan()`, `word()` / `wordTime()` (never throw), `misses()`, `frameOf()`, `contactAt()`, `ActRoot` (the sub-frame shutter), `actComposition()` |
| Motion law | named eases with exact endpoints, `tw()`, `dur()`, capped staggers, `landOn()`, `startFor()`, `defineMotion()` (the motion trace), monotone splines, `chain()` |
| Tokens and fonts | typed tokens and CSS variables (`TokenScope`), the type scale as fractions of the frame's short side, the font plan and its gate (a missing face fails the render) |
| Looks | `flat` (default), `paper-2.5d` (papery brands only), `editorial`, `blueprint`, and the AE bridge's looks by name |
| Surfaces | `Card`, `FadeCard` (fades as one sheet), `Ground`, `Grain` |
| Cameras | `Camera` (look-at, dolly in z), `WorldLayer` (2D, identity at rest), `camAt`, `lerpCam`, `drift2D` |
| Type | `PerChar` (capped), `PerWord`, `Arrive`, `CountUp` (each figure held three frames), `KineticWords` |
| Product UI | surfaces, buttons, fields, chips, pills, progress, avatars, streaming text (`ui.tsx`) |
| Charts | distributions, bars, lines, radar, rings, axes, markers |
| Logos | `LogoBuild`, `LogoSting`, `Ripple` from the brand's SVG and build spec |
| Motif and character | `MotifMark`, `MarkCharacter`, `gesture()` (slate-motion) |
| Captions | burned-in captions from `timing/captions.json` in the brand's font |
| Placeholders and review | `PlaceholderAct`, `KitReviewSheet` |

## The rules it keeps for you

Exact 0 and 1 at the ends of every ease; lookups that never throw; no spline overshoot; staggers capped at 0.6 s and
half the entrance; nothing scales from zero (kinetic words start at 85%); a count-up holds each figure for three frames
and never re-centres (`CountUp` draws over an unseen box: the count's own shape in nines until it has the final
value's figures, then the final value; a suffix holds still at its right end, and `followed` keeps a word after the
count from moving);
a fading card fades as one sheet; clips on untransformed wrappers; raster hints only while moving; a world layer
untransformed at rest; a motif that moves as a whole below its size threshold, never loops but to breathe, and
overshoots only where the brand allows.

Don't re-implement any of these locally: the films had nine copies of one timing table and six copies of one ease
wrapper, and every copy drifted.

## Rules for using it

- **Import it; never edit it from an act.** A builder who needs a kit change writes it up in the act's NOTES.md and
  works around it locally until the coordinator changes the kit.
- **A kit change is additive and opt-in**: without its new prop a component draws exactly as before. Before it lands,
  `slate engine golden` (the kit's own stills) and `slate engine golden --approved <film>` (every approved act's
  boundary frames and one frame per shot, at 45 dB against the approved render) both pass.
- **Reuse grounded components** (surfaces already matched to the product) rather than redrawing them.
- **Shared product surfaces are timeline-free faces drawn in card pixels**: a kit `Card` places one at the builder's
  pose, and 0-to-1 progress props from the act's beats drive its states, so any act can reuse it. A surface first drawn
  on one film's timeline is adapted once, when it's shared.
- **A surface a character sits in exports that slot** (its position in card pixels), and the character is drawn in the
  same card at the slot, never as a second copy.
- **Every shared surface has a cell in the film's kit review sheet.**
- **A helper that places something against text** (an underline, a marker) measures from the text's real origin and
  width in the loaded face (the kit's `measureText`), never an assumed offset: one underline sat 64 px right of its
  word.
- **Bookends reuse the same component** as the opening, with the closing's timing, never a redraw.
- **Author a world at film scale and screen-aligned at rest**, so rest frames are pixel-exact.
- **Lay out text with room** for words that arrive later, so no make-room move is needed.
- **Never fake motion blur**: the renderer averages 16 sub-frame samples across a 180-degree shutter.
- Audit the film's own shared components for repeats now and then; a repeat is a component waiting to be promoted.

## Kit review sheets

`KitReviewSheet` lays the kit's components out in the brand's tokens, for onboarding's look review and for settling
open look questions side by side. Render them as stills (`slate render stills`) or as a short moving test. Shot-recipe
cards with motion previews, one per shot type (as some open Remotion projects publish them), are good prior art for what
a review sheet should show; Remotion's licence still applies to anything built from them.

## Optional helpers

Vector drawing (an SVG character, arrows, a diagram), static frame design, component looks for a brand without a design
system, and script polish all have optional helper skills (asset-creator, canvas-design, frontend-design,
avoid-ai-writing). They help builders; none is a dependency.
