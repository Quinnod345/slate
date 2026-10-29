---
name: slate
description: Use for any request to make, edit, plan or review a video. Open asks get one question (full production or a quick generated clip?). Not for screen-recording an app (video-verifier), styling or embedding a video element. Slate's front door for video work, including explainers, ads, social reels, promos, launch and product videos, brand films, teasers, logo stings, kinetic type, character-led pieces, cut-downs or new aspects of a film, and delivering or posting a finished film (posting needs the owner's approval of that file). An open request is one like "make a video"; a scoped brief goes straight to production. Also for picking up a film in progress. Screen-recording an app to check its UI or motion is video-verifier's work, and a single still image isn't Slate's.
---

# Slate

Slate is a film studio any agent can run. Every stage of a film is a `slate` command, every check is a gate, and
everything a film knows lives in its own folder, so any agent in any session can pick up exactly where the last one
stopped. This skill is the front door: it routes the request, runs intake, keeps the goal whole and hands each stage to
its craft skill.

Two words used throughout:
- **The owner** is the person Slate works for. They approve spend, the two sittings, the look, and anything published.
- **The requester** is whoever asked for this film: often the owner, sometimes someone they work with, sometimes an
  autonomous agent working for them.

## 1. Route the request before you run anything

| The request | What you do |
|---|---|
| **Open**: "make a video", "a short video of a paper boat in a storm", no length, format or brand named ("short" isn't a length), and no lane picked | Ask exactly one question, alone in its message: *"Do you want a full production or a quick generated clip?"* Run nothing and write nothing until they answer. Brand, length and format come after. |
| **Scoped**: a length, a format, a brand or a destination is named ("a 45-second launch video for …", "a 15-second 9:16 reel for …'s spring sale, captions burned in") | Straight to production (section 3). Don't ask the lane question. |
| **The quick clip, picked** (at your question, or up front: "a quick generated clip, not a full production") and no brand involved | The fast lane: hand over to Higgsfield's own skills (higgsfield-generate for a clip, higgsfield-video-explainer for a narrated explainer). No `slate new`. Mind the credits (below). |
| **Brand work**, even when it asks for "a quick Higgsfield clip" | Never the fast lane. It's a Slate production (a short express film suits it), and any generated shot runs through `slate gen` on the Cloud API: estimated, ledgered and judged against the brand's references. Say why in one line. |
| **Recording an app** to check its UI, transitions or motion (screen recordings, frame-by-frame QA, jank, flicker) | Not Slate: use video-verifier. Its web path goes through Aside, never another browser. |
| **Not a film**: web work that mentions video (a `<video>` tag, a hero video's CSS), an email, a single image | Not Slate. Do the task as the ordinary job it is. |
| **Post or publish a film** | Only a file the owner approved: its SHA-256 matches their approval and the delivery manifest marks it approved for publishing. Otherwise say which approval is missing, and stop. Slate never posts anything itself. |
| **Live action to shoot, or audio description** | Out of scope in this version. Say so, and offer what Slate can make instead. |

**Credits.** Higgsfield's skills stay installed globally because the owner uses them directly, but outside Slate they
generate on Higgsfield's app credits, a separate pool from the Cloud API balance, and that pool isn't funded by
default. If the fast lane stops for credits, say so plainly and offer a Slate production instead. Inside Slate every
generation runs on the Cloud API. Details and edge cases: [routing](references/routing.md).

## 2. Every working session starts the same way

1. **`slate status` first.** In a film folder (or with `--film <id>`) it lists every stage as fresh, stale, missing,
   locked or skipped, the sittings, open questions, locks and the one next command. Outside a film it lists the films
   on this machine. Trust it over your memory and over any summary.
2. **Snapshot the balances**, with one label for the whole session: `slate ledger snapshot --all --session <label>`.
   At the end, run the same `slate ledger snapshot --all --session <label>` again, then
   `slate ledger reconcile --session <label>`, so spend made through the chat connectors is caught. Slate can't read
   Higgsfield's balance itself: if a Higgsfield connector or its skills ran in the session, record that balance at
   both ends too, `slate ledger snapshot --provider higgsfield --balance <n> --session <label>`, with the number from
   the connector or the dashboard.
3. **Read `brief/GOAL.md` and `brief/PRODUCTION.md`.** After any summary or compaction, re-derive the checklist from the
   verbatim request in GOAL.md, never from memory, and assume something was lost until you've checked.
4. **Read selected creative history**, when status points to it: `slate history context`. Preserve each pattern's
   scope and counterexamples. Missing history does not block work. See [creative history](references/creative-history.md).
5. **Do the next thing** `slate status` names, with the skill for that stage (section 4).

## 3. Starting a film: intake

Goal fidelity comes first. The dropped requirement is never the headline; it's the aside, the "and also", the second
message.

1. **Capture every ask** in the requester's own words: split on every "and", list, comma and follow-up. Vague asks
   become intent items rather than disappearing. Restate the list back if anything is ambiguous.
2. **Check the brand**: `slate brand validate <brand>`.
   - No pack for the brand: onboarding comes first, before any script (`slate brand init <brand>`, then the
     slate-brand skill). A film looks right only when its look is built from the brand's own design system and a
     motion law measured from reference films; generic looks were rejected four times on the first film.
   - A pack whose four references (truth, look and motion, voice, sound) aren't all approved: finish onboarding with
     `slate brand onboard <brand>` before the script. The neutral `demo` pack is the exception: it exists for trials,
     tests and demos, and its look test rides in the film's pre-production sitting.
   - Autonomous films refuse any brand without all four references approved.
3. **Choose the format** from Slate's templates (reel, ad, explainer, brand film and the rest: [formats](references/formats.md);
   each is a JSON file in Slate's `formats/` folder, named for the format). The format sets runtime, aspect, pace,
   structure, loudness, captions and safe zones.
4. **Settle mode, generation and budget** ([modes](references/modes.md), [budgets](references/budgets.md)): supervised
   unless the requester says otherwise; generation `rarely` unless they ask for more or none.
5. **Make the film**, with the request verbatim:
   ```
   slate new <film-id> --brand <brand> --format <format> --request "<the request, word for word>" --source "<who asked>"
   ```
   Add `--mode`, `--generation`, `--runtime` or `--aspect` when the brief sets them; `--request-file` takes a long
   request from a file. This writes film.json (clock, size, budget, mode), `brief/GOAL.md` (the request and a checklist
   of every ask) and `brief/PRODUCTION.md`, and starts the film's git history.
6. **Record later messages** the same way, verbatim. GOAL.md names the commands that add a message and mark an ask
   done; done always needs evidence.
7. **Go as far as asked.** When the requester says how far to take it ("as far as the script"), stop there and end by
   naming the next step in so many words: for a supervised film it's the pre-production sitting, where the owner
   approves the checklist, the claims, the script and the look before anything is voiced or rendered.

Intake in depth: [intake](references/intake.md).

## 4. The production, stage by stage

| # | Stage | Skill | Commands | Who approves |
|---|---|---|---|---|
| 0 | Brand onboarding (once per brand) | slate-brand | `slate brand init`, `slate brand onboard`, `slate analyze`, `slate brand approve` | the owner approves four references |
| 1 | Brief | slate (this) | `slate new` | pre-production sitting |
| 2 | Truth: sources, claims, sample data | slate-story | `brief/truth.json`, `slate script build` | pre-production sitting (claims) |
| 3 | Treatment and script | slate-story | `script/script.json`, `slate script build` | pre-production sitting |
| 4 | Voice: cast or design, then every line | slate-voice | `slate voice cast`, `slate voice design`, `slate voice tts` | shortlist at the sitting |
| 5 | Timing | slate-story | `slate timing` | none: frame-exact or nothing is written |
| 6 | Look test (skipped when the brand's look is approved) | slate-motion, slate-brand | `slate render` of a short test | pre-production sitting |
| 7 | Kit, builders' brief, acts, captures | slate-build | `slate emit-events`, `slate check`, `slate capture ios` | hero moments, in the packet |
| 8 | Generated shots (only where allowed) | slate-generate | `slate plates plan`, `slate gen run` | takes, in the packet |
| 9 | Score, sound and mix | slate-sound | `slate score plan`, `slate mix`, `slate verify` | sound proofs, in the packet |
| 10 | Render with true motion blur | slate-finish | `slate render` | none: gates |
| 11 | Master | slate-finish | `slate master --ae` (supervised, on the owner's go) or `slate master --ffmpeg` | the owner starts the AE finish |
| 12 | Validate and deliver | slate-qa, slate-finish | `slate qa`, `slate deliver` | final-cut sitting |
| 13 | Review and correct | slate-finish, slate-qa | `slate review`, the same gates again | the owner's notes |

`slate make <target>` rebuilds only what's stale, in order. The method behind the table, with every machine gate, is in
[orchestration](references/orchestration.md); the stages are one clock, so a re-voice re-times everything downstream.

**Parallel work.** If you can run sub-agents, build acts in parallel, one builder per act under one brief; otherwise
build them in order. Either way a builder owns only its act's files, gets a first working version of every shot onto
disk fast, and keeps its NOTES.md current (slate-build).

## 5. Sittings, packets and approvals

- **Two sittings block a supervised film.** The pre-production sitting approves the checklist, the claims, the script,
  the voice shortlist and the look test. The final-cut sitting approves the film. Stop and hand over at each one:
  finish the packet, say plainly what's waiting for them, and don't start the next stage.
- **Packets don't block.** Hero moments, generated takes and sound proofs arrive in review packets the owner answers
  when they like; keep working on everything that doesn't depend on them.
- **An approval is bound to exact bytes.** Record each one with the command `slate status` names for it: it stores
  the SHA-256 of what was approved, so a changed file is no longer approved. Approved takes, score takes, plates and the
  final cut are locked by hash and never regenerated without an explicit force and a spend approval.
- **Only a person approves.** Never record an approval on anyone's behalf, never read approval into silence, and never
  treat a REVIEW verdict as a pass. Voices, sound and looks are picked by ear and eye; your measurements only shortlist.
- **Asking for a decision:** name it in one line, then ask a few concrete sub-questions whose answers settle it (their
  situation, their preference in practice, the edge case). If your agent has an interactive question tool, use it.
  Then say which way the answers point, and read free-text answers literally.

More: [approvals](references/approvals.md).

## 6. Budgets and spend

- Every paid call goes through Slate's ledger: estimate, budget check, an in-flight row with its request id, then the
  result. `slate budget show` has the month and each film's budget; `slate ledger list` has every paid call.
- A film's budget is a hard stop. 1.5 times is allowed only for marked rework. Spending past it needs the owner's
  recorded yes, and an autonomous film can never raise its own.
- **Slate never buys credits, upgrades a plan or starts a licence.** When a balance runs out, the work stops and asks.
- An optional extra that costs real credits (a dedicated score swell is about 1,100 to 1,500) is offered with its price
  and made only on the owner's yes, even when the budget would cover it.
- Replay is the default for tests and demos (`SLATE_FAKE_VENDORS=1`); `--live` spends for real.

Default budgets by format: [budgets](references/budgets.md).

## 7. Modes

- **Supervised** (the default): both sittings block; spend past the film's budget asks first; After Effects finishes
  it on the owner's go.
- **Express**: a short film runs straight to a review cut, and every approval is batched into the final-cut sitting.
- **Draft**: no generated plates, a library voice, fewer motion-blur samples. For animatics and quick variants. Never
  delivered as final.
- **Autonomous** (agents working for the owner): approved brands and approved claims only, every generation waits for
  the owner's yes, no After Effects, a separate reviewer grades the cut before it parks, and every output stays "not
  approved for publishing" until the owner approves that exact file.

The full rules: [modes](references/modes.md).

## 8. The tracker

`brief/GOAL.md` holds the request verbatim and the checklist of every ask; `brief/PRODUCTION.md` holds the asks,
every direction change (numbered and quoted exactly), open questions, revisions, approvals and the status table. Slate
keeps the blocks between its markers current; write anything else around them. Status words mean one thing each:
**done** is verified with named evidence; a proof is not a finished shot. `slate status --write` refreshes the table.
See [tracker](references/tracker.md).

## 9. Preserve the baseline and follow the feedback

When the owner approves something and adds notes, preserve the approved version and apply the requested changes.
Keep unrelated work stable and compare the revision by measurement and listening. If they ask for a redesign or
research, do that within the authorized scope; do not ask the same creative question again. Ask only for information
that is missing. `slate revise start <file> --notes … --by <name>` snapshots the baseline and records every note,
`slate revise check` compares it, and `slate revise done` records the result without inventing renewed approval.
Draft feedback can be recorded with `slate history note` before anything is approved. Preserve the unsuccessful
attempts as well as the preferred ones: [creative history](references/creative-history.md).
See [review and correct](references/review-and-correct.md).

## 10. Resilience

A production can run for days and outlive every agent that works on it.
- Everything a film knows is in its folder; `slate status` tells any agent where it stands.
- Long work (renders, masters, word timings, score composition, generation batches) runs as detached jobs in the
  queue: `--detach` returns a job id at once; follow it with `slate job status <id>`, `slate job wait <id>` and
  `slate job log <id>`. A dying agent, a sleeping Mac or a rate limit doesn't lose it: a job turned away by a vendor's
  rate limit (exit 75) goes back in the queue and tries again after the vendor's own wait.
- **Nothing prompts.** Research slices are killed after ten minutes with stdin as a pipe, so never start anything that
  waits for input. Keys are typed by a person in their own Terminal with `slate connect`, never in chat.
- **Inside the OpenClaw gateway, every paid stage is a job.** The gateway's egress proxy blocks the vendors, and the
  job runner works outside it: run voicing, music and generation with `--detach` or `slate job submit`, never inline
  ([agents and connectors](references/agents-and-connectors.md)).
- Save early and often: a session can end without warning.

See [resilience](references/resilience.md).

## 11. Protections

- **Voices:** designed or library voices by default; the owner's own voice only as a professional clone they verified
  themselves; anyone else's only as a real recording, never a clone, and never a public figure's.
- **People and claims:** no invented testimonials or customer quotes presented as real, and no claim without a source.
- **AI labels:** none unless the law requires one. Slate still records which voices and shots are synthetic, internally,
  and platform rules on realistic AI voices or people are the owner's call per platform.
- **Company material:** company source code is read in place and never copied; brand material goes to a generator only
  when the brand owner allows it.
- **Spend:** never buy, never upgrade, never call a vendor's tool that uploads a user's skills or profile.
- **Browsers:** never open a web browser. Read the web with fetch and search tools; any page that must be driven goes
  through Aside.

The why behind each: [protections](references/protections.md).

## 12. Helpers, vendor skills, licences

- **Optional helpers:** ponder as a QA loop over every shot; visionary for hero moments; goal-fidelity for the
  checklist; asset-creator (vector drawing), canvas-design (frames), frontend-design (component looks) and
  avoid-ai-writing (script polish). None is required; Slate works without them.
- **Vendor skills:** Remotion's and ElevenLabs' skills live inside Slate's engine folder, not globally, and Slate's
  rules override theirs where they disagree. Higgsfield's stay global. Slate never deletes a vendor skill; if one keeps
  winning a request Slate should get, adjust Slate's own trigger with the owner's one-time permission.
- **Code you never copy:** OpenMontage is AGPL, and copying its code would force that licence on Slate. Ideas only.
  Licences in depth: [licences](references/licences.md).

## Reference files

- [routing](references/routing.md): every routing case, the one question, the fast lane and its credits.
- [intake](references/intake.md): capturing the brief, re-deriving the checklist, the brand check.
- [formats](references/formats.md): the format templates and derived films.
- [modes](references/modes.md): supervised, express, draft and autonomous, rule by rule.
- [approvals](references/approvals.md): sittings, packets, what counts as approval, asking for decisions.
- [budgets](references/budgets.md): default budgets, the ledger, never buying.
- [tracker](references/tracker.md): GOAL.md, PRODUCTION.md, status words, direction changes.
- [orchestration](references/orchestration.md): the method as a workflow, parallel builders, production routes.
- [review-and-correct](references/review-and-correct.md): review packets, notes, targeted fixes, versions.
- [resilience](references/resilience.md): detached jobs, sessions that end, files agents read.
- [protections](references/protections.md): voices, claims, labels, company material, spend, browsers.
- [agents-and-connectors](references/agents-and-connectors.md): the agents, the CLI, MCP, the chat lane.
- [dependencies](references/dependencies.md): tools, vendor skills, optional helpers, checking a tool first.
- [licences](references/licences.md): Remotion, Adobe, Eleven Music, ElevenLabs plans, AGPL code, fonts, sounds.
- [research](references/research.md): reading vendor docs, dated sources, the web without a browser.

`slate guide <topic>` prints these same files for an agent that can't read the skill folder.
