# Casting: choosing a voice by ear

A casting shootout has several voices read the same lines, measures every take, and gives the owner a listening reel.
The owner chooses by ear; the measurements only filter out takes that fail and put the rest in a sensible order.

## Running a shootout

```
slate voice cast --voices <voice-id>,<voice-id>,<voice-id> --shots S01,S05
slate voice cast --text "<a line that matters>" --name opening-line
```

- Pick lines that matter: the opening line, a line with the brand's name, a line with a number, the emotional peak.
- Every candidate reads the same lines on the production model with the same settings.
- Each take is measured: pace (words a second), pauses at full stops and turns, pitch range, falling endings on
  statements, the noise floor, and whether Whisper hears every word, including each brand word said in full.
- The reel is level-matched (every take at the same loudness), labelled, and plays the takes one after another, so the
  owner compares voices, not volumes.
- A clone or a famous voice among the candidates is refused before anything is spent.

## Choosing

- **The owner picks by ear.** Record the pick with its model, settings and seed, and pin it.
- **Try the pick inside the approved sound** before committing: the same proof mix with the new voice, checking
  loudness and a Whisper read.
- **Brand words decide close calls.** One engine clipped the brand's name on the first film; the best current model said
  it in full. Put every brand word in the lexicon so the checks listen for it.
- **Time shots from the real reads.** A deliberate narrator can read 1.5 to 3.2 words a second; the shots are re-timed
  from the takes after voicing.

## Which engine and model

- Narration runs on ElevenLabs directly, on its best model (recorded in `registry.json`, pinned per film). A generation
  vendor's own TTS route gave lower bitrate, allowed only a few jobs at once, and clipped a brand word.
- For long reads (60 to 120 seconds of continuous narration), A/B the best model against the vendor's most stable
  long-form model before choosing.
- For pieces with several voices, the vendor's dialogue endpoint takes requests up to about 2,000 characters, any number
  of speakers, and previous and next context.
- Check the best models against the live API at the start of a project; the catalogue changes.

## The shortlist at the sitting

Bring two or three voices to the pre-production sitting, each with its reel entry and a one-line note (what it does
well, what to listen for). Measurements go in the notes, not in the pitch: the owner is choosing a voice, not a number.
