---
name: slate-motion
description: The motion law and motion craft for Slate films. Covers named eases by job, the duration scale, staggers and their caps, landing moves on the voice's words, look and motion development, camera, transitions, morphs and shared elements, signature moments, a brand mark moved as a character, the anti-glitch rules and the glitch catalogue, read time and safe areas, with The Browser Company's craft as the north star (the feeling, never their brand) and Material 3, IBM Carbon and Apple HIG as alternative motion systems. Use when designing, building, reviewing or fixing how anything moves in a Slate film, writing a brand's motion law, animating a brand mark or motif as a character, or chasing a jitter, pop, strobe or flicker.
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

## 1. The north star and the bar

- **The Browser Company's craft and warmth** is the north star: motion that feels hand-made, where every move has one
  reason and lands with its sound. Borrow the feeling, never their brand; express it inside the brand's own tokens.
- **The visionary bar:** beautiful enough to make someone cry. One signature moment per act; correct but ordinary
  isn't enough (slate-story sets the moment, this skill stages it).
- **Intentionality:** one carrier per act, one idea per beat, every move tied to the voice's word or to the idea.
  Fewer, better moves. Stillness is part of the design.

## 2. The motion law

Every brand's law names its eases by **job**, and scenes move things only with those names:

| Job (kit role) | What it moves | Shape |
|---|---|---|
| entrance | type, cards, UI arriving, draw-ons, count-ups | fast start, long settle |
| move | rest-to-rest moves: camera push, pull and truck; carries | starts and ends at rest |
| relayout | short make-room re-layouts (0.4 s or less) | quick, soft |
| state | a chip flips, a check lands, a toggle | the product's own state-change ease, unchanged |
| exit | anything leaving | accelerates away, 0.75 times its entrance |
| settle spring | named container morphs only | a settle, never a bounce |

Durations come from the brand's scale (tick, quick, base, broad, camera, signature), exits are 0.75 times their
entrance, nothing but a state flip moves in under 0.2 s, and an act gets at most one signature duration. Staggers are
capped (a group's spread at most 0.6 s and half its entrance). A move lands (90% of its travel) within 2 frames of the
word it illustrates: the kit's `landOn` and `startFor` do the maths. A brand with no law draws with the kit's neutral
law until onboarding writes one, and the kit records that as a problem.

In depth: [motion law](references/motion-law.md), [eases](references/eases.md),
[durations and staggers](references/durations-and-staggers.md).

## 3. Look and motion development

A brand's look is built from its own design language (its components, tokens and product motion), never from generic
prompts, and it's approved from a **moving** 10 to 15 second test with sound: stills don't count. The kit's looks
library starts it (`flat` by default; `paper-2.5d` only for brands whose own language is papery; `editorial`;
`blueprint`; the After Effects bridge's looks by name). Onboarding runs this once per brand (slate-brand); a film's own
look test happens only when the brand's look isn't approved yet.

Composition: whole designed objects in generous space, one clear focal object and a clear hierarchy per frame. A thin
line on a big empty field reads as empty. See [look development](references/look-development.md).

## 4. The craft, move by move

- [camera](references/camera.md): drift to keep the world alive, rest for exact frames, one camera move per act.
- [transitions](references/transitions.md): every scene change is a carry, a portal, a mask or a whip; no hard cuts
  or crossfades except the reasoned ones.
- [morphs and shared elements](references/morphs-and-shared-elements.md): one object across a cut, keeping its label.
- [techniques](references/techniques.md): tilt-to-flat, component zoom and pull-back, mask reveals and mask morphs, paper
  techniques (ink, stamps, page turns), per-character type.
- [signature moments](references/signature-moments.md): staging the held breath, the landing and its echo.
- [logo build](references/logo-build.md): constructing a mark, and the bookend that replays it.

## 5. A brand mark as a character

When a brand's motif or mark becomes a character, it gets a small, fixed gesture vocabulary, each gesture with one
meaning and one word it keys to, and a pose that's a pure function of the film frame. Its parts move in whole-frame
phases (unison, pairs or a wave), never faster than a 16-frame swing, at most 30 px a frame and 8 px a frame squared
at a 1080-line frame, start and end at rest, draw as one flat plane in fixed sheets, never clip, and move as a whole at
small sizes. Between gestures it is exactly the brand's mark: stillness is the default. The kit's `MarkCharacter`,
`MotifMark` and `gesture()` implement the method. Full method: [character and motif motion](references/character-and-motif-motion.md);
one-page checklist: [character motion](references/character-motion.md).

## 6. Anti-glitch

Most glitches that reached a review came from a handful of causes:
- a move that starts at full speed (the entrance ease leaves rest fast) smears on one frame and goes sharp on the next
  under motion blur: run big moves on the rest-to-rest ease;
- oscillation near 4 Hz makes motion blur pulse near 8 Hz, where flicker is most visible: no wobbles, no idle loops;
- sub-frame staggers put parts at different shutter phases: offsets are whole frames;
- things that pop: an entrance starting part-way into the frame, a mask eased twice, a colour or shadow switching at a
  threshold, a label reflowing in one frame;
- planes that cross or share a depth, and slice or z-fight under the shutter;
- text that snaps to whole pixels while shapes glide, reading as jitter.

Run the [glitch catalogue](references/glitch-catalogue.md) as a checklist before every render; the reasons and fixes
are in [anti-glitch](references/anti-glitch.md), and what's banned (with its reasoned exceptions) in
[banned moves](references/banned-moves.md).

## 7. Read time and safe areas

- Every on-screen statement holds, once fully built, at least max(1.5 s, 0.4 s + 0.3 s per word); numbers 2 s after
  they land. Fix a short hold by extending the settled hold inside the shot, never by moving words or cuts
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

- `slate emit-events` writes each act's visual events and motion trace; `slate check <act>` is the builder's hand-in
  check; `slate qa motion` checks every trace: the brand's eases, durations and stagger caps, entrances from at least
  85% scale, speed limits by what moves (at 1080 lines: a rig part 30 px a frame and 8 px a frame squared, a UI element
  60 and 16, a carried page, card or camera 170 and 45; a whip past those goes to a person), no loop faster than a
  16-frame period, symmetric mark rigs, plus a pixel cross-check for fast motion the trace doesn't explain.
- **Tell the gate what moves.** A traced move names its `class` (rig, ui, carry or whip) when its name doesn't say it,
  and declares an exemption (`exempt`: a reading sweep, or a line whose steadiness is the meaning) with its `reason`.
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
- [signature-moments](references/signature-moments.md) · [logo-build](references/logo-build.md)
- [character-and-motif-motion](references/character-and-motif-motion.md) · [character-motion](references/character-motion.md)
- [anti-glitch](references/anti-glitch.md) · [glitch-catalogue](references/glitch-catalogue.md) · [banned-moves](references/banned-moves.md)
- [read-time](references/read-time.md) · [safe-areas](references/safe-areas.md)
- [motion-systems](references/motion-systems.md) · [principles](references/principles.md) · [sources](references/sources.md)
