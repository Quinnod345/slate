# A brand mark as a character: the method

When a brand's motif or mark becomes a character (a character-led film, an assistant's mark, a mascot built from the
logo), it needs its own motion spec. Every rule in it is either sourced (from research, cited by label in sources.md)
or computed from the brand's artwork and the film's own word timings. This method was written after a character's
first version was called "super glitchy"; the second, built to it, was approved as perfect.

**The bar:** playful and fluttery is compatible with perfectly synchronized. The defect feedback below concerned
jitter, clipping and overlap; it did not ask every character to become quiet. The earlier radial mark method used
coordinated gestures with stillness between them. Keep that original direction, feedback and repair together, and use
the latest brief and authorized feedback for the current character.

This document describes the current kit's radial rig and a product-film choreography recipe, not a universal character
policy. The kit's channel, phase, lint, calm and clipping contracts still apply when this rig is used; current QA also
checks waveforms, loop period and applicable speeds. A requested different grammar needs an implementation that can
meet its applicable checks. Do not claim a supported creative choice automatically overrides those technical limits.

Frame counts below are at about 30 fps and speeds at a 1080-line frame; scale both to the film's own rate and size.

## What went wrong the first time

| What the owner saw | The cause |
|---|---|
| Jitter | A 4 to 4.5 Hz damped wobble on nearly every beat: a swing every 3.3 frames. Motion blur pulses at twice that (8 to 9 Hz), where human flicker sensitivity peaks. Per-part spins made it worse. |
| No coordination | Staggers of 35 and 55 ms, which are 1.05 and 1.65 frames: no two parts ever sat at the same phase of a frame or of the shutter, so every part's blur looked different. |
| Speed jumps | A strong entrance ease leaves rest at over six times its average speed and covers a third of a 16-frame move in its first frame: the blur goes from none to a long streak in one frame. |
| Clipping | Parts cut off at container edges (nothing checked the character's envelope), and a colour mixed by distance from an edge flickered at that edge. |
| Overlap glitches | Parts that overlap by design, drawn one at a time with their own opacity and shadow, showed seams and double shadows; parts at the same 3D depth z-fought; a spread hovering near the point where the mark splits apart flickered between fused and split. |

## 1. Study the mark as a system

- **Geometry**: the parts, their shape, where they sit (often on a ring), their rest rotations, as fractions of the
  disc or frame they live in. Tabulate part size, ring radius and ink radius at every size the film uses.
- **One part, N times**: a radial mark is often one part rotated N times. Then it's a replicator: motion defined once
  for part 0, copied with a rotation and a whole-frame delay, stays symmetric by construction.
- **Overlaps by design**: if parts tuck under their neighbours, the eye sees one shape. The brand's own paint order
  defines two sheets (the parts drawn first, the parts drawn over them).
- **The split point**: compute how far the parts can spread (all together, or one group alone) before the fused shape
  splits into separate parts, where parts of the same group first touch when pulled in, and where the centre closes.
- Store the result in the brand's pack (`motif`: parts, shape, sizes, groups, curves, mark ratio, gestures).

## 2. The radial kit's channels

- **The whole mark**: its turn (rests at multiples of its symmetry angle), the whole avatar's scale, the mark's scale
  inside its disc, and the mark's offset from the disc's centre.
- **Each part**: its radial offset (in ring radii), its scale, and, for a part sent away, its place off the ring.
- Nothing else. No per-part angle, no per-part spin.

## 3. One clock

- **The pose is a pure function of the film frame**, including every sub-frame sample the renderer asks for. Nothing is
  carried between frames, no part has its own timer, and there's no randomness or noise.
- **Everything is in whole frames**: every key, and every per-part offset. Then the part in slot k at frame f is exactly
  the part in slot 0 at frame f minus its offset, rotated, blur samples included, so the blur streaks are rotated copies
  of one another instead of per-part noise.
- **Why**: every clean multi-part loader works this way. A circle spinner delays each of its 12 dots by the cycle over
  12; a replicator's delay is its duration over its count; After Effects' follow-the-leader rig reads each layer's value
  at time minus index times delay. Biology calls a fixed lag between neighbours a metachronal rhythm; perception calls
  it common fate: parts that move with the same timing read as one object.

## 4. The radial kit's three phase relationships

| Mode | Offset for the part in slot k | Symmetry | Typical use |
|---|---|---|---|
| **Unison** | 0 | full, at every frame | nods, leans, holds, returns, a greeting's breath, flights |
| **Pairs** | one group a fixed whole number of frames behind the other | within each group | a breath, an entrance in the product's own bloom order |
| **Wave** | k times a fixed whole-frame step, counted from the wave's start slot | across time | the one flutter |

- Derive the pairs lag from the product's own loader timing (a loader running one group 8% of a cycle behind became a
  4-frame lag on a 48-frame breath).
- The wave's step is its cycle over the part count, in whole frames (a 24-frame cycle over 8 parts is 3 frames), which
  also lets each accent register: hold an accent at least two frames.
- Order by slot (where parts are now, counted round the ring), not by part: a turn moves every part one slot along.

## 5. Symmetry at every frame

- **Spoke lock**: every part's centre stays on its spoke; only its distance from the centre changes. The one exception
  is an entrance where a group spirals in as one rigid piece.
- **Orientation lock**: a part's own turn is always its rest turn plus the mark's turn. A part never spins on its own.
- **Deliberate exceptions**: gestures whose meaning is breaking the ring (sending one part away to point at something,
  gathering parts round a point). The parts left on the ring stay exactly at rest.
- Good radial marks change the whole mark at once: a ring that moves outward to respond and inward to listen, dots
  rooted in consistent paths and timing, a breathing flower driven by one container animation with no per-part delay.

## 6. Loop checks and the earlier gesture recipe

- **Current loop floor:** at least 16 frames and no faster than 1.9 Hz; a brand can demand a slower loop, not loosen
  this floor. The earlier 30 fps recipe spaced consecutive reversals at least 8 frames apart.
- **Earlier overshoot recipe:** one small dip in the wave. Other expressive peaks are creative choices, but the chosen
  curve and brand overshoot policy must support them and the kit's applicable checks must still pass.
- **Earlier loop recipe:** a breath with a cycle of at least 44 frames and no other idle loop. Sustained motion may
  serve another brief; preserve current rig symmetry, determinism, loop and hand-in constraints.
- **A loop that turns** (a thinking loop that turns the mark as it breathes) turns exactly one symmetry step per cycle,
  so every cycle ends on the rest mark. Never turn it linearly and round to rest afterwards.
- Why: blur length follows speed, so a wobble at f Hz makes the blur pulse at 2f, and flicker sensitivity peaks near
  8 Hz. Material 3 fires even its bounciest spring once per transition and its effects springs never overshoot; Apple
  defaults to no bounce and advises against sustained oscillation. Stacked out-of-phase loops read as swimming.

## 7. Speed limits, and no abrupt velocity changes

- **Zero velocity at both ends** of every move, or hand the exact velocity to the next move. Parts use only rest-to-rest
  curves: a sine in-out for nearly everything, and a zero-bounce-spring shape for a quick turn that settles onto itself.
  Never the entrance, exit, state or linear eases.
- **The default is at most 30 px a frame** for every point of every part. The renderer spreads 16 samples over a 180-degree shutter, so
  the gap between samples is the speed over 32; 30 px a frame keeps it under 1 px. Up to about 48 only at the crest of
  a long flight the beat could not afford to slow was an earlier recipe exception, not an automatic QA allowance.
  Current brand speed configuration or a hash-bound review item must account for a move beyond the default.
- **At most 8 px a frame squared**: a point's movement per frame changes by no more than 8 px from one frame to the
  next, so its blur length changes by no more than 4 px.
- **A flight takes at least max(16, chord ÷ 19) frames**, so its crest stays under 30 px a frame.
- Why: strobing is too much change between adjacent frames, not a frame-rate problem; an ease curve stops abruptly where
  a spring keeps its velocity; AE's old 16-sample blur cap left visible echoes on fast objects.
- **Model every gesture numerically** at the smallest, typical and largest size it appears before building it, and
  record the worst case.

## 8. Nothing ever clipped

- Draw the character in an overlay layer above everything its parts visit, with no clipping ancestor.
- Every clip contains a clear circle round the character (about 0.85 of its disc's diameter covered the disc, its
  shadow and every gesture's reach) plus the whole path of any part sent away.
- Tabulate the envelope (the farthest ink from the centre) for every gesture.
- Inside a scroll view, it crosses the view's edge only when calm.
- A part that leaves the disc takes its colour by flight progress, never by distance from the disc's edge.

## 9. Overlaps without seams or z-fights

- **Two sheets, fixed for good**, in the brand's paint order. Membership is never re-sorted, so a turn never pops a
  layer. (Overlapping parts drawn one at a time can't have a consistent order round a ring.)
- **Opacity** on the whole mark or a whole sheet, never on a single part.
- **A fade finishes while the parts are still clear of their overlap.** A fade on part of the mark is done by the time
  its parts could touch: when a group is fully opaque, it's far past the point where parts meet.
- **Paper mode**: one shadow and one paper edge per sheet (a drop shadow on the sheet's group shadows the union), never
  one per part.
- **2D only**: no 3D transforms on parts. To tilt the character, render it flat into one layer and tilt the layer.
- **Stay clear of the split point**: a spread either stays inside the fused band or goes decisively past it and crosses
  at speed, once out and once back. Never ease to a stop near it. Parts of the same group never touch.

## 10. At small sizes it moves as a whole

- Per-part choreography needs room. The films used three regimes: full choreography at about 88 px and up; unison only
  (no lags, the wave in unison) from about 56 to 87 px; and no per-part motion below about 56 px, where spread becomes
  the whole mark's scale (flights still work). Below about 80 px, as a rule of thumb, the whole mark moves as one.
- Choose the regime once per gesture, from the size at its contact frame, so nothing switches mid-move.
- Drop any per-part motion under 2 px at the current size: it reads as shimmer, not gesture. Hold parts on stable
  sub-pixel positions (a spinner's jitter was cured by keeping successive frames within a fifth of a pixel).

## 11. Stillness in this kit's choreography

- The earlier recipe holds between gestures. The current kit's character hand-in checks also require stillness between
  its gestures and calm after them. That constraint must be reported honestly if a requested sustained treatment does
  not fit this implementation; it is not a reason to reinterpret the request as a universal preference for stillness.
- **Nothing is driven by the voice's loudness.** Speech loudness fluctuates mostly between 2 and 8 Hz, right in the
  jitter band, and a film knows every word in advance: key motion to events, never to amplitude.
- Knowing when not to move matters as much as moving; even audio-first products script their states. Delight means more
  from something that never exaggerates.

## 12. Keyed to the voice, landed with its sound

- **S** is the frame where the stressed syllable of the key word starts, and **C**, the contact frame, is two frames
  later: C = S + 2 (anywhere from S + 1 to S + 5 when a beat needs it, never later than the end of the stressed
  syllable).
- The key pose lands on C: 90% of travel for a landing, the apex for a peak. The move's top speed falls a few frames
  before C, near the vowel.
- **Early, never late.** A gesture's top speed coincides with the stressed vowel and its apex falls inside the stressed
  syllable; a gesture precedes or ends at the stressed syllable but doesn't follow it. Gestures 500 ms early didn't hurt
  recall in studies; 500 ms late did.
- **Later stress** (a word stressed on its second syllable): measure S from the take's loudness envelope (the stop
  consonant before a stressed syllable leaves a clean gap) with `slate timing stress <shot> <word>` (`--gap 2` when the
  word has two stops, as ex-CLU-sive does), and put the `stress_start` and `stress_end` it prints on the gesture in the
  act's beats. With no stop in the word it answers with the word's start, which is always safe: early, never late.
  Measure again after any re-voice.
- **Sound sits on contact frames**: every sound's attack is on a contact frame, every note has its own visible contact,
  and sound never comes before the picture. One event per note, each on its own whole frame; when two land on the same
  frame, one plays, by a fixed priority. Viewers notice sound from about 45 ms early or 125 ms late.

## 13. The gesture vocabulary

- **A small, fixed vocabulary**, each gesture with one meaning and one kind of word it keys to. The kit's defaults:
  hello (an entrance and a breath), yes (a turn that lands on itself), attention (a lean), thinking (a breath), answering
  (a pulse), delight (a wave), care (a hold), landing (a ripple), home, goodbye (a turn). A brand defines its own in its
  pack (`motif.gestures`).
- **Keep a gesture's meaning clear.** When the current treatment asks for another feeling, author its vocabulary in the
  brand's pack or use staging within the kit's supported channels; do not let the default vocabulary veto the brief.
- **Tempo carries meaning** (a fast pulse reads as an error, a slow one as rest); a small vocabulary on consistent paths
  reads as a character; a defined start and end, anticipation then release, reads as intentional.
- **Porting a product animation** to the rig: keep its choreography and replace its strong out-ease with the rig's
  rest-to-rest curve.
- **A gesture's spec**: its meaning, the word it keys to, each channel's keys in frames relative to C, its phase mode,
  its contacts, its sounds, how it behaves at each size, and what it never does (never a wobble after a landing).

## 14. How gestures combine

1. One momentary gesture at a time, with at least 4 still frames between one's end and the next one's start.
2. Held states (a lean's offset, a hold, parts sent away) persist until changed. Momentary gestures may run on top,
   except that nothing runs during a hold, and only a lean or a hold while parts are away.
3. Never two gestures on the same channel at once.
4. Every act starts and ends with the character calm: the brand mark exactly, turned to a multiple of its symmetry
   angle. At a contract frame the mark rests at exactly its rest turn, not an equivalent multiple of its symmetry
   angle: at 90 degrees the picture matches but the pixels don't, and a bit-identical contract fails.
5. A builder's own moves of the character (placing it, resizing it for a gesture) are part of its motion: the rig's
   curve and its speed limits apply.

## 15. Staging a character

- **Introduce it up close**, assembled large, then a step back: assembled at its small lockup size, it read as a speck.
  See it made, let it turn to you, step back, then name it.
- Resize it into its full-choreography size before a gesture that needs it (enlarging over about 20 frames before a
  wave).
- **Staging is separate from gestures.** For a close-up, the mark's scale inside its disc and its offset from the
  disc's centre are staging (`MarkCharacter`'s `markScale` and `markOffset`, in disc diameters), and they come back to
  1 and 0 on the rig's curve as the camera pulls back. They never change a gesture: the track draws the same pose,
  scaled and moved with the mark.
- Never show the character twice in one frame (a second copy as a chat avatar splits it).
- Check a flight's path against the marks it passes, and choose the part and placement that clear them.
- Place it where its later UI will open into empty space, not over content.
- **Every point the character uses** (its position, a lean's target, where parts are sent) is in the coordinate space
  of the layer it's drawn in: screen pixels in a full-frame layer, world pixels inside a camera, card pixels inside a
  card.
- **Carry it between surfaces on its own curve in screen space**, whatever the camera does. Each carry ends no earlier
  than any camera move it overlaps (a carry that ended first kinked the character's velocity at about 8 px a frame
  squared), and lands on a fixed target, never a moving point.
- The earlier warm sound recipe used felt notes, a wood tick and pads. The current sound palette can choose other
  materials, including chimes when its measured timbre policy allows them (slate-sound).

## 16. Sending parts away

Sending parts off the ring, to point at something or gather round a group, is the one deliberate break in the ring's
symmetry (section 5), so it has rules of its own:

- **Which part goes**: the part whose spoke points closest to the target at lift-off; ties go clockwise. The rest hold
  still and the ring keeps the gap.
- **Its path**: an arc that bulges away from the centre, a quadratic curve whose control point is the chord's midpoint
  pushed outward by min(0.12 × chord, 80 px). Size and colour change by flight progress, never by distance, and the
  part never spins.
- **Several parts to several targets**: sort the targets by angle around the centre and rotate the assignment to the
  least total angle error, so no two paths cross. The longest chord sets the flight length for all of them.
- **A ring opening around a group of targets** moves its centre on an arc and grows to an ellipse, each part at its own
  slot angle so order and orientation are kept, then holds perfectly still until it goes home.
- **One flight at a time**: a flight never lifts off before the previous one has seated.
- **Size a flight from its on-screen chord**, the camera's scale included: under a close camera, a flight sized in its
  own layer moves faster on screen. Keep a floor so a signature flight stays unhurried, and never shorter than
  max(16, chord ÷ 19) frames (section 7).

## 17. Acceptance of this method

Keep the original recipe's tests as evidence of what it achieved. The current hard plan acceptance and kit hand-in
checks still require their own proof; a changed creative direction does not silently waive them. Speed and overshoot
results must be assessed against the current configured law rather than treating the historical 48 px crest as a pass.

1. **Determinism**: any frame rendered on its own is bit-identical to the same frame rendered in sequence.
2. **Calm**: at rest the frame after every gesture ends, and at both ends of every act.
3. **Symmetry** at every sub-sample: unison parts equal; pairs equal within each group and lagged exactly; wave slots
   lagged exactly.
4. **Speed**: every point at most 30 px a frame (48 only at a long flight's crest), acceleration at most 8 px a frame
   squared.
5. **Frequency**: reversals at least 8 frames apart; the one allowed overshoot only where it's allowed.
6. **The split point**: the gap between neighbours changes sign at most once out and once back per gesture, across all
   sub-samples.
7. **Clipping**: whenever it isn't calm, every clipping ancestor contains the clear circle and any away part's path.
8. **Sound**: every character event on a whole frame, one event per note, no offsets added by the sound engine.
9. **Eye**: the gesture sheet at the smallest and largest sizes, at full and quarter speed, with sound. Nothing
   shimmers, pops or strobes, and every note lands on something you can see.

## The kit

`MarkCharacter` (the disc with the motif in it) and `MotifMark` draw the rig; `gesture(meaning, {t | frame})` makes
track entries from the vocabulary; `motifBeats()` lists the character's sound events (kind `character`, one per note);
`motifLint()` lists anything the combination rules forbid (an empty list is a clean track); `registerMotifTrace()`
records the rig for `slate qa motion`, which checks symmetry and the speed limits; `isCalm()` checks rest. At hand-in
`slate check` runs the acceptance tests it can measure on every registered mark (the character gate: the rig lint, calm
at both ends and after every gesture, still between gestures, the split point, the clear circle in every clip), and the
kit's gesture sheet (`Kit-Gestures`, and `Kit-Gestures-Quarter` at quarter speed) is the sheet a person reviews by eye.
The research behind each rule is in [sources](sources.md).
