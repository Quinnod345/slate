# Pacing a read

eleven_v4's speed setting barely moves a read (take-to-take variation is bigger than the setting's effect), and
regenerating changes texture, not timing. So pace is adjusted after the take, gently, keeping the voice natural.

## What `slate voice pace` does

1. **Shortens long pauses**: pauses longer than about a quarter of a second are cut to about 0.8 of their length, from
   the middle, so the breath and the onset either side stay intact.
2. **Quickens the read slightly** with Rubber Band's highest-quality engine (about 6% faster on the first films), with
   formants kept, so the voice's timbre doesn't shift.

Options: `--no-tempo` (pauses only), `--tempo <factor>`, `--min-pause <seconds>`, `--pause-keep <fraction>`.
`slate voice pace --check-tools` reports the Rubber Band build: an Intel build running under Rosetta is flagged, and the
installer can replace it with a native one.

## Rules

- Keep it natural: a narrator who sounds hurried is worse than a shot that's a little long. Re-time the shot instead
  (the storyboard re-derives shot lengths from the paced takes).
- Whisper must still hear every word after pacing.
- Pace every take of a film the same way, so the narration matches itself.
- Pacing never makes a read fit a shot it doesn't fit. If a read is far too long, the line needs fewer words (slate-story)
  or the shot needs more time.
- Pacing is a derived file: the raw and clean takes are kept, so it can be redone.

## Where pace is set

- **In the script**: at most 2.6 words a second per shot, planned from the words (slate-story).
- **In the text**: punctuation and structure (directing.md).
- **After the take**: this stage.
- **In the edit**: holds and air around heavy lines, set by the shot lengths.
