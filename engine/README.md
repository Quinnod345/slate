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
`{ok: false, error, hint, kind}` with Slate's exit codes (1 refused, 2 usage, 3 missing prerequisite, 5 busy, 75 try
later, 130 stopped). Progress goes to stderr. Every path arg (`film`, `out`, `out_dir`, `entry`, `serve_url`,
`brand_path`, `bundle_root`, `browser`, and a web scene's `scene` and `frames_dir`) may be absolute or relative to the
caller's folder; the engine runs from its own folder, so `cli.mjs` and the Python bridge resolve them first. The
Python bridge adds `brand_path` (the film's brand pack) itself, so every call sees the same `@film/data`.

**Temp folders.** Every engine process keeps its temp folders under Slate's data folder, never in `$TMPDIR`: `cli.mjs`
points `TMPDIR` at `<SLATE_DATA>/cache/tmp` before Remotion loads (Remotion makes each headless Chrome's profile with
`os.tmpdir()`), and the open web renderer gives its Chrome a fresh profile under `<SLATE_DATA>/cache/chrome`, removed
when the browser closes. At 17:19 on 2026-09-30 another session's temp cleanup deleted a live Slate Chrome's profile
out of `$TMPDIR`; under Slate's data folder only `slate render clean` sweeps, and only what a stopped process left a day
ago and no running Chrome names as its profile.

**Stopping.** A signal (SIGINT, SIGTERM, SIGHUP) still ends in one JSON object (`kind: "stopped"`), and the exit
handlers clean up: headless Chrome's process group, this process's bundles (a half-made one included) and any
`<out>.part`. A caller with a deadline sets `SLATE_ENGINE_DEADLINE_S`, and the engine stops itself the same clean way
when it passes. The Python bridge sets it a little inside every call's timeout (at most 10 s early), because a timeout
kill is a SIGKILL: it skips every cleanup, and Chrome, which runs in its own process group, would be orphaned.

| Command | Args | Returns |
|---|---|---|
| `version` | none | versions (pinned and installed), the commands, the browser, the kit, the web renderer |
| `browser-ensure` | none | the browser's version and path; downloads it once |
| `entry` | `film` | the generated files, compositions and acts |
| `bundle` | `film`; `release: true` deletes it; `{cleanup: true}` alone deletes every unused bundle | `serve_url`, `bundle_hash`, `reused`, `compositions`, `broken` (acts left out because they don't build) |
| `render` | `film`, `act` (or `"film"`), `from`/`to` (master frames, inclusive), `sub` (default 16; `draft: true` is 4), `out` (`.mov` ProRes, `.mkv` lossless chunk), `concurrency`, `gl`, `serve_url`, `trace` | `file`, `frames`, `film_frames`, `frame_count`, `samples`, `sha256`, `bundle_hash`, the stream's facts, `trace` |
| `stills` | `film` (optional with `entry`), `specs: ["Act2:120"]`, `scale`, `out_dir`, `contact_sheet: {comp, every_s \| count \| frames, columns, scale}`, `entry`, `serve_url`, `gl`, `pixels` | the stills (with sha256 and, asked, a pixel hash), the sheet and its index (per cell: frame, shot, timecode; for the sheet: `cell` [w, h], `padding`, `margin`, `labels`) |
| `emit-events` | `film`, `act` (default: every built act), `dry_run` (check everything, write nothing; a web act's page is opened and read, nothing written) | per act: the file, beats or silence, kinds, capture beats, the motion trace; in a dry run its problems and word misses |
| `trace` | `film`, `act` | one act's motion trace from the kit's motion registry, as the act is now (`slate render` refreshes it after each act render); a web act says its renderer writes its own |
| `check` | `film`, `act` (or `acts: [N, …]`: one run, one bundle between them), `banned_words` (the brand's list; absent or null means UNCHECKED, `[]` means it bans none), `contracts`, `hold` | gate results (each filed under the `scenes` stage) and the worst status (per act, with `acts`); a web act is checked through the open web renderer |
| `web-render` | whatever the open web renderer takes: a render, `{emit: true}` (with `dry_run`), `{scaffold: true}` | `engine/web/render.mjs`'s `webRender(args)` result |

A caller that renders several acts or stills makes one bundle (`bundle`), passes its `serve_url` to each call, then
releases it (`bundle` with `release: true`). A command given no `serve_url` makes its own bundle and deletes it when it
exits, on success, on an error and on SIGTERM.

## A film's scenes

```
<film>/scenes/act<N>/index.tsx   the act: its component is the default export, or a named export Act<N>
<film>/scenes/act<N>/beats.ts    optional, React-free: beats computed at module level with the kit's core
<film>/scenes/act<N>/events.ts   optional: the act's events, when kept apart
<film>/scenes/contracts.tsx      optional: the named rest states acts meet on (each exported component is one)
<film>/public/                   optional: files scenes load with staticFile()
<film>/captures/ <film>/plates/  conformed captures and plates: staticFile('captures/<name>/frames/f00012.jpg'),
                                 staticFile('plates/<shot>/plate/frames/f00012.jpg') (and …/matte/frames)
<film>/.slate/                   generated (entry, Root, act<N>, film, film-data, placeholder, broken, rest-states,
                                 label, spoken.json, public/): never edit
<film>/tsconfig.json             generated: type checks run from the film folder
```

An act's entry point is its `index.tsx`. A folder with sources but no index (an `Act2.tsx` on its own) isn't guessed
at: the entry says so in its warnings, the act renders as a placeholder, and emit-events and the check name the file.

To type-check the way the hand-in check does, from the film folder:
`node <engine>/node_modules/typescript/bin/tsc -p tsconfig.json` (the generated tsconfig's first lines give the path).

Scenes import only `@slate/kit` (this engine's kit), `@film/data` and packages; never a relative path out of the film
(the films reached their kit 4–5 folders up, and a moved folder broke everything). An act's component receives
`{sub, act, firstFrame}`: its motion-blur sample count, its number and its first film frame. What the engine reads from
an act's modules (`events.*` first, then `beats.*`, then `index.*`):

- `events`: a list of visual events, or a function returning one. The kit's `defineEvent()` registrations count too.
- `silent`: a string saying why the act has no beats. An act with neither is refused, because "no file" and "no beats"
  look the same to a sound engine and only one of them is a decision.
- `contracts`: `{in?: 'Name', out?: 'Name'}`, the rest states its first and last frames match.

`@film/data`'s default export is the kit's `FilmData`: `film` (id, size and frame rate from film.json, the only source
of either), `shots`, and, as the film gains them, `vo_words` (with each read's `tokens`: the script's words lined up
with what was heard), `vo_cues`, `timeline`, `events`, `captions`, `spoken` (how each number and symbol in the reads is
said), `lang` (the language's word-matching rules, `.slate/lang.json`) and `brand` (brand.json with its logo SVGs
inlined). The Python bridge writes `.slate/spoken.json` and `.slate/lang.json` from slate.timing before every call, so
the kit's `word()` answers exactly as `slate.timing.words.word_time` does (a test holds the two to the same answers). It also exports `fps`, `acts` and the types `SceneProps`, `Shot`,
`FilmInfo`. An act without scenes yet renders as a placeholder, so the whole film renders from day one: the kit's
`PlaceholderAct` (in the brand's look, inside its `ActRoot`) when the kit is there, the engine's own neutral one (shot
ids, on-screen text and VO) otherwise.

The generated Root registers `Act1…ActN`, `Film` (every act in order) and `Contract-<Name>` for each rest state, with
the same id, length, rate and size as the kit's `actComposition` (a test holds the two together).

**Parallel builders never break each other.** The films' lessons 2 and 3: one act's half-written file, or a throw at
its module's load, took down every composition in the shared bundle. So each composition loads its own module only
when it renders (`.slate/act<N>.tsx`, `.slate/film.tsx`, through Remotion's `lazyComponent`), and before bundling each
act's module is built once with esbuild. An act that doesn't build is left out behind a stand-in (the entry lists it
under `broken`, with the file and line), so the bundle still builds; rendering it, a still of it, or the whole film is
refused by name, and every other act renders as usual. An act that throws as it loads fails its own composition and
the film, and nothing else. Tests render act 1 while act 2 is half-written and while it throws.

The generated entry
registers a loader for each of the brand's Google Fonts families before anything renders (`registerGoogleFont`),
choosing them by the kit's own font plan; a family `@remotion/google-fonts` doesn't have is a warning, and the font
gate then fails the render loudly.

## Bundles

One bundle per film folder per source hash, in `SLATE_DATA/cache/bundles/`. The hash covers exactly what goes into the
bundle (esbuild walks the entry's imports with the same aliases), plus film.json, the lockfile and the Remotion
version, so a kit change the film never imports keeps its bundle. The public folder is linked, not copied: the films'
290 MB bundles were mostly copies of their plates, and 56 of them once filled 14 GB. It is `.slate/public/`, which
holds links to each entry of the film's own `public/` and to its `captures/` and `plates/` (a name the film's
`public/` already uses wins, with a note), so image sequences are read in place. Deleting a bundle or that folder
removes links, never what they point at. Every process using a bundle holds a lease, and nothing deletes a bundle a
live process holds; pruning removes a film's older bundles and anything unleased for a day.

## Rendering with motion blur (render_mb)

Generalized from the films' `pipeline/r3f/render_mb.mjs`, and the default renderer. Each master frame is drawn `sub`
times across a 180° shutter centred on the frame (phase −90°): sample k of frame f is at `f − 0.25 + 0.5(k + 0.5)/sub`.
Chrome's PNG frames stream straight into ffmpeg, which averages each group in 16-bit (`tmix` on rgb48), converts with
the BT.709 matrix, and encodes ProRes 422 HQ, 10-bit 4:2:2, at the film's own rate. 16 samples is the delivery bar;
4 is draft. A range written to `.mkv` is a lossless chunk (x264 qp 0 in the same 10-bit 4:2:2) for the render module
to splice. Every output is written to `<out>.part`, its frames are counted with ffprobe, and only the exact count
replaces `<out>`; a short render is kept as `<out>.short.mov` for a look and reported as refused.

Colour tags: ffmpeg 9 lets the frames' own metadata (unknown, from PNG) override `-color_primaries` and `-color_trc`, so
the films' renders came out tagged with the BT.709 matrix but unknown primaries and transfer (probed on one of their
act renders, 2026-09-30). The chain now stamps all three with `setparams`; the pixels are identical (same frame md5),
only the tags After Effects reads change.

`trace: true` also writes the act's motion trace (`timing/motion/actN.json`) from the kit's motion registry.

Fonts: when a page runs the kit's font gate, its verdict comes back in the result's `gates` as a `fonts` gate result
(read with the kit's own `parseReportLine` and `toGateResult`). A failed gate stops the render (fonts are by reference
and never substituted), and the refusal carries that gate result in its details. Stills do the same.

GL: `angle` (the GPU through Metal), as the films rendered. `swangle` (software) is bit-exact run to run, so the
contract check and golden stills use it; the GPU raster differs by about one level between runs.

Every tile, every sample: renders, stills and the check's contract stills launch Remotion's headless shell through
`remotion/chrome-complete.sh`, which adds `--run-all-compositor-stages-before-draw --disable-checker-imaging`, so Chrome
rasterises every tile and decodes every image before a sample is drawn. Without them a tile that isn't ready in time is
left out of its sub-frame and whatever is behind it shows through: a faint grey veil for one frame (the films' lesson
L-kit-S008, from their 2026-09-30 renders; about 6% more render time). Remotion's `openBrowser` takes no extra flags,
so they ride on the executable, as the films' renderer did. The open web renderer passes the same two flags itself.

## Events, motion traces and the hand-in check

`emit-events` loads each act's modules in Node with esbuild (no rendering), so the beats land on the frames the
picture uses, and writes `timing/visual_events/actN.json` (`[{shot, id, t, kind, size, note, start?, dur?, …}]`, kinds
tap, type, land, snap, count, draw, data, page, stamp, whoosh, swell, character, impact) or `{silent: true, reason}`.
`t` is always the beat, the film second the motion lands. A sustained motion also gives its span, `start` (film
seconds) and `dur`: a draw and a count must, and end on `t` (t = start + dur; the kit's `defineEvent` works `t` out from
them); a whoosh and a swell peak on `t`, inside theirs; any other kind may give one, with its beat inside.
`spanProblems()` holds these rules word for word as `slate.timing.beats.problems()` states them, to half a frame
(`tests/engine_core/test_beats_parity.py` keeps the two the same). The beats are the
act's own `events`, what it registered with the kit's `defineEvent()`, and the taps of every placed capture
(`captures/<name>/beats.json`, each on its shot's act, kept with `source: "capture:<name>"`, sizes spelled s/m/l),
sorted by time, then id.

It checks what the films' best emitters checked: known kinds and sizes, a time inside the act (to half a frame, the
kit's rule) and inside its shot, unique ids, a note that says what the viewer sees, at most one impact per act, and
numbers where the sound side reads optional ones (a count's `from`/`to`, `x` across the frame, `pan` from -1 to 1, a
whole film `frame`), every span true to its beat; any other field passes through untouched. It
writes nothing while any of these fail, while the kit recorded a problem with a `defineEvent()`, while a capture tap
lands in no act, or while a word lookup fell back to an estimate: film C's emitter refused that too, because a beat on
a guessed time is a sound in the wrong place. `dry_run` (`slate emit-events --dry-run`) runs every check, writes
nothing, and lists what the real run would refuse.

From the kit it makes the same calls the kit's `node/collect.mjs` `collectAct()` makes, on the module instances the
act registered into: `actEvents(act)`, `eventProblems()`, `traceMotions({act})` (the motion trace, §12's shape),
`missLabels()`, `motionProblems()`. The engine loads the act itself because it also loads the picture (`index.*`),
with media and Google Fonts imports stubbed for Node, and acts that don't use the kit at all.

`check <act>` returns gate results: `typecheck` (TypeScript 5.9.3, strict, from the film folder, over the act and what it
imports), `module` (it loads in Node without throwing), `word_misses` (none of the kit's lookups fell back to an
estimate), `kit_problems` (for acts that use the kit: what its motion registry had to assume, for a person to read),
`banned_words` (the brand's list against the string literals and JSX text of the act and every scene file it imports,
JSON and text copy included, and its shots' on-screen text; imports, types, styles, ids, event notes and the act's
notes are never on screen), `events` (present, valid, and exactly what emit-events would write now, so a word that
stopped matching fails it) and `contracts` (each boundary's rest state bit-identical on both sides and in
`scenes/contracts`, held for 6 frames), and `fonts` whenever the contract stills ran the kit's font gate. The kit's
note that its neutral motion law stands in (a brand without a motion law yet) is listed, not held against the act. A
syntax error comes back as failed gates with its file and line, never as a crash. `acts: [N, …]` checks several acts
in one run: the bundle for their contracts is made once, only if a contract needs pixels, and deleted at the end
(`slate check all` does this).

A web act (`scenes/actN/web/index.html`, built for the open web renderer) is handed in through that renderer, with the
same gates where they mean the same thing: `typecheck` (its scene scripts, from the film folder, with `@slate/web`
mapped), `page` (the page loads and draws its first frame without an error), `word_misses` and `kit_problems` (read
from the page: the kit runs inside it), `network` and `fonts` (the page's own wall and font gate), `banned_words` (its
scripts, its page's visible text and its JSON and text copy), `events` (the file against what the page says now) and
`contracts` (a web scene declares no rest states; when a neighbour declares one at their boundary the gate says it
can't compare them yet, UNCHECKED, rather than passing). The page is opened once, in a dry-run emit: nothing is
written.

## Browsers

Every child process runs with `BROWSER=none`. The engine drives headless Chrome only through Remotion (as
`remotion/chrome-complete.sh`, the shell with every tile finished) and the open web renderer, against their own local
servers; nothing here browses the web. Their profiles live under Slate's data folder, never `$TMPDIR` (see Temp
folders above). Nothing here starts Remotion Studio. If a person ever does:

```
BROWSER=none npx --no-install remotion studio <film>/.slate/entry.tsx --no-open --config=remotion/remotion.config.ts
```

## Dependency decisions (plan, appendix A)

- **remotion, @remotion/bundler, renderer, cli: 4.0.521, pinned exactly.** Upgrade within 4.0.x only after the
  render-parity check; never 5.0 unplanned (it brings licence and colour changes). `version` reports any drift.
  - **Where upstream is (checked 2026-09-30 on GitHub releases):** the latest is 4.0.531 (released that day). **4.0.530
    changes pixels:** its compositor fix, "full-range video with non-J pixel formats (10/12-bit) being range-expanded
    twice", means any full-range 10- or 12-bit video a scene plays (an `OffthreadVideo` of a camera or screen
    recording) decodes differently from 4.0.521, which expands its range twice (too much contrast). Slate's captures
    and plates are conformed JPEG sequences, which never take that path; until the pin moves, conform any such clip the
    same way (or to limited range) rather than playing it directly. The parity check for any move past 4.0.529 must
    include one full-range 10-bit clip and expect its pixels to change (the new ones are right), while every other
    frame still matches. The same upgrade brings `HtmlInCanvasMotionBlur` (4.0.529, below).
- **@remotion/google-fonts 4.0.521: kept**, for open fonts. When the engine loads an act in Node, font loaders are
  stubbed (fonts load in the browser).
- **@remotion/motion-blur: learn from.** It isn't a dependency, and a test keeps it out of package.json. render_mb
  stays the default renderer. Before any switch, test its `HtmlInCanvasMotionBlur` (Remotion 4.0.529 and later)
  against render_mb on the same act, frame for frame. That needs an engine at 4.0.529 or later, which only the
  render-parity check for a Remotion upgrade installs, so the comparison waits for it:
  1. install the candidate engine in a scratch folder (never over the pinned one) and point `SLATE_ENGINE_DIR` at it;
  2. render one kit-built act both ways at 16 samples, same frames, same GL (`swangle`, so runs are repeatable);
  3. compare them frame by frame (`slate qa psnr A B`), and look at the fastest moves side by side:
     the shutter (180°, centred), edge gradients and colour must match; speed and memory are the tiebreak;
  4. write the result here, and switch only if it matches and is simpler or faster.
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
globally, then applies Slate's overrides:

- **Voice:** code examples voice with Slate's pinned TTS model instead of `eleven_multilingual_v2` (a table comparing
  models keeps its rows).
- **Sound:** Remotion's sfx page, a list of stock and meme sounds to load from the internet, keeps only its frontmatter
  and Slate's note: a film's sound is designed for its brand.
- **Studio never auto-opens:** Remotion's "open the preview" section becomes Slate's note (previews are stills and
  renders, or a page opened through Aside); every other line that opens Studio or its local URL becomes a one-line
  note; every `npx remotion studio` gets `--no-open`.
- **Keys:** the ElevenLabs key setup starts with Slate's note (the Keychain, through `slate connect elevenlabs`, typed
  in Terminal), before any `.env` step.
- Every SKILL.md starts with a pointer to `SLATE_OVERRIDES.md`.

`--dry-run` shows the plan. The pins were checked against GitHub on 2026-09-30: both commits exist, and each repo's
take list matches its tree at that commit (12 and 10 SKILL.md folders). The overrides are tested on short stand-in
text, and on the vendors' real pinned text when `SLATE_TEST_SKILLS_MIRROR` names local clones of both repos
(`tests/engine_core/test_skills.py`); run that before the real fetch.

## Tests

`npm test` (from engine/) runs every suite: `tests/core` here, and each `tests/<name>/vitest.config.*` under its own
config. One suite: `npx --no-install vitest run --project core`. Files run one at a time, because renders start
headless Chrome. The Python side is `tests/engine_core` (pytest).
