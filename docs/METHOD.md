# The production method

How a Slate film is made, from a request to a delivered, approved film. It's the method the first three films worked
out, generalized: goals kept in the requester's own words, truth packs, look tests, kit-first foundations, parallel act
builders with frame-exact contracts, and review cuts at every stage. Each stage has a skill that knows its craft, a
command that does its work, a person who approves what only a person can judge, and a machine gate that checks the rest.

People judge the look, the voice, the sound, the hero moments and the final cut. Machines check everything that can be
measured. Nothing unchecked is ever reported as passed.

## The stages

| # | Stage | Who approves | Machine gate | Commands | Skill |
|---|---|---|---|---|---|
| 0 | Brand onboarding, once per brand | the owner (or the brand's approver) approves the four references: truth, look and motion, voice, sound | references recorded with their SHA-256 | `slate brand init`, `slate brand onboard`, `slate analyze`, `slate brand approve` | [slate-brand](../skills/slate-brand/SKILL.md) |
| 1 | Brief: the request verbatim, then the checklist, format, brand and budget | pre-production sitting | every ask captured | `slate new` | [slate](../skills/slate/SKILL.md) |
| 2 | Truth: pinned sources, research, sample data | pre-production sitting (claims) | every claim on the approved list or sourced | `brief/truth.json`, `slate script build` | [slate-story](../skills/slate-story/SKILL.md) |
| 3 | Treatment and script (`script.json` to `storyboard.json`) | pre-production sitting | 2.6 words a second or less, read time, banned words, runtime within 10%, no unsourced numbers | `slate script build` | [slate-story](../skills/slate-story/SKILL.md) |
| 4 | Voice: cast or design, then voice every line | pre-production sitting (the shortlist) | Whisper hears every word and the brand words; no leaked tails; each read fits its shot | `slate voice cast`, `slate voice design`, `slate voice tts` | [slate-voice](../skills/slate-voice/SKILL.md) |
| 5 | Timing: shots from the real reads, words, events | none | frame-exact and contiguous | `slate script build --from-takes`, `slate timing` | [slate-story](../skills/slate-story/SKILL.md) |
| 6 | Look test (skipped when the brand's look is approved) | pre-production sitting | none: a person judges a moving test | `slate render` of the test | [slate-motion](../skills/slate-motion/SKILL.md) |
| 7 | Kit, builders' brief, act builds in Remotion or After Effects (in parallel where the agent can); captures | hero moments, in the packet | type check, an empty word-miss list, bit-identical contracts, every act's events exported | `slate emit-events`, `slate check`, `slate capture ios`, `slate capture web` | [slate-build](../skills/slate-build/SKILL.md) |
| 8 | Generated shots (only when the film's generation setting allows, and code can't do the job) | takes, in the packet | estimate first; at least two takes judged; ugly or AI-looking takes never ship | `slate plates plan`, `slate gen run`, `slate gen judge`, `slate plates conform` | [slate-generate](../skills/slate-generate/SKILL.md) |
| 9 | Score, sound and mix | sound proofs, in the packet | −14 LUFS within 0.5, at or under −1 dBTP, stems sum, cues within a frame, no repeats, no unplanned music gaps, levels against the brand's sound reference | `slate score plan`, `slate score assemble`, `slate mix`, `slate verify` | [slate-sound](../skills/slate-sound/SKILL.md) |
| 10 | Render with true motion blur | none | frame counts, glitch scan triaged, act joins, motion checks | `slate render`, `slate qa` | [slate-finish](../skills/slate-finish/SKILL.md) |
| 11 | Master: After Effects for supervised films; ffmpeg for autonomous and draft films | the owner starts the AE finish | master against render PSNR of 40 dB or more on every frame, exact frame counts, the AE project audit | `slate master --ae --go`, `slate master --ffmpeg` | [slate-finish](../skills/slate-finish/SKILL.md) |
| 12 | Validate and deliver | final-cut sitting | every film gate passes; the manifest is written last | `slate qa`, `slate deliver` | [slate-qa](../skills/slate-qa/SKILL.md) |
| 13 | Review and correct | the owner's notes | the same gates, re-run on what changed | `slate review` | [slate-finish](../skills/slate-finish/SKILL.md) |

`slate status` shows where a film stands against this table, stage by stage, and names the one next command. `slate make
<target>` rebuilds only what's stale, in order.

### Sittings and packets

Two sittings block a supervised film: the **pre-production sitting** (the checklist, the claims, the script, the voice
shortlist and the look test) and the **final-cut sitting**. Hero moments, generated takes and sound proofs arrive in
review packets the owner answers when they like; they don't block. Every approval is recorded against the SHA-256 of
exactly what was approved.

### Building in parallel

If you can run sub-agents, act builders work in parallel, one per act, under one layered brief, each owning only its
act's files, with frame-exact contracts at every act boundary and a first working version of every shot on disk fast;
otherwise the acts are built in order, the same way. Finals and sound are done centrally, one at a time.

## Review and correct

`slate review` builds a packet for each version: a phone cut with burned-in timecode and shot id, a 0.5-second contact
sheet, a list of what changed since the last version, and the open questions. It goes wherever the owner watches: their
Downloads for their own productions, and for an autonomous agent's films, an iMessage with the phone cut plus a push
notification.

Notes are logged verbatim as numbered directions, and each timecode is mapped to its shot, beat and stage. Fixes are
targeted: re-voice only the changed lines with their neighbours as context, re-render the affected range and splice it
with proof, re-mix, re-run the gates. Versions are numbered, each approval records the SHA-256 of what was approved, and
a delivered version is never overwritten.

## Perfect, don't replace

When the owner approves something and adds notes, the notes are a checklist of changes to that exact artifact.
Everything else stays as it was, and the revision is checked against the approved version by measurement: levels
relative to the voice, sync, palette. A note that seems to need a new approach gets a question first. Research only
fixes a named flaw; it never becomes a redesign.

This rule exists because the first films' sound went off the rails exactly this way: an approved sound design came back
with small notes (a tonal layer that sounded cheap, glides that read as a siren, more variation wanted), and they were
answered with a new vocabulary and a new layer instead. The effects that had stood out, about 11 to 17 LU under the
voice, ended up 25 to 28 LU under it, and the owner had to point back at the approved proof.

## Resilience

A production can run for days and outlive the agents working on it.
- Everything a film knows is in its folder, so `slate status` tells any agent, in any session, where it stands and
  what's next.
- Long work runs as detached jobs in the queue (`--detach`, then `slate job status <id>`), under caffeinate, resumable
  by frame, line or act. A dying agent, a sleeping Mac or a rate limit doesn't lose it; during the first films one rate
  limit stopped eight agents at once.
- Nothing prompts: research slices run with stdin as a pipe and are killed after ten minutes, so every long step is a job
  they submit and poll, and keys are set by a person in their own Terminal.
- Builders get a first working version of every shot onto disk fast and keep their act's NOTES.md current.
- Each job reserves disk space first and stops safely before the disk fills.
- The goal survives summaries: after any compaction, the checklist is re-derived from the verbatim request in
  `brief/GOAL.md`, never from memory.

## The four modes

- **Supervised** (the default): the two sittings, with spend past the film's budget asking first.
- **Express**: a short film runs straight to a review cut, with every approval batched into the final-cut sitting.
- **Draft**: no plates, a library voice, fewer motion-blur samples. For animatics and quick variants. Never delivered as
  final.
- **Autonomous** (agents working for the owner):
  - only for brands whose four references are approved, and only claims from the approved-claims list; a new claim parks
    the film with a question for the owner;
  - REVIEW verdicts park the film with their evidence and never count as passes;
  - spend is capped monthly in Slate's own budget file, which the CLI enforces whatever film.json says, and backed by a
    separate vendor key with its own quota;
  - every generation waits for the owner's yes;
  - no After Effects (the owner can start the AE finish when they approve a held film); the owner's own professional
    voice clone is the only clone it may use;
  - default output is a 15 to 45 second 9:16 reel;
  - before parking, a separate reviewer agent grades the contact sheet and the audio against the brand's references,
    the owner's taste notes and the visionary bar;
  - every output is marked "not approved for publishing" in its manifest until the owner approves that exact SHA-256;
    after their yes, the agent posts that exact file through its normal committing path, and never any other version;
  - held films and questions reach the owner by iMessage, with the phone cut, and as a push notification.

## Protections

- **Voices**: designed or library voices by default. The owner's own voice can be a professional clone, which the owner
  verifies with the vendor themselves. Anyone else's voice only as a real recording, never a clone, and never a public
  figure's. Autonomous agents may use the owner's clone; their films are still held.
- **People and claims**: no invented testimonials or customer quotes presented as real, which is also the law, and no
  claim without a source.
- **AI labels**: none unless the law requires one. Slate still records which voices and shots are synthetic in each
  film's manifest (internal only), so it's clear when a law does apply. Platforms have their own rules on realistic AI
  voices and people; whether to follow them is the owner's call per platform.
- **Company material**: company source code is read in place, never copied. Brand material goes to generators only when
  the brand's owner allows it. Company brand packs never enter Slate's repository.
- **Spend and uploads**: Slate never buys credits, upgrades a plan or starts a licence, and never calls a vendor tool that
  uploads a user's skills or builds a personality profile.
- **Browsers**: nothing opens a web browser; browsing and web capture go through Aside only, and every child process runs
  with `BROWSER=none`.
- **After Effects**: AE steps start only on a person's go; AE's preferences are changed only by Slate's byte-safe tool,
  never the scripting-security preference; nobody quits AE for the owner.
- **Machines**: renders run locally, one heavy job at a time, never on another machine.

## Where each part lives

The skills carry the craft (`skills/`), the CLI does the work (`slate --help`), the film folder holds the state, and the
brand pack holds everything brand-specific. `slate guide <topic>` prints any skill reference file.
