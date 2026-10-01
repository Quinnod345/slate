# Slate dependency ledger

Every library, command-line tool, MCP server, skill and service Slate uses, plus the ones the films used that Slate
decided against, with the decision, the pinned version, why, and how Slate holds the line. This is PLAN.md Appendix A
kept as a maintained document: when a dependency changes, this page changes in the same commit.

Checked on 2026-09-30 against `engine/package.json` and `engine/node_modules`, `pyproject.toml` and `.venv`,
`registry.json`, and read-only version commands on this Mac (listed at the end). Where this Mac or the repo differs
from the plan, the difference comes first, because those are the lines someone has to act on.

The component map that goes with this page is `knowledge/components.json`: all 306 inventory components and where
each one lives in Slate. Ids like `ve-011` below point into it.

## Reading the tables

- **Decisions.** *Keep* (part of Slate), *depend* (installed and pinned, never copied), *replace*, *drop*, *learn from*
  (read it, copy nothing). Two more appear where the plan uses them: *optional* (Slate works without it) and *not part
  of Slate* (it's on this Mac for other work, and Slate never calls it).
- **Pinned** is what Slate requires. The installed version on 2026-09-30 follows in brackets when it differs or is
  worth stating.
- **How Slate enforces it** names the mechanism. *In place* means it exists in the repo today (the foundation, the
  pinned engine manifest, `registry.json`). *Owed* names the module that has to build it (ARCHITECTURE section 2).
  Until it lands, an owed rule is a plan, not a guarantee.
- **†** marks rows that PLAN.md Appendix A doesn't list. They came from the repo and this Mac, so the decision on them
  is this ledger's. Flag any you disagree with.

## Where this Mac and the repo differ from the plan

1. **Older builds in `/usr/local/bin` shadow the current ones under macOS's default PATH.** It holds Intel
   Homebrew's ffmpeg 7.1.1 (x86_64 only) and a universal Node 21.6.2. macOS's `/etc/paths` lists `/usr/local/bin`
   first, and `procs.child_env` only appends
   `/opt/homebrew/bin` when it's missing, so a context whose PATH comes from `/etc/paths` rather than Homebrew's shell
   setup resolves the old builds. Checked: with `PATH=/usr/local/bin:/usr/bin:/bin`, `procs.which` returns
   `/usr/local/bin/ffmpeg` and `/usr/local/bin/node`. Node 21 fails the engine's `node >= 24` and the AE bridge's.
   Doctor has to check the version and architecture of whatever `procs.which` resolves, not just that it exists, and
   the foundation should put `/opt/homebrew/bin` ahead of `/usr/local/bin`. Owners: foundation (procs), doctor.
2. **Rubber Band is still the Intel 4.0.0 under Rosetta** (`/usr/local/bin/rubberband`); arm64 Homebrew has none.
   The plan says replace it with the arm64 build. Owner: doctor (`slate doctor --install`, asking first).
3. **Remotion's Chrome headless shell isn't installed for Slate's engine yet**: there's no
   `engine/node_modules/.remotion`. The film project's engine has 149.0.7790.0. Owner: doctor (`remotion browser
   ensure`), engine-core.
4. **puppeteer 25.10.0 expects Chrome for Testing 152.0.7977.75**, and `~/.cache/puppeteer` holds only 131, 137 and
   142. The open web renderer needs that download (for local pages only) or an `executablePath` pointed at Remotion's
   headless shell. Owner: web.
5. **`uv.lock` exists since 2026-10-01**, resolved for Apple-silicon Macs only (`[tool.uv] environments`), and it pins
   what the venv runs: numpy 2.5.3, scipy 1.18.1 and Pillow 12.3.0 on CPython 3.13. The plan's "today" versions are
   the film project's (numpy 2.4.4, scipy 1.18.0, Pillow 12.2.0 on CPython 3.14.7); golden-data parity is proven on the
   locked ones. `mcp` is pinned to its major, `>=2.2,<3`. The `analyze` extra (demucs 4.1.0, librosa 0.11.0) is
   recorded there but never installed into Slate's environment: both run through uvx.
6. **ocrmac is in Slate's venv** (the `ocr` extra, installed 2026-10-01: ocrmac 1.0.1 with pyobjc 12.2.2), so the OCR
   gates run on Apple Vision. Vision prints diagnostics through C stdio; Slate sends them to stderr, so `--json` stays
   one object. The default test run never loads Vision (`SLATE_TEST_VISION=1` opts in).
7. **There's no `analyze` extra for Demucs and librosa** in `pyproject.toml` yet. Owner: finish (analyze).
8. **`@remotion/media-utils` 4.0.521 is declared in `engine/package.json` but isn't in the plan.** It stays only if
   something imports it; otherwise it goes, like the other declared-but-unused packages. Owner: engine-core.
9. **remotion-dev/skills and elevenlabs/skills aren't installed in the engine folder yet.** Owner: engine-core.
10. **The motion-blur experiment needs a newer Remotion than the pin.** `<HtmlInCanvasMotionBlur>` arrived in 4.0.529;
    Slate is pinned at 4.0.521, and npm's latest is 4.0.530. The test runs in a second engine install
    (`paths.engine_home(version)`), never by bumping the pinned engine, and any upgrade within 4.0.x still waits for
    the render-parity check. Owner: engine-core.
11. **The music model in `registry.json` hasn't been proven with composition plans.** The music role defaults to
    `music_v2_5`, but V4_TAGS.md records that the films' scores used `music_v1` because v2.5 rejected section plans.
    Since 2026-10-01 registry.json records each music model's plan shape (`chunks` for music_v2_5, `sections` for
    music_v1) and the sound module builds the shape the model takes; the tracer reel's score is the first live proof
    on the best model. Owner: sound.
12. **Higgsfield's video analysis only ever ran through the hosted connector** (`video_analysis_create`, 0 credits).
    `slate analyze` runs in the CLI lane, so the analyze module has to find a CLI or Cloud API route to it, or report
    that step UNCHECKED. Owner: finish (analyze).
13. **The AE bridge's licence is clearer than the plan says.** `fnf-after-effects-mcp`'s runtime is MIT, from
    kumo.productions' mcp-aftereffects 0.2.0 (its `UPSTREAM.md`); its 13 skills carry no redistribution grant. The
    plan's rule holds: depend and pin, never vendor.

Everything else matches: node 26.8.1 (≥ 24), npm 11.19.0, uv 0.9.28, ffmpeg and ffprobe 9.0.2 (arm64), higgsfield
1.1.26 (npm's latest), fnf-after-effects-mcp 0.1.3 (npm's latest), After Effects 26.5.0.89, every engine package at its
pin, and Whisper's large-v3-turbo model on disk (1.6 GB).

## Node: the engine (`engine/package.json`)

| Item | Pinned (installed) | Decision | Why | How Slate enforces it |
|---|---|---|---|---|
| remotion, @remotion/bundler, @remotion/renderer, @remotion/cli | 4.0.521 exactly (npm latest 4.0.530) | Keep, pinned exactly | The engine. Upgrade within 4.0.x only after the render-parity check; never 5.0 unplanned, because it brings licence and colour changes | In place: exact pins in `engine/package.json` and `registry.json` (`tools.remotion`). Owed: doctor compares installed versions with the pins (doctor); `film.json` pins the Remotion version at first use (state); the parity check before any bump (engine-core) |
| @remotion/google-fonts | 4.0.521 | Keep | Open fonts | In place: pinned. Owed: the font gate fails on a missing or substituted face (engine-kit) |
| @remotion/three | 4.0.521 | Keep | 3D inside Remotion scenes | In place: pinned |
| @remotion/motion-blur | not declared | Learn from | Test `<HtmlInCanvasMotionBlur>` (≥ 4.0.529) against render_mb; render_mb stays the default | In place: absent from the manifest. Owed: the test runs in a separate engine install (engine-core) |
| @remotion/media-utils † | 4.0.521 | Keep only if imported | Declared by the engine, not in the plan | Owed: drop it at the tracer bullet if nothing imports it (engine-core) |
| @remotion/licensing † | transitive | Not used | No licence key: Remotion is free for now (decision 5) | Nothing passes a `licenseKey`. Revisit per decision 5 |
| react, react-dom | 19.2.8 | Keep | Scenes and kit | In place: pinned |
| typescript | 5.9.3 | Keep | The type-check gate | In place: pinned. Owed: `slate check` runs `tsc --noEmit` from the film folder with the generated tsconfig (engine-core) |
| esbuild | 0.28.1 | Keep, declared | Every event emitter and the hand-in check bundle an act's beats to run in Node | In place: declared (the films relied on it without declaring it) |
| Chrome headless shell | 149, Remotion-managed (not installed for Slate yet) | Keep | Headless rendering with ANGLE GL | Owed: doctor runs `remotion browser ensure` (doctor); `film.json` pins the Chrome version at first use (state) |
| three, @react-three/fiber, @react-three/drei | 0.180.0, 9.7.0, 10.7.8 | Keep | 3D for the open web renderer, and inside Remotion through @remotion/three | In place: pinned |
| gsap | 3.15.0 | Keep, for the open web renderer | Timelines seeked frame by frame | In place: pinned; its licence facts are in registry.json (the Standard "No Charge" License: commercial use and the bonus plugins included, AI-generated code allowed; no no-code tools that compete with Webflow). Owed: before a public release, Quinn decides whether a talk-to-make-a-video tool counts as one (packaging) |
| puppeteer | 25.10.0 (its Chrome 152 not downloaded) | Keep, for the open web renderer | Captures frames of Slate's own local pages | In place: pinned, and every child process runs with `BROWSER=none` (`procs.child_env`, tested). Owed: the web engine loads only `file://` pages or a localhost server it started itself, never a website, with a test (web) |
| postprocessing, @react-three/postprocessing, framer-motion | not declared | Drop | Blank under Remotion; never imported | In place: absent from the manifest |
| vitest, @types/node, @types/react, @types/three † | 5.0.3, 26.6.3, 19.2.2, 0.180.0 | Keep, dev only | Engine tests and types | In place: pinned dev dependencies |

## Python: the CLI, tools and gates (`pyproject.toml`)

| Item | Pinned (installed) | Decision | Why | How Slate enforces it |
|---|---|---|---|---|
| numpy, scipy, Pillow | ≥ 2.0, ≥ 1.13, ≥ 10.0 (2.5.3, 1.18.1, 12.3.0) | Keep | DSP, QA, sheets | In place: pyproject floors. Owed: `uv.lock` pins exact versions (packaging); golden-data tests prove parity on them (each module) |
| jsonschema † | ≥ 4.21 (4.26.0) | Keep | Film, brand and data schemas (`jsonio.validate`) | In place: pyproject |
| mcp, the Python MCP SDK † | ≥ 1.2 (2.2.0) | Keep | The Slate MCP server | Owed: pin the major version (packaging) |
| pytest † | ≥ 8.0 (9.1.1) | Keep, dev only | Tests | In place: the `dev` extra |
| CPython † | ≥ 3.11 (3.13.3 in `.venv`, made by uv 0.9.28) | Keep | The runtime | In place: `requires-python`. The films ran 3.14.7 |
| openai-whisper, models turbo and large-v3-turbo | Homebrew 20250625_6; `~/.cache/whisper/large-v3-turbo.pt` (1.6 GB) | Keep | Default aligner and transcript checks | In place: `registry.json` alignment role; every GateResult names its engine. Owed: doctor checks the model file (doctor); every Whisper gate records the model (timing, voice, qa) |
| ElevenLabs Forced Alignment | `registry.json` role `alignment_eval` | Learn from | Evaluate it against Whisper on the three films' takes | Owed: the evaluation (voice). Whisper stays the default until it wins |
| whisper.cpp | 1.8.4 installed | Drop | Test model only | No module calls `whisper-cli` |
| demucs, librosa | through uvx, analysis only (Demucs 4.1.0) | Keep, in the analyze extra | Reference-film analysis | Owed: an `analyze` extra in pyproject (finish) |
| ocrmac (Apple Vision) | optional extra `ocr`, ≥ 1.0 (not in `.venv`) | Keep | The one OCR engine | In place: UNCHECKED is never a pass (`slate.gates`). Owed: install it into the venv, plus a doctor check (qa-core, doctor) |
| ocr_vision.swift and its compiled binary | none | Drop | One OCR engine, and no swiftc build | Nothing builds or calls it |
| tesseract | 5.5.3 installed | Drop for gates | A second engine would silently change results | Owed: the OCR gate names its engine in the evidence and never falls back (qa-core) |
| beautifulsoup4, html2text.py | never installed | Drop | Project-specific | In place: nothing in `src/` imports them |
| fonttools, uharfbuzz | Higgsedit backend only | Drop with Track A | | Nothing imports them |

## Command-line tools

| Item | Pinned (installed) | Decision | Why | How Slate enforces it |
|---|---|---|---|---|
| ffmpeg, ffprobe | 9.x slim, arm64 (9.0.2 at `/opt/homebrew/bin`; an Intel 7.1.1 also at `/usr/local/bin`) | Keep | Encoding, QA, loudness, PSNR and splices. This build has no drawtext, subtitles or libass, so captions are burned in by Remotion and sheets are drawn with Pillow | Owed: doctor resolves the arm64 9.x build, fails on the Intel one, and records the keg path if a full build is ever needed (doctor); gate evidence names the path and version (qa) |
| VideoToolbox, through ffmpeg's h264_videotoolbox † | in ffmpeg 9.0.2 | Keep | Fast review and phone cuts | macOS first (PLAN.md, Risks) |
| Rubber Band | arm64 4.x (Intel 4.0.0 under Rosetta at `/usr/local/bin`) | Replace with arm64 | Voice pacing: the R3 engine with formants kept | Owed: doctor fails on the Intel build, and `slate doctor --install` offers the arm64 one, asking first (doctor) |
| uv | 0.9.28 | Keep | The Python runtime and installs | Owed: a doctor version check (doctor) |
| node, npm | ≥ 24 (26.8.1 and 11.19.0 at `/opt/homebrew/bin`; a Node 21.6.2 at `/usr/local/bin`) | Keep | Remotion and the AE bridge both need Node 24 or later | In place: the `engines` field. Owed: doctor checks the resolved node's version, not just that it exists (doctor) |
| git, gh | 2.54.0, 2.86.0 | Keep | Film folders and updates | |
| aerender, osascript | After Effects 26.5.0.89 | Keep | The AE lane: finishing and ExtendScript | In place: `procs.which` finds aerender inside the newest AE folder. Owed: AE steps start only on a person's go, with one AE lock shared with the bridge (ae) |
| xcrun simctl | Xcode 27.0 | Keep | iOS captures | Owed: `slate capture ios` (gen-capture) |
| caffeinate, security | macOS | Keep | Keep-awake for jobs; Keychain reads | In place: `slate.secrets` reads through `/usr/bin/security` and never prints a value. Owed: jobs run under `caffeinate -i` (jobs) |
| launchctl † | macOS | Keep | The per-user launchd agent that runs the job queue | Owed (jobs, install) |
| rsvg-convert, potrace | librsvg 2.63.2, potrace 1.16 | Keep, optional | Tracing logos during onboarding | Owed: doctor shows yellow when they're missing (doctor) |
| higgsfield CLI | 1.1.26 | Depend | Sign-in and account status. Slate's generations go through the Cloud API, not the CLI | Owed: `slate connect higgsfield` prints `higgsfield auth login` for the person to run in their own Terminal, and never runs it from an agent (doctor) |
| aside | 1.26.916.1741 | Depend | The only browser on Quinn's Macs | In place: `BROWSER=none` on every child. Owed: web capture goes through Aside (gen-capture); an eval checks that no other browser launches (knowledge) |
| mcporter, sag, peekaboo, songsee, ImageMagick, exiftool | installed: 0.7.3, 0.2.2, 3.0.0-beta3, 0.1.0, 7.1.2-29, 13.25 | Drop | Not needed | No module calls them. They stay installed for Quinn's other work |
| swiftc † | Xcode | Drop | There's no build step: ocr_vision isn't built | |
| rsync, ssh over Tailscale, OrbStack, ollama † | | Drop | Only the WORK render farm and its quiet hooks used them | Left behind with work.sh |
| zsh orchestrators hashing inputs with md5 and ls -lT † | | Replace | `slate make` hashes inputs in Python and records them in `film.lock.json` | Owed (state) |
| Adobe Media Encoder † | 2026, installed, unused | Drop | Masters come from aerender and ffmpeg | |

## MCP servers

| Item | Pinned | Decision | Why | How Slate enforces it |
|---|---|---|---|---|
| Slate MCP (new) | ships with Slate | Keep | A thin wrapper over the CLI and job queue | Owed (packaging) |
| Higgsfield hosted MCP (claude.ai connector) | vendor-hosted | Depend, chat lane only | Generations there bypass Slate's ledger | In place: `ledger.reconcile` settles balance snapshots taken at the start and end of a session. Owed: Oneiro is denied TikTok publishing, website publishing and `sync_agents`, and Slate's Higgsfield client has no `sync_agents` call at all (gen-capture, jobs) |
| ElevenLabs hosted MCP (claude.ai connector) | vendor-hosted | Depend, chat lane only | Same | The same reconciliation. Its brand-kit ids go into `brand.json` grounding |
| fnf-after-effects-mcp, Higgsfield's AE bridge | 0.1.3 exactly (npm latest 0.1.3) | Depend, pinned; a core lane (decision 2) | Builds scenes in AE. The runtime is MIT; the 13 skills have no redistribution grant | In place: `registry.json` pins it exactly. Owed: it shares Slate's AE lock, gets acceptance tests on every version bump, and falls back to Slate's own ES3 library when it fails. It needs AE's "Allow Scripts to Write Files and Access Network" pref, which only Quinn turns on and `ae.prefs` refuses to touch (ae) |
| Aside MCP | with aside | Depend | Web capture and research | Owed (gen-capture) |
| mac-cua | Quinn's repo | Optional | Dismissing AE dialogs and background app control | Its browser tools attach to Dia and are forbidden; Slate never calls them |
| 21st.dev MCP | vendor-hosted, paid | Optional, project-level | Design inspiration | A person registers it per project; install.sh never does. Only generic design queries leave the Mac |
| ElevenLabs local MCP (`elevenlabs-mcp`) | | Drop | Archived and deprecated in favour of the hosted MCP, and it exposes `make_outbound_call` | Owed: install.sh never registers it, and doctor flags it if any client has it (install, doctor) |
| Remotion docs MCP (`remotion-documentation`) | | Drop | Deprecated, and scheduled for shutdown no earlier than 2026-08-31. remotion-dev/skills' `/remotion-docs` replaces it | install.sh never registers it |
| logic-pro-mcp | | Drop | Evaluated, not used: it drives Logic through the UI and MIDI, needs Accessibility, Automation and Input Monitoring grants plus a control surface, and ships unsigned binaries | |
| Framer, Linear, truespace-sidecar, oneiro-memory, apple | | Not part of Slate | The website, tickets, company operations (off-limits), Oneiro's memory, and Oneiro's App Store Connect broker | Slate registers none of them. The pursuit-slice eval runs with only aside and apple, as those slices do |
| The Oneiro connector, node_repl, openaiDeveloperDocs, computer-use, the Claude desktop extensions (Filesystem, iMessage, Control your Mac), Gmail, Vercel, visualize, bio-research, scheduled-tasks † | | Not part of Slate | Other work | Held-film messages go through Slate's own notify module (finish) |

## Skills

| Item | Owner and licence | Decision | Why | How Slate enforces it |
|---|---|---|---|---|
| visionary, goal-fidelity | Quinn | Depend: their own shareable repo, symlinked into every personal agent | Decision 10 | Owed: install.sh symlinks them after Quinn's one-time yes (install) |
| ponder | Quinn | Depend, optional | A QA loop over every shot | |
| video-verifier | third party, no licence file | Depend, for UI recordings only | Its web path is overridden to Aside | Slate never copies its scripts; `slate capture ios` is Slate's own code. Owed: routing evals keep UI screen-recording requests with it (knowledge) |
| higgsfield-* (8) | Higgsfield, MIT; v0.13.0, installed 2026-09-29 from higgsfield-ai/skills | Depend; global, and installed for Codex and OpenClaw too | Decision 11. They generate on app credits, which aren't funded | Owed: install.sh installs them for Codex and OpenClaw with the vendor's own installer, never by copying (install) |
| remotion-dev/skills (12) | Remotion; no licence file, so the Remotion License applies | Depend, inside the engine folder, not globally | Remotion know-how where the engine is | Owed: install into the engine folder (engine-core). Slate overrides its old voice model (`eleven_multilingual_v2`) with `registry.json`'s, never uses its meme sound effects, and Studio never auto-opens. Not installed yet |
| elevenlabs/skills (10) | ElevenLabs, MIT | Depend, optional, inside the engine folder, not globally | API mechanics, next to the engine that uses them | Owed: install into the engine folder (engine-core). Not installed yet |
| AE bridge skills (13) | Higgsfield (FNF); no redistribution grant | Depend, served by the bridge; used as AE looks | | Read through the bridge's `ae_get_skill`, never copied |
| asset-creator, canvas-design, frontend-design, avoid-ai-writing | third party, Anthropic | Optional | Vector drawing, frames, component looks, script polish | Never copied and never required |
| truespace-frontend-taste | Quinn | Moves to the TrueSpace pack | Brand-specific | Never in `~/code/slate` |
| aside-browser, mac-cua † | Aside; Quinn | Depend with Aside; optional with mac-cua | They travel with their tools | |
| peer-files, cross-machine † | Quinn | Not part of Slate | They moved renders to and from WORK, which is never used for renders | |
| skill-creator, write-a-skill † | Anthropic, mattpocock | Not part of Slate | Aids for writing Slate's skills, not runtime dependencies | |
| OpenClaw's bundled skills (video-frames, openai-whisper, sag, songsee, mcporter, summarize, peekaboo) † | OpenClaw | Not part of Slate | Slate's CLI covers what a film needs | |
| Oneiro's workshop skills, Codex's imagegen, the Leonxlnx taste-skill set † | Oneiro, OpenAI, third party | Not part of Slate | Other agents' own tools. Designed content is code, and generation goes through Higgsfield's Cloud API | |
| Higgsfield's remote recipes (`/use-after-effects`, `/saas-animation`) † | Higgsfield, through the hosted connector | Learn from | The recipe text is free; generating inside them spends app credits, which aren't funded | |
| HyperFrames skills | HeyGen, Apache-2.0 | Learn from | Its router skill, brand-token translation (`frame.md`), media ledger and audio patterns | Ideas only. If anything is ever copied, its Apache-2.0 notice comes with it |
| OpenMontage | AGPL-3.0 | Never copied | Its licence would force AGPL on Slate | No code, prompts or skill files from it enter the repo |

## Services and pipelines

| Item | Pinned | Decision | Why | How Slate enforces it |
|---|---|---|---|---|
| ElevenLabs: eleven_v4 TTS, eleven_ttv_v3 Voice Design, Voice Remix, Voice Isolator | `registry.json` roles; pinned per film at first use | Keep | Voice | In place: `registry.json` roles with verified-on dates; the ledger. Owed: the model id is always passed, because the API defaults are older models; v4 audio tags never include whisper, soft, quiet or gentle; clones only of Quinn's own professional voice, which he verifies (voice) |
| ElevenLabs subscription endpoint † | | Keep | Doctor's free key check, and credit snapshots before and after paid work | Owed (doctor, ledger) |
| Eleven Music | `registry.json`: `music_v2_5` (not yet proven with plans; the films used `music_v1`) | Keep, on its best model | A score is part of most films (decision 7) | Owed: compositions run under the job queue's music lock and through the ledger; broadcast formats flag that film, TV and radio need an Enterprise plan (sound, formats) |
| ElevenLabs Sound Effects | `registry.json` role `sfx` | Learn from | Filling a gap only; Slate's sound is designed | |
| ElevenLabs creative connector: brand kits, image, video and upscale † | vendor-hosted | Brand-kit ids: keep, as grounding in `brand.json`. The rest: not part of Slate's default chain | A whole-film Topaz upscale (about 310,000 credits for a 6-minute film) is beyond the plan | |
| Higgsfield Cloud API | `registry.json` plate roles; pinned per film | Keep | Plates, per generation: the one funded lane (decision 6) | In place: the ledger (estimate, budget check, an in-flight row with the request id before waiting). Owed: estimate first, never retry a POST, at least two takes judged, and Oneiro asks Quinn before every generation (gen-capture) |
| Higgsfield video analysis (0 credits) | | Keep | `slate analyze` | Owed: a CLI-lane route, or the step reports UNCHECKED (finish) |
| Higgsfield CLI and app credits | | Off; not funded | A separate billing pool (decision 6) | Owed: no Slate generation runs on app credits (gen-capture) |
| Higgsfield native audio on plates † | | Keep, as plate ambience only | The films used a plate's own wind about 30 LU under the voice | Owed (gen-capture) |
| Higgsfield's text-to-speech models † | | Drop | The runner-up clipped the brand word, so voice goes to ElevenLabs direct | |
| Higgsedit (Track A) | | Benchmark in phase 8 | Kept as the fast route for social cut-downs only if it wins (decision 9) | Its lessons are in the knowledge ledger (`cm-021`, `pl-068`) |
| Apple sound content (GarageBand, Logic, Final Cut Pro) | per machine | Keep, local only | Tonal roles use real sampled instruments | Owed: built locally and never redistributed; doctor fails when it's missing rather than faking it (sound, doctor) |
| Apple Vision OCR, through ocrmac | | Keep | The one OCR engine | See the Python table |
| Whisper, local | | Keep | See the Python table | |
| Adobe After Effects 2026 and aerender | 26.5.0.89 | Keep | Finishes every supervised film, and is a second way to build scenes | Owed: a person starts every AE step, and autonomous mode never touches AE (PSLT §1.2). `ae.prefs` is the only way to change AE's preferences: one key at a time, with AE closed, and never the scripting pref (ae) |
| Adobe Fonts † | per brand | By reference, in each brand pack | Brand fonts are licensed per brand | Owed: `brand.json` names a kit URL or installed family with its licence, the font gate fails on substitution, and no font files go in any repo (brand, engine-kit) |
| Google Fonts † | through @remotion/google-fonts | Keep | Open fonts | |
| Remotion licence | free (decision 5) | Keep free for now | Quinn is the only operator, and the films are unpublished pilots | No licence key is configured. Revisit when a TrueSpace or EOS film is about to be published; the README tells companies of four or more to get their own licence, or to use the open web renderer or After Effects |
| The WORK render farm (`work.sh`) | | Drop | WORK is never used for renders or builds | Rendering is local, and no remote backend exists |
| Logic Pro † | 11 | Not part of Slate | Hand polish of stems, outside the automated chain | |

## Considered, not used

| Item | Licence | Decision |
|---|---|---|
| Vincentwei1021/video-shotcraft | Apache-2.0 | Learn from: Remotion shot-recipe cards (Remotion's licence still applies) |
| digitalsamba/claude-code-video-toolkit | MIT | Learn from |
| howseen-ai/claude-motion-design | MIT | Learn from: HTML, Playwright and ffmpeg. Slate's web engine uses puppeteer on local pages only |
| zhuyansen/awesome-claude-video-skills | none asserted | An index of other repos; nothing to take |
| motion-canvas, revideo | MIT | Alternatives Slate doesn't need |
| ElevenLabs SDKs and CLI (`@elevenlabs/elevenlabs-js`, the `elevenlabs` Python package, `@elevenlabs/cli`) | | Not used: Slate's ElevenLabs client is a small standard-library client behind the ledger and replay mode |
| Higgsfield SDKs (`higgsfield-client`, Apache-2.0; `@higgsfield/client` v2) | | Not used, for the same reason |
| @remotion/whisper-webgpu | | Not used: Whisper runs locally |

## Hosts Slate installs into

These aren't dependencies; they're the front doors. Checked on 2026-09-30.

| Host | Version | Where Slate goes |
|---|---|---|
| Claude Code | 2.1.282 | Skills symlinked into `~/.claude/skills`; `claude mcp add --scope user`; the CLI in `~/.local/bin` |
| Codex | 0.153.4 (npm); 0.155 inside ChatGPT.app, per the inventory | `~/.agents/skills` only; `codex mcp add` |
| OpenClaw | 2026.9.4 | `~/.agents/skills`; `openclaw mcp add <name> --command … --arg … --connect-timeout 30` (2026.9.4 refuses an existing name and probes the server first; the plan's `openclaw mcp set` is the older form); the CLI also linked into `/opt/homebrew/bin` |

Doctor checks that each Slate skill appears exactly once in each host.

## What Slate deliberately left behind

These stay in the film project, untouched. Nothing was moved or deleted. Each item says why, and what survives in
Slate.

1. **All TrueSpace, Vixa and Trudy content.** The brand's tokens, logos and character mark, the sample company and its
   numbers, the product's report surfaces and data, the partner card and field guide, the character's voice brief,
   voice design spec and tagged lines, the films' scripts, treatment, capability brief, analysis, research and product
   truth, the website's component library, and the brand inputs under `research/` and `reference/`. The inventory
   classed 28 components as TrueSpace content: `ve-013`, `ve-019`, `ve-020`, `ve-023`, `ve-029`, `ve-032`, `ve-033`,
   `ve-034`, `ve-035`, `ve-036`, `ve-075`, `ve-079`, `sv-037`, `sv-044`, `sv-045`, `sv-054`, `pl-008`, `pl-009`,
   `pl-010`, `pl-011`, `pl-016`, `pl-017`, `pl-059`, `pl-065`, `cm-015`, `cm-026`, `cm-031`, `cm-032`. Why: no brand
   lives in the core, and company material never enters this repo. Whatever a TrueSpace film needs again goes to the
   private TrueSpace pack in `~/code/slate-brands` on PERSONAL, and the company's code is only ever read in place on
   WORK. What survives in Slate is brand-neutral: three pieces of generic work (the draw-any-SVG helper from `ve-019`,
   the radar chart from `ve-033`, and SOUND.md's design rules from `sv-054`, rewritten without the brand) and the
   templates the content taught (treatment, capability brief, voice brief, product truth, research method, readable
   script).
2. **The three films' scenes.** Every act of films A, B and C under `pipeline/r3f/src/`, the first film's opening
   look test, the act-boundary rest states, named places and stacking order, the plate sequences, and the third film's
   event rows (`ve-050`, `ve-051`, `ve-031`, `ve-037`, `ve-049`, `ve-053`, `cm-020`). Why: they only mean something
   for those films. Their act NOTES.md lessons go into the knowledge ledger, their data is the golden-test oracle
   (read in place), and the patterns they proved are Slate's: shared contract components at act joins, a placeholder
   act for parallel builders, and one `slate emit-events` in place of 22 emitters.
3. **`work.sh` and the WORK render farm** (`ve-004`, `aq-037`, `pl-066`, and the two notes `aq-048` and `aq-049`).
   Why: WORK is never used for renders or builds (Quinn, 2026-09-30). The film's own `render_local.sh` path is Slate's
   default: local renders through the job queue, with no remote backend, no quiet hooks and no private npm mirror.
4. **The retired v2 sound design, with its plumbing kept.** Left behind: the thirteen-family vocabulary and its bank
   (`sv-020`, `sv-034`, `cm-025`), the planner's design tables (`sv-004`, `pl-031`), the v2 cue sheet and render log
   (`pl-032`, `cm-019`), the one-film migrations, comparisons and repairs (`sv-027` to `sv-031`, `sv-035`, `pl-067`),
   SOUND_V2.md as a design (`sv-055`, kept as a record only), and v2's measuring tools (`sv-024`, `sv-025`, `sv-026`,
   `pl-037`), which come back only if the proof-3 re-inventory wants them for the no-repeats gate. Why: Quinn retired
   v2 on 2026-09-30 and went back to proof 2. Kept, per `sound/v2/HANDOVER.md`: the beat schema (`sv-056`), VO
   placement and levelling, the follower duck, stem writing and the music-gap check (`sv-022`), `final_mix.py`
   (`sv-006`, `pl-036`), `verify_mix.py` (`sv-023`), the DSP helpers (`sv-021`), the mix records (`pl-033` to
   `pl-035`), and the Eleven Music playbook (3-second pre-rolls, parts under 90 seconds, measured assembly,
   equal-power joins, freeze repairs). The effects engine grows from proof 2's renderer and the p3 pass once proof 3 is
   approved.
5. **The rejected 3D pass** (`ve-011`): a lit 3D take on the brand's signature line, and the postprocessing packages
   that rendered blank under Remotion. Why: Quinn chose flat 2.5D, and the pass never shipped. three.js, React Three
   Fiber and drei come back for a different job, the open web renderer, and the accumulation renderer (`ve-010`) stays
   for deterministic depth of field there.
6. **The website tooling.** The Framer build and push toolchain (`ve-078`), framer-code and the other website-only
   folders (`cm-032`), the website component library and its audit (`ve-075`, `ve-076`, `ve-079`), the shared project
   README (`cm-030`), html2text and beautifulsoup4 (`ve-069`), the Framer MCP, and truespace-frontend-taste, which
   moves to the TrueSpace pack. Why: it builds the website, not films.

## Keeping this ledger true

- **Adding a dependency:** add its row here in the same change, pin it (`engine/package.json`, `pyproject.toml` and
  `uv.lock`, or `registry.json` tools), and give doctor a check for it.
- **Bumping a pinned version:** Remotion needs the render-parity check (every frame of the first film's Act 4 at
  40 dB PSNR or better against the delivered render). The AE bridge needs its acceptance tests. A model change goes
  through `registry.json` with a verified-on date, and never changes a pinned film.
- **Dropping:** take it out of the manifests and say why here. "Drop" means Slate doesn't depend on it. It never means
  uninstalling anything from Quinn's Mac.
- **Re-checking:** this page was checked with read-only commands: `node --version`, `npm --version`, `uv --version`,
  `ffmpeg -version`, `ffprobe -version`, `higgsfield --version`, `aside --version`, `codex --version`,
  `openclaw --version`, `claude --version`, `xcodebuild -version`, `brew list --versions`, `file` on the
  `/usr/local/bin` binaries, `procs.which` under a default PATH, installed versions read from
  `engine/node_modules/*/package.json`, the `.venv` and the After Effects app's Info.plist, and npm registry metadata
  for remotion, @higgsfield/cli and fnf-after-effects-mcp (fetched, not installed).
