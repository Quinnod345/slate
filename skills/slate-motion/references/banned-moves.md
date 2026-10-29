# Motion choices and the defects to watch

These are starting concerns from earlier films, not universal bans. Read the current brief and selected history.
Keep an intentional move that serves the film, including a fast carry, bounce, flutter, hard cut or crossfade.
Record the intended grammar in the brand's motion data so the gate measures that choice. An existing brand's explicit
restriction still applies until its direction changes; a technical failure remains a failure.

| Starting concern | What can go wrong | Deliberate use |
|---|---|---|
| **After Effects' default Easy Ease** | Applying one preset everywhere can erase the movement's purpose. | Use a curve that fits the move, including a preset when it earns its place. |
| **Linear moves** (position, scale, rotation) | Mechanical starts and stops. | Continuous drifts that start before the shot and end after it; things that represent time (progress bars, timelines, loaders, streaming text); a constant spin on a "thinking" indicator. |
| **Hard cuts** | A cut can break an intended shared-object handoff. | A deliberate contrast, musical hit or state change can need a cut. |
| **Crossfades between shots** | They can double-expose type and hide motion. | A deliberate overlap or atmosphere change can serve the film; inspect legibility during the blend. |
| **Scaling from zero** | A literal product element may lose physical continuity. | This can be a creative idea, but current motion QA fixes the entrance floor at 85% and exposes no brand override. Do not claim that recording the idea makes it pass. |
| **Bounce or overshoot** | Unwanted rebounds distract from a landing. | A character or expressive object can need follow-through; control its amplitude and endpoint. |
| **Opacity-only entrances for hero text** | Flat and cheap. | Hero text must also move at least 16 px or unmask. Opacity-only is fine for secondary labels. |
| **Stock presets and effects**: glows, light leaks, lens flares, glitch effects | An unconsidered preset can feel generic or off-brand. | Use an effect when the current treatment calls for it and its rendered result carries the idea. |
| **Glowing orbs and other AI-glow tropes** | A familiar trope can obscure the film's own idea. | Light can belong to a film's world; make its purpose specific. |
| **Extruded 3D type** | It may clash with a flat product language. | A dimensional treatment can call for it; preserve readable text and the chosen look. |
| **Stock-photo grids** | An arbitrary grid may fail to tell the story. | A photographic mosaic can be the requested idea when its sources and composition support it. |
| **Typewriter text** | A literal product demonstration can imply a streaming behavior the product lacks. | Streaming product text or an explicitly authored graphic text treatment, with truthful claims and measured reading time. |
| **Traffic-light gradients or colour ramps for status** | Unexplained colours can misrepresent real UI state. | Use the product's real status tokens for product evidence; an interpretive graphic can use a clearly chosen scale. |
| **Radial light or edge shading on flat brand fields** | Shading can contradict an intended flat field. | Use it when the selected look is lit or dimensional. |
| **Squash and stretch on paper-like marks** | Unwanted deformation can change the brand mark or material. | An authorized expressive character treatment can deform; preserve required brand geometry and current rig checks. |
| **Character effects**: glows, particles, lens effects, extra colours | Effects can compete with the character or conflict with its palette. | Choose them within the current character direction and applicable brand restrictions. |
| **UI the product doesn't have** | Nothing is invented. | A concept screen with the requester's explicit sign-off and a visible "concept" treatment. |
| **Confetti and particles as a celebration** | Generic effects can feel disconnected from the story. | Use them when the film's world and requested celebration call for them. |

Earlier narrow exceptions describe a product-film preset. They do not prohibit a different
brand or requested treatment. Source truth still matters: an interpretive UI can simplify or dramatize a real
capability, but cannot invent a capability or make an unsupported product claim.

## Checking

- `slate qa motion` checks allowed named and kit curves against their samples, durations, stagger roles and caps,
  fixed entrance and hero-text constraints, applicable speeds, loop periods and rig symmetry. A brand can set supported
  controls, not disable every technical check. See [motion law](motion-law.md).
- Inspect the result at full and quarter speed with sound and the current brief. A trace `exception` creates a REVIEW
  item requiring hash-bound human acceptance; it does not create an automatic pass or waive hard plan acceptance.
