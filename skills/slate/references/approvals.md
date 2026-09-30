# Approvals: sittings, packets and the owner's yes

People judge what machines can't: the look, the voice, the sound, the hero moments and the final cut. Slate's job is to
put the right thing in front of them at the right time, record their decision against the exact bytes they judged, and
never mistake anything else for a yes.

## The two sittings (they block)

| Sitting | What it approves | What you prepare |
|---|---|---|
| **Pre-production** | the checklist of asks, the claims, the script, the voice shortlist, and the look test when the brand's look isn't approved yet | GOAL.md with every ask; the truth pack and the claims list; SCRIPT.md; a casting reel (slate-voice); a moving 10 to 15 second look test with sound (slate-motion) |
| **Final cut** | the film itself (in express mode, everything above too) | the review packet (`slate review`): the phone cut, the contact sheet, what changed, open questions; every gate's status |

At a sitting: finish the packet, say plainly what's waiting for them and why, and stop. Don't start the next stage
"to save time"; it will be rebuilt if they change their mind.

## Packets (they don't block)

Hero moments, generated takes, sound proofs, voice takes and score takes go into review packets the owner answers when
they like. Keep working on everything that doesn't depend on them. When they answer, record it.

## What an approval is

- **Bound to exact bytes.** Record each approval with the command `slate status` names for that stage. It stores the
  SHA-256 of the file approved, who approved it and their words. Change the file and it's no longer approved.
- **Kinds Slate records:** checklist, claims, script, voice shortlist, look test, final cut (the sittings); hero,
  plate, sound proof, take, score take (packets); and spend (a yes to spend again on something already paid for).
- **Locks follow.** Approved takes, score takes, plates and the final cut are locked by content hash. Regenerating one
  needs an explicit force and a recorded spend approval.
- **Only a person approves.** Never record an approval on someone's behalf, never read approval into silence or a
  thumbs-up on something else, and never treat a REVIEW gate as a pass.
- **By ear and by eye.** Voices, sound and looks are chosen by the owner listening and watching. Measurements shortlist
  and catch faults; they never decide.

## Human approval points across a production

1. The goal and its checklist, in the requester's words.
2. The source of truth for product claims, and later corrections to claims.
3. The script.
4. The production route, when there's a real choice (code, After Effects, generation).
5. The voice, by ear, from a reel or a proof.
6. The sound palette and sound proofs, by ear.
7. The look, from a moving test (stills don't count), grounded in the brand.
8. Hero moments, against the visionary bar.
9. Generated takes, by eye.
10. Spend beyond a balance or a budget.
11. The scope of a fidelity pass: ask "is this the pressing stuff?" before widening one, and record the ruling.
12. The final cut, and each re-delivery after corrections.

An autonomy grant ("don't ping me, just decide") covers creative decisions. It never covers purchases, upgrades,
licences or publishing.

## Asking for a decision

- Name the decision in one short line.
- Ask a few concrete sub-questions whose answers settle it: facts about their situation, what they'd do in practice,
  the edge case. Don't ask the decision itself in the abstract.
- If your agent has an interactive question tool, use it, one screen per decision.
- Afterwards, say in a few lines which way the answers point, flag any conflict between them and how you'll resolve it,
  and invite a correction. Read free-text answers literally; they often carry the real intent.

## Approved work stays approved

- **Work the owner approved is not rebuilt.** Build around it. When something next to it changes, keep its outer
  geometry and timing so it still fits.
- **Perfect, don't replace.** Notes on approved work are perfecting notes. Write them as a checklist of changes to that
  exact artifact, keep everything else as it was, and check the revision against the approved version by measurement.
  If a note seems to need a new approach, ask first. Research only to fix a specific named flaw, never to redesign.
- **How it goes wrong:** notes on an approved sound (a tonal layer that sounded cheap, glides that read as a siren, more
  variation wanted) were answered by replacing the design: a new composed layer, a researched vocabulary, and effects
  pushed from about 11 to 17 LU under the voice down to 25 to 28 LU under it, where they stopped standing out. The owner
  had to point back at the approved proof.
- **A latent bug in approved work** that produced the look they approved stays: record it rather than fix it and change
  approved frames.
- **Retiring a direction:** delete nothing, stop every watcher, leave the files in place, and write a short handover
  listing only the plumbing worth reusing.
