# Transitions

Every scene change is a **carry** (an element continues across), a **portal**, a **mask**, a **whip** (a motion-masked
cut), or a documented exception. Hard cuts and crossfades between shots aren't allowed by default: hard cuts break the
carried-pieces grammar and read as a slideshow; crossfades double-expose type into mud and hide motion.

## Carries

- The default: something the viewer is already watching continues into the next shot
  ([morphs and shared elements](morphs-and-shared-elements.md)).
- Hand-offs inside one builder's span are carries per the script's transition column, with no pop or cut.
- Move the least possible: a new section rises as an edge over a still page instead of scrolling the whole page, so
  only the edge travels.
- A new section can enter like the next sheet of paper sliding over the last, with its shadow on the page above its
  edge.

## Whips

- Blur whips (defocus or motion-blurred whips that carry the cut) are the only kind of cut the carried-pieces grammar
  allows by default; the cut happens inside the blur, where the eye can't see it.
- Hold the frame still for about half a second before a whip, so the whip reads as deliberate.
- A whip out mirrors the next act's whip in: the same direction and a matching rotation, over the last few frames
  (6 frames on the first films, with a real rotation), blurred by the real sub-sampled render, never an effect blur.
- A whip so fast it doubles an element in the blur is a defect: re-time the beats so the carry can be a gentle glide.

## Portals and masks

- **A brand mark as a portal** between acts: it scales from lockup size through the frame on the rest-to-rest ease at
  the signature duration, and its window shape becomes the next act. At most three times in a film, only at act breaks.
- A portal zoom targets a clean area of the object, so it lands on bare ground.
- **Never rotate a mark with a fixed orientation**, even in a portal zoom.
- **Reveal footage by parting the page** along a line, like paper: two sheets slide apart, each with an edge and a soft
  shadow on the footage underneath. No crossfade.
- A window growing out of a thin element reads as a white box on the ground; open a section by pulling a sheet away with
  a moving, shadowed edge instead.
- Brand icons such as frame brackets can crop into a detail like a viewfinder, then open back out.

## Rack focus

A depth-of-field rack between focal elements is a lens effect: allowed, and never counted as a crossfade.

## The reasoned exceptions

| Exception | When |
|---|---|
| Motion-masked cut | inside a whip or a defocus: it counts as a transition |
| Musical hit cuts | a few, on downbeats, only if the brand's law allows them |
| UI state cut | a tab switch in the product with the camera locked, because the product changes state instantly; under sub-frame motion blur it renders as one mixed frame, which is expected, not a glitch |
| Crossfade | atmosphere inside one shot; a blend of at most 4 frames hiding a seam in the same generated shot; audio |

## Act boundaries

Acts meet on contract frames: a named rest state both sides render exactly, held still for at least 6 frames each side,
with everything decorative settled out over the last 0.6 s before it (slate-build, contracts.md). A transition inside
an act is designed; a transition between acts is a contract.
