# The score: the Eleven Music playbook

A composed score is part of most films: Eleven Music on its best model (named explicitly in every request, because the
API's default is an older model), composed per act, assembled by measurement, and gap-checked. Every rule below is a
lesson the first films paid for.

Why a composed score at all: synthesized tonal layers sounded cheap. Music is composed (a score per act), tonal effects
play on real sampled instruments, and synthesis is kept for air, ink, beads and subs, where it shines.

## The steps

```
slate score plan                  # sound/score/plan.json and one plan per part, cut to the shots
slate score compose --dry-run     # what it will cost, nothing spent
slate score compose               # composes the missing takes; takes are kept, locked takes never redone
slate score assemble              # sound/score/score.wav and score.json
slate score qa                    # silence and noise spans in every take and the score
```

Compose runs through the ledger (estimated and budget-checked before the first call) and replays offline in tests.

## Planning (why each rule exists)

- **One composition per act**, not one for the film: a single six-minute composition came back with 21 seconds of
  silence over the film's biggest moment and 18 seconds of noise near the end.
- **Split any part longer than about 90 seconds** at a story turn: longer plans ran out of material and went silent early.
- **Every section at least 3 seconds.**
- **A 3-second pre-roll**, cut at assembly, and every part runs at least 3 seconds past its hand-over: Eleven Music fades
  in and out at a take's edges regardless of the plan.
- **An exact moment must be a part boundary on the word's time**, not a section inside a take.
- **The score never plays the effects' materials** (they go in its negative styles), so the two never blur together.
- **Leave room for the narrator** in the style, and let the score yield to a pad under a character's motif.

## Directions: sound/score/directions.json

The one hand-edited file in the score's folder. It says where the music does something special:

```json
{"parts": {"act3": {"end": "stop", "stop_before_s": 0.5},
           "act4": {"join": "bloom", "landing_event": "key_value_lands"}},
 "split": {"act2": ["S09"]},
 "planned_stops": [{"from": 0.0, "to": 1.0, "why": "the film opens out of silence"}],
 "styles": {"positive": ["felt piano", "soft low strings"], "negative": []},
 "section_styles": {"S05": ["opens up, wider"]},
 "shape_db": {"act1": -1.0},
 "seeds": [7, 21]}
```

- **Joins** (how a part comes in): `start` (the film's first frame), `act` (an equal-power crossfade centred on the cut),
  `bloom` (the bloom lands exactly on a landing: an event, a time or a word), `lift` (a small arrival nudged onto its
  beat), `arrive` (an end-card cadence).
- **Ends**: `handover` (the next part's join) or `stop` (stops dead just before the next landing: a held breath,
  recorded as a planned stop).
- **Planned stops** always carry a `why`; the gap check treats anything else as a hole.

## Musical choices that worked

- The score gives way at a film's key line: one held pad under a key word, thinner under a heavy line.
- Stillness is a held note, not silence, so the music never simply drops out (except at a planned stop).
- An act break with no voice for a couple of seconds is a natural place for the score to breathe.
- Typical planned stops: a silent opening second, a held breath before a hero moment, half a second before a reveal, and
  the ending.
- At a big moment, the music and the hit land together.

## Takes

- **Measure every take at its landing**, and keep every take. Eleven Music doesn't reliably deliver a requested dynamic:
  a requested swell rose 1.1 dB, and four retries never lifted it (1,204 credits).
- **Choose or splice from takes on hand before re-composing**: an unused take spliced in measured better than a new part.
  When no take delivers a moment, pick the best measured take and make a small fader move rather than keep paying.
- **Place takes by their measured entry**, never by plan time: takes open with their own silence (a bloom once entered
  0.75 s late).
- Score QA thresholds were tuned on one model's takes; re-check them when the model changes. Measure levels in stereo:
  a mono fold faked a 3.8 dB rise that wasn't there.
- Plan files are immutable once composed, and seeds are fixed, so a record of what was sent stays true.
- Concurrency is limited per plan (two music requests at once on the plan the films used); the queue's lock handles it.

## Assembly

- **Equal-power joins** (sine and cosine gains): squared gains dip about 3 dB between different takes.
- **Close holes in place** by freezing the previous chord with an equal-power hand-over (about 0.8 s); moving parts to
  close a hole cascades into the next seams.
- **The gap check**: no half-second window of the music under −60 dBFS outside the planned stops, predicted at assembly
  and measured again after the mix (the score's own QA misses holes under about 1.5 s and the last second).
- The end holds and is silent on the film's final frame.

## Level and licence

- The mix sets the score under the voice and ducks it under speech (the starting palette: about 15 LU under, ducked about
  4 dB; the owner found one score "a teeny bit loud" and it went about 1.5 dB lower). Adjust by ear per brand and per
  film (`slate mix --under`, `--duck`).
- Online commercial use is fine on paid plans; film, TV and radio need an Enterprise plan. The music-licence gate flags a
  broadcast format.
