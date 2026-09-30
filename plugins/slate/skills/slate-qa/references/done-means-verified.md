# Done means verified

The failure this prevents: reporting a shot, a stage or a film as done because it was made, because a proof looked
right, or because a check didn't complain. On the first films the rule became simple: a thing is done when every check
that covers it holds, with its evidence named, and a person has judged what only a person can judge.

## What counts

- **A shot passes only when every check holds.** A failure is fixed, re-rendered and rechecked before a pass is recorded.
- **An act is done** only when it's built, handed in (`slate check` passing), and reviewed on stills, full-rate frame
  sequences and a preview of the final code, with a 0.5-second contact sheet and a flicker scan.
- **A film is done** when every film gate passes, fresh; the final cut is approved by the owner against its exact bytes;
  and every ask in GOAL.md is delivered and verified, re-read against the requester's original words.

## What never counts

- **A proof is not a finished shot.** A proof shows the idea works; the shot still has to be built, rendered, gated and
  approved.
- **UNCHECKED is never passed.** A gate that couldn't run says so, and so do you.
- **REVIEW is never passed** until a person accepts the item, bound to the files they judged.
- **STALE is never passed**: re-run it.
- **"No errors" isn't "checked"**: a gate that didn't look can't pass.
- **"Every frame is good"** is never claimed unless every frame in the range was processed or inspected.
- **A replayed result** (tests, `SLATE_FAKE_VENDORS=1`) is never evidence for a real film.

## Evidence

Every PASS names:
- what was measured and the number (−14.0 LUFS, −1.4 dBTP; worst anchor 24 ms; mean 56.5 dB, worst 51.6 dB);
- the engine and its version;
- the files it read, by hash;
- where the detail is (a report, a strip of frames, a contact sheet).

Statuses in the tracker cite that evidence: **done** means verified, with the evidence named.

## At the end

Before saying the film is done, re-read the requester's original request in GOAL.md, not a summary and not the checklist
alone. Walk every ask. Anything partial or untouched isn't done: keep going, or say plainly what's left and why.
