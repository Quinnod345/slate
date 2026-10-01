# Eases, springs and landing on the word

## Exact endpoints

An ease solved by bisection never returns exactly 0 or 1: it returns something like 1e-15 before a move starts. On the
first films that made every "has it started?" check open early: a bar drawn twice, copies of a later shot on screen from
the start, characters drawn at opacity 7e-15. The kit's eases return exactly 0 before and exactly 1 after; never wrap
them in your own, and never gate on `u > 0` with anything else.

**Never ease a value twice.** A mask fed through two eases filled its hollow in one frame.

## Landing a move on a word

A move "lands" when it reaches 90% of its travel (T90). Sync means the landing is within 2 frames of the voice's word:
- `landOn(word(...), 'base', 'enter')` gives a move's frame in and frame out so it lands on the word;
- `startFor(land, dur, ease)` gives the start time for a landing time;
- gate each element's entrance on the first word it shows, not a later one, or it appears already part-way through.

Word lookups (`word()`) never throw: a miss is estimated and recorded, and the hand-in check refuses the act until the
miss list is empty.

## Rest-to-rest versus entrance

- An entrance ease leaves rest at several times its average speed: a strong one covered a third of a 16-frame move in
  its first frame, so under motion blur the streak goes from nothing to long in one frame.
- An exit ease that ends fast does the same at the other end.
- So big moves (camera, carries, anything travelling far) run on the rest-to-rest ease, and characters use only curves
  with zero velocity at both ends.

## Springs settle, they don't bounce

- Good motion systems use springs that settle: Material 3 fires even its bounciest spring once per transition, and its
  effects springs never overshoot; Apple defaults to a bounce of 0, calls about 0.3 noticeable and above about 0.4
  exaggerated for UI, and starts every spring at full damping.
- Container morphs (one container changing size) use a gentle damped settle, not a bounce.
- Ease curves stop abruptly where springs keep their velocity; when two moves hand over, pass the exact velocity or
  start and end both at rest.

## Splines and chains

- Catmull-Rom through unevenly spaced keys overshoots and backtracks (a camera once drifted 274 px past its path). The
  kit's monotone spline (Fritsch-Carlson) never does.
- Chain moves so each segment starts where the last ended, at rest or at matching velocity; two moves that overlap on
  the same path sum into an overshoot. Drive them from one clock and measure the path.

## Measuring a reference's curve

Study reference films at full frame rate, fit the curve of a clean move frame by frame (`slate.motion.fit`, run by
onboarding's analysis), and keep the fit with its RMS error and the closest standard curve. A fit with high error or a
contaminated measure (blur, clipping at the frame edge) is read qualitatively, not trusted.

## The kit's names

| Function | Use |
|---|---|
| `ease(name, u)` | the brand's named ease at progress u, exact at 0 and 1 |
| `tw(t, t0, dur, name)` | a tween of film time on a named ease |
| `dur(token)`, `durFrames` | the brand's duration scale |
| `staggerAt`, `staggered` | capped staggers |
| `landOn`, `startFor`, `t90Of` | landing moves on words |
| `defineMotion({...})` | registers a move for the act's motion trace (what `slate qa motion` checks) |
| `monoSpline`, `chain` | monotone paths and chained moves |

## Telling the motion gate what moves

`slate qa motion` holds each traced move to the speed limits of what it is, so say so when the name doesn't:

- `class`: `rig` (a mark's or character's part: 30 px a frame at 1080p), `ui` (an element: 60), `carry` (a page, card
  or camera carried across the frame: 170) or `whip` (a deliberate whip past the carry limits, which a person signs
  off). A rig part is a rig without saying so, and a move whose id or property starts with the camera's name is a
  carry.
- `exempt`: `reading_sweep` (a highlight that moves at reading pace) or `steady` (a line whose steadiness is the
  meaning), always with its `reason`.
- `exception`: a reason for a deliberate break; its findings become review items a person accepts, never a pass.
- `size` (and `unit`): the element's size in pixels, so a scale or a rotation is speed-checked at its edges.

Besides the brand's named eases, the kit knows `even`: rest to rest through a third of the travel at each third of the
time, its peak speed 4/3 of the average. Use it for long carries and camera drifts; a carry may use it without the
brand naming it, and the motion gate knows it by the same name.
