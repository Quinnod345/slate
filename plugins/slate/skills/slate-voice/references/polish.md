# The studio polish

A generated voice can sound generated: a little too harsh on its S, T and SH sounds, a bright, glassy edge, no room
around it. The polish is a studio vocal chain that fixes that without changing who the voice is. It runs on every take,
locally (numpy and scipy, nothing paid), as the last step before the take goes into the film.

`slate voice --help` lists the polish stage with the other voice stages; its own `--help` gives every option.

## What the owner asked for, and why it's built this way

The first film's owner heard a narrator as "a little too harsh… you can tell it was generated" and asked for a
de-esser, a lot of EQ and other local vocal effects. The target was a podcast host: natural and conversational, a
little room, warm and present. The strength had to sit between "barely noticeable" and "clearly warmer": a marked
change that is still the same voice.

Measuring first changed the plan. That voice's harshness wasn't in 2-5 kHz at all (it sat 3 to 7 dB under natural
speech there); it was at the very top, on its vowels and on its S sounds. A static cut at 2-5 kHz would only have dulled
it. So: **measure where a voice's harshness lives before tuning its chain** (the analysis stage's sibilance and edge
bands, and the long-term spectrum the polish report gives).

## The chain

| Stage | What it does | Why |
|---|---|---|
| High-pass | 12 dB an octave, about 80 Hz | rumble and low thump out, nothing of the voice |
| Low-mid cleanup | a gentle bell cut where the voice measures boxy or honky | one bump, not a curve |
| Compressor | moderate (2 to 2.5:1), on the take brought to -20 LUFS first | the threshold means the same thing on every line |
| The edge | a dynamic cut on 2-5 kHz, only while a consonant dominates the band | T, SH and CH come down; vowels are left alone |
| De-esser | the same on the sibilance band (5-9 kHz by default), faster | S and SH come down, only while they dominate |
| Warmth | presence, a low shelf for body, a high shelf easing a glassy top | the static colour |
| Room | a small synthetic room, 18 to 22 dB under the dry voice | space without mud or hiss |
| Loudness | back to the take's own integrated loudness (BS.1770) | the mix balance holds |

Every stage keeps the take's length sample for sample, so no word, caption or cue moves. A take whose true peak would
pass -1 dBTP sits a hair lower instead (by a few tenths of a dB, recorded in the report).

## Strengths

A voice's tuning names its strengths. The neutral default has two:
- **A**: the subtler side of a marked change;
- **B**: the warmer side.

Each has a place on a scale from 0 (the take untouched) to 1, so a number in between blends its neighbours: 0.8 sits
halfway between A (0.6) and B (1.0). Use a name when the owner picked one by ear; use a number only to find a middle the
owner asked for.

## The flow

1. **Ask the owner for the target in their own terms before tuning**: where the harshness is, a reference sound (the
   first film's was a podcast host, a little room, warm and present) and how strong a change. Then bracket their middle
   with two strengths.
2. **Try before you pin.** The polish stage's trial mode renders the strengths you name (A and B, say) for every line
   without pinning or registering anything: a level-matched reel (each line unpolished, then each strength), every
   number in `voice/polish/try/trial.json`, and on request an A/B spectrogram sheet per line plus one long-term
   spectrum chart (every line pooled, unpolished and each strength, against natural speech's shape).
3. **The owner picks by ear.** Measurements say what changed; the ear says whether it's still the voice.
4. **Polish with the pick.** The first real polish pins the strength and the whole resolved chain in film.json
   (`voice_polish`). Every later polish of that film uses the pin, so a line re-voiced in week three gets week one's
   polish. Moving it is a re-pin with who approved it and why, after a new trial.
5. **Align again.** Whisper hears the polished takes (the words gate runs on what the audience hears), then run the
   voice checks.

## What it writes

- `voice/takes/<shot>/<take>.polished.wav` beside the take. The raw, clean and paced files are never changed.
- `voice/takes/<shot>/<take>.polish.json`: the report (below).
- `voice/polish/summary.json`: the film's polish, pooled over every line.
- film.lock.json records the polished take with the stage it was made from and the raw take as inputs, so a new take
  or a new pace makes the polish stale. Cleaning or pacing a take again drops its old polish.
- Approving a take locks its polished file too. Polishing an approved take again needs `--force` and the person's
  approval id.

## The report

Both sides are brought to the same working loudness by the same gain, and measured over speech frames:
- **sibilance**: the de-esser band's level and its loudest 5 ms moments (the 99th percentile), before and after;
- **the edge**: the same for 2-5 kHz;
- **spectral tilt** (50 Hz-1 kHz against 1-5 kHz) and **warmth** (80-500 Hz against 1-5 kHz);
- **low-mid**: the octave around the cleanup frequency;
- **clarity**: 1-5 kHz, where words stay clear in a mix;
- **loudness and true peak**, in and out, and the length;
- the third-octave change and its RMS (the overall spectral change);
- what each stage did: the compressor's gain reduction, how often and how deep the edge cut and the de-esser acted.

Bands a take barely uses (more than 60 dB under its loudest third-octave) are left out, so a narrowband recording
can't produce a fake "change".

## The gate

Every polished take is verified by measurement:
- the length is identical;
- loudness is back within 0.5 LU of the take;
- the true peak stays under -1 dBTP, or the take's own peak if it was higher;
- when the de-esser acted, the S peaks came down; they never came down more than the chain can do;
- the 2-5 kHz peaks didn't rise (presence may lift them a little, never more than 1 dB);
- not over-processed: the overall spectral change at most 6 dB, and at most 2.5 dB taken out of 1-5 kHz;
- made with the film's pinned polish, from the current take.

A film that doesn't use the polish passes the gate and says so. A voice can require it (`polish.required` in its
profile), and then every line must be polished. On the first film the two strengths measured 2.5 and 3.7 dB of overall
change, took out 0.7 and 1.4 dB of 1-5 kHz, and cut the S peaks by about 6 and 9 dB.

## Tuning a voice

A brand's voice profile can carry its own tuning in `polish`: named `points` (each a chain; anything left out comes from
the neutral chain), their `strengths` on the 0-1 scale, a `default_strength`, and `verify` overrides. Tune it on the
voice's own measurements:
- where its sibilance peaks (some voices' S sits at 10-11 kHz, others' at 5-8 kHz): set the de-esser band there;
- whether it has a glassy top (a high shelf) or a presence bump (a small cut at 3 kHz);
- where it's boxy or honky (the low-mid cleanup);
- how heavy its lows are (the low shelf).

Keep the measurement source honest: the voice's breath and tag measurements read the unpolished stages, because a
polished baseline would make the next round's new takes look harsher than the voice's own reads.

## Parity

The chain was ported from the first film's tool, whose EQ and compressor ran in a GPL-licensed plug-in host. Slate
doesn't depend on it: the same formulas in numpy and scipy reproduce that tool's polished takes to within 0.001 dB per
band, on two voices and three strengths.
