# Takes, pins and re-voicing

## One take per line, pinned

- `slate voice tts` voices every line of the script, one take per line, with the neighbouring lines as plain-text
  context. The takes and their records live in `voice/` (which take is selected, its raw, clean and paced files, its
  alignment, and whether it's approved).
- At first use the film pins the model, the voice id, the settings, each line's seed and each request id. A registry
  refresh never changes a pinned film; moving a pin needs the owner's name, a reason and ideally an A/B as evidence.
- Existing takes are kept unless you ask for new ones.

## Approved takes are locked

- An approved take is locked by its content hash. Regenerating it needs `--force` and a recorded spend approval: the
  owner's yes to spend again on something already paid for.
- **Reuse, never regenerate, approved assets.** When the film changes around an approved take, re-time the shot, not
  the take.
- Superseded takes are archived (with a note of what replaced them), never deleted.

## Leaked tails

Context-conditioned speech can leak: the next line's first sound appears at the end of a take (on the first film, a
line ended with the first consonant of the next line's first word, and the owner heard it). So:
- the tails check scans every take's end for sound returning after silence;
- the fix is to **trim** the take after its last word decays (the trim is recorded with the take), never to regenerate an
  approved take for it;
- the dialogue stem is digital silence at the trimmed point.

## Re-voicing

When a line's words change (a correction, a claim removed):
1. Change the script, rebuild it (slate-story).
2. Re-voice **only the changed lines**, with their neighbouring lines as context, at the same delivery (the same voice,
   model, settings, cleanup and pinned polish), so they match the lines around them.
3. Whisper must hear the new lines word for word; the new takes must fit their shots.
4. Re-time, re-render the affected range and splice it (slate-finish), re-mix, re-run the gates.

Because every beat is keyed to words, the picture follows the new timing automatically; nothing is re-timed by hand.

A film that uses the studio polish pinned its strength and chain at first use, so a re-voiced line gets exactly the
same polish, and the polish check fails until it has it ([polish](polish.md)). A tagged line's pick from a tag round
goes in as that exact file ([the tag loop](tag-loop.md)): seeds aren't fully deterministic, so it's never re-voiced in
the hope of getting it back.

## Fitting the shot

Every read fits its shot: it starts a short lead into the shot and ends at least a short tail before the cut. The fit
check measures each paced take against its shot's room. A read that doesn't fit gets a longer shot (the storyboard is
re-timed from the takes), fewer words, or a gentler pace, in that order of preference.

## Brand words

Every take is checked for the brand's words said in full. A clipped brand word is a failed take, whatever else is right
about it.
