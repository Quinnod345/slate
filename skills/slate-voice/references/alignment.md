# Word timings

Every beat in a Slate film is keyed to the voice's words, so word timings are the film's clock. They come from the takes
themselves, after cleaning and pacing.

## Whisper by default

```
slate voice align                     # every take, with Whisper (local, free)
slate timing words                    # re-time the words and rebuild the timing files
```

- Whisper's turbo model with word timestamps, run locally; the model and its version are recorded in the evidence of
  every result that uses them.
- **Word starts are snapped to the take's energy onset** (within about 80 ms, on a clear rise), because Whisper's word
  starts can be off by tens of milliseconds.
- Results are cached per take by its hash: a take that changes is re-aligned; one that doesn't isn't.

## Forced alignment is for evaluation

ElevenLabs' Forced Alignment (`--engine elevenlabs`) is paid per minute of audio. It stays an evaluation until it's been
compared with Whisper on approved takes (`--compare` writes the per-word differences), and the owner decides whether to
switch. Whether the newest TTS model returns its own timestamps isn't documented; verify before relying on it.

## Whisper's quirks

- It writes numbers as digits and symbols as symbols: word lookups match through the language's spoken forms both ways,
  so "twenty twenty-six" finds "2026".
- Over digital silence at the very end of a track it can hallucinate a repeat of the last phrase: ignore silent tails.
- It can mishear a brand word: the lexicon tells the checks which words must be heard in full.
- For reference analysis (someone else's film), take the voice's text from Whisper with a second model agreeing on
  disputed lines, never from a generation vendor's scene analysis (which once paraphrased the on-screen text instead of
  the voice).

## Word misses

- A word lookup (`word()` in the kit, `word_time` in Python) never throws: when a word can't be found it returns an
  estimate from the line and records a miss.
- `slate timing misses` lists them; the list must be empty before any act is handed in.
- A miss usually means the script and the take disagree (a changed word, a number spoken differently): fix the script or
  the take, never the timing file.

## After alignment

```
slate script build --from-takes
slate timing
```

The storyboard is re-timed from the takes, and the timing files are rebuilt on the film's clock. Once an act render is
registered, the timing locks.
