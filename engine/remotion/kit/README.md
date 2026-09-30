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
  `tests/vectors/eases.json` and `t90.json`.
- **Tokens and fonts** (`core/tokens.ts`, `core/fontplan.ts`, `fonts.tsx`): typed tokens, CSS variables
  (`<TokenScope>`), and the font gate.
- **Looks** (`core/looks.ts`): `paper-2.5d` (papery brands only), `flat` (the default), `editorial`, `blueprint`; the
  AE bridge's 13 looks by name.
- **Components**: `Card`, `FadeCard`, `Ground`, `Grain` (`paper.tsx`); `Camera`, `LookAtCamera`, `WorldLayer`, 2D
  camera helpers (`camera.tsx`); `PerChar`, `PerWord`, `Arrive`, `CountUp` (`type.tsx`); app UI (`ui.tsx`); charts
  (`charts.tsx`); `LogoBuild`, `LogoSting`, `Ripple` (`logo.tsx`); `MotifMark`, `MarkCharacter`, `gesture()`
  (`motif.tsx`); `KineticWords`, `KineticStack` (`kinetic.tsx`); `Captions`; `PlaceholderAct`; `KitReviewSheet`.

## For the engine

- **Entry.** Alias `@slate/kit` to this folder and `@film/data` to the film's generated data module. Register
  Google Fonts families before the root (`registerGoogleFont('Inter', loadFont)`), one static import each.
- **Placeholders.** An act with no scene yet renders `<ActRoot act={n}><PlaceholderAct act={n} /></ActRoot>`.
- **emit-events, check, motion trace.** `node/collect.mjs` `collectAct({act, beats, filmData})` bundles the act's
  beats with esbuild and returns `{events, eventProblems, trace, motionProblems, misses, missLabels, ok}`; write
  `events` to `timing/visual_events/actN.json` and `trace` to `timing/motion/actN.json`.
- **Font gate.** The page logs one line, `SLATE_FONT_GATE {…}`, and a failure cancels the render with the same
  report in its message. `node/stills.mjs` `findGateReport(text)` finds it; `core/fontplan.ts` `toGateResult()` turns
  it into a gate result.
- **Stills.** `node/stills.mjs` bundles and renders with @remotion/renderer; it never downloads a browser (pass one).

## Golden stills

```
cd engine
node remotion/kit/node/golden.mjs            # compare with tests/kit/golden/ (45 dB PSNR, swangle)
node remotion/kit/node/golden.mjs --update   # rewrite the baselines: look at every still before keeping them
```

Software GL (`swangle`) is bit-exact run to run. On a GPU renderer (`--gl=angle`) it isn't, so `--update` renders each
still twice and keeps a second baseline when they differ, and a still passes when it matches either.

## Rules the kit keeps for you

Exact 0 and 1 at the ends of every ease · lookups that never throw · no Catmull-Rom overshoot · staggers capped at
0.6 s and half the entrance · nothing scales from zero (kinetic words start at 85%) · a count-up holds each figure for
three frames · a fading card fades as one sheet · clips on untransformed wrappers · raster hints only while moving · a
world layer untransformed at rest · a motif moves as a whole below its size threshold, never loops but to breathe, and
overshoots only where the brand allows.
