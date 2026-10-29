# Review and correct

Every version gets a review packet, every note gets logged verbatim, and every fix is targeted: change what the note is
about, prove nothing else moved, and re-run the same gates.

## The review packet

`slate review` builds a packet for the current version:
- a phone cut with burned-in timecode and shot id, so a note like "at 1:28" maps to a shot;
- a 0.5-second contact sheet of the whole film;
- the list of what changed since the last version;
- the open questions.

It goes wherever the owner watches: their Downloads for their own productions; for an autonomous agent's films, an
iMessage with the phone cut plus a push notification.

## Logging notes

- Every note is logged verbatim as a numbered direction change, with who said it and what it supersedes (tracker.md).
- Map each timecode to its shot, beat and stage before touching anything, and say where the fix lands.
- A note on approved work is a perfecting note: it becomes a checklist of changes to that exact artifact (below).

## Targeted fixes

| The note is about | The fix |
|---|---|
| A line's words or delivery | Re-voice only the changed lines, with their neighbouring lines as context, at the same delivery (slate-voice). Every beat is keyed to words, so timing follows. |
| A picture detail | Redraw it in its act, re-render only the affected range and splice it in with proof: frame hashes and PSNR at the edges (slate-finish). |
| Sound | Adjust the sound itself, re-mix, re-verify (slate-sound). |
| A claim or a word on screen | Correct the script and the truth pack first, then the picture and the voice, then re-run the claims and banned-word gates. |

After every fix: re-run the gates on what changed (`slate qa` with the gates that cover it), rebuild the master when
picture or sound changed, and rebuild the packet.

## Preserve the baseline and the iteration

When the owner approves something and adds notes:
1. Write the notes as a checklist of changes to that exact artifact. Slate's revise stage (in `slate --help`) records
   them, snapshots the approved version and hashes everything else.
2. Keep everything else as it was.
3. Check the revision against the approved version by measurement: levels relative to the voice, sync, palette, and
   that nothing outside the checklist changed.
4. A requested new approach becomes work within its scope. Ask only for missing information. Keep the feedback and
   resulting version in the creative history, including a rejected experiment or a return to an earlier proof.

**Testing a voice change** (a polish, a re-voice, a new tag): test it on the clip the owner knows best, with every mix
setting pinned to that clip's approved build. Prove the pin first by rebuilding the approved mix sample for sample, then
change only the voice, so whatever the owner hears is the voice change and nothing else.

## Versions

- Versions are numbered. Each approval records the SHA-256 of what was approved.
- A delivered version is never overwritten: the next one is a new version, and superseded material goes to the film's
  `archive/` with a line saying what replaced it.
- The review packet always says which version it is and what changed since the last.

## Scope

Before a wide fidelity pass ("make everything match the product"), propose its scope and ask whether it's the pressing
stuff. Core surfaces must match exactly; secondary ones only need to feel right unless something is plainly wrong.
Record the ruling in the tracker.
