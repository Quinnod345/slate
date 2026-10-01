# The Slate kit (`@slate/kit`)

The shared visual kit every Remotion scene imports. It draws a film in its brand's tokens, on the brand's motion law,
on the film's one clock, and it knows no brand, frame size or frame rate of its own. The React-free half,
`@slate/kit/core`, is what an act's beats module imports, so the engine can run the same beats in Node.

```tsx
import {ActRoot, useFilmTime, word, landOn, defineMotion, defineEvent, Card, P, PerChar} from '@slate/kit';

// beats.ts: every beat at module level, from the words the voice says (lookups never throw)
export const LAND = word('S03', 'report');
export const cardUp = defineMotion({id: 'card-up', shot: 'S03', prop: 'y', ...landOn(LAND, 'base', 'enter'), from: 40, to: 0, entrance: true});
defineEvent({shot: 'S03', id: 'card_lands', t: LAND, kind: 'land', size: 'm', note: 'the report card lands on "report"'});

// the scene: a pure function of film time
export const Act2 = ({sub}: {sub: number}) => (
  <ActRoot act={2} sub={sub}>
    <Scene />
  </ActRoot>
);
```

## What the kit reads

**`@film/data`** is one module the engine generates per film (the default export, `core/types.ts` `FilmData`):

| Key | From | Used for |
|---|---|---|
| `film` | film.json | `fps_num`, `fps_den`, `width`, `height` (the only source of rate and size) |
| `shots` | timing/shots.json | shot spans; `frame_out` is exclusive (the next shot's `frame_in`) |
| `vo_cues` | timing/vo_cues.json | where each read starts in its shot (`at`), and the sheet's `lead` |
| `vo_words` | timing/vo_words.json | word timings, for `word()`, `voSpan()`, kinetic type; each read's `tokens` (the script's words lined up with what was heard, by slate.timing) answer `word()` first |
| `captions` | timing/captions.json | burned-in captions: a list of `{shot, text, lines, start, end}` or `{cues: […]}` |
| `spoken` | `slate.timing.spoken.forms` | how each number or symbol in the script (and in what was heard) is said: `{"64": [["sixty-four"]], "&": [["and"]]}` |
| `lang` | the language's table (`.slate/lang.json`) | how a word becomes a match key: the kept symbols, the edge symbols, negative marks, the longest spoken run |
| `brand` | the brand pack | tokens, type, motion law, logos (SVG **inlined as text**), motif, look |

**The brand pack** (docs/ARCHITECTURE.md §7). Every reader is forgiving and lists what it had to assume:

- `palette`: tokens, nested or flat. `palette.roles` names the roles (`page`, `surface`, `ink`, `accent`, `inverse` …)
  by token path (`"gray.50"`) or colour; `palette.tones` gives `ok`, `warn`, `risk` (`{dot}` or `{bg, ink, dot}`).
  Missing roles are derived by mixing, with contrast guaranteed (ink2 ≥ 7:1, ink3 and accent ink ≥ 4.5:1).
- `type.families`: `[{role, family, weights, source, licence}]`; `source` is `installed`, `google`, a kit URL, or a
  font file URL. `type.scale` (optional) overrides roles (`hero`, `h1`, `body` …) as fractions of the short side, or
  px with `type.reference`.
- `motion`: `eases` (`{name: [x1, y1, x2, y2] | "sine" | "linear" | {kind: "spring0"}}` with `lineage`), `durations`
  (`{name: seconds}`), `staggers` (seconds, or `{frames}` at the film's rate), `stagger_cap`, `exit_factor`,
  `roles` (`entrance`, `move`, `state`, `exit`, `relayout` → ease names), `overshoot` (`{allowed, max, where}`).
- `logos`: `{name: {svg, build}}`; `build` is a `LogoBuildSpec` (guides, vertices, order, keystone, timing).
- `motif`: parts, shape, sizes, groups, curves, mark ratio, and `gestures` (meaning → gesture kinds).
- `look`: `{id, overrides, engine}` from the looks library; `engine` is where the brand's scenes are built:
  `remotion` (the default), `web` (the open web renderer, no Remotion licence) or `ae` (implied by an AE look).
  `sceneEngine()` reads it.

## The pieces

- **Clock** (`core/film.ts`, `clock.tsx`): `useFilmTime()`, `shot()`, `voSpan()`, `word()` / `wordTime()` (the script's
  own tokens first, then the heard words, by the rules in `tests/vectors/spoken.json`; the answers match
  `slate.timing.words.word_time`, which `engine/tests/kit/spoken.test.ts` holds it to), `misses()`,
  `frameOf()` (Python's rounding), `contactAt()`, `ActRoot` (sub-frame shutter), `actComposition()` for the generated
  Root. Word lookups never throw: a miss is estimated from the line and recorded, and the hand-in check refuses an act
  while `misses()` isn't empty.
- **Motion law** (`core/eases.ts`, `spline.ts`, `motion.ts`): exact-endpoint eases, T90, `startFor()`, `landOn()`,
  monotone splines, `dur()` / `durRank()`, capped staggers, and `defineMotion()`, whose registry
  `traceMotions({act})` samples into `timing/motion/actN.json`. The Python mirror is `slate.motion`; both are held to
  `tests/vectors/eases.json` and `t90.json`. Besides the brand's named eases the kit knows `linear`, `sine`,
  `spring0` and `even` (the even travel profile: rest to rest through a third of the travel at each third of the time,
  peak speed 4/3 of the average, for long carries and camera drifts; `slate qa motion` knows it by the same name).
  `defineMotion` also takes what the motion gate needs to know about a move, written into the trace only when given:
  `class` (`rig`, `ui`, `carry` or `whip`: a page or card carried across the frame says `carry`; a rig part is `rig`
  and a move named for the camera is `carry` without saying so), `exempt` (`reading_sweep` or `steady`) with its
  `reason`, `exception`, `size` and `unit`.
- **Events** (`core/events.ts`): `defineEvent()` next to the move that makes the sound. `t` is always the beat; a draw
  or a count gives its span (`start`, `dur`) and ends on `t` (give `start` and `dur` and `defineEvent` works `t` out),
  a whoosh or a swell peaks on `t` inside its span. `validateEvents()` holds slate.timing.beats' span rules word for
  word (`spanProblems()`); the logo build's draws (`logoEvents()`) give their spans, with `t` where each stroke ends.
- **Timing margins** (`core/margins.ts`): `margin(name, earlier, later, min)` records the slack between two beats the
  picture depends on ("the card has landed before the word that names it"), and `defineMargin()` one the act worked
  out itself. Every beat is keyed to a word, so a re-voice can make a margin negative without anything else breaking:
  the hand-in check lists every margin and fails a negative one, and emit-events writes nothing while one is
  (L-timing-D039). An act may also export `margins` (`[{name, slack}]`, or a function returning them).
- **Tokens and fonts** (`core/tokens.ts`, `core/fontplan.ts`, `fonts.tsx`): typed tokens, CSS variables
  (`<TokenScope>`), and the font gate. `useLine()` is the look's accent-line weight in px on this frame; every
  component that draws the accent line takes it by default (the charts' curves and lines, the logo build's outline),
  so an act's own line and the kit's meet at one weight (L-kit-D131).
- **Looks** (`core/looks.ts`): `paper-2.5d` (papery brands only), `flat` (the default), `editorial`, `blueprint`; the
  AE bridge's 13 looks by name.
- **Components**: `Card` (with `back`, a face for a card turned past 90°: L-kit-052), `FadeCard`, `Ground`, `Grain`
  (`paper.tsx`); `Camera`, `LookAtCamera`, `WorldLayer`, 2D camera helpers (`camera.tsx`); `PerChar`, `PerWord`,
  `Arrive`, `CountUp` (`type.tsx`, below); app UI (`ui.tsx`); charts (`charts.tsx`: a marker on a drawing line grows
  in over the tenth of the draw before the line reaches it, `dotGrowth()` and `lineArrivals()`, so nothing pops at 98%;
  `LineChart dots` does this, and `Axis` draws a chart's labels); `LogoBuild`, `LogoSting`, `Ripple` (`logo.tsx`);
  `MotifMark`, `MarkCharacter`, `gesture()` (`motif.tsx`); `KineticWords`, `KineticStack` (`kinetic.tsx`);
  `Captions`; `PlaceholderAct`; `KitReviewSheet`.
- **Counting** (`CountUp`): one value per master frame (`masterTime()`), so a frame's motion-blur samples show the same
  figures, and each figure holds for three frames (`countValue()`). It never re-centres, because it's drawn over an
  unseen box that holds still while its figures change (the films' refined rule, 2026-10-01): the count's own shape in
  nines (`countShape()`: 99 while it passes 10 to 99) until it has the final value's figures, then the final value, so
  a centred count from 0 sits within a pixel or two of its true centre and the final value rests exactly where it
  would on its own. A suffix in the format ("2,500+") holds still at the box's right end. `followed` is for a count
  with a word right after it ("%", a label): the final value is the box for the whole count, so the word never moves.
- **Logo builds** (`logo.tsx`): `logoBuildExtent(plan)` is everything a build draws (the guides past the mark, the
  keystone where it hangs, `KEYSTONE_HANG` of the artwork's height away, and its echo), and `fitLogoBuild(plan, w, h)`
  places a build so all of it fits an area with a margin. Fit a cell or a frame to that, never to the resting artwork:
  the review sheet's logo cell once clipped the demo mark's hanging keystone by about 5 px. `LogoBuild` and `LogoSting`
  draw the mark in its own colourway for their ground (`colourwayLogo(name, dark)`: a sibling drawn for the inverse
  ground, or the logo's `colourways`), never a recoloured copy; a mark with one colourway is drawn as it is.
- **The character** (`motif.tsx`, `core/motif.ts`): `MarkCharacter` takes staging separate from gestures, `markScale`
  and `markOffset` for a close-up, brought back on the rig's curve as the camera pulls back (L-kit-D075), and draws its
  disc on a transform (`subpixel`, the default) so it sits under the parts on every frame (L-kit-S013). The rig's
  combination rules are a pure function of the track: `gestureSpans()` (the frames and channels each gesture moves),
  `checkTrack()` (what runs, what's switched off and why) and `motifLint()`; the picture, the sound (`motifBeats()`)
  and the hand-in check all read them. `registerMotifTrace(track, {…, character: true, clip})` puts a mark's parts in
  the motion trace and keeps its track for the check's character gate, which runs `characterAcceptance()`: calm at the
  act's ends and after each gesture, still between gestures, no dithering across the split point, and a clipping
  container that keeps `CLEAR_CIRCLE` of the disc clear.

## For the engine

- **Entry.** Alias `@slate/kit` to this folder and `@film/data` to the film's generated data module. Register
  Google Fonts families before the root (`registerGoogleFont('Inter', loadFont)`), one static import each.
- **Placeholders.** An act with no scene yet renders `<ActRoot act={n}><PlaceholderAct act={n} /></ActRoot>`.
- **emit-events, check, motion trace.** `node/collect.mjs` `collectAct({act, beats, filmData})` bundles the act's
  beats with esbuild and returns `{events, eventProblems, trace, motionProblems, misses, missLabels, ok}`; write
  `events` to `timing/visual_events/actN.json` and `trace` to `timing/motion/actN.json`. The engine's own loader
  (`engine/remotion/load_act.mjs`) also reads `margins()` and `motifTraces()` from the act's kit instance, for the
  check's margins, rig-lint and character gates.
- **Font gate.** The page logs one line, `SLATE_FONT_GATE {…}`, and a failure cancels the render with the same
  report in its message. `node/stills.mjs` `findGateReport(text)` finds it; `core/fontplan.ts` `toGateResult()` turns
  it into a gate result.
- **Stills.** `node/stills.mjs` bundles and renders with @remotion/renderer; it never downloads a browser (pass one).

## Review sheets and golden stills

The review sheet (`review.tsx`, `review/root.tsx`) is how a person looks at the kit in a brand: `Kit-Review` and
`Kit-Review-Vertical` (every component), one `Kit-Section-<id>` per section (the extra sections `count`, the box rule
mid-count, and `card-turn`, two-sided cards past 90°, aren't on the full sheet), and `Kit-Gestures` and
`Kit-Gestures-Quarter`: every gesture the rig draws in its own cell, landing at t = 1 s, at the smallest and largest
size a film uses the character (`GESTURE_SIZES`, shares of the short side), with the rig's sounds on a strip under
each (L-kit-079). Look at the gesture sheet, at full speed and at quarter speed, before builders use a changed rig.

```
cd engine
node remotion/kit/node/golden.mjs            # compare with tests/kit/golden/ (45 dB PSNR, swangle)
node remotion/kit/node/golden.mjs --update   # rewrite the baselines: look at every still before keeping them
slate engine golden --approved <film>        # every approved act still draws as approved (below)
```

Software GL (`swangle`) is bit-exact run to run. On a GPU renderer (`--gl=angle`) it isn't, so `--update` renders each
still twice and keeps a second baseline when they differ, and a still passes when it matches either.

Kit changes are additive and opt-in: without its new prop a component draws exactly as it did (L-kit-D060). The
approved-acts mode proves it on a real film: every act a person approved (an approval in approvals.json of its
registered render) has its boundary frames and one frame per shot rendered with this kit, at the approved render's
sample count on software GL, and compared with the approved render at the same 45 dB bar. `--plan` lists the frames
without rendering. Run it before a kit change goes in while a film is in production.

## Rules the kit keeps for you

Exact 0 and 1 at the ends of every ease · lookups that never throw · no Catmull-Rom overshoot · staggers capped at
0.6 s and half the entrance · nothing scales from zero (kinetic words start at 85%) · a count-up holds each figure for
three frames, one value per frame, over a box that never re-centres · markers grow in as their line arrives · one
accent-line weight per look · a logo build fitted by everything it draws · a fading card fades as one sheet · clips
on untransformed wrappers · raster hints only while moving · a world layer untransformed at rest · a motif moves as a
whole below its size threshold, never loops but to breathe, overshoots only where the brand allows, and runs one
gesture per channel with four still frames between momentary ones.
