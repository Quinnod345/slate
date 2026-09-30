# Versions and review packets

## The packet

`slate review` builds a review packet for the current version, in the film's `review/` folder:
- **a phone cut** with burned-in timecode and shot id, so a note like "at 1:28, the V sound" maps straight to a shot;
- **a 0.5-second contact sheet** of the whole film, labelled;
- **what changed** since the last version, in plain words;
- **the open questions**, each one decision.

Review cuts can be made straight from the act renders, before the masters, so the owner sees the film early. Other
useful review material: voice-only previews, per-shot half-resolution previews with sound, side-by-side comparisons (two
takes, before and after a fix), and gesture sheets for a character (slate-qa, review-packets.md).

## Where it goes

- The owner's own productions: their Downloads.
- An autonomous agent's films: an iMessage with the phone cut, and a push notification. Never in tests or replay.

## Notes

1. Log every note **verbatim**, numbered, with who said it and what it supersedes (the tracker's direction changes).
2. Map each timecode to its shot, beat and stage.
3. Decide the targeted fix for each (slate, review-and-correct.md): re-voice only changed lines with their neighbours;
   redraw, re-render the range and splice it with proof; re-mix; re-run the gates on what changed; rebuild the master.
4. A note on approved work is a perfecting note: change that exact artifact, keep the rest, and measure the revision
   against the approved version.

## Versions

- Versions are numbered in film.json; each starts with a note of what it's for.
- Each approval records the SHA-256 of exactly what was approved.
- A delivered version is never overwritten. Superseded material (old takes, old renders, old mixes) goes to the film's
  `archive/` with a line saying what it was and what replaced it; earlier deliveries' copies stay as they were.
- The packet always names its version and what changed since the last one.

## Before sending a packet

- The gates for everything that changed have been re-run, and their status is in the packet (only PASS counts).
- You've watched the phone cut yourself, start to finish, with sound.
- The questions are ones only the owner can answer; everything else is decided and noted.
