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
  registry.json                  model ids, versions, prices, limits; each with verified_on
  src/slate/
    __init__.py  __main__.py  cli.py           dispatcher (foundation)
    paths.py config.py secrets.py procs.py jsonio.py gates.py log.py   shared (foundation)
    ledger.py                                   spend ledger + budgets (foundation interface; jobs builder hardens)
    jobs/        queue, launchd server, client                          [jobs]
    state/       film.json, film.lock.json, status, make, new, derived  [state]
    schemas/     *.schema.json + validate                               [state]
    story/       script.json -> storyboard.json + SCRIPT.md, gates      [story-timing]
    timing/      shots, timeline, words (Whisper/alignment), clock      [story-timing]
    voice/       ElevenLabs client, cast, design, tts, clean, pace, align, captions, profile   [voice]
    sound/       score (Eleven Music), assembly, mix, verify, gap; effects/ gated on proof 3   [sound]
    gen/         Higgsfield Cloud API client, plates, conform, matte, policy                   [gen-capture]
    capture/     ios (simctl), web (Aside), conform, beat map                                  [gen-capture]
    render/      remotion + web engine drivers, chunked renders, splice, registry, stills      [render]
    ae/          driver, prefs, masters, project build, smoke, bridge wrapper, ES3 kit glue    [ae]
    qa/          film gates, glitch, boundary, motion, ocr, banned, readtime, access           [qa]
    finish/      master (AE / ffmpeg), deliver (manifest, cuts, derived), review packets       [finish]
    analyze/     reference-film analysis                                                       [finish]
    brand/       pack schema, validate, init, onboard (references + approvals)                 [brand]
    mcp/         stdio MCP server over the CLI and job queue                                   [packaging]
    skills_build.py  guide.py  demo.py                                                        [packaging]/[integration]
  engine/                        Node engine (package.json pins remotion 4.0.521 exactly)
    remotion/                    kit, looks, motif rig, render_mb.mjs, stills.mjs, entry generation      [engine]
    web/                         open web renderer: seekable page runtime + puppeteer capture           [web-engine]
    ae/                          ES3 library + build/queue/validate/smoke scripts                       [ae]
    cli.mjs                      versioned JSON commands the Python CLI calls                            [engine]
  skills/<name>/SKILL.md + references/     ten skills (spec-only frontmatter)                          [skills]
  plugins/slate/                 .claude-plugin/plugin.json, .mcp.json, plugin.json (Agent Plugins)      [packaging]
  .claude-plugin/marketplace.json                                                                       [packaging]
  formats/*.json                 format templates                                                        [finish]
  brands/_template/ brands/demo/ brand packs (neutral only)                                              [brand]
  knowledge/lessons.json         every lesson/rule mapped to a destination                               [knowledge]
  tests/  engine/tests/  fixtures/  evals/                                                               [each module]
  docs/                          ARCHITECTURE (this), PLAN, PLAN_TRACKER, METHOD, DECISIONS
```

A builder writes only inside its own module paths plus its tests. Shared modules (foundation) are read-only for
builders; if one needs a change, the builder says so in its report instead of editing it.

## 3. Shared foundation (already written; import, don't copy)

- `slate.paths`: `DATA` (`~/Library/Application Support/Slate`), `FILMS` (`~/Movies/Slate`, override `SLATE_FILMS`),
  `BRAND_DIRS` (repo `brands/`, `~/code/slate-brands`), `REPO`, `ENGINE_SRC`, `engine_home(version)`, `film_dir(id)`.
- `slate.config`: `load()` merges defaults, `DATA/slate.toml`, and env; `registry()` loads `registry.json`;
  `model(role)` returns the configured model id for a role (e.g. `tts`, `voice_design`, `music`, `plate_video`).
- `slate.secrets`: `get(name)` for `elevenlabs`, `elevenlabs_oneiro`, `higgsfield`; raises `MissingSecret` whose
  message says which `slate connect …` to run in Terminal. `present(name) -> bool` for doctor. Never logs values.
- `slate.procs`: `run(argv, **kw)` (BROWSER=none, no shell, text capture, timeout), `which(tool)`, `agent_context()`
  (True when running under Claude Code, Codex or OpenClaw, from their env vars), `ae_running()`, `aerender_running()`.
- `slate.jsonio`: `read(path)`, `write_atomic(path, obj)` (temp + rename, sorted keys off, 1-space indent, trailing
  newline), `sha256(path)`, `validate(obj, schema_name)`.
- `slate.gates`: `GateResult` (§6), `write_report(film_dir, results)`, `summarize(results)`.
- `slate.ledger`: `estimate`, `check_budget`, `begin` (in-flight row, returns row id), `finish`, `rows`; file
  `DATA/ledger.jsonl` under a file lock; budgets in `DATA/budget.json` (monthly caps per mode) and `film.json`.
- `slate.log`: `info/warn/error` to stderr; `emit(obj)` prints JSON to stdout for `--json`.
- `slate.cli`: argparse dispatcher. Every module exposes `register(sub)` adding its subcommands; `cli.py` lists them.
  Handlers return an int exit code and print human text, or JSON with `--json`.

## 4. CLI surface (the contract every agent relies on)

Global flags: `--json` (machine output on stdout, logs on stderr), `--film <id|path>` (default: the film folder you're
in), `--live` (spend; default is replay when `SLATE_FAKE_VENDORS=1`), `--dry-run`. Exit codes: 0 ok, 1 gate failed or
refused, 2 usage, 3 missing prerequisite, 4 spend refused (budget/approval), 5 busy (AE open, job running).

| Command | Does | Owner |
|---|---|---|
| `slate doctor [--install] [--context NAME]` | checks everything; `--install` installs free prerequisites, asking first | foundation/doctor |
| `slate connect claude\|higgsfield\|elevenlabs\|aftereffects` | the four connect steps; OAuth prints the command for the person to run | doctor |
| `slate guide [topic]` | prints skill reference files | packaging |
| `slate new <id> --brand B --format F [--parent P --derivation JSON]` | scaffolds a film folder | state |
| `slate status [--json]` | per stage: fresh/stale/missing/locked + next command | state |
| `slate make <target>` | rebuilds only stale nodes | state |
| `slate script build` | script.json → storyboard.json + SCRIPT.md, refuses on failed gates | story-timing |
| `slate voice cast\|design\|tts\|clean\|pace\|align\|captions` | the voice stages | voice |
| `slate timing` | shots.json, vo_cues.json, vo_words.json, timeline.json | story-timing |
| `slate emit-events [--act N]` | visual events from each act's beats | engine |
| `slate check <act>` | hand-in check: type check, word misses, banned words, events | engine |
| `slate capture ios\|web ...` | scripted captures, conformed | gen-capture |
| `slate gen ...` / `slate plates ...` | Higgsfield generations with estimate, ledger, takes | gen-capture |
| `slate score ...` / `slate mix` / `slate verify` | score, mix, verification | sound |
| `slate sfx ...` | effects engine; refuses until the proof-3 gate is cleared | sound |
| `slate render [--act N] [--from F --to T] [--engine remotion\|web\|ae] [--sub N] [--detach]` | renders | render |
| `slate qa [gate ...]` / `slate qa motion` | gates | qa |
| `slate master [--ae\|--ffmpeg]` | masters | finish/ae |
| `slate deliver` | delivery set + manifest (last) | finish |
| `slate review` | review packet for the current version | finish |
| `slate analyze <video>` | reference analysis | finish |
| `slate brand init\|validate\|onboard\|approve` | brand packs | brand |
| `slate job submit\|status\|log\|cancel` / `slate jobs serve` | the queue | jobs |
| `slate budget` / `slate ledger` | spend | jobs |
| `slate mcp` | stdio MCP server | packaging |
| `slate skills build` | claude.ai zip, Codex sidecars, OpenClaw entries | packaging |
| `slate demo [--live]` | neutral 15-second film end to end | integration |
| `slate knowledge check` | every lesson mapped | knowledge |
| `slate update` | git pull + reinstall | packaging |

## 5. The film folder (one film = one folder; film as data, one clock)

```
<film>/film.json            id, title, format, language ("en"), fps_num/fps_den, width, height, brand, voice,
                            targets {runtime_s, lufs, true_peak}, budget, mode (supervised|express|draft|autonomous),
                            generation ("none"|"rarely"|"where-it-helps"), parent, derivation, pins {…}
<film>/film.lock.json       per derived file: producer, tool_version, inputs {path: sha256}, locked (bool), made_at
<film>/brief/GOAL.md  brief/PRODUCTION.md
<film>/script/script.json   the ONLY hand-edited script file (acts, lines, on_screen, picture, sources)
<film>/script/storyboard.json  script/SCRIPT.md          generated
<film>/voice/  timing/  scenes/  captures/  sound/  renders/  masters/  qa/  review/  deliver/  archive/
```

Timing files keep the films' proven shapes (read FILMROOT/build for real examples): `shots.json`
(`fps_num, fps_den, width, height, total_frames, acts[], shots[{id, act, frame_in, frame_out, sec_in, sec_out, …}]`),
`vo_cues.json` (`cues[{shot, at, file, gain, fade_in, fade_out}]`), `vo_words.json`
(`shots{ID:{take, line, words[{w, start, end}]}}`), `events.json`, `timeline.json`, and
`timing/visual_events/actN.json` (`[{shot, id, t, kind, size, note, …}]`, kinds: tap, type, land, snap, count, draw,
data, page, stamp, whoosh, swell, character, impact; an act with no beats has `{"silent": true, "reason": …}`).
Generated files carry `"generated": "do not edit by hand: <command>"`.

## 6. Gate results

```json
{"gate": "loudness", "status": "PASS|FAIL|REVIEW|UNCHECKED|STALE", "summary": "one line",
 "evidence": ["path or measurement"], "engine": {"name": "ffmpeg ebur128", "version": "9.0.2"},
 "inputs": {"<path>": "<sha256>"}, "checked_at": "ISO-8601", "details": {}}
```

`qa/gates.json` holds the latest result per gate. A result turns STALE when any input hash changes.

## 7. Brand packs

`brands/<name>/brand.json` + assets. Keys: `name`, `palette` (tokens), `type` (families with `source`: installed name,
kit URL or Google Fonts; `licence`; never font files in git), `logos` (SVG paths + `build` spec), `motif` (optional),
`look` (id + references), `motion` (named eases with lineage, duration scale, staggers, overshoot policy, signature
motion), `voice` (provider, voice_id, model, settings, lexicon), `sound` (key, tempo range, materials, banned timbres,
motif, palette), `truth` (sources, claim_authority, approved_claims, banned_words, never_claim, sample_data),
`grounding` (ElevenLabs/Higgsfield brand-kit ids), `references` {truth, look_motion, voice, sound}: each
`{file, sha256, approved_by, approved_at}` or null. Autonomous mode refuses a brand unless all four references are
approved.

## 8. Engine boundary

The Python CLI never imports Node. It calls `node engine/cli.mjs <command> --json '<args>'` and reads one JSON object
from stdout. Commands: `entry` (generate Root + per-act entries from shots.json), `bundle`, `render` (film, act, from,
to, sub, out), `stills`, `emit-events`, `check`, `web-render` (open web renderer). Engine code aliases `@slate/kit` and
`@film/data`; one bundle per film per source hash, deleted after use. Renders are ProRes 422 HQ 10-bit BT.709 via the
16-bit sub-frame average (render_mb), lossless intermediates for chunks, splices proven by frame hashes and PSNR.

## 9. Jobs

Every long job (render, master, whisper, bank build, generation batch) goes through the queue: one JSON per job in
`DATA/jobs/`, states queued → running → done|failed|cancelled, detached (own session, `caffeinate -i`), resumable,
per-resource locks (render slots, `ae`, `elevenlabs_music`, `elevenlabs_tts`, `higgsfield`, disk reservation).
Supervised outranks autonomous. `slate … --detach` returns a job id at once; `slate job status <id>` polls.

## 10. Testing

- pytest in `tests/`, vitest in `engine/tests/`. Tests must pass offline with `SLATE_FAKE_VENDORS=1`.
- Golden data: generalized tools rerun on FILMROOT's A/B/C inputs must match the delivered JSON within stated
  tolerances (read FILMROOT, write outputs to a temp dir; never write into FILMROOT).
- Cross-language vectors in `tests/vectors/*.json` (eases, T90, spoken numbers) checked by pytest and vitest.
- Every bug fixed from the films (listed in the inventory) gets a regression test.

## 11. Style

Python: stdlib + numpy/scipy/Pillow unless the module says otherwise; type hints; small functions; docstrings that say
why. TypeScript: strict. Comments and docs in Quinn's voice. No TODO stubs presented as done: if something can't be
built yet (e.g. the effects engine before proof 3), it refuses with a clear message and a test proves the refusal.
