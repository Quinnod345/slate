# The production tracker: GOAL.md and PRODUCTION.md

Two files in every film's `brief/` folder keep the goal whole across days, sessions and agents. They're written in the
requester's own words, so nobody has to remember what was asked.

## brief/GOAL.md: the goal

- **The verbatim request**, exactly as it arrived, with who sent it and when.
- **The checklist**: one row per ask, in the requester's words, with its status and evidence; plus the brief's settings
  (format, brand, budget, mode, generation).
- **Later messages**, verbatim, each with what it added or changed.
- The rule at its top: if a conversation is summarized, re-derive the checklist from the verbatim request, not from
  memory.

## brief/PRODUCTION.md: the live tracker

- **Asks**, in the requester's words, with status and evidence.
- **Direction changes**, numbered and quoted exactly, each saying what it supersedes. Follow the latest authorized
  direction within its scope, and record when it supersedes an earlier creative plan. Approval of an artifact still
  binds to the exact approved bytes.
- **Questions waiting for a person**, and their answers.
- **Revisions**: preserve the approved baseline and record each requested change as a checklist, including a redesign.
  Ask only when missing or conflicting information prevents the next step.
- **Approvals**, with who approved what and its SHA-256.
- **Status**: one row per stage from `slate status` (`slate status --write` refreshes it).

## Keeping them current

- Slate keeps the blocks between its `slate:` markers current. Write your own notes around the markers, never inside
  them; anything inside is regenerated from the film's records.
- GOAL.md names the commands that record a later message, mark an ask done (with evidence) and log a direction change.
  Use them rather than editing the tables by hand, so the records and the Markdown agree.
- Record the owner's calls on each script draft (trims, keeps, confirmations) as numbered decisions, with their words.

## Status words mean one thing each

| Word | Meaning |
|---|---|
| **done** | verified, with the evidence named (a gate result, a file, a measurement, the owner's words) |
| **built** | made, but not through its gates yet |
| **in progress** | being worked on now |
| **open** | not started |
| **blocked** | waiting on something, with the reason |

A proof is not a finished shot. A shot is done only when it's built, rendered, through its gates and, where a person
judges it, approved.

## Before you say the film (or a stage) is finished

Re-read the requester's original words in GOAL.md, not your summary and not the checklist alone. Walk every ask: is it
genuinely delivered and verified? Anything partial or untouched isn't done; keep going, or say plainly what's left and
why. Report the result against each ask.

## Other records worth keeping

- **Notes on each act** live with the act (slate-build): decisions, restaging, what couldn't be done.
- **Superseded material** stays in the film's `archive/` with a line saying what it was and what replaced it; a
  delivered version is never overwritten.
- **When a decision changes** (say, where renders run), update or retire every note and doc that states the old rule,
  so nobody follows it later.
