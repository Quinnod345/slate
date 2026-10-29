---
name: slate-build
description: Build a Slate film's scenes with the kit, act by act. Covers kit-first foundations, act scaffolds and placeholders (a first working version of every shot, fast), beats keyed to the voice's words, frame-exact contracts between acts, exporting visual events, the hand-in check, stills and previews, briefs for parallel act builders, real app captures for a film, product UI drawn truthfully, and acts built in After Effects. Use when writing or fixing an act's scene code under scenes/, briefing act builders, capturing an app for a film, handing an act in, or when a render shows a renderer-specific glitch.
---

# slate-build

Scenes are code: a pure function of film time, drawn in the brand's tokens on the brand's motion law, keyed to the
words the voice says. The shared kit (`@slate/kit`) knows no brand, frame size or frame rate of its own; the film's data
(`@film/data`) and the brand pack supply them. Builders write only their act's files; everything they import is
read-only.

This is stage 7 of the production. Before it: the script, the voice and the timing exist (slate-story, slate-voice), and
the brand's look and motion law are approved (slate-brand, slate-motion).

## 1. Kit first

Nothing is built act by act until the shared foundation is ready: the brand's tokens (from its own code), its eases and
duration scale, the clock, cameras and surfaces in the brand's look, UI parts, charts, logos compiled from SVG, fonts,
and the film's sample data. Most of that is the kit plus the brand pack; anything film-specific that several acts share
(a report surface, a recurring card, a named rest state) is built once, before the builders start, and then read-only.
Reuse surfaces already matched to the product rather than redrawing them. See [kit](references/kit.md).
Choose the representation for the transformations the scene needs. An object that opens, folds, reshapes, splits or
travels into another screen needs parts whose geometry, content and identities can survive that move. React/SVG,
DOM surfaces, meshes, footage and plates each have uses. A faithful screenshot can establish visual truth without
being the scene's animation structure. Keep product values, states and hierarchy truthful while staging the film.
For a recurring physical book, use the kit's `HingedBook` with shared caller-owned faces:
[building hinged books](references/hinged-books.md).

## 2. The act scaffold

```
<film>/scenes/act<N>/index.tsx   the act; its component is the default export
<film>/scenes/act<N>/beats.ts    every beat, computed at module level from the voice's words (React-free)
<film>/scenes/act<N>/NOTES.md    decisions, restaging, contracts, what couldn't be done
<film>/scenes/contracts.tsx      the named rest states acts meet on
<film>/public/                   files scenes load (plates, captures): linked, never copied
```

- **Placeholders first.** An act with no scene renders as the kit's placeholder (its shots, on-screen text and voice)
  in the brand's look, so the whole film plays from day one.
- **Get a first working version of every shot onto disk fast**, then refine. Sessions end without warning; what isn't
  on disk is lost. Keep NOTES.md current as you go.
- Scenes import only `@slate/kit`, `@film/data` and packages, never a relative path out of the film.
- Every beat comes from the voice's words through lookups that never throw (a miss is estimated and recorded, and must
  be empty at hand-in). Moves register with `defineMotion`, sound beats with `defineEvent`.
- **Tell the motion gate what moves**: a move whose name doesn't say it gives its `class` (rig, ui, carry or whip), and
  an exempt move gives `exempt` (`reading_sweep` or `steady`) with its `reason`. Long carries and camera drifts can use
  the kit's `even` curve. The rules are slate-motion's (eases.md).

The pattern, file by file, with a worked act: [act scaffolds](references/act-scaffolds.md).

## 3. Contracts between acts

Acts meet on **contract frames**: a named rest state drawn by one shared component, rendered by both builders on their
boundary frames and held still for at least 6 frames each side, so the joins are bit-identical and a splice or bridge
has clean frames. Everything decorative settles out over the last 0.6 s before a contract. The simplest contract is
the brand's empty ground. See [contracts](references/contracts.md).

## 4. Events: tell the sound what each beat is

Every beat that lands exports as a visual event: shot, id, time, **kind** (tap, type, land, snap, count, draw, data,
page, stamp, whoosh, swell, character, impact), **size** (s, m, l) and a plain note. The sound is designed from this
list, one sound family per kind, so every beat must say what it is. An act with no beats says so explicitly, with a
reason. **`t` is always the beat**, never the start: a sustained motion also gives its span (`start` and `dur`,
together), a draw or a count ends on its beat, and a whoosh or a swell peaks on it. See [events](references/events.md).

## 5. Hand in

```
slate emit-events --act <N>     # the act's visual events and motion trace, from its scenes
slate check <N>                 # the hand-in check
slate timing misses             # word lookups that fell back to an estimate: must be empty
```

`slate check <N>` runs, as gates: the type check from the film folder; the act module loading in Node without throwing;
an empty word-miss list; what the kit had to assume; the brand's banned words in the act's strings; the events file
matching the act; the contracts at its boundaries, bit-identical and held; and the brand's motion law on the act's
trace, so a broken law fails here, before a render. A web act hands in through the open web
renderer the same way, plus its page loading and drawing, no network, and its fonts. Only PASS counts. The full
hand-in checklist, and how to check your work with stills and previews: [hand-in](references/hand-in.md).

Passing the technical hand-in establishes the checked properties; it does not establish beauty. Review the intended
transformations in motion with sound, then improve the choreography from the requester's specific feedback. Keep
the comparison versions and feedback so the next builder can understand what changed and why (slate-motion).

Builders make stills and half-scale previews only, in their own scratch folder. Finals render centrally, one at a time
(slate-finish).

## 6. Parallel builders

If you can run sub-agents, build acts in parallel, one builder per act under one brief; otherwise build them in order
with the same brief. Each builder owns only its act's folder, reads the kit and everyone else's work read-only, writes
kit requests into its NOTES.md, and exports its events. Briefs are layered (a base brief plus the film's differences)
and end with the hand-in checklist. See [builder briefs](references/builder-briefs.md) and the fill-in template,
[builder's brief](references/builders-brief.md).

## 7. The product on screen

- **Real app footage**: `slate capture ios --spec <file>` records a pinned build on an isolated Simulator with a demo
  account and a 9:41 status bar; `slate capture web --spec <file>` captures a page through Aside at a fixed viewport,
  frame by frame. Both conform to the film's frame rate and map taps onto the voice's words. Capture for truth and
  speed; choose code when the intended move needs independently transforming product objects, restaging,
  motion blur or exact type. A camera move alone can also work with a capture. See
  [captures](references/captures.md).
- **Product UI drawn in code** follows the product exactly: its copy, its states, its order, its hierarchy. See
  [app UI](references/app-ui.md) and [charts](references/charts.md).

## 8. Web scenes: the open web renderer

A brand whose look needs what the browser does best (GSAP timelines, three.js and React Three Fiber, live DOM) can build
an act as a **web scene** instead: a local page that draws its act at any time t, rendered frame by frame with the same
motion blur, clock, events and gates (`look.engine: "web"` in the brand pack makes it the default for new acts).
`slate engine scaffold --act <N>` starts one from the neutral template (`--engine web` for a brand whose look doesn't
pick it; `--force` to replace a scene that's there); `slate render --act <N> --engine web` renders it. The page can't
reach the network, its clock is frozen to the frame, and its fonts must pass the font gate. Everything else (beats keyed
to words, events, contracts, hand-in with `slate check <N>`) is the same as a kit act. The page contract and its traps
are in the engine's own `engine/web/README.md`; its hand-in gates are in [hand-in](references/hand-in.md).

## 9. Building in After Effects

After Effects is a second way to build acts, on the same film clock and through the same gates, when a look calls for
it (the AE bridge's looks, or a brand whose motion lives in AE). Every AE step needs the owner's go, never runs for an
autonomous film, and never touches AE's preferences outside Slate's byte-safe tool. See [AE scenes](references/ae-scenes.md)
and [the AE bridge](references/ae-bridge.md).

## 10. Renderer traps

Headless Chrome snaps text to whole pixels under slow motion, races its 3D sorting when planes stack, fails some clip
paths silently, and can't see locally installed web-kit fonts. Know them before you build:
[Remotion gotchas](references/remotion-gotchas.md), [headless Chrome](references/headless-chrome.md). Slate's rules
override Remotion's own skills where they disagree: [Remotion overrides](references/remotion-overrides.md).

## Rules that hold everywhere

- Keep the script's beats, words, timing and on-screen text; restage the picture freely, and say so in NOTES.md.
- Preserve the approved baseline, exact feedback and revised version. Follow requested redesigns within their scope;
  keep outer geometry or other parts unchanged when the brief calls for that continuity.
- Never fake motion blur in scene code: the renderer averages sub-frame samples.
- One heavy process at a time; never a full-resolution final from a builder; never render on another machine.
- Nothing opens a browser: web pages are captured through Aside only.

## References

- [kit](references/kit.md): what the kit reads and draws, the rules it keeps for you, review sheets.
- [hinged-books](references/hinged-books.md): physical book surfaces, page coordinates, state, raster rules and review.
- [act-scaffolds](references/act-scaffolds.md): the act's files, beats at module level, a worked act, NOTES.md.
- [contracts](references/contracts.md): named rest states, holds, settling out, checking joins.
- [events](references/events.md): the beat vocabulary, sizes, silent acts, captures' taps.
- [hand-in](references/hand-in.md): the check, stills and previews, the checklist.
- [builder-briefs](references/builder-briefs.md): running parallel builders; ownership; conflicts; notes.
- [builders-brief](references/builders-brief.md): a fill-in builder's brief.
- [app-ui](references/app-ui.md): drawing product UI truthfully.
- [charts](references/charts.md): data on screen.
- [captures](references/captures.md): iOS and web captures, conforming, beat maps.
- [remotion-gotchas](references/remotion-gotchas.md): raster stability, depth sorting, stacking.
- [headless-chrome](references/headless-chrome.md): CSS that fails silently, fonts, performance.
- [remotion-overrides](references/remotion-overrides.md): where Slate overrides Remotion's own skills.
- [ae-scenes](references/ae-scenes.md): acts built in After Effects.
- [ae-bridge](references/ae-bridge.md): Higgsfield's AE bridge, its looks and its limits.
