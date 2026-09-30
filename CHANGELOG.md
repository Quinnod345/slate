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
- **Jobs and spend.** One machine-wide queue for long work (`slate job`, `slate jobs`), resource slots, disk
  reservations, resumable jobs, and `slate budget` and `slate ledger` with per-film and monthly caps.
- **Evals.** `slate evals`: triggering, routing, negative cases, the dry run and the no-browser check across four agent
  configurations.
- **Packaging.** `slate mcp`, Slate's MCP server over the command line and job queue (long work always becomes a job);
  the desktop extension for the Claude app (`mcpb/`, packed by `slate mcpb build`); the plugin for Claude Code and
  Agent Plugins clients (`plugins/slate`) with its marketplace (`.claude-plugin/marketplace.json`); `slate skills
  build`, which generates the plugin's skill copies, Codex sidecars, OpenClaw entries and knowledge-only zips for
  Claude chats, with `--check` for CI; `slate guide`; the README, AGENTS.md, this changelog, the licence, the decision
  records and CI.

### Still to come in 0.1.0

The skills themselves, brand packs and onboarding, formats, doctor and connect, the finish stages (`slate master`,
`deliver`, `review`), reference analysis, the open web renderer, the knowledge ledger (`slate knowledge check`), the
demo film, `slate update` and `install.sh`.
