# The open web renderer

Slate's third engine, beside Remotion and After Effects, and the one that needs no Remotion licence (docs/PLAN.md §2
and decision 5). A scene is a local web page that can draw its act at any time t: GSAP timelines seeked frame by frame,
three.js and React Three Fiber for 3D, the DOM for UI. The renderer loads that page in a headless browser that can't
reach the network, captures it frame by frame, averages sub-frames into true motion blur and hands the frames to ffmpeg.
It shares the film clock, the brand, the motion trace, the events and the gates with the other two engines.

```
slate render --act 2 --engine web                       how you'll normally run it (chunked, resumable, registered)
node engine/cli.mjs web-render --json '{"film": "…", "act": 2}'     what slate calls
node engine/web/render.mjs --json '{…}'                 the same, on its own (the tests use both)
```

## What happens in a render

1. **The call meets the film clock** (`lib/job.mjs`). The frame rate and size come from `film.json`; the act's frames
   from `timing/shots.json`, through engine-core's own loader, exactly as a Remotion act gets them. Nothing here
   assumes a rate or a size.
2. **A local server** (`lib/server.mjs`) binds to 127.0.0.1 on a port the OS picks and serves the scene folder (`/`),
   the film folder read-only (`/_slate/film/`) and the brand folder (`/_slate/brand/`). Scene scripts are compiled
   when the page asks for them (`lib/bundle.mjs`, esbuild): a scene imports `three`, `gsap`, `@slate/web`,
   `@slate/kit/core` and `@film/data` by name and needs no build step and no `node_modules` of its own. Nothing
   outside those folders is reachable, by `..` or by symlink.
3. **One headless Chrome**: Remotion's chrome-headless-shell (the engine's `node_modules/.remotion`, found through
   engine-core's browser helper), driven by puppeteer (`lib/browser.mjs`). puppeteer's own Chrome is never used: every
   launch passes the shell as `executablePath`. Point `SLATE_WEB_BROWSER` at any chrome-headless-shell and the web
   engine doesn't load a single Remotion package (a test proves it).
4. **A frozen clock** (`lib/page-clock.mjs`), installed before the page's own scripts run: `Date`, `performance.now`
   and `requestAnimationFrame` follow the frame time, `Math.random` is seeded, CSS animations and Web Animations are
   paused and set to the frame time, and GSAP's lag smoothing is off.
5. **The scene registers itself**: `window.slateScene = {duration, fps, seek(t), ready()}`. The renderer awaits
   `ready()` once, then checks the scene against the film: its rate must be the film's, its length the act's (to half
   a frame), its fonts must pass the font gate, and it must not have reached for the network or thrown.
6. **Every frame, every sample**: the clock is set, `seek(t)` is awaited, CSS animations are set, queued animation
   frames run once, then the renderer waits for fonts, decoded images and a painted frame, and takes a PNG screenshot.
   `t = (frame + offset) × fps_den ÷ fps_num`, in act seconds (0 at the act's first frame).
7. **ffmpeg** (`lib/encode.mjs`) takes the PNGs in frame order, averages each frame's samples in 16-bit (tmix on
   rgb48) and encodes ProRes 422 HQ 10-bit 4:2:2 BT.709 for a `.mov` master, lossless x264 in the same 10-bit 4:2:2
   for a `.mkv` chunk, or one averaged frame for a `.png` still. The chain is render_mb's, byte for byte (a test holds
   them together), so a web act joins, splices, masters and gates exactly like a Remotion act.
8. **Proof before replacement**: the output is written as `<out>.part`, its frames are counted with ffprobe, and only
   an exact count replaces `<out>`. A short render is kept as `.short` for a look and the render refuses.
9. **The motion trace**: moves made with `tween()` are written to `timing/motion/act<N>.json`, the file
   `slate qa motion` checks.

Frames go to pages in turn (frame i to page i mod n), so every page only moves forward in time and any number of pages
draws the same pictures.

## Writing a scene

Start from the template (`template/`): `node engine/cli.mjs web-render --json '{"film": "…", "act": N, "scaffold":
true}'` copies it into `scenes/act<N>/web/` (and refuses to overwrite a scene that's there). `slate new` is meant to do
the same when a brand's look picks the web engine (`look.engine: "web"` in the brand pack, read by the kit's
`sceneEngine()`); until the state module calls it, run the scaffold yourself. The template's moves shrink to fit an act
shorter than the brand's durations, so its beat always lands inside the act. The film's generated tsconfig maps
`@slate/web` to this runtime, so `tsc` from the film folder type-checks a web scene too. A scene is `index.html` plus a
script:

```ts
import * as THREE from 'three';
import {defineEvent, durRank, frameSize, roleEase, t90Of, tokens, typeStyle, word} from '@slate/kit/core';
import {actStart, defineScene, sceneAct, tween} from '@slate/web';
import {createRenderer, renderOnSeek} from '@slate/web/three';

// 1. build the picture once, at module level, in the brand's tokens
// 2. move things with tween(): the brand's eases and durations, whole film frames, traced
tween('#title', {opacity: [0, 1], y: [24, 0]}, {id: 'title-in', at: word('W03', 'clock'), dur: 'base', entrance: true});
// 3. put each beat that wants a sound next to the move that makes it
defineEvent({shot: 'W03', id: 'title-lands', t: /* 90% of travel */ …, kind: 'land', size: 's', note: 'the title settles'});
// 4. finish with defineScene(): it registers window.slateScene
defineScene();
```

**The one rule: a frame is a function of t.** Any frame renders alone, in any order, on any page, and comes out the
same every time. The renderer freezes `Date`, `performance.now`, animation frames and `Math.random` so that libraries
behave, but a scene should read time from `seek(t)` (or R3F's clock) and nothing else. Timers stay real: never animate
with `setTimeout`.

What `@slate/web` gives a scene (`runtime/`):

| | |
|---|---|
| `defineScene({seek?, render?, ready?, events?, silent?, fonts?})` | registers `window.slateScene`; its length comes from the film clock |
| `timeline()`, `addTimeline(tl)`, `onSeek(fn)`, `onRender(fn)`, `onReady(fn)` | GSAP timelines are paused and seeked every frame (`totalTime(t)`); GSAP's root timeline follows t too, so plain `gsap.to()` tweens work |
| `tween(target, {prop: to or [from, to]}, {id, at or frame_in, dur or frame_out, ease?, entrance?, shot?, unit?, size?, exception?})` | one move per property on whole film frames, drawn by GSAP with the brand's own curve, registered with the kit's motion registry for the trace |
| `brandEase(name)`, `registerBrandEases()` | the brand's eases as GSAP eases (exactly 0 at the start and 1 at the end; GSAP rounds positions to 1e-7 s, so the ease snaps to its ends within 1e-4 of a frame) and as `brand.<name>` / `brand.<role>` strings |
| `sceneAct()`, `actStart()`, `filmTime(t)`, `actTime(s)`, `timeOfFrame(f)`, `sceneTime()` | where the scene is: act time ↔ film time (beats and word timings are film seconds) |
| `bindSequence(img, {dir, pattern?})`, `sequenceUrl()` | plates and captures as image sequences, one image per film frame |
| `runWebFontGate()` | the kit's font gate, faces loaded the web renderer's way (ready() runs it) |
| `@slate/web/three`: `createRenderer()`, `renderOnSeek()`, `Accumulator` | a film-sized WebGL renderer drawn once per seek; the accumulation renderer (depth of field and anti-aliasing), generalized from the films' `Accum.tsx` |
| `@slate/web/r3f`: `mountR3F(<Stage/>)`, `<Accum/>` | React Three Fiber with `frameloop: 'never'`, advanced once per seek; R3F's clock reads act seconds |

Use the brand's **roles and ranks** (`roleEase('entrance')`, `durRank(2)`) in anything meant for any brand, and its
own names (`'enter'`, `'base'`) in a film's scenes. `slate qa motion` accepts only the brand's named eases and
duration tokens, and entrances that start from at least 85% scale.

**Video**: never `<video>`. A browser can't seek a video to an exact frame on demand. Plates and captures are
conformed to image sequences at the film's rate (the plate path); show them with `bindSequence`.

**Fonts**: the page can't fetch fonts from the web. A face must be installed on this Mac, or be a font file the brand
pack names by path, or, for a family the pack serves from Google Fonts or a type service, a local copy of its files in
`<SLATE_DATA>/fonts/<Family>/` (or the brand folder's `fonts/<Family>/`; weights and styles are read from the file
names, and a variable font covers every weight). Font files never go into git. The gate measures every declared face
against three generic families and fails on a substitute: a film's render stops (`on_fonts: "fail"`, the default with a
film), and a scene rendered on its own, which is only ever a preview, reports the failed gate and carries on
(`on_fonts: "report"`, the default without a film).

**Beats**: `defineEvent` from the kit, next to the move that makes the sound, in film seconds.
`web-render {"emit": true}` writes `timing/visual_events/act<N>.json` (or `{"silent": true, "reason": …}` from
`defineScene({silent: '…'})`) and the motion trace from the page itself, checked with engine-core's own rules.

## The call

| arg | |
|---|---|
| `film`, `act` | the film folder and act (the act can be left out when the film has one) |
| `scene` | a scene folder or `.html` (default `<film>/scenes/act<N>/web/`; with no film, the template) |
| `from`, `to` | act frames, inclusive (default: the whole act) |
| `sub` or `samples`, `draft`, `shutter` | motion-blur samples per frame (16; 4 with `draft`; up to 64) over a shutter in degrees (180, centred on the frame, as the films did) |
| `out`, `codec` | `.mov` (ProRes 422 HQ), `.mkv` (lossless chunk) or `.png` (one averaged frame); default `<film>/renders/act<N>.web.mov` |
| `gl` | `angle` (the GPU, as the films rendered) or `swangle` (software: bit-exact run to run, for contracts and tests); also `egl`, `swiftshader`, `vulkan`, `angle-egl` |
| `concurrency` | pages drawing at once (default a quarter of the cores, 1 to 4) |
| `on_blocked`, `on_fonts` | `fail` or `report` (see above and below) |
| `trace`, `emit`, `scaffold` (+ `force`), `hashes`, `frames_dir` | the motion trace (on with a film), beats, a new scene from the template, every output frame's md5, every sub-frame's PNG |
| `brand_path` | the brand pack (slate's bridge passes the film's) |
| `frames`, `fps_num`, `fps_den`, `width`, `height` | a scene rendered without a film gets a scratch film of this shape (default: the engine's neutral film) |
| `seed`, `epoch`, `browser`, `load_timeout_ms`, `seek_timeout_ms` | the page's random seed and `Date` epoch, another headless shell, patience |

The result carries the file, frame count, sha256, `bundle_hash` (a hash of everything the page loaded), the stream's
facts, the scene's runtime and problems, the browser (path, version, pid), every network attempt, the page's warnings
and errors, the trace, and the gates: `web.network` and `fonts`. Refusals exit 1, bad calls 2, a missing scene or
browser 3, each with a hint.

## Why it only ever loads our own pages

The web belongs to Aside on Quinn's Macs; puppeteer only ever loads the renderer's own local page. And a frame that
depends on the network isn't a function of t: a font that loads on one run and not the next changes the film. So the
network is closed three ways, and every attempt is written down:

1. **request interception**: any request that isn't to the local server's origin (or `data:`/`blob:`) is aborted, even
   another server on this very Mac;
2. **a wall**: Chrome's proxy is a small server on 127.0.0.1 that records where each connection was headed and refuses
   it, which catches what interception can't see (WebSockets, service workers);
3. **no names resolve** except the loopback address, and WebRTC may not use UDP outside the proxy.

A render stops at the first attempt (`on_blocked: "fail"`) and names it; `"report"` renders anyway and lists every
attempt in the result and a FAIL `web.network` gate. New windows a page opens are closed and reported.

## The gates it faces

The same ones as the other engines. The render module counts frames on every chunk, proves every join by PSNR and
registers the act (`renders/registry.json`); `slate qa` runs the film gates on the render (glitch scan, act joins,
motion on the trace and on the pixels); the web renderer adds its own `web.network` and `fonts` results to every
render, and refuses a scene whose rate or length doesn't match the film clock.

## Determinism

Everything a frame depends on is fixed: the clock, the seed, the fonts, the page size (the film's, at a device pixel
ratio of 1), the colour profile (sRGB). With `gl: "swangle"` renders are bit-identical run to run and with any number
of pages (tests hold that); the GPU raster (`angle`) can differ by about one level between runs, as it did for the films.

## Licences

- **GSAP 3.15** is under GSAP's "Standard 'no charge' License" (Webflow): free, commercial use included, but not in a
  tool that lets people build visual animations without code in competition with Webflow. Slate is a code-first film
  engine, but **check GSAP's licence terms before Slate is released publicly**.
- three.js, React Three Fiber and drei are MIT; puppeteer is Apache-2.0; esbuild is MIT. ffmpeg runs as a separate
  program, never linked.
- **HyperFrames** (HeyGen, Apache-2.0) is prior art we learned from, not code we copied: seek, don't play (the page
  never runs its own clock); paused GSAP timelines seeked per frame; screenshots with "did the frame land" checks on
  macOS, where Chrome's deterministic BeginFrame mode isn't available; video pre-extracted to image frames; one code
  path for time; web fonts made local instead of fetched. Its skill-level patterns (the router, the media ledger, the
  voice-over "carve") belong to Slate's skills and sound modules. **OpenMontage is AGPL: never copy its code.**

## Files

```
render.mjs          webRender(args): render, emit, scaffold; and the command line on its own
lib/job.mjs         the call resolved against the film clock (and a scratch film for a scene on its own)
lib/server.mjs      the local server (127.0.0.1, random port, read-only mounts, scripts compiled on request)
lib/bundle.mjs      esbuild: '@slate/web', '@slate/kit', '@film/data', assets as local URLs
lib/browser.mjs     the headless shell, its flags, request interception, the wall
lib/page-clock.mjs  the frozen clock put into every page
lib/encode.mjs      ffmpeg: the 16-bit sub-frame average, ProRes / lossless / still (render_mb's chain)
runtime/            '@slate/web': env, scene, timeline, motion, media, fonts, three, r3f
template/           the neutral starting scene: a three.js object and a line of text in the brand's tokens
```

Tests: `engine/tests/web` (vitest: `npx --no-install vitest run --project web`) and `tests/web` (pytest:
`slate render --engine web` end to end).
