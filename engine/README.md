# Slate's engine

One versioned Node project, with Remotion pinned exactly (4.0.521). The Python CLI never imports Node: it runs
`node engine/cli.mjs <command> --json '<args>'` through `slate.engine.bridge` and reads one JSON object back. Everything
a film's picture needs lives here: the Remotion kit (`remotion/kit/`), the entry generator, bundling, the motion-blur
renderer, stills, the event emitter and the hand-in check (`remotion/`), the open web renderer (`web/`) and the
ExtendScript library (`ae/`).

```
engine/
  cli.mjs                 the JSON command line (the only door from Python)
  remotion/
    entry.mjs             a film's generated entry: Root, '@film/data', placeholders, tsconfig
    bundle.mjs            one bundle per film per source hash, reused, then deleted
    render_mb.mjs         true motion blur into ProRes 422 HQ (or a lossless chunk)
    stills.mjs            review stills, labelled contact sheets, review sheets
    load_act.mjs          an act's modules, loaded in Node without rendering
    events.mjs            emit-events: visual events and motion traces
    check.mjs             the hand-in check, as gate results
    browser.mjs           chrome-headless-shell 149, GL modes
    lib/                  shared helpers (errors, children with BROWSER=none, film loading)
    neutral/, data/       a brand-free stand-in film for the engine's type check, vitest and kit review sheets
    remotion.config.ts    Remotion's settings if a person ever starts its tools by hand (Studio never opens a browser)
    kit/                  the kit (its own README)
  web/  ae/               the open web renderer and the ExtendScript library (their builders' modules)
  tests/                  vitest: tests/core (this boundary), tests/kit, tests/web…
```

## Installing

`slate engine install --npm-ci` copies the engine's sources into `~/Library/Application Support/Slate/engine/<remotion
version>/` and runs `npm ci` there (the lockfile's exact packages; never `npm install`, never global). A film renders
with the engine for its pinned Remotion, so an upgrade is a new folder, never a change to an old one. In development,
when nothing is installed, Slate runs this checkout's `engine/`; `SLATE_ENGINE_DIR` points it anywhere else.

`node cli.mjs browser-ensure` downloads Remotion's chrome-headless-shell (149.0.7790.0, the version Remotion 4.0.521 was
tested with) into `node_modules/.remotion/`. `slate engine status` shows which engine runs, its versions, the browser,
the kit, the web renderer and the bundles on disk.

## The command line

Every command takes one JSON object and prints one: `{ok: true, engine_version, protocol, command, …}`, or
`{ok: false, error, hint, kind}` with Slate's exit codes (1 refused, 2 usage, 3 missing prerequisite, 5 busy, 130
stopped by a signal). Progress goes to stderr. Paths may be absolute or relative to the caller's folder. The Python
bridge adds `brand_path` (the film's brand pack) itself, so every call sees the same `@film/data`.

| Command | Args | Returns |
|---|---|---|
| `version` | none | versions (pinned and installed), the commands, the browser, the kit, the web renderer |
| `browser-ensure` | none | the browser's version and path; downloads it once |
| `entry` | `film` | the generated files, compositions and acts |
| `bundle` | `film`; `release: true` deletes it; `{cleanup: true}` alone deletes every unused bundle | `serve_url`, `bundle_hash`, `reused` |
| `render` | `film`, `act` (or `"film"`), `from`/`to` (master frames, inclusive), `sub` (default 16; `draft: true` is 4), `out` (`.mov` ProRes, `.mkv` lossless chunk), `concurrency`, `gl`, `serve_url`, `trace` | `file`, `frames`, `film_frames`, `frame_count`, `samples`, `sha256`, `bundle_hash`, the stream's facts, `trace` |
| `stills` | `film` (optional with `entry`), `specs: ["Act2:120"]`, `scale`, `out_dir`, `contact_sheet: {comp, every_s \| count \| frames, columns, scale}`, `entry`, `serve_url`, `gl`, `pixels` | the stills (with sha256 and, asked, a pixel hash), the sheet and its index |
| `emit-events` | `film`, `act` (default: every built act) | per act: the file, beats or silence, kinds, the motion trace |
| `check` | `film`, `act`, `banned_words` (the brand's list; absent means UNCHECKED), `contracts`, `hold` | gate results and the worst status |
| `web-render` | whatever the open web renderer takes | `engine/web/render.mjs`'s `webRender(args)` result |

A caller that renders several acts or stills makes one bundle (`bundle`), passes its `serve_url` to each call, then
releases it (`bundle` with `release: true`). A command given no `serve_url` makes its own bundle and deletes it when it
exits, on success, on an error and on SIGTERM.

## A film's scenes

```
<film>/scenes/act<N>/index.tsx   the act: its component is the default export
<film>/scenes/act<N>/beats.ts    optional, React-free: beats computed at module level with the kit's core
<film>/scenes/act<N>/events.ts   optional: the act's events, when kept apart
<film>/scenes/contracts.tsx      optional: the named rest states acts meet on (each exported component is one)
<film>/public/                   optional: files scenes load with staticFile() (linked into the bundle, never copied)
<film>/.slate/                   generated (entry, Root, film-data, placeholder, label, spoken.json): never edit
<film>/tsconfig.json             generated: type checks run from the film folder
```

Scenes import only `@slate/kit` (this engine's kit), `@film/data` and packages; never a relative path out of the film
(the films reached their kit 4–5 folders up, and a moved folder broke everything). An act's component receives
`{sub, act, firstFrame}`: its motion-blur sample count, its number and its first film frame. What the engine reads from
an act's modules (`events.*` first, then `beats.*`, then `index.*`):

- `events`: a list of visual events, or a function returning one. The kit's `defineEvent()` registrations count too.
- `silent`: a string saying why the act has no beats. An act with neither is refused, because "no file" and "no beats"
  look the same to a sound engine and only one of them is a decision.
- `contracts`: `{in?: 'Name', out?: 'Name'}`, the rest states its first and last frames match.

`@film/data`'s default export is the kit's `FilmData`: `film` (id, size and frame rate from film.json, the only source
of either), `shots`, and, as the film gains them, `vo_words`, `vo_cues`, `timeline`, `events`, `captions`, `spoken` and
`brand` (brand.json with its logo SVGs inlined). It also exports `fps`, `acts` and the types `SceneProps`, `Shot`,
`FilmInfo`. An act without scenes yet renders as a placeholder, so the whole film renders from day one: the kit's
`PlaceholderAct` (in the brand's look, inside its `ActRoot`) when the kit is there, the engine's own neutral one (shot
ids, on-screen text and VO) otherwise.

The generated Root registers `Act1…ActN`, `Film` (every act in order) and `Contract-<Name>` for each rest state, with
the same id, length, rate and size as the kit's `actComposition` (a test holds the two together). The generated entry
registers a loader for each of the brand's Google Fonts families before anything renders (`registerGoogleFont`),
choosing them by the kit's own font plan; a family `@remotion/google-fonts` doesn't have is a warning, and the font
gate then fails the render loudly.

## Bundles

One bundle per film folder per source hash, in `SLATE_DATA/cache/bundles/`. The hash covers exactly what goes into the
bundle (esbuild walks the entry's imports with the same aliases), plus film.json, the lockfile and the Remotion
version, so a kit change the film never imports keeps its bundle. The public folder is linked, not copied: the films'
290 MB bundles were mostly copies of their plates, and 56 of them once filled 14 GB. Every process using a bundle
holds a lease, and nothing deletes a bundle a live process holds; pruning removes a film's older bundles and anything
unleased for a day.

## Rendering with motion blur (render_mb)

Generalized from the films' `pipeline/r3f/render_mb.mjs`, and the default renderer. Each master frame is drawn `sub`
times across a 180° shutter centred on the frame (phase −90°): sample k of frame f is at `f − 0.25 + 0.5(k + 0.5)/sub`.
Chrome's PNG frames stream straight into ffmpeg, which averages each group in 16-bit (`tmix` on rgb48), converts with
the BT.709 matrix, and encodes ProRes 422 HQ, 10-bit 4:2:2, at the film's own rate. 16 samples is the delivery bar;
4 is draft. A range written to `.mkv` is a lossless chunk (x264 qp 0 in the same 10-bit 4:2:2) for the render module
to splice. Every output is written to `<out>.part`, its frames are counted with ffprobe, and only the exact count
replaces `<out>`; a short render is kept as `<out>.short.mov` for a look and reported as refused.

Colour tags: ffmpeg 9 lets the frames' own metadata (unknown, from PNG) override `-color_primaries` and `-color_trc`, so
the films' renders came out tagged with the BT.709 matrix but unknown primaries and transfer (probed on
`build/B/renders/r3f/B1_B01-B04.mov`, 2026-09-30). The chain now stamps all three with `setparams`; the pixels are
identical (same frame md5), only the tags After Effects reads change.

`trace: true` also writes the act's motion trace (`timing/motion/actN.json`) from the kit's motion registry.

Fonts: when a page runs the kit's font gate, its verdict comes back in the result's `gates` as a `fonts` gate result
(read with the kit's own `parseReportLine` and `toGateResult`). A failed gate stops the render (fonts are by reference
and never substituted), and the refusal carries that gate result in its details. Stills do the same.

GL: `angle` (the GPU through Metal), as the films rendered. `swangle` (software) is bit-exact run to run, so the
contract check and golden stills use it; the GPU raster differs by about one level between runs.

## Events, motion traces and the hand-in check

`emit-events` loads each act's modules in Node with esbuild (no rendering), so the beats land on the frames the
picture uses, and writes `timing/visual_events/actN.json` (`[{shot, id, t, kind, size, note}]`, kinds tap, type, land,
snap, count, draw, data, page, stamp, whoosh, swell, character, impact) or `{silent: true, reason}`. It checks what the
films' best emitters checked: known kinds and sizes, a time inside the act and inside its shot, unique ids, a note that
says what the viewer sees, at most one impact per act. From the kit it reads `actEvents(act)`, `traceMotions({act})`
(the motion trace, §12's shape), `missLabels()` and the kit's problem lists.

`check <act>` returns gate results: `typecheck` (TypeScript 5.9.3, strict, from the film folder, over the act and what it
imports), `module` (it loads in Node without throwing), `word_misses` (none of the kit's lookups fell back to an
estimate), `kit_problems` (for acts that use the kit: what it had to assume, for a person to read), `banned_words` (the
brand's list against the act's string literals and JSX text, and its shots' on-screen text; imports, types, styles,
ids and event notes are never on screen), `events` (present, valid, and the same as the act says now) and
`contracts` (each boundary's rest state bit-identical on both sides and in `scenes/contracts`, held for 6 frames),
and `fonts` whenever the contract stills ran the kit's font gate. The kit's note that its neutral motion law stands in
(a brand without a motion law yet) is listed, not held against the act.

## Browsers

Every child process runs with `BROWSER=none`. The engine drives headless Chrome only through Remotion, against its
own local bundle server; nothing here browses the web. Nothing here starts Remotion Studio. If a person ever does:

```
BROWSER=none npx --no-install remotion studio <film>/.slate/entry.tsx --no-open --config=remotion/remotion.config.ts
```

## Dependency decisions (plan, appendix A)

- **remotion, @remotion/bundler, renderer, cli: 4.0.521, pinned exactly.** Upgrade within 4.0.x only after the
  render-parity check; never 5.0 unplanned (it brings licence and colour changes). `version` reports any drift.
- **@remotion/google-fonts 4.0.521: kept**, for open fonts. When the engine loads an act in Node, font loaders are
  stubbed (fonts load in the browser).
- **@remotion/motion-blur: learn from.** It isn't a dependency. Before any switch, test its `HtmlInCanvasMotionBlur`
  (Remotion 4.0.529 and later) against render_mb on the same act, frame for frame; render_mb stays the default until
  that comparison says otherwise.
- **react, react-dom 19.2.8: kept.** The bundler aliases one copy for every film.
- **typescript 5.9.3: kept**, as the type-check gate (`check`).
- **esbuild 0.28.1: kept and declared.** emit-events, the check and the bundle hash use it (the films used it
  undeclared, through @remotion/bundler).
- **chrome-headless-shell 149: kept**, Remotion-managed; `browser-ensure` fetches it and doctor runs
  `remotion browser ensure`.
- **postprocessing, @react-three/postprocessing, framer-motion: dropped.** Declared and never imported by the films;
  @react-three/postprocessing renders blank under Remotion's frame loop.

## Vendor skills

`slate engine skills` fetches remotion-dev/skills (12 skills, no licence file: depend, never vendor) and
elevenlabs/skills (10, MIT) at pinned commits into the installed engine's `skills/`, never into this repo and never
globally, then applies Slate's overrides: code examples voice with Slate's pinned TTS model instead of
`eleven_multilingual_v2`; Remotion's stock and meme sound list is removed (a film's sound is designed for its brand);
every `npx remotion studio` gets `--no-open` and no instruction opens a browser; the ElevenLabs key goes into the
Keychain through `slate connect elevenlabs`. `--dry-run` shows the plan.

## Tests

`npm test` (from engine/) runs every suite: `tests/core` here, and each `tests/<name>/vitest.config.*` under its own
config. One suite: `npx --no-install vitest run --project core`. Files run one at a time, because renders start
headless Chrome. The Python side is `tests/engine_core` (pytest).
