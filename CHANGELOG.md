# Changelog

Every notable change to Slate, newest first. Versions follow semantic versioning: the command line, the film folder's
files and the MCP tools are the public surface, and a change that breaks one of them bumps the major version.

## 0.1.0 (in progress since 2026-09-30)

The first build, extracted and generalized from the pipeline behind three brand films. No brand is in the core: brands
are data packs, and the tests use a neutral demo brand.

### Added

- **Foundation.** Paths, settings and the model registry, Keychain-backed secrets, child processes that never open a
  browser, atomic JSON, the film clock, gate results (PASS, FAIL, REVIEW, UNCHECKED, STALE), the spend ledger, replay
  mode for vendor calls, and the `slate` dispatcher with shared exit codes.
- **Film state.** `slate new`, `status`, `make`, `approve`, `film`, `variant` and `revise`: a film is a folder, every
  derived file records what made it, pins keep a re-voice in week three matching week one, and approvals bind a
  person's yes to a SHA-256.
- **Story and timing.** `slate script build` and `lint` gate the one hand-edited script; `slate timing` puts shots,
  voice cues, words, events and the timeline on one clock. Reruns on the three source films match their delivered
  data.
- **Voice.** `slate voice` casting, design, tags, TTS, clean-up, pacing, alignment, captions and checks, through one
  replay-aware ElevenLabs client, with the cloning policy and the voice-tag rules enforced in code.
- **Sound.** A BS.1770-4 meter, the fixed-gain mix whose four stems sum to it, twelve verify gates, and the Eleven
  Music score pipeline; `slate sfx` refuses until proof 3 is approved.
- **Generation and capture.** `slate gen` and `slate plates` on the Higgsfield Cloud API (estimate first, two takes
  judged, a person's pick), and `slate capture ios|web` conformed to the film's clock and beat-mapped to its words.
- **QA.** `slate qa` with sixteen film gates plus the motion and accessibility gates, triage, PSNR and parking for
  autonomous films.
- **After Effects.** The byte-safe prefs tool, the AppleScript driver, data-driven project builds, chunked masters,
  the smoke test and the pinned bridge wrapper, all waiting for a person's go.
- **Engine and render.** The Remotion kit (film clock, motion law, cards, type, app UI, charts, logo build, motif rig,
  captions, review sheets), `slate engine`, `slate emit-events` and `slate check`, and `slate render`: chunked,
  resumable renders with proven joins and splices.
- **Open web renderer.** `slate render --engine web`: a scene as a local page, seeked frame by frame in headless
  Chrome that only ever loads Slate's own pages, then encoded. It needs no Remotion licence.
- **Reference analysis.** `slate analyze <video>`: cuts, on-screen text, measured eases, the transcript, stems and
  beats of a reference film.
- **Jobs and spend.** One machine-wide queue for long work (`slate job`, `slate jobs`), resource slots, disk
  reservations, resumable jobs, and `slate budget` and `slate ledger` with per-film and monthly caps.
- **Evals.** `slate evals`: triggering, routing, negative cases, the dry run and the no-browser check across four agent
  configurations.
- **Doctor and connect.** `slate doctor` checks everything Slate needs on this Mac and only reads: tools the way Slate
  runs them (and the stale Intel copies an old Homebrew left in `/usr/local/bin`), the engine and its Chrome, keys by
  presence and the vendors' free probes, After Effects without ever launching it, every agent's skills and MCP
  registration, the job queue's runner and launchd agent, the default browser, and `~/.codex/AGENTS.md`'s Dia line.
  Nothing unchecked is green. `slate connect claude|higgsfield|elevenlabs|aftereffects` does the promise's connect
  steps; keys are typed only by a person, in their own Terminal.
- **Install and update.** `install.sh` (one command, safe to run again), `slate install`, `slate uninstall` and
  `slate update`: skills symlinked from one checkout into `~/.claude/skills` and `~/.agents/skills` (never
  `~/.codex/skills`), the CLI in `~/.local/bin` and `/opt/homebrew/bin`, and the MCP server registered with Claude
  Code, Codex and OpenClaw (`openclaw mcp add`, with a 30-second connect timeout); a scripted fresh-home test.
- **Skills.** The ten skills in the open Agent Skills format (the `slate` front door plus story, motion, build, voice,
  sound, generate, finish, QA and brand) and `docs/METHOD.md`.
- **Packaging.** `slate mcp`, Slate's MCP server over the command line and job queue (long work always becomes a job);
  the desktop extension for the Claude app (`mcpb/`, packed by `slate mcpb build`); the plugin for Claude Code and
  Agent Plugins clients (`plugins/slate`) with its marketplace (`.claude-plugin/marketplace.json`); `slate skills
  build`, which generates the plugin's skill copies, Codex sidecars, OpenClaw entries and knowledge-only zips for
  Claude chats, with `--check` for CI; `slate guide`; the README, AGENTS.md, this changelog, the licence, the decision
  records and CI.

### Integrated (2026-10-01)

- **Exit 75, try later.** A vendor's rate limit (429) or an unavailable service (503) is `TryLater`, exit 75: a queued
  job goes back in the queue after a backoff that's never shorter than the vendor's own Retry-After, instead of
  failing. A Higgsfield submit whose answer was lost after its keyed resends is a try-later too (the same
  Idempotency-Key makes it safe).
- **Stopping cleanly.** `slate` turns SIGTERM and SIGHUP into an orderly exit (143, 129), so a command killed directly
  stops every process it started, as the queue's own cancel already did.
- **JSON all the way.** With `--json`, a usage error is a JSON object on stdout too, and once a command's object is out,
  nothing native code prints can follow it there (Apple Vision's diagnostics go to stderr, buffered lines included).
- **Gates that don't go stale for nothing.** Gate inputs can be named (`film:<key>`, `film:*`, `brand:<name>`,
  `format:<name>`); a gate that reads film.json records it without its bookkeeping, so a pin, a version or a budget
  raise no longer turns every gate STALE.
- **The command list, exactly.** Each command is registered once, by its own module, and the tests hold `slate --help`
  and ARCHITECTURE §4 to the CLI both ways. `slate qa stability <action>` reaches the stability gate's own tools, and
  `slate sound balance` the balance; their gates join `slate qa` and `slate verify`.
- **Deliveries need their sound gates.** A master with sound delivers only when `slate verify`'s gates pass (the
  balance gates among them), and a draft delivery can't be approved as the final cut.
- **Variants know their channel.** `slate variant … --channel tiktok` records the channel's route; a social cut-down
  masters with ffmpeg.
- **The registry, re-checked.** The knowledge owner's 2026-09-30 re-check merged into registry.json: Forced Alignment
  at 330 credits a minute, music_v2_5's chunk plans and limits beside music_v1's sections, corrected Seedance and Kling
  prices with their discounts' end dates, a providers block (ElevenLabs plans and terms; Higgsfield's estimate reading,
  idempotency, errors and billing), Remotion 4.0.531 noted while Slate stays on 4.0.521, and the GSAP and
  fnf-after-effects-mcp licence facts. Video plates stay at 720p by default; 1080p (2.46x the price) is the owner's
  call.
- **OCR on.** The `ocr` extra (ocrmac, Apple Vision) is installed in Slate's environment; the default test run still
  never loads Vision (`SLATE_TEST_VISION=1` opts in). pyproject pins the MCP SDK to 2.x and records the `analyze` extra.
- **A quicker MCP start.** `slate mcp` answers a client's initialize without importing every module first (about
  0.5 s instead of 2 or more on a busy Mac); the whole CLI loads on its first tool call.
- **Story honours declared reading.** A shot's `reading` (voice-matched, or not for reading, with its reason) passes
  the word budget and read-time gates the way `slate qa readtime` does, and travels into the storyboard.

### Still to come in 0.1.0

Being finished: brand packs and onboarding, formats, and the finish stages (`slate master`, `deliver`, `review`).
Not built yet: the demo film (`slate demo`), which needs a quiet Mac to prove its render end to end.
