# Look and motion: the brand's own

## Why it's done this way

The first film looked right only after its look was built from the brand's own design system. Generic looks were
rejected four times: flat shapes that didn't look polished, a 3D filament with no hierarchy, glossy futuristic glass,
and generated imagery that read as AI. What worked was the brand's own components and tokens, staged in space, with a
motion law measured from reference films and the product's own motion tokens, approved from a short moving test.

## Inputs

- **The brand's own components**: website code, app screens or SwiftUI views, guidelines.
- **Two or three reference films**, analysed with `slate analyze` and their clean moves fitted to curves
  (reference-analysis.md).
- **The product's own motion values**: its CSS easing tokens, or its SwiftUI spring response and damping.

## Outputs

- **Kit components rebuilt from the brand's design language**, at film scale: borrow the look language, don't import
  the website's code.
- **A brand MOTION.md and its motion law** in the pack (`motion`): named eases with bezier values and where each came
  from, the duration scale, staggers and caps, the exit factor, the roles, the overshoot policy, the brand motif's
  signature motion, the read-time rule, a banned list with reasoned exceptions, and a per-shot motion check.
- **A kit review sheet** in the brand's tokens.

## The motion law, anchored in the product

- **Anchor the film's motion grammar in the product's own grammar** (one clock, the camera as a guide, pieces carried
  between beats), so the film feels like the product, only bigger.
- **Keep the state-change ease identical to the product's**, so a state change feels the same in the app and the film.
- **Film entrances decelerate harder than the product's UI entrance curve**: film travel is 5 to 20 times UI travel and
  needs a longer settle to read as calm.
- **Soften an in-out for camera moves**: a quart in-out lurches over 1 to 3 second moves; good reference camera moves
  start and end at rest.
- **A website's scroll easing is a different vocabulary**; don't reuse web-scroll eases for film moves without
  measuring.
- **Derive a motif's lags from the product's own loader timing** (a loader running one group 8% of a cycle behind became
  a 4-frame lag on a 48-frame breath).
- **A signature motif keeps one colour** through a film and changes form only at designed moments.

The structure of a motion law, and the kit's neutral stand-in: slate-motion, motion-law.md.

## The gate: moodboards, then a moving test

1. **Moodboards first**, then the same moodboards grounded in the brand's kit (its real references wired in). At least
   two directions. Ungrounded moodboards drift to generic.
2. **A moving 10 to 15 second look-and-motion test, with sound**, built in the kit (or in After Effects, if that's the
   brand's engine). Stills don't count.
3. **The owner picks** from the moving test. Open questions (an opening colour, a ground) get their own side-by-side look
   tests.
4. **Record the approval** of the test file with its SHA-256; it becomes the brand's look-and-motion reference.

## Choosing the engine

The look is built in whichever engine suits the brand: the Remotion kit (the default), the open web renderer, or After
Effects with the AE bridge's looks. The engine can be chosen per brand, and later per act.
