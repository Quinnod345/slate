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

### The skills and the plugin catch up (2026-10-01)

- **The films' lessons, where agents meet them.** The 56 lessons the knowledge ledger listed for the skills are in the
  skills now, generalized: sending a mark's parts away, moving contracts, shared product surfaces, paper techniques,
  plate overlays, read time by measured cap height, the stability flags a clean act still shows, the score's no-names
  rule, the voice polish's first question, a voice change moving the balance, and the rest. A rule every film needs is
  in its skill's SKILL.md; the rest sit in the reference for that work. `tests/skills/test_skills_lessons.py` holds
  each one by its lesson id.
- **What landed in integration, documented.** `slate voice polish`, `slate voice analyze` and every step of
  `slate voice tags` (rules, set, check, strip, ab, and the tag loop's optimize, pick, abc and adopt);
  `slate qa stability` (its triage order, baselines, references and strips); the six balance
  gates, the score's level and its guard; spans on visual events (`t` is always the beat); web act hand-ins and
  `slate engine scaffold`; a trace's `class`, `exempt` and `reason` and the `even` curve; `slate formats show`, the
  delivery's platform files and onboarding's spend line; the ledger snapshot and reconcile at both ends of a session;
  and the gateway route: from inside the OpenClaw gateway, every paid stage is a job, because its proxy blocks the
  vendors and the job runner works outside it. The newest film steps are described too: an input copied in and locked
  by its hash, an autonomy grant recorded with its scope, a film in iCloud Drive that never uploads its heavy folders,
  and plate timing repair.
- **The MCP server knows the new stages.** The tag loop runs through it (`voice tags estimate`, `pick` and `adopt` now;
  `voice tags optimize`, which spends, and the `abc` listening reel as jobs), and so does render stability (`qa
  stability` as a job, `compare` and `strip` queued on the render slot). Accepting a stability baseline, choosing a
  reference and approving a take are refused with where a person runs them. `qa triage` through the server works again
  (it was reported as not built).
- **The whole plugin is generated.** `slate skills build` now writes the plugin's manifests, its MCP configs (Slate's
  server plus ElevenLabs' and Higgsfield's hosted connectors, which sign in with OAuth, so no key is ever in the file),
  its README (with the auto-update step and a permission allowlist per agent for unattended runs) and its LICENSE, so
  nothing in `plugins/slate` is edited by hand. The build refuses media in a skill, a listing past Claude Code's
  1,536-character cut, a skill list past Codex's 8,000-character budget (it notes one within a tenth of it), and a
  plugin past claude.ai's 200 MB or 5,000 files. `--check` also runs `claude plugin validate --strict` and
  `skills-ref validate` wherever they're installed, each skipped with a notice where it isn't (`--no-validate` skips
  them).
- **Client behaviours, recorded.** Five behaviours the packaging could lean on are recorded with their hands-on checks
  in `tests/packaging/client_behaviours.json`: `claude plugin validate` taking both manifests and the gateway's default
  state folder are confirmed; OpenClaw's placeholders in Claude bundles, Codex reading Claude-style marketplace sources
  and nested skill metadata stay unconfirmed, and Slate relies on none of them.
- **Every cited step and flag is real.** The skills tests hold each `slate …` a skill cites to the live CLI below the
  stage too: a step such as `slate qa stability strip` and every `--flag` must be one the command takes.

### The films' lessons, closed (2026-10-01)

- **A film made autonomous stays autonomous.** `slate new` and `slate variant` record each film's mode in Slate's own
  data folder, outside the film, and every rule that depends on the mode (the monthly caps, the capped key, a yes per
  generation, After Effects, the queue, review routing, the claims gate, voice and sound) judges the film by that
  record, whatever its film.json says. Only a person at their own Terminal changes it, with `--by` and `--reason`; an
  agent's attempt is refused and recorded. The owner's own AE finish of a held film is `slate master --ae --go` in
  their Terminal, after a final-cut approval of the held master. Inside the OpenClaw gateway, vendor calls queue
  themselves for the launchd runner, and a proxy's refusal names the allowlist and the queue route.
- **New film steps.** `slate film input` copies a supplied file in, verified and locked; `slate film icloud` keeps a
  film's heavy folders out of iCloud sync, with a yes; `slate approve grant --scope` records an autonomy grant in the
  owner's words, as its own approval kind. The mix goes stale when the brand's sound palette or the effect cues change,
  and cancelling a generation job cancels its requests at the vendor.
- **The kit and the engine.** A count never re-centres (its box is its own nines until it has the final figures, a
  suffix holds still, `followed` keeps the next word still) and shows one value per master frame; the look's line
  weight everywhere; dots that grow in and nothing drawn at zero length; staged axis labels; two-sided cards; the
  character's disc placed by a transform, staging props kept apart from gestures, the gesture sheet and the rig's
  combination rules; a mark drawn in its own colourway for the ground. `slate check` adds never-claims, frame
  literals, timing margins with the rig lint, the character's acceptance checks, `--markup [--against]` and
  `--determinism`, and hands in a web shot inside a Remotion act. `slate engine golden [--approved FILM]` proves a kit
  change against the kit's stills and every approved act. An act render proves itself before it replaces the
  registered one, and the AE lane starts acts from the brand's `look.engine`.
- **Sound, voice, timing and story.** New gates: `sound.voice_limiter`, `sound.key`, `sound.timbre`, `sound.mix_words`
  and `sound.balance.approved_spans`. A 25 Hz high-pass on every part, big moments that land together, act joins
  measured, a silent last second by default, and `slate mix --listen`. `slate voice comp|preview|swap|trim`, each live
  read stitched to its neighbours' request ids, `slate timing pin`, one impact per act, `script.hero_lines`,
  `script.on_screen`'s 40% of the voice, and quoted product facts re-read at their pinned commit. Whole-mix parity
  for all three films (opt-in and heavy).
- **QA, finishing, generation, brand and doctor.** The `cuts` and `end_hold` gates; title-safe and body type size;
  hero entrances that move; type that waits for the camera; gestures on their word; banned words in every form and in
  what the voice said; read time counted only while text is legible; glitch flags explained by motion or a fade;
  blank-frame runs; `slate qa stability baseline --drop`. `slate plates repair` re-times a plate that hitches (numpy
  and scipy, no OpenCV), and the conform warns about one. `slate review --audio` reviews a proposed mix; a delivery
  writes its README from its own records and tags a snapshot of the code and data behind it; review cuts play the
  voice before the mix exists; a generation's audio flag goes out explicitly off. Brand packs keep their website's
  copy and take banned-word forms with substitutes, `truth.self_commentary` and a mark's colourways. Doctor adds the
  gateway's egress proxy, AE's launch dialogs, one MCP server copy per client, the Claude Code version and a stray
  `.env`.
- **The ledger, re-mapped.** Every one of the 166 lessons that were gaps now lands somewhere real or waits with its
  planned file: 147 closed, each with its file, symbol and tests; 10 wait for proof 3 and 9 for the tilt wave. The film
  session's work after the second harvest is harvested as an addendum (8 lessons). `slate knowledge gaps --write`
  regenerates `knowledge/GAPS.md` from the ledger, and a test holds the file to it.

### Still to come in 0.1.0

Waiting for a person: the first live After Effects finish (`slate ae smoke --go` and the rest, on Quinn's go), the
effects engine (after proof 3 is approved) and the agents' live eval runs. Being built: the kit's tilt raster mode and
its stability bench (bench first), then the effects engine's spotting, guards and family stems once proof 3 is
approved; `knowledge/GAPS.md` lists each with its planned file.
