# Banned moves, with their reasoned exceptions

Banned by default in every brand's law. A brand can add to the list; it can loosen an item only with a reason recorded
in its MOTION.md and the owner's approval.

| Banned | Why | Reasoned exception |
|---|---|---|
| **After Effects' default Easy Ease** (33.3% influence, 0 speed on both sides) | It's the look of a template. Measured reference moves were nowhere near it (the nearest fit was off by 11.6%), so the ban is a fact, not a taste. | None on anything visible. Only on invisible utility keys, where no curve is ever seen. |
| **Linear moves** (position, scale, rotation) | Mechanical starts and stops. | Continuous drifts that start before the shot and end after it; things that represent time (progress bars, timelines, loaders, streaming text); a constant spin on a "thinking" indicator. |
| **Hard cuts** | They break the carried-pieces grammar and read as a slideshow. | Cuts masked by motion (inside a whip or a defocus); a few musical hit cuts on downbeats if the brand's law allows them; UI state cuts with the camera locked. |
| **Crossfades between shots** | They double-expose type into mud and hide motion. | Light or atmosphere changes within one shot; a blend of at most 4 frames hiding a seam in the same generated shot; audio. |
| **Scaling from zero** | Nothing in a good product scales from nothing. | Entrances start from at least 85% scale. |
| **Bounce or overshoot** | Good products settle; they don't bounce. | The brand's settle spring, on named container morphs only, within its stated peak; a character's one allowed dip, if its spec has one. |
| **Opacity-only entrances for hero text** | Flat and cheap. | Hero text must also move at least 16 px or unmask. Opacity-only is fine for secondary labels. |
| **Stock presets and effects**: glows, light leaks, lens flares, glitch effects | Off-brand, and they read as a template. | None. |
| **Glowing orbs and other AI-glow tropes** | Generic and off-brand. | None. |
| **Extruded 3D type** | Dated and generic. | None. |
| **Stock-photo grids** | Generic. | None. |
| **Typewriter text** | A gimmick. | Where the product really streams text that way (an assistant's streaming answer). |
| **Traffic-light gradients or colour ramps for status** | Status colours are the brand's tokens. | None. |
| **Radial light or edge shading on flat brand fields** | Reads as a glow. | None. |
| **Squash and stretch on paper-like marks** | Paper doesn't squash; parts keep the brand's shape. | None. |
| **Anything off-brand on a character**: glows, particles, lens effects, extra colours | The character is the brand's own mark. | None. |
| **UI the product doesn't have** | Nothing is invented. | A concept screen with the requester's explicit sign-off and a visible "concept" treatment. |
| **Confetti and particles as a celebration** | A template's idea of delight. | None: the brand's own objects carry the moment. |

## Checking

- `slate qa motion` fails an act whose trace uses an ease the brand doesn't name, a stagger past its cap, or an entrance
  from under 85% scale.
- The rest are checked by eye at review: contact sheets, the preview at quarter speed, and the review checklist
  (slate-qa).
