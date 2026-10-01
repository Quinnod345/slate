---
name: slate-finish
description: Render, master, derive and deliver a Slate film. Covers final renders with true motion blur (chunked, resumable, patched and spliced with proof, queued), masters (After Effects for supervised films on the owner's go, ffmpeg for autonomous and draft films), derived films (cut-downs, new aspects, new languages), captions and thumbnails at delivery, the delivery manifest (written last, never approved for publishing until the owner approves that exact file), and review packets and versions. Use when a Slate film needs rendering or re-rendering, a master, a cut-down or another aspect, a delivery set, or a review packet for the owner.
---

# slate-finish

The last mile is where most time and disk go, and where a film is either proven or quietly broken. Every render is
counted, every splice is proven, every master is compared frame by frame with what it was made from, and nothing leaves
until the gates pass and a person approves the exact file.

This covers stages 10 (render), 11 (master), 12 (deliver) and 13 (review and correct) of the production. The gates
themselves are slate-qa's.

## 1. Render

```
slate render                          # every act, one at a time, 16 motion-blur samples, ProRes 422 HQ
slate render --act 3                  # one act; registering it locks the film's timeline
slate render --act 3 --from 120 --to 260
                                      # re-render a range and splice it in, proven by frame hashes and PSNR
slate render --resume                 # pick up renders that didn't finish, keeping every good chunk
slate render --detach                 # queue it and return a job id at once
```

- Renders are chunked and resumable: a chunk with the right frame count and hash is kept; a short or missing one is
  rendered again. Long renders always go through the job queue (`slate job status <id>` follows one).
- Each frame is drawn 16 times across a 180-degree shutter and averaged in 16-bit (4 in draft), into ProRes 422 HQ,
  10-bit, BT.709. Never fake motion blur, never render on another machine, one heavy render at a time.
- Every render reserves its disk first and removes its bundle when it ends.
- `slate render list` shows what's rendered, stale or unfinished; `slate render stills --frames <f,…>` makes review
  stills; `slate render clean` removes leaked bundles and finished chunks.
- After Effects acts render with `--engine ae --go`, only on the owner's go and never for autonomous films.

See [renders](references/renders.md).

## 2. Master

- **Supervised films are finished in After Effects**, on the owner's go: an editable AE project built from the film's
  data, then chunked aerender masters. `slate master --ae --go` builds the project; ask the owner to save and quit AE;
  then the masters render. Never quit AE for them. See [AE finish](references/ae-finish.md).
- **Autonomous and draft films are mastered with ffmpeg** (`slate master --ffmpeg`), and held for the owner. The owner
  can start the AE finish when they approve a held film.
- Every master is compared with the act renders frame for frame (at least 40 dB PSNR on every frame, exact frame counts),
  and the AE project is audited. That check caught both a colour shift and a one-frame lag on the first films.

See [masters](references/masters.md).

## 3. Derived films: cut-downs, aspects, languages

```
slate variant <parent-id> <child-id> --aspect 9:16      # or --cut 15, or --language fr
```

A derived film reuses its parent's approved takes, timings, beats and assets by reference, keeps its clock, re-stages
its layout for its own type scale and safe zones, re-plans its sound, runs every gate and needs its own approval. Hero
placements are derived at master quality; social cut-downs can take a faster route once one is proven. See
[derived films](references/derived-films.md).

## 4. Deliver

`slate deliver` writes the delivery set, and the manifest last:
- the masters (and derived films' masters), the mix and its four stems;
- the format's platform files (`deliver/vN/platform/`);
- captions as SRT and WebVTT (burned in too, where the format asks);
- a review cut, a phone cut and 0.5-second contact sheets;
- thumbnails;
- the AE project, for supervised films;
- internal flags for which voices and shots are synthetic (never published);
- the manifest: every file with its size, SHA-256 and probe, anything missing listed, and **"not approved for
  publishing"** until the owner approves that exact file's SHA-256.

A delivery refuses replayed plates or captures, plates without an approved take, and draft films. A delivered version is
never overwritten. See [delivery](references/delivery.md).

## 5. Review and correct

`slate review` builds the packet for the current version: a phone cut with burned-in timecode and shot id, a 0.5-second
contact sheet, what changed since the last version, and the open questions. It goes to the owner's Downloads for their
own productions, or by iMessage with the phone cut and a push notification for an autonomous agent's films. Notes come
back verbatim, numbered; fixes are targeted (re-voice only changed lines, re-render and splice only the affected range,
re-mix, re-run the gates); versions are numbered, and every approval records the SHA-256 of what was approved.
`slate notes add|list|plan` logs and plans them; `slate review grade` records a separate reviewer's verdict on an
autonomous film; `slate approved <file>` says whether a file may be posted. See
[versions and review](references/versions-and-review.md).

## Rules that hold everywhere

- Renders are local, one heavy job at a time, through the queue; never on another machine.
- A final render replaces an act's render only when its frame count is exact and its proof passes.
- After Effects steps start only on the owner's go; autonomous films never touch AE.
- Nothing publishes itself: posting needs the owner's approval of that exact file.
- A delivered version is never overwritten, and superseded material is archived with a note.

## References

- [renders](references/renders.md): chunks, resuming, patches and splices, engines, the queue, disk.
- [masters](references/masters.md): what a master is, the checks against the renders, loudness targets.
- [ae-finish](references/ae-finish.md): the AE project from data, aerender masters, colour, frame placement, the cache.
- [derived-films](references/derived-films.md): cut-downs, aspects, languages, and faster variant routes.
- [delivery](references/delivery.md): the delivery set, thumbnails, captions, the manifest, publishing.
- [versions-and-review](references/versions-and-review.md): review packets, notes, targeted fixes, versions.
