# Decisions

Quinn's decisions, as short records: what was decided, why, and what it means for the code. The twelve founding
decisions were made on 2026-09-30; later decisions follow in order. Decisions 1 to 4 were taken first; 5 to 12 came from his answers to
four concrete questions per decision, and decision 5 was revised the same day after his note on it. `docs/PLAN.md` has
the full plan they belong to.

Company brands are named generically here on purpose: nothing about a company's brand enters this repository.

A new decision gets the next number, a date and the same four parts. A decision that changes gets a new record that
says which one it replaces; old records stay as they were.

---

## ADR-001: The name is Slate

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** the package needed one name for the command, the skills, the plugin and the repository.
- **Decision:** Slate.
- **Consequences:** the command is `slate`, the Python package `slate`, the front-door skill `slate` and the craft
  skills `slate-<craft>`; the plugin and its marketplace are both `slate`.

## ADR-002: After Effects is a full creative engine

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** After Effects finished the films this pipeline came from, and Higgsfield's AE bridge showed it can also
  build scenes. Adobe's enterprise terms bar operations a person didn't start.
- **Decision:** After Effects finishes every film Quinn supervises, as an editable project plus the masters, and he
  starts it. It's also a second way to build scenes, with the bridge's 13 motion-design skills as looks. Autonomous
  films are mastered with ffmpeg and held; he can start their AE finish when he approves one. Building scenes in AE
  needs AE's "Allow Scripts to Write Files and Access Network" preference, which he turned on on 2026-09-30. Slate
  never changes it.
- **Consequences:** every AE step waits for a person's go; the job queue refuses the `ae` resource for autonomous films
  (`slate.jobs.client.submit`), and `slate render --engine ae` needs `--go`. The byte-safe prefs tool refuses to touch
  the scripting preference (`slate.ae.prefs`, `PROTECTED`).

## ADR-003: Oneiro is capped, and every film it makes is held

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** Oneiro, Quinn's autonomous agent, will make films for his own projects without him in the loop.
- **Decision:** reels only for brands Quinn has approved, within a monthly cap: Oneiro's own ElevenLabs key with a
  credit limit, and no generated footage by default. No After Effects. Every film is held until he approves that exact
  file, and nothing posts without his yes. The starting cap is 20,000 ElevenLabs credits a month (about ten reels),
  and every Higgsfield generation waits for his yes. He can change it any time.
- **Consequences:** the ledger holds autonomous films to their monthly caps whatever film.json says, requires an
  approval id for each autonomous Higgsfield call, and routes autonomous ElevenLabs spend through Oneiro's own key
  (`slate.ledger.DEFAULT_BUDGET`).

## ADR-004: Quinn's first, shareable later

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** Slate starts as Quinn's own tool, and some of the brands it will serve are companies'.
- **Decision:** the core lives in a private GitHub repository on his personal account, free of company material and
  secrets. Company brand packs stay on his personal Mac only; the pack for his own app can live in a private
  repository.
- **Consequences:** no brand in the core (tests use a neutral demo brand); real packs live in `~/code/slate-brands`,
  outside this repository; the licence is proprietary for now (`LICENSE`), and the plugin marketplace serves the people
  he shares it with.

## ADR-005: Remotion's licence is free for now

- **Date:** 2026-09-30 (revised the same day after Quinn's note). **Status:** accepted.
- **Context:** Remotion is free for individuals, companies of up to three people, non-profits and evaluation; larger
  companies need a company licence. Its coming v5 terms count the people operating it, contractors included, and say
  receiving finished media isn't use.
- **Decision:** nothing to buy now: Quinn is the only person operating Remotion, and the films are unpublished pilots.
  Revisit when a company's film is about to be published: under today's terms, work for a company of four or more is
  the grey area, so check then. If Slate goes public, most people using it are individuals or small teams, who are
  free; the README tells companies of four or more to get their own licence, or to use the open web renderer or After
  Effects. "A video for" a client means the client's own film, so onboarding names an approver at that client for
  claims and brand use.
- **Consequences:** Slate doesn't depend on Remotion alone: `slate render --engine remotion|web|ae`, where the open web
  renderer (three.js, React Three Fiber, drei, GSAP and puppeteer on local pages) and After Effects need no Remotion
  licence. Remotion stays pinned at 4.0.521 and off 5.0 until a parity check says otherwise.

## ADR-006: Higgsfield's Cloud API is the one lane

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** Higgsfield bills its API and its app credits from separate pools, and Quinn funds only the API.
- **Decision:** Slate generates only through the Cloud API; the app-credit lane stays off. Best quality wins, with the
  best models by default and overridable per film; the recommended agent is Opus 5.5 at max effort, also configurable.
  Generation is rare by default and the requester decides how much: code builds anything designed, and generation is
  for real people, real scenes and stock-style shots code can't make, or where it genuinely adds something. Every take
  is judged, and ugly takes never ship. Oneiro asks for Quinn's yes on each generation.
- **Consequences:** a film's `generation` setting is none, rarely or where-it-helps, checked per shot
  (`slate.gen.policy.allowed`); every generation is estimated, budgeted and ledgered, with at least two takes judged.

## ADR-007: Every brand gets its own sound

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** the films' sound design is tuned to one brand's taste, and it went off course once when research
  replaced an approved design instead of perfecting it.
- **Decision:** Quinn's palette is a starting point; each brand gets its own palette during onboarding, and his own
  app's comes from its own world. A composed score is part of most films. The effects engine waits for his approval of
  proof 3. The first autonomous reel uses the score, the voice and its onboarding sound reference.
- **Consequences:** `slate sfx` refuses until proof 3 is approved (`slate.sound.effects.require_proof3`); scores are
  composed per act on Eleven Music's best model and assembled by measurement.

## ADR-008: Voices

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** voice clones and AI labels carry legal and personal weight.
- **Decision:** Quinn's own voice may be an ElevenLabs professional clone, which he verifies himself. Anyone else only
  as a real recording, never a clone. Oneiro may use his clone. No AI labels unless the law requires them; platform
  rules are his call per platform.
- **Consequences:** the voice profile's cloning policy is enforced in code before any request (`slate.voice.profile`),
  and each film's manifest records which voices and shots are synthetic, for internal use.

## ADR-009: Fast variants, with quality set by the channel

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** one film often needs cut-downs and new aspects for several channels.
- **Decision:** variants sometimes, with quality depending on the channel: hero placements at master quality, social
  cut-downs fast. A second engine is fine if it's faster; Higgsedit gets benchmarked in phase 8 and is kept only if it
  wins. The channels are Instagram and TikTok, YouTube, LinkedIn, the website and the App Store.
- **Consequences:** a cut-down, a new aspect or a new language is a derived film with a parent (`slate variant`),
  reusing the parent's approved takes, timings and assets by reference and running every gate again.

## ADR-010: visionary and goal-fidelity get their own repository

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** two of Quinn's personal skills matter to every film but aren't Slate's.
- **Decision:** one small repository of their own, separate from Slate and shareable, symlinked into every personal
  agent. Claude maintains them. The company's agent stays its own thing, and his other skills stay exactly as they
  are.
- **Consequences:** Slate's skills may name those skills as optional companions but never ship copies of them.

## ADR-011: Higgsfield's own skills stay global

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** Higgsfield's skills also claim "make a video", and Quinn uses them directly sometimes.
- **Decision:** they stay global and get installed for Codex and OpenClaw too. An open "make a video" gets Slate's
  question first: a full production, or a quick generated clip? One yes from Quinn covers these setup changes. Outside
  Slate they need app credits to generate.
- **Consequences:** the front-door skill asks that one question on open requests and goes straight to production on a
  scoped brief; routing evals check it in every agent. Slate never deletes a vendor skill.

## ADR-012: The first autonomous reel

- **Date:** 2026-09-30. **Status:** accepted.
- **Context:** the plan needed a real proof of autonomous mode, on Quinn's own app's brand.
- **Decision:** by Oct 8, on a topic Oneiro chooses from its campaign. Held for Quinn by iMessage with the cut, plus a
  push notification. Oneiro posts the exact file he approves.
- **Consequences:** autonomous mode runs only for brands whose four references are approved, parks the film on any
  REVIEW verdict, marks every output "not approved for publishing" until that exact SHA-256 is approved, and masters
  with ffmpeg.

## ADR-013: Port the approved sound references

- **Date:** 2026-10-04. **Status:** accepted.
- **Context:** the effects renderer deliberately waited for approval of proof 3 and the full p3 mix. Both exact
  reference files were presented in the completion chat, with independent copies and SHA-256 receipts.
- **Decision:** Quinn approved both sound references: “Yeah I like the sound files a lot. I approve that definitely
  for the sound decisions.” The approved proof is the v2 balance revision. The full p3 mix is approved alongside it.
- **Consequences:** re-inventory and port the approved effects design now, with measured regression evidence.
  Preserve the real sampled instruments, timing, balance and approved-work rules. The runtime proof-3 record carries
  the exact proof hash and the full mix's hash; the private review receipt retains both artifacts. This decision
  approves sound references only. Brand onboarding, live AE acceptance and publication keep their own decisions.

## ADR-014: Use the latest B redesign as an additional craft reference

- **Date:** 2026-10-04. **Status:** accepted.
- **Context:** Quinn added B's latest redesigned sound and its Remotion craft to the approved basis, highlighting the
  passport ending. A private snapshot binds the latest picture, polished-voice mix, cue choices and balance records.
- **Decision:** preserve the new mix's cue design, write-on onset alignment and composed-score practice. An approved
  cue can stay fixed while unpinned rides are recalculated around revised narration. Carry the physical book's
  front/back registration, curling leaves and continuous shadows into reusable kit components. Carry its story
  principle into the craft guidance: one meaningful object can accumulate evidence and connect several perspectives.
- **Consequences:** keep each reference's exact provenance and approval scope. B supplements the approved proof; it
  does not replace its record. The kit exposes caller-owned faces and brand tokens, not the source film's artwork,
  scenes, camera path or copy. Review both raster modes in a neutral motion proof. Approved spans never relocate
  silently; moving one needs an explicit revision and renewed approval.

## ADR-015: Learn from iterations and keep creative restrictions contextual

- **Date:** 2026-10-05. **Status:** accepted.
- **Context:** Quinn said that too many rules were limiting Slate's creative output. A final reference loses the
  process that made it good: what he requested, what changed, what regressed, and why he preferred one version.
  Earlier sound decisions already described the palette as a starting point per brand (ADR-007).
- **Decision:** preserve the full available source history, including rewinds, branches and answered questions.
  Derive scoped guidance with source evidence and counterexamples. Keep it in private data outside the checkout and
  make it available through `slate history context`, including the MCP read surface. A new brief and newer feedback
  take priority. A requested redesign becomes work without asking the same creative question again. Quiet or gentle
  voice delivery is allowed unless a particular voice's explicit policy restricts it. Motion and sound presets
  guide exploration; they do not define every future brand's style.
- **Consequences:** revision starts and finishes append notes with baseline and result hashes to the film's creative
  history. Drafts can record feedback before they have an approval. An unavailable history never blocks production.
  Preview approval, a preferred draft and a measured result remain distinct from approval of a delivered file.
  This supersedes blanket creative bans and the interpretation of ADR-013/014 that prohibited a requested change
  of approach; it preserves their source approvals. Exact script words, source truth, secret handling, cloning,
  budget accounting, publication approval, After Effects controls and honest technical checks remain in force.
  An unchanged approved brand voice answers the casting choice in every mode when the film profile and synthesis
  pins match it. Reusing that cast approval never approves new narration, spending, a script or a delivered file.
  Explicit sound material and banned-timbre lists replace the starter lists; measured timbre limits are configurable
  per palette. Detector evidence remains visible even when a chosen timbre is permitted.

## ADR-016: Serve public file leaves as regular bytes while keeping large media in place

- **Date:** 2026-10-06. **Status:** accepted.
- **Context:** the bundle linked every top-level public entry through a generated folder. Remotion 4.0.521's static
  handler uses `lstat` and refuses a final-file symlink, so an ordinary public logo and nested cached font or image
  links could produce HTTP 404 despite existing on disk. The earlier test established link existence rather than
  actual serving. Copying capture and plate trees would restore the disk duplication this design originally avoided.
- **Decision:** prepare a bundle-owned public view. Copy regular top-level public files and final-file link targets
  as exact regular bytes. Inspect directory-link targets and overlay subtrees containing file links; retain clean
  directory subtrees as aliases. Captures and plates remain opaque directory aliases, read in place. Preserve the
  film's public sources and unmanaged legacy `.slate/public` entries, including their collision precedence.
- **Consequences:** bump the bundle format and include copied-leaf bytes, source and resolved target identities, and
  effective alias structure in the cache hash. Rebuilding replaces only the owned view, and existing temporary,
  lease and prune ownership rules govern its cleanup. The HTTP regressions exercise the pinned handler against
  ordinary files, nested links and actual bundle output. Regular capture and plate leaves retain their existing
  serving path; final-file symlinks within those opaque media trees are not claimed as supported by this change.
