# The tag loop: voicing, measuring and picking audio tags

When a line's direction matters (a smile on the greeting, reassurance on a hard truth, a lively fall on a one-word
answer), one tagged take isn't enough to know. The tag loop voices several candidates on several seeds, measures each
against the voice's own untagged reads of the same words, gates and ranks them, lets the owner hear the best side by
side, and puts the pick into the film as that exact file.

```
slate voice tags optimize --estimate   # the round's cost from voice/tags/candidates.json; nothing is voiced
slate voice tags optimize              # voice every candidate on every seed plus the untagged reads, then measure
slate voice tags pick                  # gate and rank each line's takes; ranking.md shows every gate
slate voice tags abc S01               # the best passing takes, level-matched in one reel, to hear side by side
slate voice tags adopt S01 N1s101      # the owner's pick (candidate N1, seed 101) goes in as that exact file
```

Each action works on the round in `voice/tags/main/` unless `--round R` names another; `--help` gives the rest.

## What the first films learned

- **The seed matters as much as the tag.** A tag that passed on one seed measured breathier on three others, and a
  winning text lost its lift on a new seed. So candidates get several seeds, and the pick goes in as that exact file.
- **Some tags make a voice breathier even when they sound harmless.** On one voice every "sincere", "kind", "warm" and
  "reassuring" take measured breathier than its untagged reads; a smile made the same voice fuller.
- **One average hides a whispered word.** A tag on the last word changed that word while the line's average barely
  moved, so every take is measured on three spans.
- **The rules tightened as the rounds went on**, each written down before the pick: the strict breath rule, the
  question-rise rule, the mid-word stop.

## The plan: voice/tags/candidates.json

Hand-written, checked against `schemas/voice_tag_candidates.schema.json`:

```json
{"lines": {
  "S01": {"intent": "smile", "emotion_from_word": 0, "baseline_seeds": [301, 401],
          "candidates": [{"key": "N1", "tagged": "[warmly, with a smile] Hi, it's good to meet you.", "seeds": [101, 201]},
                         {"key": "N2", "tagged": "[confident] Hi, it's good to meet you.", "seeds": [102, 202]}]},
  "S07": {"intent": "rhetorical", "rhetorical": true, "baseline_seeds": [303],
          "candidates": [{"key": "M1", "tagged": "[matter-of-fact] Why does it matter? Because it decides.", "seeds": [107]}]}
}}
```

- **intent**: the feeling the pick must measurably show (below), or `none` for the gates only.
- **emotion_from_word**: where the feeling should show (0-based script word); every take of the line is compared on
  the same words.
- **baseline_seeds**: extra untagged reads, so the voice's own spread is known (the film's current take counts too,
  when it's untagged).
- A candidate may set `stability`, `context` (`neighbours`, `no_next` or `none`) and `role`.
- **hold**: planned but waiting for the person's go.

Every candidate must strip back to the script exactly, with no banned tag; the whole plan is refused before anything is
voiced if one doesn't.

## Optimize: voice and measure a round

Each take is voiced the film's way (its pinned voice, model and settings, the neighbouring lines as plain context),
cleaned and paced. **Every take is a paid TTS call in live mode**, through the one ElevenLabs client (estimate, budget,
ledger row); ask for the estimate first. The round is resumable: a take already voiced is never voiced again.

Then every take is transcribed (local Whisper) and measured on its clean file, on three spans (the tag's words, the
emotion words, the whole line), against the untagged reads of the same words. Everything lands in
`voice/tags/<round>/metrics.json`.

## Pick: the gates and the feeling

A take that fails any gate is out:

| Gate | Rule |
|---|---|
| Full voice, when `tag_policy.avoid_low_effort=true` | two tests on every span: the statistical rule and no span breathier than the breathiest untagged read of the same words. Without this scoped preference the measurements stay in the report and do not disqualify the take. |
| Words | Whisper hears the script word for word, no repeat, no tag read aloud |
| Creak | no more creak than the untagged reads, plus one point |
| Fits | the paced take fits its shot |
| Ending | the last word rises at most 3 semitones past the untagged endings; on a rhetorical line, no question word rises more than 1 semitone past its untagged reads |
| Complete | the take's last 20 ms sit at least 35 dB under its speech |

The feelings, measured on the emotion span against the untagged reads:

| Intent | Present when |
|---|---|
| smile | pitch up 0.8 to 3.5 semitones (past that reads theatrical), and brighter (centroid up 4% or F2 up 2%) |
| smile_end | a smile, and the phrase lifts over the rest at least as much as untagged |
| reassure | steadier (pitch movement at least 0.2 semitones lower) at full level |
| hopeful_end | the last phrase lifts at least 0.5 semitones more than untagged, fully voiced, level held |
| sincere | steadier, or 3% slower, at full level |
| rhetorical | the question words rise at least 1 semitone less than untagged |
| plain | no wider pitch movement than untagged, at full level |
| encourage, warm | a modest lift (warm may brighten instead), at full level |
| alive_fall | the last word falls 1.5 to 4 semitones from its peak, with more range and no less energy or clarity than the reference |
| calmer | narrower range, a lower peak, less rise on the last word, a steadier level and not rushed, against a reference take, without going flat |

Among the takes that pass, the pick is the clearest feeling with the fullest voice. If none pass, the line stays on its
current take: retagging it is a job for the owner's ear, not another round. Replayed stand-ins (tests, demos) are never
picked. `ranking.md` shows every take's gates.

Pooling more untagged reads (new baseline seeds, more lines) shifts the calibration a little and can flip a near-edge
verdict: after a recalibration, re-run every approved and proposed take through the gates.

## ABC: listen

For one line: A is the film's current take, then the best passing candidates (one per tag family first), each
level-matched to -20 LUFS, in one reel with short gaps; abc.json with the timings and measurements; an A/B sheet per
candidate; and the last word's pitch for every take on one axis. Near misses can be added, labelled with what they
missed, never presented as passes.

## Adopt: the exact file

The owner's pick goes into the film as that exact file: a new take of the shot (raw, clean and paced copied byte for
byte), selected, with its tagged text as the line's direction and the round's measurements carried with it, so the
inflection gate knows it was measured against untagged reads of the same words. Adopting over an approved take needs
the person's go. Then align, polish (if the film uses it) and check as usual.
