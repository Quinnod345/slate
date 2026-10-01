# Plates: generated atmosphere in a code-built film

A plate is a generated background shot the film plays from its own clock: a landscape, weather, a real place. It's
atmosphere only: the film's own code draws everything designed over it.

## Rules on screen

- **Plates are never covered with UI, charts or logos.** Only the film's signature line and type sit over them.
- **No extra blur on plate footage**: its motion blur is baked in; blur only the graphics over it.
- **Keep type legible over footage** with a soft brand-colour grade behind it, never a box.
- **Place type over footage where the plate is darkest and least textured**, measured from its frames (the first films
  used luma 45 to 85, with texture within a few levels), never guessed; the access gate's contrast check confirms it.
- **A thin accent line over footage of a similar colour nearly vanishes**: give code-drawn elements over footage a paper
  edge and a soft shadow, so they read as objects laid on the image.
- **Reveal a plate the brand's way**: parting the page along a line, a mask, a portal; never a crossfade
  (slate-motion, transitions.md).
- **Show stress on footage as an atmosphere change** (a shadow front over the land, the sky kept clean), never as a
  cover over the image.

## Continuity

Generate later plates from the film's own stills and frames of an earlier selected plate (reference-to-video), or extend
a selected plate, so the world stays the same world across shots.

## Plate briefs are templates

Every brand keeps its own reference pack for generations, and every approved plate's brief (its prompt, references,
model and seed) becomes the template for that brand's next plates (prompting.md).

## Conforming

```
slate plates conform --shot S05
```

- Only the approved take (its SHA-256 must still match the pick).
- Motion-compensated to the film's frame rate from the clip's native rate (plates often arrive at 24 fps), then written
  as a JPEG sequence at the film's size in `plates/<shot>/plate/frames/`, which scenes play from film time (video decoders
  crash long parallel renders).
- Native audio, if any, is kept as a 48 kHz, 24-bit ambience file for the mix, high-passed and well under the voice.
- Optionally a ProRes 422 HQ copy at the native rate, for After Effects.
- `plate.json` records the provenance: request id, model, sizes, rates, duration, the estimated and actual cost, and who
  approved it.

**Inspect interpolation on every shot that uses a conformed plate**: motion-compensated conforms can tear on thin
features and haze.

**Uneven steps get repaired, not re-generated.** A generated plate can hitch once a second where the model joins its
chunks (a dropped or a frozen frame). `slate plates repair <shot>` measures each
step of the approved take on the plate's moving band, puts the frames on a true-time axis (a missing frame takes about
1.8 units, a frozen frame none), makes every film frame at its even place with optical flow (or a blend) and moves the
plate's edge track through the same warp. It stages everything with a report first and installs only when no step is
over 1.3 times its neighbours (`--install`), keeping the old sequence; the approved take is never touched. The conform
warns when a plate hitches, and `--dry-run` measures without writing.

## Drawing on a plate

When the film's line has to sit exactly on a plate's feature (a ridge, a horizon), track it:
`slate plates track --shot S05 --hue <range>` finds where the land meets the sky per sample, with the colours as data.
Trackers hop between near-equal features and drift outside their reliable window: follow features, hold outside the
window, and smooth over time.

When the footage settles, bring it to exactly full frame at 1:1 scale: the track is in plate pixels, so any scale left
on the footage moves the line off the feature.

## Provenance and delivery

Every plate carries its request id, model, prompt, cost and approval. A plate from a replay (tests) is marked so and
never ships in a final delivery; neither does a plate without an approved take.
