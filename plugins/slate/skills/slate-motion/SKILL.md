---
name: slate-motion
description: The motion law and motion craft for Slate films. Covers named eases by job, the duration scale, staggers, landing moves on the voice's words, look and motion development, camera, transitions, morphs and shared elements, signature moments, a brand mark moved as a character, the anti-glitch rules and glitch catalogue, read time and safe areas, with The Browser Company's craft as the north star (the feeling, never their brand). Use when designing, building, reviewing or fixing how anything moves in a Slate film, writing a brand's motion law, animating a brand mark or motif as a character, or chasing a jitter, pop, strobe or flicker.
---

# slate-motion

Motion is where a film feels made by hand or made by a template. Slate's films earned their motion the hard way: a
motion law measured from reference films and the product's own tokens, a catalogue of every glitch that shipped to a
review and why, and a character spec rewritten after "it's super glitchy". This skill carries all of it, generalized
for any brand.

The law is data, the kit enforces it, and a gate checks it:
- the brand's **motion law** lives in its pack (`motion` in brand.json, written at onboarding);
- the Remotion kit moves things only by the law's names and exact-endpoint eases, and records every move;
- `slate emit-events` writes each act's motion trace, and `slate qa motion` checks it.

Read `slate history context --topic motion` when a private history is selected. Preserve what each correction was
about: jitter, depth conflicts and unreadable text are defects; fast travel, flutter, bounce and stillness are
creative choices. The values below are starting presets. The current brief can choose a different grammar; record
that choice in the brand's motion data and measure the result against it. Do not turn one film's fixes into universal
bans or smooth away the energy the requester wanted to keep.

## 1. The north star and the bar

- **The current brief's references govern the creative direction.** Study their sequence, construction and iteration
  history, then pursue that level of craft inside the current brand. The references below are useful starting points;
  they do not replace the requester's selected benchmark or prescribe one appearance for every film.
- **The Browser Company's craft and warmth** is the north star: motion that feels hand-made, where every move has one
  reason and lands with its sound. Borrow the feeling, never their brand; express it inside the brand's own tokens.
- **The visionary bar:** beautiful enough to make someone cry. One signature moment per act is a useful starting
  structure, not a quota for every film (slate-story chooses the structure, this skill stages it).
- **Intentionality:** make the viewer's attention and the movement's purpose clear. Earlier product films used one
  carrier per act and one idea per beat; the current brief may call for several voices, sustained motion or a still
  frame. Preserve the requested energy while keeping words, objects and transitions legible.

## 2. The motion law

Every brand's law names its eases by **job**. The kit also supports known curves by motion class; the trace must use a
curve the gate recognizes and match its measured shape. These role shapes are a starting recipe:

| Job (kit role) | What it moves | Shape |
|---|---|---|
| entrance | type, cards, UI arriving, draw-ons, count-ups | fast start, long settle |
| move | rest-to-rest moves: camera push, pull and truck; carries | starts and ends at rest |
| relayout | short make-room re-layouts | quick, soft; earlier films used 0.4 s or less |
| state | a chip flips, a check lands, a toggle | the product's own state-change ease, unchanged |
| exit | anything leaving | accelerates away; the starter uses 0.75 times its entrance |
| settle spring | container morphs and gestures where the brief calls for it | a controlled settle; bounce is a deliberate choice |

The starting duration scale uses tick, quick, base, broad, camera and signature. Earlier product films used exits at
0.75 times their entrance, a 0.2 s floor outside state flips, at most one signature duration per act, and a stagger
spread of at most 0.6 s and half the entrance. Choose the brand's scale and caps for the current direction; a signature
count is enforced when its duration token declares `max_per_act`. Current QA restricts stagger groups to entrance,
exit and state roles. A move illustrates a word by landing (90% of its travel) within 2 frames of that word: the kit's
`landOn` and `startFor` do the maths. A brand with no law draws with the neutral law until onboarding writes one, and
the kit records the missing law as a problem.

The current brief and authorized feedback govern creative revisions. Keep the original, the feedback and the revised
law together. A requested exploration does not need another approval of the same creative choice; the resulting
evidence and any separately required source or delivery approval still have to be current.

In depth: [motion law](references/motion-law.md), [eases](references/eases.md),
[durations and staggers](references/durations-and-staggers.md).

## 3. Look and motion development

Study a reference as a sequence. Name its rendered version and visible time window, the object that persists, and
how its geometry, content, camera and sound reach the next state. Read the actual source when available, distinguish
implementation evidence from notes, and record source-to-render gaps. Transfer the mechanism into this brand's own
design language. A beautiful final frame is useful, but its transitions and the feedback that shaped them explain
why the film works. See [morphs and shared elements](references/morphs-and-shared-elements.md).

A brand's look is built from its own design language (its components, tokens and product motion), never from generic
prompts, and it's approved from a **moving** 10 to 15 second test with sound: stills don't count. The kit's looks
library starts it (`flat` by default; `paper-2.5d` only for brands whose own language is papery; `editorial`;
`blueprint`; the After Effects bridge's looks by name). Onboarding runs this once per brand (slate-brand); a film's own
look test happens only when the brand's look isn't approved yet.

The earlier composition recipe used whole designed objects in generous space with a clear focal object. Choose the
composition for this film's purpose; sparse, dense and abstract frames can all work when attention is clear. The
earlier thin-line test read as empty because it did not carry its idea. See [look development](references/look-development.md).

## 4. The craft, move by move

- [camera](references/camera.md): choose drift, travel or stillness for the idea; preserve exact contract frames.
- [transitions](references/transitions.md): carries, portals, masks, whips, cuts and crossfades have different uses;
  follow the film's chosen grammar and preserve its required handoffs.
- [morphs and shared elements](references/morphs-and-shared-elements.md): one object across a cut, keeping its label.
- [techniques](references/techniques.md): tilt-to-flat, component zoom and pull-back, mask reveals and mask morphs, paper
  techniques (ink, stamps, page turns), per-character type.
- [physical books](references/physical-books.md): one object through cover, contact, comparison and audience views,
  hinged paper, continuous shadows and settled read holds.
- [continuous evidence](references/continuous-evidence.md): crowds becoming data, weighted objects becoming
  navigation, distance-paced curves and closing objects that retain their history.
- [signature moments](references/signature-moments.md): staging the held breath, the landing and its echo.
- [logo build](references/logo-build.md): constructing a mark, and the bookend that replays it.

## 5. A brand mark as a character

The kit's mark-character method uses a small gesture vocabulary and a pose that's a pure function of the film frame.
Its rig parts use whole-frame phases (unison, pairs or a wave). Current motion QA requires symmetric rig waveforms and
a loop period of at least 16 frames and no faster than 1.9 Hz; a brand can require a slower loop. Default rig limits are
30 px a frame and 8 px a frame squared at a 1080-line frame, with supported brand speed overrides. The earlier paper
character recipe starts and ends at rest, draws flat in fixed sheets and returns to the unchanged mark between
gestures. These are useful choices for that character, not a universal requirement that every character stay quiet.
The kit's `MarkCharacter`, `MotifMark` and `gesture()` implement this method; a different requested character still
needs truthful traces and the current technical checks. Full method: [character and motif motion](references/character-and-motif-motion.md);
one-page checklist: [character motion](references/character-motion.md).

## 6. Anti-glitch

Most glitches that reached a review came from a handful of causes:
- a move that starts at full speed can smear on one frame and go sharp on the next under motion blur: a rest-to-rest
  ease or a velocity-matched handoff often fixes the discontinuity;
- periodic motion can make shutter sampling pulse: test intentional flutter or idle motion in the final render and
  remove visible flicker without deleting the gesture;
- sub-frame staggers put parts at different shutter phases: offsets are whole frames;
- things that pop: an entrance starting part-way into the frame, a mask eased twice, a colour or shadow switching at a
  threshold, a label reflowing in one frame;
- planes that cross or share a depth, and slice or z-fight under the shutter;
- text that snaps to whole pixels while shapes glide, reading as jitter.

Run the [glitch catalogue](references/glitch-catalogue.md) as a checklist before every render; the reasons and fixes
are in [anti-glitch](references/anti-glitch.md), and the contextual concerns and current checks are in
[banned moves](references/banned-moves.md).

## 7. Read time and safe areas

- The starting read-time policy gives a statement at least max(1.5 s, 0.4 s + 0.3 s per word) once fully built;
  format and script settings can choose its reading policy. Numbers still need 2 s after they land. Fix a short hold
  inside the shot, or revise the script and rebuild its clock when an authorized timing change is needed
  ([read time](references/read-time.md)).
- Text stays inside the title-safe area and each platform's safe zones; body text at least 28 px at a 1080-line frame;
  text contrast from the brand's colour pairs; no more than three flashes a second ([safe areas](references/safe-areas.md)).
- Smaller text is only for labels of three words or fewer, and type size is judged by its measured cap height at full
  resolution, never by the font size in code.

## 8. Other motion systems and principles

Material 3, IBM Carbon and Apple's HIG are alternative motion systems: Material 3 for brands built on Material, Carbon's
productive curves for product UI shown inside a film and its expressive curves for the film's own big moments, Apple's
guidance for restraint. The twelve animation principles and UX in Motion's twelve apply with one caution: only easing
carries over directly from character animation to interface motion. See [motion systems](references/motion-systems.md),
[principles](references/principles.md) and [sources](references/sources.md).

## 9. Checking motion

Technical checks establish the properties they measure, including truthful content, valid timing, stable geometry
and legibility. They do not establish beauty. Review a moving example of the intended transformations with sound in
the target composition, and use the requester's specific feedback to improve the next version. Preserve comparison
versions and feedback, then extend successful choreography across the requested outputs with staging suited to each
aspect and duration. This is creative iteration within the authorized brief, not an additional approval gate.

- `slate emit-events` writes each act's visual events and motion trace; `slate check <act>` is the builder's hand-in
  check; `slate qa motion` checks every trace: the brand's eases, durations and stagger caps, entrances from at least
  85% scale, speed limits by what moves (at 1080 lines: a rig part 30 px a frame and 8 px a frame squared, a UI element
  60 and 16, a carried page, card or camera 170 and 45; a whip past those goes to a person), no loop faster than a
  16-frame period or faster than 1.9 Hz, symmetric mark rigs, plus a pixel cross-check for fast motion the trace doesn't
  explain. Brand speed limits can replace the defaults; the entrance scale floor and loop and rig checks remain
  current technical constraints. Read [motion law](references/motion-law.md) for the exact supported controls.
- **Tell the gate what moves.** A traced move names its `class` (rig, ui, carry or whip) when its name doesn't say it,
  and declares an exemption (`exempt`: a reading sweep, or a line whose steadiness is the meaning) with its `reason`.
  Other deliberately linear properties belong in the brand's `linear_props`; naming a linear ease alone is not enough.
  Long carries and camera drifts can use the kit's `even` curve (a third of the travel in each third of the time),
  which the gate knows by name. See [eases](references/eases.md).
- `slate qa stability` watches every act's render for judder a viewer feels before they can name it: pops, whole-pixel
  snaps, shakes, reversals, flicker and shimmer (slate-qa).
- `slate qa glitch` scans the rendered acts for one- and two-frame glitches, skipping flags within 3 frames of a planned
  beat; triage the rest (slate-qa).
- Look as well as measure: stills at the changed frames, contact sheets, a preview at quarter speed with sound. For any
  move, check its start, anticipation, fastest frame, overshoot, settle and last frame with motion blur at delivery
  settings.

## References

- [motion-law](references/motion-law.md) · [eases](references/eases.md) · [durations-and-staggers](references/durations-and-staggers.md)
- [look-development](references/look-development.md) · [camera](references/camera.md) · [transitions](references/transitions.md)
- [morphs-and-shared-elements](references/morphs-and-shared-elements.md) · [techniques](references/techniques.md)
- [physical-books](references/physical-books.md)
- [signature-moments](references/signature-moments.md) · [logo-build](references/logo-build.md)
- [character-and-motif-motion](references/character-and-motif-motion.md) · [character-motion](references/character-motion.md)
- [anti-glitch](references/anti-glitch.md) · [glitch-catalogue](references/glitch-catalogue.md) · [banned-moves](references/banned-moves.md)
- [read-time](references/read-time.md) · [safe-areas](references/safe-areas.md)
- [motion-systems](references/motion-systems.md) · [principles](references/principles.md) · [sources](references/sources.md)
