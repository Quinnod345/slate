# The gates

Every gate returns a result with a state (PASS, FAIL, REVIEW, UNCHECKED, STALE), a one-line summary, its evidence
(paths and measurements), the engine and version it used, and the hashes of its inputs. Only PASS counts.

## Script (stage 3): `slate script build`

`script.schema`, `script.structure`, `script.pace` (2.6 words a second or less per shot), `script.read_time`,
`script.on_screen` (12 words or fewer), `script.runtime` (within 10% of the target), `script.banned_words`,
`script.claims`, `script.numbers`, `script.quotes`, `script.sources`, `script.first_line`, `script.vo_dashes`,
`script.shot_ids`. A failing build writes nothing (slate-story).

## Voice (stage 4): the voice checks

Policy (the voice may be used), direction (no banned tag; tagged lines strip back to the script), takes (every line has
one), words (Whisper hears every word and every brand word in full, no repeats, no tag spoken), tails (nothing leaking
from the next line), fit (each read fits its shot). Replayed takes are UNCHECKED (slate-voice).

## Timing (stage 5): `slate timing`

`timing.frames` (frame-exact and contiguous: nothing is written unless it passes), `timing.reads`, `timing.words`,
`timing.events`. `slate timing misses` must be empty at hand-in.

## Hand-in (stage 7): `slate check <act>`

Type check, module load, word misses, kit problems, banned words, events, contracts, fonts (slate-build).

## Generation and captures (stages 7 and 8)

A generated shot's takes are REVIEW until a person picks one; a capture's taps must land on their words.

## Sound (stage 9): `slate verify`

`sound.loudness`, `sound.stems_sum`, `sound.gap`, `sound.voice_sync`, `sound.cue_sync`, `sound.repeats`, `sound.glides`,
`sound.drone`, `sound.levels`, `sound.reference`, `sound.clarity`, `sound.music_licence` (slate-sound).

## The film (stages 10 to 12): `slate qa`

| Gate | Checks |
|---|---|
| `schema` | every film data file validates against its schema (a file with no schema yet is UNCHECKED, not passed) |
| `frames` | shots contiguous on the film's clock; every act render and master exactly as long as it should be, at the film's size and rate; the mix as long as the picture |
| `events_complete` | every act has a valid events file, or an explicit silence with a reason |
| `sync` | cues within a frame of their beats, takes exactly on their cues, word-anchored beats on their words, every act covered |
| `loudness` | integrated loudness on target, true peak under the ceiling (ffmpeg's EBU R128 meter) |
| `bit_depth` | the mix and every stem are integer PCM at the delivery depth and rate (a 16-bit or float mix fails) |
| `silent_stems` | no stem is silent |
| `stems_sum` | the stems add up to the mix, sample for sample |
| `captions` | 2 lines of 42 characters, 17 characters a second, at least 1 s, ordered, never overlapping, word for word |
| `claims` | every claim approved or sourced; autonomous films use approved claims only |
| `banned_words` | the brand's one list against the script, the captions and the OCR of the picture |
| `ocr` | Apple Vision reads the whole picture every 0.25 s; UNCHECKED (never another engine) when it can't |
| `readtime` | every on-screen statement readable for max(1.5 s, 0.4 s + 0.3 s per word) once fully built |
| `glitch` | one- and two-frame glitches in every act render, triaged against planned beats |
| `boundary` | every join between consecutive act renders: contiguous, no bigger a jump than the picture's own motion either side |
| `psnr` | the master against the act renders: at least 40 dB on every frame, every frame there |
| `motion` | the brand's eases, durations and stagger caps; entrances from 85% scale; speed and acceleration limits; no fast loops; symmetric mark rigs |
| `access` | at most 3 flashes a second; the brand's text colour pairs at 4.5:1 (body) and 3:1 (large); caption rate; 9:16 safe zones by OCR |

## One OCR engine

OCR gates use Apple Vision only. When it's unavailable the gate is UNCHECKED: a second engine would silently change the
results. Logo lockups and stylized marks go to the contact-sheet review (OCR misreads them).

## Validators read the film they validate

Every gate takes the film folder and reads that film's own files: on the first films a validator once read another
film's captions and cue sheets. A read-time checker needs the text as actually drawn: most of one film's read-time
failures were the checker, not the film.

## Trusting a new metric

Before a new measurement gates anything, check it against the owner's ear or eye on takes or shots they've already
judged. The voice's breathiness measures were validated that way before they rejected anything.

## Stale gates

A result records the hashes of what it read. When any input changes, the result turns STALE, and `slate status` places
the stale gate on its stage. Re-run it; never carry a PASS across a change.
