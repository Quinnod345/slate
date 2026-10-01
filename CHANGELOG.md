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

### Proven end to end (2026-10-01)

- **`slate demo`.** A neutral 15-second 9:16 reel for the demo brand, made through every stage, each the real command
  in its own process with one line saying what it did and how long it took: the script, the voice with its studio
  polish, alignment and an A/B spectrogram sheet, the film clock, captions, a placeholder act and a kit-built act
  rendered at 4 samples, the score and the mix, the sound, voice and film gates (OCR on Apple Vision), an ffmpeg
  master, the review packet (copied to a scratch folder, nothing sent) and a draft delivery whose manifest says why it
  isn't final. The vendors are replayed, so nothing is spent; it exits non-zero when a stage fails, and every gate
  that isn't PASS has to be one it expects, with its reason. `--keep` keeps the film; `--live` spends after a person's
  yes (asked in a Terminal, refused under an agent unless the person set `SLATE_LIVE=1`). The whole run is an opt-in
  test (`SLATE_TEST_INTEGRATION=1`, three to four minutes).
- **The studio polish reaches the mix.** Timing now takes a shot's polished take ahead of the paced one it was made
  from (the polish keeps every sample where it was), so the cue sheet and the mix play the de-essed, EQ'd take. The
  demo fails if they don't.
- **Where the harshness lives.** `slate voice analyze --spectrum` measures each take's long-term spectrum in third
  octaves against natural speech (ANSI S3.5), so a glassy 2-5 kHz edge or hot 5-9 kHz sibilance shows as a shape, not
  a hunch; the sheet rings each take's hottest edge and sibilance moments, and `slate voice polish --try` adds the
  spectrum per strength and a reel of the harshest moments to its trial.
- **The score sits where the owner approved it.** The starting palette puts the score 18.5 LU under the voice with a
  6 dB duck (proof 2's 15 and 4 before), the p3 balance that `sound.balance.levels` holds every mix to: a default mix
  used to fail the gate that delivery requires.
- **The score's own clarity guard.** `slate mix` rides the score's top (above 600 Hz) under any word it would crowd,
  by just enough to keep 19 dB of clear voice in 1-5 kHz (at most 20 dB, held 0.15 s, ramped over 0.3 s), and records
  every dip in `final_mix.json`. A brand turns it off with `score_guard: false` in its palette's balance.
- **A fade into a planned silence is part of the silence.** `sound.gap` widens each planned stop by the music's own
  fade, the rule approved on 2026-09-30, as `sound.planned_stops` does; a hole anywhere else still fails.
- **Silent on purpose.** A score-and-voice film's effects and ambience stems are still written (every delivery has
  four), and the mix record names them in `empty_stems`, so `silent_stems` passes them and still fails a stem that came
  out silent by mistake.
- **Schemas for every film file.** The motion trace has its schema; the polish report, the voice analysis and the tag
  candidates claim their own files; a composition plan validates as one; the shot-id freeze gate names its engine. The
  schema gate no longer goes stale the moment a later command writes `qa/gates.json` or `film.lock.json`.
- **Steady placeholders, planned captions.** The kit's placeholder picks out the word being said by colour alone (a
  bold word reflowed its line, which the stability gate caught), and a burned-in caption coming or going is a planned
  change on the clock, like a beat.
- **Smaller fixes on the way.** `slate qa --quick` skips the stability scan with the other slow gates, and `slate qa
  triage` shows each act's stability scan the way it shows the glitch scan. `slate analyze` and the doctor honour
  `SLATE_WHISPER`, like timing and voice. The brand's kit review sheet is stilled at frame 45, so the logo build and
  type in motion show, and it's proven to render. The MCP server can queue `voice polish` and `voice analyze`.

### Still to come in 0.1.0

Waiting for a person: the first live After Effects finish (`slate ae smoke --go` and the rest, on Quinn's go), the
effects engine (after proof 3 is approved) and the agents' live eval runs. Being built: the changes the knowledge
ledger lists as gaps (`slate knowledge check` names each; `knowledge/GAPS.md` groups them by module), the accent and
air guards (with the effects engine) and the kit's stability bench.
