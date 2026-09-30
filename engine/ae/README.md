# The After Effects lane

After Effects finishes every film a person supervises (an editable project plus the masters) and is a second way to
build scenes. This folder holds Slate's ExtendScript; the Python side is `src/slate/ae/`. Nothing here knows a brand
or a film: sizes, frame rates, colours, fonts, eases and logos all come from the film's data and its brand pack.

## Who starts After Effects

- **A person does.** Every command that drives AE or aerender needs `--go`, which means "I'm starting this now". An
  agent passes it only on the person's say-so.
- **Never for an autonomous film.** Adobe's enterprise terms (PSLT §1.2) bar operations a person didn't start, and until
  the person's own licence terms are checked, Slate treats that as the rule. Autonomous films are mastered with ffmpeg
  and held; once the person approves one, they can switch it to supervised and start its AE finish themselves.
- **Slate never quits AE and never discards work.** When AE has to be closed (masters, preference edits), the command
  stops with exit 5 and asks the person to save their work and quit. Before a script replaces the open project, any
  unsaved work in it is saved to `~/Library/Application Support/Slate/ae/preserved/`.

## Two routes in

1. **Slate's own route** (needs no special AE setting): `osascript` tells AE to `DoScriptFile` a small wrapper that
   pins the run's arguments into `$.global.SLATE_ARGS` and evaluates one of the scripts here. AE 26.5 returns "0" to
   AppleScript whatever a script returns, so scripts report back through `app.settings` into AE's own prefs file (read
   back byte for byte by `slate.ae.report`), or through a `report.json` when the person has allowed scripts to write
   files. Building, saving, reading JSON and aerender all work with that setting off.
2. **Higgsfield's AE bridge** (`fnf-after-effects-mcp`, pinned in `registry.json`): its MCP operations and its 13
   motion-design skills, used as AE looks. It needs "Allow Scripts to Write Files and Access Network", which only the
   person turns on; Slate reads that setting and never changes it. A version is used for scenes only after
   `slate ae bridge acceptance --go` passes on it. Where the bridge isn't ready, Slate's own library builds scenes.
   The looks are referenced by name and fetched from the bridge when used (its skill corpus has no redistribution
   grant, so Slate never copies it).

Both routes share one lock: Slate's `ae` resource lock (the job queue's) plus the bridge's own `dispatcher-busy.lock`
protocol, so AE never gets a second script while one runs (that raises a modal that blocks all scripting).

## The scripts

| File | What it does |
|---|---|
| `lib/slate_math.jsx` | Pure ES3 maths, no AE objects: JSON, the motion law (the Remotion kit's eases, T90, landing a move on a beat, the AE speed/influence rule, monotone splines), the film clock. Runs in node for the vectors. |
| `lib/slate_ae.jsx` | The AE library: run context and reporting, never-discard project handling, comps on the film clock with the sample bias, shapes, text by font role, logos from SVG data, keyframes with the brand's eases, markers, data-driven scenes, the masters queue, motion sampling. |
| `build_film.jsx` | The film's editable project from `ae/build_spec.json`: shot, act and master comps, markers, AE-built acts, a brand board, the masters queue. |
| `validate.jsx` | The project audit (`slate ae audit --go`): each check PASS, FAIL or UNCHECKED. |
| `queue_masters.jsx` | Re-queues the master chunks in an existing project. |
| `smoke_build.jsx` | The 1-second smoke comp; `slate ae smoke --go` renders and measures it. |
| `trace_export.jsx` | Samples an act's animated properties as AE evaluates them (for acts built through the bridge). |
| `open_project.jsx`, `purge.jsx`, `software_gpu.jsx`, `font_check.jsx` | Small reads and fixes the driver uses. |

ES3 rules: `var` only; no `JSON`, `Array.indexOf/forEach/map/filter`, `String.trim`, `Object.keys`, trailing commas,
getters or setters; address AE properties by match name and re-resolve after every `addProperty`; size ease arrays to
the property's dimensions (Scale is three inside AE). No `#include` may start a line inside a comment: ExtendScript's
preprocessor reads directives line by line. `tests/jsx_loader.mjs` lints for all of it.

## Acts built in After Effects

An act is built in AE when its film holds `scenes/ae/actN.json`: layers and animations as data, timed against the film
clock (timeline events, moments in a shot or the act, film frames), with the brand's eases, duration tokens, palette
tokens, font roles and logos (see `src/slate/ae/acts.py`). Python resolves every key to a frame; the build draws the
scene with each ease as AE speed and influence (sines and springs through an exact expression; a position whose ease
anticipates or overshoots is keyed per dimension, because AE's spatial speed can't run backwards). The act's motion
trace (`timing/motion/actN.json`) and visual events (`timing/visual_events/actN.json`) are written by Slate without
AE, `slate ae acts render N --go` renders it with aerender and registers it, and it faces the same gates as a Remotion
act.

## Dialogs

AE's launch dialogs (sign-in, the Home screen, crash recovery, missing fonts or footage) sit outside
`beginSuppressDialogs` and can stall a run. A person dismisses them. When the person asks, mac-cua (optional) can
dismiss them in the background with its app-control tools; its browser tools are never used.

## Tests

- `node engine/ae/tests/run_vectors.mjs` runs this ES3 maths against the shared vectors (`tests/vectors/eases.json`
  at 1e-12 and `t90.json` at 1e-9), with the local stand-in `tests/fixtures/eases.local.json` when they're missing.
- `npx --no-install vitest run --config tests/ae/vitest.config.mjs` (from `engine/`) runs the vectors, the ES3 lint and
  every script here against `tests/ae_mock.mjs`, a mock of AE's scripting DOM that draws keyframes the way AE does.
- `.venv/bin/python -m pytest tests/ae -q -p no:cacheprovider` (from the repo) runs the Python side, end to end through
  the same mock (`tests/mock_run.mjs`). Nothing in any of these starts After Effects, aerender or ffmpeg, and nothing
  reads the real AE prefs file.
