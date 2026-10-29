# Changelog

Every notable change to Slate, newest first. Versions follow semantic versioning: the command line, the film folder's
files and the MCP tools are the public surface, and a change that breaks one of them bumps the major version.

## 0.1.0 (in progress since 2026-09-30)

The first build, extracted and generalized from the pipeline behind three brand films. No brand is in the core: brands
are data packs, and the tests use a neutral demo brand.

### Owner-approved Voice Library voices (2026-10-15)

A voice profile can now carry a `library_approval`: the owner's recorded yes for one shared ElevenLabs Voice Library
voice that the vendor labels "professional". With it, the voice is used as a library voice in `voice tts`, `voice
tags`, `voice ab` and `voice cast`, all of which share the profile's policy and vendor checks. Until now Slate refused
these voices as someone else's clone; Quinn's rule (2026-10-12) is about clones of people he works with, not voices
their owners share in the vendor's library. The approval names the exact voice id, who approved it, when and the
ruling. It never covers an instant clone, a famous voice or a clone of a named person, and autonomous films need
`autonomous: true` in the approval. The policy tests cover each case; `voice-policy.md` documents the field. The Vixa
brand pack records its narrator, Adam, this way, so its films no longer need a film-local wrapper.

Also fixed: the `voice_fit` gate now measures a trimmed take by its play length (`slate voice trim`), the same
length timing places it by; before, it measured the whole file and disagreed with timing.

### Correctness pass (2026-10-04)

- **Alignment comparisons measure the same words.** Only equal normalized occurrences contribute timing
  disagreement; substitutions, missing and added words remain explicit with coverage. Saved engine copies must
  bind current selected audio and forced-alignment text. Invalid clocks and incomplete inputs return exit 1.
  Replay stays labelled, and comparison readiness does not grant timing quality or listening approval.

- **Benchmark decisions bind the compared files.** Higgsedit protocol v2 freezes one master, clock, frame ranges
  and channel. Runs bind exact input bytes and full comparison coverage; each distinct measured cut needs a
  current review. Replanning preserves history, legacy records remain incomplete, and nonfinite values or rounded
  threshold failures cannot create a winning verdict. See `docs/guides/higgsedit-benchmark.md` for the new-folder
  migration and exact-cut review commands. The funded comparison remains open.
- **Picture timing measures the pre-polish source.** Transcription and onset snapping use the selected polish's
  verified source; cues and mixing keep the polished playback. Explicit source/output hashes and equal sample
  rates/frame counts are checked before cache reuse. A narrow voice selector tracks source choices and stage
  availability while excluding approval and QA bookkeeping. Whisper reuse checks language/model; forced alignment
  checks its exact script text. Inherited gate inputs follow parent edits, child overrides and reference changes.
- **Carry one idea through changing forms.** The shared motion guide explains crowds becoming data, weighted objects
  becoming navigation, distance-paced curved travel and closing objects that retain their history. It combines the
  existing kit; the authored paths, data, artwork and finished-scene proof remain with the film.

- **The approved effects renderer is implemented.** `slate sfx spot|render|run` turns picture events into measured,
  varied cues, plays installed sampled instruments, and balances shared families and hall returns around narration.
  The mix preserves approved cue choices, recalculates unpinned rides, and writes exact solo deltas for review.
  Render logs and sync evidence bind to the current cue sheet and delivered mix. Event-specific choices survive
  re-spotting; write-ons align at their audible start.
- **Physical books join the kit.** `HingedBook` and `PageLeaf` expose caller-owned front and back faces, curling
  strips, cover thickness and continuous shadows. Both raster modes keep reverse artwork registered at landing.
  The craft guides explain how one object can carry evidence across scenes without copying a source film's design.
- **Updates refresh source bytes.** Installed engine code refreshes even when the Remotion pin stays the same.
  Replacement holds the render and AE slots, preserves installed dependencies and vendor skills, and restores the
  old sources if it fails. An interrupted dependency install remains unusable until a successful retry. Update also
  reports relinking and doctor failures instead of claiming success.
- **Renders resume against all their inputs.** Web chunk hashes include the scene HTML, film settings, shared kit,
  engine code and package manifests, so a changed input cannot reuse an old chunk.
- **Takes retain their provenance.** Reusing a shot refuses changed models, request settings or uploaded bytes before
  any vendor submission. Additional takes can extend the same request; a different request needs a new shot id.
- **Variants reuse approved media.** Generation takes and captured media resolve through the parent reference, then
  conform and receive judgments in the child. A variant cannot submit, cancel or refresh the parent's vendor work.
  MCP script reads also resolve inherited sources, and writes respect current storyboard and inherited approvals.
- **Delivery keeps every required gate.** A missing QA or sound module becomes an explicit UNCHECKED blocker. The
  master gate accepts the declared ffmpeg route for social variants in supervised and express modes.
- **Failed eval starts preserve useful evidence.** Inconclusive runs keep their own logs and never replace a recorded
  PASS or FAIL. The sandbox notice now states the same Aside-only browser rule the grader already enforced.
- **Golden inputs can be frozen.** Private snapshots preserve and verify delivered inputs by hash. Four historical
  failures were resolved by recovering the matching delivery revisions, applying the source checker's missing-field
  semantics and handling decimal rounding at the existing tolerance boundary. Expected measurements are unchanged.

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
- **The demo, green again.** The new `end_hold` and type-size checks stopped `slate demo` at QA: the social reel now
  says its own end hold (`end_hold_s`, which a format may set now: 1.5 s, a tenth of the shortest reel, where longer
  films keep the films' 3 s), and the demo's placeholder act, drawn small inside the platforms' safe column, declares
  its text not for reading. The logo sting says its hold beat's 1.5 s the same way, since 3 s is most of a 5-second
  sting. Running the state tests before the job tests no longer leaks a fake brand pack.
- **The ledger, re-mapped.** Every one of the 166 lessons that were gaps now lands somewhere real or waits with its
  planned file: 147 closed, each with its file, symbol and tests; 10 wait for proof 3 and 9 for the tilt wave. The film
  session's work after the second harvest is harvested as an addendum (8 lessons). `slate knowledge gaps --write`
  regenerates `knowledge/GAPS.md` from the ledger, and a test holds the file to it.
- **The kit's tilt raster mode and its stability bench.** An act opts in with one wrapper at its root
  (`<RasterModeProvider value={actRasterMode()}>`): type is drawn under a y-skew too small to see, so headless Chrome
  places it at quarter pixels instead of snapping it to whole ones, and nothing is hinted or kept between frames, so
  slow moves stay smooth and type stays crisp at once. `TypeTilt`, `TiltSpan` and `TiltPlane` put the tilt where it
  belongs; `Card`, the cameras, `PerChar`, `PerWord`, `Arrive`, `KineticWords`, `Field` and `StreamText` (one tilted
  block, each character faded by its colour) handle it themselves; a card whose size morphs lays out at whole even
  sizes (`morphPose`). Hint mode, the default, draws exactly what it drew: the markup bench proves it against the kit at
  HEAD (the whole review sheet, 300 samples in two looks, identical), and its new tilt coverage audit holds a converted
  act to the placement rules without rendering a pixel. The engine kit's stability bench (`engine/remotion/kit/bench`)
  renders through the queue with `slate qa stability conformance --render --detach`; its first run on this Mac is
  queued, and the nine tilt lessons stay pending until it passes.
- **Smaller closings.** A text element's motion trace carries its type role and words, and a gesture's beat its word
  and stressed syllable. Every supervised After Effects step stops on a launch dialog, and the AE bridge's status
  names the dialogs AE is showing. A shot's engine travels from the script through the storyboard into
  `timing/shots.json`. A web act's boundary frames are held to its neighbours' declared rest states, bit for bit.
  `slate analyze` hears through Slate's one Whisper runner. The script schema names the motion law's transition kinds
  (`{kind, note}`, beside the words every script so far uses) and a shot's engine; short formats say their end holds.
  `slate mix` records the brand's sound palette and the effect cue sheet in its own lock entry. A proxy that refuses
  the tunnel fails at once instead of being retried. Measuring a tag round no longer holds the TTS slot. CI names both
  skill validators in steps of their own. A queued job keeps the engine its caller chose (`SLATE_ENGINE_DIR`), and the
  markup bench works through a symlinked data folder.
- **The data-parity proof (P192).** Timing, cue sheets, captions and every mix, rerun on films A, B and C's own
  inputs with the heavy tests on, match what they delivered within the stated tolerances: 49 tests, all passing
  (`docs/PROOFS.md`).

### Live runs (2026-10-01)

- **Renders survive a busy Mac.** Every Remotion render, still and determinism check now waits up to three minutes
  for a page to be ready (`renderTimeoutMs()` in `engine/remotion/browser.mjs`; `SLATE_RENDER_TIMEOUT_MS` overrides
  it), and the kit's font gate holds its first frame as long. Remotion's own 30 s default failed `slate demo` while
  the font gate loaded its faces at load 600. A test holds every render call to it.
- **`slate timing stress <shot> <word>`.** Where a key word's stressed syllable starts (S) and its contact frame
  (C = S + 2), measured from the take's loudness envelope with the films' own `stress_onset.py` maths (parity-tested
  against it), in film seconds on the sync gate's own path. It prints the beat's `stress_start` and `stress_end`, falls
  back to the word's start when the word has no stop (early, never late), and refuses a word the take never says.
  It's in the MCP server's quick stages too.
- **Frame-for-frame comparisons pair frames by their place, not their timestamps.** ffmpeg pairs two videos by time,
  and two containers that round the same frame times differently (Matroska to the millisecond, QuickTime to 1/30000 s)
  matched a third of the frames with their neighbours: identical pictures read 27.98 dB. `slate qa psnr`, the `psnr`
  gate and the Higgsedit benchmark's quality check now renumber both sides on one clock first (`INDEX_CLOCK`), with a
  test on the same frames as MKV and MOV. `slate qa psnr` also takes `--a-frames` and `--b-frames FIRST-LAST`, so a
  shot's render is held against its frames in a whole act without cutting a file.
- **Proofs P193 and P282 are in `docs/PROOFS.md`.** Film A's Act 4 through Slate's engine: all 2,712 frames at 62.91 dB
  or better against the delivered render (193 bit-identical). `HtmlInCanvasMotionBlur` was 12 times faster than
  render_mb and lost on quality (an 8-bit canvas sum), so render_mb stays the default.
- **The font gate holds a page once.** The kit's font gate took its hold on the first frame in a component's state,
  so a render React threw away, or a tree mounted twice, left a hold nothing released: the stability bench's first
  tilt-mode variant waited three minutes on a gate that had passed in every tab. The hold is now the page's, one per
  font plan (`holdFirstFrame`), released by the gate's own result, with tests.
- **`slate job submit --dry-run` queues nothing.** It used to queue the job for real; now it checks the job exactly as
  a submit would (`client.plan`) and prints what it would queue.
- **Eleven Music's stereo stays stereo.** Every live ElevenLabs answer was labelled one channel, so a live look test's
  48 kHz stereo score was written as mono at twice the length, an octave low. A take is now written with the
  channels it came in: the answer's own (a WAV header, a `channels=` parameter), else its format's (Eleven Music stereo,
  speech mono). Recordings keep the count, so a replay writes the live take byte for byte. Tests on a stereo answer, a
  mono read and a replay.
- **A brand edit stales only what reads it.** The script and the timing recorded the whole brand.json, so any pack
  edit, `slate brand approve` included, made every film on the brand stale down to its locked timing. Each now records
  the blocks it reads: the script `brand:<name>#truth` and its duration scale, the timing `brand:<name>#motion.eases`.
  An approval or a look edit leaves both fresh; a new claim, duration or ease still stales what reads it. Old locks
  compare the whole file until their stage runs again.
- **The hand-in runs the motion law.** `slate check` passed an act that `slate qa motion` failed four ways. It now runs
  the motion gate's trace checks on the act's own, current trace (`handin.act<N>.motion`, no pixels: 0.06 s for 300
  moves), so a broken law fails before anything renders; its other gates and output are unchanged.
- **`labels` means one thing to both gates.** The script gates took `"reading": {"labels": true}` (words that name what
  the picture shows, such as a Simulator label) and `slate qa readtime` refused it. Readtime now takes it with the same
  meaning and limits: it answers the 40% voice-share rule only, and the words are still timed in full.
- **`--dry-run` is a dry run, everywhere.** `slate --dry-run qa` ran two minutes of whole-picture analysis outside the
  queue and wrote caches; now it names the gates, the renders and whether it would queue, and runs nothing. An audit of
  every command found about sixty more that ignored the flag: `--detach` dropped it and queued the real, paid job, and
  others spent, sent an iMessage, asked for a key, opened After Effects or wrote records. Each now stops first
  (`slate.cli.dry_stop`), and one test holds them all to it with tripwires on processes, network, messages and the queue.
- **Slate's own starting points keep the motion law.** Once the hand-in ran the law, both acts of the end-to-end test
  failed it at 320 × 180 over 10 frames: the web template clamped its turn to 10 frames, off the brand's scale, and
  dropped its line at 3.2 px/frame² against a UI element's 2.7; the kit fixture's card and dot moved 17.5 and 77.8 px a
  frame against 10. The kit now sizes a move to the law before anything renders: `durFit()` takes the longest step of
  the scale that fits, and `maxTravel()` the longest travel a move can make inside its class's speed limits at the
  film's size, measured as the gate measures a trace (a test holds its `SPEED_LIMITS` to the gate's). The template uses
  both, so a fresh web act passes `handin.act<N>.motion` in any act, and in one with room it draws exactly what it drew
  before (60 frames at 1080p, hash for hash). The fixture's moves are now a tick long, and travel 4 and 17 px.
- **The motion gate reads radians.** A rotation traced in radians (three.js records them, as the web template's turn
  does) was read as degrees, so a whole radian a frame passed the edge-speed limit 57 times over. Traces in radians
  are converted when they're read, with a regression test.

### Still to come in 0.1.0

Waiting for a person: the first live After Effects finish (`slate ae smoke --go` and the rest, on Quinn's go), the
effects engine (after proof 3 is approved) and the agents' live eval runs. Being built: the stability bench's first
pass on this Mac (queued), then acts converted to the kit's tilt raster mode one at a time; the effects engine's
spotting, guards and family stems once proof 3 is approved. `knowledge/GAPS.md` lists each pending lesson with its
planned file.
