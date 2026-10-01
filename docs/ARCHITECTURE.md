# Slate: architecture and builder contracts

This is the brief every Slate builder works from. It fixes the layout, the shared modules, the CLI surface, the data
files and the rules, so that modules written in parallel fit together. The product plan is `docs/PLAN.md` (a copy of
the vault page "Slate — Plan"); every point in it is tracked in `docs/PLAN_TRACKER.md`.

Slate is extracted and generalized from the pipeline behind the TrueSpace/Vixa films. That pipeline lives in
`~/Documents/TrueSpace/website-build-2026-09-28` (called FILMROOT below) and is still in use by
another session. **Read it; copy and generalize from it; never move, edit or delete anything in it.** The inventory of
it is in `FILMROOT/research/video-package-inventory-2026-09-30/` (visual-engine.md, sound-voice.md,
ae-qa-delivery.md, process-lessons.md, agents-wiring.md, packaging-research.md, media-research.md, coverage.md).

## 1. Hard rules (every module, every test)

1. **No brand in the core.** Nothing TrueSpace-, Vixa-, Trudy-, EOS- or Inner-Echo-specific in `~/code/slate`: no
   colours, names, copy, voice ids, logos, fonts, sample data or banned words. Brands are data packs (§7). Tests use the
   neutral `brands/demo` pack or fixtures.
2. **Company material never enters this repo.** TrueSpace and EOS brand packs live only in `~/code/slate-brands/`
   (local, never pushed). No code from the truespace-v2 company repo, ever.
3. **Secrets:** only through `slate.secrets` (env, then macOS Keychain via `/usr/bin/security`). Never print, log,
   write or return a key. Never prompt for one outside `slate connect`. Never put one in a URL.
4. **Browsers:** every child process runs with `BROWSER=none` (use `slate.procs.run`). Remotion Studio always runs with
   `--no-open`. Nothing opens a web browser. Web capture and any web step go through Aside (`~/.local/bin/aside`).
   puppeteer only ever loads our own local pages (the open web renderer), never a website.
5. **After Effects preferences** are edited only through `slate.ae.prefs` (the byte-safe tool: one key, CR line
   endings kept, AE closed, never `Pref_SCRIPTING_FILE_NETWORK_SECURITY`). Never read/write that file in text mode.
   Never quit AE with "saving no"; ask the person to save and quit.
6. **AE steps start only on a person's go** (supervised mode). Autonomous mode never touches AE.
7. **Spend is explicit:** every paid call goes through `slate.ledger` (estimate → budget check → in-flight row → result).
   Slate never buys credits, upgrades a plan or starts a licence. Replay mode (`SLATE_FAKE_VENDORS=1`) is the default
   for tests, evals and `slate demo`; `--live` spends.
8. **Gates, not vibes:** every check returns a `GateResult` (§6). Only PASS counts as passed. Unchecked is never passed.
9. **Never render on the WORK Mac.** Rendering is local.
10. **Perfect, don't replace:** code that revises approved work edits that artifact; it never swaps the approach.
11. **Voice:** never use whisper/soft/quiet/gentle v4 audio tags. Clones only of Quinn's own voice (professional clone
    he verified), never anyone else's.
12. **Portable text:** agent-facing text (skills, `--help`, errors) never names Claude-only tools.
13. **Quinn's voice** in docs, comments and messages: clear, specific, warm, no filler.

## 2. Repository layout (who owns what)

```
~/code/slate/
  README.md  AGENTS.md  INSTALL_FOR_AGENTS.md  CHANGELOG.md
  install.sh                     links skills, CLI, MCP; installs the launchd job agent; runs doctor
  pyproject.toml  uv.lock        Python package "slate" (>=3.11); [project.scripts] slate = "slate.cli:main"
                                 extras: ocr (ocrmac, Apple Vision; part of a normal install), analyze (demucs,
                                 librosa: recorded only, run through uvx), dev (pytest)
  registry.json                  model ids, versions, prices, limits, providers; each with verified_on
  src/slate/
    __init__.py  __main__.py  cli.py           dispatcher (foundation)
    paths.py config.py secrets.py procs.py jsonio.py gates.py log.py errors.py http.py replay.py clock.py
                                                shared (foundation)
    ledger.py                                   spend ledger + budgets (foundation interface; jobs builder hardens)
    jobs/        queue, launchd server, client, resume, ledger CLI                                [jobs]
    state/       film.json, film.lock.json, status, make, new, approve, film, revise, variant     [state]
    schemas/     *.schema.json + validate                                                         [state; voice_*: voice]
    story/       script.json -> storyboard.json + SCRIPT.md, gates                                [story-timing]
    timing/      shots, timeline, words (Whisper/alignment), beats, spoken forms, clock           [story-timing]
    voice/       cast, design, tags, tts, clean, pace, polish, analyze, align, captions, profile  [voice]
    sound/       score (Eleven Music), assembly, mix, verify, gap; effects/ gated on proof 3      [sound]
                 balance.py (the p3 balance gates and `slate sound balance`)                       [stability]
    gen/         Higgsfield Cloud API generations, plates, conform, matte, policy                 [gen-capture]
    capture/     ios (simctl), web (Aside), conform, beat map                                     [gen-capture]
    render/      remotion + web engine drivers, chunked renders, splice, registry, stills         [render]
    ae/          driver, prefs, masters, project build, smoke, bridge wrapper, ES3 kit glue       [ae]
    vendors/     elevenlabs.py (TTS, design, remix, isolator, alignment, music, subscription)    [voice]
                 higgsfield.py (Cloud API: estimate, submit, wait, cancel, upload, probe)        [gen-capture]
    engine/      Python side of the Node boundary: bridge, install, `engine|emit-events|check`    [engine-core]
    doctor/      doctor + connect                                                               [doctor]
    install/     install, uninstall (links, shims, MCP registration, the job agent)              [install]
    update.py    `slate update`                                                                 [packaging]
    notify.py    iMessage + push for held films and questions (never sent in tests)             [finish]
    qa/          film gates, glitch, boundary, ocr, banned, readtime; motion.py, access.py      [qa]
                 stability.py (render stability: pops, snaps, shakes, flicker)                    [stability]
    finish/      master (AE / ffmpeg), deliver (manifest, cuts, platform files), review, notes   [finish]
    formats.py   format templates: load, names, safe zones, captions, routes                    [formats]
    analyze/     reference-film analysis                                                          [formats]
    bench/       benchmarks (`slate bench higgsedit`, phase 8)                                    [formats]
    brand/       pack schema, validate, init, onboard (references + approvals)                    [brand]
    mcp/         stdio MCP server over the CLI and job queue; the desktop extension               [packaging]
    skills_build.py  guide.py                                                                    [packaging]
    knowledge.py `slate knowledge` (the lesson ledger)                                            [knowledge]
    evals/       eval runner and cases                                                           [knowledge]
    motion/      eases, fitting, shared vectors                                                  [engine-kit]
  engine/                        Node engine (package.json pins remotion 4.0.521 exactly; installed already)
    remotion/kit/                kit, looks, motif rig, eases, fonts, logo build, review sheets          [engine-kit]
    remotion/                    render_mb.mjs, stills.mjs, entry generation, bundling (not kit/)        [engine-core]
    web/                         open web renderer: seekable page runtime + puppeteer capture           [web-engine]
    ae/                          ES3 library + build/queue/validate/smoke scripts                       [ae]
    cli.mjs                      versioned JSON commands the Python CLI calls                            [engine-core]
    tests/                       vitest (each builder its own subfolder: tests/kit, tests/core, tests/web)
  skills/<name>/SKILL.md + references/     ten skills (spec-only frontmatter)                          [skills]
  plugins/slate/                 .claude-plugin/plugin.json, .mcp.json, plugin.json (Agent Plugins)      [packaging]
  .claude-plugin/marketplace.json                                                                       [packaging]
  formats/*.json                 format templates (_shared.json, _platforms.json: shared data)          [formats]
  brands/_template/ brands/demo/ brand packs (neutral only)                                              [brand]
  knowledge/lessons.json         every lesson/rule mapped to a destination                               [knowledge]
  tests/  engine/tests/  fixtures/  evals/                                                               [each module]
  docs/                          ARCHITECTURE (this), PLAN, PLAN_TRACKER, METHOD, DECISIONS
```

`src/slate/demo.py` and `src/slate/demo_film/` (its script and two scenes) are `slate demo` [integration]: a neutral
15-second 9:16 reel made end to end through every stage, each the real command in its own process.

A builder writes only inside its own module paths plus its tests. Shared modules (foundation) are read-only for
builders; if one needs a change, the builder says so in its report instead of editing it.

## 3. Shared foundation (already written; import, don't copy)

- `slate.paths`: `DATA` (`~/Library/Application Support/Slate`), `FILMS` (`~/Movies/Slate`, override `SLATE_FILMS`),
  `BRAND_DIRS` (repo `brands/`, `~/code/slate-brands`), `REPO`, `ENGINE_SRC`, `engine_home(version)`, `film_dir(id)`.
- `slate.config`: `load()` merges defaults, `DATA/slate.toml`, and env; `registry()` loads `registry.json`;
  `model(role)` returns the configured model id for a role (e.g. `tts`, `voice_design`, `music`, `plate_video`).
  `DEFAULTS` lists every setting a module reads, with its default: `[render]` (slots, motion_blur_samples,
  draft_samples, chunk_frames, foreground_max_subframes 2400, chunk_tries 2), `[jobs]` (disk_reserve_gb 5,
  disk_stop_gb 4, poll_seconds, kill_grace_s 10, max_load_per_core 3.0, nice 5, agent_label `dev.slate.jobs`,
  slots per resource), `[gen]` (video_resolution: unset means registry.json's 720p default), `[films]`, `[ae]`,
  `[models]`.
- `slate.secrets`: `get(name)` for `elevenlabs`, `elevenlabs_oneiro`, `higgsfield`; raises `MissingSecret` whose
  message says which `slate connect …` to run in Terminal. `present(name) -> bool` for doctor. Never logs values.
- `slate.procs`: `run(argv, **kw)` (BROWSER=none, no shell, text capture, timeout; each child leads its own process
  group, so a timeout, an interrupt or a stop signal stops everything it started), `which(tool)`, `agent_context()`
  (True when running under Claude Code, Codex or OpenClaw, from their env vars), `ae_running()`, `aerender_running()`.
- `slate.jsonio`: `read(path)`, `write_atomic(path, obj)` (temp + rename, sorted keys off, 1-space indent, trailing
  newline), `sha256(path)`, `validate(obj, schema_name)`.
- `slate.gates`: `GateResult` (§6), `inputs_for(film_dir, paths)`, `write_report(film_dir, results)`,
  `load_report(film_dir)` (STALE when an input changed), `summarize(results)`. Inputs may be named
  (`film:<dotted.key>`, `film:*`, `brand:<name>`, `format:<name>`, hashed through `slate.state.lock.hash_inputs`);
  listing `film.json` records `film:*` (film.json without its bookkeeping), so a pin, a version or a budget raise
  doesn't turn gates STALE.
- `slate.errors`: one exception per exit code (§4): `Refused` 1, `UsageError` 2, `MissingPrerequisite` 3,
  `SpendRefused` 4, `Busy` 5, `TryLater` 75 (with `retry_after_s`).
- `slate.http`: urllib with Slate's TLS context and User-Agent; reads retry, anything that can bill never does. A 429
  or 503 that ends a request raises `RateLimited`, an `HttpError` that is also `TryLater` (exit 75), carrying the
  vendor's Retry-After.
- `slate.ledger`: `estimate`, `check_budget`, `begin` (in-flight row, returns row id), `finish`, `rows`; file
  `DATA/ledger.jsonl` under a file lock; budgets in `DATA/budget.json` (monthly caps per mode) and `film.json`.
  Estimates quote a price's own `price_verified_on` when registry.json has one.
- `slate.replay`: replay mode (`SLATE_FAKE_VENDORS=1`): `respond`, `record`, `register_faker` (fakers are
  process-wide; tests change them with monkeypatch only).
- `slate.log`: `info/warn/error` to stderr; `emit(obj)` prints JSON to stdout for `--json`; `seal_stdout()` sends
  fd 1 to stderr once a `--json` command's object is out (native code that prints at exit can't follow it).
- `slate.cli`: argparse dispatcher. Every module exposes `register(sub)` adding its subcommands; `MODULES` lists each
  module with exactly the commands it adds (each registered once; `REGISTERED` records who added what and `BROKEN`
  why a module couldn't, and the tests hold it empty). `SUBROUTES` sends `slate qa stability …` to that module's own
  command, and `FAST_COMMANDS` lets `slate mcp` start without importing every module. Handlers return an int exit code
  and print human text, or JSON with `--json`.

## 4. CLI surface (the contract every agent relies on)

Global flags, before or after the command: `--json` (machine output: exactly one JSON object on stdout, usage errors
included as `{"error": "usage", "message", "exit_code": 2, "usage", "hint"}`; logs on stderr), `--film <id|path>`
(default: the film folder you're in), `--live` (spend; default is replay when `SLATE_FAKE_VENDORS=1`), `--dry-run`,
`--quiet`. Exit codes: 0 ok, 1 gate failed or refused, 2 usage, 3 missing prerequisite, 4 spend refused
(budget/approval), 5 busy (AE open, a lock or slot held), 75 try later (a vendor's rate limit or a service that's
briefly unavailable; a queued job goes back in the queue after a backoff, at least the vendor's Retry-After). A
command stopped with SIGTERM or SIGHUP stops what it started first, then exits 143 or 129.

Every command below is registered by exactly one module (`slate.cli.MODULES`); `slate --help` lists them all and
`slate <command> --help` the rest. Where a row names a command's stages (`slate voice cast|design|…`), those are all
of them.

| Command | Does | Owner |
|---|---|---|
| `slate doctor [--install] [--context NAME] [--only GROUP] [--strict]` | checks everything; `--install` installs free prerequisites, asking first | doctor |
| `slate connect claude\|higgsfield\|elevenlabs\|aftereffects` | the four connect steps; a sign-in that needs a browser is printed for the person to run | doctor |
| `slate guide [topic]` | prints skill reference files | packaging |
| `slate demo [--live] [--keep]` | a neutral 15-second 9:16 reel through every stage, one line per stage; replayed unless `--live` (asked first in a Terminal, refused under an agent unless the person set `SLATE_LIVE=1`) | integration |
| `slate new <id> --brand B --format F [--mode M] [--parent P --derivation JSON] [--by NAME --reason TEXT]` | scaffolds a film folder; its mode is recorded in Slate's data folder (DATA/film_modes.jsonl), outside the film | state |
| `slate status [--write]` | per stage: fresh/stale/missing/locked + next command | state |
| `slate make [target] [--list] [--spend]` | rebuilds only stale nodes | state |
| `slate approve <what> <path> --by NAME` | records a person's approval of exactly this file (sha256); `slate approve proof3 <file> --by NAME` needs no film and writes DATA/approvals.json; `slate approve grant --scope "<the owner's words>" --by NAME` records an autonomy grant with its scope (no file) | state |
| `slate film show\|set\|input\|icloud\|pin\|repin\|ask\|mark\|direction\|questions\|answer\|park` | film.json, supplied inputs (copied to brief/inputs/, verified and locked), a film in iCloud Drive kept out of sync, pins, the brief's asks and directions, questions for a person; `set mode` away from autonomous needs a person at their own Terminal (`--by NAME --reason TEXT`) | state |
| `slate variant <parent> <child> [--aspect A] [--cut S] [--language L] [--format F] [--by NAME --reason TEXT]` | a derived film that reuses its parent's work by reference (it starts in its parent's mode) | state |
| `slate revise start\|check\|done\|list` | perfect, don't replace: revise approved work by its notes | state |
| `slate script build\|lint` | script.json → storyboard.json + SCRIPT.md, refuses on failed gates | story-timing |
| `slate timing [words\|misses\|show\|pin]` | shots.json, vo_cues.json, vo_words.json, events.json, timeline.json; `pin` keeps a shot's delivered word times through a re-voice | story-timing |
| `slate voice profile\|account\|cast\|design\|tags\|tts\|clean\|pace\|polish\|analyze\|align\|captions\|check\|approve\|comp\|preview\|swap\|trim` | the voice stages, from casting to the studio polish, measured inflection and captions; re-voicing: a one-word pickup (`comp`), the re-voiced narration on the timeline (`preview`), an approved set swapped in (`swap`), a leaked tail trimmed off a take's play length (`trim`) | voice |
| `slate score plan\|compose\|assemble\|qa` / `slate mix [--listen [N]]` / `slate verify [gate ...]` | score, mix (and the listening file of the hardest-ridden accents), the sound gates (with slate.sound.balance's) | sound |
| `slate sfx [status\|spot\|render]` | effects engine; refuses until the proof-3 gate is cleared | sound |
| `slate sound instruments\|loudness\|balance` | sound tools: tonal instruments, BS.1770-4 loudness, the balance against the voice | sound |
| `slate gen estimate\|run\|takes\|judge\|pick\|cancel\|request-approval\|approve\|probe` | Higgsfield generations with estimate, ledger, takes | gen-capture |
| `slate plates plan\|conform\|matte\|track\|repair` | generated plates: the plan, the conform of an approved take, mattes, and the timing repair of a plate that hitches (staged with its report, then `--install`) | gen-capture |
| `slate capture ios\|web\|conform\|beatmap` | scripted captures, conformed to the film's clock and beat-mapped | gen-capture |
| `slate engine install\|status\|path\|skills\|scaffold\|golden` | the Node engine; `golden` checks the kit's golden stills, or with `--approved FILM` every approved act against this kit | engine |
| `slate emit-events [--act N]` | visual events and motion traces from each act's scenes | engine |
| `slate check <act> [--markup [--against REF\|FOLDER]] [--determinism]` | hand-in check: type check, word misses, banned words, never-claims, frame literals, timing margins, the character, events, contracts, each web shot of a Remotion act | engine |
| `slate render [list\|splice\|clean\|stills] [--act N] [--from F --to T] [--engine remotion\|web\|ae] [--sub N] [--resume] [--detach]` | renders | render |
| `slate qa [gate ...] [--quick] [--list] [--detach]` / `slate qa triage\|psnr\|unpark` | the film's gates (`slate qa motion`, `slate qa access` run those gates with their own flags) | qa |
| `slate qa stability [run\|baseline\|reference\|strip\|compare\|selftest\|controls\|conformance]` | render stability and its tools; `baseline --drop F[-F] --by NAME --reason TEXT` takes a fault's flags out of the baseline before its fix is re-rendered | qa (stability) |
| `slate ae prefs\|smoke\|build\|run\|masters\|audit\|acts\|bridge` | After Effects, started only on a person's go | ae |
| `slate master [--ae\|--ffmpeg] [--go]` | masters | finish/ae |
| `slate deliver [--draft] [--list]` | delivery set + platform files + manifest (last) | finish |
| `slate review [grade-brief\|grade] [--to DEST] [--audio MIX]` | review packet for the current version, and its grade; `--audio` reviews a proposed mix against the current picture | finish |
| `slate notes add\|list\|plan` | the owner's notes, mapped to shot, beat and stage; the fix plan | finish |
| `slate approved <file>` | exit 0 only when this exact file is approved for publishing | finish |
| `slate analyze <video> [--cuts --ocr --eases --transcript --stems --beats \| --all]` | reference analysis | formats |
| `slate formats list\|show\|check\|channels` | format templates and the channel library | formats |
| `slate bench higgsedit` | benchmarks that decide whether a second engine earns its place | formats |
| `slate brand list\|show\|init\|validate\|onboard\|approve\|status` | brand packs and onboarding | brand |
| `slate job submit\|status\|wait\|log\|cancel\|retry\|list` | one job in the queue | jobs |
| `slate jobs serve\|status\|locks\|disk\|prune\|agent` | the queue itself, its launchd agent, locks and disk | jobs |
| `slate budget show\|set\|film` / `slate ledger list\|inflight\|reconcile\|snapshot` | spend | jobs |
| `slate mcp [--list-tools] [--check]` / `slate mcpb build` | stdio MCP server; the desktop extension | packaging |
| `slate skills build\|list` | plugin copies, Codex sidecars, OpenClaw entries, claude.ai zips | packaging |
| `slate knowledge check\|stats\|show\|gaps` | every lesson mapped; `gaps --write` regenerates knowledge/GAPS.md from the ledger | knowledge |
| `slate evals list\|run\|report` | agent evals: triggering, routing, negatives, the dry run | knowledge |
| `slate update` | git pull + reinstall | packaging |
| `slate install [--check]` / `slate uninstall` | wire Slate into every agent here, or undo it | install |

## 5. The film folder (one film = one folder; film as data, one clock)

```
<film>/film.json            id, title, format, language ("en"), fps_num/fps_den, width, height, aspect, brand, voice,
                            targets {runtime_s, lufs, true_peak}, budget, mode (supervised|express|draft|autonomous),
                            generation ("none"|"rarely"|"where-it-helps"), parent, derivation, pins {…}, versions
<film>/film.lock.json       per derived file: producer, tool_version, inputs {path or named input: sha256},
                            locked (bool), made_at; a derived film's reused files are entries with a `ref`
<film>/brief/GOAL.md  brief/PRODUCTION.md
<film>/script/script.json   the ONLY hand-edited script file (acts, lines, on_screen, picture, sources, reading,
                            generate)
<film>/script/storyboard.json  script/SCRIPT.md          generated
<film>/voice/  timing/  scenes/  captures/  plates/  sound/  renders/  masters/  qa/  review/  deliver/  archive/
```

Timing files keep the films' proven shapes (read FILMROOT/build for real examples):

- `shots.json`: `fps_num, fps_den, width, height, total_frames, acts[], shots[{id, act, frame_in, frame_out, sec_in,
  sec_out, …}]`. It names the frame size, so every film makes its own (a derived film's frames match its parent's).
- `vo_cues.json`: `cues[{shot, at, file, gain, fade_in, fade_out, dur?}]`.
- `vo_words.json`: `shots{ID:{take, line, heard, words[{w, start, end}], tokens, take_seconds}}` plus `spoken`
  (`{token: [[words]]}`: how every number or symbol in the lines is said, so an engine's word lookup matches "664"
  against "six six four").
- `events.json`, `timeline.json`.
- `captions.json`: the caption cues (`cues[{shot, text, lines, start, end}]`), the source the caption gates read;
  `slate voice captions` also writes SRT and WebVTT to `voice/captions/`.
- `visual_events/actN.json`: `[{shot, id, t, kind, size, note, start?, dur?, from?, to?, x?, pan?, source?}]`. Kinds:
  tap, type, land, snap, count, draw, data, page, stamp, whoosh, swell, character, impact. **`t` is always the beat**,
  the film second the motion lands. `start` and `dur` (film seconds, seconds) go together and give a sustained
  motion's span: required for draw and count, which end on their beat (t = start + dur, within half a frame); a
  whoosh or a swell peaks on t, inside its span; any other kind may give a span with t inside it.
  `slate.timing.beats.problems(beats, fps)` checks this the one way every engine and reader shares. `source` is
  `capture:<name>` for a capture's beats. An act with no beats is `{"silent": true, "reason": …}`.
- `motion/actN.json`: the act's motion trace (§12, engine data contracts).

Generated files carry `"generated": "do not edit by hand: <command>"`.

## 6. Gate results

```json
{"gate": "loudness", "status": "PASS|FAIL|REVIEW|UNCHECKED|STALE", "summary": "one line",
 "evidence": ["path or measurement"], "engine": {"name": "ffmpeg ebur128", "version": "9.0.2"},
 "inputs": {"<path or named input>": "<sha256>"}, "checked_at": "ISO-8601", "details": {}}
```

`qa/gates.json` holds the latest result per gate. A result turns STALE when any input's hash changes. Named inputs
(`film:<dotted.key>`, `film:*`, `brand:<name>`, `format:<name>`) are hashed by value, so a pin or a new version in
film.json doesn't make a gate STALE; changing what the gate read does. Gates whose names don't say which stage they
check add `details.stage`.

## 7. Brand packs

`brands/<name>/brand.json` + assets. Keys: `name`, `palette` (tokens), `type` (families with `source`: installed name,
kit URL or Google Fonts; `licence`; `postscript`, the face's PostScript name After Effects sets fonts by; never font
files in git), `logos` (SVG paths + `build` spec), `motif` (optional), `look` (id, `engine`: remotion, web or ae, and
references), `motion` (named eases with lineage, duration scale, staggers, overshoot policy, signature motion,
`extra_curves` and `speed_limits` for `slate qa motion`), `voice` (the voice profile: provider, voice_id, model,
settings, lexicon), `sound` (key, tempo range, materials, banned timbres, motif, palette, reference levels), `truth`
(sources, claim_authority, approved_claims, banned_words, never_claim, sample_data), `grounding` (ElevenLabs/Higgsfield
brand-kit ids; `uploads_allowed`), `references` {truth, look_motion, voice, sound}: each
`{file, sha256, approved_by, approved_at}` or null. Autonomous mode refuses a brand unless all four references are
approved.

## 8. Engine boundary

The Python CLI never imports Node. It calls `node engine/cli.mjs <command> --json '<args>'` and reads one JSON object
from stdout. Commands: `version`, `browser-ensure` (Remotion's headless Chrome), `entry` (generate Root + per-act
entries from shots.json), `bundle`, `render` (film, act, from, to, sub, out), `stills`, `emit-events`, `trace` (film,
act: one act's motion trace from the kit's registry; `slate render` calls it after each whole act or patch),
`check` (one act, or several with one bundle), `golden` (the kit's golden stills, or a film's approved acts rendered
with this kit and compared with their approved renders), `web-render` (the open web renderer: render, `emit` with
`dry_run` to read beats without writing, or `scaffold` to start a web scene). Engine code aliases `@slate/kit` and
`@film/data`; one bundle per film per source hash, deleted after use. Renders are ProRes 422 HQ 10-bit BT.709 via the
16-bit sub-frame average (render_mb), lossless intermediates for chunks, splices proven by frame hashes and PSNR. A
derived film's reused files reach the engine through `.slate/refs.json` (written from `slate.state.lock.resolve`).

**Per-shot engines (P074).** The brand picks the engine for its scenes (`look.engine`); a shot may name its own `engine`
in script.json (read there until timing carries it), and by default it's drawn by its act's. Built: a web shot inside a
Remotion act. Its page lives in `scenes/shots/<SHOT>/web/`, its beats join the act's events, `slate check` hands it in
(`handin.act<N>.shot.<ID>.*` and `handin.act<N>.shots`), and `slate render` renders it over exactly its frames at the
act's samples and splices it in with proof before the act is registered (the registry entry lists `shot_engines`). A
seam between engines is always a cut, so a patch never crosses one. Refused by name until built: an AE shot in a code
act or a code shot in an AE act (After Effects builds and renders whole acts), a Remotion shot in a web act, and a web
shot's tweens in the act's motion trace. engine/README.md holds the rules; `slate.render.shot_engines` enforces them.

## 9. Jobs

Every long job (render, master, whisper, bank build, generation batch) goes through the queue: one JSON per job in
`DATA/jobs/`, states queued → running → done|failed|cancelled, detached (own session, `caffeinate -i`), resumable,
per-resource locks (render slots, `ae`, `elevenlabs_music`, `elevenlabs_tts`, `higgsfield`, `whisper`, disk
reservation). Supervised outranks autonomous. `slate … --detach` returns a job id at once; `slate job status <id>`
polls. A job's command sees the slots it holds in `SLATE_JOB_HOLDS`, so `slate.jobs.locks.hold` inside it uses the
job's own slot. A command that exits 5 (busy) or 75 (try later) goes back in the queue with a backoff (1, 2, 4… minutes,
at most 15, never shorter than the vendor's Retry-After that `slate.cli` notes on the job); paid calls never pay
twice on the way (`slate.jobs.resume`).

## 10. Testing

- pytest in `tests/`, vitest in `engine/tests/`. Tests must pass offline with `SLATE_FAKE_VENDORS=1`.
- Opt-in, never in the default run: `slow` (a real environment or install; `SLATE_TEST_SLOW=1`), `render` (a real
  render; `SLATE_TEST_RENDER=1`), `integration` (`slate demo` end to end, minutes: `SLATE_TEST_INTEGRATION=1` or
  `-m integration`), `live` (a vendor or a real model), Whisper (`-m whisper` or `SLATE_WHISPER_LIVE=1`) and Apple
  Vision OCR (`SLATE_TEST_VISION=1`; the default run never loads it, so OCR gates are UNCHECKED unless a test scripts a
  backend).
- No test changes another's world: process-wide registries (replay fakers, resume hooks and balance readers, revise
  hooks, gate lists) change only through monkeypatch, and module reloads restore the package attribute too.
- Golden data: generalized tools rerun on FILMROOT's A/B/C inputs must match the delivered JSON within stated
  tolerances (read FILMROOT, write outputs to a temp dir; never write into FILMROOT).
- Cross-language vectors in `tests/vectors/*.json` (eases, T90, spoken numbers) checked by pytest and vitest.
- Every bug fixed from the films (listed in the inventory) gets a regression test.

## 11. Style

Python: stdlib + numpy/scipy/Pillow unless the module says otherwise; type hints; small functions; docstrings that say
why. TypeScript: strict. Comments and docs in Quinn's voice. No TODO stubs presented as done: if something can't be
built yet (e.g. the effects engine before proof 3), it refuses with a clear message and a test proves the refusal.

## 12. Module interfaces (call these across modules)

Builders work in parallel, so these are the only cross-module calls. The owner provides exactly these names and
signatures (more is fine). A caller whose dependency isn't there yet writes against this table, uses a small fake in
its own tests, and says so in its report. Film paths are always the film folder (`Path`); results are plain dicts or
`GateResult`s.

**state** (`slate.state`)
- `film.create(film_id, *, brand, format, mode="supervised", title=None, parent=None, derivation=None, generation="rarely", films_dir=None) -> Path`
- `film.load(film_dir) -> dict` · `film.save(film_dir, data) -> None` (validated, atomic) · `film.new_version(film_dir, note=None) -> int`
- `lock.record(film_dir, rel_path, *, producer, inputs, tool_version=None) -> dict` (a film.lock.json entry; inputs are
  paths or named inputs: `film:<dotted.key>`, `film:*` (film.json without pins, the pin log, versions, budgets, model
  overrides and stamps), `brand:<name>`, `format:<name>`) · `lock.hash_inputs(film_dir, inputs) -> dict`
- `lock.is_stale(film_dir, rel_path) -> bool` · `lock.lock_asset(film_dir, rel_path, *, reason) -> None` · `lock.is_locked(film_dir, rel_path) -> bool`
- `lock.resolve(film_dir, rel_path) -> Path` (a derived film's reused file: its own first, then its parent's)
- `lock.freeze_shot_ids(film_dir, ids, *, source=...) -> dict` · `lock.scene_references(film_dir, ids) -> dict[str, list[str]]`
  · `lock.check_renumber(film_dir, {old: new | None}) -> dict` (the plan's rule: a renumber is refused once a scene uses
  the ids)
- `pins.pin(film_dir, key, value, *, repin=False) -> value` (first use wins) · `pins.get(film_dir, key) -> value | None`
- `approvals.record(film_dir, *, what, path, approved_by, note=None) -> dict` (records sha256; a final_cut of a draft
  delivery is refused) · `approvals.is_approved(film_dir, what, path) -> bool` (sha must match)
  · `approvals.record_global(*, what, path, approved_by, note=None) -> dict` (Slate's own approvals, such as proof 3:
  no film, DATA/approvals.json; `slate approve proof3 <file> --by NAME`)
- `production.add_direction(film_dir, text, *, source) -> int` (numbered, verbatim) · `status.compute(film_dir) -> dict`
- `modes.check(film_dir, action, **ctx) -> dict` (refuses what the film's mode forbids: `deliver_final`, `generate`
  with the person's `approval_id`, `master`, `approve`, …)
- `revise.register_hook(name, fn, *, applies=None, owner=None)` (`fn(film_dir, approved, current, revision)` returns a
  GateResult; sound registers level_vs_voice, qa palette and sync)
- `derive.create(parent_dir, child_id, *, format=None, aspect=None, language=None, cut=None) -> Path` (derived film)
- `autonomy.film_mode(film_dir, fj=None) -> str` (the mode a film is judged in: Slate's own record in
  DATA/film_modes.jsonl, written at `slate new` and `slate variant`, wins over film.json, so a film made autonomous
  stays autonomous until a person at their own Terminal changes it) · `autonomy.is_locked(film_dir, fj=None) -> bool`
  · `autonomy.ae_finish_check(film_dir) -> dict` (the owner's own AE finish of a held master, after a final-cut
  approval). Every module that routes by mode (spend, keys, After Effects, the queue, review, notes, generation, QA's
  claims) asks this, or `slate.ledger.film_mode(film, mode=None)`, never film.json's `mode` directly.

**story / timing** (`slate.story`, `slate.timing`)
- `story.build(film_dir) -> list[GateResult]` · `story.gates.check_script(script, *, brand, fmt) -> list[GateResult]`
  (a shot's `reading` declaration, voice_matched or not_for_reading with its reason, is honoured and carried into
  storyboard.json)
- `timing.build(film_dir) -> list[Path]` (shots, vo_cues, vo_words, timeline) · `timing.load_shots(film_dir) -> dict`
  (follows a derived film's references)
- `timing.words.word_time(film_dir, shot_id, word, occ=1, which="start") -> tuple[float, bool]` (never raises; bool = exact)
- `timing.spoken.forms(token, lang="en") -> list[list[str]]` (how a number/symbol is said; language tables, no literals
  in logic) · `timing.spoken.forms_in(texts, lang="en") -> dict[str, list[list[str]]]` (vo_words.json's `spoken`)
- `timing.beats.problems(beats, fps=None) -> list[str]` (§5: t is the beat; spans for draw and count)
  · `timing.beats.span(beat) -> (start, end) | None` · `timing.beats.beat_span(beat, *, default_dur=None)`
- `timing.align.whisper_exe()` honours `SLATE_WHISPER` like the voice module, so both Whisper runners use one CLI
- `timing.pins.pinned_time(film_dir, shot, word, occ=1, which="start") -> float | None` (a delivered word time kept
  through a re-voice; `word_time` prefers it)

**brand** (`slate.brand.pack`, `slate.brand.onboard`)
- `find(name) -> Path` · `load(name_or_path) -> dict` · `references_approved(pack) -> bool` · `missing_references(pack) -> list[str]`
- `banned_words(pack) -> list[str] | None` (None when the pack states no list: gates are UNCHECKED, never a clean pass)
  · `approved_claims(pack) -> list[dict]` · `motion(pack) -> dict` · `palette(pack) -> dict` · `uploads_allowed(pack) -> bool`
- `contrast_pairs(pack) -> list[dict]` ({fg, bg, role: body|large})
- `onboard.billing(name) -> dict`: what onboarding spend passes to the ledger as `film=`, so voice design and look
  tests bill against `ledger.format_budget('onboarding')` per brand

**formats** (`slate.formats`)
- `load(name, *, aspect=None) -> dict` (a name, an alias or a path to a format file, always validated; resolved for one
  aspect) · `names() -> list[str]` · `canonical(name) -> str`
- `safe_zones(fmt, platform, aspect=None) -> dict` · `zones_for(fmt, aspect=None) -> dict` · `captions_for(fmt, aspect=None) -> dict`
- `route_for(channel) -> dict` (hero: the full pipeline; social: may take the fast route) · `loudness_for(fmt, *, broadcast=None)`
  · `onboarding_budget() -> dict`. A delivery's platform files go to `deliver/vN/platform/`.

**vendors** (`slate.vendors.elevenlabs.Client()`, `slate.vendors.higgsfield.Client()`) — replay-aware; every paid call goes
through `slate.ledger`. ElevenLabs: `subscription()`, `tts(...)`, `design(...)`, `remix(...)`, `isolate(...)`,
`align(...)`, `compose_music(...)`; `music_plan_schema(model_id)` reads registry.json (`chunks` for music_v2 and
v2_5, `sections` for music_v1). Higgsfield: `probe()`, `estimate(path, body)`, `submit(path, body)`, `wait(request_id)`,
`cancel(request_id)`, `upload(path)`; `/estimate`'s `usd` is the price and `discount.usd` the saving; every submit
carries an `Idempotency-Key` derived from its ledger row and is resent with the same key and body after a lost
answer (a keyless submit is never retried); an ambiguous keyed submit is `HiggsfieldTryLater` (exit 75), a rate limit
`HiggsfieldBusy` (exit 5). `sync_agents` does not exist and never will.
Inside the OpenClaw gateway every vendor call goes through `slate.vendors.egress.route(provider, *, host=None,
op=None, queue=True)` first: the command queues itself for the launchd runner (or refuses with the exact `slate job
submit` line), and a proxy's tunnel refusal is `EgressBlocked`, a MissingPrerequisite naming the allowlist and the
queue route. ElevenLabs' `tts(...)` also takes `previous_request_ids` and `next_request_ids` (up to three a side).

**voice** (`slate.voice`): `tags.strip(text) -> str` · `tags.check(tagged, line) -> list[str]` (problems; banned tags refused)
· `profile.load(brand_pack) -> dict` · `captions.build(film_dir) -> list[Path]`
· `polish.run(film_dir, *, only=None, strength=None, ...) -> dict` (the studio chain on every take: length and loudness
kept, verified by measurement, pinned per film; `<take>.polished.wav` and its `<take>.polish.json` report)
· `polish.trial(film_dir, strengths, *, only=None, sheet=False)` · `polish.apply(x, sr, chain) -> (y, info)`
· `polish.tuning_for(profile) -> dict` · `analyze.measure_take(path, *, text=None, words=None, ...) -> dict`
· `analyze.run(targets, *, film_dir=None, ab_ref=None, sheet=False, stage=None, ...) -> dict` (measured inflection, A/B
spectrogram sheets) · `align.transcribe(path, *, model=None, language="en") -> dict` (the one Whisper runner; honours
`SLATE_WHISPER`). A selected take's audio is its last stage: polished, paced, clean, then raw; timing's take resolver
keeps the same order, so the cue sheets and the mix play the polished take. Gates: `voice_polish`, `voice_inflection`
(with policy, direction, takes, words, tails and fit).

**sound** (`slate.sound`): `measure.loudness(path) -> dict` (integrated LUFS, true peak, LRA) · `mix.run(film_dir) -> Path`
(`sound/mix/final_mix.wav` with its four stems; `final_mix.json` names in `empty_stems` the stems a film has nothing
for, written silent on purpose)
· `verify.run(film_dir, names=None, *, write=True) -> list[GateResult]` · `verify.GATE_NAMES` (the core gates)
· `verify.gate_names()` (with every gate merged in from `slate.sound.balance`, which deliver requires)
· `effects.require_proof3()` (raises Refused until proof 3 is approved)
- `slate.sound.balance` [stability]: `GATES` (`sound.balance.*`, `sound.planned_stops`, each taking verify's context
  or a film folder), `run(film_dir, names=None)`, `register(sub)` (`slate sound balance`)

**gen / capture**: `gen.policy.allowed(film_json, shot) -> tuple[bool, str]` · `capture.ios.capture(film_dir, spec) -> Path`
· `capture.web.capture(film_dir, spec) -> Path` · `capture.conform.to_sequence(src, film_dir, name) -> Path`
· `gen.bodies.default_resolution(role, width, height) -> str` (video plates: registry.json's 720p unless
`[gen] video_resolution` says otherwise; stills: the short side)

**engine** (`slate.engine`): `bridge.call(command, args, *, timeout=None) -> dict` (runs `node engine/cli.mjs <command>
--json '<args>'`) · `bridge.engine_dir() -> Path` · `install.ensure(version=None) -> Path`

**render** (`slate.render`): `run(film_dir, *, act=None, frames=None, engine="remotion", samples=None, out=None) -> dict`
· `registry.entries(film_dir) -> list[dict]` · `registry.register(film_dir, entry) -> None`
· `splice.splice(film_dir, base, patch, frame_in, frame_out) -> dict` (proof: frame hashes + PSNR). A render returns
its gates (`render.actN.frames`, `render.splice`, the engine's fonts and `web.network`).

**qa** (`slate.qa`): `run(film_dir, names=None) -> list[GateResult]` (writes qa/gates.json) · `GATES: dict[str, callable]`
· `EXTERNAL` · `load_external(refresh=False) -> dict` · `measure.psnr(a, b) -> float` · `ocr.available() -> tuple[bool, dict]`
· `ocr.stdout_to_stderr()` (every OCR call: Vision's native prints never reach a `--json` stdout). qa-motion adds its
gates to `GATES` from `slate.qa.motion` and `slate.qa.access`, and the stability agent from `slate.qa.stability`
(`GATES = {"stability": gate}`, `register(sub)` for `slate qa stability <action>`), each import-guarded.

**ae** (`slate.ae`): `prefs` (byte-safe: `check()`, `get()`, `set()`, `restore()`) · `driver.run_jsx(path, *, args=None) -> dict`
· `driver.ae_lock()` (context manager, shared with the bridge's lock) · `smoke.run(out_dir) -> GateResult`
· `masters.render(film_dir, **kw) -> dict` · `project.build(film_dir) -> Path` · `bridge.status() -> dict`

**finish** (`slate.finish`): `master.run(film_dir, *, method="auto", go=False, ...) -> dict` · `deliver.run(film_dir, *,
final=True) -> Path` (manifest, written last; requires every qa gate, the sound gates when the master carries sound,
and the master gates) · `review.packet(film_dir, *, rebuild=False) -> Path`
· `slate.notify.imessage(text, attachment=None)` / `slate.notify.push(title, body)`

**jobs** (`slate.jobs`): `client.submit(kind, argv, *, film=None, resources=(), priority="supervised", disk_gb=0.0, cwd=None) -> str`
· `client.status(job_id) -> dict` · `client.wait(job_id, timeout=None) -> dict` · `client.cancel(job_id) -> None`
· `client.detach_current(kind, resources=(), *, film=None, disk_gb=0.0) -> str` (the `--detach` convention)
· `client.note_try_later(job_id, *, retry_after_s=None, reason=None)` (slate.cli calls it as a queued command exits 75)
· `locks.hold(resource, *, timeout=None, owner=None)` (context manager; resources: `render`, `ae`, `elevenlabs_tts`,
`elevenlabs_music`, `higgsfield`, `whisper`) · `disk.reserve(path, gb)` (context manager)
· `resume.paid_call(est, *, key, submit, wait=None, ...)` (never pays twice) · `resume.register(provider, poll)`
· `resume.register_balance(provider, read)`

**doctor** (`slate.doctor`): `checks(context=None) -> list[dict]` (each `{id, status: green|yellow|red, summary, fix}`)

**Engine data contracts** (files, so three engines share them): the kit and the web engine write a motion trace per
act, `timing/motion/actN.json` = `[{id, shot, prop, ease, frame_in, frame_out, from, to, samples: [[frame, value]…],
entrance: bool, loop_period: int|null, rig: str|null}]`, which `slate qa motion` checks. Optional keys the motion gate
reads: `class` (rig | ui | carry | whip: which speed limits apply; a rig part is rig, a camera move or a page or card
carried across the frame is carry), `exempt` (reading_sweep | steady) with its `reason`, `exception`, `size` and
`unit`. Besides the brand's named eases, a trace's `ease` may be one of the kit's own (`linear`, `sine`, `spring0`, or
`even`: rest to rest through a third of the travel at each third of the time, for long carries and camera drifts) or
`measured` (an act sampled in After Effects). `src/slate/schemas/motion_trace.schema.json` holds the shape.
Renders register in `renders/registry.json` = `{entries: [{act, engine, file, frames: [in, out], samples, sha256,
made_at, bundle_hash}]}`.
